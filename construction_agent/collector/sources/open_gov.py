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
        # **标注与实际不符**（2026-10-05 实测，§2.5.50）：该端点 302 到辽宁政务
        # 统一身份认证（`sso.lnzwfw.gov.cn` 的 `login.jsp`），**要登录才拿得到**。
        # 仍写 `True`：数据集本身确实向社会开放，差的只是一个账号；但**它现在
        # 拿不到数据**。是否为此申请账号/改为手工下载/下线该源，是作者的裁决项，
        # 代码侧不擅自改这个开关。
        "open_access": True,
        "api_endpoint": "https://data.shenyang.gov.cn/api/dataset/{dataset_id}/data",
        # 2026-10-05 实测：这个 ID **服务端认得**——登录页把原始 URL 原样放在
        # `surl` 里回传（base64 解码 = `http://data.shenyang.gov.cn:8080/api/
        # dataset/yuhong_shengchanshigu/data`）。原先「需要实际查询确认」可以
        # 划掉了，但 `:8080` 是内网/反代后端地址，外网直连三段全超时（§2.5.50）。
        "dataset_id": "yuhong_shengchanshigu",
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

# 五个数据集的 name，供 `_is_foreign` 判「这个本地文件是不是另一家的」。
# 从 `GOV_DATASETS` 派生而不是另抄一份——抄的那份迟早与配置对不上。
_DATASET_NAMES: list[str] = [ds["name"] for ds in GOV_DATASETS]


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

        # 没采到任何数据的数据集个数（2026-10-01 第三十九轮新增）。
        #
        # 与 TRS 基类的 `_fetch_failed` 同名同义：都是「没拿到有效内容」。
        # 有了它，「结果」栏才分得出「这个源活着但平台今天没数据」与
        # 「三个激活平台**一个都没通**」——修前两者在表上都是「空 / 0 条」，
        # 日志里也都没有一句话说得出全部失败。
        self._fetch_failed = 0

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
                    self._note_failure(ds, "API 与本地文件两条通路都没拿到数据")

            except Exception as e:
                logger.warning(f"{ds_name} 采集失败: {e}")
                self._print_manual_guide(ds)
                self._note_failure(ds, f"{type(e).__name__}: {e}")

            # 请求间隔
            time.sleep(self.get_crawl_delay())

        if self._fetch_failed >= len(self._active_sources) and self._active_sources:
            logger.error(
                f"[open_gov] {len(self._active_sources)} 个激活数据集**全部**"
                f"没采到数据——这不是「平台今天没内容」，是通路本身不通。"
                f"逐个原因见上面的 warning。"
            )
        return results

    def _note_failure(self, ds: dict, reason: str) -> None:
        """把一个数据集的失败记成一件看得见的事，并计入 `_fetch_failed`。

        **修前这类失败有三种去向，没有一种读得出来**（2026-10-01 第三十九轮
        干跑实测，3 个激活数据集）：

        - `shenyang`  `api_endpoint` 返回的是 **HTML**（一次 200 / 61,375 字节、
          另一次 502 / 156 字节），**从来不是 JSON**——`resp.json()` 抛
          `JSONDecodeError`，被 `logger.debug` 吞掉（默认级别看不见）。
          而它的 `base_url` 本身是 200 / 96,594，站点完全活着。
        - `heze`      `Network is unreachable`。这个域名**同时有 A 和 AAAA 记录**
          （`58.59.15.41` + `2408:8614:402:7fff:ffff:ffff:0:30c9`），本机没有
          IPv6 出口——**换一台有 IPv6 的机器可能就通了**，属环境依赖不是代码缺陷。
        - `shandong`  `base_url` 200 / 179,446（站点活着），可是**配置里压根
          没有 `api_endpoint`**，本地也没有手工下载的文件——三条通路只剩两条，
          两条都空。

        **上面 `shenyang` 与 `heze` 两条的推断，2026-10-05 直连实测后都不成立**
        （见 `docs/web/01_在线采集.md` §2.5.50）：

        - `shenyang`  那个 HTML **是辽宁政务 SSO 的登录页**，不是 404 页。
          302 的 `Location` 把原始 URL 放在 `surl` 参数里回传（base64 解码 =
          `http://data.shenyang.gov.cn:8080/api/dataset/yuhong_shengchanshigu/data`）
          ——**服务端认得这个数据集 ID，端点是活的，只是要登录**。
          旧推断把「拿到 HTML」等同于「端点失效」，把 64,943 字节的登录页与
          156 字节的错误页混成了一件事。
        - `heze`      强制 IPv4（猴补丁 `getaddrinfo` 只留 `AF_INET`）后
          **从 `ENETUNREACH` 变成 `ConnectTimeout`**——路由有了、SYN 没人应。
          两种错误不是一回事：前者是「没有到该网络的路由」（IPv6 无出口），
          后者是「对端丢包」。**IPv4 也不通，所以换机器不一定能解决**。
          且实测到的 IPv4 已是 `112.6.51.21`，与上面记的 `58.59.15.41` 不同，
          DNS 已更新——那条观测本身也过了保质期。

        修前第二种会落进 `else: 无数据或需要手动获取`，与「这个平台今天确实
        没数据」**同形**；第一种更糟，连 INFO 都没有。
        """
        self._fetch_failed += 1
        logger.warning(
            f"[open_gov] {ds['name']}（{ds['description']}）没采到：{reason}"
        )

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
                    logger.warning(
                        f"[open_gov] {ds['name']} 的 API 返回 200，但里面"
                        f"解不出记录（拿到的不是预期的 JSON 结构）——"
                        f"端点 {api_url} 未经确认，很可能早已失效。"
                    )
                else:
                    # **修前这里什么都没有。** `if resp.status_code == 200:`
                    # 为假时整个 API 分支静默跳过，于是「502」与「压根没配
                    # api_endpoint」在日志里同形——都是一片安静。实测遇到过
                    # 一次 502 / 156 字节。
                    logger.warning(
                        f"[open_gov] {ds['name']} 的 API 返回 HTTP "
                        f"{resp.status_code}（端点 {api_url}，未经确认）——"
                        f"非 200 同样是「没拿到数据」，不能静默跳过。"
                    )
            except Exception as e:
                # **原先是 `logger.debug`。** 默认级别下这一行等于没有，
                # 于是「端点返回 HTML 不是 JSON」这件事从来没有出现在任何
                # 日志里——见 `_note_failure` 的实测表。
                logger.warning(
                    f"[open_gov] {ds['name']} 的 API 取数失败："
                    f"{type(e).__name__}: {e}"
                    f"（端点 {api_url}，未经确认）"
                )

        # 方式2: 搜索本地已下载文件
        for fmt in ds.get("formats", ["json", "csv"]):
            for filepath in self._local_files(ds, fmt):
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

    def _local_files(self, ds: dict, fmt: str) -> list[Path]:
        """找出可能属于 `ds` 的本地文件。

        两级：先按 `{name}*.{fmt}` 精确匹配，没命中再退到全部 `*.{fmt}`
        （用户手工下载时多半不会按我们的命名规则改文件名，这个回退是有意的）。
        **两级都要过 `_is_foreign` 那一关**。

        为什么非得加这一关（2026-10-05 实测，见 `docs/web/01_在线采集.md`
        §2.5.51）——原来这两行 glob 各自咬人：

        1. **缓存文件命中自己。** `_save_cache` 写的是 `{name}_cache.json`，
           而它**匹配** `{name}*.json`。后果不是"多读一个文件"，是
           **`_load_cache` 那道 30 天闸门被绕过去**：缓存过期 → `_load_cache`
           按设计返回空 → 本该去平台重新取数 → 结果 `_fetch_dataset` 又从
           缓存文件里把同一份旧数据读了回来 → `collect()` 再把它
           `_save_cache` 续期。**实测**：放一份 `{name}_cache.json`，
           `_fetch_dataset` 返回 1 条，内容正是那份缓存。
        2. **捞到别的数据集的文件。** 无前缀回退是 `*.{fmt}`，会把
           `open_gov_heze.csv` 交给 shandong。转换用的却是 shandong 的 `ds`
           （关键词、字段映射全不对），`data_source` 还标成
           `open_gov_shandong`——**张冠李戴且事后看不出来**。**实测**：
           shandong 读出了 heze 的 CSV。更糟的是它会**持久化**：
           `collect()` 紧接着把这份外来数据 `_save_cache("open_gov_shandong",
           ...)`，污染从此留在盘上、下一轮 `_load_cache` 还会把它当 shandong
           的正规缓存加载。
        """
        name = ds["name"]
        found = list(self.data_dir.glob(f"{name}*.{fmt}"))
        if not found:
            found = list(self.data_dir.glob(f"*.{fmt}"))
        return [p for p in found if not self._is_foreign(p, ds)]

    @staticmethod
    def _is_foreign(path: Path, ds: dict) -> bool:
        """这个本地文件该不该由 `ds` 采用。`True` = 不该。

        两条判据：①本类的缓存文件（`*_cache`）；②属于**另一个**已登记数据集
        （按 `GOV_DATASETS` 里的 name 前缀认）。

        用 `stem`（去扩展名）比 name 稳：用户若命名成
        `open_gov_heze_data.csv`，`stem` = `open_gov_heze_data`，前缀判据照样
        命中，而按完整文件名比会漏。五个 name 互不为前缀（`shenyang` 与
        `shandong` 只共有前 8 个字符，不是包含关系），所以前缀判据不会误伤。
        """
        stem = path.stem
        if stem.endswith("_cache"):
            return True
        return any(
            other != ds["name"] and stem.startswith(other)
            for other in _DATASET_NAMES
        )

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
            f"  3. 下载数据（{', '.join(f.upper() for f in ds['formats'])} 格式）\n"
            f"  4. 将文件放入: {self.data_dir}/\n"
            f"     文件名**建议以 '{ds['name']}' 开头**（如 {ds['name']}.csv）——\n"
            f"     不加前缀也能被认到，但加了才不会被当成别的数据集的文件：\n"
            f"     平台上有 5 个数据集共用这个目录，无前缀时按扩展名回退匹配，\n"
            f"     而属于其他数据集的文件会被明确排除（见 `_local_files`）。\n"
            f"  5. 重新运行采集器即可自动导入\n"
        )
