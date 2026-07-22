"""学术数据集导入器。

直接下载学术界已公开的建筑施工事故数据集：
- Mendeley Data: 571起事故案例(2010-2019)
  https://data.mendeley.com/datasets/8m8fdd7vgv
- GitHub: Chinese-construction-accident-database-159 (159份事故报告)
  https://github.com/IsmailMaCode/Chinese-construction-accident-database-159

数据特征：通常已经是 Excel/CSV 格式，仅需字段映射和标准化。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date as dt_date
from pathlib import Path
from typing import Optional

import httpx

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── Mendeley 数据集元信息 ──
MENDELEY_DATASET_URL = "https://data.mendeley.com/datasets/8m8fdd7vgv"
MENDELEY_DOWNLOAD_URL = "https://data.mendeley.com/public-files/datasets/8m8fdd7vgv/files/8e8d2a3b-1c2a-4d2b-b7f8-9e6f5a4b3c2d/file_downloaded"
MENDELEY_EXPECTED_FIELDS = [
    "case_id", "date", "province", "city", "accident_type",
    "deaths", "injuries", "direct_cause", "project_type",
]

# ── GitHub 数据集元信息 ──
GITHUB_REPO_URL = "https://github.com/IsmailMaCode/Chinese-construction-accident-database-159"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/IsmailMaCode/Chinese-construction-accident-database-159/main"


class AcademicDatasetImporter(BaseCollector):
    """学术数据集导入器。

    从 Mendeley Data 和 GitHub 等学术平台下载已发布的建筑事故数据集，
    解析后转换为统一的 RawAccidentText 格式，后续由清洗层统一处理。
    """

    def __init__(self, data_dir: Optional[Path] = None):
        self.data_dir = data_dir or (settings.DATA_DIR / "imported")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=60.0, follow_redirects=True)

    def source_name(self) -> str:
        return "academic_import"

    def collect(self) -> list[RawAccidentText]:
        """导入学术数据集。"""
        results: list[RawAccidentText] = []

        # 尝试 Mendeley
        try:
            mendeley_results = self._import_mendeley()
            results.extend(mendeley_results)
            logger.info(f"Mendeley 数据集: 导入 {len(mendeley_results)} 条")
        except Exception as e:
            logger.warning(f"Mendeley 数据集导入失败: {e}")

        # 尝试 GitHub
        try:
            github_results = self._import_github()
            results.extend(github_results)
            logger.info(f"GitHub 数据集: 导入 {len(github_results)} 条")
        except Exception as e:
            logger.warning(f"GitHub 数据集导入失败: {e}")

        # 尝试本地缓存
        cached = self._load_local_cache()
        if cached:
            results.extend(cached)
            logger.info(f"本地缓存: 加载 {len(cached)} 条")

        return results

    def _import_mendeley(self) -> list[RawAccidentText]:
        """导入 Mendeley Data 的 571 条事故案例。"""
        # 首先尝试从本地缓存加载
        cache_file = self.data_dir / "mendeley_571_cases.json"
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                return self._parse_mendeley_data(data)
            except Exception:
                pass

        # 尝试通过 DOI 获取数据集元信息
        # Mendeley 数据集通常需要手动下载，这里提供引导
        logger.info(
            f"Mendeley 数据集需要手动下载。请访问 {MENDELEY_DATASET_URL} "
            f"下载后放入 {self.data_dir} 目录。"
            f"支持格式: .xlsx, .csv, .json"
        )

        # 自动检测数据目录中是否有相关文件
        for pattern in ["*.xlsx", "*.csv", "*.json"]:
            for filepath in self.data_dir.glob(pattern):
                if "mendeley" in filepath.name.lower() or "571" in filepath.name:
                    logger.info(f"检测到候选文件: {filepath}")
                    try:
                        data = self._load_file(filepath)
                        return self._parse_mendeley_data(data)
                    except Exception as e:
                        logger.warning(f"解析 {filepath.name} 失败: {e}")

        return []

    def _import_github(self) -> list[RawAccidentText]:
        """导入 GitHub 上的 Chinese-construction-accident-database-159。"""
        results: list[RawAccidentText] = []

        # GitHub raw 文件路径
        files_to_try = [
            f"{GITHUB_RAW_BASE}/data/accidents.json",
            f"{GITHUB_RAW_BASE}/data/accidents.csv",
            f"{GITHUB_RAW_BASE}/accidents.json",
            f"{GITHUB_RAW_BASE}/dataset.json",
        ]

        for url in files_to_try:
            try:
                resp = self._client.get(url)
                if resp.status_code != 200:
                    continue

                if url.endswith(".json"):
                    data = resp.json()
                    if isinstance(data, list):
                        for item in data:
                            raw = self._github_item_to_raw(item)
                            if raw:
                                results.append(raw)
                    elif isinstance(data, dict):
                        items = data.get("data", data.get("accidents", []))
                        for item in items:
                            raw = self._github_item_to_raw(item)
                            if raw:
                                results.append(raw)

                elif url.endswith(".csv"):
                    import io, csv
                    reader = csv.DictReader(io.StringIO(resp.text))
                    for row in reader:
                        raw = self._github_item_to_raw(row)
                        if raw:
                            results.append(raw)

                if results:
                    break  # 成功读取就停止

            except Exception as e:
                logger.debug(f"GitHub {url} 失败: {e}")
                continue

        return results

    def _load_local_cache(self) -> list[RawAccidentText]:
        """加载本地缓存的学术数据集。"""
        results: list[RawAccidentText] = []
        cache_dir = self.data_dir

        if not cache_dir.exists():
            return results

        for filepath in sorted(cache_dir.glob("*.json")):
            # 跳过非数据集文件
            if filepath.name.startswith(".") or "cache" in filepath.name.lower():
                continue
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            raw = self._generic_item_to_raw(item, source_file=filepath.name)
                            if raw:
                                results.append(raw)
            except Exception:
                continue

        return results

    def _load_file(self, filepath: Path) -> list[dict]:
        """加载文件（支持 xlsx/csv/json）。"""
        if filepath.suffix.lower() == ".json":
            return json.loads(filepath.read_text(encoding="utf-8"))
        elif filepath.suffix.lower() == ".csv":
            import csv
            with open(filepath, "r", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        elif filepath.suffix.lower() in (".xlsx", ".xls"):
            try:
                import pandas as pd
                df = pd.read_excel(filepath)
                return df.to_dict(orient="records")
            except ImportError:
                raise ImportError("需要安装 pandas 和 openpyxl 来读取 Excel 文件")
        else:
            raise ValueError(f"不支持的文件格式: {filepath.suffix}")

    def _parse_mendeley_data(self, data: list[dict]) -> list[RawAccidentText]:
        """解析 Mendeley 数据集格式。"""
        results: list[RawAccidentText] = []
        for item in data:
            raw = self._generic_item_to_raw(item, source_file="mendeley")
            if raw and raw.title:
                results.append(raw)
        return results

    def _github_item_to_raw(self, item: dict) -> Optional[RawAccidentText]:
        """将 GitHub 数据集的单条记录转换为 RawAccidentText。"""
        return self._generic_item_to_raw(item, source_file="github_159")

    def _generic_item_to_raw(self, item: dict, source_file: str = "") -> Optional[RawAccidentText]:
        """通用转换：将任意 dict 格式的事故记录转为 RawAccidentText。

        尝试自动识别常见字段名，做最佳努力映射。
        """
        if not isinstance(item, dict) or not item:
            return None

        # ── 标题字段映射 ──
        title = ""
        for key in ["title", "accident_title", "case_name", "事件名称", "事故名称",
                     "标题", "事故标题", "name", "事件", "事故"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                title = val.strip()
                break

        if not title:
            # 尝试拼接已有字段构建标题
            parts = []
            for key in ["date", "日期", "city", "城市", "accident_type", "事故类型"]:
                val = item.get(key, "")
                if val:
                    parts.append(str(val))
            if len(parts) >= 2:
                title = " ".join(parts)
            else:
                return None  # 没有标题就不导入

        # ── 正文/summary 字段映射 ──
        raw_text = ""
        for key in ["raw_text", "description", "summary", "text", "content",
                     "detail", "accident_description", "事故描述", "事故经过",
                     "简要情况", "调查报告", "报告内容", "全文"]:
            val = item.get(key, "")
            if isinstance(val, str) and len(val) > 20:
                raw_text = val
                break

        # 如果没有单独的正文字段，用所有非空字段拼接
        if not raw_text:
            text_parts = []
            for k, v in item.items():
                if isinstance(v, str) and len(v) > 5 and k not in ("id", "case_id"):
                    text_parts.append(f"{k}: {v}")
            raw_text = "\n".join(text_parts)

        raw = RawAccidentText(
            source=f"academic_{source_file}",
            title=title,
            raw_text=raw_text,
            source_url=item.get("source_url", item.get("url", item.get("链接", ""))),
        )

        # ── 预提取字段 ──
        # 日期
        for key in ["date", "accident_date", "happen_date", "日期", "发生日期",
                     "事故日期", "时间"]:
            val = item.get(key, "")
            if val:
                if isinstance(val, dt_date):
                    raw.extracted_date = val
                    raw.confidence_date = 0.95
                elif isinstance(val, str) and val.strip():
                    from construction_agent.utils.date_utils import parse_date_to_dateobj
                    d = parse_date_to_dateobj(val.strip())
                    if d:
                        raw.extracted_date = d
                        raw.confidence_date = 0.9
                break

        # 城市
        for key in ["city", "城市", "市", "发生城市"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extracted_city = val.strip()
                raw.confidence_city = 0.9
                break

        # 省份
        for key in ["province", "location", "省", "省份", "地点", "发生省份"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extracted_province = val.strip()
                break

        # 伤亡
        for key in ["deaths", "death_count", "fatalities", "死亡人数", "死亡",
                     "死者", "dead"]:
            val = item.get(key, 0)
            try:
                d = int(val)
                if d > 0:
                    raw.extracted_deaths = d
                    break
            except (ValueError, TypeError):
                continue

        for key in ["injuries", "injury_count", "injured", "受伤人数", "受伤",
                     "重伤"]:
            val = item.get(key, 0)
            try:
                i = int(val)
                if i > 0:
                    raw.extracted_injuries = i
                    break
            except (ValueError, TypeError):
                continue

        # 事故类型
        for key in ["accident_type", "type", "事故类型", "类型", "category"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extracted_accident_type = val.strip()
                raw.confidence_accident_type = 0.85
                break

        # 保留原始数据到 extra
        raw.extra["original_item"] = {k: str(v)[:200] for k, v in item.items()
                                      if k not in ("raw_text", "text", "content", "detail")}

        return raw


def download_mendeley_instructions() -> str:
    """返回 Mendeley 数据集的手动下载指引。"""
    return f"""
    === Mendeley 建筑施工事故数据集下载指引 ===

    数据集: Critical Causes Identification for Construction Accidents in China
    记录数: 571 起事故案例 (2010-2019)
    地址: {MENDELEY_DATASET_URL}

    下载步骤:
    1. 访问上述 URL
    2. 点击 "Download" 按钮（可能需要注册 Mendeley 账号）
    3. 下载 .xlsx 文件
    4. 将文件放入: {settings.DATA_DIR}/imported/
    5. 重新运行本采集器即可自动识别

    如有代理/网络问题，也可尝试直接联系论文作者获取数据集。
    """
