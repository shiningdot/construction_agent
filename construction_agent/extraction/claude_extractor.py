"""基于 Claude API 的事故特征提取模块。"""

from __future__ import annotations

import json
import logging
from typing import Optional

from anthropic import Anthropic

from construction_agent.config import settings
from construction_agent.models.accident import (
    Accident,
    AccidentExtractionResult,
    HumanFactor,
    MachineFactor,
    ManagementFactor,
    EnvironmentalFactor,
)
from construction_agent.search.baidu_search import SearchResult
from construction_agent.database.repository import ProcessingRecord, LlmApiRecord, SearchLogRecord
from construction_agent.utils.location import normalize_province

logger = logging.getLogger(__name__)

# ── Tool / 结构化输出定义 ──

EXTRACTION_TOOL = {
    "name": "extract_accident_info",
    "description": "提取建筑施工事故信息，使用CAICS致因框架",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "date": {"type": "string", "description": "ISO格式YYYY-MM-DD"},
            "location": {"type": "string", "description": "省级行政区"},
            "city": {"type": "string"},
            "accident_type": {
                "type": "string",
                "enum": ["坍塌","高处坠落","触电","机械伤害","起重伤害","物体打击","土方坍塌","车辆伤害","火灾爆炸","中毒窒息","淹溺","其他"]
            },
            "severity": {
                "type": "string",
                "enum": ["一般事故","较大事故","重大事故","特别重大事故"]
            },
            "deaths": {"type": "integer"},
            "injuries": {"type": "integer"},
            "direct_cause": {"type": "string"},
            "causes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "code": {"type": "string", "enum": ["M1","M2","M3","M4","M5","F1","F2","F3","F4","T1","T2","T3","T4","H1","H2","H3","H4","C1","C2"]},
                        "name": {"type": "string"},
                        "description": {"type": "string", "description": "引用原文的具体描述"},
                        "reasoning": {"type": "string", "description": "判断逻辑：为什么选这个编码，引用原文语句"}
                    },
                    "required": ["code","name","description","reasoning"]
                },
                "description": "CAICS致因编码(5层19类，必须每个code附带reasoning)"
            },
            "summary": {"type": "string"}
        },
        "required": ["title","accident_type","severity","deaths","injuries","summary","causes"]
    }
}

# ── 系统提示词 ──

EXTRACTION_SYSTEM_PROMPT = """你是一名建筑施工安全事故分析专家。从事故文本中提取结构化JSON。直接输出纯JSON。

## 输出字段 (必填)
title, date(YYYY-MM-DD), location(省级), city, accident_type(坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/车辆伤害/火灾爆炸/中毒窒息/淹溺/其他), severity(一般事故/较大事故/重大事故/特别重大事故), deaths, injuries, direct_cause, summary, causes(array)

## CAICS 致因框架 (5层19类 — category必须从以下固定编码选)
按事故因果链顺序判断，每层可多选:

M.管理根基(5): M1现场监管缺失 M2隐患排查失效 M3安全培训不足 M4制度规程漏洞 M5市场行为违规
F.防线失效(4): F1临边洞口防护缺失 F2支撑体系失稳 F3设备安全装置失效 F4个体防护未使用
T.触发条件(4): T1设备机具故障 T2物料状态异常 T3地质环境突变 T4气象水文触发
H.人的行为(4): H1违反操作规程 H2违规指挥决策 H3风险辨识失败 H4应急行为失当
C.后果放大(2): C1救援响应迟缓 C2信息迟报瞒报

## 编码规则
- 每层可多选，无相关则不选（不能全空，至少1个编码）
- 典型事故通常有 M(某类管理缺陷) → F(某类防线失效) → T(某类触发) → H(某类人的行为) 的因果链
- category 必须是编码(如"H1")，description 引用原文，reasoning 说明判断逻辑

## JSON模板
{
  "title": "...", "date": "2024-03-15", "location": "广东省", "city": "广州市",
  "accident_type": "坍塌", "severity": "较大事故", "deaths": 3, "injuries": 2,
  "direct_cause": "...",
  "causes": [
    {"code": "M1", "name": "现场监管缺失", "description": "监理单位无市政资质违法承揽", "reasoning": "文本'监理单位不具备承担市政工程监理的资质'说明存在监管资质缺失"},
    {"code": "F1", "name": "临边洞口防护缺失", "description": "沟槽边1m内堆土且无支护", "reasoning": "文本'未采取任何安全防护措施'"},
    {"code": "T2", "name": "物料状态异常", "description": "临边堆土加大荷载", "reasoning": "文本'在边坡临边堆土加大了边坡荷载'"},
    {"code": "H1", "name": "违反操作规程", "description": "放坡1:0.4而非方案要求的1:0.67", "reasoning": "文本'未按施工方案确定的比例放坡'"}
  ],
  "summary": "..."
}"""


class ClaudeExtractor:
    """基于 Claude API 的事故特征提取器。"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.ANTHROPIC_MAX_TOKENS
        self.client: Anthropic | None = None
        self.last_api_meta: dict = {}  # 最新一次 API 调用的元数据

        if self.api_key:
            self.client = Anthropic(
                api_key=self.api_key,
                base_url=settings.ANTHROPIC_BASE_URL,
            )
        else:
            logger.warning("Anthropic API Key 未配置")

    @staticmethod
    def classify_text(text: str) -> dict:
        """预筛选：判断事故文本类型。返回 {'is_single': bool, 'flag': str}"""
        import re
        # 多事故通报关键词
        multi_patterns = [
            r'(通报|曝光).{0,10}\d+起', r'共发生.{0,5}\d+起',
            r'近期.{0,10}事故', r'连续.{0,5}\d+天',
        ]
        for pat in multi_patterns:
            if re.search(pat, text):
                return {"is_single": False, "flag": "multi_accident", "reason": f"匹配模式: {pat}"}
        # 极短文本（<80字且无伤亡数字）可能是摘要而非事故
        if len(text) < 80 and not re.search(r'\d+人死亡|\d+人受伤', text):
            return {"is_single": False, "flag": "too_short", "reason": "文本过短且无伤亡信息"}
        return {"is_single": True, "flag": "ok", "reason": ""}

    def extract(self, text: str) -> Optional[AccidentExtractionResult]:
        """从文本中提取事故特征，失败时自动用简化 prompt 重试。"""
        if not self.client:
            logger.error("无法提取：Anthropic 客户端未初始化")
            return None

        try:
            logger.info(f"开始提取事故特征，文本长度: {len(text)} 字")

            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=EXTRACTION_SYSTEM_PROMPT,
                messages=[{
                    "role": "user",
                    "content": f"请分析以下建筑施工事故信息，提取结构化数据：\n\n{text}",
                }],
            )

            result = self._parse_json_response(response)
            if result:
                result.source_urls = self._extract_urls(text)
                logger.info(f"成功提取事故: {result.title}")
                return result

            # ── 自动重试: 简化 prompt，加 output 格式约束 ──
            logger.warning("首次提取失败，自动重试(简化模式)...")
            retry_response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system="你是施工安全专家。从事故文本中提取结构化数据。只输出纯JSON，不要markdown，不要解释。JSON必须包含title,accident_type,severity,deaths,injuries字段。",
                messages=[{
                    "role": "user",
                    "content": text,
                }],
            )
            result = self._parse_json_response(retry_response)
            if result:
                result.source_urls = self._extract_urls(text)
                logger.info(f"重试成功: {result.title}")
                return result

            logger.warning("API提取完全失败，使用降级正则提取")
            return self._fallback_extract(text)

        except Exception as e:
            logger.error(f"提取异常: {e}", exc_info=True)
            return None

    def extract_from_search_results(
        self,
        raw_input: str,
        search_results: list[SearchResult],
    ) -> Optional[AccidentExtractionResult]:
        """综合原始输入和搜索结果进行提取。

        Args:
            raw_input: 用户输入的原始事故信息
            search_results: 联网搜索到的相关结果

        Returns:
            提取结果
        """
        if not self.client:
            logger.error("Anthropic 客户端未初始化")
            return None

        # 构建增强提示
        context = f"## 用户输入的原始事故信息\n{raw_input}\n\n"
        if search_results:
            context += "## 联网检索到的相关信息\n\n"
            for i, result in enumerate(search_results, 1):
                context += f"### 来源 {i}: {result.title}\n"
                context += f"URL: {result.url}\n"
                context += f"摘要: {result.snippet}\n\n"

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=EXTRACTION_SYSTEM_PROMPT + "\n\n注意：你获得了联网搜索到的补充信息。综合分析这些信息，提取最准确的结构化数据。",
                messages=[{
                    "role": "user",
                    "content": f"请综合分析以下建筑施工事故信息（包括用户输入和联网搜索结果），提取结构化数据并进行致因分析：\n\n{context}",
                }],
            )

            result = self._parse_json_response(response)
            if result:
                # 合并搜索结果的 URL
                all_urls = set(result.source_urls)
                for sr in search_results:
                    if sr.url:
                        all_urls.add(sr.url)
                result.source_urls = list(all_urls)
                return result

            return None

        except Exception as e:
            logger.error(f"综合提取异常: {e}", exc_info=True)
            return None

    def _extract_urls(self, text: str) -> list[str]:
        """从文本提取 URL。"""
        import re
        urls = re.findall(r'https?://[^\s）)）]+', text)
        return urls

    def _parse_json_response(self, response) -> Optional[AccidentExtractionResult]:
        """从响应中解析 JSON，自动修复字段名差异，捕获中间数据。"""
        import re

        meta = {
            "thinking_block": "", "raw_texts": [],
            "json_attempted": "", "parse_error": "", "parse_success": False,
        }

        if hasattr(response, "usage") and response.usage:
            meta["prompt_tokens"] = response.usage.input_tokens or 0
            meta["completion_tokens"] = response.usage.output_tokens or 0

        for block in response.content:
            if hasattr(block, "thinking") and block.thinking:
                meta["thinking_block"] = block.thinking

            # 模式1: tool_use
            if hasattr(block, "type") and block.type == "tool_use":
                if hasattr(block, "name") and block.name == "extract_accident_info":
                    try:
                        result = AccidentExtractionResult(**block.input)
                        meta["parse_success"] = True
                        self.last_api_meta = meta
                        return result
                    except Exception as e:
                        meta["parse_error"] = f"tool_use: {e}"
                        # 尝试字段映射修复
                        fixed = self._fix_field_names(block.input)
                        try:
                            result = AccidentExtractionResult(**fixed)
                            logger.info("tool_use 字段映射修复成功")
                            meta["parse_success"] = True
                            self.last_api_meta = meta
                            return result
                        except Exception:
                            continue

            # 模式2: 文本 JSON
            if hasattr(block, "type") and block.type == "text":
                text = block.text
                meta["raw_texts"].append(text[:5000])

                # 解析 JSON
                parsed = None
                # 尝试1: 直接 parse
                try: parsed = json.loads(text)
                except: pass
                # 尝试2: code block
                if not parsed:
                    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                    if m:
                        try: parsed = json.loads(m.group(1))
                        except: pass
                # 尝试3: 智能 brace
                if not parsed:
                    start = text.find('{')
                    if start >= 0:
                        depth = 0; end = -1
                        for i in range(start, len(text)):
                            if text[i] == '{': depth += 1
                            elif text[i] == '}':
                                depth -= 1
                                if depth == 0: end = i + 1; break
                        if end > 0:
                            try: parsed = json.loads(text[start:end])
                            except: pass
                # 尝试4: 贪婪 brace
                if not parsed:
                    m = re.search(r'\{[\s\S]*\}', text)
                    if m:
                        s = m.group(0)
                        diff = s.count('{') - s.count('}')
                        if diff > 0: s += '}' * diff
                        try: parsed = json.loads(s)
                        except: pass

                if parsed:
                    meta["json_attempted"] = json.dumps(parsed, ensure_ascii=False, indent=2)[:5000]
                    # 先尝试直接映射，失败则字段修复
                    try:
                        result = AccidentExtractionResult(**parsed)
                        meta["parse_success"] = True
                        self.last_api_meta = meta
                        return result
                    except Exception as e:
                        fixed = self._fix_field_names(parsed)
                        try:
                            result = AccidentExtractionResult(**fixed)
                            logger.info(f"text JSON 字段映射修复成功")
                            meta["parse_success"] = True
                            self.last_api_meta = meta
                            return result
                        except Exception as e2:
                            meta["parse_error"] = f"text JSON: {e} / fixed: {e2}"
                else:
                    meta["parse_error"] = "未找到有效JSON"

        self.last_api_meta = meta
        return None

    def encode_hfacs(self, text: str, factors: dict) -> list[str]:
        """第二步：基于已提取的致因，进行 HFACS-CS 编码。

        Args:
            text: 事故原始文本
            factors: {"human": [...], "machine": [...], "management": [...], "environmental": [...]}

        Returns:
            HFACS编码列表，如 ["A1", "S1", "W2"]
        """
        if not self.client:
            return []

        factors_str = json.dumps(factors, ensure_ascii=False, indent=2)

        system = """你是施工安全HFACS编码专家。根据事故文本和已提取的致因，给出HFACS-CS编码。

编码规则 (29选N):
L1组织: O1安全文化缺失 O2分包/承包商管理缺陷 O3工期/成本压力 O4培训体系不健全
L2监督: S1现场监管缺失 S2隐患排查不充分 S3制度规程不健全 S4违规组织施工 S5培训交底不足
L3前提: P1操作技能不足 P2安全意识薄弱 P3生理心理不佳 P4沟通协调不足
L4行为: A1违章操作 A2违规指挥 A3冒险作业 A4未使用PPE A5操作失误
L5现场: W1脚手架模板缺陷 W2临时设施防护缺陷 W3设备机具故障 W4构筑物结构缺陷 W5材料质量问题 W6基坑地基不良
L6环境: E1不良地质 E2恶劣天气 E3作业环境复杂 E4照明不足 E5外部干扰

输出格式: {"hfacs_codes": ["A1", "S2", "W1"]}
只输出JSON，不要其他文字。"""

        try:
            response = self.client.messages.create(
                model=self.model,
                max_tokens=512,
                temperature=0.0,
                system=system,
                messages=[{"role": "user", "content": f"事故文本:\n{text[:3000]}\n\n已提取致因:\n{factors_str}\n\n请给出HFACS编码。"}],
            )
            raw = ""
            for blk in response.content:
                if hasattr(blk, "text"): raw += blk.text
            import re
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                try:
                    data = json.loads(m.group(0))
                    codes = data.get("hfacs_codes", [])
                    if codes:
                        logger.info(f"HFACS编码: {len(codes)}个 → {codes}")
                        return codes
                except json.JSONDecodeError:
                    pass
            # 回退: 从文本中直接提取编码
            codes = re.findall(r'\b[OSPAWE]\d\b', raw)
            if codes:
                codes = list(dict.fromkeys(codes))  # 去重保序
                logger.info(f"HFACS编码(回退): {len(codes)}个 → {codes}")
                return codes
        except Exception as e:
            logger.warning(f"HFACS编码失败: {e}")
        return []

    @staticmethod
    def _fallback_extract(text: str) -> Optional[AccidentExtractionResult]:
        """降级提取：用正则从文本中提取基本伤亡信息，API全失败时兜底。"""
        import re
        deaths = 0; injuries = 0
        dm = re.search(r'(\d+)人死亡', text) or re.search(r'死亡(\d+)人', text) or re.search(r'造成(\d+)人死亡', text)
        if dm: deaths = int(dm.group(1))
        im = re.search(r'(\d+)人受伤', text) or re.search(r'重伤(\d+)人', text)
        if im: injuries = int(im.group(1))

        # 类型推断
        atype = "其他"
        type_kw = {"坍塌":["坍塌","垮塌","倒塌"],"高处坠落":["坠落","高坠"],"触电":["触电","电击"],
                   "起重伤害":["塔吊","起重机","升降机"],"物体打击":["砸中","打击"],
                   "土方坍塌":["基坑","沟槽","边坡"],"火灾爆炸":["火灾","爆炸","燃爆"],
                   "中毒窒息":["中毒","窒息","有毒"],"淹溺":["溺水","淹溺"]}
        for t, kws in type_kw.items():
            if any(kw in text for kw in kws): atype = t; break

        return AccidentExtractionResult(
            title=text[:80].split('\n')[0].strip('# '),
            accident_type=atype, severity="较大事故" if deaths>=3 else "一般事故",
            deaths=deaths, injuries=injuries,
            summary=text[:200], human_factors=[], machine_factors=[],
            management_factors=[], environmental_factors=[],
        )

    # ── 字段名映射 ──
    @staticmethod
    def _fix_field_names(data: dict) -> dict:
        """将 DeepSeek 可能用的非标准字段名映射到标准字段名。"""
        field_map = {
            "accident_title": "title", "name": "title",
            "accident_date": "date", "happen_date": "date",
            "province": "location", "level": "severity",
            "severity_level": "severity", "accident_level": "severity",
            "death_toll": "deaths", "death_count": "deaths", "fatalities": "deaths",
            "injury_count": "injuries", "injured": "injuries",
            "economic_loss": "direct_economic_loss", "loss": "direct_economic_loss",
            "cause": "direct_cause", "direct_cause_text": "direct_cause",
            "accident_summary": "summary", "description": "summary",
            "contractor_name": "contractor",
            "human_factor_list": "human_factors", "machine_factor_list": "machine_factors",
            "management_factor_list": "management_factors",
            "environmental_factor_list": "environmental_factors",
        }
        fixed = dict(data)
        for old, new in field_map.items():
            if old in fixed and new not in fixed:
                fixed[new] = fixed.pop(old)
        # 递归修复嵌套对象中的字段名
        for key in ["human_factors", "human_factor_list", "machine_factors", "machine_factor_list",
                     "management_factors", "management_factor_list",
                     "environmental_factors", "environmental_factor_list"]:
            if key in fixed and isinstance(fixed[key], list):
                fixed[key] = [
                    {field_map.get(k, k): v for k, v in item.items()} if isinstance(item, dict) else item
                    for item in fixed[key]
                ]
        return fixed

        self.last_api_meta = meta
        return None


class ExtractionPipeline:
    """完整的提取流水线：搜索 → 提取 → 格式化。"""

    def __init__(self, repository=None):
        from construction_agent.search.search_integration import SearchIntegrator
        self.integrator = SearchIntegrator()
        self.searcher = self.integrator.text_searcher
        self.image_searcher = self.integrator.image_searcher
        self.extractor = ClaudeExtractor()
        self.repository = repository  # 可选：用于写入中间数据日志

    def process(self, raw_input: str, enable_search: bool = True, source: str = "",
                repository=None) -> Optional[Accident]:
        """处理单条事故信息。

        Args:
            raw_input: 用户输入的原始事故信息
            enable_search: 是否启用联网搜索增强
            source: 数据来源标注（PDF识别/新闻报告/用户输入）
            repository: 数据仓库（用于写入中间数据日志）

        Returns:
            结构化的事故数据
        """
        import time as _time
        repo = repository or self.repository
        t_start = _time.time()

        logger.info(f"开始处理事故: {raw_input[:80]}...")

        # Step 0: 预筛选 — 非单条事故单独标记
        text_class = ClaudeExtractor.classify_text(raw_input)
        if not text_class["is_single"]:
            logger.warning(f"预筛选标记: {text_class['flag']} — {text_class['reason']}")
            from pathlib import Path as _Path
            _flag_file = _Path("data/flagged_multi_accidents.json")
            _existing = []
            if _flag_file.exists():
                try: _existing = json.loads(_flag_file.read_text(encoding='utf-8'))
                except: pass
            _existing.append({"text_preview": raw_input[:500], "flag": text_class["flag"],
                              "reason": text_class["reason"], "length": len(raw_input)})
            _flag_file.write_text(json.dumps(_existing, ensure_ascii=False, indent=2), encoding='utf-8')
            return None

        # Step 1: 联网文本搜索 + 图片搜索（可选）
        if enable_search:
            logger.info("Step 1/3: 联网检索相关信息...")
            search_keyword = raw_input[:80]
            search_data = self.integrator.process_full(
                text=raw_input,
                keyword=search_keyword,
                accident_id="tmp",
                enable_text_search=True,
                enable_image_search=True,
            )
            enhanced_text = search_data["enhanced_text"]
            # 记录搜索日志
            if repo and search_data.get("search_results"):
                for sr in search_data["search_results"]:
                    try:
                        repo.log_search(SearchLogRecord(
                            processing_id=0,
                            engine="baidu",
                            query=search_keyword,
                            raw_html=getattr(sr, "raw_html", ""),
                            result_count=len(search_data["search_results"]),
                            result_positions=json.dumps([
                                {"url": getattr(r, "url", ""), "title": getattr(r, "title", "")}
                                for r in search_data["search_results"]
                            ], ensure_ascii=False),
                            success=True, duration_ms=0,
                        ))
                    except Exception:
                        pass  # 日志失败不中断主流程
        else:
            logger.info("Step 1/3: 联网搜索已禁用，直接使用原始文本")
            search_data = {"enhanced_text": raw_input, "search_results": [],
                          "search_queries": [], "snippets_meta": [],
                          "image_sources": [], "image_local_paths": []}
            enhanced_text = raw_input

        # Step 2: 特征提取
        logger.info("Step 2/3: 调用 Claude API 提取特征...")
        t_llm_start = _time.time()
        if search_data["search_results"]:
            extraction_result = self.extractor.extract_from_search_results(
                raw_input, search_data["search_results"]
            )
        else:
            extraction_result = self.extractor.extract(raw_input)
        t_llm_ms = int((_time.time() - t_llm_start) * 1000)

        # 记录 LLM 日志
        if repo:
            meta = self.extractor.last_api_meta
            try:
                repo.log_llm_api(LlmApiRecord(
                    processing_id=0,
                    model=self.extractor.model,
                    system_prompt=EXTRACTION_SYSTEM_PROMPT[:5000] if EXTRACTION_SYSTEM_PROMPT else "",
                    user_prompt=enhanced_text[:10000],
                    raw_response=json.dumps(
                        {"raw_texts": meta.get("raw_texts", [])[:3]},
                        ensure_ascii=False
                    )[:50000],
                    thinking_block=meta.get("thinking_block", "")[:20000],
                    json_extracted=meta.get("json_attempted", "")[:10000],
                    parse_success=meta.get("parse_success", False),
                    parse_error=meta.get("parse_error", ""),
                    prompt_tokens=meta.get("prompt_tokens", 0),
                    completion_tokens=meta.get("completion_tokens", 0),
                    duration_ms=t_llm_ms,
                ))
            except Exception:
                pass

        if not extraction_result:
            logger.error("特征提取失败")
            # 记录失败处理历史
            if repo:
                try:
                    repo.log_processing(ProcessingRecord(
                        accident_id=None, pipeline_version="v2",
                        status="failed",
                        input_summary=raw_input[:100],
                        error_message=meta.get("parse_error", "特征提取失败"),
                        error_category="PARSE_ERROR" if meta.get("parse_error") else "API_ERROR",
                        duration_ms=int((_time.time() - t_start) * 1000),
                        token_usage_prompt=meta.get("prompt_tokens", 0),
                        token_usage_completion=meta.get("completion_tokens", 0),
                    ))
                except Exception:
                    pass
            return None

        # 转换为完整的 Accident 模型
        accident = extraction_result.to_accident(raw_input=raw_input)
        if source:
            accident.source = source

        # 归一化地点为省级行政区
        if accident.location:
            original = accident.location
            accident.location = normalize_province(accident.location)
            if original != accident.location:
                logger.info(f"地点归一化: {original} → {accident.location}")

        # Step 3: HFACS-CS 编码（独立 API 调用，避免注意力稀释）
        logger.info("Step 3/4: HFACS-CS 编码...")
        try:
            factors = {
                "human": [f.category for f in accident.human_factors],
                "machine": [f.category for f in accident.machine_factors],
                "management": [f.category for f in accident.management_factors],
                "environmental": [f.category for f in accident.environmental_factors],
            }
            hfacs_codes = self.extractor.encode_hfacs(raw_input, factors)
            if hfacs_codes:
                accident.hfacs_codes = hfacs_codes
        except Exception as e:
            logger.warning(f"HFACS编码异常: {e}")

        # Step 4: 整合搜索元数据到 Accident
        logger.info("Step 4/4: 整合搜索元数据...")
        self.integrator.integrate_into_accident(
            accident=accident,
            search_results=search_data["search_results"],
            search_queries=search_data["search_queries"],
            snippets_meta=search_data["snippets_meta"],
            image_sources=search_data["image_sources"],
            image_local_paths=search_data["image_local_paths"],
        )
        logger.info(
            f"搜索元数据: {len(accident.search_snippets)}条摘要, "
            f"{len(accident.source_urls)}个URL, "
            f"{len(accident.images)}张图片"
        )

        logger.info(f"处理完成: {accident.title}")

        # 记录成功处理历史
        if repo:
            try:
                meta = self.extractor.last_api_meta
                repo.log_processing(ProcessingRecord(
                    accident_id=accident.id,
                    pipeline_version="v2",
                    status="success",
                    input_summary=raw_input[:100],
                    duration_ms=int((_time.time() - t_start) * 1000),
                    token_usage_prompt=meta.get("prompt_tokens", 0),
                    token_usage_completion=meta.get("completion_tokens", 0),
                ))
            except Exception:
                pass

        return accident

    def process_batch(self, inputs: list[str], enable_search: bool = True, source: str = "",
                      repository=None) -> list[Accident]:
        """批量处理多条事故信息。

        Args:
            inputs: 事故信息文本列表
            enable_search: 是否启用联网搜索增强（默认开启）
            source: 数据来源标注（如 PDF识别/新闻报告）
            repository: 数据仓库（用于写入中间数据日志）

        Returns:
            结构化事故数据列表
        """
        num = len(inputs)
        results = []
        for i, text in enumerate(inputs, 1):
            logger.info(f"--- 处理第 {i}/{num} 条 ---")
            result = self.process(text.strip(), enable_search=enable_search, source=source,
                                  repository=repository)
            if result:
                results.append(result)
            else:
                logger.warning(f"第 {i} 条处理失败，跳过")
        return results
