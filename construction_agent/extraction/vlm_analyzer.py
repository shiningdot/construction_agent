#!/usr/bin/env python3
"""VLM 多模态图片分析器 — GLM-5V-Turbo 两阶段分析。

Phase 1: 纯图片盲观察 — VLM 看图描述
Phase 2: 图片 + Step1 上下文交叉验证 — 判断一致性和信息增量
"""
import os, json, time, base64, httpx, logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
ZHIPU_KEY = os.getenv("ZHIPU_API_KEY", "")
VL_MODEL = "glm-5v-turbo"

PHASE1_PROMPT = """请描述这张建筑施工事故相关的图片。按以下格式输出：

## 事故类型
判断这张图片展示的是哪种事故（坍塌/高处坠落/起重伤害/物体打击/触电/火灾爆炸/机械伤害/土方坍塌/车辆伤害/其他）

## 可见损伤
描述图片中可见的结构损伤、设备损坏、废墟等情况

## 施工场景
描述图中可能的施工环境（在建工地/拆除现场/装修/桥梁/隧道等）

## 可见人员与设备
描述图中能看到的人员（工人/救援人员/路人）和施工设备（塔吊/挖掘机/脚手架等）

## 天气与光照
描述图中的天气状况和光照条件"""

PHASE2_PROMPT = """以下是关于一起建筑施工事故的已知信息：
- 事故类型: {accident_type}
- 严重等级: {severity}
- 死亡: {deaths}人 / 受伤: {injuries}人
- 城市: {city}
- 工序: {work_task}

请结合以上信息和图片，输出一段简洁的描述（80-150字），用于多模态展示。
要求：将事故基本情况与图片中可见的场景、天气、人员等内容自然融合，不加判断、不评价一致性。
格式：直接输出描述文本，不需要JSON。"""


@dataclass
class VLMAnalysisResult:
    """VLM 分析结果"""
    accident_id: str = ""
    local_path: str = ""
    phase1_text: str = ""
    phase1_scene_type: str = ""
    phase1_damage: str = ""
    phase1_context: str = ""
    phase1_people: str = ""
    phase1_weather: str = ""
    phase2_consistency: str = ""
    phase2_confidence: str = ""
    phase2_new_info: str = ""
    phase2_text: str = ""
    vlm_model: str = VL_MODEL
    duration_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self):
        return {
            "accident_id": self.accident_id,
            "local_path": self.local_path,
            "phase1_text": self.phase1_text,
            "phase1_scene_type": self.phase1_scene_type,
            "phase1_damage": self.phase1_damage,
            "phase1_context": self.phase1_context,
            "phase1_people": self.phase1_people,
            "phase1_weather": self.phase1_weather,
            "phase2_consistency": self.phase2_consistency,
            "phase2_confidence": self.phase2_confidence,
            "phase2_new_info": self.phase2_new_info,
            "phase2_text": self.phase2_text,
            "vlm_model": self.vlm_model,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class VLMAnalyzer:
    """GLM-5V-Turbo VLM 图片分析器"""

    def __init__(self):
        self._enabled = bool(ZHIPU_KEY)
        if not self._enabled:
            logger.warning("ZHIPU_API_KEY 未配置, VLM 禁用")

    def analyze(self, image_path: str, accident_context: Optional[dict] = None) -> VLMAnalysisResult:
        """两阶段分析单张图片"""
        result = VLMAnalysisResult(local_path=image_path)
        t0 = time.time()

        if not self._enabled:
            result.error = "ZHIPU_API_KEY未配置"
            result.duration_ms = (time.time() - t0) * 1000
            return result

        try:
            img_b64 = self._encode_image(image_path)

            # Phase 1: 盲观察
            phase1_text = self._call_vlm(PHASE1_PROMPT, img_b64)
            result.phase1_text = phase1_text or ""
            self._parse_phase1(result, phase1_text or "")

            # Phase 2: 交叉验证
            if accident_context:
                phase2_text = self._call_vlm(
                    PHASE2_PROMPT.format(
                        accident_type=accident_context.get("accident_type","未知"),
                        severity=accident_context.get("severity","未知"),
                        deaths=accident_context.get("deaths",0),
                        injuries=accident_context.get("injuries",0),
                        city=accident_context.get("city","未知"),
                        work_task=accident_context.get("work_task","未知"),
                    ),
                    img_b64
                )
                result.phase2_text = phase2_text or ""
                self._parse_phase2(result, phase2_text or "")
        except Exception as e:
            result.error = f"{type(e).__name__}: {e}"
            logger.error(f"VLM分析失败 [{image_path}]: {e}")

        result.duration_ms = (time.time() - t0) * 1000
        return result

    def _call_vlm(self, prompt: str, img_b64: str) -> Optional[str]:
        """调用 GLM-5V-Turbo"""
        body = {
            "model": VL_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ]
            }],
            "max_tokens": 1200,
            "temperature": 0.3,
        }

        for attempt in range(3):
            try:
                resp = httpx.post(
                    ZHIPU_URL,
                    headers={"Authorization": f"Bearer {ZHIPU_KEY}", "Content-Type": "application/json"},
                    json=body, timeout=60
                )
                if resp.status_code == 200:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                elif resp.status_code in (429, 402):
                    code = resp.json().get("error", {}).get("code", "")
                    raise RuntimeError(f"额度不足或限流: HTTP {resp.status_code} {code}")
                else:
                    logger.warning(f"VLM HTTP {resp.status_code}: {resp.text[:100]}")
                    if attempt < 2:
                        time.sleep(2 ** attempt)
            except RuntimeError:
                raise  # 额度问题直接抛
            except Exception as e:
                if attempt < 2:
                    time.sleep(1)
                else:
                    logger.error(f"VLM调用失败: {e}")
        return None

    @staticmethod
    def _encode_image(path: str) -> str:
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def save_to_db(self, result: VLMAnalysisResult, db_path: str = "data/caiems_v2.db"):
        """将 VLM 分析结果写入 image_analyses 表"""
        import sqlite3
        db = sqlite3.connect(db_path)
        db.execute("""
            INSERT OR REPLACE INTO image_analyses
            (accident_id, local_path, source_url,
             phase1_scene_type, phase1_objects, phase1_people, phase1_damage, phase1_weather, phase1_raw_response,
             phase2_consistency, phase2_confidence, phase2_new_info, phase2_raw_response,
             vlm_model, duration_ms, created_at)
            VALUES (?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, datetime('now'))
        """, (
            result.accident_id or "",
            result.local_path or "",
            "",
            result.phase1_scene_type or "",
            "",
            result.phase1_people or "",
            result.phase1_damage or "",
            result.phase1_weather or "",
            (result.phase1_text or "")[:5000],
            result.phase2_consistency or "",
            result.phase2_confidence or "",
            (result.phase2_new_info or "")[:1000],
            (result.phase2_text or "")[:2000],
            result.vlm_model,
            int(result.duration_ms),
        ))
        db.commit()
        db.close()

    @staticmethod
    def _parse_phase1(result: VLMAnalysisResult, text: str):
        """从 Markdown 章节解析 Phase1 输出"""
        sections = {
            "事故类型": "phase1_scene_type",
            "可见损伤": "phase1_damage",
            "施工场景": "phase1_context",
            "可见人员与设备": "phase1_people",
            "天气与光照": "phase1_weather",
        }
        for heading, attr in sections.items():
            # 匹配 "## 事故类型" 或 "事故类型" 后的内容
            import re
            m = re.search(rf'(?:##\s*)?{heading}\s*\n(.*?)(?=\n(?:##\s*)?\S|\Z)', text, re.DOTALL)
            if m:
                setattr(result, attr, m.group(1).strip())

    @staticmethod
    def _parse_phase2(result: VLMAnalysisResult, text: str):
        """Phase2 现在输出纯文本描述"""
        result.phase2_new_info = (text or "")[:2000]
        result.phase2_consistency = ""  # 不再需要一致性判断
        result.phase2_confidence = ""
