"""政府开放数据平台采集器。

从各省市公共数据开放平台下载建筑施工事故相关数据集：

已确认的可用数据集：
- 沈阳: 于洪区_生产安全事故调查处理 (1,266条，无条件开放)
  https://data.shenyang.gov.cn
- 咸宁: 事故信息 (3,166条，有条件开放)
  https://data.xianning.gov.cn
- 菏泽: 建筑安全隐患排查治理信息 (3,567条，无条件开放)
  https://hzdata.sd.gov.cn
- 山东: 烟台栖霞市建设工程安全备案信息 (5,224条)
  https://data.sd.gov.cn

中国政府开放数据平台通常支持 CSV/JSON/XML/XLSX 多种格式导出，
通过标准 API 接口或直接下载链接获取。
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

import httpx

from construction_agent.collector.base import BaseCollector, RawAccidentText
from construction_agent.config import settings

logger = logging.getLogger(__name__)

# ── 已知的政府开放数据平台及其数据集 ──
# 每个数据源定义: {name, base_url, dataset_id, format, 无条件开放?, 字段映射}
GOV_DATASETS: list[dict] = [
    {
        "name": "open_gov_shenyang",
        "base_url": "https://data.shenyang.gov.cn",
        "description": "沈阳于洪区生产安全事故调查处理",
        "record_count": 1266,
        "open_access": True,
        "api_endpoint": "https://data.shenyang.gov.cn/api/dataset/{dataset_id}/data",
        "dataset_id": "yuhong_shengchanshigu",  # 需要实际查询确认
        "formats": ["json", "csv", "xlsx"],
        "keywords": ["事故", "生产安全"],
    },
    {
        "name": "open_gov_xianning",
        "base_url": "https://data.xianning.gov.cn",
        "description": "咸宁事故信息",
        "record_count": 3166,
        "open_access": False,  # 有条件开放
        "api_endpoint": None,
        "formats": ["json", "csv"],
        "keywords": ["事故", "死亡", "重伤"],
    },
    {
        "name": "open_gov_heze",
        "base_url": "https://hzdata.sd.gov.cn",
        "description": "菏泽建筑安全隐患排查治理信息",
        "record_count": 3567,
        "open_access": True,
        "api_endpoint": None,
        "formats": ["json", "csv", "xlsx"],
        "keywords": ["建筑", "隐患", "安全"],
    },
    {
        "name": "open_gov_shandong",
        "base_url": "https://data.sd.gov.cn",
        "description": "烟台栖霞市建设工程安全备案信息",
        "record_count": 5224,
        "open_access": True,
        "api_endpoint": None,
        "formats": ["json", "csv"],
        "keywords": ["建设工程", "安全备案"],
    },
    {
        "name": "open_gov_zibo",
        "base_url": "https://data.zibo.gov.cn",
        "description": "淄博建筑工程施工安全监督信息",
        "record_count": 3563,
        "open_access": False,
        "api_endpoint": None,
        "formats": ["json", "csv"],
        "keywords": ["建筑施工", "安全监督"],
    },
]


class OpenGovCollector(BaseCollector):
    """政府开放数据平台采集器。

    设计思路：
    1. 优先尝试平台的标准 API 接口
    2. API 不可用时，尝试直接下载公开 CSV/JSON
    3. 均失败时，提示用户手动下载
    4. 所有数据转换为统一 RawAccidentText 格式
    """

    def __init__(self, data_dir: Optional[Path] = None, sources: list[str] | None = None):
        """
        Args:
            data_dir: 数据缓存目录
            sources: 要采集的数据源名称列表，None 表示全部
                     可选值: shenyang, xianning, heze, shandong, zibo
        """
        self.data_dir = data_dir or (settings.DATA_DIR / "open_gov")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        self._client.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        })

        # 筛选要采集的数据源
        if sources:
            self._active_sources = [
                ds for ds in GOV_DATASETS
                if any(s in ds["name"] for s in sources)
            ]
        else:
            # 默认只采集无条件开放的数据集
            self._active_sources = [ds for ds in GOV_DATASETS if ds["open_access"]]

        logger.info(f"政府开放平台采集器: 激活 {len(self._active_sources)} 个数据源")

    def source_name(self) -> str:
        return "open_gov"

    def get_crawl_delay(self) -> float:
        """政府网站请求间隔设置更长，避免给对方服务器造成压力。"""
        return 5.0

    def collect(self) -> list[RawAccidentText]:
        """采集所有激活的政府开放平台数据集。"""
        results: list[RawAccidentText] = []

        for ds in self._active_sources:
            ds_name = ds["name"]
            logger.info(f"开始采集: {ds_name} ({ds['description']})")

            try:
                # 先检查本地缓存
                cached = self._load_cache(ds_name)
                if cached:
                    logger.info(f"{ds_name}: 从缓存加载 {len(cached)} 条")
                    results.extend(cached)
                    continue

                # 尝试 API 获取
                items = self._fetch_dataset(ds)
                if items:
                    converted = self._convert_to_raw(items, ds)
                    logger.info(f"{ds_name}: 采集 {len(converted)} 条")
                    # 写入缓存
                    self._save_cache(ds_name, items)
                    results.extend(converted)
                else:
                    logger.info(f"{ds_name}: 无数据或需要手动获取")
                    # 打印获取指引
                    self._print_manual_guide(ds)

            except Exception as e:
                logger.warning(f"{ds_name} 采集失败: {e}")
                self._print_manual_guide(ds)

            # 请求间隔
            time.sleep(self.get_crawl_delay())

        return results

    def _fetch_dataset(self, ds: dict) -> list[dict]:
        """尝试多种方式获取数据集。"""
        items: list[dict] = []

        # 方式1: 标准 API 端点
        if ds.get("api_endpoint"):
            api_url = ds["api_endpoint"].format(**ds)
            try:
                resp = self._client.get(api_url)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict):
                        items = data.get("data", data.get("result", data.get("records", [])))
                        if isinstance(items, dict):
                            items = items.get("records", items.get("data", []))
                    elif isinstance(data, list):
                        items = data
                    if items:
                        logger.info(f"API 获取成功: {len(items)} 条")
                        return items
            except Exception as e:
                logger.debug(f"API 获取失败: {e}")

        # 方式2: 搜索本地已下载文件
        for fmt in ds.get("formats", ["json", "csv"]):
            pattern = f"{ds['name']}*.{fmt}"
            matched = list(self.data_dir.glob(pattern))
            if not matched:
                # 也搜索无前缀的文件
                matched = list(self.data_dir.glob(f"*.{fmt}"))

            for filepath in matched:
                try:
                    file_items = self._load_file(filepath, fmt)
                    if file_items:
                        # 筛选包含事故关键词的记录
                        filtered = self._filter_accident_records(file_items, ds)
                        if filtered:
                            logger.info(f"从文件 {filepath.name} 加载 {len(filtered)} 条")
                            return filtered
                except Exception as e:
                    logger.debug(f"文件 {filepath.name} 加载失败: {e}")

        return items

    def _load_file(self, filepath: Path, fmt: str) -> list[dict]:
        """加载数据文件。"""
        if fmt == "json":
            data = json.loads(filepath.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                for key in ["data", "records", "result", "items"]:
                    val = data.get(key)
                    if isinstance(val, list):
                        return val
                return [data]
        elif fmt == "csv":
            import csv
            with open(filepath, "r", encoding="utf-8-sig") as f:
                return list(csv.DictReader(f))
        elif fmt in ("xlsx", "xls"):
            try:
                import pandas as pd
                df = pd.read_excel(filepath)
                return df.to_dict(orient="records")
            except ImportError:
                logger.warning("需要安装 pandas + openpyxl 读取 Excel")
                return []
        return []

    def _filter_accident_records(self, items: list[dict], ds: dict) -> list[dict]:
        """筛选与建筑施工事故相关的记录。

        根据数据集的 keywords 和事故相关字段进行过滤。
        """
        if not items:
            return items

        keywords = ds.get("keywords", ["事故"])
        if not keywords:
            return items

        filtered = []
        for item in items:
            # 将所有字段值拼接为搜索文本
            search_text = " ".join(str(v) for v in item.values() if v)
            # 任一关键词命中即保留
            if any(kw in search_text for kw in keywords):
                filtered.append(item)

        # 如果过滤后为空，保留全部（可能是字段名不匹配）
        if not filtered and len(items) < 10000:
            return items

        return filtered

    def _convert_to_raw(self, items: list[dict], ds: dict) -> list[RawAccidentText]:
        """将政府数据平台的记录转换为 RawAccidentText。"""
        results = []
        for item in items:
            raw = self._single_item_to_raw(item, ds)
            if raw:
                results.append(raw)
        return results

    def _single_item_to_raw(self, item: dict, ds: dict) -> Optional[RawAccidentText]:
        """单条记录转换。支持各地不同字段命名，做最佳努力映射。"""
        # ── 标题 ──
        title = ""
        for key in ["事故名称", "标题", "title", "name", "项目名称", "工程名称",
                     "单位名称", "事故发生单位名称", "事件名称", "案件名称"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                title = val.strip()
                break

        # ── 正文 ──
        raw_text_parts = []
        for key in ["事故调查处理情况", "事故概况", "简要情况", "隐患描述",
                     "description", "content", "detail", "summary", "整改情况",
                     "违法行为", "调查情况"]:
            val = item.get(key, "")
            if isinstance(val, str) and len(val) > 10:
                raw_text_parts.append(f"{key}: {val}")

        # 如果以上都没有，拼接所有字段
        if not raw_text_parts:
            for k, v in item.items():
                if isinstance(v, str) and len(v) > 3:
                    raw_text_parts.append(f"{k}: {v}")

        raw_text = "\n".join(raw_text_parts)

        if not title and not raw_text:
            return None

        raw = RawAccidentText(
            source=ds["name"],
            title=title,
            raw_text=raw_text,
            source_url=f"{ds['base_url']}",
        )

        # ── 预提取字段 ──
        # 日期
        for key in ["日期", "date", "发生日期", "事故日期", "time", "时间",
                     "录入日期", "登记日期"]:
            val = item.get(key, "")
            if val and isinstance(val, str) and val.strip():
                from construction_agent.utils.date_utils import parse_date_to_dateobj
                d = parse_date_to_dateobj(str(val).strip()[:10])
                if d:
                    raw.extracted_date = d
                    raw.confidence_date = 0.8
                    break

        # 伤亡（政府开放数据的字段名较为规范）
        for dk in ["死亡人数", "死亡", "deaths", "dead", "死者数量"]:
            val = item.get(dk, 0)
            try:
                d = int(float(str(val)))
                if d > 0:
                    raw.extracted_deaths = d
                    break
            except (ValueError, TypeError):
                continue

        for ik in ["受伤人数", "重伤人数", "受伤", "injuries", "injured"]:
            val = item.get(ik, 0)
            try:
                i = int(float(str(val)))
                if i > 0:
                    raw.extracted_injuries = i
                    break
            except (ValueError, TypeError):
                continue

        # 事故类型
        for key in ["事故类型", "类型", "type", "accident_type", "事故类别",
                     "生产安全事故类型"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extracted_accident_type = val.strip()
                raw.confidence_accident_type = 0.7
                break

        # 施工单位/项目
        for key in ["施工单位", "施工企业", "单位名称", "事故发生单位名称"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extra["contractor"] = val.strip()
                break

        for key in ["工程名称", "项目名称", "project_name"]:
            val = item.get(key, "")
            if isinstance(val, str) and val.strip():
                raw.extra["project_name"] = val.strip()
                break

        raw.extra["data_source"] = ds["name"]
        raw.extra["original_item"] = {k: str(v)[:200] for k, v in item.items()}

        return raw

    def _load_cache(self, ds_name: str) -> list[RawAccidentText]:
        """从本地缓存加载之前采集的数据。"""
        cache_file = self.data_dir / f"{ds_name}_cache.json"
        if not cache_file.exists():
            return []

        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            # 只加载最近 30 天内的缓存
            import os
            mtime = os.path.getmtime(cache_file)
            if time.time() - mtime > 30 * 86400:
                logger.info(f"{ds_name}: 缓存已过期（>30天），将重新采集")
                return []
            return self._convert_to_raw(data, {"name": ds_name, "keywords": []})
        except Exception:
            return []

    def _save_cache(self, ds_name: str, items: list[dict]):
        """将采集数据写入本地缓存。"""
        cache_file = self.data_dir / f"{ds_name}_cache.json"
        cache_file.write_text(
            json.dumps(items, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug(f"缓存已保存: {cache_file}")

    def _print_manual_guide(self, ds: dict):
        """打印手动获取指引。"""
        logger.info(
            f"\n  === {ds['description']} 手动获取指引 ===\n"
            f"  平台: {ds['base_url']}\n"
            f"  记录数: {ds['record_count']} 条\n"
            f"  开放条件: {'无条件开放' if ds['open_access'] else '有条件开放'}\n"
            f"  支持格式: {', '.join(ds['formats'])}\n"
            f"\n"
            f"  操作步骤:\n"
            f"  1. 访问 {ds['base_url']}\n"
            f"  2. 搜索 '{ds['description']}'\n"
            f"  3. 下载数据（CSV 或 JSON 格式）\n"
            f"  4. 将文件放入: {self.data_dir}/\n"
            f"  5. 重新运行采集器即可自动导入\n"
        )
