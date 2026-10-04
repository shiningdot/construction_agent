#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§2.5.79 文档样例的值与类型面：`"size": 50` 与 `"size": "50"` 分不分得开。

## 来路

`docs/web/09_端到端对齐核查.md` §10.5 三条「比对过、但没比出来的」，第一条
（层级归属）已由 §2.5.78 结掉，这是第二条。`check_api_contract.py` 的
「不覆盖什么」里明写着：

    **值与类型不比**。只比字段名在不在——`"size": 50` 与 `"size": "50"`
    在这层看着一样

## 结这条的两个动作

**一、把「值与类型」拆成两件事**——类型能比，值不能比。

样例里的值是**示意值**。`"accidents": 2623` 那个 2623 今天恰好等于真值，可它
是写作那天抄的，不是契约的一部分；明天库里多一条事故这个数就该变，**文档没有
义务跟**。把示意值当判据核，等于要求文档每改一次数据就重抄一遍——那不是契约，
那是快照，而且是会过期的快照。§2.5.77 立过的规矩在这里同样成立：**判据在多数
样本上恒真就等于没有**。

类型不一样。`"count": 12` 与 `"count": "12"` 是**两种形状**：前端拿到 `"12"`
再 `+ 1` 会拼成 `"121"`。类型属于契约，且**不随数据变**。

**二、值面留下唯一能核的一格**：`ok`。它是布尔**断言**不是数据——文档画
`"ok": true`，说的是「这个接口成功时给 true」，而它永远不该是 false（false
意味着失败，那时要么状态码不该是 200、要么得有个错因字段）。所以 `ok` 的值
可核。本节单列一档。

## 判据：三档硬 + 两档定量

| 档 | 判据 | 抓什么 | 红？ |
|---|---|---|---|
| A1 | 文档声明的类型（剔除 null/占位）须 ∈ 真实类型集合（剔除 null） | 形状错（文档说 number、实现给 string） | 红 |
| A2 | 文档**同一路径声明了不止一种**类型 | 文档自己前后矛盾 | 红，且**优先于 A1** |
| A3 | 真实响应里名为 `ok` 的路径，取值须恒为 `true` | 失败被当成成功（HTTP 200 + `ok:false`） | 红 |
| B1 | 文档只写 `null`/占位 ⇒ **无从比对** | ——（豁免面，必须量化） | 定量 |
| B2 | 真实含 null 而文档只声明了具体类型 | 文档没标可空 | 定量 |

**互斥设计**（承 §2.5.78 的教训）：A2 命中即**跳过** A1——否则「文档给了两种
类型」会同时触发两档，红的集合分不清是哪条判据抓到的。B1 与 A1/A2 也互斥
（声明为空就无从比）。于是每条公共路径**恰好**归入 A1/A2/B1 之一。

## B2 为什么不算错

文档样例画的是**一条有数据的记录**。`contractor` 在真实里可能是 null，文档画
null 或画字符串都说得通——画 null 是「这字段可能没有」，画字符串是「有值时长
这样」。本节只把差额**量出来**（前端要防的空值面），不判红：要求文档给 48 个
字段逐个标可空性，是另一种文档体例，不在这份蓝图里。

## 判据边界（写清它管不到什么）

- **核的是"文档声明的类型在真实里出现过没有"，不是"真实只有这一种类型"**。
  真实同时给 number 与 null 时，文档声明 number 就算通过——"可空"归 B2 提示，
  不归 A1 判错。
- **样本依赖**：真实侧类型是**每个接口单打一次**的并集（带 `<id>` 的路由取库里
  第一条事故，与 `check_2578_response_shape.measure()` 同一份实测）。某个字段在
  这条样本上恰好是 null，就展不出类型（实测 22 条这样的路径，A 段列出基数
  分布）。**单次采样让判据更严而不是更松**——真实类型集合越小，`dec ⊆ rts`
  越难过。但它仍然只是采样面，不是全库断言。
- **`ok` 那一档的作用面很窄**（十几条接口），且今天全绿。它的价值不在今天，
  在于实现某天把 `ok` 写成 false 而 HTTP 仍是 200 时能抓住。
- **值面除 `ok` 外一概不判**——理由见上。别把它读成「值已经核过了」。

## 四段

    A. 实测：公共路径 / 声明面 / 豁免面 / 真实类型稳定性
    B. 三档硬判据 + 两档定量 + 互斥闭合
    C. 注入式自证：源码面 2 例 + 文档面 2 例，断言红的**恰好**是哪几项
    D. 文档 ↔ 实测

口径与 §2.5.78 同界：`app.test_client()`，只打 GET 且返回 JSON 200 的路由。
**实测层直接复用 `check_2578_response_shape.py` 的 `measure()`**——同一份响应
遍历出路径/容器/类型/布尔取值四样东西，在两个脚本里各写一遍遍历只会制造
分叉（§2.5.76）。本脚本只换判据，不碰实测。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import check_2578_response_shape as S                    # noqa: E402

DOC = S.DOC                                   # 01 号：本节正文写这儿
DOC4 = S.ROOT / "docs" / "web" / "04_展示_概览与检索.md"   # 文档面注入的锚点在这儿
COLLECT = S.COLLECT
REVIEW = S.REVIEW
PY = S.PY

#: 值面唯一可核的键。判据是「真实给的值恒为 True」。
ASSERT_KEYS = {"ok"}

#: 文档面锚点（D 段）
DOC_NEEDLES = ["值与类型"]


# ══════════════════════════════════════════════════════════════════
# 判据
# ══════════════════════════════════════════════════════════════════
def compare(m: dict) -> dict:
    """三档硬判据 + 两档定量。返回可 JSON 化的报告。"""
    a1: list[str] = []          # 文档声明的类型，真实里没有
    a2: list[str] = []          # 文档同一路径声明了不止一种类型
    a3: list[str] = []          # `ok` 给了 false
    b1: list[str] = []          # 文档只写 null/占位 ⇒ 无从比对
    b2: list[str] = []          # 真实含 null 而文档只声明了具体类型
    passed = 0                  # 声明了一种类型且真实里有（判据通过，不记录明细）
    seen = 0
    for key, rv in sorted(m["real"].items()):
        dt = m["doc_types"].get(key, {})
        rt = rv["types"]
        for path, dts in sorted(dt.items()):
            if path not in rt:
                continue                    # 路径差异归 §2.5.78，本节不重复判
            seen += 1
            dec = set(dts) - {"null", "placeholder"}
            if not dec:
                b1.append(f"{key} {path} 文档只写 {sorted(dts)}")
                continue
            if len(dec) > 1:
                a2.append(f"{key} {path} 文档声明 {sorted(dec)}")
                continue                    # 互斥：A2 命中即跳过 A1
            rts = set(rt[path]) - {"null"}
            if not dec <= rts:
                a1.append(f"{key} {path} 文档声明 {sorted(dec)}"
                          f"，真实给 {sorted(rt[path])}")
                continue
            passed += 1
            if "null" in rt[path]:
                b2.append(f"{key} {path} 文档声明 {sorted(dec)}，真实含 null")
        # A3：值面。只看真实侧叫 `ok` 的路径——文档侧的存在性归
        # `check_api_contract.py` 管，这里管的是它给了什么值。
        for path, vals in sorted(rv["bools"].items()):
            if path.split(".")[-1] in ASSERT_KEYS and False in vals:
                a3.append(f"{key} {path} 真实给 {sorted(vals)}")

    rep = {"a1": a1, "a2": a2, "a3": a3, "b1": b1, "b2": b2,
           "passed": passed, "seen": seen}
    rep["exit"] = 1 if (a1 or a2 or a3) else 0
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


def part_a(m: dict, rep: dict) -> None:
    print("\n═══ A. 实测 ═══")
    print(f"  有样例且打成 JSON 200 的接口 {len(m['real'])} 条"
          f"，两侧公共路径 {rep['seen']} 条")
    print(f"  文档**声明了类型**的公共路径 {rep['seen'] - len(rep['b1'])} 条"
          f"（A1/A2 的作用面）")
    print(f"  文档**只写 null/占位**（无从比对）{len(rep['b1'])} 条"
          f"（占 {len(rep['b1']) / max(rep['seen'], 1):.1%}）")

    # 真实侧类型是否稳定：基数 >1 的格子只能判「⊆」不能判「==」
    card: dict[int, int] = {}
    for rv in m["real"].values():
        for ts in rv["types"].values():
            card[len(set(ts) - {"null"})] = card.get(len(set(ts) - {"null"}), 0) + 1
    print(f"  真实侧路径的类型集合基数（剔除 null 后）："
          f"{dict(sorted(card.items()))}——基数 >1 的格子类型不稳定，"
          f"基数 0 的这次采样里只有 null、展不出类型")
    odd = sorted({t for rv in m["real"].values() for ts in rv["types"].values()
                  for t in ts if t.startswith("other:")})
    print(f"  非 JSON 类型（不该有）：{odd or '无'}")

    oks = sorted(f"{k} {p}" for k, rv in m["real"].items()
                 for p in rv["bools"] if p.split(".")[-1] in ASSERT_KEYS)
    print(f"  真实侧叫 `ok` 的路径 {len(oks)} 条（A3 的作用面）：{oks[:6]}"
          + ("…" if len(oks) > 6 else ""))


def part_b(m: dict, rep: dict) -> None:
    print("\n═══ B. 三档硬判据 ═══")
    ok(not rep["a1"], "A1 类型面：文档声明的类型在真实里都出现过",
       "" if not rep["a1"] else str(rep["a1"][:3]))
    ok(not rep["a2"], "A2 文档自洽：没有一条路径被声明成两种类型",
       "" if not rep["a2"] else str(rep["a2"][:3]))
    ok(not rep["a3"], "A3 值面：真实响应的 `ok` 恒为 true",
       "" if not rep["a3"] else str(rep["a3"][:3]))

    print("\n═══ B'. 两档定量（不判红）═══")
    print(f"  B1 文档只写 null/占位 ⇒ 无从比对：{len(rep['b1'])} 条")
    for s in rep["b1"][:6]:
        print(f"    · {s}")
    if len(rep["b1"]) > 6:
        print(f"    …另有 {len(rep['b1']) - 6} 条")
    print(f"  B2 真实含 null 而文档只声明了具体类型（前端要防的空值面）："
          f"{len(rep['b2'])} 条")
    for s in rep["b2"][:10]:
        print(f"    · {s}")
    if len(rep["b2"]) > 10:
        print(f"    …另有 {len(rep['b2']) - 10} 条")

    tot = len(rep["a1"]) + len(rep["a2"]) + len(rep["b1"]) + rep["passed"]
    ok(tot == rep["seen"],
       f"互斥闭合：A1+A2+B1+通过 = {rep['seen']} 条公共路径（每条恰好归一档）",
       f"实际 {len(rep['a1'])}+{len(rep['a2'])}+{len(rep['b1'])}"
       f"+{rep['passed']}={tot}")


def _run_diff() -> dict:
    p = subprocess.run([PY, __file__, "--diff"], capture_output=True, text=True)
    line = [l for l in p.stdout.splitlines() if l.startswith("@@JSON@@")]
    if not line:
        raise RuntimeError(f"--diff 没有输出 JSON：{p.stdout[-500:]}{p.stderr[-500:]}")
    return json.loads(line[0][len("@@JSON@@"):])


def _key_of(s: str) -> str:
    """明细行 → 接口名。`GET /api/xxx path …` 取前两段。"""
    return " ".join(s.split()[:2])


def _red_set(rep: dict) -> dict[str, list[str]]:
    """红集合，**档 × 接口**粒度（明细条数随注入规模而变，断言用接口级才稳定）。

    B1/B2 是定量档、不参与退出码，故不进红集合——它们若被算进来，"恰好命中"
    的断言就与退出码脱钩了。
    """
    out: dict[str, list[str]] = {}
    for k in ("a1", "a2", "a3"):
        if rep[k]:
            out[k] = sorted({_key_of(s) for s in rep[k]})
    return out


def part_c() -> None:
    print("\n═══ C. 注入式自证（红的恰好是哪几项）═══")
    # 源码面两例：一例动类型（A1）、一例动取值（A3）。
    cases = [
        ("实现把 `total_raw` 的 number 包成字符串（类型错位）", COLLECT,
         '            "total_raw": r.get("total_raw") or 0,\n',
         '            "total_raw": str(r.get("total_raw") or 0),\n',
         {"a1": ["GET /api/collect/sources"]}),
        ("实现把 `ok` 写成 false 而 HTTP 仍是 200（失败当成功）", REVIEW,
         '    return jsonify({"ok": True, "data": funnel_payload()})',
         '    return jsonify({"ok": False, "data": funnel_payload()})',
         {"a3": ["GET /api/review/stats"]}),
    ]
    for label, path, old, new, want in cases:
        with S._with_file(path, old, new):
            got = _red_set(_run_diff())
        ok(got == want, f"{label} → 红集合精确命中",
           f"实际 {sorted(got)}" if got != want else "")

    # 文档面两例：一例动类型（A1）、一例让同一路径有两种声明（A2）。
    # **文档面必须自证**：只做源码面的话，「判据真的在读文档侧的类型」这件事
    # 一次都没被验证过——判据可能只看了真实侧就报绿。
    # 锚点在 **04 号**（`/api/overview` 的样例住那儿），不是本节要写的 01 号。
    dcases = [
        ("文档把 `accidents` 的 number 写成字符串", DOC4,
         '"accidents": 2623,', '"accidents": "2623",',
         {"a1": ["GET /api/overview"]}),
        # 触发 A2 要让**同一路径**同时有两种声明：`severity.key` 本来是
        # `"一般事故"`（string）+ `null`（占位），把那个 null 换成数字，声明
        # 就变成 {string, number} 两种。**不能反过来把 `"一般事故"` 改成数字**
        # ——那样只是把 string 换成 number、仍是一种，会落到 A1 去（首版这么写
        # 过，被"红的恰好是哪几条"这条断言抓出来了）。
        ("文档把同一路径 `severity.key` 声明成两种类型", DOC4,
         '{"key":null,"count":1}', '{"key":1889,"count":1}',
         {"a2": ["GET /api/overview"]}),
    ]
    for label, path, old, new, want in dcases:
        with S._with_file(path, old, new):
            got = _red_set(_run_diff())
        ok(got == want, f"{label} → 红集合精确命中",
           f"实际 {sorted(got)}" if got != want else "")


def part_d(m: dict, rep: dict) -> None:
    print("\n═══ D. 文档 ↔ 实测 ═══")
    txt = DOC.read_text(encoding="utf-8")
    for needle in DOC_NEEDLES:
        ok(needle in txt, f"01 号文档里有锚点「{needle[:22]}」")
    stat = {
        "公共路径": rep["seen"],
        "文档声明类型的路径": rep["seen"] - len(rep["b1"]),
        "文档只写 null 或占位": len(rep["b1"]),
        "类型矛盾的路径": len(rep["a1"]),
    }
    for label, n in stat.items():
        mm = S.re.search(r"\|\s*" + S.re.escape(label)
                         + r"\s*\|\s*\*\*([\d,]+)\*\*\s*\|", txt)
        ok(bool(mm) and int(mm.group(1).replace(",", "")) == n,
           f"文档表里「{label}」= {n}",
           f"文档写的是 {mm.group(1) if mm else '（没找到该行）'}")


def main() -> int:
    if "--diff" in sys.argv:
        rep = compare(S.measure())
        print("@@JSON@@" + json.dumps(rep, ensure_ascii=False))
        return rep["exit"]

    m = S.measure()
    rep = compare(m)
    part_a(m, rep)
    part_b(m, rep)
    part_c()
    part_d(m, rep)

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
