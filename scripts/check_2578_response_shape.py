#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.78 响应结构的层级归属：样例画的层级，实现给的是不是那一层。

## 来路

`docs/web/09_端到端对齐核查.md` §10.5 列了三条「比对过、但没比出来的」，
第一条就是**层级归属**：`check_api_contract.py` 的 `keys_of()` 只收**键名**，
不比路径——`{"data": {"items": [{"id": 1}]}}` 与 `{"data": {"id": 1}, ...}`
在它眼里是同一组键。`docs/web/00_总体架构与数据层.md` §4.1 末尾自陈
「本节这张表是靠人核出来的，脚本护不住」。

## 判据：三档，互不重叠

顶层键**从路径面里排除**、专归 B1 管——这样任何一处形状错只会命中恰好一档，
不会「一条缺陷三档齐红」分不清是哪条判据抓到的。

| 档 | 判据 | 抓什么 | 数据为空时 |
|---|---|---|---|
| B1 | 样例的**顶层键**集合 == 响应的顶层键集合 | 整体形状错（`data` 被平铺、少/多一个 `meta`） | **不受影响** |
| B2 | 响应里**每个带点的路径**都能在样例里找到 | 实现多给了文档没写的嵌套字段 | —— |
| B3 | 样例里**每条带点的路径**在响应里缺失时，须能归因到一个**空容器**，且该容器有**源侧行数**印证 | 实现漏给了文档写了的嵌套字段 | 这档就是为空数据设计的 |

B3 是关键：没有它，`data` 为空的接口上判据是空转的、结论无从谈起（§2.5.77 的
「值面」教训：判据在多数样本上恒真，就等于没有）。有了它，「这个容器为什么空」
必须给出**源表行数**，而不是由实现自己声称。登记表另配元判据（不重不漏），
防「新增了空容器没登记」与「登记了、数据已到、却还挂着旧结论」两种无声过期。

## 四段

    A. 实测：逐路由收响应键路径树 + 空容器
    B. 三档判据 + 登记表元判据 + 源侧行数印证 + 覆盖面
    C. 注入式自证：实现侧两例 + 数据侧一例，断言红的**恰好**是哪几项
    D. 文档 ↔ 实测

口径：`app.test_client()`，只打 GET 且返回 JSON 200 的路由。非 JSON（HTML、
图片）与 POST 不在范围内——与 §2.5.77 同界。样例侧的路径由**自制括号配对器**
解出（不能 `json.loads`：样例里有 `//` 注释与 `…` 占位），故注释放行尾、
字符串值不当作键这两条另立自证。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_api_contract as A                            # noqa: E402
from _common import main_db_path, main_repo_root          # noqa: E402
from _serve import point_at                               # noqa: E402

DOC = ROOT / "docs" / "web" / "01_在线采集.md"
WEB = ROOT / "construction_agent" / "web"
COLLECT = WEB / "collect.py"
REVIEW = WEB / "review.py"
PY = "/home/suntianhao/construction_agent/.venv/bin/python3"

MAIN_DB = main_db_path()
STAGING_DB = main_repo_root() / "data" / "caiems_staging.db"

#: 探针用临时库指工作库（数据侧自证），默认不用。
ENV_STAGING = "C2578_STAGING_DB"

KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"')
MAX_DEPTH = 12
LIST_HEAD = 50

#: 空容器 ↔ 它的**源表**。判据是「源表 0 行 ⇔ 该容器在响应里为空」，
#: 两侧必须同时成立；源表来数据了而响应还空着，就是实现把数据丢了。
#: 表名与容器路径都是**读实现读出来的**，不是猜的：
#:   collect/raw     ← `raw_accidents`（collect.py:321-366）
#:   extract/results ← `staging_accidents`（extract.py:399-422）
#:   review/queue    ← `staging_accidents`（review.py:204-243、`_warn_scan`）
#:   review/stats    ← `staging_accidents` / `promote_log`（`funnel_payload`）
EMPTY_SOURCES: dict[tuple[str, str], str] = {
    ("GET /api/collect/raw", "data"): "raw_accidents",
    ("GET /api/extract/results", "data"): "staging_accidents",
    ("GET /api/review/queue", "data"): "staging_accidents",
    ("GET /api/review/queue", "by_warning"): "staging_accidents",
    ("GET /api/review/stats", "data.by_warning"): "staging_accidents",
    ("GET /api/review/stats", "data.promote_history"): "promote_log",
}

#: 文档面锚点（D 段）
DOC_NEEDLES = ["样例画的层级，实现给的是不是那一层"]


# ══════════════════════════════════════════════════════════════════
# 路径解析
# ══════════════════════════════════════════════════════════════════
def paths_of(text: str) -> tuple[set[str], set[str]]:
    """括号配对解出样例的**键路径** → `(全部路径, 其中是容器的路径)`。

    数组元素的对象**继承数组键的路径**（`items` 里的 `id` 记作 `items.id`，
    不记 `items[].id`——判据要比的是层级，不是下标）。

    不引 `json.loads`：样例里有 `//` 注释与 `…` 占位，两份样例合起来也不是
    合法 JSON。所以 `//` 到行尾要跳过，且**字符串值不当作键**——`"items"` 与
    `"items":` 差一个冒号，这一条是解析器的命门。

    返回"哪些是容器"是判据需要的：**样例有意省略叶子字段**（只写代表字段 +
    `…`），但**容器结构是画全的**。所以「实现多给了字段」不该报红，「实现多
    包了一层容器」才该报红——这条区分让 B2 只判层级、不判字段完整性（字段
    完整性有另一个方向的判据在 `check_api_contract.py` 里）。
    """
    res: set[str] = set()
    conts: set[str] = set()
    stack: list[str] = []                      # 每层容器内的路径前缀
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            m = KEY_RE.match(text, i)
            if m:
                key = m.group(1)
                j = m.end()
                while j < n and text[j] in " \t":
                    j += 1
                if j < n and text[j] == ":":
                    j += 1
                    while j < n and text[j] in " \t":
                        j += 1
                    prefix = stack[-1] if stack else ""
                    path = f"{prefix}.{key}" if prefix else key
                    res.add(path)
                    if j < n and text[j] in "{[":
                        conts.add(path)
                        stack.append(path)
                        i = j + 1
                        continue
                    i = j
                    continue
            # 是字符串**值**（或匹配不上键名形态的字符串）：整段跳过
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c in "{[":
            stack.append(stack[-1] if stack else "")   # 数组元素：继承路径
            i += 1
            continue
        if c in "]}":
            if stack:
                stack.pop()
            i += 1
            continue
        i += 1
    return res, conts


def doc_shapes() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """接口 → 样例的路径集合 / 容器集合。归属口径与 `A.doc_samples` 逐字一致。"""
    out: dict[str, set[str]] = {}
    conts: dict[str, set[str]] = {}
    for path in sorted(A.DOCS.glob("[0-9][0-9]_*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        cur: str | None = None
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.lstrip().startswith("#"):
                m = A.CALL_RE.search(line)
                cur = f"{m.group(1)} {m.group(2)}" if m else None
            if line.startswith("```json"):
                j = i + 1
                chunk: list[str] = []
                while j < len(lines) and not lines[j].startswith("```"):
                    chunk.append(lines[j])
                    j += 1
                text = "\n".join(chunk)
                if '"ok"' in text and cur:
                    key = A.norm(cur)
                    ps, cs = paths_of(text)
                    out.setdefault(key, set()).update(ps)
                    conts.setdefault(key, set()).update(cs)
                i = j
            i += 1
    return out, conts


def real_shape(obj, prefix: str = "", paths: set[str] | None = None,
               conts: set[str] | None = None, empt: set[str] | None = None,
               depth: int = 0):
    """响应对象 → `(全部路径, 容器路径, 空容器路径)`。

    数组**遍历所有元素**（上限 `LIST_HEAD` 条，防大列表拖慢）：只取前几条会
    漏掉「第 6 个元素才有的键」，那是**假绿**的方向。
    """
    if paths is None:
        paths, conts, empt = set(), set(), set()
    assert conts is not None and empt is not None
    if depth > MAX_DEPTH:
        return paths, conts, empt
    if isinstance(obj, dict):
        if prefix:
            conts.add(prefix)
            if not obj:
                empt.add(prefix)
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            paths.add(p)
            real_shape(v, p, paths, conts, empt, depth + 1)
    elif isinstance(obj, list):
        if prefix:
            conts.add(prefix)
            if not obj:
                empt.add(prefix)
        for v in obj[:LIST_HEAD]:
            real_shape(v, prefix, paths, conts, empt, depth + 1)
    return paths, conts, empt


def tops(paths: set[str]) -> set[str]:
    return {p.split(".")[0] for p in paths if p}


def dotted(paths: set[str]) -> set[str]:
    """只留带点的路径——顶层键归 B1 管，不重复判。"""
    return {p for p in paths if "." in p}


def blame(path: str, real: set[str], empt: set[str]) -> str | None:
    """样例有、响应无的一条路径：找**最近的空容器祖先**，找不到就返回 None。"""
    parts = path.split(".")
    for k in range(len(parts) - 1, 0, -1):
        anc = ".".join(parts[:k])
        if anc in empt:
            return anc
        if anc in real:
            return None
    return None


# ══════════════════════════════════════════════════════════════════
# 实测
# ══════════════════════════════════════════════════════════════════
def staging_path() -> Path:
    return Path(os.environ.get(ENV_STAGING) or STAGING_DB)


def measure() -> dict:
    point_at(str(MAIN_DB), staging_path())
    from construction_agent.web.factory import create_app

    app = create_app()
    c = app.test_client()
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    aid = con.execute("SELECT id FROM accidents LIMIT 1").fetchone()[0]
    con.close()

    doc, doc_conts = doc_shapes()
    res: dict = {"doc": {k: sorted(v) for k, v in doc.items()},
                 "real": {}, "no_sample": [], "non_json": []}
    for rule in sorted({str(r) for r in app.url_map.iter_rules()
                        if "GET" in r.methods}):
        if rule.startswith("/static/") or "<path:" in rule:
            continue
        key = f"GET {A.norm(rule)}"
        r = c.get(re.sub(r"<[^>]*>", aid, rule))
        if r.mimetype != "application/json" or r.status_code != 200:
            res["non_json"].append(key)
            continue
        if key not in doc:
            res["no_sample"].append(key)
            continue
        paths, conts, empt = real_shape(r.get_json(silent=True))
        res["real"][key] = {"paths": sorted(paths),
                            "conts": sorted(conts), "empt": sorted(empt)}
    return res


def compare(m: dict) -> dict:
    """三档判据 + 元判据 + 源侧印证。返回可 JSON 化的报告。"""
    b1: list[dict] = []
    b2: list[str] = []
    b3: list[str] = []
    used: set[tuple[str, str]] = set()      # 被 blame **实际用到**的空容器
    for key, rv in sorted(m["real"].items()):
        d, real = set(m["doc"][key]), set(rv["paths"])
        empt = set(rv["empt"])
        if tops(d) != tops(real):
            b1.append({"key": key, "doc_only": sorted(tops(d) - tops(real)),
                       "real_only": sorted(tops(real) - tops(d))})
        # B2 只看**容器**：样例有意省略叶子字段（只写代表字段 + `…`），
        # 但容器结构是画全的。所以「多给字段」不报，「多包一层容器」才报。
        for p in sorted(set(rv["conts"]) - d):
            b2.append(f"{key} {p}")
        for p in sorted(dotted(d) - dotted(real)):
            b = blame(p, real, empt)
            if b is None:
                b3.append(f"{key} {p}")
            else:
                used.add((key, b))

    # 元判据。基准不是「所有空容器」而是「**被归因用到**的空容器」：
    # `/api/health` 的 `missing_indexes` 为空是索引齐全（好事）、
    # `/api/causes/distribution` 的 `filter.*` 为空是该次查询没传筛选
    # （正常）——它们不为任何归因服务，强求登记只是把登记表撑成摆设。
    seen = {(k, e) for k, rv in m["real"].items() for e in rv["empt"]}
    registered = set(EMPTY_SOURCES)
    meta_missing = sorted(f"{k} {e}" for k, e in used - registered)
    meta_stale = sorted(f"{k} {e}" for k, e in registered - seen)
    meta_unused = sorted(f"{k} {e}" for k, e in registered - used)

    # 源侧印证：登记的每个源表须实测 0 行（"空"不能只有实现一句话）
    src: list[str] = []
    sp = staging_path()
    if not sp.exists():
        src.append(f"工作库不存在：{sp}")
    else:
        con = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        for (key, cont), table in sorted(EMPTY_SOURCES.items()):
            if (key, cont) not in seen:
                continue        # 该容器这次不空 → 归 meta_stale 管，不重复报
            try:
                n = con.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]
            except sqlite3.Error as exc:
                src.append(f"{key} {cont}：源表 {table} 查不动（{exc}）")
                continue
            if n:
                src.append(f"{key} {cont}：源表 {table} 有 {n} 行，"
                           f"响应却空着——数据到了没发出去")
        con.close()

    rep = {"b1": b1, "b2": b2, "b3": b3,
           "meta_missing": meta_missing, "meta_stale": meta_stale,
           "meta_unused": meta_unused, "used": sorted(f"{k} {e}" for k, e in used),
           "src": src}
    rep["exit"] = 1 if (b1 or b2 or b3 or meta_missing or meta_stale or src) else 0
    return rep


# ══════════════════════════════════════════════════════════════════
# 段
# ══════════════════════════════════════════════════════════════════
FAILED: list[str] = []


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"   {extra}" if extra else ""))
    if not cond:
        FAILED.append(label)
    return bool(cond)


def part_a(m: dict) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  样例接口 {len(m['doc'])} 条 / 实测到 JSON 200 的 {len(m['real'])} 条"
          f" / 无样例 {len(m['no_sample'])} 条 / 非 JSON {len(m['non_json'])} 条")
    tot = sum(len(v["paths"]) for v in m["real"].values())
    conts = sum(len(v["conts"]) for v in m["real"].values())
    empt = sum(len(v["empt"]) for v in m["real"].values())
    print(f"  响应键路径合计 {tot} 条（其中容器 {conts} 条）、空容器 {empt} 个")
    print(f"  非 JSON 路由（不在范围内）：{m['non_json']}")
    print(f"  无样例路由（无源可核）：{m['no_sample']}")


def part_b(m: dict, rep: dict) -> None:
    print("\n═══ B. 三档判据 ═══")
    ok(not rep["b1"], f"B1 顶层键面：{len(m['real'])} 条接口全等",
       "" if not rep["b1"] else str(rep["b1"][:3]))
    ok(not rep["b2"], "B2 响应容器 ⊆ 样例路径（实现没多包一层、没搬走分支）",
       "" if not rep["b2"] else str(rep["b2"][:3]))
    ok(not rep["b3"], "B3 样例多出的路径全部可归因到空容器",
       "" if not rep["b3"] else str(rep["b3"][:3]))
    ok(not rep["meta_missing"], "B3 元判据：被归因用到的空容器全部已登记",
       str(rep["meta_missing"]))
    ok(not rep["meta_stale"], "B3 元判据：登记的空容器全部仍然为空（无过期结论）",
       str(rep["meta_stale"]))
    print(f"  登记但当前未被归因用到（不是错，只提示）：{rep['meta_unused']}")
    ok(not rep["src"], f"源侧印证：{len(EMPTY_SOURCES)} 个登记容器的源表实测 0 行",
       str(rep["src"]))

    weak = {k: len(dotted(set(v["paths"])))
            for k, v in m["real"].items() if v["empt"]}
    print(f"  覆盖强度：{len(weak)} 条接口存在空容器，B2 在它们上只能见到"
          f"这么多个嵌套路径（越少越接近空转）：{weak}")
    extra = sum(len(dotted(set(m["doc"][k])) - dotted(set(v["paths"])))
                for k, v in m["real"].items())
    print(f"  样例比响应多出的嵌套路径共 {extra} 条，全部由空容器解释。")
    print("  三档分工：顶层键只归 B1；B2 只看**容器**（样例有意省略叶子字段，"
          "故「多给字段」不报）；B3 只看**样例多出的路径**。")


def _run_diff() -> dict:
    p = subprocess.run([PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 没有输出 JSON：{p.stdout[-500:]}{p.stderr[-500:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def _key_of(s: str) -> str:
    """明细行 → 接口名。`GET /api/xxx container` / `GET /api/xxx path` 都取前两段。"""
    return " ".join(s.split()[:2])


def _red_set(rep: dict) -> dict[str, list[str]]:
    """红集合，**档 × 接口**粒度（明细条数随数据而变，断言用接口级才稳定）。

    粒度写清楚：这里断言的是「哪些档在哪个接口上红了」，不是「红了多少条
    路径」——后者会随注入的大小而变，断言它等于赌注入的规模。
    """
    out: dict[str, list[str]] = {}
    for k in ("b1", "b2", "b3", "meta_missing", "meta_stale", "src"):
        if not rep[k]:
            continue
        if k == "b1":
            out[k] = sorted(d["key"] for d in rep[k])
        else:
            out[k] = sorted({_key_of(s) for s in rep[k]})
    return out


def _with_file(path: Path, old: str, new: str):
    """临时改真实源码，`try/finally` 保证逐字还原。"""
    class _Ctx:
        def __enter__(self):
            self.src = path.read_text(encoding="utf-8")
            if old not in self.src:
                raise RuntimeError(f"锚点未命中：{path.name} / {old!r}")
            path.write_text(self.src.replace(old, new, 1), encoding="utf-8")

        def __exit__(self, *exc):
            path.write_text(self.src, encoding="utf-8")
            return False
    return _Ctx()


def part_c() -> None:
    print("\n═══ C. 注入式自证（红的恰好是哪几项）═══")
    cases = [
        ("collect/sources 的 data 多包一层（整层错位）", COLLECT,
         "    return jsonify(sources_payload())\n",
         "    _p = sources_payload()\n"
         '    _p["data"] = {"rows": _p["data"]}\n'
         "    return jsonify(_p)\n",
         {"b2": ["GET /api/collect/sources"],
          "b3": ["GET /api/collect/sources"]}),
        ("review/stats 的顶层 ok 键被删（只动顶层）", REVIEW,
         '    return jsonify({"ok": True, "data": funnel_payload()})',
         '    return jsonify({"data": funnel_payload()})',
         {"b1": ["GET /api/review/stats"]}),
    ]
    for label, path, old, new, want in cases:
        with _with_file(path, old, new):
            got = _red_set(_run_diff())
        ok(got == want, f"{label} → 红集合精确命中",
           f"实际 {sorted(got)}" if got != want else "")

    # 数据侧：把工作库灌一行，登记表必须**自己过期转红**（不是永久豁免）
    sp = staging_path()
    tmpd = Path(tempfile.mkdtemp(prefix="c2578_"))
    tmp = tmpd / "staging.db"
    try:
        if not sp.exists():
            ok(False, "数据侧自证：工作库不存在，无法构造", str(sp))
            return
        src = sqlite3.connect(f"file:{sp}?mode=ro", uri=True)
        dst = sqlite3.connect(tmp)
        src.backup(dst)                     # WAL 库不能用 shutil.copy2（会滞后）
        dst.execute("INSERT INTO raw_accidents "
                    "(raw_id, source_key, source_url, fetch_time, url_hash) "
                    "VALUES ('c2578probe','c2578','https://example.invalid/x',"
                    "'2026-10-05T00:00:00','h')")
        dst.commit()
        dst.close()
        src.close()
        env = dict(os.environ, **{ENV_STAGING: str(tmp)})
        p = subprocess.run([PY, __file__, "--diff"], capture_output=True,
                           text=True, env=env)
        line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
        got = _red_set(json.loads(line[0][len("@@JSON@@"):])) if line else {"crash": 1}
        ok(got == {"meta_stale": ["GET /api/collect/raw"]},
           "数据侧自证：工作库灌 1 行 → 登记表过期转红（其余判据仍绿）",
           f"实际 {got}")
    finally:
        shutil.rmtree(tmpd, ignore_errors=True)


def part_d(m: dict) -> None:
    print("\n═══ D. 文档 ↔ 实测 ═══")
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        ok(needle in txt, f"01 号文档里有锚点「{needle[:22]}」")
    rep = compare(m)
    extra = sum(len(dotted(set(m["doc"][k])) - dotted(set(v["paths"])))
                for k, v in m["real"].items())
    stat = {
        "有样例的接口": len(m["real"]),
        "顶层键面一致": len(m["real"]) - len(rep["b1"]),
        "样例多出的嵌套路径": extra,
        "空容器登记项": len(EMPTY_SOURCES),
        "无样例的接口": len(m["no_sample"]),
    }
    for label, n in stat.items():
        mm = re.search(r"\|\s*" + label + r"\s*\|\s*\*\*([\d,]+)\*\*\s*\|", txt)
        ok(bool(mm) and int(mm.group(1).replace(",", "")) == n,
           f"文档表里「{label}」= {n}",
           f"文档写的是 {mm.group(1) if mm else '（没找到该行）'}")


def main() -> int:
    if "--diff" in sys.argv:
        rep = compare(measure())
        print("@@JSON@@" + json.dumps(rep, ensure_ascii=False))
        return rep["exit"]

    m = measure()
    part_a(m)
    part_b(m, compare(m))
    part_c()
    part_d(m)

    print()
    if FAILED:
        print(f"❌ 失败 {len(FAILED)} 项：")
        for f in FAILED:
            print(f"   - {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
