"""`jl_em` 两层通路的单元测试。**不联网。**

本源的两层都不是从别的源推得的，各有一组判据钉住（形态见模块 docstring）：

1. **域名笔误**——原配置写 `yjglt.jl.gov.cn`，那个域名 DNS 都解析不出来。
   同一个域名在仓库里出现**三处**（采集器 / `registry.py` / `run_collect.py`），
   2026-10-05 修的时候正好是「一处对、两处错」，所以这里有一条测试专门钉
   `registry.py` 那份，防它再漂回去。
2. **列表是 JSONP**——`puburl` / `efectdate` 的形态与 `jx_em` 的
   `urls.pc` / `pubDate` 都不同，且 `channelId` 与 `subcat` 是两个不同的参数
   （混起来会安安静静地得到 `num=0`）。

最后一条是 `collect()` 级的：`jx_em` 那轮的教训写得很直白——**脚本级直调全绿
≠ `collect()` 绿**，而「到底采到几条」只有 `collect()` 答得出。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from construction_agent.collector.sources import jl_em as jl
from construction_agent.collector.sources.jl_em import (
    JlEMCollector,
    _api_url,
    _loads_jsonp,
    _norm_date,
    _prefer_https,
)


@pytest.fixture
def collector(tmp_path):
    # `delay=0`：不联网，取数只是为了拿到 `cfg`。
    return JlEMCollector(delay=0, data_dir=tmp_path)


# 与真实接口同形的一条记录（2026-10-05 落盘的第 1 条）。**保留了那些空壳键**
# ——接口一条记录吐 ~90 个键、绝大多数为空，是本源列表的常态而非异常。
_REAL_ITEM = {
    "puburl": ("http://xxgk.jl.gov.cn/zsjg/aqjg/xxgkmlqy/202211/"
               "t20221129_8640381.html"),
    "MetaDataId": "8640381",
    "title": "吉林化纤股份有限公司“2·27”较大中毒事故防范和整改措施落实情况评估报告",
    "efectdate": "2022.11.21 00:00:00",
    "effect": "1",
    "isguifan": "0",
    "jd_title1": "",
    "jd_links1": "",
    "tip": {"dates": "2022-11-29", "MetaDataId": "8640381", "subcat": ""},
}


def _jsonp(payload: dict, cb: str = "result") -> str:
    return f"{cb}({json.dumps(payload, ensure_ascii=False)});"


def _page(items: list[dict], num: int | None = None, n_page: int | None = None) -> str:
    payload: dict = {"num": len(items) if num is None else num,
                     "data": items}
    if n_page is not None:
        payload["nPage"] = n_page
    return _jsonp(payload)


# --------------------------------------------------------------------------
# 纯函数：域名升级 / JSONP 剥离 / 日期归一
# --------------------------------------------------------------------------

def test_白名单域名的http升级成https():
    """接口返回的 `puburl` 全写明文 http，而站点支持 https（实测 200）。"""
    assert (_prefer_https("http://xxgk.jl.gov.cn/a/b.html")
            == "https://xxgk.jl.gov.cn/a/b.html")


def test_别的域名不做升级():
    """**不做全局替换**：这个接口是全省共用的，列表里可能混进别的域名，
    而强行 https 会把「这个站本来就是 http」变成一个采集故障。"""
    assert _prefer_https("http://other.gov.cn/a.html") == "http://other.gov.cn/a.html"
    assert _prefer_https("https://xxgk.jl.gov.cn/a.html") == "https://xxgk.jl.gov.cn/a.html"


def test_JSONP外壳被剥掉():
    assert _loads_jsonp('result({"num":5});') == {"num": 5}
    assert _loads_jsonp('result({"num":5})') == {"num": 5}
    # 裸 JSON 也要认：`callback` 是接口参数，换一下回调名就不该全崩。
    assert _loads_jsonp('{"num":5}') == {"num": 5}


def test_JSONP解析不了时返回None而不是空dict():
    """`None` = 接口换形态了要改解析；`{"num":0,"data":[]}` = 这个栏目真的没有
    内容。两者在健康检查表上都显示「解析空」，但处置相反。"""
    assert _loads_jsonp("<html>被 WAF 换成了拦截页</html>") is None
    assert _loads_jsonp("") is None


@pytest.mark.parametrize("raw,want", [
    ("2022.11.21 00:00:00", "2022-11-21"),   # 接口的点分隔
    ("2022-11-29", "2022-11-29"),            # tip.dates
    ("2022/4/9", "2022-04-09"),              # 不补零也认，补成两位
    ("", ""),
    (None, ""),
    ("不是日期", ""),
])
def test_日期归一(raw, want):
    assert _norm_date(raw) == want


def test_接口地址三个参数都带上了():
    u = _api_url(3)
    assert "page=3" in u
    assert f"lb={jl._LB}" in u
    assert f"channelId={jl._CHANNEL_ID}" in u
    # **`subcat` 与 `channelId` 是两个不同的参数**：混起来会得到 num=0
    # （实测：`channelId=5359` 无 subcat → 100 字节的空 JSONP）。
    assert f"subcat={jl._SUBCAT_ACCIDENT_REPORT}" in u


# --------------------------------------------------------------------------
# 配置：域名笔误的修法
# --------------------------------------------------------------------------

def test_配置用的是真实域名(collector):
    assert collector.cfg.base_url == "https://xxgk.jl.gov.cn"
    assert "yjglt" not in collector.cfg.base_url
    assert "yjglt" not in collector.cfg.list_url


def test_registry里的URL与采集器同域():
    """**同一个域名在三处出现，改一处会漂。**

    2026-10-05 修之前正好是「`run_collect.py` 写对、`jl_em.py` 与
    `registry.py` 写错」——而健康检查表上显示的 URL 取的正是 `registry.py`
    那份，所以从表上看只能读成「没连上」，与 TLS 失败、412 拦截逐格相同。
    """
    root = Path(__file__).resolve().parents[1]
    txt = (root / "construction_agent" / "collector" / "registry.py").read_text(
        encoding="utf-8")
    m = re.search(r'_s\("jl_em".*?\n\s*"([^"]+)"', txt, re.S)
    assert m, "在 registry.py 里找不到 jl_em 的条目"
    assert "xxgk.jl.gov.cn" in m.group(1), f"registry 里还是 {m.group(1)}"


# --------------------------------------------------------------------------
# 第二层：JSONP 列表接口
# --------------------------------------------------------------------------

def test_列表条目解析成标题URL日期(collector):
    links = collector._parse_list(_page([_REAL_ITEM], n_page=1))
    assert len(links) == 1
    one = links[0]
    assert one["url"] == ("https://xxgk.jl.gov.cn/zsjg/aqjg/xxgkmlqy/"
                          "202211/t20221129_8640381.html")
    assert one["date"] == "2022-11-21", "efectdate 优先于 tip.dates 的 2022-11-29"
    assert "吉林化纤" in one["title"]


def test_efectdate缺失时退到tip里的发布日期(collector):
    """一条记录 ~90 个键、绝大多数为空——「某个字段缺失」是本源的常态。"""
    it = {k: v for k, v in _REAL_ITEM.items() if k != "efectdate"}
    links = collector._parse_list(_page([it]))
    assert len(links) == 1
    assert links[0]["date"] == "2022-11-29"


def test_puburl是javascript的条目跳过(collector):
    """站点 JS 对这种情况会改指向「依申请公开」表单，那不是事故详情页。"""
    it = dict(_REAL_ITEM, puburl="javascript:void(0)")
    assert collector._parse_list(_page([it])) == []


def test_缺标题或缺地址的条目跳过(collector):
    items = [dict(_REAL_ITEM, title=""),
             dict(_REAL_ITEM, puburl=""),
             dict(_REAL_ITEM, puburl="   "),
             _REAL_ITEM]
    assert len(collector._parse_list(_page(items))) == 1


def test_标题里的全角空格与换行被清掉(collector):
    it = dict(_REAL_ITEM, title=" 吉林化纤\n股份　有限公司 事故 ")
    assert collector._parse_list(_page([it]))[0]["title"] == "吉林化纤股份有限公司事故"


def test_列表接口没返回JSONP时返回空表并记一声(collector, caplog):
    with caplog.at_level("WARNING"):
        assert collector._parse_list("<html>拦截页</html>") == []
    assert any("没返回可解析的 JSONP" in r.message for r in caplog.records)


def test_接口返回空data时是零条而不是错误(collector):
    """`{"num":0,"data":[]}` 与解析失败必须分开——前者是「栏目真的没有」。"""
    assert collector._parse_list(_page([])) == []


# --------------------------------------------------------------------------
# 分页：本源与 jx_em 的关键区别——**这个接口能翻页**
# --------------------------------------------------------------------------

def test_单页栏目只返回一个地址(collector):
    collector._fetch = lambda url: _page([_REAL_ITEM], num=5, n_page=1)
    assert collector._discover_pages() == [_api_url(1)]


def test_第1页的响应进了缓存不重复发(collector):
    body = _page([_REAL_ITEM] * 16, num=40, n_page=3)
    collector._fetch = lambda url: body
    collector._discover_pages()
    assert collector._page_cache[_api_url(1)] == body


def test_多页栏目按nPage展开(collector):
    collector._fetch = lambda url: _page([_REAL_ITEM] * 16, num=40, n_page=3)
    urls = collector._discover_pages()
    assert urls == [_api_url(n) for n in (1, 2, 3)]


def test_nPage缺失时按num和每页条数推算(collector):
    """`nPage` 是接口给的、最准；「每页 16 条」只是观察值。缺了才退化推算。"""
    collector._fetch = lambda url: _page([_REAL_ITEM] * 16, num=17)
    assert len(collector._discover_pages()) == 2


def test_第1页取不到时返回空列表(collector):
    """交给 `collect()` 走「0 个列表页」；失败已由 `_fetch` 记进 `_fetch_failed`。"""
    collector._fetch = lambda url: None
    assert collector._discover_pages() == []


def test_接口不是JSONP时交回地址让parse给出零条(collector, caplog):
    """**不在 `_discover_pages` 里替 `_parse_list` 下结论**——否则
    「解析出 0 条」这件事就没有任何一行代码看得见。"""
    collector._fetch = lambda url: "<html>拦截页</html>"
    with caplog.at_level("WARNING"):
        assert collector._discover_pages() == [_api_url(1)]


# --------------------------------------------------------------------------
# 端到端：**只有 `collect()` 答得出「采到几条」**（jx_em 那轮的教训）
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.content = b""
        self.text = ""


class _RouteClient:
    """按 URL 子串分发的假 client——列表接口与详情页要回不同的东西。"""

    def __init__(self, routes: list[tuple[str, int, str]]) -> None:
        self.routes, self.calls = routes, []

    def get(self, url: str, **kw):
        self.calls.append(url)
        for sub, code, text in self.routes:
            if sub in url:
                r = _FakeResp(code)
                r.text = text
                return r
        r = _FakeResp(404)
        return r

    def close(self) -> None:
        pass


_DETAIL_HTML = """<html><body><div>
索引号：11220000740468418C/2022-05568　分类：安全生产事故调查报告
标题：吉林省长春市李氏婚纱梦想城“7·24”重大火灾事故调查报告
<a href="//xxgk.jl.gov.cn/PDFfile/202211/8639621.pdf">pdf下载</a>
</div></body></html>"""


def test_端到端列表层走通并计入总数(tmp_path):
    """`jx_em` 那轮写下的教训：**脚本级直调过了 ≠ `collect()` 过了**。

    这里量的是「列表层」，即 `_total_listed`（标题初筛**之前**的总数）——
    它是 `collect()` 独有的计数，`_parse_list` 单测永远看不到。
    """
    items = [dict(_REAL_ITEM, title="吉林省长春市李氏婚纱梦想城“7·24”重大火灾事故调查报告"),
             dict(_REAL_ITEM, efectdate="2022.06.27 00:00:00")]
    c = JlEMCollector(delay=0, data_dir=tmp_path)
    real = c._client
    c._client = _RouteClient([
        ("zf_jd_list.jsp", 200, _page(items, num=2, n_page=1)),
        ("t2022", 200, _DETAIL_HTML),
    ])
    try:
        c.collect()
        assert c._list_pages_ok == 1
        assert c._total_listed == 2, "两条都该进列表总数"
        assert c._fetch_failed == 0, (
            f"列表层不该有失败请求，实际 {c._fetch_failed} 次；"
            f"打过：{c._client.calls}")
    finally:
        c._client = real
        real.close()


def test_端到端列表页取不到时计为没连上(tmp_path):
    """假 client 对列表接口回 404：`_fetch` 立刻返回 None，不计重试。"""
    c = JlEMCollector(delay=0, data_dir=tmp_path)
    real = c._client
    c._client = _RouteClient([("zf_jd_list.jsp", 404, "")])
    try:
        out = c.collect()
        assert out == []
        assert c._list_pages_ok == 0
        assert c._total_listed == 0
    finally:
        c._client = real
        real.close()
