#!/usr/bin/env python3
"""响应契约核查：蓝图里画的响应长什么样，实现真的长那样吗。

已有的两个核查各管一头，中间空着一格：

| 脚本 | 比的是 | 管到哪一层 |
|---|---|---|
| `check_field_alignment.py` | 前端读的 `.属性` ↔ 后端返回的字段 | 代码内部两边 |
| `check_blueprint_alignment.py` | 蓝图标题的接口 ↔ 代码注册的路由 | 文档 ↔ 代码，但只到**路径** |
| **本脚本** | 蓝图里的响应样例 ↔ 真实响应的键名 | 文档 ↔ 代码，**字段**这一层 |

路径对上、字段在代码内部也自洽，但**文档写了 `images_available`、实现没返回**，
前两个都看不见——第一个只问前端有没有人读它，第二个只问路由在不在。

## 判据

**文档侧**：蓝图里 `### ``GET /api/…``` 标题下的 ```json 块，块内含 `"ok"` 的
算**响应样例**。这一条就够把请求体撇开——本项目响应一律 `{ok, data}`，请求体
不带 `ok`，于是 `POST /api/review/promote` 那种同时画了请求与响应的段落自动分开。

只认**标题行**：正文里提到 `/api/process` 是在说「旧实现怎么错」，那不算规定
（同一个坑 `check_blueprint_alignment.py` 踩过）。`##` 级标题会把归属清空，免得
上一节的接口一直往下接管。

**实现侧**：起 app 打真实请求，把响应里出现的**键名**（递归，取 basename）收成
集合。`/api/accidents/<id>` 多打几条——那是条件字段最多的地方，单打一条会把
「这篇有天气、那篇没有」的字段误报成缺口。

## 报什么

- **文档写到、响应没有** —— 硬缺口，退出码 1。要么实现漏了，要么文档过时
- **响应里有、文档没写** —— 文档不全，只提示。这类不影响运行，但下一轮读文档
  的人会以为没有这个字段
- **未判定** —— 分两种，都不算缺口：① **整个接口展不开**（工作库未建、本次响应
  所有列表都空）② **字段住在空数组里**（文档把 `token` 画在 `promote_history`
  里，而这次 `promote_history` 返回 `[]`）。前者的判据是「全空」，后者要求
  该字段归属的数组**全**是空的——只要有一个非空，元素结构就摆在那儿，照记
  硬缺口

## 不覆盖什么

- **层级归属比不出来**。只比**键名**，不比路径：文档把 `data` 里的键平铺写在
  顶层，脚本照样判「文档写了」。`data` 只装查询结果这条约定得靠人核
  （见 `00_总体架构与数据层.md` §4.1）
- **POST 接口不打**。它们会写库，`check_api_contract` 只读不写。这类如实列在
  「未比对」里，别当成已核对
- **工作库不可用时不比**。采集 / 提取 / 审核三组的表在空工作库里是 0 行，响应的
  元素字段展不出来，比了全是假缺口。脚本会看 `available`，缺就跳过并说明
- **值与类型不比**。只比字段名在不在——`"size": 50` 与 `"size": "50"` 在这层看着
  一样
- **规范示意块不参与比对**。块里画着 `<载荷>` 这类占位符的（00 §4.1 的统一响应
  结构就是），是「形状长这样」的意思，本来就不属于某个接口。脚本分开列出来，
  不混进「归属不明」

## 用法

    .venv/bin/python3 scripts/check_api_contract.py
    .venv/bin/python3 scripts/check_api_contract.py --verbose

指向测试工作库能多比对几个接口（采集 / 提取 / 审核三组）：

    .venv/bin/python3 scripts/check_api_contract.py \
        --staging-db .claude/worktrees/classifier-mod/data/caiems_staging.db

不起 HTTP 服务，用 `app.test_client()` 直接打，退出码 `0` = 没有硬缺口，
`1` = 有（可直接接 CI）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_db_path, main_repo_root              # noqa: E402

DOCS = ROOT / "docs" / "web"

#: 接口标题：`### `GET /api/xxx`` 或 `### GET /api/xxx`。只认 # 开头的行。
CALL_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE)\s+(/api/[A-Za-z0-9_\-/<>.{}$\[\]]*)")
#: JSON 里的键名。值里的中文与 `…` 占位都不会命中这个形状。
KEY_RE = re.compile(r'"([A-Za-z_][A-Za-z0-9_]*)"\s*:')
#: 路径参数一律等价：文档写 `<id>`、代码写 `<accident_id>` 是同一个接口。
SLOT_RE = re.compile(r"<[^>]+>")
#: 块里写着 `<载荷>` 这类尖括号占位符的，是**规范示意**，不是哪个接口的真实
#: 样例——真实样例不会画占位符，画了就说明它想说的是「形状长这样」。
#: 00 §4.1 的统一响应结构正是这种，它不该被当成「归属不明」挨一记警告。
PLACEHOLDER_RE = re.compile(r"<[\w一-鿿|…]+>")


def norm(route: str) -> str:
    """把路径参数抹平，好让文档与代码的写法对得上。"""
    return SLOT_RE.sub("<>", route).rstrip("/")


def keys_of(obj, out: set[str] | None = None, depth: int = 0) -> set[str]:
    """递归收集 JSON 里出现过的**键名**（不要路径，只要名字）。

    用名字而不是路径，是因为文档样例的嵌套靠缩进表达，把它还原成路径要么写个
    缩进解析器、要么把 `//` 注释和 `…` 占位先清洗掉，两条路都脆。名字这一层
    足够回答「文档写的这个字段，实现有没有给」。
    """
    if out is None:
        out = set()
    if depth > 8:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(k)
            keys_of(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:5]:                    # 元素结构一致，取几个够
            keys_of(v, out, depth + 1)
    return out


def array_field_map(text: str) -> dict[str, set[str]]:
    """把样例里「数组键 → 该数组内出现过的键」解出来。

    `/api/review/stats` 的 `by_warning` 与 `promote_history` 在空工作库上是空
    数组，元素字段一个也展不出来；没有这张表，脚本只能看到「文档写了 `token`
    响应没有」，看不出 `token` 本来就住在那个空数组里，于是**记成硬缺口**。
    有了它，缺口就能被正确归到「那边还没数据」而不是「实现漏了」。

    只用括号配对，不引 `json.loads`：样例里有 `//` 注释与 `…` 占位，解析器会
    直接抛。配对失败时该数组整段丢弃，退回「不归类」的旧行为——宁可漏报，
    不给错的归类。
    """
    out: dict[str, set[str]] = {}
    stack: list[tuple[str, set[str]]] = []          # [(数组键, 数组内键名)]
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
                if j < n and text[j] == "[":
                    stack.append((key, set()))      # 进数组
                elif stack:                          # 普通键，归当前数组
                    stack[-1][1].add(key)
                i = m.end()
                continue
            # 不是键名，是字符串**值**：跳到配对的引号
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue
        if c == "]" and stack:
            k, s = stack.pop()
            out.setdefault(k, set()).update(s)
        i += 1
    return out


def doc_samples() -> tuple[dict[str, set[str]], list[str], list[str],
                           list[str], dict[str, dict[str, set[str]]]]:
    """从蓝图抽响应样例。

    返回 (接口→键名, 真·归属不明的块, 规范示意块, 请求体块, 接口→数组键→
    数组内键名)。后两者不参与比对，但要说清是「没认出归属」还是「本来就
    不是某个接口的样例」；末项供空数组时把缺口归到「数据没到」。
    """
    samples: dict[str, set[str]] = {}
    orphans: list[str] = []
    specs: list[str] = []
    bodies: list[str] = []
    arrays: dict[str, dict[str, set[str]]] = {}

    for path in sorted(DOCS.glob("[0-9][0-9]_*.md")):
        lines = path.read_text(encoding="utf-8").splitlines()
        cur: str | None = None
        i = 0
        while i < len(lines):
            line = lines[i]
            if line.lstrip().startswith("#"):
                # 标题行才更新归属；`##` 级标题（没写接口）会把它清空
                m = CALL_RE.search(line)
                cur = f"{m.group(1)} {m.group(2)}" if m else None
            if line.startswith("```json"):
                j = i + 1
                chunk: list[str] = []
                while j < len(lines) and not lines[j].startswith("```"):
                    chunk.append(lines[j])
                    j += 1
                text = "\n".join(chunk)
                where = f"{path.name}:{i + 1}"
                if '"ok"' in text:
                    if cur is None:
                        (specs if PLACEHOLDER_RE.search(text)
                         else orphans).append(where)
                    else:
                        key = norm(cur)
                        samples.setdefault(key, set()).update(
                            KEY_RE.findall(text))
                        for ak, fs in array_field_map(text).items():
                            arrays.setdefault(key, {}).setdefault(
                                ak, set()).update(fs)
                else:
                    bodies.append(where)
                i = j
            i += 1
    return samples, orphans, specs, bodies, arrays


#: 「这块数据没准备好」的统一说法 → 人话。值为假（false / null）都算。
AVAIL_FLAGS = {
    "available": "工作库未建立",
    "images_available": "图片清单未重建",
}


def blocking_flag(obj, depth: int = 0) -> str | None:
    """响应里有没有「这块没准备好」的标志，**递归**找。

    必须是递归的：`/api/images/stats` 把 `available` 放在 `data` 里面，
    只看顶层会漏过去，然后 `by_ext` 空数组展不出元素键，`ext`/`count`
    就被误报成缺口。

    ## 判据必须是 `is False`，不能是 `not v`

    `available` 这个名字在同一个响应里有两层意思。详情接口的
    `tiers[].available` 是**该档有几个字段有值**（整数，某条事故上为 0 很正常），
    而 `images_available` 才是「清单建没建」的布尔。用 `not v` 的话，只要碰上
    一条某档有值数为 0 的事故，整个接口就被判成「数据没准备好」，**真的缺口
    被吞掉**——实测踩过：`images` 明明有数据（4 张），却被标成「未判定」。
    布尔值只可能是 `True`/`False`，整数 `0` 不是 `False`，这一条就分开了。
    """
    if depth > 6:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in AVAIL_FLAGS and v is False:
                return AVAIL_FLAGS[k]
            got = blocking_flag(v, depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for v in obj[:3]:
            got = blocking_flag(v, depth + 1)
            if got:
                return got
    return None


def empty_list_keys(obj, out: set[str] | None = None, depth: int = 0) -> set[str]:
    """响应里哪些**键的值是空数组**。

    与 `empty_payload` 的区别：那个问「是不是**整份**都没数据」（决定接口要不要
    整条跳过），这个问「**哪些**子结构空着」（决定某几个字段算不算缺口）。
    `/api/review/stats` 的 `funnel` 有数、`by_warning` 与 `promote_history` 空，
    前者不成立、后者成立——正是靠这个把 10 个假缺口归到「数据没到」。
    """
    if out is None:
        out = set()
    if depth > 6:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and not v:
                out.add(k)
            else:
                empty_list_keys(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:3]:
            empty_list_keys(v, out, depth + 1)
    return out


def empty_payload(obj, depth: int = 0, acc: list[int] | None = None) -> bool:
    """响应里**有列表、且所有列表都空**吗。

    空表时元素字段一个也展不出来，比下去全是假缺口——`/api/collect/raw` 在
    刚建好还没采过数据的空工作库上就是这样：12 个文档字段被判「响应没有」，
    其实只是还没采到东西。这类接口该记「未判定」，不该记「实现漏了」。

    判据用「**全**空」而不是「有空的」：列表只要有一条非空，元素结构就摆在那里，
    缺口就是真的，不能拿旁边那条空列表当挡箭牌。
    """
    if acc is None:
        acc = [0, 0]                       # [列表总数, 空列表数]
    if depth > 6:
        return False
    if isinstance(obj, dict):
        for v in obj.values():
            empty_payload(v, depth + 1, acc)
    elif isinstance(obj, list):
        acc[0] += 1
        if not obj:
            acc[1] += 1
        else:
            for v in obj[:3]:
                empty_payload(v, depth + 1, acc)
    return acc[0] > 0 and acc[0] == acc[1]


def staging_ready(path: str) -> bool:
    """工作库建了没有——以 `raw_accidents` 表在不在为准。

    不拿「文件在不在」当判据：`data/caiems_staging.db` 可能是个 0 张表的空
    文件（实测过），文件名骗人。表不在，采集 / 提取 / 审核三组的响应就只剩
    骨架键，元素字段一律展不出来。
    """
    p = Path(path)
    if not p.exists():
        return False
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_accidents'"
        ).fetchone()
        con.close()
        return row is not None
    except sqlite3.Error:
        return False


def real_samples(main_db: str, staging_db: str, verbose: bool = False) -> tuple[
        dict[str, set[str]], list[str], list[str], dict[str, str],
        dict[str, set[str]]]:
    """打真实 GET 请求。

    返回 (接口→键名, 写库跳过的, 库不可用跳过的, 子结构不可用的接口→原因,
    接口→本次响应里空数组的键名)。第四项不是「跳过」——接口照比，只是它某个
    子结构（`images`）没数据，那部分的缺口不参与判定。第五项配合文档侧的
    数组归属表，把「住在空数组里的字段」也归到「数据没到」。
    """
    os.environ["CAIEMS_DB"] = main_db
    os.environ["CAIEMS_STAGING_DB"] = staging_db
    os.environ.setdefault("CAIEMS_DATA_DIR", str(main_repo_root() / "data"))

    import sqlite3
    from construction_agent.web.factory import create_app

    staging_ok = staging_ready(staging_db)

    con = sqlite3.connect(f"file:{main_db}?mode=ro", uri=True)
    # `/api/accidents/<id>` 多打几条：条件字段（天气、坠落高度、被困人数）
    # 单条覆盖不全，一条一条打会把它们误报成缺口
    accident_ids = [r[0] for r in
                    con.execute("SELECT id FROM accidents LIMIT 5").fetchall()]
    repl = {
        "accident_id": accident_ids[0] if accident_ids else "1",
        "code": con.execute("SELECT code FROM caics_causes LIMIT 1").fetchone()[0],
        "task_id": "1", "stage_id": "1", "filename": "x", "path": "x",
    }
    con.close()

    app = create_app()
    client = app.test_client()

    got: dict[str, set[str]] = {}
    skipped_write: list[str] = []
    skipped_empty: list[str] = []
    partial: dict[str, str] = {}
    empty_keys: dict[str, set[str]] = {}

    for rule in sorted(app.url_map.iter_rules(), key=lambda r: str(r)):
        methods = rule.methods or ()
        route = str(rule)
        if route.startswith("/static") or "/media/" in route:
            continue
        if "GET" not in methods:
            skipped_write.append(f"{'/'.join(sorted(methods - {'HEAD', 'OPTIONS'}))} {route}")
            continue

        # 工作库没建时，这三组的响应只剩 ok/available/hint 几个骨架键，
        # 文档里写的元素字段一个都展不出来——比下去全是假缺口。整组不参与
        # 比对，但要写在「跳过」里说清原因，不能静默。
        if not staging_ok and route.startswith(("/api/collect/", "/api/extract/",
                                               "/api/review/")):
            skipped_empty.append(f"GET {route}（工作库未建立）")
            continue

        # 带路径参数的，多打几条取并集；其余单次
        slots = re.findall(r"<(?:[^:>]+:)?(\w+)>", route)
        if slots in (["id"], ["accident_id"]) and len(accident_ids) > 1:
            probes = [route.replace(f"<{slots[0]}>", str(a))
                      for a in accident_ids]
        else:
            probes = [re.sub(r"<(?:[^:>]+:)?(\w+)>",
                             lambda m: str(repl.get(m.group(1), "1")), route)]

        names: set[str] = set()
        blocks: list[str] = []           # 每次采样各记一笔，不用 or 混成一次判断
        bodies: list = []
        empty = False
        for p in probes:
            r = client.get(p)
            if r.status_code != 200 or not r.is_json:
                continue
            body = r.get_json()
            bodies.append(body)
            blocked_here = blocking_flag(body)
            if blocked_here:
                blocks.append(blocked_here)
            empty = empty or empty_payload(body)
            names |= keys_of(body)
        if empty and not blocks:
            blocks = ["列表为空（还没数据）"] * len(probes)
        # **每次采样都不可用**才算「展不出来」。只要有一条能取到元素，
        # 缺口就是真的——`or` 会把「五条里一条没图」升级成「整个接口没数据」，
        # 于是真缺口不报，比不查还坏。
        blocked = blocks[0] if blocks and len(blocks) == len(probes) else None
        if names:
            key = f"GET {norm(route)}"
            got[key] = names
            if blocked:
                partial[key] = blocked
            ek: set[str] = set()
            for b in bodies:
                ek |= empty_list_keys(b)
            if ek:
                empty_keys[key] = ek
            if verbose:
                print(f"  · GET {route} → {len(names)} 个键名（{len(probes)} 次采样）"
                      + (f"，{blocked}" if blocked else "")
                      + (f"，空数组：{'、'.join(sorted(ek))}" if ek else ""))
        else:
            skipped_empty.append(f"GET {route}"
                                 + (f"（{blocked}）" if blocked else ""))

    return got, skipped_write, skipped_empty, partial, empty_keys


def compare(doc: dict[str, set[str]], real: dict[str, set[str]],
            partial: dict[str, str] | None = None,
            doc_arrays: dict[str, dict[str, set[str]]] | None = None,
            empty_keys: dict[str, set[str]] | None = None) -> list[dict]:
    """只比对两侧都有的接口。真实侧独有的那些不在这层的职责里（§九 管）。

    两处「不作数」：

    - **整个接口展不开**（`partial`，如工作库未建）——`unjudged` 里说明原因
    - **字段住在空数组里**（`empty_held`）——文档把 `token` 画在 `promote_history`
      里，而响应里 `promote_history` 是 `[]`，那这个缺口就不是实现漏了。判据是
      「该字段归属的数组**全**是空的」：只要归属的数组里有一个非空（同一字段名
      在两个数组里都出现），元素结构就摆在那儿，照记硬缺口
    """
    partial = partial or {}
    doc_arrays = doc_arrays or {}
    empty_keys = empty_keys or {}
    rows = []
    for route in sorted(set(doc) & set(real)):
        doc_only = sorted(doc[route] - real[route])
        ek = empty_keys.get(route, set())
        arrays = doc_arrays.get(route, {})
        held = [f for f in doc_only
                if (owners := [a for a, fs in arrays.items() if f in fs])
                and all(o in ek for o in owners)]
        rows.append({
            "route": route,
            "doc_only": [f for f in doc_only if f not in held],
            "real_only": sorted(real[route] - doc[route]),
            "unjudged": partial.get(route),
            "empty_held": held,
            "empty_keys": sorted(ek),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--main-db", default=None,
                    help="主库路径（默认按 scripts/_common 的解析）")
    ap.add_argument("--staging-db", default=None, help="工作库路径")
    ap.add_argument("--verbose", action="store_true",
                    help="逐个接口打印采样情况")
    args = ap.parse_args()

    staging = args.staging_db or os.environ.get("CAIEMS_STAGING_DB") or str(
        Path(main_db_path()).parent / "caiems_staging.db")

    doc, orphans, specs, doc_bodies, doc_arrays = doc_samples()
    if args.verbose:
        print("文档侧响应样例：")
    real, skipped_write, skipped_empty, partial, empty_keys = real_samples(
        args.main_db or main_db_path(), staging, args.verbose)

    print(f"\n蓝图里的响应样例 {len(doc)} 个接口，真实打到的 GET {len(real)} 个")
    print(f"  两侧都有、能比对的：{len(set(doc) & set(real))}")

    rows = compare(doc, real, partial, doc_arrays, empty_keys)
    # 子结构不可用的那几条，缺口不算数
    hard = [r for r in rows if r["doc_only"] and not r["unjudged"]]
    soft = [r for r in rows if r["real_only"]]
    held = [r for r in rows
            if (r["doc_only"] and r["unjudged"]) or r["empty_held"]]

    for r in rows:
        if not r["doc_only"] and not r["real_only"] and not r["empty_held"]:
            continue
        print(f"\n  {r['route']}")
        if r["doc_only"] and r["unjudged"]:
            print(f"    未判定（{r['unjudged']}，"
                  f"{len(r['doc_only']) + len(r['empty_held'])} 个字段展不出来）："
                  + "、".join(r["doc_only"][:12] + r["empty_held"][:4])
                  + ("…" if len(r["doc_only"]) > 12 else ""))
        if r["doc_only"]:
            if not r["unjudged"]:
                print(f"    文档写了、响应没有（{len(r['doc_only'])}）："
                      + "、".join(r["doc_only"][:12])
                      + ("…" if len(r["doc_only"]) > 12 else ""))
        if r["empty_held"] and not r["unjudged"]:
            # 整个接口已经「未判定」了，就不必再按空数组说一遍
            print(f"    未判定（{'、'.join(r['empty_keys'])} 在本次响应里是空数组，"
                  f"{len(r['empty_held'])} 个字段展不出来）："
                  + "、".join(r["empty_held"][:12])
                  + ("…" if len(r["empty_held"]) > 12 else ""))
        if r["real_only"]:
            print(f"    响应里有、文档没写（{len(r['real_only'])}）："
                  + "、".join(r["real_only"][:12])
                  + ("…" if len(r["real_only"]) > 12 else ""))

    only_doc = sorted(set(doc) - set(real))
    if only_doc:
        print("\n  只在文档里、没打成请求的接口（写库动作或参数不适用）：")
        for route in only_doc:
            print(f"    · {route}")

    if specs:
        print(f"\n  规范示意块（画的是形状不是某接口，本就不该比对）：{len(specs)} 处")
        for w in specs:
            print(f"    · {w}")
    if orphans:
        print("\n  ⚠ 归属不明的响应块（标题里找不到接口，未参与比对）：")
        for w in orphans:
            print(f"    · {w}")
        print("    在标题行写上 `### `GET /api/…`` 就会纳入比对。")
    if skipped_empty:
        print(f"\n  库不可用 / 无数据而跳过的 GET（{len(skipped_empty)}）：")
        for s in skipped_empty[:10]:
            print(f"    · {s}")
        if len(skipped_empty) > 10:
            print(f"    …另有 {len(skipped_empty) - 10} 个")

    print("\n" + "=" * 52)
    print(f"硬缺口（文档承诺、实现没给）：{len(hard)} 个接口，"
          f"合计 {sum(len(r['doc_only']) for r in hard)} 个字段")
    print(f"文档不全（实现有、文档没写）：{len(soft)} 个接口，"
          f"合计 {sum(len(r['real_only']) for r in soft)} 个字段")
    if held:
        n_held = sum(len(r["doc_only"]) + len(r["empty_held"]) for r in held)
        print(f"未判定（数据没准备好，字段展不出来）：{len(held)} 个接口，"
              f"合计 {n_held} 个字段")
    if hard:
        return 1
    print("✓ 文档样例里的字段，实现一个不缺。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
