#!/usr/bin/env python3
"""§2.5.66 自检：配置项的「有名无实」四层 + 跨副本 config.py 分叉。

来路：§2.5.65 查的是**数据库列**「有名无实」（列在、语义在、数据不在）。
本节换到**代码侧**——同一个病在配置上的投影：字段/参数/分支**名字都在**，
但实际不生效。查着查着，第四层把主题带到了版本控制层：
`sync-from-worktree` 这个**分支名**承诺同步，实际落后 62 个提交。

七段：
  A. **TrsSiteConfig 的字段 ↔ 读取点**——4 个零读取点（`base_url` 有 12 个源
     填值、三个 `list_*` 无人填也无人读），其余 15 个逐个列出读取点。
  B. **registry kwargs ↔ 构造器签名**——24 源全查，被 `build()` 静默丢弃的
     参数 **0 个**（`beijing_em` 那处 2026-10-01 已修，且实测是唯一一例）。
  C. **两份 config.py 的字段集合对账**——`worktree` vs 主仓库。字段名集合相等
     （各 17），默认值差异（`DATASET_FILE`）单列提示。
  D. **修复的回归保护**——4 个字段存在 + `step1:484` / `step2:239` 两处是
     **裸属性访问**（不是 getattr，缺字段即 AttributeError）。
  E. **两处结构性事实**——`build()` 的参数过滤是**整体**的（不逐源判断）；
     `enable_classify`（4 个子类构造器参数）与 `enable_classify_v2`（基类 cfg
     字段）是**同名不同物**，前者被用、后者从无源设过值。
  F. 文档 ↔ 实测。
  G. `--drift-test`——往文档注入漂移，逐个断言退出码转 1。

口径：只读代码，零网络请求，不写任何文件。
"""
from __future__ import annotations

import ast
import dataclasses
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._common import REPO_ROOT, main_repo_root  # noqa: E402

DOC = REPO_ROOT / "docs" / "web" / "01_在线采集.md"
CFG_DIR = REPO_ROOT / "construction_agent"
SELF_CFG = CFG_DIR / "config.py"

FAILS: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    """记一条判据。**只往 FAILS 里记，不改变返回值**（§2.5.62 教训）。"""
    if cond:
        print(f"    ✅ {label}")
    else:
        print(f"    🔴 {label}" + (f"\n       {detail}" if detail else ""))
        FAILS.append(label)


def _load_sources():
    """动态 import 本仓库的 collector 源码。"""
    import importlib
    spec = importlib.import_module("construction_agent.collector.registry")
    return spec


# ────────────────────────── A 段：死字段 ──────────────────────────
#: `TrsSiteConfig` 的持有者变量名。**必须收窄到这几个**——裸判据
#: 「有没有 `.字段名`」在混合语料里会抓到**别的类的同名字段**：
#: 实测 `evaluation/experiment1/model_clients.py:40` 的 `self.config.base_url`
#: 是实验框架自己的 config 对象，与 `TrsSiteConfig` 无关，却让 `base_url`
#: 从"零读取点"变成了"有读取点"（与 §2.5.63「裸子串判据制造假阳性」同族）。
HOLDERS = frozenset({"cfg", "site_config", "site_cfg"})


def scan_readers(field: str) -> list[tuple[str, int, str]]:
    """AST 判据：接收者是 `cfg` / `site_config` 的 `.field` 属性访问。

    `self.cfg.base_url` → 接收者是 `Attribute(attr='cfg')` ⇒ 命中。
    `self.config.base_url` → 接收者是 `Attribute(attr='config')` ⇒ 不命中。
    """
    out = []
    for p in sorted(CFG_DIR.rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        src = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        lines = src.splitlines()
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Attribute) and n.attr == field):
                continue
            recv = n.value
            name = (recv.id if isinstance(recv, ast.Name)
                    else recv.attr if isinstance(recv, ast.Attribute) else None)
            if name in HOLDERS:
                out.append((str(p.relative_to(REPO_ROOT)), n.lineno,
                            lines[n.lineno - 1].strip()))
    return out


def part_a() -> dict:
    print("\n[A] TrsSiteConfig：字段 ↔ 读取点")
    from construction_agent.collector.sources.trs_cms_base import TrsSiteConfig
    fields = [f.name for f in dataclasses.fields(TrsSiteConfig)]
    readers = {f: scan_readers(f) for f in fields}
    dead = [f for f in fields if not readers[f]]

    ok(len(fields) == 23, f"TrsSiteConfig 共 23 个字段", f"实测 {len(fields)}")
    ok(sorted(dead) == sorted(["base_url", "list_item_selector",
                               "list_title_attr", "list_date_pattern"]),
       "零读取点字段恰好 4 个：base_url / list_item_selector / "
       "list_title_attr / list_date_pattern", f"实测 {sorted(dead)}")
    ok(len(fields) - len(dead) == 19, "活字段 19 个", f"实测 {len(fields)-len(dead)}")

    # base_url 被 12 个源填了值（填了但从不读 ⇒ 12 处赋值是空话）
    filled = 0
    for p in sorted((CFG_DIR / "collector" / "sources").glob("*.py")):
        src = p.read_text(encoding="utf-8")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "TrsSiteConfig"
                    and any(k.arg == "base_url" for k in node.keywords)):
                filled += 1
    ok(filled == 12, "base_url 被 12 个源填了值（全部为死赋值）", f"实测 {filled}")

    # 三个 list_* 全仓只出现 1 次 = 定义那一行
    for f in ["list_item_selector", "list_title_attr", "list_date_pattern"]:
        bare = len(re.findall(r"\b" + re.escape(f) + r"\b",
                              "\n".join(p.read_text(encoding="utf-8")
                                        for p in CFG_DIR.rglob("*.py")
                                        if "__pycache__" not in p.parts)))
        ok(bare == 1, f"{f} 全仓仅定义处 1 次（无人填、无人读）", f"实测 {bare}")

    return {"fields": fields, "dead": dead, "readers": readers}


# ────────────────────────── B 段：registry ↔ 构造器 ─────────────────────
def part_b() -> dict:
    print("\n[B] registry 传的 kwargs ↔ 各源构造器签名")
    import importlib
    import inspect
    R = _load_sources()
    dropped_all, accepted_pairs = [], []
    for key, spec in R.SOURCES.items():
        mod = importlib.import_module(
            f"construction_agent.collector.sources.{spec.module}")
        cls = getattr(mod, spec.cls)
        params = set(inspect.signature(cls.__init__).parameters)
        given = dict(spec.build_kwargs)
        dropped = sorted(set(given) - params)
        accepted = sorted(k for k in given if k in params)
        if dropped:
            dropped_all.append((key, dropped))
        if accepted:
            accepted_pairs.append((key, accepted))

    ok(len(R.SOURCES) == 24, "登记表共 24 个源", f"实测 {len(R.SOURCES)}")
    ok(not dropped_all,
       "**0 个**源的 kwargs 被 build() 静默丢弃",
       f"实测 {dropped_all}")
    ok(sorted(accepted_pairs) == sorted([
        ("hebei_em", ["enable_classify"]), ("henan_em", ["enable_classify"]),
        ("ln_em", ["enable_classify"]), ("nmg_em", ["enable_classify"]),
        ("mohurd", ["collect_mem"])]),
       "恰好 5 个源各被接受 1 个参数（4×enable_classify + 1×collect_mem）",
       f"实测 {sorted(accepted_pairs)}")
    return {"dropped": dropped_all, "accepted": accepted_pairs}


# ────────────────────────── C 段：两份 config.py ─────────────────────
def _settings_fields(p: Path) -> dict[str, str]:
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for st in node.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    out[st.target.id] = ast.get_source_segment(src, st.value) or ""
    return out


def part_c() -> dict:
    print("\n[C] 两份 config.py：字段集合对账（跨副本身份）")
    mine = _settings_fields(SELF_CFG)
    main_root = main_repo_root()
    other = main_root / "construction_agent" / "config.py"
    if main_root == REPO_ROOT or not other.exists():
        print("    ⚠️ 在主仓库内运行或找不到另一份，本段跳过（不是失败）")
        return {"skipped": True}

    theirs = _settings_fields(other)
    missing_here = sorted(set(theirs) - set(mine))
    missing_there = sorted(set(mine) - set(theirs))
    ok(not missing_here,
       "主仓库有、本副本缺的字段 **0 个**（2026-10-05 已补齐 4 个）",
       f"仍缺 {missing_here}")
    ok(not missing_there,
       "本副本有、主仓库缺的字段 0 个", f"实测 {missing_there}")
    ok(len(mine) == 17 and len(theirs) == 17,
       "两份字段数均 17", f"本 {len(mine)} / 主 {len(theirs)}")

    diffs = {k: (mine[k], theirs[k]) for k in set(mine) & set(theirs)
             if mine[k] != theirs[k]}
    print(f"    ⚠️ 默认值不同的字段 {len(diffs)} 个（**提示，不判错**）：")
    for k, (a, b) in sorted(diffs.items()):
        print(f"       {k}: 本={a}  主={b}")
    return {"diffs": diffs, "n_mine": len(mine), "n_theirs": len(theirs)}


# ────────────────────────── D 段：修复的回归保护 ─────────────────────
BARE_READERS = [
    ("construction_agent/extraction/step1_basic_extractor.py",
     "ANTHROPIC_BASE_URL"),
    ("construction_agent/extraction/step2_caics_encoder.py",
     "ANTHROPIC_BASE_URL"),
]
SAFE_READERS = [
    ("construction_agent/collector/llm_classifier.py", "ANTHROPIC_BASE_URL"),
    ("construction_agent/web/db.py", "DATABASE_URL"),
]


def _attr_reads(rel: str, field: str) -> tuple[int, int]:
    """返回 (裸属性访问数, getattr 形式数)。"""
    p = REPO_ROOT / rel
    src = p.read_text(encoding="utf-8")
    tree = ast.parse(src)
    bare = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == field
        and isinstance(n.value, ast.Name) and n.value.id == "settings")
    g = sum(
        1 for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "getattr" and len(n.args) >= 2
        and isinstance(n.args[1], ast.Constant) and n.args[1].value == field)
    return bare, g


def part_d() -> dict:
    print("\n[D] 修复的回归保护：字段存在 + 引用点是裸访问")
    from construction_agent.config import settings
    for f in ["ANTHROPIC_BASE_URL", "DATABASE_URL",
              "MINERU_API_TOKEN", "DATASET_NORMALIZED"]:
        ok(hasattr(settings, f), f"`settings.{f}` 存在")

    for rel, field in BARE_READERS:
        bare, g = _attr_reads(rel, field)
        ok(bare >= 1 and g == 0,
           f"{Path(rel).name} 用**裸访问**读 {field}（缺字段即 AttributeError）",
           f"裸 {bare} / getattr {g}")

    for rel, field in SAFE_READERS:
        bare, g = _attr_reads(rel, field)
        ok(g >= 1 and bare == 0,
           f"{Path(rel).name} 用 getattr 兜底读 {field}（同一个字段，两种写法）",
           f"裸 {bare} / getattr {g}")

    # 反面自证：字段被摘掉时，裸访问必须真的抛
    class _Fake:
        pass
    try:
        _Fake().ANTHROPIC_BASE_URL  # noqa: B018
        raised = False
    except AttributeError:
        raised = True
    ok(raised, "自证：属性缺失时裸访问确实抛 AttributeError")
    return {}


# ────────────────────────── E 段：两处结构事实 ─────────────────────
def part_e() -> dict:
    print("\n[E] 两处结构性事实")
    src = (CFG_DIR / "collector" / "registry.py").read_text(encoding="utf-8")
    # build() 的过滤是整体推导式，不是逐源 if/else
    ok("accepted = {k: v for k, v in kwargs.items() if k in sig.parameters}"
       in src.replace("\n", " ").replace("  ", " "),
       "build() 的参数过滤是**整体**推导式（不逐源判断 ⇒ 键名写错即整条失效）")

    # enable_classify（4 子类构造器参数） vs enable_classify_v2（基类 cfg 字段）
    n_param = len(re.findall(r"enable_classify\s*[:=]", "\n".join(
        p.read_text(encoding="utf-8")
        for p in (CFG_DIR / "collector" / "sources").glob("*.py"))))
    ok(n_param >= 4, "4 个子类构造器有 `enable_classify` 参数", f"实测 {n_param}")
    n_cfg = len(re.findall(r"enable_classify_v2\s*=", "\n".join(
        p.read_text(encoding="utf-8")
        for p in CFG_DIR.rglob("*.py") if "__pycache__" not in p.parts)))
    ok(n_cfg == 0,
       "`TrsSiteConfig.enable_classify_v2` **从无任何源设过值**（永远默认 True）",
       f"实测 {n_cfg} 处赋值")
    return {"n_param": n_param, "n_cfg": n_cfg}


# ────────────────────────── F 段：文档对账 ─────────────────────
def grab(pat: str, label: str, expect: str) -> None:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(pat, text)
    ok(bool(m) and m.group(1) == expect,
       label, f"文档={m.group(1) if m else '锚点未命中'} 期望={expect}")


def part_f(a, b, c, d, e) -> None:
    print("\n[F] 文档 §2.5.66 ↔ 实测")
    # ⚠️ 锚点里的 `**` 必须写成 `\*\*`——裸 `*` 是量词，会静默匹配错东西；
    #    且每条都必须有捕获组，否则 grab 里 `group(1)` 直接 IndexError。
    grab(r"零读取点的\*\*死字段 (\d+) 个\*\*", "死字段数 4", "4")
    grab(r"`base_url` 被 \*\*(\d+) 个源\*\*填了值", "base_url 填值源数 12", "12")
    grab(r"\*\*被静默丢弃的参数 (\d+) 个\*\*", "静默丢弃 0", "0")
    grab(r"`Settings` 字段数 \*\*13 → (\d+)\*\*", "字段数 17", "17")
    grab(r"领先 \*\*(\d+) 个提交\*\*", "领先提交数 62", "62")


# ────────────────────────── G 段：drift ─────────────────────
DRIFT = [
    (r"零读取点的\*\*死字段 4 个\*\*", r"零读取点的**死字段 5 个**"),
    (r"\*\*12 个源\*\*填了值", r"**13 个源**填了值"),
    (r"\*\*被静默丢弃的参数 0 个\*\*", r"**被静默丢弃的参数 1 个**"),
    (r"`Settings` 字段数 \*\*13 → 17\*\*", r"`Settings` 字段数 **13 → 18**"),
    (r"领先 \*\*62 个提交\*\*", r"领先 **63 个提交**"),
]


def drift_test() -> int:
    print("\n[G] 注入漂移：逐个断言退出码转 1")
    orig = DOC.read_text(encoding="utf-8")
    bad = 0
    try:
        for i, (old, new) in enumerate(DRIFT, 1):
            if not re.search(old, orig):
                print(f"    🔴 用例 {i} 锚点未命中（用例本身失效）：{old}")
                bad += 1
                continue
            DOC.write_text(re.sub(old, new, orig, count=1), encoding="utf-8")
            rc = _run_quiet()
            print(f"    {'✅' if rc == 1 else '🔴'} 用例 {i}：改「{old[:28]}…」"
                  f"→ 退出码 {rc}")
            if rc != 1:
                bad += 1
            DOC.write_text(orig, encoding="utf-8")
    finally:
        DOC.write_text(orig, encoding="utf-8")
    ok(bad == 0, f"drift 用例全部转红（{len(DRIFT)-bad}/{len(DRIFT)}）")
    return 0 if bad == 0 else 1


def _run_quiet() -> int:
    """子进程跑一次主检查，只要退出码。"""
    r = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--main-only"],
        capture_output=True, text=True, cwd=str(REPO_ROOT))
    return r.returncode


def run() -> int:
    print("=" * 74)
    print("§2.5.66 配置项「有名无实」——四层 + 跨副本 config.py 分叉")
    print("=" * 74)
    a = part_a()
    b = part_b()
    c = part_c()
    d = part_d()
    e = part_e()
    part_f(a, b, c, d, e)
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
