"""正文预览截断长度的共享约定（2026-10-01 第三十四轮）。

**这个文件是钉子，不是功能测试。** 它挡的是同一个错法再犯第五次：
正文该进多少字，本来只有一处写对，而没有任何地方写着正确答案。

- `gd_em`：第二十二轮已改成 40000，并在注释里写了
  「**别再写回三位数**」——**警告只保住了它自己那一个源**。
- `hebei_em` / `ln_em` / `nmg_em` / `henan_em`：到第三十四轮仍是各写一份
  `[:3000]`。实测后果见 `base.PREVIEW_CHARS` 的注释（主库 f4a3957053fd）。

修法是把 `PREVIEW_CHARS` 收进 `collector/base.py`（`BODY_MIN_CHARS` 的邻居），
五个源都指过去。下面三条钉住这个约定：

1. 常量本身不能写回小数；
2. **任何源**都不能再写死一个比它小的正文截断值（源码级扫描，不是只看这五个）；
3. 常量与 executor 那层 50000 的总上限保持留余量。
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from construction_agent.collector.base import PREVIEW_CHARS  # noqa: E402

SOURCES = ROOT / "construction_agent" / "collector" / "sources"

# 扫源码时剥掉行尾注释：本轮把四处 `[:3000]` 改掉后，`3000` 反而出现在
# **解释为什么改**的注释里——不剥的话这条钉子会被自己的说明文字触发。
_CODE_CUT = re.compile(r"#.*$", re.M)
_INDEX_CUT = re.compile(r"\[:(\d+)\]")

# 逐处读源码判定过的**采样**（不是正文截断），理由见
# `test_没有源把正文截断写死成更小的数` 的 docstring。
# 键 = (文件名, 截断值)，值 = 出现次数。
_KNOWN_SAMPLING_CUTS = {
    ("mem.py", 2000): 1,      # is_construction_text：关键词匹配的取样
    ("mem.py", 1000): 1,      # _extract_date：正则找日期
    ("safehoo.py", 1000): 2,  # is_construction_text + _extract_date
    ("safehoo.py", 5000): 1,  # _detect_pagination：找「共 N 条数据」
}


def test_共享常量不是三位数():
    """**别再写回三位数。**

    三位数意味着正文只到报告的前三四页。实测那 14,275 字里，一～七级章节
    的位置是 2494 / 4592 / 5562 / 7178 / 7943 / 11554 / 12921——3000 只
    保得住第一章，而「事故原因分析」（CAICS 编码的原始依据）在 5562。
    """
    assert PREVIEW_CHARS >= 20000, (
        f"PREVIEW_CHARS={PREVIEW_CHARS}，又回到三位数量级了——"
        f"正文会被切到报告前几页，CAICS 编码要用的「事故原因分析」章就在切口外"
    )


def test_常量给外层五千字上限留了余量():
    """`executor._row_from_sink_record` 还会把整个 item 截到 50000。

    常量等于或超过它，`content` 里就只剩正文——标题、来源、附件 URL
    那几行会被挤掉，而 `_ASSET_KEYS` 认附件正是靠 URL 行。
    """
    assert PREVIEW_CHARS < 50000, \
        "PREVIEW_CHARS 不该顶到外层的 50000，得给标题/来源/附件 URL 留余量"


def test_没有源把正文截断写死成更小的数():
    """源码级扫描：`sources/*.py` 里的字面截断必须是**登记过的采样**。

    只看那五个源不够——下一个源抄的会是**最近的那个源**，不是一个白纸。
    小于 1000 的不算（`[:50]` 那种是标题/文件名裁剪，不是正文）。

    **「截断」与「采样」得分清。** 第一次跑这条时扫出五处，逐一读过源码后
    全部是采样——取开头一段**只为了判断**（关键词匹配 / 正则找日期 / 探测
    分页数），那一段文本本身不进产出：

        mem.py:100        is_construction_text    title + content[:2000] 做关键词
        mem.py:656        _extract_date           content[:1000] 找「YYYY年MM月DD日」
        safehoo.py:112    is_construction_text    同上，[:1000]
        safehoo.py:338    _detect_pagination      页面文本[:5000] 找「共 N 条数据」
        safehoo.py:441    _extract_date           content[:1000] 找日期

    采样取小是**对的**（够用且快），拿它们当缺陷修反而是错。但「对」也得
    有个登记处——不然这五处和那四个源的老 `[:3000]` 在源码里长得一模一样，
    下次谁也认不出哪个该改。所以这里要的不是「一个都不许有」，而是
    **「有，且都在名单上，且名单上写着为什么」**：多一处报错，少一处也报错
    （少了说明代码动过，名单该更新了）。
    """
    actual = Counter()
    for path in sorted(SOURCES.glob("*.py")):
        code = _CODE_CUT.sub("", path.read_text(encoding="utf-8"))
        for m in _INDEX_CUT.finditer(code):
            n = int(m.group(1))
            if 1000 <= n < PREVIEW_CHARS:
                actual[(path.name, n)] += 1

    assert actual == _KNOWN_SAMPLING_CUTS, (
        "字面截断与登记名单对不上。\n"
        f"  实际：{dict(sorted(actual.items()))}\n"
        f"  名单：{dict(sorted(_KNOWN_SAMPLING_CUTS.items()))}\n"
        "  多出来的是新写死的正文截断（该改走 base.PREVIEW_CHARS）；"
        "少了的说明名单过期（核对后更新本文件）"
    )


@pytest.mark.parametrize("源", ["hebei_em", "ln_em", "nmg_em", "henan_em", "gd_em"])
def test_五个源都从共享常量取长度(源):
    """回退成字面值的那一天，这条红。

    `gd_em` 用的是本地别名 `_PREVIEW_CHARS`，所以判据是「出现过
    `PREVIEW_CHARS]`」而不是「出现过 `[:PREVIEW_CHARS]`」。
    """
    code = _CODE_CUT.sub(
        "", (SOURCES / f"{源}.py").read_text(encoding="utf-8"))
    assert "PREVIEW_CHARS]" in code, (
        f"{源}.py 里找不到 `PREVIEW_CHARS]`——正文截断又写回字面值了"
    )


def test_gd_em的本地别名就是共享常量():
    """`gd_em` 用了 `_PREVIEW_CHARS` 这个别名（第二十二轮改的时候留下的）。

    别名本身没错——错的是它能悄悄背离。这里钉住它只是**指向**共享常量，
    不是另一个可以各改各的值。
    """
    from construction_agent.collector.sources import gd_em

    assert gd_em._PREVIEW_CHARS == PREVIEW_CHARS, (
        "gd_em._PREVIEW_CHARS 与 base.PREVIEW_CHARS 不一致——"
        "这就是第二十二轮到第三十四轮之间那四份副本的老路"
    )
