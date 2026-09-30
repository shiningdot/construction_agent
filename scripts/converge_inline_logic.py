#!/usr/bin/env python3
"""把散落在旧批处理脚本里的内联 evidence_quality / sort_order 收敛到共享出口。

## 背景

`construction_agent/extraction/evidence_quality.py` 是三级证据等级与编码排序号的
**唯一计算出口**。但主仓库这 4 个脚本各自内联了一份，且逐字相同：

    batch_runner.py:79-82      process_pdf.py:73-76
    process_pdf3.py:55-58      process_ebook800.py:63-66

它们**不产生错误数据**——拿主库 16,323 条实测，两种实现结论完全一致：

  · 证据等级：16,323 / 16,323 相同，差异 0 条
  · 排序号：主库现存 19 种码（C1 C2 F1-F4 H1-H4 M1-M5 T1-T4）上 0 条不同

但内联的 `order` 比共享出口脆：它写的是 `int(code[1])`，**只取第 2 个字符**——
遇到 `M12` 算成 102（共享出口给 112），遇到单字符码给 0（共享出口给 100），
遇到 `Mx` 这种非数字直接抛 ValueError。今天不出错，只是因为码表恰好全是
"字母 + 单数字"。

所以这是一次**行为等价重构**：消除重复，顺带把未来的坑填上。

（严格说，等价性建立在非 NULL 输入上：内联版 `"推断" in c2.inference` 遇到
NULL 会抛 TypeError，共享出口有 `or ""` 兜底。主库这两列零 NULL，实测
16,323 条结论一致；新版对 NULL 更宽容，这是改进不是回归。）

## 顺带发现：这 5 个脚本都有"重跑翻倍"隐患（本工具不处理）

改这 5 个文件时注意到，它们写 `caics_causes` 都没有"先清后写"：

    run_batch.py      裸 INSERT，全文零条 DELETE
    另外 4 个          INSERT OR REPLACE

`INSERT OR REPLACE` 听着安全，但 `caics_causes` **没有 UNIQUE 约束**——
主库实测 16,323 行、零索引、278 组同 (accident_id,code) 多行，而
`docs/项目文件地图.md` 所记"同码多条不同证据是真实标注"，不能加约束。
没有约束就没有冲突可替换，`OR REPLACE` 退化成普通 INSERT。
于是 5 个脚本重跑同一个 accident，编码都会翻倍，且**静默无报错**。

主库目前**零条**四列全同（accident_id/code/evidence/inference）的行，
说明这个隐患至今没被触发。`scripts/run_batch_pipeline.py` 已在 2c64b2f 补上
"先清后写"，这 5 个还没有——改不改、按覆盖还是追加语义，留给作者决定，
所以本工具只做 eq/order 的等价替换，不碰写入语义。

## 用法

    python3 scripts/converge_inline_logic.py --check       # 只报告，不改
    python3 scripts/converge_inline_logic.py --fix         # 备份后原地修复

`--fix` 前先做**共享出口预检**：从 `--dir` 逐级向上找
`construction_agent/extraction/evidence_quality.py`，找不到就拒绝改写——否则脚本
会被改成 `ImportError`（本工具在 worktree，待改脚本在主仓库，这正是会踩的坑）。
确认要强行改写用 `--force`。

预检通过后，先写 `.bak.<时间戳>`，改完用 `py_compile` 校验语法；任一文件失败就回滚该文件。
"""

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 三个 eq 赋值行。空白一律放宽：这 5 份复制品并非逐字相同——
# run_batch.py 的 order 那行写的是 `{"M": 100, ...}`（冒号后有空格），
# 而另外 4 份是 `{"M":100,...}`。严格逐字匹配会漏掉它，只改到 eq、
# 留下 order 内联，正好是最糟的"半改"状态。
_EQ_BLOCK = re.compile(
    r'^[ \t]*eq\s*=\s*"direct_quote"\n'
    r'[ \t]*if\s+"推断"\s+in\s+c2\.inference\s+or\s+"表明"\s+in\s+c2\.inference'
    r'\s*:\s*eq\s*=\s*"indirect_infer"\n'
    r'[ \t]*if\s+"原文未直接"\s+in\s+c2\.evidence\s+or\s+"原文未直接"\s+in\s+c2\.inference'
    r'\s*:\s*eq\s*=\s*"weak_infer"\n',
    re.M,
)
# order 赋值行。语义 token 全部保留（字典五档、.get(code[0],600)、
# int(code[1])），只放宽空白——避免把写法不同的实现误判成同一份。
_ORDER_LINE = re.compile(
    r'^[ \t]*order\s*=\s*\{\s*"M"\s*:\s*100\s*,\s*"F"\s*:\s*200\s*,\s*'
    r'"T"\s*:\s*300\s*,\s*"H"\s*:\s*400\s*,\s*"C"\s*:\s*500\s*\}'
    r'\.get\(\s*c2\.code\[0\]\s*,\s*600\s*\)'
    r'\s*\+\s*int\(\s*c2\.code\[1\]\s*\)'
    r'[^\n]*\n',
    re.M,
)

_ANCHOR = ("from construction_agent.extraction.step2_caics_encoder "
           "import Step2CaicsEncoder\n")
_IMPORT = ("from construction_agent.extraction.evidence_quality import (\n"
           "    compute_evidence_quality, compute_sort_order)\n")

SELF = Path(__file__).resolve()


def _indent_of(match: re.Match, text: str) -> str:
    """取匹配块首行的缩进。"""
    line_start = text.rfind("\n", 0, match.start()) + 1
    return re.match(r"[ \t]*", text[line_start:]).group()


def rewrite(src: str) -> tuple[str, list[str]]:
    """返回 (改写后源码, 动作清单)。无内联则原样返回。"""
    actions: list[str] = []
    out = src

    m = _EQ_BLOCK.search(out)
    if m:
        ind = _indent_of(m, out)
        out = out[:m.start()] + f"{ind}eq = compute_evidence_quality(c2.evidence, c2.inference)\n" + out[m.end():]
        actions.append("eq 三行 → compute_evidence_quality()")

    m = _ORDER_LINE.search(out)
    if m:
        ind = _indent_of(m, out)
        out = out[:m.start()] + f"{ind}order = compute_sort_order(c2.code)\n" + out[m.end():]
        actions.append("order 内联字典 → compute_sort_order()")

    if actions and "compute_evidence_quality" not in out.split(_ANCHOR)[0]:
        if _ANCHOR not in out:
            raise SystemExit("✗ 找不到 import 锚点（Step2CaicsEncoder 那行），中止")
        out = out.replace(_ANCHOR, _ANCHOR + _IMPORT, 1)
        actions.append("补 import")

    return out, actions


def _preflight(root: Path) -> str | None:
    """目标仓库里有没有共享出口？

    改写会把内联逻辑换成
    `from construction_agent.extraction.evidence_quality import ...`。
    若那个仓库里还没有这个模块，改写等于把能跑的脚本改成 ImportError——
    本工具诞生于 worktree，而待改的脚本住在主仓库，两边不同步时正是这个局面。
    `py_compile` 只查语法，查不出 import 名不存在，所以这道检查必须单独做。

    从 --dir 逐级向上找挂有 construction_agent/extraction/ 的目录，
    到仓库根（有 .git 的那层）还没找到就报缺失。
    """
    rel = Path("construction_agent") / "extraction" / "evidence_quality.py"
    for base in [root, *root.parents]:
        if (base / rel).exists():
            return None
        if (base / ".git").exists():
            return f"✗ 仓库 {base} 里没有 {rel}"
    return f"✗ 在 {root} 及其上级目录里都没找到 {rel}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="只报告不改（默认行为，显式写出来便于对照）")
    ap.add_argument("--fix", action="store_true", help="备份后原地修复")
    ap.add_argument("--force", action="store_true",
                    help="跳过共享出口预检（仅用于隔离环境自测）")
    ap.add_argument("--dir", default=str(SELF.parent), help="脚本目录")
    args = ap.parse_args()

    root = Path(args.dir)
    targets = sorted(p for p in root.glob("*.py") if p.resolve() != SELF)

    pending: list[tuple[Path, str, list[str]]] = []
    for p in targets:
        new, actions = rewrite(p.read_text(encoding="utf-8"))
        if actions:
            pending.append((p, new, actions))

    for p, _, actions in pending:
        print(p.name)
        for a in actions:
            print(f"    · {a}")

    if not pending:
        print("没有发现内联实现——已经收敛过了。")
        return 0

    print()
    if not args.fix:
        print(f"发现 {len(pending)} 个文件仍有内联实现。加 --fix 修复。")
        return 0

    blocker = _preflight(root)
    if blocker and not args.force:
        print(blocker)
        print("  改完这些脚本会 import 不到共享出口。先把 evidence_quality.py "
              "同步到该仓库（主仓库需先 merge worktree-classifier-mod），"
              "确认无误再加 --force。")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    done = 0
    for p, new, _ in pending:
        bak = p.with_suffix(p.suffix + f".bak.{stamp}")
        shutil.copy(p, bak)
        p.write_text(new, encoding="utf-8")
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            shutil.copy(bak, p)
            print(f"    ✗ {p.name} 语法校验失败，已回滚：{e}")
            continue
        done += 1
        print(f"    ✓ {p.name} 已改写（备份 {bak.name}），语法校验通过")

    print(f"\n处理了 {done}/{len(pending)} 个文件。建议再跑一次 --check 确认，"
          f"并用 `diff -u <bak> <file>` 复核改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
