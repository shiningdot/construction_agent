#!/usr/bin/env python3
"""§2.5.67 自检：展示层例外白名单的可证伪性。

来路：§2.5.60–66 七节全在查**数据的上游**（正文溯源、OCR、转义、错误列、
133 列体检、配置项）。本节从**出口**倒着查：主库 `accidents` 的 48 列里，
有几列在 web 上根本渲染不出来。

查出的**不是数据缺陷**——两条"不可见"一条是假阳性、一条是有据裁决——而是
核验结构上的缺陷：`check_field_alignment.py` 的 `DETAIL_NOT_SHOWN` 是**白名单**，
值只是文字理由、脚本从不复核，等于永久豁免。

四段：
  A. **列数账**——`accidents` 48 列 / 详情页覆盖 46 / 例外 2（走 `detail.py` 与
     主库实读，不抄文档）。
  B. **两条例外的实测底数**——`raw_text` 100% × 2,623 × 去重 2,602；
     `work_task_enum` 1,538 条 / 15 个取值 / 703 条查不到名字（45.7%）。
  C. **判据当下成立**——`exemption_checks()` 两条都要 ✓；且 `EXEMPTION_RULES`
     覆盖 `DETAIL_NOT_SHOWN` 的每个键（元判据）。
  D. 文档 ↔ 实测。
  E. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：只读主库与源码，零网络请求，不写任何文件。
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._common import REPO_ROOT, main_repo_root  # noqa: E402

DOC = REPO_ROOT / "docs" / "web" / "01_在线采集.md"

FAILS: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**（§2.5.62 教训）。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


def _main_db() -> str:
    p = main_repo_root() / "data" / "caiems_v2.db"
    if not p.exists():
        raise SystemExit(f"主库不在：{p}")
    return str(p)


# ────────────────────────── A 段：列数账 ──────────────────────────
def part_a() -> dict:
    print("\n[A] 列数账：accidents ↔ 详情页覆盖")
    from construction_agent.web import detail

    covered = ({n for _, _, names in detail.CONTENT_TIERS for n in names}
               | set(detail.META_FIELDS))
    con = sqlite3.connect(f"file:{_main_db()}?mode=ro", uri=True)
    try:
        cols = [r[1] for r in con.execute("PRAGMA table_info(accidents)")]
    finally:
        con.close()

    exempt = dict(detail.__dict__.get("DETAIL_NOT_SHOWN", {}))
    # `DETAIL_NOT_SHOWN` 定义在核验脚本里（不在 detail.py）——这才是对的：
    # 展示层只负责"显示什么"，"为什么不显示"的解释属于核验侧。
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_field_alignment as C  # noqa: E402

    exempt = dict(C.DETAIL_NOT_SHOWN)
    gaps = [c for c in cols if c not in covered and c not in exempt]

    ok(len(cols) == 48, "`accidents` 48 列", f"实测 {len(cols)}")
    ok(len(covered) == 46, "详情页四档 + 元数据区覆盖 46 列", f"实测 {len(covered)}")
    ok(sorted(exempt) == ["raw_text", "work_task_enum"],
       "例外恰好 2 个：raw_text / work_task_enum", f"实测 {sorted(exempt)}")
    ok(len(gaps) == 0, "`accidents` 没有「哪个档都不在、也不在例外里」的列",
       f"实测 {gaps}")
    return {"cols": cols, "covered": covered, "exempt": exempt}


# ────────────────────────── B 段：两条例外的实测底数 ─────────────
def part_b() -> dict:
    print("\n[B] 两条例外的实测底数")
    from construction_agent.web import accidents_query

    con = sqlite3.connect(f"file:{_main_db()}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM accidents").fetchone()[0]
        rv = con.execute(
            "SELECT COUNT(*) FROM accidents WHERE raw_text IS NOT NULL "
            "AND TRIM(raw_text) != ''").fetchone()[0]
        rd = con.execute(
            "SELECT COUNT(DISTINCT raw_text) FROM accidents").fetchone()[0]
        wv = con.execute(
            "SELECT COUNT(*) FROM accidents WHERE work_task_enum IS NOT NULL "
            "AND TRIM(work_task_enum) != ''").fetchone()[0]
        wd = con.execute(
            "SELECT COUNT(DISTINCT work_task_enum) FROM accidents "
            "WHERE TRIM(COALESCE(work_task_enum,'')) != ''").fetchone()[0]
        rows = con.execute(
            "SELECT work_task_enum, COUNT(*) FROM accidents "
            "WHERE TRIM(COALESCE(work_task_enum,'')) != '' "
            "GROUP BY work_task_enum").fetchall()
    finally:
        con.close()

    names = accidents_query.enum_names("work_task", _main_db())
    unknown = sum(c for code, c in rows if code not in names)
    pct = 100.0 * unknown / wv

    ok(n == 2623, "主库 2,623 条", f"实测 {n}")
    ok(rv == 2623 and 100.0 * rv / n == 100.0,
       "`raw_text` 有值 2,623 = 100.0%", f"实测 {rv} ({100.0*rv/n:.1f}%)")
    ok(rd == 2602, "`raw_text` 去重 2,602", f"实测 {rd}")
    ok(wv == 1538, "`work_task_enum` 有值 1,538（58.6%）", f"实测 {wv}")
    ok(wd == 15, "`work_task_enum` 去重 15 个取值", f"实测 {wd}")
    ok(unknown == 703, "其中 703 条在枚举表里查不到名字", f"实测 {unknown}")
    ok(abs(pct - 45.7) < 0.05, "703/1,538 = 45.7%", f"实测 {pct:.1f}%")
    return {"unknown": unknown, "total": wv, "pct": pct}


# ────────────────────────── C 段：判据当下成立 ─────────────────────
def part_c(front_ok: bool = True) -> dict:
    print("\n[C] 例外判据当下成立 + 元判据")
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import check_field_alignment as C  # noqa: E402

    front = C.frontend_attrs()
    got = C.exemption_checks(_main_db(), front)
    for col, good, detail in got:
        ok(good, f"`{col}` 的豁免理由仍成立 —— {detail}")

    # 元判据：每条例外都必须配到判据（防"加例外忘了配判据"）
    missing = sorted(set(C.DETAIL_NOT_SHOWN) - set(C.EXEMPTION_RULES))
    ok(not missing,
       "`EXEMPTION_RULES` 覆盖 `DETAIL_NOT_SHOWN` 的每个键", f"缺 {missing}")

    # 判据**有能力报红**才可信——真跑一次 self-test
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "check_field_alignment.py"),
         "--self-test"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    ok(r.returncode == 0 and "6/6" in r.stdout,
       "`check_field_alignment.py --self-test` 6/6 通过",
       f"退出码 {r.returncode}；{r.stdout.strip().splitlines()[-1:]}")
    return {"exemptions": got}


# ────────────────────────── D 段：文档 ↔ 实测 ─────────────────────
def grab(pat: str, label: str, expect: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(pat, text)
    ok(bool(m) and m.group(1) == expect,
       label, f"文档={m.group(1) if m else '锚点未命中'} 期望={expect}")


def part_d(a, b, c) -> None:
    print("\n[D] 文档 §2.5.67 ↔ 实测")
    # ⚠️ 锚点里的 `**` / `|` 必须转义；每条都要有捕获组，否则 group(1) 会 IndexError
    grab(r"`accidents` 的 (\d+) 列，有几列在 web", "正文：48 列", "48")
    # ⚠️ 千分位：文档里的数字**带逗号**（`2,602`），`(\d+)` 会在逗号前停住、
    #    随后要求 ` | ` 而匹配失败——表现为"锚点未命中"，与"判据报警"在输出上
    #    都是红，分不清。（§2.5.64 立过这条，本次是第三次复发。）
    grab(r"\| `raw_text` \| \*\*100\.0%\*\*（2,623/2,623） \| ([\d,]+) \|",
         "表：raw_text 去重 2,602", "2,602")
    grab(r"\| `work_task_enum` \| 58\.6%（1,538） \| ([\d,]+) \|",
         "表：work_task_enum 去重 15", "15")
    grab(r"实测 \*\*1,538 条中 ([\d,]+) 条（45\.7%）\*\*", "正文：703 条", "703")
    grab(r"两列同时有值时 \*\*([\d,]+) 条互相矛盾\*\*", "正文：1,392 条矛盾", "1,392")
    # ⚠️ `**` 的位置：文档写的是 `**`--self-test` 6/6**`（`**` 在最前），
    #    写成 `` `--self-test` \*\*(\d)/\d\*\* `` 会匹配不上。
    grab(r"\*\*`--self-test` (\d)/\d\*\*", "正文：self-test 6/6", "6")
    grab(r"(\d+) 个空数组端点仍是盲区", "正文：12 个空数组端点", "12")


# ────────────────────────── E 段：drift ─────────────────────
DRIFT = [
    (r"`accidents` 的 48 列，有几列在 web", r"`accidents` 的 49 列，有几列在 web"),
    (r"\| `raw_text` \| \*\*100\.0%\*\*（2,623/2,623） \| 2,602 \|",
     r"| `raw_text` | **100.0%**（2,623/2,623） | 2,603 |"),
    (r"实测 \*\*1,538 条中 703 条（45\.7%）\*\*", r"实测 **1,538 条中 704 条（45.7%）**"),
    (r"\*\*`--self-test` 6/6\*\*", r"**`--self-test` 5/6**"),
]


def drift_test() -> int:
    print("\n[E] 注入漂移：逐个断言退出码转 1")
    orig = DOC.read_text(encoding="utf-8")
    bad = 0
    try:
        for i, (old, new) in enumerate(DRIFT, 1):
            if not re.search(old, orig):
                print(f"    🔴 用例 {i} 锚点未命中（用例本身失效）：{old[:34]}")
                bad += 1
                continue
            DOC.write_text(re.sub(old, new, orig, count=1), encoding="utf-8")
            rc = _run_quiet()
            print(f"    {'✅' if rc == 1 else '🔴'} 用例 {i}：改「{old[:26]}…」"
                  f"→ 退出码 {rc}")
            if rc != 1:
                bad += 1
            DOC.write_text(orig, encoding="utf-8")
    finally:
        DOC.write_text(orig, encoding="utf-8")
    ok(bad == 0, f"drift 用例全部转红（{len(DRIFT)-bad}/{len(DRIFT)}）")
    return 0 if bad == 0 else 1


def _run_quiet() -> int:
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--main-only"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    return r.returncode


def run() -> int:
    print("=" * 74)
    print("§2.5.67 展示层例外白名单的可证伪性")
    print("=" * 74)
    a = part_a()
    b = part_b()
    c = part_c()
    part_d(a, b, c)
    print("\n" + "=" * 74)
    if FAILS:
        print(f"❌ {len(FAILS)} 项未通过：")
        for f in FAILS:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


def main() -> int:
    if "--drift-test" in sys.argv:
        return drift_test()
    return run()


if __name__ == "__main__":
    if "--main-only" in sys.argv:
        sys.exit(run())
    sys.exit(main())
