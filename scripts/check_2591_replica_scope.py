"""§2.5.91 核查副本的适用范围：核查脚本核的，是**哪一份** web。

## 这一节要回答什么

`docs/web/01_在线采集.md` 的 §2.5.67–§2.5.90 里有大量"用户看到什么"的结论
（对比度 757 → 0、焦点遮挡 11/18 → 0、`layout.js` 动态跟随顶栏高度、
`raw_text` 点开看得见、错误提示写进了 DOM……）。这些结论有一个从不被写出来
的前提：

> **它们只对「核查脚本自己所在的那份副本」成立。**

本项目同时存在两份 web 代码：

| 副本 | 路径 | 谁在跑 |
|---|---|---|
| **生产副本** | 主仓库根（`_common.main_repo_root()`） | 用户敲 `python -m construction_agent.main web` |
| **核查副本** | 脚本自身所在仓库根（worktree） | 全部 19 个起服务 / 建 app 的核查脚本 |

`web/factory.py:33` 是 `WEB_DIR = Path(__file__).resolve().parent`，
`static_folder` 由它派生 ⇒ **运行哪份代码就取哪份静态资源**。两份副本一旦分叉，
"分支侧全绿"与"生产侧仍是旧样"可以同时成立，而**任何脚本都不吭声**。

## 判据

- **A 段（运行时）**：两副本各自建 app，实测 `/static/app.css` 与
  `/static/layout.js` 取到的是**自己那份**（`static_folder` 落在各自根下），
  且两腿**不是同一份**（是同一份说明本节前提不成立，须明说而非静默通过）。
- **B 段（登记面，双向可证伪）**：`construction_agent/web/` 下逐文件比对
  两副本的 md5，与 `REGISTER` 双向对账 ——
  - 分叉了却没登记 ⇒ 报红（新增分叉）
  - 登记了却已同源 ⇒ 报红（登记过期；多半是主仓库 merge 过了）
  - 登记着但任一侧 md5 变了 ⇒ 报红（内容变了，登记该更新）

  **登记不是为了"允许分叉"，是为了让分叉这件事有一个会过期的记录**。没有它，
  "分支侧修好了"读起来就像"修好了"（§2.5.67 的规矩：只写理由不验证＝永久豁免）。
- **C 段（注入式自证）**：四个用例，红的**恰好**是哪几条判据。

## 判据边界（明写）

1. 本节只核"两份副本一不一样"、**不核"哪一份才对"**——分支侧领先是开发中的
   正常状态，不是缺陷。缺陷是**这件事没人记录**。
2. B 段只覆盖 `construction_agent/web/` 子树；`data/`、`docs/`、`scripts/`
   的分叉不在射程内（那些是资产与核查脚本本身，随 worktree 走）。
3. 判据是**文件级 md5**：一个文件里改一个字与重写整个文件，在这里都记"变了"。
   它答不了"改了多少、改得对不对"。
4. 两副本一旦 merge 成同源，B 段会整体报"登记过期"——**那是提示该撤登记，
   不是数据坏了**。
5. 本节不发一个请求、不碰任何库；A 段只 GET 静态资源。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

from _common import main_repo_root                            # noqa: E402

PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
ENV_PROD = "C2591_PROD"
WEB_REL = "construction_agent/web"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
ALIGN = ROOT / "docs" / "web" / "09_端到端对齐核查.md"
TODO = ROOT / "docs" / "web" / "10_待执行清单.md"
SNAP = ROOT / "docs" / "web" / "11_执行状态快照_2026-10-05.md"
CM = ROOT / "CLAUDE.md"
E2E = ROOT / "scripts" / "e2e" / "README.md"

# ---------------------------------------------------------------- 登记表
# 键 = `construction_agent/web/` 下的相对路径；
# 值 = (生产侧 md5 或 None〔文件不存在〕, 核查侧 md5, 这一侧的改动出自哪里)
#
# **登记的是「分支侧有、生产侧没有」的改动**，即两副本的分叉面。任一侧 md5 变了
# 就该来更新这里 —— 判据会把"变了"报出来，不会让它悄悄过期。
REGISTER: dict[str, tuple[str | None, str, str]] = {
    "dberr.py": (
        "d9ab3048bbc4e4ea75cb169af08da3b7",
        "b20b54573499847c1fa6c7d30a7988f0",
        "§2.5.90：`_hint` 按 kind 分岔 —— 工作库「文件不在」给出建库动作，"
        "最终库保持原文（它是数据资产，不存在=环境指错）",
    ),
    "images.py": (
        "16d468a95d28ae8fc7f397bedb8cafca",
        "625961f84acef9c651fe375e5ccf80e7",
        "计数类字段「没量过」由 0 改 None（`empty` → `unknown`）：0 是可用答案，"
        "拿它表示不知道会让读的人得出相反结论",
    ),
    "templates/base.html": (
        "3aaf25853c544ce8568b8dc6b64567d2",
        "34324841f39efe1984f7c330ce702284",
        "§2.5.75：引入 `layout.js`（defer）",
    ),
    "static/layout.js": (
        None,
        "eb2c83be40736a24f7f5b88eefb2caab",
        "§2.5.75：新增 —— ResizeObserver 量顶栏真实高度写回 `--topbar-h`",
    ),
    "static/app.css": (
        "79bdaaff798b39a484b27dc3c6857ac2",
        "771e16b5609d34eec1426cd31a462a2b",
        "§2.5.72/73/75 三轮：8 个对比度 token、`--border-input`、"
        "`--scroll-pad`/`--topbar-h`/`--thead-h`/`--bulkbar-*`",
    ),
    "static/causes.js": (
        "b995f9afeaaa5307dbaa425dcfd1ae9a",
        "2f19378eada0ed87c4067d1281e119e7",
        "§2.5.73：删掉 JS 里那份色值副本，改从 `:root` token 现读",
    ),
    "static/accident.js": (
        "a7624b22cb7c575c51688814292f63eb",
        "180b4a2180f62907f825ea72150fb43d",
        "§2.5.70：灯箱焦点陷阱（Tab 环形）+ 关闭后焦点归还",
    ),
    "static/overview.js": (
        "d1aedb7b4cca84a27acb96fe6589db69",
        "027d0ca31917c7572054a86fe61c7fa3",
        "§2.5.83：图片统计补解 `data` 层（此前「现场图片」卡从没出现过）；"
        "§2.5.87：两处静默 catch 改为写进 DOM",
    ),
    "factory.py": (
        "61a81671d981e20a14a18be5ea56096d",
        "fd728cfd3f33c2fe4b382dbdf4639632",
        "§2.5.93：`TRUSTED_HOSTS` 白名单 + `before_request` 同源守卫"
        "（跨源非安全方法 403；`setdefault` 是空操作，改判调用方有没有显式传）",
    ),
    "review.py": (
        "f640f59c8f6eff8d025f85fb29d8f78d",
        "c579765b136501f327952dbe009389f7",
        "§2.5.92：`rollback` 必须指名 `promote_id`"
        "（此前一个空 body 的 POST 就删掉了最终库 1 条事故与它下面的编码）",
    ),
    "static/review.js": (
        "517e5cde46c6c3fc3043f469b0c2bf59",
        "80f06a6eeedbf1204933d562173676d7",
        "§2.5.92：回滚带 `promote_id`（取页面渲染时那条），"
        "确认文案与请求体说的是同一条",
    ),
    "static/collect.js": (
        "c5c0769fa606e4d4c7814373ca1e938e",
        "fcd24179edf3e9edb01e5d705b80b917",
        "§2.5.92：`deleteTask` 补一句确认（同一个删除动作"
        "在提取段有确认、采集段一句都没有）；`showDbNotice` 原样透传 `hint`",
    ),
    "static/extract.js": (
        "db6222ed6577d72dc0d643b1e781ba3a",
        "9d3ee639667ff39da58dce9158e63dfc",
        "§2.5.9x：提取页的前端改动（逐条见 01 号对应节）",
    ),
}

# A 段探的两个资源 + 四个"只在分支侧"的修复 token
ASSETS = ("app.css", "layout.js")
TOKENS = ("--border-input", "--scroll-pad", "--topbar-h", "--bulkbar-h")

# A 段要核的"起服务/建 app"脚本。判据：这些脚本必须把**自身仓库根**插进 sys.path。
SERVICE_SCRIPTS = (
    "check_2568_render_visibility.py", "check_2569_interaction_click.py",
    "check_2570_keyboard_path.py", "check_2575_focus_obscured.py",
    "check_2576_column_exit.py", "check_2577_response_face.py",
    "check_2578_response_shape.py", "check_2580_request_body.py",
    "check_2581_response_body.py", "check_2582_query_params.py",
    "check_2583_get_response.py", "check_2584_live_keys.py",
    "check_2585_error_branches.py", "check_2586_write_error_branches.py",
    "check_2587_frontend_error_face.py", "check_2588_error_notice.py",
    "check_contrast_a11y.py", "check_color_source.py",
    "check_web_interaction.py", "screenshot_web.py",
    "check_api_contract.py", "check_field_alignment.py",
    "check_enum_alignment.py", "check_web_alignment.py", "check_2579_response_types.py",
)


def prod_root() -> Path:
    """生产副本根。C 段靠环境变量把它指向临时副本注入。"""
    raw = os.environ.get(ENV_PROD)
    return Path(raw).expanduser().resolve() if raw else main_repo_root()


def md5_of(p: Path) -> str | None:
    return hashlib.md5(p.read_bytes()).hexdigest() if p.is_file() else None


def walk_web(root: Path) -> dict[str, str | None]:
    """`construction_agent/web/` 下非 `__pycache__` 文件的相对路径 → md5。"""
    base = root / WEB_REL
    out: dict[str, str | None] = {}
    if not base.is_dir():
        return out
    for f in sorted(base.rglob("*")):
        if f.is_file() and "__pycache__" not in f.parts:
            out[str(f.relative_to(base))] = md5_of(f)
    return out


def diverged(prod: dict, branch: dict) -> dict[str, tuple[str | None, str | None]]:
    """返回 `{相对路径: (生产侧 md5, 分支侧 md5)}`，只含两侧不一致的。"""
    out = {}
    for name in sorted(set(prod) | set(branch)):
        a, b = prod.get(name), branch.get(name)
        if a != b:
            out[name] = (a, b)
    return out


# ---------------------------------------------------------------- A 段
_A_CHILD = r'''
import hashlib, json, sys
root = sys.argv[1]
sys.path.insert(0, root)
import construction_agent.web.factory as F
app = F.create_app({"TESTING": True})
c = app.test_client()
def probe(name):
    r = c.get("/static/" + name)
    d = r.data
    return {"status": r.status_code, "bytes": len(d),
            "md5": hashlib.md5(d).hexdigest(),
            "tokens": sorted(t for t in TOKENS if t.encode() in d)}
out = {"root": root, "factory": F.__file__, "web_dir": str(F.WEB_DIR),
       "static_folder": app.static_folder,
       "assets": {n: probe(n) for n in ASSETS}}
print("RESULT " + json.dumps(out, ensure_ascii=False))
'''


def _probe(root: Path) -> dict:
    """在一个**独立子进程**里建 app 并探静态资源。

    必须另起进程：同进程里 `construction_agent` 只会被 import 一次，
    第二腿会命中 `sys.modules` 缓存、读到第一腿的副本 ⇒ 用例静默失效
    （§2.5.84 踩过同一个坑）。
    """
    src = "TOKENS = %r\nASSETS = %r\n" % (list(TOKENS), list(ASSETS)) + _A_CHILD
    p = subprocess.run([PY, "-c", src, str(root)], capture_output=True,
                       text=True, cwd=str(root), timeout=120)
    lines = [l for l in p.stdout.splitlines() if l.startswith("RESULT ")]
    if p.returncode != 0 or not lines:
        tail = " | ".join(p.stderr.strip().splitlines()[-4:])
        raise RuntimeError(f"子进程探针失败（root={root} rc={p.returncode}）：{tail}")
    return json.loads(lines[-1][7:])


def part_a() -> list[str]:
    """A 段：运行哪份代码就取哪份资源 + 起服务脚本的 sys.path 形态。"""
    fails: list[str] = []
    print("A 段 · 两份副本各取哪份静态资源（运行时实测）")
    prod, branch = prod_root(), ROOT
    pr, br = _probe(prod), _probe(branch)

    for tag, r, root in (("生产", pr, prod), ("核查", br, branch)):
        sf = Path(r["static_folder"]).resolve()
        ok = sf == (root / WEB_REL / "static").resolve()
        print(f"  {tag}副本  {r['root']}")
        print(f"    factory     {r['factory']}")
        print(f"    static_dir  {sf}")
        for name in ASSETS:
            a = r["assets"][name]
            tk = ",".join(a["tokens"]) or "—"
            print(f"    /static/{name:<10} {a['status']}  {a['bytes']:>6} B  "
                  f"md5={a['md5'][:12]}  token={tk}")
        if not ok:
            fails.append(f"A1 {tag}副本的 static_folder 不在自身仓库根下：{sf}")

    if pr["static_folder"] == br["static_folder"]:
        fails.append("A3 两腿取到同一份 static —— 本节前提（两个副本）不成立，"
                     "不许静默通过")
    else:
        print("  A3 两腿 static_folder 不同 ✓")

    # 缺哪些修复 token —— 这是"生产侧落后多少"的直接表述
    pt, bt = set(), set()
    for name in ASSETS:
        pt |= set(pr["assets"][name]["tokens"])
        bt |= set(br["assets"][name]["tokens"])
    missing = sorted(bt - pt)
    print(f"  修复 token：核查侧 {len(bt)} 个 / 生产侧 {len(pt)} 个；"
          f"只在核查侧的 {len(missing)} 个 → {missing or '—'}")
    if not missing:
        fails.append("A4 两副本的修复 token 集合相同 —— 若主仓库已 merge，"
                     "B 段的登记表也该撤了")

    # A5 起服务脚本必须把自身仓库根插进 sys.path
    bad = []
    for name in SERVICE_SCRIPTS:
        f = ROOT / "scripts" / name
        if not f.is_file():
            bad.append(f"{name}(缺失)")
            continue
        src = f.read_text(encoding="utf-8")
        if "sys.path.insert" not in src or "parents[1]" not in src:
            bad.append(f"{name}(无自身仓库根派生)")
    print(f"  A5 起服务脚本 {len(SERVICE_SCRIPTS)} 个，"
          f"自身仓库根派生齐全 {len(SERVICE_SCRIPTS) - len(bad)} 个")
    for b in bad:
        fails.append(f"A5 {b} —— 会把别的副本插进 sys.path，核的就不是自己那份页面")
    return fails


# ---------------------------------------------------------------- B 段
def part_b(verbose: bool = True) -> list[str]:
    """B 段：分叉清单 ↔ 登记表，双向可证伪。"""
    fails: list[str] = []
    prod, branch = walk_web(prod_root()), walk_web(ROOT)
    if not prod:
        fails.append(f"B0 生产副本的 {WEB_REL} 读不到")
        return fails
    real = diverged(prod, branch)
    if verbose:
        print(f"B 段 · {WEB_REL} 逐文件比 md5："
              f"共 {len(set(prod) | set(branch))} 个文件，分叉 {len(real)} 个")
        for name, (a, b) in real.items():
            print(f"  ≠ {name}")
            print(f"      生产 {a or '（不存在）'}")
            print(f"      核查 {b or '（不存在）'}")

    reg = set(REGISTER)
    only_real = sorted(set(real) - reg)
    only_reg = sorted(reg - set(real))
    for n in only_real:
        fails.append(f"B1 「{n}」两副本分叉，却不在登记表上 —— 新增分叉须登记")
    for n in only_reg:
        fails.append(f"B2 「{n}」登记了却已同源 —— 登记过期（多半是主仓库 merge 过了）")

    for n in sorted(set(real) & reg):
        ra, rb, _why = REGISTER[n]
        a, b = real[n]
        if a != ra or b != rb:
            fails.append(
                f"B3 「{n}」内容变了 —— 登记 (生产 {ra or '不存在'} / 核查 {rb or '不存在'})"
                f"，实测 (生产 {a or '不存在'} / 核查 {b or '不存在'})")
    if verbose and not (only_real or only_reg):
        print(f"  B1/B2 双向对账 ✓（实测 {len(real)} = 登记 {len(reg)}）")
        print(f"  B3 内容未变 ✓")
    return fails


# ---------------------------------------------------------------- C 段
def _copy_web(src: Path, dst: Path) -> Path:
    """把 `src` 的 `construction_agent/web/` 复制成一个假的生产副本。"""
    target = dst / WEB_REL
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src / WEB_REL, target)
    return dst


def _touch(p: Path, tail: str = "\n") -> None:
    """往文件尾部追加内容（改变 md5，不改语义）。"""
    p.write_text(p.read_text(encoding="utf-8") + tail, encoding="utf-8")


def inject_test() -> int:
    """C 段：六个用例（C1–C4 副本/源码面 + C5 文档面），红的**恰好**是哪几条判据。

    每个用例显式声明跑哪几段：跑 A 段必须给一个**完整的**仓库（子进程要
    import `construction_agent.config`），只跑 B 段的假副本只需 `web/` 子树。
    """
    tmp = Path(tempfile.mkdtemp(prefix="c2591_"))
    prod, br = main_repo_root(), ROOT

    # C2：假副本 = 核查侧拷贝 ⇒ 与核查侧同源 ⇒ B2「登记全部过期」
    same = _copy_web(br, tmp / "same")
    # C3：假副本 = 生产侧拷贝 + 改一个**未登记**的文件 ⇒ B1
    extra = _copy_web(prod, tmp / "extra")
    _touch(extra / WEB_REL / "static" / "dom.js", "\n// probe\n")
    # C4：假副本 = 生产侧拷贝 + 改一个**已登记**文件的内容 ⇒ B3
    touched = _copy_web(prod, tmp / "touched")
    _touch(touched / WEB_REL / "static" / "app.css", "\n/* probe */\n")

    cases: list[tuple[str, dict, tuple[str, ...], list[str]]] = [
        ("C0 基线（真主仓库）", {}, ("a", "b"), []),
        ("C1 两腿同源", {ENV_PROD: str(br)}, ("a",), ["A3", "A4"]),
        ("C2 假副本已同源", {ENV_PROD: str(same)}, ("b",), ["B2"]),
        ("C3 未登记的分叉", {ENV_PROD: str(extra)}, ("b",), ["B1"]),
        ("C4 已登记文件内容变了", {ENV_PROD: str(touched)}, ("b",), ["B3"]),
    ]
    rc = 0
    for tag, env, parts, want in cases:
        os.environ.update(env)
        try:
            f: list[str] = []
            if "a" in parts:
                f += part_a()
            if "b" in parts:
                f += part_b(verbose=False)
            if "d" in parts:
                f += part_d()
        finally:
            for k in env:
                os.environ.pop(k, None)
        got = sorted({x.split()[0] for x in f})
        red = "、".join(got) or "—"
        ok = got == sorted(want)
        print(f"  {'✓' if ok else '✗'} {tag}：红的恰好 "
              f"{'/'.join(want) or '（无）'} → 实测 {red}")
        if not ok:
            print(f"      明细：{f}")
            rc = 1

    # C5：**文档面**注入——C0–C4 全在改副本与源码，D 段这一面从没接过失败路径
    # （§2.5.68：两个注入面都要测，只测一面时另一面被改坏了不会有任何用例转红）。
    # 改的是真实 README，`try/finally` 恢复；先断言"注入确实做进去了"，
    # 否则替换落空会与"判据没报"长得一模一样（§2.5.89）。
    print("  ── C5 文档面：e2e/README 计数句被改 ──")
    orig = E2E.read_text(encoding="utf-8")
    injected = orig.replace("二十八条里 **27 条只读不写**", "二十九条里 **28 条只读不写**")
    if injected == orig:
        print("  ✗ C5 注入未生效：README 里找不到待替换的计数句")
        rc = 1
    else:
        try:
            E2E.write_text(injected, encoding="utf-8")
            f = part_d()
        finally:
            E2E.write_text(orig, encoding="utf-8")
        assert E2E.read_text(encoding="utf-8") == orig, "README 未恢复"
        got = sorted({x.split()[0] for x in f})
        want5 = ["D7"]
        ok = got == want5
        print(f"  {'✓' if ok else '✗'} C5 文档面：红的恰好 D7 → 实测 "
              f"{'、'.join(got) or '（无）'}")
        if not ok:
            print(f"      明细：{f}")
            rc = 1

    print(f"  （注入用的临时副本在 {tmp}，不碰任何真副本）")
    return rc


# ---------------------------------------------------------------- self-test
def self_test() -> int:
    """纯函数正反用例：`diverged()` 与 md5 判定。"""
    cases = [
        ("两侧相同不算分叉", {"a": "1"}, {"a": "1"}, {}),
        ("内容不同算分叉", {"a": "1"}, {"a": "2"}, {"a": ("1", "2")}),
        ("只在一侧算分叉（None）", {"a": "1"}, {}, {"a": ("1", None)}),
        ("另一侧独有也算", {}, {"a": "1"}, {"a": (None, "1")}),
        # 一条用例同时覆盖三种形态：a 两边都有但不同、b 只在生产侧有、c 只在核查侧有。
        # 期望曾漏写 b（判据是对的、期望写窄了）—— §2.5.88：「恰好」是双向断言。
        ("多项混合", {"a": "1", "b": "1"}, {"a": "2", "c": "1"},
         {"a": ("1", "2"), "b": ("1", None), "c": (None, "1")}),
        ("两边都空", {}, {}, {}),
    ]
    bad = 0
    for tag, p, b, want in cases:
        got = diverged(p, b)
        ok = got == want
        print(f"  {'✓' if ok else '✗'} {tag}：{got!r}")
        if not ok:
            print(f"      want {want!r}")
            bad += 1
    # md5_of 对目录与不存在路径都须返回 None 而不是抛错
    for p in (ROOT / "docs", ROOT / "__no_such_file__"):
        try:
            r = md5_of(p)
            ok = r is None
        except Exception as e:                                   # noqa: BLE001
            r, ok = repr(e), False
        print(f"  {'✓' if ok else '✗'} md5_of({p.name}) → {r!r}")
        bad += 0 if ok else 1
    print(f"self-test：{len(cases) + 2 - bad}/{len(cases) + 2}")
    return 1 if bad else 0


# ---------------------------------------------------------------- D 段
def part_d() -> list[str]:
    """D 段：文档对账（核标题行与关键数字，核不到就当锚点未命中报红）。

    每条失败带编号 `D1`–`D7`：C 段用 `x.split()[0]` 取失败标识，若不编号
    就全塌成 `D`，用例只能断言"报了红"、断言不出"**恰好**是哪一条"
    （§2.5.68 立的规矩）。
    """
    import re
    fails: list[str] = []
    checks = [
        (DOC, r"^### 2\.5\.91 ", "01 号 §2.5.91 标题行"),
        (ALIGN, r"§2\.5\.91", "09 号引用 §2.5.91"),
        (TODO, r"check_2591_replica_scope", "10 号清单登记本节脚本"),
        (SNAP, r"§2\.5\.91", "11 号快照登记本节"),
        (CM, r"§2\.5\.91", "CLAUDE.md 登记本节"),
        (E2E, r"check_2591_replica_scope\.py", "e2e/README 只读脚本表登记本节脚本"),
        (E2E, r"二十八条里 \*\*27 条只读不写\*\*", "e2e/README 计数已随表行数递增"),
    ]
    for i, (path, pat, what) in enumerate(checks, start=1):
        if not path.is_file():
            fails.append(f"D{i} {what}：文件不存在 {path}")
            continue
        src = path.read_text(encoding="utf-8")
        if not re.search(pat, src, flags=re.M):
            fails.append(f"D{i} {what}：锚点未命中 {pat}")
    if not fails:
        print(f"  D 段 {len(checks)} 处文档锚点全中 ✓")
    return fails


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--inject-test" in sys.argv:
        print("C 段 · 注入式自证")
        return inject_test()

    print("=" * 72)
    print(f"生产副本（用户实跑）  {prod_root()}")
    print(f"核查副本（本脚本所在）{ROOT}")
    print("=" * 72)
    fails = part_a() + part_b() + part_d()
    print("=" * 72)
    if fails:
        print(f"✗ 合计 {len(fails)} 条：")
        for f in fails:
            print(f"  · {f}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
