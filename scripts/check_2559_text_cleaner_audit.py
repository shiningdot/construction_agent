#!/usr/bin/env python3
"""§2.5.59 自检：`TextCleaner` 的规则形态 + 它在真实正文上的改动面 ↔ 文档数字。

三件事，缺一不可：

  A. **规则形态**——`text_cleaner.py` 在**代码里**到底怎么写的（`ZERO_WIDTH_CHARS`
     几个、`NOISE_PATTERNS` 几条、③ 用的是不是整段 NFKC、⑩ 的阈值是不是 4）。
     文档里「某一步有害」的论断成立与否，前提是这一步确实长这样。
  B. **改动面实测**——主库 `accidents.raw_text` 2,623 条上重跑一遍，逐步骤量命中数，
     以及 NFKC 的逐字符对分组、短行过滤的孤立/夹心拆分、两个替代实现的对照。
  C. **文档 ↔ 实测**——把 §2.5.59 里的那些数字**定位到承载它的那一处**再比
     （裸查 token 会静默放行：`16,484` 在别处出现照样命中，见 §2.5.58 的教训）。

口径：主库只读（零请求）。这些数字是「这类中文事故正文里各步影响多大」的先验，
不是那 12 个 TRS 源的实测——文档第二节已声明，脚本不越界。
"""
from __future__ import annotations

import html as html_module
import re
import sqlite3
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from construction_agent.collector.cleaners.text_cleaner import TextCleaner  # noqa: E402
from _common import main_db_path, REPO_ROOT  # noqa: E402

DOC = REPO_ROOT / "docs" / "web" / "01_在线采集.md"
SRC = REPO_ROOT / "construction_agent" / "collector" / "cleaners" / "text_cleaner.py"

fails: list[str] = []


def ok(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {detail}" if detail else ""))
    if not cond:
        fails.append(label)


# ---------------------------------------------------------------- A. 规则形态
def part_a() -> dict:
    print("\nA. 规则形态（读 text_cleaner.py 源码）")
    print("=" * 74)
    code = SRC.read_text(encoding="utf-8")
    r: dict[str, object] = {}

    r["零宽字符数"] = len(TextCleaner.ZERO_WIDTH_CHARS)
    r["噪声正则数"] = len(TextCleaner.NOISE_PATTERNS)
    ok(r["零宽字符数"] == 8, f"ZERO_WIDTH_CHARS 8 个", f"实测 {r['零宽字符数']}")
    ok(r["噪声正则数"] == 11, "NOISE_PATTERNS 11 条", f"实测 {r['噪声正则数']}")

    # ③ 是不是**整段** NFKC（而非逐字符白名单）
    r["NFKC是整段"] = bool(re.search(
        r"text\s*=\s*unicodedata\.normalize\(\s*['\"]NFKC['\"]\s*,\s*text\s*\)", code))
    ok(r["NFKC是整段"], "③ 用整段 unicodedata.normalize('NFKC', text)",
       "文档称其为「有害」的一步，前提就是它整段生效")

    # ⑩ 阈值形态：len < 4 且 not \d
    m = re.search(r"len\(\s*stripped\s*\)\s*<\s*(\d+)\s*and\s*not\s+re\.search\("
                  r"r?['\"]\\d['\"]", code)
    r["短行阈值"] = int(m.group(1)) if m else None
    ok(r["短行阈值"] == 4, "⑩ 的短行阈值是 4 且排除含数字行", f"实测 {r['短行阈值']}")

    # ④ 是死代码的前提：① 的 unescape 在它前面
    i_unesc = code.find("html_module.unescape(text)")
    i_ent = code.find("'&nbsp;'")
    r["unescape在实体replace之前"] = 0 <= i_unesc < i_ent
    ok(r["unescape在实体replace之前"], "④ 在 ① 之后（故在 ① 已覆盖时为死代码）")

    # 其余七步的存在性（文档说它们「可原样接」）
    r["有零宽去除"] = "for ch in self.ZERO_WIDTH_CHARS" in code
    r["有换行统一"] = "\\r\\n" in code
    r["有空白压缩"] = bool(re.search(r"re\.sub\(r'\[ \\t\]\+'", code))
    r["有换行压缩"] = bool(re.search(r"re\.sub\(r'\\n\\s\*\\n", code))
    r["有strip"] = "line.strip() for line in text.split" in code
    for k in ("有零宽去除", "有换行统一", "有空白压缩", "有换行压缩", "有strip"):
        ok(bool(r[k]), f"其余步骤在位：{k}")
    return r


# --------------------------------------------------------- B. 改动面实测
def measure() -> dict:
    """一趟扫完全库，算出文档里用到的每个量。"""
    con = sqlite3.connect(f"file:{main_db_path()}?mode=ro", uri=True)
    rows = con.execute("SELECT source_name, raw_text FROM accidents "
                       "WHERE raw_text IS NOT NULL").fetchall()
    NOISE = [re.compile(p, re.IGNORECASE | re.DOTALL) for p in TextCleaner.NOISE_PATTERNS]
    ZW = TextCleaner.ZERO_WIDTH_CHARS
    c = TextCleaner()
    m: dict[str, object] = {"分母": len(rows)}
    st = Counter()
    nfkc_pairs: Counter = Counter()
    nfkc_group = Counter()
    iso = mid = cons = 0
    mid_src = Counter()
    short_src = Counter()          # ⑩ 删的行按来源（文档第六节表格的「短行/条」）
    # UI 白名单必须按 **NFKC 之后的形态** 来写：③ 在 ⑩ 之前跑，`更新日期：` 到 ⑩
    # 手上已经变成 `更新日期:`——第一版按原样写全角，匹配数从 2,569 掉到 1,322，
    # 正是「③ 的有害会污染下游」的一处实证（下游规则得按被改过的形态重写）。
    UI_WORDS = {unicodedata.normalize("NFKC", w) for w in
                ("评论：", "图片", "正文:", "来源：", "更新日期：",
                 "字体：", "打印", "关闭", "分享", "上一篇", "下一篇", "返回顶部")}
    KW = set("，。、；：？！“”‘’（）《》〈〉【】「」『』—…～·")
    FW = {}
    FW.update({chr(x): chr(x - 0xFEE0) for x in range(0xFF10, 0xFF1A)})
    FW.update({chr(x): chr(x - 0xFEE0) for x in range(0xFF21, 0xFF3B)})
    FW.update({chr(x): chr(x - 0xFEE0) for x in range(0xFF41, 0xFF5B)})

    def white_nfkc(t: str) -> str:
        t = t.replace("\u3000", " ").replace("\xa0", " ")
        return "".join(FW.get(ch, ch) for ch in t)

    for src, t0 in rows:
        t = t0 or ""
        # ⚠️ 步骤必须**串行**（每步的输出是下一步的输入），与真实管道一致。
        # 第一版写成「每步都从原始 t 出发」，⑥ 得 1,511 而文档是 1,517 ——
        # 差的 6 条正是 ③ NFKC 把 `　` 变成 U+0020 之后**新出现的连续空白**。
        # 独立口径量的是「各步自己的功劳」，串行口径量的才是「管道跑一遍会怎样」。
        # 白名单那两行是**独立口径**（从原始 t 出发，回答「换掉这一步会怎样」），
        # 与步骤表不同源，故分开记。
        a = html_module.unescape(t)
        if a != t:
            st["①"] += 1
        b = a
        for ch in ZW:
            b = b.replace(ch, "")
        if b != a:
            st["②"] += 1
        d = unicodedata.normalize("NFKC", b)
        if d != b:
            st["③"] += 1
        for ch in set(b):          # 取 **b（NFKC 之前）** —— 在 `d` 上找这些字符一个也找不到
            n = unicodedata.normalize("NFKC", ch)
            if n != ch:
                nfkc_pairs[(ch, n)] += b.count(ch)   # 累加，不是覆盖
                nfkc_group["CJK" if ch in KW else
                           ("空白" if ch in "　\xa0" else "其它")] += b.count(ch)
        if white_nfkc(b) != b:
            st["白名单改动"] += 1
        if white_nfkc(b) != d:
            st["白名单与整段不同"] += 1

        e = d.replace("\r\n", "\n").replace("\r", "\n")
        if e != d:
            st["⑤"] += 1
        g = re.sub(r"[ \t]+", " ", e)
        if g != e:
            st["⑥"] += 1
        h = re.sub(r"\n\s*\n\s*\n+", "\n\n", g)
        if h != g:
            st["⑦"] += 1
        i2 = "\n".join(ln.strip() for ln in h.split("\n"))
        if i2 != h:
            st["⑧"] += 1

        lines = i2.split("\n")
        for k, ln in enumerate(lines):
            s = ln.strip()
            if not s:
                continue
            if any(p.search(ln) for p in NOISE):
                st["⑨"] += 1
                continue
            if s in UI_WORDS:
                cons += 1          # ← 保守替代版会删的（本行及以下都在同一串行链路上）
            if len(s) >= 4 or re.search(r"\d", s):
                continue
            st["⑩"] += 1
            short_src[src or "<空>"] += 1
            prev = lines[k - 1].strip() if k else ""
            nxt = lines[k + 1].strip() if k + 1 < len(lines) else ""
            if prev or nxt:
                mid += 1
                mid_src[src or "<空>"] += 1
            else:
                iso += 1

        if c.clean(t0 or "") == t:
            st["⑪不变"] += 1

    m["步骤"] = dict(st)
    # 三堆：CJK 标点 / 空白 / 其它（按**待改字符**归类，不是按结果字符）
    for k in ("CJK", "空白", "其它"):
        def belongs(a: str, k: str = k) -> bool:
            if k == "CJK":
                return a in KW
            if k == "空白":
                return a in "　\xa0"
            return a not in KW and a not in "　\xa0"
        m.setdefault("NFKC分组", {})[k] = (
            sum(1 for (a, _) in nfkc_pairs if belongs(a)), nfkc_group[k])
    m["孤立"] = iso
    m["夹心"] = mid
    m["夹心按源"] = dict(mid_src)
    m["短行按源"] = dict(short_src)
    # 「白名单保留下来的」= NFKC 会改它、而白名单（只转全角数字/字母 + 空白归一）
    # **不动**它的那些字符的出现次数。**不能**只取 nfkc_group["CJK"]：`－`／`％`／`．`
    # 这些全角标点不在 KW 集合里，`Ⅱ`／`㎡` 又不在 FF 区，它们同样被白名单保留。
    m["白名单保留标点"] = sum(
        n for (a, _), n in nfkc_pairs.items() if a not in FW and a not in "　\xa0")
    m["保守短行删除"] = cons
    con.close()
    return m


def part_b(m: dict) -> None:
    print("\nB. 改动面实测（主库 %d 条，只读）" % m["分母"])
    print("=" * 74)
    st = m["步骤"]
    for k, want in [("①", 4), ("②", 0), ("③", 2371), ("④", 0), ("⑤", 40),
                    ("⑥", 1517), ("⑦", 509), ("⑧", 1283), ("⑨", 6),
                    ("⑩", 16528), ("⑪不变", 251), ("白名单改动", 1241)]:
        ok(st.get(k, 0) == want, f"步骤 {k} 命中 {want}", f"实测 {st.get(k, 0)}")
    ok(st.get("白名单与整段不同", 0) == 2371,
       "白名单与整段 NFKC 不同的条数 = 2,371（= ③）",
       f"实测 {st.get('白名单与整段不同', 0)}")
    g = m["NFKC分组"]
    ok(g["CJK"] == (9, 112038), "NFKC·CJK 标点 9 种 / 112,038 次", f"实测 {g['CJK']}")
    ok(g["空白"] == (2, 4424), "NFKC·空白 2 种 / 4,424 次", f"实测 {g['空白']}")
    ok(g["其它"] == (53, 1469), "NFKC·其它 53 种 / 1,469 次", f"实测 {g['其它']}")
    ok(m["孤立"] == 10706, "短行·孤立 10,706 行", f"实测 {m['孤立']}")
    ok(m["夹心"] == 5822, "短行·夹心 5,822 行", f"实测 {m['夹心']}")
    for src, want in [("esafety", 4020), ("safehoo", 1337),
                      ("cq_em", 182), ("ln_em", 87)]:
        got = m["夹心按源"].get(src, 0)
        ok(got == want, f"夹心·{src} {want} 行", f"实测 {got}")
    print("      短行按源（孤立+夹心全部）：" + ", ".join(
        f"{k}={v}" for k, v in sorted(m["短行按源"].items(),
                                      key=lambda kv: -kv[1])[:6]))
    ok(m["白名单保留标点"] == 112709, "白名单保留下来的 CJK 标点 112,709 次",
       f"实测 {m['白名单保留标点']}")
    ok(m["保守短行删除"] == 2569, "保守短行过滤删 2,569 行", f"实测 {m['保守短行删除']}")


# --------------------------------------------------------- C. 文档 ↔ 实测
#: (说明, 定位正则, 期望值, 来源) —— **必须定位到承载该数字的那一处**，
#: 不能用裸 token 查（§2.5.58 第四节：`16,484` 在别处出现照样命中，判据静默放行）。
NUM = int


def part_c(m: dict) -> None:
    print("\nC. 文档 §2.5.59 的数字 ↔ 步骤 B 的实测")
    print("=" * 74)
    doc = DOC.read_text(encoding="utf-8")
    st = m["步骤"]

    def num(s: str) -> int:
        return int(s.replace(",", ""))

    checks = [
        ("步骤表 ① unescape",
         r"\| ① `html\.unescape` \| (\d+) \|", st.get("①", 0)),
        ("步骤表 ② 零宽（0）",
         r"\| ② 去零宽字符（\d+ 个） \| \*\*(\d+)\*\* \|", st.get("②", 0)),
        ("步骤表 ③ NFKC",
         r"\| ③ \*\*`unicodedata\.normalize\('NFKC'\)`\*\* \| \*\*([\d,]+)\*\* \|",
         st.get("③", 0)),
        ("步骤表 ④ 实体 replace（0）",
         r"\| ④ URL 实体[^|]*\| \*\*(\d+)\*\* \|", st.get("④", 0)),
        ("步骤表 ⑤ 统一换行",
         r"\| ⑤ 统一换行[^|]*\| (\d+) \|", st.get("⑤", 0)),
        ("步骤表 ⑥ 压缩空白",
         r"\| ⑥ 压缩连续空白 \| ([\d,]+)（减 [\d,]+ 字符） \|", st.get("⑥", 0)),
        ("步骤表 ⑦ 压缩换行",
         r"\| ⑦ 压缩连续换行[^|]*\| (\d+)（减 [\d,]+ 字符） \|", st.get("⑦", 0)),
        ("步骤表 ⑧ strip",
         r"\| ⑧ 行首尾 `strip` \| ([\d,]+) \|", st.get("⑧", 0)),
        ("步骤表 ⑨ 噪声行（6 行）",
         r"\| ⑨ 噪声行正则（\d+ 条） \| (\d+) 行 \|", st.get("⑨", 0)),
        ("步骤表 ⑩ 短行过滤",
         r"\| ⑩ \*\*短行过滤[^|]*\*\* \| \*\*([\d,]+) 行\*\* \|", st.get("⑩", 0)),
        ("⑪ 清洗前后不变条数",
         r"完全不变的只有 (\d+) / ([\d,]+) 条", st.get("⑪不变", 0)),
        ("NFKC·CJK 标点次数",
         r"\| \*\*CJK 标点转半角\*\*[^|]*\| \d+ \| \*\*([\d,]+)\*\* \|",
         m["NFKC分组"]["CJK"][1]),
        ("NFKC·空白次数",
         r"\| 空白归一[^|]*\| \d+ \| ([\d,]+) \|", m["NFKC分组"]["空白"][1]),
        ("NFKC·其它次数",
         r"\| 其它[^|]*\| \d+ \| ([\d,]+) \|", m["NFKC分组"]["其它"][1]),
        ("短行·孤立行数",
         r"孤立短行（前后皆空）([\d,]+) 行", m["孤立"]),
        ("短行·夹心行数",
         r"夹心短行（前或后有内容）([\d,]+) 行", m["夹心"]),
        ("夹心·esafety", r"`esafety` 4,(\d+) ／", m["夹心按源"].get("esafety", 0)),
        ("白名单 NFKC 改动条数",
         r"\| 白名单版 \| \*\*([\d,]+)\*\* \|\n\| 差额 = 保留下来的 CJK 标点",
         st.get("白名单改动", 0)),
        ("白名单保留的 CJK 标点",
         r"\| 差额 = 保留下来的 CJK 标点 \| \*\*([\d,]+) 次\*\* \|",
         m["白名单保留标点"]),
        ("保守短行过滤删行数",
         r"\| 白名单版 \| \*\*([\d,]+)\*\* \|\n\| 差额 = 超出白名单",
         m["保守短行删除"]),
    ]
    for label, pat, want in checks:
        mm = re.search(pat, doc)
        if not mm:
            ok(False, f"{label}：**文档里找不到该处**", pat)
            continue
        got = num(mm.group(1)) if "esafety" != label.split("·")[-1] else num("4" + mm.group(1))
        ok(got == want, f"{label} = {want}", f"文档 {got}")

    # 源码形态描述（文档 A 段那几句）
    ok("去零宽字符（8 个）" in doc, "文档写明零宽字符 8 个")
    ok("11 条" in doc, "文档写明噪声正则 11 条")
    ok("`len<4 且无数字 → 删`" in doc or "`len(stripped) < 4 and not re.search(r'\\d'" in doc,
       "文档写明 ⑩ 的阈值形态")


# --------------------------------------------------------- D. 漂移自证
def drift_test() -> int:
    """往被测对象注入漂移，逐个断言退出码转 1。

    **不做这一步，「退出码 0」只说明脚本没崩，不说明它有鉴别力。**
    注入面只覆盖被测判据（文档数字 / 源码形态描述），不碰主库。
    """
    import subprocess
    script = str(Path(__file__).resolve())

    def run() -> int:
        return subprocess.run([sys.executable, script],
                              capture_output=True).returncode

    if run() != 0:
        print("\n❌ 基线不是 0，漂移测试无从谈起")
        return 1

    DRIFT = [
        ("步骤表 ⑩ 16,528 → 16,529",
         r"(\| ⑩ \*\*短行过滤[^|]*\*\* \| \*\*16,)528( 行\*\* \|)", r"\g<1>529\g<2>"),
        ("短行·孤立 10,706 → 10,707",
         r"(孤立短行（前后皆空）10,)706( 行)", r"\g<1>707\g<2>"),
        ("步骤表 ③ 2,371 → 2,372",
         r"(\| ③ \*\*`unicodedata\.normalize\('NFKC'\)`\*\* \| \*\*2,)371(\*\* \|)",
         r"\g<1>372\g<2>"),
        ("NFKC·CJK 112,038 → 112,039",
         r"(\| \*\*CJK 标点转半角\*\*[^|]*\| 9 \| \*\*112,)038(\*\* \|)", r"\g<1>039\g<2>"),
        ("白名单保留标点 112,709 → 112,710",
         r"(\| 差额 = 保留下来的 CJK 标点 \| \*\*112,)709( 次\*\* \|)", r"\g<1>710\g<2>"),
        ("保守短行 2,569 → 2,570",
         r"(\| 白名单版 \| \*\*2,)569(\*\* \|\n\| 差额 = 超出白名单)", r"\g<1>570\g<2>"),
        ("零宽字符数描述 8 → 9",
         r"(去零宽字符（)8( 个）)", r"\g<1>9\g<2>"),
    ]
    print("\nD. 漂移自证（往文档注入，逐个断言退出码转 1）")
    print("=" * 74)
    orig = DOC.read_text(encoding="utf-8")
    bad = 0
    try:
        for label, pat, repl in DRIFT:
            new = re.sub(pat, repl, orig, count=1)
            if new == orig:
                print(f"  ❌ {label}：**替换没落地**，用例静默失效")
                bad += 1
                continue
            DOC.write_text(new, encoding="utf-8")
            rc = run()
            print(f"  {'✅' if rc == 1 else '❌'} {label} → 退出码 {rc}")
            if rc != 1:
                bad += 1
    finally:
        DOC.write_text(orig, encoding="utf-8")
        if DOC.read_text(encoding="utf-8") != orig:
            print("  ❌ **还原不一致**")
            bad += 1
    ok(bad == 0, f"漂移用例全部被捕获（{len(DRIFT) - bad}/{len(DRIFT)}）")
    return bad


def main() -> int:
    print("§2.5.59 清洗管道核验：规则形态 + 改动面 ↔ 文档")
    print("=" * 74)
    if not DOC.exists():
        print(f"❌ 文档不在：{DOC}")
        return 1
    part_a()
    m = measure()
    part_b(m)
    part_c(m)
    print("\n" + "=" * 74)
    if fails:
        print(f"❌ 共 {len(fails)} 项不符：")
        for f in fails:
            print(f"   · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    if "--drift-test" in sys.argv:
        sys.exit(drift_test())
    sys.exit(main())
