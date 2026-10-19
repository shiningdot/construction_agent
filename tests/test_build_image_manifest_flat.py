"""平铺池归属的回归测试（`build_image_manifest.py` 的 `flat` 分支）。

**这块逻辑值得单独盯住的原因**：平铺池里 3,701 个文件此前一张都没进过
清单，把它们接上来靠的是三道推断——手工表、旧库标题桥、主库 id 前缀桥。
推断一旦放宽，后果不是"少几张图"，而是**把别的工地的照片挂到这条事故
的详情页上**，读者无从分辨。所以这里逐条钉死"什么情况下归属、什么情况
下必须留空"。

全部在 `tmp_path` 下自建库与目录，绝不碰 `data/caiems_v2.db`。
"""

from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# scripts/ 不是包，只能按文件路径加载
_spec = importlib.util.spec_from_file_location(
    "build_image_manifest", ROOT / "scripts" / "build_image_manifest.py")
bim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bim)

# 4 字节就是 PNG 魔数，够 `probe()` 认它是图片
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def _write_final_db(path: Path, rows: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE accidents (id TEXT PRIMARY KEY, title TEXT NOT NULL);")
    con.executemany("INSERT INTO accidents (id, title) VALUES (?, ?)", rows)
    con.commit()
    con.close()


def _write_legacy_db(path: Path, rows: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE accidents (id TEXT PRIMARY KEY, title TEXT NOT NULL);")
    con.executemany("INSERT INTO accidents (id, title) VALUES (?, ?)", rows)
    con.commit()
    con.close()


# ══════════════════════════════════════════════════════════════════════
# 文件名解析
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("stem,expected", [
    ("018e3f8e_01", "018e3f8e"),
    ("018e3f8e_12", "018e3f8e"),
    ("齐齐哈尔体育馆_02", "齐齐哈尔体育馆"),
    # 结尾不是「下划线 + 纯数字」，`_致3死` 是标题的一部分，不能剥
    ("湖南邵东一水库隧洞施工事故_致3死", "湖南邵东一水库隧洞施工事故_致3死"),
    ("湖南邵东一水库隧洞施工事故_致3死_1", "湖南邵东一水库隧洞施工事故_致3死"),
    ("7_22广州塔吊倒塌事故", "7_22广州塔吊倒塌事故"),
])
def test_flat_ident(stem, expected):
    assert bim.flat_ident(stem) == expected


def test_norm_title_strips_punctuation_only():
    assert bim.norm_title("甘肃甘州区“7·2”施工升降机坠落事故") == \
        "甘肃甘州区72施工升降机坠落事故"
    # 不做同义替换：不改字、不补后缀
    assert bim.norm_title("某工地坍塌") == "某工地坍塌"


# ══════════════════════════════════════════════════════════════════════
# 归属判定
# ══════════════════════════════════════════════════════════════════════


def test_prefix_bridge_unique(tmp_path):
    """主库 12 位 id 的前 8 位 = 平铺标识 → 归属。"""
    final = tmp_path / "final.db"
    _write_final_db(final, [("018e3f8e82c6", "武汉天兴洲大桥引桥钢梁断裂坍塌事故")])
    owner, diag = bim.build_owner_map(final, None, {"018e3f8e"})
    assert owner == {"018e3f8e": "018e3f8e82c6"}
    assert diag["by_prefix"] == {"018e3f8e": "018e3f8e82c6"}


def test_prefix_bridge_needs_unique_prefix(tmp_path):
    """前 8 位撞车时宁可留空——挂错事故比没有图严重。"""
    final = tmp_path / "final.db"
    _write_final_db(final, [("018e3f8e1111", "甲事故"), ("018e3f8e2222", "乙事故")])
    owner, diag = bim.build_owner_map(final, None, {"018e3f8e"})
    assert "018e3f8e" not in owner
    assert diag["prefix_ambiguous"] == {"018e3f8e": ["018e3f8e1111", "018e3f8e2222"]}


def test_title_bridge_wins_over_prefix(tmp_path):
    """两桥都通时走标题桥（语义证据），且结论被记录在 bridged 而非 by_prefix。"""
    final = tmp_path / "final.db"
    old = tmp_path / "old.db"
    _write_final_db(final, [("aaaa11112222", "某地脚手架坍塌事故")])
    _write_legacy_db(old, [("aaaa1111", "某地脚手架坍塌事故")])
    owner, diag = bim.build_owner_map(final, old, {"aaaa1111"})
    assert owner == {"aaaa1111": "aaaa11112222"}
    assert diag["bridged"] == {"aaaa1111": "aaaa11112222"}
    assert diag["by_prefix"] == {}


def test_title_bridge_survives_rewritten_title(tmp_path):
    """标题被改写、前缀也对不上时——两桥都不通，必须留空。"""
    final = tmp_path / "final.db"
    old = tmp_path / "old.db"
    _write_final_db(final, [("bbbb11112222", "完全不同的另一起事故")])
    _write_legacy_db(old, [("aaaa1111", "某地脚手架坍塌事故")])
    owner, diag = bim.build_owner_map(final, old, {"aaaa1111"})
    assert owner == {}
    assert diag["no_main_match"] == ["aaaa1111"]


def test_ambiguous_title_is_skipped(tmp_path):
    """主库两条事故标题归一化后相同 → 不归属（实测有 4 组这种）。"""
    final = tmp_path / "final.db"
    old = tmp_path / "old.db"
    _write_final_db(final, [("cccc11112222", "杭州地铁塌陷事故"),
                            ("cccc33334444", "杭州地铁塌陷事故")])
    _write_legacy_db(old, [("aaaa1111", "杭州地铁塌陷事故")])
    owner, diag = bim.build_owner_map(final, old, {"aaaa1111"})
    assert owner == {}
    assert diag["ambiguous"]["aaaa1111"] == ["cccc11112222", "cccc33334444"]


def test_manual_owner_applied(tmp_path):
    """手工表优先，且不看标题——「齐齐哈尔体育馆」与主库标题不构成子串。"""
    final = tmp_path / "final.db"
    _write_final_db(final, [("ae16d12d2c7f", "齐齐哈尔市第三十四中学体育馆屋顶坍塌事故")])
    owner, diag = bim.build_owner_map(final, None, {"齐齐哈尔体育馆"})
    assert owner == {"齐齐哈尔体育馆": "ae16d12d2c7f"}
    assert diag["manual"] == {"齐齐哈尔体育馆": "ae16d12d2c7f"}


def test_manual_owner_pointing_nowhere_is_fatal(tmp_path):
    """手工表写错 id 是脚本 bug，必须炸——不能悄悄变成「这几张图没归属」。"""
    final = tmp_path / "final.db"
    # 库里没有手工表指定的那个 id（`ae16d12d2c7f`）
    _write_final_db(final, [("111122223333", "别的什么事故")])
    with pytest.raises(SystemExit):
        bim.build_owner_map(final, None, {"齐齐哈尔体育馆"})


def test_manual_owner_unused_ident_not_checked(tmp_path):
    """平铺池里没出现的标识，其手工映射不该拖垮一次无关的扫描。"""
    final = tmp_path / "final.db"
    _write_final_db(final, [("111122223333", "别的什么事故")])
    owner, _ = bim.build_owner_map(final, None, {"11112222"})
    assert owner == {"11112222": "111122223333"}


def test_third_id_system_unresolved(tmp_path):
    """第三套 8 位 hex：旧库没它、主库前缀没它 → 无从归属。"""
    final = tmp_path / "final.db"
    old = tmp_path / "old.db"
    _write_final_db(final, [("dddd11112222", "甲事故")])
    _write_legacy_db(old, [("eeee9999", "乙事故")])
    owner, diag = bim.build_owner_map(final, old, {"ffff0000"})
    assert owner == {}
    assert diag["unresolved_hex"] == ["ffff0000"]


def test_denied_idents_never_owned(tmp_path):
    """明确排除表里的标识不归属——哪怕旧库里真有一条同名记录。"""
    final = tmp_path / "final.db"
    old = tmp_path / "old.db"
    _write_final_db(final, [("aaaa11112222", "某事故")])
    _write_legacy_db(old, [("7_22广州塔吊倒塌事故", "某事故")])
    denied = next(iter(bim.FLAT_DENIED))
    owner, _ = bim.build_owner_map(final, old, {denied})
    assert owner == {}


# ══════════════════════════════════════════════════════════════════════
# 扫描
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def pool(tmp_path):
    """一个 `data/images/` 平铺目录，含可归属与不可归属各两批。"""
    data = tmp_path / "data"
    images = data / "images"
    images.mkdir(parents=True)
    # 可归属：前缀桥
    (images / "018e3f8e_01.jpg").write_bytes(JPG)
    (images / "018e3f8e_02.jpg").write_bytes(JPG + b"x")   # 内容不同
    (images / "018e3f8e_01.png").write_bytes(JPG)          # 与 _01.jpg 同内容
    # 不可归属
    (images / "ffff0000_01.jpg").write_bytes(JPG)
    # 明确排除
    (images / "7_22广州塔吊倒塌事故_1.jpg").write_bytes(JPG)
    return data, images


def test_scan_flat_pool_dedupes_by_sha1(tmp_path, pool):
    """同一事故内同内容的文件只留一条——不然详情页会出现多张一样的图。"""
    data, images = pool
    final = tmp_path / "final.db"
    _write_final_db(final, [("018e3f8e82c6", "武汉天兴洲大桥引桥钢梁断裂坍塌事故")])
    owner, _ = bim.build_owner_map(final, None, {"018e3f8e"})
    scan = bim.scan_flat_pool(images, data, owner)

    assert len(scan["records"]) == 2          # _01.jpg 与 _01.png 内容相同，去一
    assert {r["accident_id"] for r in scan["records"]} == {"018e3f8e82c6"}
    assert scan["denied_hits"] == {"7_22广州塔吊倒塌事故": 1}


def test_scan_flat_pool_drops_unowned(tmp_path, pool):
    """归属不上的文件直接丢弃，不进 records。"""
    data, images = pool
    scan = bim.scan_flat_pool(images, data, {})
    assert scan["records"] == []
    # 被明确排除的那个仍要计数，好在报告里交代
    assert scan["denied_hits"] == {"7_22广州塔吊倒塌事故": 1}


def test_scan_flat_pool_rejects_non_image_content(tmp_path):
    """扩展名像图片、内容是 HTML 的，剔除。"""
    data = tmp_path / "data"
    images = data / "images"
    images.mkdir(parents=True)
    (images / "aaaa1111_01.jpg").write_bytes(b"<html>404</html>")
    scan = bim.scan_flat_pool(images, data, {"aaaa1111": "aaaa11111111"})
    assert scan["records"] == []
    assert scan["bad_magic"] == ["images/aaaa1111_01.jpg"]


def test_scan_flat_pool_rel_path_is_media_servable(tmp_path, pool):
    """rel_path 必须以 `images/` 开头——`/media/` 路由的白名单只认这三个根。"""
    data, images = pool
    final = tmp_path / "final.db"
    _write_final_db(final, [("018e3f8e82c6", "武汉天兴洲大桥引桥钢梁坍塌事故")])
    owner, _ = bim.build_owner_map(final, None, {"018e3f8e"})
    scan = bim.scan_flat_pool(images, data, owner)
    assert scan["records"]
    for r in scan["records"]:
        parts = r["rel_path"].split("/")
        assert len(parts) >= 2 and parts[0] == "images"
