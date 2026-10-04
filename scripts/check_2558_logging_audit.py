"""§2.5.58 的落档核验：历史日志的**归属**审计 + 这一节的每个数可重算。

背景：§2.5.56 发现「按行相邻读子进程输出会归错源」，§2.5.57 把产生它的机制
修掉（每条输出补源名前缀）。两轮末尾都挂着同一问——**过去有多少诊断受了它
影响？** 本脚本是那个回答的**可复跑形态**（不是一次性的临时探针）。

判据三条，缺一不可：
  ① 消息本身 —— 单源文件产出的行，文件名即源名
  ② URL 域名 —— 域名→源名从 `registry.py` 的 `_s(...)` **自动派生，绝不按缩写猜**
     （`hub_em` 是**湖**北；`sx_em` 是山西，陕西另有一个 `shaanxi_em`。手抄这张
     表必错——本轮就踩过，见 §2.5.58 第三节）
  ③ 紧邻的汇总行 —— 就是「按相邻归属」会得到的答案，**用来判对错的那一端**

判决：❌ 相邻≠真相 ／ ✅ 相邻=真相 ／ ❓ 真相不可判 ／ — 行在首个汇总行之前

**日志为什么不重新生成**：四份日志在 `docs/web/logs/`，其中 `health.txt`（10-01）
是**旧代码**跑出来的——`check_collector_health.py` 修了之后再也生成不出那一份。
它是不可再生的证据，所以随脚本一起入库，而不是留在临时目录里。

用法：
    python3 scripts/check_2558_logging_audit.py                # 核验，退出码 0 = 全绿
    python3 scripts/check_2558_logging_audit.py --drift-test   # 失败路径自证（注入漂移）
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "construction_agent"
LOGD = ROOT / "docs" / "web" / "logs"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
SRC = ROOT / "scripts" / "check_collector_health.py"

LOGS = ["health.txt", "health_20261005.txt", "health_full_2553.txt",
        "health_after_2557.txt"]
LOUD = {"warning", "error", "critical", "exception", "fatal", "warn"}
PREFIX_HINTS = ("[%s]", "[{}]", "[{self.key}]", "[{self.source_key}]")
SHARED_FILES = ("trs_cms_base.py", "base.py", "registry.py", "executor.py",
                "pipeline.py", "stores/", "cleaners/", "llm_classifier.py",
                "search/")
# 文档第四节那张表：| 日志 | 行数 | 裸行 | 归错 | 无相邻 | 带前缀 |
# 末两格 None = **不适用**（零裸行时「归错 / 无相邻」无从谈起，文档写 `—`）
TABLE_EXPECT = {"health.txt": (89, 17, 16, 1, 0),
                "health_20261005.txt": (117, 35, 34, 1, 6),
                "health_full_2553.txt": (98, 20, 19, 1, 7),
                "health_after_2557.txt": (38, 0, None, None, 14)}

SUMMARY = re.compile(r"^\s*\[\s*\d+/\d+\]\s+(\S+)")
PREF = re.compile(r"^\[([\w\-]+)\]\s")
SRCNAME = re.compile(r"\b([a-z]{2,6}_em|news_aggregator|safehoo|mem|"
                     r"open_gov|esafety)\b")


# --------------------------------------------------------------------------
# ② 域名 → 源名（自动派生）
# --------------------------------------------------------------------------
def host_to_source() -> dict:
    src = (PKG / "collector/registry.py").read_text(encoding="utf-8")
    m: dict = {}
    for node in ast.walk(ast.parse(src)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_s" and len(node.args) >= 5):
            continue
        key, url = node.args[0], node.args[4]
        if not (isinstance(key, ast.Constant) and isinstance(url, ast.Constant)):
            continue
        host = re.match(r"https?://([^/]+)", str(url.value))
        if host:
            h = host.group(1).lower()
            m[h] = key.value
            m[h.replace("www.", "")] = key.value
    return m


H2S = host_to_source()

# registry 只登**入口** URL，源文件里还有别的域名（`jl_em` 入口是
# `xxgk.jl.gov.cn`，请求的却是 `yjglt.jl.gov.cn`）——补扫源文件自己的域名，
# 仍然自动派生。只查 registry 会把这条判成「真相不可判」这个**假结论**。
for _p in sorted((PKG / "collector/sources").glob("*.py")):
    _key = _p.stem
    if _key in ("__init__", "trs_cms_base", "academic_import"):
        continue
    for _h in re.findall(r"https?://([a-z0-9.\-]+)",
                         _p.read_text(encoding="utf-8")):
        H2S.setdefault(_h.lower(), _key)
        H2S.setdefault(_h.lower().replace("www.", ""), _key)

# 搜索层的 warning 由「谁 import 了 search.*」决定——也从代码派生
SEARCH_USERS = set()
for _p in sorted((PKG / "collector").rglob("*.py")):
    if re.search(r"from\s+[\w.]*search[\w.]*\s+import|import\s+[\w.]*search\b",
                 _p.read_text(encoding="utf-8")):
        SEARCH_USERS.add(_p.stem)
SEARCH_KEYS = {k for k in H2S.values() if k in SEARCH_USERS}


def url_source(line: str) -> str:
    u = re.search(r"https?://([^/\s，,)]+)", line)
    if not u:
        return ""
    h = u.group(1).lower()
    if h in H2S:
        return H2S[h]
    parts = h.split(".")
    for i in range(1, len(parts) - 1):
        if ".".join(parts[i:]) in H2S:
            return H2S[".".join(parts[i:])]
    return ""


# --------------------------------------------------------------------------
# ① 消息 → 产出文件 → 源名
# --------------------------------------------------------------------------
def fmt_text(node: ast.AST) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(str(v.value) if isinstance(v, ast.Constant) else "{}"
                       for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return fmt_text(node.left) + fmt_text(node.right)
    if isinstance(node, ast.Name):
        return f"<{node.id}>"
    return f"<{type(node).__name__}>"


def literal_prefix(msg: str, n: int = 12) -> str:
    """取字面前缀：**先切到第一个 `{}` 再截断**。

    直接 `msg[:12]` 会把 `HTTP {} (尝试 {}): {}` 截成 `HTTP {} (尝试 {}` 这个
    永不匹配的键，于是所有 `HTTP 412 (尝试 1)` 与 `百度搜索「…」` 行都落进
    「认不出是谁」——**恰恰把最该看的两类藏起来**。
    """
    s = msg.lstrip()
    cut = s.find("{}")
    return (s[:cut] if cut > 0 else s).rstrip()[:n]


def collect_calls():
    for p in sorted(list((PKG / "collector").rglob("*.py"))
                    + list((PKG / "search").rglob("*.py"))):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            f = node.func
            if (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                    and f.value.id in ("logger", "log", "_log")):
                yield p, node.lineno, f.attr, fmt_text(node.args[0])


def survey() -> dict:
    """普查：warning+ 调用里，多少条自带前缀、多少条不带、落在哪种文件。"""
    bare_map: dict = {}
    n_loud = n_tagged = 0
    dropped = []
    for p, ln, lvl, msg in collect_calls():
        if lvl not in LOUD:
            continue
        n_loud += 1
        if any(h in msg for h in PREFIX_HINTS) or msg.startswith("["):
            n_tagged += 1
            continue
        rel = str(p.relative_to(ROOT)).replace("construction_agent/", "")
        shared = any(s in rel for s in SHARED_FILES)
        own = rel.rsplit("/", 1)[-1].replace(".py", "")
        pre = literal_prefix(msg)
        if len(pre) < 4:
            # 门槛**不能取 6**：`HTTP `（5 字）与 `百度搜索「`（5 字）会被滤掉。
            dropped.append(msg.strip())
            continue
        # 值是**列表**不是单值：同一个字面前缀可能由多个源产出。首版用 dict
        # 单值，76 条静默塌成 65 条（后到者覆盖先到者且无人知晓）。
        bare_map.setdefault(pre, []).append(
            (rel, ln, None if shared else own, shared, msg.lstrip()))
    return {"n_loud": n_loud, "n_tagged": n_tagged, "bare_map": bare_map,
            "dropped": dropped,
            "entries": [c for v in bare_map.values() for c in v]}


def judge(entries_map: dict, logs: list[str]) -> tuple[dict, dict]:
    """对每份日志逐行判「相邻会归给谁 vs 它是谁」。"""
    total = {"✅": 0, "❌": 0, "❓": 0, "—": 0, "带前缀": 0}
    per: dict = {}
    for name in logs:
        f = LOGD / name
        if not f.exists():
            # 报错不静默跳过：日志随脚本入库，缺了就是环境不对
            raise FileNotFoundError(f"日志不在：{f}")
        lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        cur, bare, wrong, noadj, tagged = None, 0, 0, 0, 0
        for raw in lines:
            s = raw.strip()
            m = SUMMARY.match(raw)
            if m:
                cur = m.group(1)
                continue
            if not s:
                continue
            if PREF.match(s):
                tagged += 1
                total["带前缀"] += 1
                continue
            hit = None
            for pre in sorted(entries_map, key=len, reverse=True):
                if s.startswith(pre):
                    hit = entries_map[pre]
                    break
            if hit is None:
                continue
            bare += 1
            owners = {c[2] for c in hit if c[2]}
            truth = next(iter(owners)) if len(owners) == 1 else ""
            truth = truth or url_source(s)
            if not truth and hit[0][0].startswith("search/"):
                truth = next(iter(SEARCH_KEYS)) if len(SEARCH_KEYS) == 1 else ""
            if cur is None:
                verdict = "—"
            elif not truth:
                verdict = "❓"
            else:
                verdict = "✅" if truth == cur else "❌"
            total[verdict] += 1
            if cur is None:
                noadj += 1
            elif verdict == "❌":
                wrong += 1
        per[name] = (len(lines), bare, wrong, noadj, tagged)
    return total, per


# --------------------------------------------------------------------------
# 核验
# --------------------------------------------------------------------------
def main() -> int:
    for p in (DOC, SRC, PKG / "collector/registry.py"):
        if not p.exists():
            print(f"❌ 缺文件：{p}")
            return 1

    sv = survey()
    bare_map, entries = sv["bare_map"], sv["entries"]
    total, per = judge(bare_map, LOGS)

    doc = DOC.read_text(encoding="utf-8")
    src = SRC.read_text(encoding="utf-8")
    lines_all = doc.splitlines()
    i0 = doc.index("### §2.5.58")
    i1 = doc.index("## 三、目标架构", i0)
    sec = doc[i0:i1]

    problems: list[str] = []
    n = 0

    def check(tag: str, ok: bool, detail: str = "") -> None:
        nonlocal n
        n += 1
        print(f"  {'✅' if ok else '❌'} {tag}" + (f"  {detail}" if detail else ""))
        if not ok:
            problems.append(f"{tag} {detail}")

    def num_after(pattern: str, label: str):
        m = re.search(pattern, sec)
        if not m:
            check(f"文档里有「{label}」这一处", False)
            return None
        return int(m.group(1))

    print("A 普查数字（从源码 AST 重算）")
    all_calls = sum(1 for _ in collect_calls())
    n_shared = sum(1 for c in entries if c[3])
    n_own = sum(1 for c in entries if not c[3])
    check("warning+ 调用 = 95", sv["n_loud"] == 95, f"实为 {sv['n_loud']}")
    check("自带源名前缀 = 16", sv["n_tagged"] == 16, f"实为 {sv['n_tagged']}")
    check("不带源名前缀 = 79", sv["n_loud"] - sv["n_tagged"] == 79)
    check("能建 ≥4 字前缀 = 76", len(entries) == 76, f"实为 {len(entries)}")
    check("落在多源共用文件 = 27", n_shared == 27, f"实为 {n_shared}")
    check("落在单源文件 = 49", n_own == 49, f"实为 {n_own}")
    check("27 + 49 = 76（两个子集互斥且穷尽）", n_shared + n_own == len(entries))
    check("被丢弃的 = 3 条", len(sv["dropped"]) == 3, f"实为 {len(sv['dropped'])}")
    check("文档把丢弃的 3 条**具名写出**了（不假装 100% 覆盖）",
          all(d in sec for d in sv["dropped"]), repr(sv["dropped"]))
    check("多源共用涉及 8 个文件", len({c[0] for c in entries if c[3]}) == 8,
          f"实为 {len({c[0] for c in entries if c[3]})}")

    print("B 判决数字（重跑归属）")
    check("相邻归错 = 69", total["❌"] == 69, f"实为 {total['❌']}")
    check("相邻也对 = 0", total["✅"] == 0, f"实为 {total['✅']}")
    check("真相不可判 = 0", total["❓"] == 0, f"实为 {total['❓']}")
    check("无相邻关系 = 3", total["—"] == 3, f"实为 {total['—']}")
    check("可判的裸行 = 69（✅ + ❌）", total["✅"] + total["❌"] == 69)

    def as_doc_form(got: tuple) -> tuple:
        """零裸行 → 归错/无相邻两格「不适用」，文档写 `—`。"""
        ln, b, w, na, t = got
        return (ln, b, None, None, t) if b == 0 else (ln, b, w, na, t)

    for name, exp in TABLE_EXPECT.items():
        got = per[name]
        check(f"{name}：行数/裸行/归错/无相邻/带前缀 = {exp}",
              as_doc_form(got) == exp, f"实为 {got}")
        row = re.search(rf"^\| `{re.escape(name)}`.*$", sec, re.M)
        if not row:
            check(f"文档表里有 {name} 一行", False)
            continue
        # 加粗格写成 `**16**`，取值前先剥 `*`——不剥就整格当「取不到」，
        # 逐格比对会静默退化成只认没加粗的那几格。
        cells = [c.strip() for c in row.group(0).split("|")[2:-1]]
        plain = [c.strip("*").strip() for c in cells]
        nums = [int(c) if re.fullmatch(r"\d+", c) else None for c in plain]
        check(f"文档表里 {name} 的**逐格数字**与重算一致", nums == list(exp),
              f"文档 {nums} vs 重算（表形态）{list(exp)}")
        if exp[1] == 0:
            check(f"零裸行那份（{name}）后两格写 `—` 而不是 0（不适用 ≠ 零）",
                  plain[2] == plain[3] == "—", f"实为 {cells}")
    m = re.search(r"按相邻读会归错 (\d+)/(\d+) = (\d+)%", sec)
    check("文档把「69/69 = 100%」写在正文里",
          m is not None and tuple(int(g) for g in m.groups()) == (69, 69, 100),
          f"文档写的是 {m.group(0) if m else '取不到'}")
    check("文档写明 69 是**可判**的裸行（3 条无相邻关系不计入）",
          "可判的裸行" in sec and "没有一次对" in sec)

    print("C 三条具体证据")
    hen = (PKG / "collector/sources/henan_em.py").read_text(encoding="utf-8")
    check("`未找到 div.zfxxgk_zdgkc` 出自 henan_em.py",
          "未找到 div.zfxxgk_zdgkc" in hen)
    others = [p.name for p in PKG.rglob("*.py")
              if p.name != "henan_em.py"
              and "未找到 div.zfxxgk_zdgkc" in
              p.read_text(encoding="utf-8", errors="ignore")]
    check("且**只有** henan_em 产出它（本句独有 → 不属本修法要解决的那一类）",
          not others, repr(others))
    trs_l = (PKG / "collector/sources/trs_cms_base.py").read_text(
        encoding="utf-8").splitlines()
    check("基类那句在 trs_cms_base.py:1063（warning 级、带重试计数与 URL）",
          "logger.warning" in trs_l[1062] and "尝试" in trs_l[1062]
          and "HTTP " in trs_l[1062], repr(trs_l[1062]))
    check("文档用的 `HTTP {} …` 是 AST 归一化形态，源码是 f-string（已交代）",
          "HTTP {} (尝试 {}): {}" not in "\n".join(trs_l)
          and 'f"HTTP {resp.status_code}' in trs_l[1062]
          and "AST 归一化" in sec)
    check("基类里**没有** `未找到 div.`（§2.5.57 那句 docstring 举错了）",
          "未找到 div." not in "\n".join(trs_l))
    jl = (PKG / "collector/sources/jl_em.py").read_text(encoding="utf-8")
    check("jl_em 入口 xxgk.jl.gov.cn / 请求 yjglt.jl.gov.cn（双域名）",
          "xxgk.jl.gov.cn" in jl and "yjglt.jl.gov.cn" in jl)
    check("文档记了 `hub_em` 是湖北（不是 `hb_em`）这条教训",
          "hub_em" in sec and "hb_em" in sec)
    check("文档记了 `sx_em` 是山西、`shaanxi_em` 才是陕西",
          "shaanxi_em" in sec and "shanxi" in sec)

    print("D §2.5.57 的 docstring 修复已生效（复核不回归）")
    check("旧的错写法已不在（`基类的` 后面不再跟 `未找到 div.`）",
          not re.search(r"基类的\s*`未找到 div", src))
    check("新举法在：点名 `trs_cms_base.py:1063`", "trs_cms_base.py:1063" in src)
    check("新举法说明了为什么举基类那句（12 个源共用）",
          "12 个源共用同一句" in src)
    check("**这个错误本身被写进 docstring**",
          "本 docstring 初版就把这句写成了「基类的」" in src)
    check("「七条口径」计数自洽仍在", "## 七条口径" in src)
    check("`_BLOCK_INDENT` 仍在", '_BLOCK_INDENT = " " * 10' in src)

    print("E 文档 ↔ 数字（提取承载处再比，不裸查 token）")
    # 裸查 token 会静默放行：把「全部 warning+ 调用 95 条」改成 96，token「95」
    # 在别处照样出现。故一律**定位承载该数字的那一处**。
    for pat, label, want in [
            (r"全仓（`collector/` \+ `search/`）共 \*\*(\d+) 条\*\*",
             "日志调用总数", all_calls),
            (r"warning\+ 只有 \*\*(\d+) 条\*\*", "warning+ 数", sv["n_loud"]),
            (r"全部 warning\+ 调用 (\d+) 条", "普查回显 warning+", sv["n_loud"]),
            (r"自带源名前缀：(\d+) 条", "带前缀", sv["n_tagged"]),
            (r"\*\*不带源名前缀：(\d+) 条\*\*", "不带前缀",
             sv["n_loud"] - sv["n_tagged"]),
            (r"落在\*\*多源共用文件\*\*：(\d+) 条", "多源共用", n_shared),
            (r"落在单个源自己的文件：(\d+) 条", "单源文件", n_own),
            (r"里 (\d+) 条能取出", "可建前缀", len(entries)),
            (r"另 (\d+) 条取不出", "被丢弃", len(sv["dropped"])),
            (r"多源共用\*\*的落在 (\d+) 个文件", "多源文件数",
             len({c[0] for c in entries if c[3]}))]:
        got = num_after(pat, label)
        check(f"文档「{label}」= {want}", got == want, f"文档写的是 {got}")

    print("F 零污染结论的判据可复现")
    first_2556 = next(i for i, l in enumerate(lines_all)
                      if l.startswith("### §2.5.56"))
    hits = [i + 1 for i, l in enumerate(lines_all)
            if "按行相邻" in l or "相邻归属" in l]
    check("`按行相邻`只出现在 §2.5.56 及其后（即讲这件事的那几节）",
          bool(hits) and all(h > first_2556 for h in hits),
          f"命中行 {hits}，§2.5.56 起于第 {first_2556 + 1} 行")
    other_md = [f.name for f in (ROOT / "docs/web").rglob("*.md")
                if f.name != "01_在线采集.md"
                and ("按行相邻" in f.read_text(encoding="utf-8")
                     or "相邻归属" in f.read_text(encoding="utf-8"))]
    check("其它 md 里零命中（污染没扩散出这一篇）", not other_md, repr(other_md))
    end_2558 = next((k for k in range(
        next(k for k, l in enumerate(lines_all)
             if l.startswith("### §2.5.58")) + 1, len(lines_all))
        if lines_all[k].startswith("## 三、目标架构")), len(lines_all))
    # **行号要取文件真行号**：对裁剪后的列表重新 enumerate，报出的编号是列表内
    # 序号，复核者会去查一行无关的文字（第一版就这么错过）。
    outside = [(k + 1, l) for k, l in enumerate(lines_all)
               if k < first_2556 or k >= end_2558]
    WRONG = {"cq_em": "412", "sx_em": "500", "js_em": "SSL", "gd_em": "zfxxgk"}
    for s, kw in WRONG.items():
        hits2 = [(k, l) for k, l in outside if s in l and kw in l]
        hits2 += [(i, l) for f in (ROOT / "docs/web").rglob("*.md")
                  if f.name != "01_在线采集.md"
                  for i, l in enumerate(
                      f.read_text(encoding="utf-8").splitlines(), 1)
                  if s in l and kw in l]
        if not hits2:
            check(f"{s} 在**讲述区之外**没被安上 {kw}", True)
            continue
        # 「列举」（行内并列 ≥2 个源名）与「归错」（行内只 1 个源名）有一条
        # **可数**的界线。不写「读起来像不像列举」那种软规则——它一旦进脚本
        # 就会退化成「凡是解释得通就放行」。
        for k, l in hits2:
            names = sorted(set(SRCNAME.findall(l)))
            check(f"{s}+{kw} 同现行（L{k}）是**列举**（行内 {len(names)} 个源名）",
                  len(names) >= 2, f"只有 {names} —— 单源同名 = 真归错，要报红")
    inside = lines_all[first_2556:end_2558]
    check("反例本身确实写在讲述区里（上面的判据不是空转）",
          any("cq_em" in l and "412" in l for l in inside)
          and any("sx_em" in l and "500" in l for l in inside))
    check("文档如实区分了「列举同现」与「归错」", "列举" in sec)
    check("文档如实记了 shaanxi_em / henan_em 两处**没记载**（遗漏不是错）",
          "没记载" in sec)

    print("G 边界登记")
    check("登记了「没审会话过程」", "会话过程" in sec or "也审不了" in sec)
    check("登记了别的临时日志没进范围", "单源" in sec)
    check("登记了 info/debug 没审及其依赖环境",
          "debug" in sec and "不落盘" in sec)
    check("登记了本轮请求账 = 0", "请求账：0" in sec)

    print()
    if problems:
        print(f"❌ {len(problems)} 项待处置：")
        for p in problems:
            print(f"   - {p}")
        return 1
    print(f"✅ 全部通过（七段 / {n} 项断言）")
    print(f"   四份日志：{LOGD}")
    print(f"   判决：❌ 相邻归错 {total['❌']} ／ ✅ 相邻也对 {total['✅']} ／ "
          f"❓ 不可判 {total['❓']} ／ — 无相邻 {total['—']}")
    return 0


# --------------------------------------------------------------------------
# 失败路径自证
# --------------------------------------------------------------------------
DRIFT = [
    ("文档正文注入一处假归错（cq_em+412，落在讲述区之外）", DOC,
     lambda t: t.replace("\n---\n\n## 二、",
                         "\n`cq_em` 那台机器 412 反爬，查过一轮。\n\n---\n\n## 二、", 1)),
    ("文档抹掉一条被丢弃裸行的具名", DOC,
     lambda t: t.replace("`解析 {} 失败: {}`", "`某条取不出前缀的裸行`", 1)),
    ("源码 docstring 改回「基类的 `未找到 div.`」", SRC,
     lambda t: t.replace("`HTTP … (尝试 …): …`——**12 个源共用同一句**",
                         "`未找到 div.zfxxgk_zdgkc`——基类那句", 1)),
    ("文档把一个普查数 95 改成 96", DOC,
     lambda t: t.replace("全部 warning+ 调用 95", "全部 warning+ 调用 96", 1)),
]


def drift_test() -> int:
    """往被测对象注入漂移，逐个断言退出码转 1。

    **不做这一步，「退出码 0」只说明脚本没崩，不说明它有鉴别力。**
    注入面各自只覆盖被测判据，否则分不清是哪一行抓到的。
    """
    def run_self() -> int:
        return subprocess.run([sys.executable, str(Path(__file__).resolve())],
                              capture_output=True).returncode

    if run_self() != 0:
        print("❌ 基线就不是 0，先修")
        return 1
    print("基线退出码 = 0  ✅")
    bad = 0
    for name, path, mutate in DRIFT:
        orig, h = path.read_text(encoding="utf-8"), hash(path.read_bytes())
        try:
            new = mutate(orig)
            if new == orig:
                print(f"❌ {name}：**替换没落地**，用例静默失效")
                bad += 1
                continue
            path.write_text(new, encoding="utf-8")
            rc = run_self()
            print(f"{'✅' if rc == 1 else '❌'} {name} → 退出码 {rc}"
                  + ("" if rc == 1 else "（应转 1，判据没抓到）"))
            if rc != 1:
                bad += 1
        finally:
            path.write_text(orig, encoding="utf-8")
            if hash(path.read_bytes()) != h:
                print(f"❌ {name}：**还原不一致**")
                bad += 1
    rc = run_self()
    print(f"还原后退出码 = {rc}（应为 0）")
    if rc != 0:
        bad += 1
    print(f"{'✅ 失败路径 4/4' if not bad else f'❌ {bad} 项待处置'}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(drift_test() if "--drift-test" in sys.argv else main())
