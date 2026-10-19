"""`OpenGovCollector` 的失败通路，以及「全灭」这件事说不说得出来。

**不联网、不真调政府平台**：`_fetch_dataset` / `httpx.Client` 换成假的。

2026-10-01 第三十九轮实测（干跑，3 个默认激活数据集）：

    shenyang  base_url 200 / 96,594 活着；但 `api_endpoint` 返回的是 **HTML**
              （一次 200 / 61,375、另一次 502 / 156），**从来不是 JSON**——
              `resp.json()` 抛 `JSONDecodeError`，被 `logger.debug` 吞掉
    heze      `Network is unreachable`。域名同时有 A 与 AAAA 记录
              （58.59.15.41 + 2408:8614:402:7fff:ffff:ffff:0:30c9），
              本机没有 IPv6 出口
    shandong  base_url 200 / 179,446 活着，但**配置里压根没有 api_endpoint**，
              本地也没有手工下载的文件

三条通路**一条都没通**，可是修前在健康检查表上这三格与「平台今天真的没数据」
**逐格相同**（都是「空 / 0 条」），日志里也没有任何一句话说得出「全部失败」。
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.sources import open_gov  # noqa: E402
from construction_agent.collector.sources.open_gov import (  # noqa: E402
    OpenGovCollector)

LOGGER_NAME = "construction_agent.collector.sources.open_gov"

# 三个默认无条件开放的数据集（`open_access=True`），也就是实跑时激活的那三个
_THREE = ["shenyang", "heze", "shandong"]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """`collect()` 每个数据集之间 `sleep(5.0)`，三个就是 15 秒。"""
    monkeypatch.setattr(open_gov.time, "sleep", lambda _s: None)


def _make(tmp_path, sources: list[str] | None = None) -> OpenGovCollector:
    return OpenGovCollector(data_dir=tmp_path, sources=sources or _THREE)


class _FakeResp:
    def __init__(self, status_code: int, body, as_json: bool = True):
        self.status_code = status_code
        self._body = body
        self._as_json = as_json

    def json(self):
        if not self._as_json:
            raise json.JSONDecodeError("Expecting value", str(self._body), 0)
        return self._body


class _FakeClient:
    def __init__(self, resp: _FakeResp):
        self._resp = resp

    def get(self, url):
        return self._resp


# --------------------------------------------------------------------------
# 「全灭」要说得出来
# --------------------------------------------------------------------------


def test_全部数据集没采到时计数并打error(tmp_path, caplog):
    agg = _make(tmp_path)
    assert len(agg._active_sources) == 3
    agg._fetch_dataset = lambda ds: []

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        got = agg.collect()

    assert got == []
    assert agg._fetch_failed == 3
    assert "全部" in caplog.text
    assert "通路本身不通" in caplog.text, "要说清楚这不是「平台今天没内容」"


def test_只有一个数据集没采到时不报全灭(tmp_path, caplog):
    """1/3 失败不能说成全部——否则告警变噪音。"""
    agg = _make(tmp_path)
    ok = {"事故名称": "某工地高坠", "事故概况": "正文" * 20}
    agg._fetch_dataset = lambda ds: ([ok] if ds["name"].endswith("shandong") else [])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        got = agg.collect()

    assert len(got) == 1
    assert agg._fetch_failed == 2
    assert "全部" not in caplog.text


def test_数据集抛异常也计入并带上异常名(tmp_path, caplog):
    agg = _make(tmp_path, sources=["shenyang"])

    def _boom(ds):
        raise ConnectionError("Network is unreachable")

    agg._fetch_dataset = _boom

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        got = agg.collect()

    assert got == []
    assert agg._fetch_failed == 1
    assert "ConnectionError" in caplog.text
    assert "Network is unreachable" in caplog.text


# --------------------------------------------------------------------------
# `_fetch_dataset` 的三条失败来路，各自要看得见
# --------------------------------------------------------------------------


def test_API返回非JSON时是warning不是debug(tmp_path, caplog):
    """实测形状：`api_endpoint` 返回 HTML，`resp.json()` 抛 JSONDecodeError。

    修前这里是 `logger.debug`——**默认级别下这一行等于不存在**，于是「端点
    返回 HTML 不是 JSON」这件事从来没有出现在任何日志里。
    """
    agg = _make(tmp_path, sources=["shenyang"])
    agg._client = _FakeClient(_FakeResp(200, "<html>不是 JSON</html>", as_json=False))

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        items = agg._fetch_dataset(agg._active_sources[0])

    assert items == []
    assert "JSONDecodeError" in caplog.text
    assert "未经确认" in caplog.text, "端点是否还有效是没确认过的事，不能说得比知道的多"


def test_API返回200但解不出记录时也要说(tmp_path, caplog):
    agg = _make(tmp_path, sources=["shenyang"])
    agg._client = _FakeClient(_FakeResp(200, {"code": 0, "msg": "ok"}))

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        items = agg._fetch_dataset(agg._active_sources[0])

    assert items == []
    assert "解不出记录" in caplog.text


def test_API非200状态码也要说(tmp_path, caplog):
    """实测遇到过一次 **502 / 156 字节**。

    修前 `if resp.status_code == 200:` 为假，整个 API 分支**静默跳过**——
    这条失败在日志里一个字都没有，与「压根没配 api_endpoint」同形。
    """
    agg = _make(tmp_path, sources=["shenyang"])
    agg._client = _FakeClient(_FakeResp(502, "Bad Gateway", as_json=False))

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        items = agg._fetch_dataset(agg._active_sources[0])

    assert items == []
    assert "502" in caplog.text


def test_没配api_endpoint时不说_API失败(tmp_path, caplog):
    """反例：`shandong` 配置里压根没有端点，不该报「API 返回了 502」。

    没有端点就是没有端点——把「没配」说成「取数失败」会把人引到错的方向。
    """
    agg = _make(tmp_path, sources=["shandong"])
    ds = agg._active_sources[0]
    assert not ds.get("api_endpoint")

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        items = agg._fetch_dataset(ds)

    assert items == []
    assert "502" not in caplog.text
    assert "取数失败" not in caplog.text


# --------------------------------------------------------------------------
# 计数器的存在本身
# --------------------------------------------------------------------------


def test_计数器与TRS基类同名(tmp_path):
    """`check_collector_health.py` 的 `_probe_child` 按 `_fetch_failed` 读。

    名字写错不会报错，只会让表上那栏显示出「没有计数器」的样子。
    """
    agg = _make(tmp_path)
    assert isinstance(agg._fetch_failed, int)
    assert agg._fetch_failed == 0


def test_判据自检_不记_fetch_failed这一条就红(tmp_path, caplog):
    """反改验证：把 `_note_failure` 换回空操作（等于修前）。

    修前没有 `_fetch_failed` 这个属性，`_probe_child` 读到 None，表上「闭环」
    那栏是 `—`，且**三个数据集全灭时没有任何一行日志说得出这件事**。
    """
    agg = _make(tmp_path)
    agg._fetch_dataset = lambda ds: []
    agg._note_failure = lambda ds, reason: None  # 等于修前

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        got = agg.collect()

    assert got == []
    assert agg._fetch_failed == 0
    assert "全部" not in caplog.text


# --------------------------------------------------------------------------
# 本地文件的归属：谁的文件归谁（§2.5.51）
# --------------------------------------------------------------------------

def _ds(c, name: str) -> dict:
    return next(x for x in c._active_sources if x["name"] == name)


def test_本类缓存文件不会被当成本地数据文件(tmp_path):
    """`{name}_cache.json` **匹配** `{name}*.json`——但它不是数据源。

    不管的话 `_load_cache` 那道 30 天闸门会被绕过：缓存过期返回空、本该去
    平台重新取数，结果 `_fetch_dataset` 又从缓存文件里把同一份旧数据读回来。
    """
    c = _make(tmp_path, ["shandong"])
    (tmp_path / "open_gov_shandong_cache.json").write_text(
        json.dumps([{"备注": "30 天前的旧数据"}], ensure_ascii=False),
        encoding="utf-8")

    assert c._fetch_dataset(_ds(c, "open_gov_shandong")) == []


def test_别的数据集的文件不会被误用(tmp_path):
    """无前缀回退会捞到另一家的文件，而转换时用的是**本家**的字段映射。"""
    c = _make(tmp_path, ["shandong"])
    (tmp_path / "open_gov_heze.csv").write_text(
        "单位名称,隐患描述\n菏泽某工地,脚手架连墙件缺失\n", encoding="utf-8")

    assert c._fetch_dataset(_ds(c, "open_gov_shandong")) == []


def test_自己前缀的文件仍能读到(tmp_path):
    """修法不能把正常路径一起关掉——这是 `_print_manual_guide` 推荐的命名。"""
    c = _make(tmp_path, ["shandong"])
    (tmp_path / "open_gov_shandong.csv").write_text(
        "单位名称,隐患描述\n烟台某工程,安全备案信息缺项\n", encoding="utf-8")

    got = c._fetch_dataset(_ds(c, "open_gov_shandong"))
    assert len(got) == 1
    assert got[0]["单位名称"] == "烟台某工程"


def test_无前缀且非他家的文件仍能读到(tmp_path):
    """无前缀回退是**有意**的：用户手工下载多半不会按我们的命名规则改名。"""
    c = _make(tmp_path, ["shandong"])
    (tmp_path / "栖霞市建设工程.csv").write_text(
        "单位名称,隐患描述\n栖霞某工程,备案缺失\n", encoding="utf-8")

    assert len(c._fetch_dataset(_ds(c, "open_gov_shandong"))) == 1


def test_缓存过期后不会被自己的缓存续期(tmp_path):
    """闸门的端到端形态：过期不是「读不到」，而是「读到了同一份旧的」。

    修前的实际路径——`_load_cache` 返回空（过期）→ `_fetch_dataset` 从
    **同一个缓存文件**里把数据读回来 → `collect()` 再 `_save_cache` 续期。
    「30 天过期」等于没写。修后断言两件事：没有产出，且缓存文件没被续期。
    """
    c = _make(tmp_path, ["shandong"])
    cache = tmp_path / "open_gov_shandong_cache.json"
    cache.write_text(json.dumps([{"单位名称": "旧数据"}], ensure_ascii=False),
                     encoding="utf-8")
    old = time.time() - 60 * 86400          # mtime 推到 60 天前
    os.utime(cache, (old, old))

    out = c.collect()

    assert out == [], "过期缓存不应被当成数据产出"
    assert c._fetch_failed == 1
    assert os.path.getmtime(cache) < time.time() - 50 * 86400, "缓存被续期了"
