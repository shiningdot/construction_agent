"""24 个采集源的登记表与构造工厂。

**为什么要有这张表**：源清单原来散在 24 个模块里——模块名、类名、中文名、
入口 URL、请求间隔各写各的，页面想展示「有哪些源」只能硬编码一份副本，
而副本一定会和代码漂移（旧 web 的 `collector_sources.json` 就是这么烂掉的：
它躺在 `data/misc/`，没有任何 Python 代码引用它）。这里把元信息收成一张表，
再 upsert 进工作库的 `collect_sources`，成为页面、执行器、统计三处的唯一来源。

**表里的每一行都是实测出来的**，不是照抄文档：12 个 TRS 源的 URL 写在各自
`__init__` 内部的 `TrsSiteConfig(...)` 局部变量里，静态扫源码扫不到，只能
实例化后读 `collector.cfg`。构造签名里的 `data_dir` 参数是本轮新加的（7 个
源原来把 `settings.DATA_DIR / "<源名>"` 硬编码在 `__init__` 里，见蓝图 01 §3.4）。
"""

from __future__ import annotations

import importlib
import inspect
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_PKG = "construction_agent.collector.sources"

# `collect_sources.track` 的取值。24 个源目前全走网络；文献轨（`input/` 下的
# PDF）是否纳入在线管道还没裁决（蓝图 01 §9 待决项 C），所以这里只有 network。
TRACK_NETWORK = "network"

# `collect_sources.source_group` 的取值 → 中文名。
# `literature` / `academic` 在 DDL 里留着位，本期没有源落进去。
GROUP_LABELS = {
    "national": "部委",
    "province": "省级厅局",
    "media": "行业与新闻站",
    "open_data": "开放数据平台",
    "literature": "文献轨（未启用）",
    "academic": "学术库（未启用）",
}

# 采集阶段不跑二级筛选的源：这几个构造器有 `enable_classify` 开关，默认 True。
# 在线采集只负责「把原始文本拿回来」，三分类是阶段②的活（蓝图 02）。
#
# **这里原先写着「多花一次模型调用」，是错的。** 这些开关背后调的是
# `collector/classifier.py::classify_v2`——一个 242 行的纯关键词规则函数，
# 全文没有一处网络或 LLM 调用（`grep -nE 'anthropic|openai|httpx|requests|api_key'`
# 零命中）。关掉它**省不下任何模型成本**；真实代价是反过来的：没有二级筛选
# 的判据，条目一律落 `status=review`（各源 `item_status` 的初值），人工审核
# 队列只会更长。
#
# 那为什么还留着？因为它买到的是**不丢数据**：`classify_v2` 判「高置信度
# 非建筑」时各源直接 `continue` 丢弃整条，规则误判不可逆。采集阶段不筛，
# 把判定留给阶段②带证据的流程更稳。
_NO_CLASSIFY: tuple[tuple[str, Any], ...] = (("enable_classify", False),)

# `MohurdCollector` 的构造器默认 `collect_mem=True`——那是它早期兼管两个部委
# 时留下的：`run_collect.py` 里这条源的描述至今写着「mohurd.gov.cn + mem.gov.cn」。
# 后来应急部独立成 `mem` 这个 source_key（`MemCollector`），两件事就重了：
#
# 1. **筛选标准两套，宽的那套会放煤矿进来。** `_collect_mem` 的关键词表只有
#    建筑词（「坍塌」之类）、**没有任何排除词**；而 `MemCollector` 的
#    `EXCLUDE_KW` 里有「煤矿」「煤业公司」等。实测 `_collect_mem` 收进来的
#    两条都是「内蒙古阿拉善新井煤业有限公司露天煤矿“2·22”特别重大坍塌事故」，
#    正是 `MemCollector` 会排除的。
# 2. **去重挡不住。** 两条路的 `source_url` 口径不同——`_collect_mem` 记的是
#    PDF 直链（`.../W020240204579335303864.pdf`），`MemCollector` 记的是详情页
#    （`.shtml`）。`raw_accidents` 的 `UNIQUE(url_hash)` 因此命中不了同一个报告，
#    两个源都跑就会各存一份。
#
# 关掉不丢数据：`MemCollector` 遍历 2005–2025 全部年份目录，覆盖面是
# `_collect_mem`（只抓入口页一层）的超集，实测它抓到的报告也在同一个
# `fdzdgknr/` 目录下。
_MOHURD_ONLY: tuple[tuple[str, Any], ...] = (("collect_mem", False),)


@dataclass(frozen=True)
class SourceSpec:
    """一个采集源的元信息 + 构造它需要的东西。

    `build_kwargs` 存成 tuple 而不是 dict，是因为 `frozen=True` 的 dataclass
    要求字段可哈希——在线采集是并发跑的，这张表会被多线程同时读，冻住最省心。
    """

    key: str                      # source_key，`collect_sources` 的主键
    display_name: str             # 页面展示用的中文名
    group: str                    # GROUP_LABELS 的键
    authority: str                # 发布机构全称
    entry_url: str                # 入口页；聚合器没有单一入口，留空
    crawl_delay: float            # 请求间隔（秒），取自各源构造器的默认值
    module: str                   # `construction_agent.collector.sources.<module>`
    cls: str
    build_kwargs: tuple[tuple[str, Any], ...] = field(default=())
    note: str = ""

    @property
    def dotted_path(self) -> str:
        return f"{_PKG}.{self.module}.{self.cls}"


def _s(key: str, name: str, group: str, authority: str, url: str,
       delay: float, module: str, cls: str, *,
       kwargs: tuple[tuple[str, Any], ...] = (), note: str = "") -> tuple[str, SourceSpec]:
    return key, SourceSpec(key=key, display_name=name, group=group,
                           authority=authority, entry_url=url, crawl_delay=delay,
                           module=module, cls=cls, build_kwargs=kwargs, note=note)


# ── 24 个源，按 source_key 字典序，便于和 collect_sources 表逐行对账 ──
#
# 省级应急管理厅局一律 `enable_classify=False`（见 `_NO_CLASSIFY` 的注释）；
# 只有 5 个源有这个参数，别的源不传就是过滤掉，不会报错。
SOURCES: dict[str, SourceSpec] = dict([
    # 唯一**不挂** `_NO_CLASSIFY` 的省级源。它的正文只存在于 PDF 里（列表页
    # 那段 HTML 只有标题、发布单位和附件路径），而「下载 PDF 提正文」与
    # 「跑 `classify_v2`」共用同一个开关 `enable_pdf_classify`——挂上去等于
    # 把正文来源一并关掉。
    #
    # 这里原先写的就是 `_NO_CLASSIFY`，但常量的键是 `enable_classify`，与本源
    # 构造器的 `enable_pdf_classify` 对不上；`build()` 会把构造器不认识的参数
    # **静默丢掉**（只记一条 `logger.debug`），所以它一次也没生效过——实测
    # 实例上 `_enable_pdf_classify is True`。摘掉只是把既成事实写实。
    _s("beijing_em", "北京市应急管理局", "province", "北京市应急管理局",
       "http://yjglj.beijing.gov.cn/col/col4520/index.html", 1.0,
       "beijing_em", "BeijingEMCollector",
       note="正文只存在于 PDF 里，故不挂 _NO_CLASSIFY（见上方注释）；"
            "实测会建 data_dir 目录（原 note 说「不建」，与现状不符）"),
    _s("cq_em", "重庆市应急管理局", "province", "重庆市应急管理局",
       "https://yjj.cq.gov.cn/zwgk_230/fdzdgknr/yjyayjxx/zhsgdcpg/dcbg/", 1.0,
       "cq_em", "CqEMCollector"),
    _s("esafety", "易安网", "media", "易安网", "https://www.esafety.cn", 0.5,
       "esafety", "EsafetyCollector",
       note="需要 token 才能走 API 分页；无 token 时退化为列表页抓取"),
    _s("gd_em", "广东省应急管理厅", "province", "广东省应急管理厅",
       "https://yjgl.gd.gov.cn/gk/zdlyxxgk/sgdcbg/index.html", 1.0,
       "gd_em", "GdEMCollector",
       note="`data_dir` 是附件缓存目录（2026-10-01 起本源要下载附件提正文）"),
    _s("gz_em", "贵州省应急管理厅", "province", "贵州省应急管理厅",
       "https://yjgl.guizhou.gov.cn/gk/zfxxgk/fdzdgk/dcbg_5888759/", 1.0,
       "gz_em", "GzEMCollector"),
    _s("hain_em", "海南省应急管理厅", "province", "海南省应急管理厅",
       "https://yjglt.hainan.gov.cn/xxgk/sgdcbg/", 1.0,
       "hain_em", "HainEMCollector"),
    _s("hebei_em", "河北省应急管理厅", "province", "河北省应急管理厅",
       "https://yjgl.hebei.gov.cn/portal/infopublic/toInfoPublicNews?type=sgdc", 1.0,
       "hebei_em", "HebeiEMCollector",
       kwargs=_NO_CLASSIFY,
       note="走 JSON API 而非 HTML 列表页"),
    _s("henan_em", "河南省应急管理厅", "province", "河南省应急管理厅",
       "https://yjglt.henan.gov.cn/zwgk/xxgkml/sgxx/", 1.0,
       "henan_em", "HenanEMCollector",
       kwargs=_NO_CLASSIFY,
       note="默认下 PDF（正文多在 PDF 里），文件落执行器给的临时目录"),
    _s("hub_em", "湖北省应急管理厅", "province", "湖北省应急管理厅",
       "https://yjt.hubei.gov.cn/yjgl/aqsc/sgdc/", 1.0,
       "hub_em", "HubEMCollector"),
    # 域名 2026-10-05 更正：`yjglt.jl.gov.cn` **DNS 解析不出来**（各省应急厅
    # 是 `yjglt.<省>.gov.cn` 的规律，吉林没有这个二级域名）。真实站点是省
    # 政府信息公开专栏下的厅局子站。**这张表是健康检查显示 URL 的来源**，
    # 写错这里，表上就只能是「没连上」——与「TLS 谈不成」「412」逐格相同。
    _s("jl_em", "吉林省应急管理厅", "province", "吉林省应急管理厅",
       "https://xxgk.jl.gov.cn/zsjg/aqjg/xxgkmlqy/", 1.0,
       "jl_em", "JlEMCollector"),
    _s("js_em", "江苏省应急管理厅", "province", "江苏省应急管理厅",
       "https://yjglt.jiangsu.gov.cn/col/col81395/index.html", 1.0,
       "js_em", "JsEMCollector"),
    _s("jx_em", "江西省应急管理厅", "province", "江西省应急管理厅",
       "https://yjglt.jiangxi.gov.cn/jxsyjglt/col/251210dxsgal/", 1.0,
       "jx_em", "JxEMCollector"),
    _s("ln_em", "辽宁省应急管理厅", "province", "辽宁省应急管理厅",
       "https://yjgl.ln.gov.cn/yjgl/xxgk/zwgkzdgz/aqsczdlyxxgk/sgdccl/index.shtml", 1.0,
       "ln_em", "LnEMCollector",
       kwargs=_NO_CLASSIFY,
       note="默认下 PDF（正文多在 PDF 里），文件落执行器给的临时目录"),
    _s("mem", "应急管理部（特别重大事故）", "national", "应急管理部",
       "https://www.mem.gov.cn/gk/sgcc/tbzdsgdcbg/", 2.0,
       "mem", "MemCollector",
       note="只收特别重大事故调查报告，量少但都是完整报告"),
    _s("mohurd", "住房和城乡建设部事故通报", "national", "住房和城乡建设部",
       "https://www.mohurd.gov.cn", 1.0,
       "mohurd", "MohurdCollector",
       kwargs=_MOHURD_ONLY,
       note="不挂 raw_store，产出靠 collect() 返回值传出来；"
            "collect_mem 已关，应急部归 mem 源，见 _MOHURD_ONLY 的注释"),
    _s("news_aggregator", "新闻搜索聚合（百度 / Bing）", "media", "—",
       "", 1.0,
       "news_aggregator", "NewsAggregator",
       note="没有单一入口页；不挂 raw_store，产出靠 collect() 返回值传出来"),
    _s("nmg_em", "内蒙古自治区应急管理厅", "province", "内蒙古自治区应急管理厅",
       "https://yjglt.nmg.gov.cn/zwgk/zdxx/sgxxccpg/", 1.0,
       "nmg_em", "NmgEMCollector",
       kwargs=_NO_CLASSIFY),
    _s("open_gov", "政府开放数据平台", "open_data", "—",
       "", 1.0,
       "open_gov", "OpenGovCollector",
       note="多平台集合（含 cdata），不挂 raw_store，产出靠 collect() 返回值传出来"),
    _s("safehoo", "安全管理网", "media", "安全管理网",
       "https://www.safehoo.com/NewsSpecial/Build/", 2.0,
       "safehoo", "SafehooCollector",
       note="商业站，历史上贡献了主库全部境外/港澳台记录；默认 is_active 待裁决"),
    _s("sc_em", "四川省应急管理厅", "province", "四川省应急管理厅",
       "https://yjt.sc.gov.cn/scyjt/dcbg/GovInfoPublic.shtml", 1.0,
       "sc_em", "ScEMCollector"),
    _s("sh_em", "上海市应急管理局", "province", "上海市应急管理局",
       "https://yjglj.sh.gov.cn/xxgk/zfxxgk/zcwj/sgdcbg/", 1.0,
       "sh_em", "ShEMCollector"),
    _s("shaanxi_em", "陕西省应急管理厅", "province", "陕西省应急管理厅",
       "https://yjt.shaanxi.gov.cn/gk/zwxxgk/sgdc/index_1.html", 1.0,
       "shaanxi_em", "ShaanxiEMCollector"),
    _s("sx_em", "山西省应急管理厅", "province", "山西省应急管理厅",
       "https://yjt.shanxi.gov.cn/zwgk_0/zfxxgk_78327/fdzdgknr_78331/gszl/sgdc_78341/", 1.0,
       "sx_em", "SxEMCollector"),
    _s("tj_em", "天津市应急管理局", "province", "天津市应急管理局",
       "https://yjgl.tj.gov.cn/ZWGK6939/SGDCBG354/", 1.0,
       "tj_em", "TjEMCollector"),
])


def get(key: str) -> SourceSpec:
    """按 source_key 取登记项，取不到就抛 KeyError（不做静默兜底）。"""
    try:
        return SOURCES[key]
    except KeyError:
        raise KeyError(f"未登记的采集源: {key!r}；已知 {len(SOURCES)} 个") from None


def all_sources() -> list[SourceSpec]:
    """全部源，按 source_key 字典序。"""
    return [SOURCES[k] for k in sorted(SOURCES)]


def by_group() -> dict[str, list[SourceSpec]]:
    """`GET /api/collect/sources` 的首屏分组。"""
    out: dict[str, list[SourceSpec]] = {}
    for spec in all_sources():
        out.setdefault(spec.group, []).append(spec)
    return out


def build(key: str, *, data_dir: Optional[Path] = None
          ) -> tuple[Any, SourceSpec]:
    """按登记表构造一个采集器。

    `data_dir` 传了就透给采集器（7 个源是本轮才加的这个参数，其余 15 个本来
    就有）；源不接受这个参数时**静默跳过**——实测只剩 `esafety` 不接受
    （`gd_em` 第二十轮补上了，它要下载附件提正文，需要落盘位置），而
    `esafety` 本身不建任何目录，跳过是安全的。

    返回 `(collector, spec)`：执行器两边都要用，spec 用来写 `collect_tasks`
    与日志，collector 用来真跑。
    """
    spec = get(key)
    module = importlib.import_module(_PKG + "." + spec.module)
    cls = getattr(module, spec.cls)
    sig = inspect.signature(cls.__init__)

    kwargs: dict[str, Any] = dict(spec.build_kwargs)
    if data_dir is not None:
        if "data_dir" in sig.parameters:
            kwargs["data_dir"] = Path(data_dir)
        else:
            logger.debug("[%s] 构造器不接受 data_dir，跳过隔离设置", key)

    # 只留构造器认识的参数：`build_kwargs` 是按源写的，多传会 TypeError。
    # 这一层过滤让「给 5 个源设 enable_classify」不需要 24 个源各写一遍。
    accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}
    dropped = set(kwargs) - set(accepted)
    if dropped:
        logger.debug("[%s] 忽略构造器不接受的参数: %s", key, sorted(dropped))
    return cls(**accepted), spec


def sync_to_db(conn) -> int:
    """把登记表 upsert 进工作库的 `collect_sources`，返回写入行数。

    **只覆盖元信息**：`is_active` / `enable_classify` / `last_run_at` /
    `last_run_ok` / `last_error` / `total_raw` / `total_promoted` 都是运行期
    状态或人工开关，重跑这个函数不许把它们抹掉——那等于每次同步都清空一遍
    采集历史和人工的取舍。所以 `DO UPDATE SET` 里没有它们。

    幂等：反复调用结果一致，`source_key` 是主键。
    """
    rows = [
        (s.key, s.display_name, s.group, TRACK_NETWORK, s.entry_url or None,
         s.crawl_delay, 1, 1, s.note or None)
        for s in all_sources()
    ]
    conn.executemany(
        """
        INSERT INTO collect_sources
            (source_key, display_name, source_group, track, entry_url,
             crawl_delay, enable_classify, is_active, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            display_name = excluded.display_name,
            source_group = excluded.source_group,
            track        = excluded.track,
            entry_url    = excluded.entry_url,
            crawl_delay  = excluded.crawl_delay,
            notes        = excluded.notes
        """,
        rows,
    )
    return len(rows)
