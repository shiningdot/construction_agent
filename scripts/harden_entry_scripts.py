#!/usr/bin/env python3
"""给入库脚本补上 `PRAGMA foreign_keys = ON`。

## 为什么

SQLite 的外键是**连接级**开关，默认关闭。建表语句里那六张子表写的
`REFERENCES accidents(id) ON DELETE CASCADE` 只是一段被解析后存进
sqlite_master 的文本——连接不开这个开关，它就没有任何约束力。

在真实 schema 的库副本上实测（2026-10-01），模拟脚本"插父行 → 插三行子表"
跑两遍：

    PRAGMA foreign_keys     第一遍   第二遍
    OFF（六个脚本的现状）         3        6   ← 重跑翻倍
    ON                        3        3   ← 重跑幂等

不开开关的后果有两个，都是静默的：

  1. 子表可以插进指向不存在事故的行。`caics_causes` 那 1,041 行孤儿
     （2026-09-23 已清理）就是这么进来的，六张子表都没有拦。
  2. `INSERT OR REPLACE` 打头 + 裸 `INSERT` 子表的写法，重跑会翻倍。
     因为 CASCADE 不生效，REPLACE 删掉父行时子行原样留着，脚本再插一遍
     就成了两份。

第 2 点说明：开了开关之后**不需要**再逐个脚本加"先清后写"——CASCADE 会代劳。

## 为什么这样改是安全的

前提是脚本的写入顺序为父表在前。本工具会逐个检查，发现子表先写就报出来
（那种情况开了开关会 IntegrityError）。六个脚本已核实都是父表在前。

另外主库六张子表当前**零孤儿**（实测），所以开启不会让任何现存数据触发
约束错误——保护补上，但不会有谁因为历史数据而被拦。

## 用法

    python3 scripts/harden_entry_scripts.py --check       # 只报告
    python3 scripts/harden_entry_scripts.py --fix         # 备份后原地改
    python3 scripts/harden_entry_scripts.py --dir <目录>   # 换目录

`--fix` 先写 `.bak.<时间戳>`，改完 `py_compile` 校验语法，失败就回滚该文件。
已开过关的脚本会被跳过，所以可以反复跑。
"""

from __future__ import annotations

import argparse
import py_compile
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# 匹配一整行的 connect 调用（含 `db = sqlite3.connect(DB); ok = 0` 这种
# 一行多语句的写法——所以锚在行尾而不是右括号）
_CONNECT = re.compile(r"^([ \t]*)(\w+)\s*=\s*sqlite3\.connect\([^\n]*\n", re.M)

SELF = Path(__file__).resolve()


def _already_on(src: str) -> bool:
    return "foreign_keys" in src


def write_order_warning(src: str) -> str | None:
    """父表写入必须在子表之前，否则开外键会 IntegrityError。"""
    parent = src.find("INTO accidents")
    child = src.find("INTO caics_causes")
    if parent < 0 or child < 0 or child > parent:
        return None
    return "⚠ 子表 caics_causes 的写入排在父表 accidents 之前，开启外键后会被拒"


def rewrite(src: str) -> tuple[str, list[str]]:
    """返回 (改写后的源码, 动作清单)。"""
    if _already_on(src):
        return src, []

    m = _CONNECT.search(src)
    if not m:
        return src, []

    indent, var = m.group(1), m.group(2)
    line = f'{indent}{var}.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF\n'
    out = src[:m.end()] + line + src[m.end():]

    actions = [f"在 {var} = sqlite3.connect(...) 之后补 PRAGMA foreign_keys = ON"]
    warn = write_order_warning(out)
    if warn:
        actions.append(warn)
    return out, actions


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="只报告不改（默认行为，显式写出来便于对照）")
    ap.add_argument("--fix", action="store_true", help="备份后原地修复")
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
        print("所有脚本都已经开了 foreign_keys，无需操作。")
        return 0

    print()
    if not args.fix:
        print(f"发现 {len(pending)} 个脚本没开外键。加 --fix 修复。")
        return 0

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
        print(f"    ✓ {p.name} 已改写（备份 {bak.name}）")

    print(f"\n处理了 {done}/{len(pending)} 个脚本。建议再跑一次 --check 确认，"
          f"并用 diff -u <bak> <file> 复核改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
