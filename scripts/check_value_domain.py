#!/usr/bin/env python3
"""取值域核验：提示词声明的 enum ↔ 主库实际落库值（§2.5.74）。

## 为什么有这个脚本

§2.5.65 在扫主库 133 列的填充率时，末尾留了一句「建议登记：查询层统一『缺失』
判定函数」。顺着这条线查下去，发现真正没人核过的是**另一件事**：`step1_basic_extractor`
的 tool schema 里给每一列写了一串 enum（"这一列只能取这些值"），而主库里的实际取值
**从来没有和那份声明对过账**。`scripts/check_enum_alignment.py` 查的是「声明 ↔ 前端
标签 ↔ 后端写入字面量」，三方都不含**库里已落地的值**。

第一次对账就查出一条真缺陷：`report_type` 声明 `教材/案例`，而离线电子书那 895 条
（全库 34.1%）落的是 `教材案例`——同一语义在库里分裂成两个值。

## 五段判据

- **A 声明**：从 `step1_basic_extractor.py` 的 tool schema 抽每列的 enum。
  用**括号配平**扫描而不是正则匹配缩进（正则一遇到换行/缩进变化就静默返回空集，
  而空集会让 C 段"全部合规"，属 §2.5.73 立的「判据根本没跑却报绿」）。抽到的
  列数须等于期望值，否则判红。
- **B 实际**：从主库抽每列实际取值。多值列（`equipment_involved` / `material_involved`
  / `worker_type`，顿号分隔）先拆再取值。任一列取到空集判红——库读不到 ≠ 没有越界。
- **C 差集**：越界值（实际有、声明无）与未用值（声明有、实际无）。越界值须逐条落在
  `KNOWN_OUT_OF_DOMAIN` 登记表里，未登记的判红；登记表里**过期**的（已不在越界集
  合里）同样判红——否则登记表会变成永久豁免。未用值只提示不判红（枚举预留类是正常的）。
- **D 哨兵台账**：缺失在本库有四种表示——`NULL`、空串 `''`、**字符串哨兵**、**数值
  哨兵 `0`**。后两种是"写在值里的缺失"，`IS NULL OR = ''` 抓不到，是本段的对象。
  台账分两类：`E1` 在 enum 里的哨兵（由 M2/M3 两条元判据自动发现，**集合须相等**）、
  `E2` 由 description 约定的裸值（不在 enum 里，判据是"出处片段能在源码命中"）。
- **E 文档对账**：核 01 号文档 §2.5.74 标题行在位，且正文里的关键数字与实测一致。

## 判据边界（明写）

- C 段只能发现**声明了 enum 的列**。没有 enum 声明的列（`weather`、`deaths`、
  `weather` 之外的裸值列）在本脚本里完全不可见——所以它们只能靠 E2 台账**人工登记**，
  漏登记抓不到。这是本脚本最大的一处缺口。
- M3（代码型兜底哨兵）靠 description 的列表行 `- EQP13其他: ...` 识别，**只对把
  兜底类写在列表行里的枚举有效**。若某枚举把"其他"类写成散文而非列表行，M3 不命中。
- 台账里的"展示层处理"字段只打印不判红：某哨兵未被归一化**可能是设计**（见 §2.5.74
  正文「`不适用` 与 `未知` 的差异化处理是有意的」），脚本没有立场把它判成缺陷。
- 本脚本核的是"取值**在不在**声明里"，**核不到取值对不对**（把 895 条全改成
  `教材案例` 之后，语义是否恰当是另一回事）。

用法：
    .venv/bin/python3 scripts/check_value_domain.py            # 主检
    .venv/bin/python3 scripts/check_value_domain.py --drift-test   # 注入式自证
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import REPO_ROOT, main_db_path  # noqa: E402

SRC = REPO_ROOT / "construction_agent" / "extraction" / "step1_basic_extractor.py"
DOC = REPO_ROOT / "docs" / "web" / "01_在线采集.md"
DETAIL = REPO_ROOT / "construction_agent" / "web" / "detail.py"

#: A 段期望抽到的列数。写死是为了让"正则失效抽到 0 列"变成**红**而不是"全绿"。
EXPECT_COLS = 12

#: 多值列：这些列存的是顿号分隔的代码串（见 step1 的 description），
#: 必须"先拆再取值"，否则 `WKR04、WKR08` 整体会被当成一个越界值。
MULTI = {"equipment_involved", "material_involved", "worker_type"}

#: C 段登记表：已知的越界值及其来源。**判据是双向的**——越界值须全在表里，
#: 表里的也须全在越界集合里（过期登记判红）。
KNOWN_OUT_OF_DOMAIN = {
    ("accident_type", ""): "空串：§2.5.65 已查明的纯空串列",
    ("severity", ""): "空串：§2.5.65 已查明的纯空串列",
    ("construction_phase", "其他/未知"):
        "串台：该值是 project_type 的声明值，1 条（模型串档）",
    ("project_type", "维保/改造"):
        "串台：该值是 construction_phase 的声明值，1 条（模型串档）",
}

#: 哨兵候选词（M2 用）：声明的 enum 成员里含这些字样的，语义上就是"缺失"。
SENTINEL_WORDS = ("未提及", "未知", "不适用", "其他", "不详", "不明", "暂无", "待定")

#: E1 台账：**在 enum 里**的哨兵（那一类目语义上就是"缺失/无法归类"）。
#: 由元判据 M2/M3 从声明里自动发现，此处逐条补语义。判据是**集合相等**：
#: 台账里有的、声明里必须有；声明里发现的、台账里必须登记。
E1_SENTINELS = {
    ("accident_type", "其他"): "事故类型无法归入 11 个具体类",
    ("project_type", "其他/未知"): "工程类型未提及或无法归类",
    ("construction_phase", "未知"): "施工阶段未提及",
    ("contractor_type", "未提及"): "原文没写施工单位性质",
    ("report_type", "其他"): "体裁不属于前四类",
    ("collapse_subtype", "不适用"): "非坍塌类事故（问了但不适用）",
    ("collapse_subtype", "其他坍塌"): "是坍塌，但子类型无法归入 7 个具体类",
    ("building_structure", "BLD12"): "其他结构：竹/土坯/冰/简易结构等罕见类型",
    ("work_task", "WT99"): "跨类混合/非建筑施工/信息严重不足",
    ("equipment_involved", "EQP13"): "罕见/特殊设备或无法归类",
    ("material_involved", "MAT07"): "其他材料及杂物",
    ("worker_type", "WKR13"): "其他专业人员（低频工种/管理技术岗）",
}

#: E2 台账：**不在 enum 里、由 description 约定**的裸哨兵值。
#: (列, 值, 语义, 展示层处理, 出处片段——须能在 step1 源码里逐字命中)
E2_SENTINELS = [
    ("weather", "未提及", "「问过但原文没写天气」", "未归一，详情页原样显示『未提及』",
     "天气状况。未提及填'未提及'"),
    ("deaths", 0, "「原文没写死亡人数」——与『真的 0 人死亡』不可区分",
     "detail.py 把 0 当有值（`deaths=0` 会显示），overview.py 用 `deaths > 0` 另计",
     "死亡人数，未提及填0"),
    ("injuries", 0, "同上", "未归一", "受伤人数，未提及填0"),
    ("trapped_count", 0, "同上", "未归一",
     "被困人数（区别于已确认的死亡/受伤）。未提及填0"),
]

FAILS: list[tuple[str, str]] = []


def fail(tag: str, msg: str) -> None:
    FAILS.append((tag, msg))
    print(f"❌ {tag}: {msg}")


# ────────────────────────── A 段：声明 ──────────────────────────

def _blocks(src: str) -> dict[str, str]:
    """扫出 schema 里形如 `"name": { ... }` 的块，**按括号配平**取，不看缩进。

    正则按缩进匹配的写法在本项目已出过事（`check_tables_v2.py` 的 `len(row)<6`
    把整张表滤掉）：写法一改就静默返回空集。配平扫描至少不会因为多缩进两格而失效。

    **只收叶子块**：`input_schema` / `properties` 这类外层容器也写成 `"x": {`，
    它们包着整个字段表，块内嵌着第一个字段的 `enum`——不收叶子的话，第一轮实跑
    就多抽了 2 个"列"（`input_schema`/`properties`），并把 `accident_type` 的
    枚举当成它们的取值域。
    """
    out: dict[str, str] = {}
    for m in re.finditer(r'"([A-Za-z_]\w*)"\s*:\s*\{', src):
        i = src.index("{", m.start())
        depth, j = 0, i
        while j < len(src):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        if j >= len(src):
            continue
        blk = src[i:j + 1]
        if re.search(r'"[A-Za-z_]\w*"\s*:\s*\{', blk[1:]):
            continue                     # 非叶子：容器块，跳过
        out[m.group(1)] = blk
    return out


def declared_enums() -> dict[str, list[str]]:
    """每列的声明 enum。只收有 enum 的块。"""
    out: dict[str, list[str]] = {}
    for name, blk in _blocks(SRC.read_text(encoding="utf-8")).items():
        m = re.search(r'"enum"\s*:\s*(\[[^\[\]]*\])', blk, flags=re.S)
        if not m:
            continue
        try:
            vals = ast.literal_eval(re.sub(r"\s+", " ", m.group(1)))
        except (ValueError, SyntaxError):
            continue
        if isinstance(vals, list):
            out[name] = [str(v) for v in vals]
    return out


def part_a() -> dict[str, list[str]]:
    print("\n=== A 段：声明（step1_basic_extractor.py 的 tool schema）===")
    decl = declared_enums()
    for k, v in decl.items():
        print(f"  {k:<22} {len(v):>2} 类")
    if len(decl) != EXPECT_COLS:
        fail("A", f"抽到 {len(decl)} 列，期望 {EXPECT_COLS} 列——"
                  f"schema 写法变了或源码读不到（抽到的：{sorted(decl)}）")
    else:
        print(f"  ✅ {len(decl)} 列，与期望一致")
    return decl


# ────────────────────────── B 段：实际 ──────────────────────────

def actual_values(con: sqlite3.Connection, col: str) -> Counter:
    rows = [r[0] for r in con.execute(
        f'SELECT "{col}" FROM accidents WHERE "{col}" IS NOT NULL')]
    cnt: Counter = Counter()
    for r in rows:
        if col in MULTI:
            for part in re.split(r"[、,，]", str(r)):
                if part.strip():
                    cnt[part.strip()] += 1
        else:
            cnt[str(r)] += 1
    return cnt


def part_b(con: sqlite3.Connection, decl: dict[str, list[str]]) -> dict[str, Counter]:
    print("\n=== B 段：实际取值（主库 accidents 表）===")
    print(f"  库：{main_db_path()}")
    act: dict[str, Counter] = {}
    for col in decl:
        c = actual_values(con, col)
        act[col] = c
        n_dist = len(c)
        print(f"  {col:<22} 去重 {n_dist:>3} / 声明 {len(decl[col]):>2}"
              f"{'  （多值列，已按顿号拆分）' if col in MULTI else ''}")
        if not c:
            # 库读不到 ≠ 没有越界。降级成空集合会让 C 段报"全部合规"。
            fail("B", f"{col} 在主库取到 0 个值——库路径错误或列名变了，"
                      f"此时 C 段的对账无意义")
    return act


# ────────────────────────── C 段：差集 ──────────────────────────

def _norm(s: str) -> str:
    """写法归一：只用于识别「同一语义两种写法」，不用于放行。

    `教材/案例` 与 `教材案例` 归一后相同。**归一化只用来分类、不用来豁免**——
    分类结果仍进 C 段的对账，只是能给出人话解释。
    """
    return re.sub(r"[\s/·\-—－_、,，()（）]", "", s)


def part_c(decl: dict[str, list[str]], act: dict[str, Counter]) -> None:
    print("\n=== C 段：声明 ↔ 实际 差集 ===")
    all_decl = {v for vals in decl.values() for v in vals}
    oob: list[tuple[str, str, int]] = []
    unused: list[tuple[str, list[str]]] = []
    for col, dvals in decl.items():
        d, a = set(dvals), act[col]
        for v in sorted(set(a) - d, key=lambda x: -a[x]):
            oob.append((col, v, a[v]))
        u = sorted(d - set(a))
        if u:
            unused.append((col, u))

    print("  -- 越界值（实际有、声明无）--")
    if not oob:
        print("    （无）")
    for col, v, n in oob:
        kind = "空串"
        if v == "":
            pass
        elif v in all_decl:
            who = [c for c, vals in decl.items() if v in vals]
            kind = f"★串台（该值是 {who} 的声明值）"
        elif any(_norm(v) == _norm(x) for x in decl[col]):
            near = [x for x in decl[col] if _norm(x) == _norm(v)]
            kind = f"写法差异（归一后同 {near}）"
        else:
            kind = "未归类"
        mark = "✅" if (col, v) in KNOWN_OUT_OF_DOMAIN else "❌"
        print(f"    {mark} {col} = {v!r} × {n}   [{kind}]")
        if (col, v) in KNOWN_OUT_OF_DOMAIN:
            print(f"        登记：{KNOWN_OUT_OF_DOMAIN[(col, v)]}")

    unregistered = [(c, v) for c, v, _ in oob if (c, v) not in KNOWN_OUT_OF_DOMAIN]
    if unregistered:
        fail("C", f"{len(unregistered)} 个越界值未登记：{unregistered}")

    seen = {(c, v) for c, v, _ in oob}
    stale = [k for k in KNOWN_OUT_OF_DOMAIN if k not in seen]
    if stale:
        fail("C", f"登记表里 {len(stale)} 条已过期（库里已不存在该越界值）：{stale}")

    print("  -- 未用值（声明有、实际无，不判红：枚举预留类属正常）--")
    if not unused:
        print("    （无）")
    for col, u in unused:
        print(f"    · {col}: {u}")

    if not unregistered and not stale:
        print("  ✅ 越界值全部有登记，无过期登记")


# ────────────────────────── D 段：哨兵台账 ──────────────────────────

def _enum_sentinels(decl: dict[str, list[str]], src: str) -> set[tuple[str, str]]:
    """元判据 M2 + M3：从**声明**里发现哨兵，反过来查台账有没有漏登记。

    M2 值命中哨兵词（`accident_type` 的 `其他` 这类）。
    M3 代码型兜底：值本身是 `EQP13` 看不出语义，但 description 的列表行
       `- EQP13其他: ...` 写着——只对写成列表行的枚举有效。
    """
    found: set[tuple[str, str]] = set()
    blocks = _blocks(src)
    for col, vals in decl.items():
        for v in vals:
            if any(w in v for w in SENTINEL_WORDS):
                found.add((col, v))
        desc = blocks.get(col, "")
        for line in desc.splitlines():
            m = re.match(r"\s*-\s*([A-Z]+\d+)\s*(.+)", line)
            if m and m.group(1) in vals and any(w in m.group(2) for w in SENTINEL_WORDS):
                found.add((col, m.group(1)))
    return found


def part_d(con: sqlite3.Connection, decl: dict[str, list[str]]) -> None:
    print("\n=== D 段：哨兵台账（写在值里的缺失）===")
    src = SRC.read_text(encoding="utf-8")
    dtext = DETAIL.read_text(encoding="utf-8") if DETAIL.exists() else ""
    found = _enum_sentinels(decl, src)

    print(f"  -- E1：enum 里的哨兵（M2/M3 自动发现 {len(found)} 个，台账登记 "
          f"{len(E1_SENTINELS)} 个）--")
    for col, v in sorted(set(E1_SENTINELS) | found):
        if (col, v) not in E1_SENTINELS:
            fail("D-META1", f"声明里发现哨兵 {col}={v!r}，但 E1 台账未登记——"
                            f"新加的哨兵类目必须补登语义")
            continue
        if (col, v) not in found:
            fail("D-META1", f"E1 台账登记了 {col}={v!r}，但声明的 enum 里没有这个"
                            f"值——登记已过期或列名改了")
            continue
        n = actual_values(con, col).get(v, 0) if col in MULTI else con.execute(
            f'SELECT COUNT(*) FROM accidents WHERE "{col}" = ?', (v,)).fetchone()[0]
        disp = "已归一（详情页按缺失处理）" if (dtext and f'"{v}"' in dtext) \
            else "未归一（脚本不判红：可能是设计）"
        # 条数只打印不判红——"声明了但从未用过"（如 report_type 的 `其他`）是
        # 合法的枚举预留，不是缺陷。值被改写成别的形态会由 C 段的越界抓。
        tagged = f" × {n:>5}" if n else " ×     0（声明预留，未用过）"
        print(f"    · {col} = {v!r}{tagged}")
        print(f"        语义：{E1_SENTINELS[(col, v)]}")
        print(f"        展示层：{disp}")
    if set(E1_SENTINELS) == found:
        print("  ✅ E1 台账与自动发现集合相等")

    print(f"  -- E2：description 约定的裸值（不在 enum 里，{len(E2_SENTINELS)} 条手工登记）--")
    for col, v, meaning, disp, evidence in E2_SENTINELS:
        lit = con.execute(
            f'SELECT COUNT(*) FROM accidents WHERE "{col}" IS NULL').fetchone()[0]
        cnt = con.execute(
            f'SELECT COUNT(*) FROM accidents WHERE "{col}" = ?', (v,)).fetchone()[0]
        print(f"    · {col} = {v!r} × {cnt:>5}（该列 NULL {lit} 条）  语义：{meaning}")
        print(f"        出处：{evidence!r}")
        print(f"        展示层：{disp}")
        if evidence not in src:
            fail("D-META2", f"{col}={v!r} 的出处片段在 step1 源码里找不到："
                            f"{evidence!r}——登记依据是编的或源码改了")
        if cnt == 0:
            fail("D-META2", f"{col}={v!r} 在库里 0 条——登记已过期，或该列的"
                            f"缺失表示改成了别的形态")


# ────────────────────────── E 段：文档对账 ──────────────────────────

#: 文档里必须出现的关键数字（由主检实测后填定）。改文档忘改脚本会被这条抓住。
DOC_NUMBERS = ["895", "1,320", "399", "201"]


def _doc_path() -> Path:
    """文档路径。`CVD_DOC` 开关**只给 `--drift-test` 用**（指向副本，避免改真文档）。"""
    raw = os.environ.get("CVD_DOC")
    return Path(raw).resolve() if raw else DOC


def part_e() -> None:
    print("\n=== E 段：文档对账 ===")
    doc = _doc_path()
    if not doc.exists():
        fail("E", f"文档不存在：{doc}")
        return
    text = doc.read_text(encoding="utf-8")
    if not re.search(r"^### 2\.5\.74 ", text, flags=re.M):
        fail("E", "01 号文档里找不到 `### 2.5.74 ` 标题行（节的正文不自称 §，"
                  "故核标题行而不是 `§2.5.74`）")
    else:
        print("  ✅ 标题 `### 2.5.74 ` 在位")
    missing = [n for n in DOC_NUMBERS if n not in text]
    if missing:
        fail("E", f"文档缺这些数字：{missing}")
    else:
        print(f"  ✅ {len(DOC_NUMBERS)} 个关键数字齐备：{DOC_NUMBERS}")


# ────────────────────────── 主流程 ──────────────────────────

def main(drift: bool = False) -> int:
    if drift:
        return drift_test()
    print("=" * 72)
    print("取值域核验：声明（step1 schema）↔ 实际（主库 accidents）")
    print("=" * 72)
    decl = part_a()
    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    act = part_b(con, decl)
    part_c(decl, act)
    part_d(con, decl)
    con.close()
    part_e()

    print("\n" + "=" * 72)
    if FAILS:
        print(f"结论：❌ {len(FAILS)} 处待处置")
        for tag, msg in FAILS:
            print(f"  ❌ {tag}: {msg}")
    else:
        print("结论：✅ 全部通过")
    print("MARKS " + json.dumps(
        {"fails": sorted({t for t, _ in FAILS})}, ensure_ascii=False))
    return 1 if FAILS else 0


# ────────────────────────── 注入式自证 ──────────────────────────

def _run(env_over: dict[str, str]) -> tuple[int, list[str]]:
    """子进程跑主检，返回 (退出码, 红的段落集合)。"""
    env = dict(os.environ)
    env.update(env_over)
    r = subprocess.run([sys.executable, str(Path(__file__).resolve())],
                       capture_output=True, text=True, env=env, timeout=300)
    m = re.search(r"^MARKS (.+)$", r.stdout, flags=re.M)
    fails = json.loads(m.group(1))["fails"] if m else ["<无 MARKS 行>"]
    return r.returncode, fails


def drift_test() -> int:
    """往每个注入面各打一枪，断言**红的恰好是哪几条**（§2.5.68/§2.5.73 立的）。

    三个注入面：step1 源码（就地改、try/finally 恢复）、主库（**副本**，绝不碰
    真库，用 `con.backup()` 生成以避开 WAL 未 checkpoint 的风险）、01 号文档
    （副本，用 `CVD_DOC` 开关指向）。
    """
    print("=" * 72)
    print("注入式自证：每个注入面打一枪，断言红的恰好是哪几条")
    print("=" * 72)
    tmp = Path(tempfile.mkdtemp(prefix="cvd_drift_"))
    cases: list[tuple[str, dict[str, str], set[str]]] = []
    orig_src = SRC.read_text(encoding="utf-8")
    orig_doc = DOC.read_text(encoding="utf-8")

    # ① 基准：不改任何东西，须全绿
    code, fails = _run({})
    cases.append(("基准（未注入）", {"exit": code, "fails": fails},
                  {"exit": 0, "fails": []}))

    # ② 归一化函数自证：`教材/案例` 与 `教材案例` 必须归一为同一串。
    #    这条不跑子进程——它验的是 C 段"写法差异"分类所依赖的那个函数本身，
    #    函数错了，C 段会把真缺陷说成"写法差异"（或者反过来）。
    norm_ok = _norm("教材/案例") == _norm("教材案例") == "教材案例"
    cases.append(("归一化函数（教材/案例 ≡ 教材案例）",
                  {"exit": 0 if norm_ok else 1, "fails": [] if norm_ok else ["_norm"]},
                  {"exit": 0, "fails": []}))

    # ③ 库副本：把一条 report_type 改成未登记的值
    db_copy = tmp / "caiems_copy.db"
    src_con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    dst_con = sqlite3.connect(str(db_copy))
    src_con.backup(dst_con)          # backup API：WAL 未 checkpoint 也安全
    cur = dst_con.execute(
        "UPDATE accidents SET report_type='外星报道' WHERE report_type='新闻报道' "
        "AND rowid=(SELECT MIN(rowid) FROM accidents WHERE report_type='新闻报道')")
    assert cur.rowcount == 1, f"库副本里没有 `新闻报道` 可改（rowcount={cur.rowcount}）"
    dst_con.commit()
    src_con.close()
    dst_con.close()
    code, fails = _run({"CAIEMS_MAIN_DB": str(db_copy)})
    cases.append(("库副本：一条 report_type 改成 `外星报道`",
                  {"exit": code, "fails": fails},
                  {"exit": 1, "fails": ["C"]}))

    # ④ step1 源码：从 report_type 的 enum 里删掉一个**实际在用**的值
    try:
        assert '"新闻报道"' in orig_src
        SRC.write_text(orig_src.replace('"新闻报道", ', "", 1), encoding="utf-8")
        code, fails = _run({})
        cases.append(("step1 源码：enum 里删掉 `新闻报道`",
                      {"exit": code, "fails": fails},
                      {"exit": 1, "fails": ["C"]}))
    finally:
        SRC.write_text(orig_src, encoding="utf-8")

    # ⑤ step1 源码：把 `未知` 从 construction_phase 的 enum 里删掉。
    #    这一枪**两条判据同时响**（首版期望只写 C，实测报 7/8——不是脚本错，
    #    是期望错）：C 段抓到 399 条 `未知` 成了越界值；D-META1 抓到 E1 台账
    #    还登记着这个声明里已经没有的哨兵。**双向判据的两半各响一次**，故期望
    #    是两个标签——这也正是"红的恰好是哪几条"的价值：只看"退出码非 0"的话，
    #    这条用例会掩盖掉其中一半。
    try:
        SRC.write_text(orig_src.replace('"维保/改造", "未知"', '"维保/改造"', 1),
                       encoding="utf-8")
        code, fails = _run({})
        cases.append(("step1 源码：enum 里删掉哨兵 `未知`（C + D-META1 各响一次）",
                      {"exit": code, "fails": fails},
                      {"exit": 1, "fails": ["C", "D-META1"]}))
    finally:
        SRC.write_text(orig_src, encoding="utf-8")

    # ⑥ step1 源码：把 E2 的出处片段改掉（台账依据须逐字命中源码）
    try:
        SRC.write_text(orig_src.replace("未提及填'未提及'", "未提及填＜未提及＞", 1),
                       encoding="utf-8")
        code, fails = _run({})
        cases.append(("step1 源码：改掉 weather 的出处片段",
                      {"exit": code, "fails": fails},
                      {"exit": 1, "fails": ["D-META2"]}))
    finally:
        SRC.write_text(orig_src, encoding="utf-8")

    # ⑦ 库副本：抹掉 weather 的全部哨兵（台账项在库里归零）
    #    先复位 ③ 留下的 `外星报道`，否则本用例会同时挂 C 段，归因不唯一。
    dst_con = sqlite3.connect(str(db_copy))
    dst_con.execute("UPDATE accidents SET report_type='新闻报道' WHERE report_type='外星报道'")
    dst_con.execute("UPDATE accidents SET weather=NULL WHERE weather='未提及'")
    dst_con.commit()
    dst_con.close()
    code, fails = _run({"CAIEMS_MAIN_DB": str(db_copy)})
    cases.append(("库副本：抹掉 weather 全部 `未提及`",
                  {"exit": code, "fails": fails},
                  {"exit": 1, "fails": ["D-META2"]}))

    # ⑧ 文档副本：删标题行
    doc_copy = tmp / "doc_copy.md"
    doc_copy.write_text(orig_doc.replace("### 2.5.74 ", "### 2.5.74X ", 1),
                        encoding="utf-8")
    code, fails = _run({"CVD_DOC": str(doc_copy)})
    cases.append(("文档副本：标题 `### 2.5.74 ` 改名",
                  {"exit": code, "fails": fails},
                  {"exit": 1, "fails": ["E"]}))

    print()
    ok = 0
    for name, got, want in cases:
        good = got["exit"] == want["exit"] and set(got["fails"]) == set(want["fails"])
        ok += good
        print(f"  {'✅' if good else '❌'} {name}")
        print(f"       期望 exit={want['exit']} fails={want['fails']}   "
              f"实测 exit={got['exit']} fails={got['fails']}")
    print(f"\n--drift-test {ok}/{len(cases)}")
    shutil.rmtree(tmp, ignore_errors=True)
    return 0 if ok == len(cases) else 1


if __name__ == "__main__":
    sys.exit(main("--drift-test" in sys.argv))
