"""多模态事故图片分析模块 — 两阶段 VLM 设计。

Phase 1 (盲观察): VLM 纯看图，客观描述场景，不给事故上下文
Phase 2 (上下文补充): 接收 Phase 1 观察 + 文本提取事实，做交叉验证

VLM: 通义千问 VL (DashScope API, qwen-vl-max)
图片匹配: 仅分析已有本地图片，不重新搜索下载(百度反爬)

输出:
  - phase1_observation: 场景描述(场景类型/物体/人员/损伤/天气)
  - phase2_verification: 一致性判断 + 信息增益 + 矛盾检测 + 可信度
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")
DASHSCOPE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
VL_MODEL = "qwen-vl-max"


# ══════════════════════════════════════════════════════════════════
# 数据类
# ══════════════════════════════════════════════════════════════════

@dataclass
class Phase1Observation:
    """Phase 1: VLM 盲观察结果。"""
    scene_type: str = ""              # 工地/废墟/救援现场/文档截图/其他
    objects_visible: list[str] = field(default_factory=list)  # 塔吊/脚手架/钢筋...
    people_visible: str = ""          # 可见人员的数量和状态
    damage_signs: str = ""            # 坍塌范围/结构变形/散落物
    weather_lighting: str = ""        # 天气和光照
    raw_response: str = ""            # VLM 完整原始回复


@dataclass
class Phase2Verification:
    """Phase 2: 交叉验证结果。"""
    consistency: str = ""             # "一致" / "部分一致" / "矛盾"
    consistency_detail: str = ""      # 具体哪里一致/矛盾
    new_info_from_image: list[str] = field(default_factory=list)  # 图片有但文本没有的信息
    text_unverifiable: list[str] = field(default_factory=list)    # 文本有但图片无法验证的
    confidence: str = ""              # 可信度: 强佐证/中等佐证/弱佐证/矛盾
    raw_response: str = ""


@dataclass
class ImageAnalysisResult:
    """单张图片的完整分析结果。"""
    image_path: str = ""
    source_url: str = ""
    phase1: Optional[Phase1Observation] = None
    phase2: Optional[Phase2Verification] = None
    error: Optional[str] = None
    _elapsed: float = 0.0

    def to_dict(self) -> dict:
        return {
            "image_path": self.image_path,
            "source_url": self.source_url,
            "phase1": {
                "scene_type": self.phase1.scene_type,
                "objects_visible": self.phase1.objects_visible,
                "people_visible": self.phase1.people_visible,
                "damage_signs": self.phase1.damage_signs,
                "weather_lighting": self.phase1.weather_lighting,
            } if self.phase1 else None,
            "phase2": {
                "consistency": self.phase2.consistency,
                "consistency_detail": self.phase2.consistency_detail,
                "new_info_from_image": self.phase2.new_info_from_image,
                "text_unverifiable": self.phase2.text_unverifiable,
                "confidence": self.phase2.confidence,
            } if self.phase2 else None,
            "error": self.error,
            "elapsed": self._elapsed,
        }


# ══════════════════════════════════════════════════════════════════
# Prompts
# ══════════════════════════════════════════════════════════════════

PHASE1_PROMPT = """你是一个建筑施工事故现场观察员。请客观描述这张图片的内容，不要推测事故原因或伤亡人数。

请按以下格式回复：

## 场景类型
（工地/建筑/废墟/道路/救援现场/文档截图/其他）

## 可见物体和结构
列出图中可见的相关物体（塔吊/脚手架/挖掘机/钢筋/混凝土/围挡/车辆等）

## 可见人员
描述图中人员的数量和状态（工人/救援人员/围观群众/无人员）

## 损伤迹象
描述可见的损伤（坍塌范围/结构变形/散落物/裂缝等）

## 天气和光照
描述天气状况和光照条件"""

PHASE2_PROMPT = """你是建筑施工事故分析专家。请结合以下信息，判断图片与文本描述的一致性。

## 图片独立观察
{phase1_observation}

## 文本提取结果
事故类型: {accident_type}
等级: {severity}
死亡: {deaths}人  受伤: {injuries}人
城市: {city}
事发作业: {work_task}
施工单位: {contractor}
坍塌子类型: {collapse_subtype}
工程类型: {project_type}

## 任务
1. **一致性判断**: 图片与文本描述是否一致？（地点/类型/规模）
2. **信息增益**: 图片中有哪些文本未提及的信息？
3. **无法验证项**: 文本中有哪些信息无法从图片中验证？
4. **综合可信度**: 图片为本次事故提供什么级别的佐证？（强佐证/中等佐证/弱佐证/矛盾）

请简洁回复。"""


# ══════════════════════════════════════════════════════════════════
# 主类
# ══════════════════════════════════════════════════════════════════

class ImageAnalyzer:
    """两阶段 VLM 事故图片分析器。

    用法:
        analyzer = ImageAnalyzer()
        results = analyzer.analyze_images(
            image_paths=["data/images/abc001_01.jpg", ...],
            accident_context={"accident_type":"坍塌", "deaths":3, ...},
        )
    """

    def __init__(self):
        self._api_key = DASHSCOPE_API_KEY
        self._enabled = bool(self._api_key)
        if not self._enabled:
            logger.warning("DASHSCOPE_API_KEY 未配置，VLM 图片分析不可用")

    # ══════════════════════════════════════════════════════════
    # 公共 API
    # ══════════════════════════════════════════════════════════

    def analyze_images(
        self,
        image_paths: list[str],
        accident_context: Optional[dict] = None,
    ) -> list[ImageAnalysisResult]:
        """对多张事故图片执行两阶段分析。

        Args:
            image_paths: 图片本地路径列表
            accident_context: Step1 提取结果 (accident_type, city, deaths, ...)
        """
        results = []
        for path in image_paths:
            t0 = time.time()
            result = self._analyze_single(path, accident_context)
            result._elapsed = time.time() - t0
            results.append(result)
        return results

    def _analyze_single(
        self, path: str, context: Optional[dict],
    ) -> ImageAnalysisResult:
        """对单张图片执行两阶段分析。"""
        if not self._enabled:
            return ImageAnalysisResult(image_path=path, error="VLM API 未配置")

        img_b64 = self._encode_image(path)
        if not img_b64:
            return ImageAnalysisResult(image_path=path, error="图片读取/编码失败")

        # ── Phase 1: 盲观察 ──
        phase1 = self._phase1_blind_observation(img_b64)
        if not phase1:
            return ImageAnalysisResult(image_path=path, error="Phase 1 失败", _elapsed=0)

        # ── Phase 2: 上下文交叉验证 (仅当有事故信息时执行) ──
        phase2 = None
        if context:
            phase2 = self._phase2_verify(phase1, context)

        return ImageAnalysisResult(
            image_path=path, phase1=phase1, phase2=phase2,
        )

    # ══════════════════════════════════════════════════════════
    # Phase 1: 盲观察
    # ══════════════════════════════════════════════════════════

    def _phase1_blind_observation(self, img_b64: str) -> Optional[Phase1Observation]:
        """VLM 纯看图，不给任何事故上下文。"""
        messages = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                {"type": "text", "text": PHASE1_PROMPT},
            ],
        }]

        text = self._call_vlm(messages)
        if not text:
            return None

        return self._parse_phase1(text)

    def _parse_phase1(self, text: str) -> Phase1Observation:
        """从 VLM 文本回复中解析结构化字段。

        VL 回复格式（Markdown 风格标题+内容），如：
        ## 场景类型
        建筑废墟 / 救援现场
        ## 可见物体和结构
        塔吊、钢筋、混凝土碎块
        """
        obs = Phase1Observation(raw_response=text)

        # 按标题分段提取
        sections = {
            "场景类型": "",
            "可见物体": "",
            "可见人员": "",
            "损伤迹象": "",
            "天气": "",
        }

        current_section = None
        current_content = []

        for line in text.split('\n'):
            line = line.strip().lstrip('#').strip()
            # 检测分段标题
            if '场景类型' in line and len(line) < 20:
                if current_section: sections[current_section] = ' '.join(current_content)
                current_section = "场景类型"; current_content = []; continue
            if '可见物体' in line and len(line) < 20:
                if current_section: sections[current_section] = ' '.join(current_content)
                current_section = "可见物体"; current_content = []; continue
            if '可见人员' in line and len(line) < 20:
                if current_section: sections[current_section] = ' '.join(current_content)
                current_section = "可见人员"; current_content = []; continue
            if '损伤' in line and len(line) < 20:
                if current_section: sections[current_section] = ' '.join(current_content)
                current_section = "损伤迹象"; current_content = []; continue
            if '天气' in line and len(line) < 20:
                if current_section: sections[current_section] = ' '.join(current_content)
                current_section = "天气"; current_content = []; continue

            if current_section and line:
                current_content.append(line)

        # 保存最后一段
        if current_section:
            sections[current_section] = ' '.join(current_content)

        # 填充结果
        if sections["场景类型"]:
            obs.scene_type = sections["场景类型"][:200]

        if sections["可见物体"]:
            # 提取物体列表
            objects_text = sections["可见物体"]
            obs.objects_visible = [
                o.strip() for o in objects_text.replace('、', ',').replace('，', ',').split(',')
                if o.strip() and len(o.strip()) > 1
            ][:10]

        if sections["可见人员"]:
            obs.people_visible = sections["可见人员"][:300]

        if sections["损伤迹象"]:
            obs.damage_signs = sections["损伤迹象"][:500]

        if sections["天气"]:
            obs.weather_lighting = sections["天气"][:200]

        # 回退: 如果分段解析全失败, 用前500字
        if not any([obs.scene_type, obs.damage_signs, obs.people_visible]):
            obs.scene_type = "见原始响应"
            obs.damage_signs = text[:500]

        return obs

    # ══════════════════════════════════════════════════════════
    # Phase 2: 交叉验证
    # ══════════════════════════════════════════════════════════

    def _phase2_verify(
        self, phase1: Phase1Observation, context: dict,
    ) -> Optional[Phase2Verification]:
        """带上下文做交叉验证。"""
        prompt = PHASE2_PROMPT.format(
            phase1_observation=phase1.raw_response[:1500],
            accident_type=context.get("accident_type", "未知"),
            severity=context.get("severity", "未知"),
            deaths=context.get("deaths", 0),
            injuries=context.get("injuries", 0),
            city=context.get("city", "未知"),
            work_task=context.get("work_task", "未提及"),
            contractor=context.get("contractor", "未提及"),
            collapse_subtype=context.get("collapse_subtype", "不适用"),
            project_type=context.get("project_type", "未知"),
        )

        messages = [{"role": "user", "content": prompt}]
        text = self._call_vlm(messages, max_tokens=600)
        if not text:
            return None

        return self._parse_phase2(text)

    def _parse_phase2(self, text: str) -> Phase2Verification:
        """解析 Phase 2 的 VLM 回复。"""
        v = Phase2Verification(raw_response=text)

        lines = text.split('\n')
        for line in lines:
            line = line.strip()
            if '一致' in line and ('图片' in line or '文本' in line or '描述' in line):
                if '矛盾' in line or '不一致' in line:
                    v.consistency = "矛盾"
                elif '部分' in line or '基本' in line:
                    v.consistency = "部分一致"
                elif v.consistency == "":
                    v.consistency = "一致"
                v.consistency_detail = line

            if '强佐证' in line:
                v.confidence = "强佐证"
            elif '中等佐证' in line:
                v.confidence = "中等佐证"
            elif '弱佐证' in line:
                v.confidence = "弱佐证"
            elif '矛盾' in line and v.confidence == "":
                v.confidence = "矛盾"

            if '信息增益' in line or '未提及' in line or '补充' in line:
                info = line.split('增益', 1)[-1].strip() if '增益' in line else line
                info = info.split('未提及', 1)[-1].strip() if '未提及' in info else info
                if len(info) > 10:
                    v.new_info_from_image.append(info[:200])

            if '无法验证' in line or '不能验证' in line:
                v.text_unverifiable.append(line[:200])

        if not v.consistency:
            v.consistency = "未能判定"
        if not v.confidence:
            v.confidence = "中等佐证"

        return v

    # ══════════════════════════════════════════════════════════
    # VLM 调用
    # ══════════════════════════════════════════════════════════

    def _call_vlm(self, messages: list[dict], max_tokens: int = 800) -> Optional[str]:
        """调用千问 VL API。"""
        try:
            resp = httpx.post(
                DASHSCOPE_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": VL_MODEL,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                },
                timeout=60,
            )
            if resp.status_code != 200:
                logger.warning(f"VL API HTTP {resp.status_code}: {resp.text[:200]}")
                return None

            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except Exception as e:
            logger.warning(f"VL API 异常: {e}")
            return None

    # ══════════════════════════════════════════════════════════
    # 工具方法
    # ══════════════════════════════════════════════════════════

    @staticmethod
    def _encode_image(path: str) -> Optional[str]:
        """读取图片并 base64 编码。"""
        if not os.path.exists(path):
            logger.warning(f"图片不存在: {path}")
            return None
        try:
            with open(path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        except Exception as e:
            logger.warning(f"图片编码失败 [{path}]: {e}")
            return None

    # ══════════════════════════════════════════════════════════
    # 图片采集 (从搜索增强的同事故网页中提取)
    # ══════════════════════════════════════════════════════════

    def collect_images_from_search(
        self,
        same_accident_snippets: list,
        accident_id: str,
        max_images: int = 5,
    ) -> list[str]:
        """从搜索增强的同事故网页中采集图片, 天然避免错配。"""
        urls = self._extract_news_urls(same_accident_snippets)
        accident_dir = os.path.join(IMAGE_DIR, accident_id)
        os.makedirs(accident_dir, exist_ok=True)

        downloaded = []
        seen = set()
        web_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'text/html,application/xhtml+xml',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }

        for url in urls[:5]:
            if len(downloaded) >= max_images:
                break
            try:
                resp = httpx.get(url, headers=web_headers, timeout=10, follow_redirects=True)
                if resp.status_code != 200:
                    continue
                img_urls = re.findall(
                    r'<img[^>]+src=["\']([^"\']+)["\']',
                    resp.text, re.IGNORECASE,
                )
                for src in img_urls:
                    if len(downloaded) >= max_images:
                        break
                    if src.startswith('//'): src = 'https:' + src
                    elif src.startswith('/'):
                        from urllib.parse import urljoin
                        src = urljoin(url, src)
                    elif not src.startswith('http'):
                        continue
                    if re.search(r'(icon|logo|qrcode|qr_code|avatar|banner|emoji)', src, re.IGNORECASE):
                        continue
                    if not re.search(r'\.(jpg|jpeg|png)(\?|$)', src, re.IGNORECASE):
                        continue
                    url_hash = hashlib.md5(src.encode()).hexdigest()[:12]
                    if url_hash in seen:
                        continue
                    seen.add(url_hash)
                    path = self._download_and_filter(src, accident_dir, len(downloaded))
                    if path:
                        downloaded.append(path)
            except Exception:
                pass
        return downloaded

    @staticmethod
    def _extract_news_urls(snippets: list) -> list[str]:
        urls = []
        for s in (snippets or []):
            if isinstance(s, dict):
                u = s.get('url', '')
                if u and u.startswith('http'):
                    urls.append(u)
            elif isinstance(s, str):
                for m in re.finditer(r'https?://[^\s,，。]+', s):
                    urls.append(m.group(0))
        seen = set()
        unique = []
        for u in urls:
            u = u.rstrip('/.')
            if u not in seen:
                seen.add(u)
                if any(kw in u for kw in ['weixin.qq.com', 'weibo.com', 't.cn']):
                    unique.append(u)
                else:
                    unique.insert(0, u)
        return unique

    def _download_and_filter(self, img_url: str, target_dir: str, index: int):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.baidu.com/',
        }
        try:
            resp = httpx.get(img_url, headers=headers, timeout=15, follow_redirects=True)
            if resp.status_code != 200:
                return None
            content = resp.content
            size_kb = len(content) / 1024
            if size_kb < 10:
                return None
            w, h = self._guess_image_size(content)
            if w > 0 and h > 0 and (w < 200 or h < 200):
                return None
            ext = '.jpg'
            ct = resp.headers.get('content-type', '')
            if 'png' in ct: ext = '.png'
            elif 'webp' in ct: ext = '.webp'
            filepath = os.path.join(target_dir, f'{index:02d}{ext}')
            with open(filepath, 'wb') as f:
                f.write(content)
            return filepath
        except Exception:
            return None

    @staticmethod
    def _guess_image_size(data: bytes) -> tuple:
        try:
            if data[:2] == b'\xff\xd8':
                i = 2
                while i < len(data) - 9:
                    if data[i] == 0xff and data[i+1] in (0xc0, 0xc2):
                        return (int.from_bytes(data[i+7:i+9], 'big'),
                                int.from_bytes(data[i+5:i+7], 'big'))
                    i += int.from_bytes(data[i+2:i+4], 'big') + 2
            elif data[:8] == b'\x89PNG\r\n\x1a\n':
                return (int.from_bytes(data[16:20], 'big'),
                        int.from_bytes(data[20:24], 'big'))
        except Exception:
            pass
        return (0, 0)
