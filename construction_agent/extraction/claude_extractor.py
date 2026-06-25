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
from construction_agent.utils.location import normalize_province

logger = logging.getLogger(__name__)

# ── Tool / 结构化输出定义 ──

EXTRACTION_TOOL = {
    "name": "extract_accident_info",
    "description": "提取建筑施工事故的结构化信息",
    "input_schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "事故标题/名称"},
            "date": {"type": "string", "description": "事故发生日期，严格ISO格式 YYYY-MM-DD，如2024-03-15。不要使用中文格式。如果不确定填null"},
            "location": {"type": "string", "description": "事故地点（仅省级：省/自治区/直辖市/特别行政区），如：广东省"},
            "project_name": {"type": "string", "description": "工程项目名称"},
            "accident_type": {
                "type": "string",
                "enum": ["坍塌", "高处坠落", "触电", "机械伤害", "起重伤害", "物体打击", "土方坍塌", "火灾爆炸", "中毒窒息", "淹溺", "其他"],
                "description": "事故类型",
            },
            "severity": {
                "type": "string",
                "enum": ["一般事故", "较大事故", "重大事故", "特别重大事故"],
                "description": "事故等级（一般:≤2死, 较大:3-9死, 重大:10-29死, 特别重大:≥30死）",
            },
            "deaths": {"type": "integer", "description": "死亡人数（若无则为0）"},
            "injuries": {"type": "integer", "description": "受伤人数（若无则为0）"},
            "direct_economic_loss": {"type": "number", "description": "直接经济损失（万元），若无填null"},
            # ── 新增基础特征 ──
            "accident_time": {"type": "string", "description": "事故发生具体时刻，HH:MM格式，如14:52。不确定填null"},
            "city": {"type": "string", "description": "地级市，如齐齐哈尔市、广州市。注意：直辖市（北京/上海/天津/重庆）也要填写city如'北京市'。不确定填空字符串"},
            "accident_location_detail": {"type": "string", "description": "事故发生的具体部位，如'3#楼北侧外脚手架'、'B区基坑'、'2层作业面'。不确定填空字符串"},
            "project_type": {
                "type": "string",
                "description": "工程类型。房屋建筑工程（含住宅/公建/学校/体育馆/商场等）、市政基础设施工程（道路/桥梁/管网等）、交通运输工程（铁路/地铁/隧道/公路）、水利工程（水库/水坝/水渠）、工业厂房、装饰装修工程、拆除工程、其他。不确定填空字符串",
            },
            "building_structure": {"type": "string", "description": "建筑结构类型，如砖混结构、钢结构、框架结构、剪力墙结构等。不确定填空字符串"},
            "building_completion_year": {"type": "string", "description": "建筑建成年份，如2020。不确定填空字符串"},
            "construction_phase": {"type": "string", "description": "施工阶段，如主体施工、装饰装修、拆除工程等。从文本中提取原始描述，不修改字眼。不确定填空字符串"},
            "weather": {
                "type": "string",
                "description": "注意从文本中提取气象描述！如文中提及降雨、暴雨、大风、高温、雨雪、大雾等天气，据此判断。选项：天气良好/大风/暴雨/雨雪/高温/无法判断。文本中提到了'降雨''暴雨''雨'等则填对应值，未提及则填无法判断",
            },
            "human_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "分类，如：违章操作、安全意识不足、疲劳作业、技能不足、未佩戴PPE、违规指挥等"},
                        "description": {"type": "string", "description": "具体描述"},
                    },
                    "required": ["category", "description"],
                },
                "description": "人的不安全行为因素",
            },
            "machine_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "分类，如：设备故障、防护缺陷、工具不合格、安全装置失效、脚手架问题等"},
                        "description": {"type": "string", "description": "具体描述"},
                    },
                    "required": ["category", "description"],
                },
                "description": "设备/物的不安全状态因素",
            },
            "management_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "分类，如：监管缺失、安全制度不健全、培训不足、隐患排查不到位、应急预案缺失、违法分包等"},
                        "description": {"type": "string", "description": "具体描述"},
                    },
                    "required": ["category", "description"],
                },
                "description": "管理因素",
            },
            "environmental_factors": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "category": {"type": "string", "description": "分类，如：恶劣天气、地质条件不良、作业环境复杂、照明不足等"},
                        "description": {"type": "string", "description": "具体描述"},
                    },
                    "required": ["category", "description"],
                },
                "description": "环境因素",
            },
            "summary": {"type": "string", "description": "事故经过摘要"},
            "direct_cause": {"type": "string", "description": "事故直接原因简述，如'污水支管施工开挖过程中发生坍塌'、'违规堆放珍珠岩致荷载超限'。从文本中提取，不确定填空字符串"},
            "contractor": {"type": "string", "description": "施工总承包单位名称，如'中铁十六局'、'黑龙江嘉美建设有限公司'。从文本中提取，不确定填空字符串"},
            "trapped_count": {"type": "integer", "description": "事故中被困人数，区别于死亡和受伤人数。如'3人被困'则填3。未提及填0"},
        },
        "required": ["title", "accident_type", "severity", "deaths", "injuries", "summary"],
    },
}

# ── 系统提示词 ──

EXTRACTION_SYSTEM_PROMPT = """你是一名建筑施工安全事故分析专家。你的任务是从事故信息中提取结构化数据并进行致因分析。

## 事故类型
- 坍塌：建筑物/构筑物/脚手架/模板坍塌
- 高处坠落：从高处坠落
- 触电：接触电流
- 机械伤害：机械设备造成伤害
- 起重伤害：起重设备造成伤害
- 物体打击：被物体砸中/打击
- 土方坍塌：基坑/沟槽/边坡坍塌
- 火灾爆炸：火灾或爆炸
- 中毒窒息：有毒气体/缺氧环境
- 淹溺：溺水

## 事故等级划分
- 一般事故：造成1-2人死亡
- 较大事故：造成3-9人死亡
- 重大事故：造成10-29人死亡
- 特别重大事故：造成30人以上死亡

## 致因分析要求
请从以下四个维度进行事故致因分析：

1. **人的因素**：违章操作、违规指挥、安全意识不足、疲劳作业、技能不足、未佩戴PPE、酒后作业等
2. **设备/物因素**：设备故障、防护缺陷、工具不合格、安全装置失效、脚手架/模板问题、临时设施问题等
3. **管理因素**：监管缺失、安全制度不健全、培训不足、隐患排查不到位、应急预案缺失、违法分包/转包、抢工期等
4. **环境因素**：恶劣天气（大风/暴雨/高温/冰冻）、地质条件不良、作业环境复杂、照明不足、噪音等

对于每一条因素，需提供分类和具体描述。
如果信息不足无法判断某类因素，返回空数组即可，不要编造。

## 输出格式
你必须以**纯 JSON 格式**输出，不要包含 markdown 代码块标记或其他文字说明。
直接输出 JSON 对象，字段如下：
{
  "title": "事故标题",
  "date": "ISO格式 YYYY-MM-DD（如2024-03-15），不要写中文格式如2024年3月15日，未知则为null",
  "accident_time": "HH:MM格式，如14:52，未知则为null",
  "location": "省级行政区（省/自治区/直辖市/特别行政区），如广东省、北京市。只输出省级，不要写市/县/区",
  "city": "地级市，如齐齐哈尔市、广州市，未知则为空字符串",
  "accident_location_detail": "事故具体部位，如3#楼北侧外脚手架，未知则为空字符串",
  "project_name": "项目名称",
  "project_type": "房屋建筑工程（含住宅/学校/商场等）/交通运输工程（铁路/地铁/隧道）/市政基础设施工程（道路/桥梁/管网）/水利工程/工业厂房/装饰装修工程/拆除工程/其他",
  "building_structure": "建筑结构类型，如砖混结构、钢结构，未知则为空字符串",
  "building_completion_year": "建筑建成年份，如2020，未知则为空字符串",
  "construction_phase": "施工阶段，如主体施工、装饰装修、拆除，未知则为空字符串",
  "accident_type": "坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/火灾爆炸/中毒窒息/淹溺/其他",
  "severity": "一般事故/较大事故/重大事故/特别重大事故",
  "deaths": 整数,
  "injuries": 整数,
  "direct_economic_loss": 浮点数或null,
  "weather": "注意从文本提取！有降雨则填暴雨/雨雪，有大风则填大风，未提及则填无法判断",
  "direct_cause": "事故直接原因简述，如'污水支管施工开挖过程中发生坍塌'",
  "contractor": "施工总承包单位名称，如'中铁十六局'",
  "trapped_count": 3,
  "human_factors": [{"category": "分类", "description": "具体描述"}],
  "machine_factors": [{"category": "分类", "description": "具体描述"}],
  "management_factors": [{"category": "分类", "description": "具体描述"}],
  "environmental_factors": [{"category": "分类", "description": "具体描述"}],
  "summary": "事故经过摘要"
}"""


class ClaudeExtractor:
    """基于 Claude API 的事故特征提取器。"""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or settings.ANTHROPIC_API_KEY
        self.model = settings.ANTHROPIC_MODEL
        self.max_tokens = settings.ANTHROPIC_MAX_TOKENS
        self.client: Anthropic | None = None

        if self.api_key:
            self.client = Anthropic(api_key=self.api_key)
        else:
            logger.warning("Anthropic API Key 未配置")

    def extract(self, text: str) -> Optional[AccidentExtractionResult]:
        """从文本中提取事故特征。

        Args:
            text: 事故相关文本

        Returns:
            提取结果，失败返回 None
        """
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

            # 解析 JSON 响应
            result = self._parse_json_response(response)
            if result:
                result.source_urls = self._extract_urls(text)
                logger.info(f"成功提取事故: {result.title}")
                return result

            logger.warning("无法从响应中解析 JSON")
            return None

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
        """从响应中解析 JSON，支持 tool_use 和 text 两种模式。"""
        import re

        for block in response.content:
            # 模式1: tool_use 响应
            if block.type == "tool_use" and block.name == "extract_accident_info":
                try:
                    return AccidentExtractionResult(**block.input)
                except Exception as e:
                    logger.warning(f"tool_use 解析失败: {e}")
                    continue

            # 模式2: 文本响应中提取 JSON
            if block.type == "text":
                text = block.text

                # 尝试找 ```json ... ``` 代码块
                json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                if json_match:
                    try:
                        data = json.loads(json_match.group(1))
                        return AccidentExtractionResult(**data)
                    except (json.JSONDecodeError, Exception):
                        pass

                # 尝试直接找 {...} 最外层 JSON 对象
                brace_match = re.search(r'\{[\s\S]*\}', text)
                if brace_match:
                    try:
                        data = json.loads(brace_match.group(0))
                        return AccidentExtractionResult(**data)
                    except (json.JSONDecodeError, Exception):
                        pass

        return None


class ExtractionPipeline:
    """完整的提取流水线：搜索 → 提取 → 格式化。"""

    def __init__(self):
        from construction_agent.search.search_integration import SearchIntegrator
        self.integrator = SearchIntegrator()
        self.searcher = self.integrator.text_searcher
        self.image_searcher = self.integrator.image_searcher
        self.extractor = ClaudeExtractor()

    def process(self, raw_input: str) -> Optional[Accident]:
        """处理单条事故信息。

        流程：
        1. 联网搜索更多相关信息（文本+图片）
        2. 综合原始输入和搜索结果进行特征提取
        3. 保存完整的搜索元数据
        4. 返回结构化的 Accident 对象

        Args:
            raw_input: 用户输入的原始事故信息

        Returns:
            结构化的事故数据
        """
        logger.info(f"开始处理事故: {raw_input[:80]}...")

        # Step 1: 联网文本搜索 + 图片搜索
        logger.info("Step 1/3: 联网检索相关信息...")
        search_keyword = raw_input[:80]
        search_data = self.integrator.process_full(
            text=raw_input,
            keyword=search_keyword,
            accident_id="tmp",  # 临时 ID，提取后会被覆盖
            enable_text_search=True,
            enable_image_search=True,
        )

        enhanced_text = search_data["enhanced_text"]

        # Step 2: 特征提取（使用增强后的文本）
        logger.info("Step 2/3: 调用 Claude API 提取特征...")
        if search_data["search_results"]:
            extraction_result = self.extractor.extract_from_search_results(
                raw_input, search_data["search_results"]
            )
        else:
            extraction_result = self.extractor.extract(raw_input)

        if not extraction_result:
            logger.error("特征提取失败")
            return None

        # 转换为完整的 Accident 模型
        accident = extraction_result.to_accident(raw_input=raw_input)

        # 归一化地点为省级行政区
        if accident.location:
            original = accident.location
            accident.location = normalize_province(accident.location)
            if original != accident.location:
                logger.info(f"地点归一化: {original} → {accident.location}")

        # Step 3: 整合搜索元数据到 Accident
        logger.info("Step 3/3: 整合搜索元数据...")
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
        return accident

    def process_batch(self, inputs: list[str]) -> list[Accident]:
        """批量处理多条事故信息。

        Args:
            inputs: 事故信息文本列表

        Returns:
            结构化事故数据列表
        """
        results = []
        for i, text in enumerate(inputs, 1):
            logger.info(f"--- 处理第 {i}/{len(inputs)} 条 ---")
            result = self.process(text.strip())
            if result:
                results.append(result)
            else:
                logger.warning(f"第 {i} 条处理失败，跳过")
        return results
