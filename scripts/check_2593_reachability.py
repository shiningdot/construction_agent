"""§2.5.93 可达面：这些写路由，谁能把请求送到。

## 这一节要回答什么

§2.5.92 问的是「**要删的东西，问过谁**」，把确认机制分成 L0/L1/L2 三档，查出
`rollback` 删最终库却只有客户端确认（L1）。本节是它的**直接续问**：

> 那些**根本到不了** L1/L2 的调用方，有多少？

换个说法：§2.5.92 的 L2（服务端凭证）**到底在防谁**——防「用户手滑」，还是防
「别人替我按」？如果谁能到达这些路由都没查过，那 L2 的强度就没有参照系。

## 三层可达性（本节的实际结论）

**第一层：绑定面——只有本机能连。**
`FLASK_HOST` 默认 `127.0.0.1`（`config.py`），`.env` 没有覆盖，`main.py:350`
起服务用的就是它。这一条是**唯一真正的边界**，而且是对的。

**第二层：身份面——一个都没有。**
`web/` 下 `login` / `password` / `authorization` / `bearer` / `api_key` /
`login_required` / `session` **全部零命中**。唯一的身份相关配置
`FLASK_SECRET_KEY`（`factory.py:58` 唯一的读取点）是**死配置**——`web/` 里没有
任何 session，它签名什么都不影响。

⚠️ **身份面 ≠ 凭证面**，两者必须分开说（否则会把 promote 的一次性令牌误报成
"有鉴权"）：
- **身份面**回答「**你是谁**」——本节实测**零**
- **凭证面**回答「**这次操作带没带对既有状态**」——`POST /api/review/promote`
  有（§2.5.92 的 L2），其余为 0

第二层为零，意味着**「本机」就是全部的身份**。于是问题变成：**「本机」等于
「只有你」吗？**

**第三层：浏览器面——不等于。**
同源策略拦的是「**读**」不是「**发**」。浏览器里任意一个页面都能让它替自己
向 `127.0.0.1:5000` 发请求。实测两道门：

| 门 | 挡住了什么 | 为什么 |
|---|---|---|
| **JSON content-type ⇒ 预检** | **读 body 的**写路由 | `Content-Type: application/json` 不是简单请求 ⇒ 预检 ⇒ 而本应用**零 `Access-Control-*` 头**（实测 OPTIONS 返 200、无 ACAO）⇒ 浏览器拦下 |
| **POST 是** CORS 安全方法 | 挡不住**输入全在 URL 里**的 POST | 跨源 `<form method=POST>` 不触发预检、也不需要读到响应 |

所以「读 body 的路由安全」是**设计意外的副产品**（因为它恰好用了 JSON），
不是有意的防护：换个 `text/plain` 或改用表单编码就没了。

**DNS rebinding 是第三层的例外口。** 攻击流程：①诱导用户访问 `evil.com`；
②把 `evil.com` 的 A 记录改成 `127.0.0.1`（TTL 设 0，浏览器已缓存的原页面
继续跑）；③此后 `evil.com` 与目标**同源**，可以**读**响应。原先 `TRUSTED_HOSTS`
未设 ⇒ 任意 Host 照常服务（实测伪造 Host 返 200）⇒ rebinding 打通后，
`promote` 要的既有状态（`promote_id`）可从只读接口读到 ⇒ **§2.5.92 的 L2 在
它面前不构成防线**。

## 本节改了什么（两处，都在 `construction_agent/web/factory.py`）

1. **`TRUSTED_HOSTS`**——挡 Host 伪装（挡「读」）。本应用是本机工具，不该接受
   任意 Host 头。端口由 Werkzeug 自动剥离、`127.0.0.1.evil.com` 后缀伪装也挡得住
   （均实测）。
2. **`Origin` 同源守卫**（`before_request`）——挡跨源写（挡「发」）。

**两条修复互为依赖，缺一条都不完整**：只留 `Origin` 校验，攻击者可以用
rebinding 把 Host 伪装成 `127.0.0.1` 从而「同源」；只留 `TRUSTED_HOSTS`，
跨源表单写照样能打到不需要 body 的路由。

## 判据边界（明写，别把它读强了）

1. **`Origin` 守卫挡的是"浏览器被利用"，不是"本机以外的客户端"。** 不带
   `Origin` 的请求一律放行——`curl` / `requests` / Flask 测试客户端都不带。
   本机上的任何进程本来就能直连，这一层**不针对它们**（也不该针对：那需要的是
   身份面，而身份面本节实测为零，是另一个待决项）。
2. **同源判定用 `request.host_url`，而它取自 `Host` 头** ⇒ 这一条**依赖**
   `TRUSTED_HOSTS`。单独把 `TRUSTED_HOSTS` 关掉，本质上是把两道门一起关掉。
3. **本节不核「这些路由该不该需要鉴权」**——那是产品决策。本节只把现状量出来：
   零身份面 + 本机绑定 + 两道浏览器门。
4. **E 段的"表单可达"是静态分类**，不是真打（真打要真改库，§2.5.86 已立规矩：
   验证手段不能制造它要防的损害）。分类判据是「method 是不是 POST」×「实现读不读
   body 的键」，两者都是实测的。
"""

from __future__ import annotations

import importlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))      # scripts/_common

C80 = importlib.import_module("check_2580_request_body")

PY = "/home/suntianhao/construction_agent/.venv/bin/python3"
DOC = ROOT / "docs" / "web" / "01_在线采集.md"
ALIGN = ROOT / "docs" / "web" / "09_端到端对齐核查.md"
TODO = ROOT / "docs" / "web" / "10_待执行清单.md"
SNAP = ROOT / "docs" / "web" / "11_执行状态快照_2026-10-05.md"
CM = ROOT / "CLAUDE.md"
E2E = ROOT / "scripts" / "e2e" / "README.md"

WEB = ROOT / "construction_agent" / "web"
CONFIG = ROOT / "construction_agent" / "config.py"
MAIN = ROOT / "construction_agent" / "main.py"
ENV = ROOT / "construction_agent" / ".env"
FACTORY = WEB / "factory.py"

#: 鉴权面要搜的词。**收窄过的**——裸搜 `token` 会命中 `promote` 的一次性令牌，
#: 那是**凭证**（这次操作带没带对状态），不是**身份**（你是谁）。§2.5.63/§2.5.66：
#: 判据的宽度决定结论的强度，裸子串会在混合语料里自己制造假读数。
AUTH_TOKENS = ("login", "password", "passwd", "authorization", "bearer",
               "api_key", "apikey", "login_required", "session")

#: 回环地址（绑定面唯一可接受的取值）
LOOPBACK = ("127.0.0.1", "localhost", "::1", "[::1]")

#: Host 面探针的**期望表**——两张表合起来必须恰好等于探针清单本身（C0 元判据）。
#: 分表是为了让 C2 与 C3 **方向相反**（一个查「真 Host 有没有被误挡」、一个查
#: 「伪造 Host 有没有被放行」）；写成 `set(good) == 表1` / `set(bad) == 表2`
#: 那种双向等式，两条判据在固定探针清单下互为充要条件、归因不唯一（首版如此）。
EXPECT_GOOD = ("127.0.0.1", "127.0.0.1:5199", "localhost",
               "localhost:5100", "[::1]:5100")
EXPECT_BAD = ("evil.example.com", "evil.example.com:5199", "127.0.0.1.evil.com")

#: 表单可达面的登记表：**实测出来的结论**，每条都要能双向核（§2.5.67：
#: 只写理由不验证＝永久豁免）。
#:   form      —— 跨源 `<form method=POST>` 直接打得到（不触发预检、不需要读响应）
#:   preflight —— 是 POST，但实现读 JSON body ⇒ 表单的 form-urlencoded/text-plain
#:                会被 `get_json(silent=True)` 读成 None，业务层拒之
#:   noform    —— HTML `<form>` 根本发不出这些方法（DELETE）
FORM_REACHABLE = {
    "form": {
        "collect.api_cancel_task",
        "extract.api_start_task",
        "extract.api_cancel_task",
        "extract.api_rerun",
    },
    "noform": {
        "collect.api_delete_task",
        "extract.api_delete_task",
    },
}

#: 文档对账的锚点（写清的 `(文件, 正则或子串)` → 该节写下的实测数字）
DOC_ANCHORS: list[tuple[str, str, str]] = [
    ("01", r"零身份面|身份面.*零", "01 号 §2.5.93 记下零身份面"),
    ("01", r"TRUSTED_HOSTS", "01 号 §2.5.93 记下 TRUSTED_HOSTS"),
    ("01", r"DNS rebinding", "01 号 §2.5.93 记下 DNS rebinding"),
    ("09", r"2\.5\.93", "09 号 §十五 登记本节为第三十五个维度"),
    ("10", r"2\.5\.93", "10 号清单登记本节"),
    ("11", r"2\.5\.93", "11 号快照登记本节"),
    ("e2e", r"check_2593_reachability", "e2e/README 只读脚本表登记本节脚本"),
]


def ok(cond: bool, label: str, extra: str = "") -> bool:
    print(f"  {'✓' if cond else '✗'} {label}" + (f"　{extra}" if extra else ""))
    return cond


def is_loopback(host: str) -> bool:
    """绑定地址是不是回环。空串按 False（Werkzeug 里空串等于 `0.0.0.0`）。"""
    return host.strip().lower() in LOOPBACK


# ══════════════════════════════════════════════════════════════
# A 段：绑定面（静态）—— 只有本机能连
# ══════════════════════════════════════════════════════════════


def part_a() -> list[str]:
    print("\n【A 段】绑定面：服务绑在哪个地址上（静态）")
    fails = []

    cfg = CONFIG.read_text(encoding="utf-8")
    m = re.search(r'FLASK_HOST[^=]*=\s*os\.getenv\(\s*"FLASK_HOST"\s*,\s*"([^"]*)"', cfg)
    if not ok(m is not None, "config.py 里能取到 FLASK_HOST 的默认值"):
        return ["A1 config.py FLASK_HOST 默认值取不到"]
    default_host = m.group(1)
    if not ok(is_loopback(default_host), "A1 默认绑定地址是回环（只有本机能连）",
              f"实测 {default_host!r}"):
        fails.append(f"A1 默认绑定地址不是回环：{default_host!r}")

    #: `FLASK_DEBUG` 默认必须关——debug 模式带 Werkzeug 调试器（可执行任意代码）
    m = re.search(r'FLASK_DEBUG[^=]*=\s*os\.getenv\(\s*"FLASK_DEBUG"\s*,\s*"([^"]*)"\)',
                  cfg)
    dbg_default = m.group(1) if m else "?"
    if not ok(dbg_default.lower() == "false", "A2 FLASK_DEBUG 默认关闭",
              f"实测 {dbg_default!r}"):
        fails.append(f"A2 FLASK_DEBUG 默认不是 false：{dbg_default!r}")

    #: `.env` 覆盖检查——`config.py` 是 `load_dotenv(override=True)`，`.env` 说了算
    env_txt = ENV.read_text(encoding="utf-8") if ENV.exists() else ""
    hits = [l for l in env_txt.splitlines()
            if re.match(r"\s*FLASK_(HOST|PORT|DEBUG)\s*=", l)]
    if not ok(not hits, "A3 .env 未覆盖任何 FLASK_* 绑定项（默认值真的生效）",
              f"命中 {hits}" if hits else ""):
        fails.append(f"A3 .env 覆盖了 FLASK_* 绑定项：{hits}")

    mtxt = MAIN.read_text(encoding="utf-8")
    used = re.findall(r"host\s*=\s*settings\.(\w+)", mtxt)
    if not ok(used == ["FLASK_HOST"], "A4 起服务用的是 settings.FLASK_HOST", f"实测 {used}"):
        fails.append(f"A4 起服务没用 settings.FLASK_HOST：{used}")
    return fails


# ══════════════════════════════════════════════════════════════
# B 段：身份面（静态）—— 一个都没有
# ══════════════════════════════════════════════════════════════


def auth_hits() -> list[tuple[str, str]]:
    """`web/` 下命中鉴权词的地方，返回 [(相对路径, 词)]。"""
    out = []
    for p in sorted(WEB.rglob("*")):
        if p.suffix not in (".py", ".js", ".html") or not p.is_file():
            continue
        txt = p.read_text(encoding="utf-8", errors="replace").lower()
        for w in AUTH_TOKENS:
            if w in txt:
                out.append((str(p.relative_to(ROOT)), w))
    return out


def part_b() -> list[str]:
    print("\n【B 段】身份面：调用方是谁（静态）")
    fails = []

    hits = auth_hits()
    if not ok(not hits, "B1 web/ 下零鉴权标识",
              f"命中 {hits}" if hits else "（零身份面——「本机」就是全部身份）"):
        fails.append(f"B1 web/ 出现鉴权标识：{hits}")

    #: 凭证面**必须**存在，否则上面那句"零身份面"就成了"什么都没有"的笼统说法。
    #: promote 的一次性令牌是 §2.5.92 的 L2——它是**凭证**不是**身份**。
    cred = "confirm_token" in (WEB / "review.py").read_text(encoding="utf-8")
    if not ok(cred, "B2 凭证面另在（review.py 的一次性令牌）——与身份面分开记"):
        fails.append("B2 review.py 找不到 confirm_token（凭证面登记过期）")

    #: `FLASK_SECRET_KEY` 的**全部**出现面。它一变，说明真的启用了 session ⇒
    #: 本节「死配置」的结论过期、须重判。**登记的是"面"不是"次数"**——
    #: 声明在 `config.py`、唯一赋值在 `factory.py:58`，此外没有第三个地方碰它。
    sk = sorted({str(p.relative_to(ROOT))
                 for p in ROOT.joinpath("construction_agent").rglob("*.py")
                 for l in p.read_text(encoding="utf-8", errors="replace").splitlines()
                 if re.search(r"\bFLASK_SECRET_KEY\b|\bapp\.secret_key\b", l)})
    want_sk = ["construction_agent/config.py", "construction_agent/web/factory.py"]
    if not ok(sk == want_sk,
              "B3 FLASK_SECRET_KEY 只出现在 config.py（声明）与 factory.py（赋值）"
              "——web/ 无 session，是死配置", f"实测 {sk}"):
        fails.append(f"B3 FLASK_SECRET_KEY 出现面变了（死配置结论可能过期）：{sk}")
    return fails


# ══════════════════════════════════════════════════════════════
# C/D 段：浏览器面（运行时）
# ══════════════════════════════════════════════════════════════


def build_client():
    """建 test_client。**在子进程里跑**——父进程的 `sys.modules` 缓存会让源码
    注入静默失效（§2.5.64：「锚点写错 = 用例根本没跑」的又一形态）。
    """
    from construction_agent.web.factory import create_app
    app = create_app()
    return app, app.test_client()


def guard_blocked(resp) -> bool:
    """守卫报的是 403 且 message 含「跨源」——**其它状态码都说明请求穿过了守卫**
    （`curl` 同样到不了这一层，故不能只看"被拒了没有"）。
    """
    if resp.status_code != 403:
        return False
    try:
        d = resp.get_json() or {}
    except Exception:  # noqa: BLE001
        return False
    return "跨源" in (d.get("message") or "")


#: 打写路由用的靶子：空 stage_ids **必然**被业务层拒（400），不写任何库。
#: 所以「穿过守卫」的观测值就是 400，「被守卫拦下」是 403 —— 两者分得开。
WRITE_PROBE = ("/api/review/batch-approve", {"stage_ids": []})


def run_runtime() -> dict:
    """一次子进程里跑完 C/D 两段需要的全部观测（省得重复建应用）。"""
    app, c = build_client()
    path, body = WRITE_PROBE
    out: dict = {"trusted": app.config.get("TRUSTED_HOSTS")}

    #: C 段：Host 面。用 test_client 而非真服务器——Flask 的 host 校验在
    #: `wsgi_app` 里，test_client 也走它（两侧实测一致，见文档第三节）。
    out["host"] = {}
    for h in ("127.0.0.1", "127.0.0.1:5199", "localhost", "localhost:5100",
              "[::1]:5100", "evil.example.com", "evil.example.com:5199",
              "127.0.0.1.evil.com"):
        out["host"][h] = c.get("/api/health", headers={"Host": h}).status_code

    #: D 段：Origin 面四格。
    out["origin"] = {
        "same": guard_blocked(c.post(path, json=body,
                                     headers={"Origin": "http://localhost"})),
        "same_explicit": guard_blocked(c.post(
            path, json=body,
            headers={"Host": "127.0.0.1:5100", "Origin": "http://127.0.0.1:5100"})),
        "cross": guard_blocked(c.post(path, json=body,
                                      headers={"Origin": "http://evil.example.com"})),
        "none": guard_blocked(c.post(path, json=body)),
        "cross_get": guard_blocked(c.get("/api/health",
                                         headers={"Origin": "http://evil.example.com"})),
        "rebind": c.post(path, json=body,
                         headers={"Host": "evil.example.com",
                                  "Origin": "http://127.0.0.1:5100"}).status_code,
    }
    return out


def part_c(rt: dict) -> list[str]:
    print("\n【C 段】Host 面：伪造 Host 还会被服务吗（运行时）")
    fails = []

    #: **这条判据是有来历的**：首版用 `app.config.setdefault("TRUSTED_HOSTS", …)`
    #: 写，而 Flask 的默认配置里本来就有这个键（值是 None）⇒ setdefault 永远
    #: 不生效，值仍是 None，四个伪造 Host 全 200 放行。**不报错、不抛异常，
    #: 只是那一层不存在**——§2.5.64「退出码 0 只有当脚本有能力报 1 时才可信」
    #: 的同一个病：配置看着设上了，其实没设。
    t = rt["trusted"]
    if not ok(t == ["127.0.0.1", "localhost", "[::1]"],
              "C1 TRUSTED_HOSTS 真的设上了（不是 Flask 默认的 None）",
              f"实测 {t!r}"):
        fails.append(f"C1 TRUSTED_HOSTS 没生效：{t!r}")

    h = rt["host"]
    #: 判据**方向必须相反**，否则两条判据等价、归因不唯一：首版写的是
    #: `set(good) == 五个` 与 `set(bad) == 三个`，而探针清单固定为这 8 个
    #: ⇒ 两个等式互为充要条件（`good` 恰好是那 5 个 ⟺ `bad` 恰好是那 3 个）。
    #: 后果在 `--inject-test` 的 I1 上暴露：把 Host 层整层关掉时**两条一起红**，
    #: 分不清是哪条判据抓到的。改成方向相反的两个**包含**断言后，
    #: 「层消失」⇒ 只有 C3 红（伪造 Host 被放行了）、「误挡真 Host」⇒ 只有 C2 红。
    good = [k for k, v in h.items() if v == 200]
    bad = [k for k, v in h.items() if v != 200]
    #: 元判据：探针清单必须与两张期望表**不重不漏**地对上，否则新加一格探针
    #: 忘了归类，它既不在 C2 也不在 C3 的射程里（§2.5.67：例外要有判据兜着）。
    if not ok(set(h) == set(EXPECT_GOOD) | set(EXPECT_BAD),
              "C0 探针清单与期望表不重不漏", f"探针 {sorted(h)}"):
        fails.append(f"C0 探针清单与期望表对不上：{sorted(h)}")
    #: 比**集合**不比顺序——`sorted()` 按 ASCII 排，`[` (0x5B) 会跑到 `l` (0x6C)
    #: 前面，写成期望列表就得把 `[::1]:5100` 插在 `localhost` 之前，那是把
    #: 一个无关的排序事实混进判据（首版因此报了一次假红）。
    if not ok(set(EXPECT_GOOD) <= set(good),
              "C2 真 Host（含端口、IPv6）全部放行", f"放行 {sorted(good)}"):
        fails.append(f"C2 真 Host 有被误挡的：{sorted(good)}")
    if not ok(set(EXPECT_BAD) <= set(bad),
              "C3 伪造 Host（含后缀伪装）全部挡住", f"挡住 {sorted(bad)}"):
        fails.append(f"C3 伪造 Host 没挡住：{sorted(bad)}")
    if not ok(rt["origin"]["rebind"] == 400,
              "C4 Host 伪装 + 跨源 Origin → Host 层先拦（400，连不到 Origin 那层）",
              f"实测 {rt['origin']['rebind']}"):
        fails.append(f"C4 叠加场景未在 Host 层被拦：{rt['origin']['rebind']}")
    return fails


def part_d(rt: dict) -> list[str]:
    print("\n【D 段】Origin 面：跨源写请求打得到吗（运行时，四格）")
    fails = []
    o = rt["origin"]
    #: 四格**方向各不相同**，缺任何一格都会把"一律拒绝"与"该拒的拒了"混起来
    #: （§2.5.86：判据要能分辨「该拒的拒了」与「一律拒绝」）。
    for key, want, label in (
            ("same", False, "D1 同源写请求 → 穿过守卫（守卫不是无差别拦）"),
            ("same_explicit", False, "D2 同源写（显式 Host 带端口）→ 穿过守卫"),
            ("cross", True, "D3 跨源写请求 → 被守卫拦下（403）"),
            ("none", False, "D4 不带 Origin（curl/requests）→ 放行，守卫不针对它"),
            ("cross_get", False, "D5 跨源 GET → 不受影响（读方法无副作用）")):
        got = o[key]
        if not ok(got is want, label,
                  f"实测 {'被拦' if got else '穿过'}"):
            fails.append(f"{label.split()[0]} 判定不符：{'被拦' if got else '穿过'}")
    return fails


# ══════════════════════════════════════════════════════════════
# E 段：表单可达面（静态分类，不真打）
# ══════════════════════════════════════════════════════════════


def form_class(method: str, keys: set[str]) -> str:
    """把一条写路由分到三档。**分类判据是实测的**（method × 读不读 body 键）。

    只在 URL 里的按键（如 `/api/x/<id>/cancel`）不算"输入在 body 里"——它们
    恰恰**是**能被表单打到的那种。
    """
    if method != "POST":
        return "noform"
    return "preflight" if keys else "form"


def measure_forms() -> tuple[list[tuple[str, str, str, str]], int]:
    """返回 [(method, rule, ident, 档)] 与写路由总数。复用 §2.5.80 的解析
    （§2.5.76：同一个源不写第二遍解析）。"""
    from construction_agent.web.factory import create_app
    app = create_app()
    routes = C80.write_routes(app)
    reads, _why = C80.impl_side()
    return [(m, r, i, form_class(m, reads.get(i) or set())) for m, r, i in routes], len(routes)


def part_e() -> list[str]:
    print("\n【E 段】表单可达面：哪些写路由能被一个跨源 <form> 打到（静态分类）")
    fails = []
    rows, total = measure_forms()
    by = {k: sorted(i for _m, _r, i, c in rows if c == k)
          for k in ("form", "preflight", "noform")}

    print(f"    写路由共 {total} 条："
          f"表单可达 {len(by['form'])} / 需预检 {len(by['preflight'])} / "
          f"表单发不出 {len(by['noform'])}")
    for i in by["form"]:
        rule = next(r for _m, r, j, _c in rows if j == i)
        print(f"      ★ 表单可达　{rule}　({i})")

    #: 登记表**双向核**：实测多出来 = 有新的暴露面没登记；登记表里多出来 =
    #: 结论过期（那条可能已经改成读 body 了）。§2.5.67：只写理由不验证＝永久豁免。
    for k in ("form", "noform"):
        got, want = set(by[k]), FORM_REACHABLE[k]
        if not ok(got == want, f"E-{k} 登记表 ↔ 实测一致",
                  "" if got == want else
                  f"新增 {sorted(got - want)} / 过期 {sorted(want - got)}"):
            fails.append(f"E-{k} 登记表与实测不符：新增 {sorted(got - want)} "
                         f"过期 {sorted(want - got)}")

    #: **元判据**：三档之和 == 写路由总数（分类是完备的，没有静默漏掉的档）。
    if not ok(sum(len(v) for v in by.values()) == total,
              "E-meta 三档之和 == 写路由总数（分类完备）",
              f"{sum(len(v) for v in by.values())} vs {total}"):
        fails.append("E-meta 分类不完备（有路由落不进任何一档）")

    #: 覆盖面（不判红，摆出来给人看）：表单可达档的"为什么安全"全押在
    #: 「输入在 URL 里」这个前提上——它一变，这一档的结论就变。
    if sum(len(v) for v in by.values()) == total and total:
        print(f"    覆盖面：`form` 档 {len(by['form'])} 条全部是"
              f"「输入在 URL 里」；改成读 JSON body 即落到 `preflight`（意外受保护）")
    return fails


# ══════════════════════════════════════════════════════════════
# F 段：文档对账
# ══════════════════════════════════════════════════════════════


def part_f() -> list[str]:
    print("\n【F 段】文档对账")
    fails = []
    files = {"01": DOC, "09": ALIGN, "10": TODO, "11": SNAP, "e2e": E2E, "CM": CM}
    for key, pat, label in DOC_ANCHORS:
        p = files[key]
        txt = p.read_text(encoding="utf-8") if p.exists() else ""
        hit = re.search(pat, txt) is not None
        if not ok(hit, f"F-{key} {label}"):
            fails.append(f"F-{key} {label}：锚点未命中 {pat}")
    return fails


# ══════════════════════════════════════════════════════════════
# 自检与注入式自证
# ══════════════════════════════════════════════════════════════


def self_test() -> int:
    """判据自身的正反用例（不碰真源码、不碰真库）。"""
    bad = total = 0

    def chk(cond: bool, label: str, extra: str = "") -> None:
        nonlocal bad, total
        total += 1
        if not ok(cond, label, extra):
            bad += 1

    #: 1. is_loopback 正反——**空串必须为 False**（Werkzeug 里空串等于 0.0.0.0，
    #: 是"监听所有网卡"，不是"回环"）
    for host, want in (("127.0.0.1", True), ("localhost", True), ("::1", True),
                       ("[::1]", True), ("0.0.0.0", False), ("", False),
                       ("192.168.1.5", False), ("evil.com", False)):
        got = is_loopback(host)
        chk(got is want, f"is_loopback({host!r}) → {want}", f"实测 {got}")

    #: 2. form_class 三档判据（**全部正反都有**，否则漏一个方向就静默失效）
    for method, keys, want in (("POST", set(), "form"),
                               ("POST", {"a"}, "preflight"),
                               ("DELETE", set(), "noform"),
                               ("POST", {"stage_ids"}, "preflight")):
        got = form_class(method, keys)
        chk(got == want, f"form_class({method}, {sorted(keys)}) → {want}",
            "" if got == want else f"实测 {got}")

    #: 3. 鉴权词表**能命中真的、不命中不该命中的**（用合成串，不碰真源码）
    authy = "if request.headers.get('Authorization') == ..."
    credy = "tok = issue_token(stage_ids); consume_token(tok)"
    chk(any(w in authy.lower() for w in AUTH_TOKENS), "鉴权词表认得 Authorization")
    chk(not any(w in credy.lower() for w in AUTH_TOKENS),
        "鉴权词表**不**把一次性令牌误认成鉴权（凭证面 ≠ 身份面）")

    #: 4. guard_blocked 的判据是 403 **且** message 含「跨源」——只看状态码会把
    #: 业务层的 403 也算进来（判据太宽，§2.5.63）
    class _R:
        def __init__(self, code, msg): self.status_code, self._m = code, msg
        def get_json(self): return {"message": self._m}

    chk(guard_blocked(_R(403, "跨源写请求被拒（Origin 与服务端不一致）")),
        "guard_blocked：守卫的 403 → True")
    chk(not guard_blocked(_R(403, "该操作不被允许")),
        "guard_blocked：业务层的 403 → False（不能只看状态码）")
    chk(not guard_blocked(_R(400, "stage_ids 必须是非空数组")),
        "guard_blocked：业务层的 400（穿过守卫）→ False")

    #: 5. 文档锚点表**覆盖了每一个文件**（防"加一行忘配锚点"）
    chk({k for k, _p, _l in DOC_ANCHORS} == {"01", "09", "10", "11", "e2e"},
        "文档锚点覆盖 01/09/10/11/e2e 五个面",
        f"实测 {sorted({k for k, _p, _l in DOC_ANCHORS})}")

    print(f"self-test：{total - bad}/{total}")
    return 1 if bad else 0


def markers(fails: list[str]) -> list[str]:
    """把失败行压成「档 + 标识」的记号，供注入式自证断言"红的**恰好**是哪几条"。

    §2.5.68 立的规矩：只断言"退出码非 0"太弱——「一律拒绝」与「该拒的拒了」
    在输出上就分不开了。
    """
    out = []
    for f in fails:
        m = re.match(r"([A-Z]\d+(?:-\w+)?)\s", f + " ")
        out.append(m.group(1) if m else f.split()[0])
    return sorted(out)


def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    if "--inject-test" in sys.argv:
        return inject_test()
    if "--runtime" in sys.argv:
        #: 子进程入口：跑 C/D 两段并输出机器可读结果（父进程的注入要靠它）
        print("RT " + json.dumps(run_runtime(), ensure_ascii=False))
        return 0

    print("=" * 72)
    print("§2.5.93 可达面：这些写路由，谁能把请求送到")
    print("=" * 72)

    a = part_a()
    b = part_b()
    rt = run_runtime()
    c = part_c(rt)
    d = part_d(rt)
    e = part_e()
    f = part_f()
    fails = a + b + c + d + e + f
    print("=" * 72)
    if fails:
        for x in fails:
            print(f"  ✗ {x}")
        if "--marks" in sys.argv:
            print("MARKS " + json.dumps(markers(fails), ensure_ascii=False))
        print(f"共 {len(fails)} 项待处置")
        return 1
    if "--marks" in sys.argv:
        print("MARKS []")
    print("✅ 全部通过")
    return 0


# ══════════════════════════════════════════════════════════════
# 注入式自证：判据真的会报红吗（§2.5.64：退出码 0 只有在脚本
# 有能力报 1 时才可信）
# ══════════════════════════════════════════════════════════════

#: 四个注入面，**两个面都测**（§2.5.68）：源码面三例 + 文档面一例。
#:   subs —— 逐个 replace，一处都不许漏（§2.5.89：`replace(old,new,1)` 只换
#:          一处，判据若在别处也有同名子串就会静默失效）
#:   want —— 期望红的**恰好**是哪几条
INJECTS = [
    dict(tag="I1", path=lambda: FACTORY,
         subs=[('if "TRUSTED_HOSTS" not in (config or {}):', "if False:")],
         want=["C1", "C3", "C4"],
         why="把 TRUSTED_HOSTS 那行关掉 ⇒ **正是首版那个 setdefault 静默失效的"
             "形态**：Host 层整层不存在，伪造 Host 全部放行（C1 是「没设上」、"
             "C3/C4 是被它连带的后果——三个视角同一个既成事实，§2.5.86："
             "正确的连带不是遮蔽）"),
    dict(tag="I2", path=lambda: FACTORY,
         subs=[("if request.method not in UNSAFE_METHODS:", "if True:")],
         want=["D3"],
         why="让守卫对所有请求提前返回 ⇒ 跨源写不再被拦（只有 D3 该红："
             "其余三格本来就期望「穿过」，一律放行时它们照样穿过）"),
    dict(tag="I3", path=lambda: CONFIG,
         subs=[('os.getenv("FLASK_HOST", "127.0.0.1")',
                'os.getenv("FLASK_HOST", "0.0.0.0")')],
         want=["A1"],
         why="绑定地址改成 0.0.0.0 ⇒ 暴露到局域网，只剩 A1 该红"),
    dict(tag="I4", path=lambda: E2E,
         subs=[("check_2593_reachability", "__gone_2593__")],
         want=["F-e2e"],
         why="文档面：抹掉 e2e/README 里的脚本名 ⇒ F-e2e 锚点未命中"),
]


def inject_test() -> int:
    """逐个注入、逐个断言，红的**恰好**是那几条。`try/finally` 逐字恢复现场。"""
    print("=" * 72)
    print("§2.5.93 注入式自证：把判据该看见的东西改坏，看它报不报")
    print("=" * 72)
    bad = 0
    for case in INJECTS:
        p: Path = case["path"]()
        orig = p.read_text(encoding="utf-8")
        if any(o not in orig for o, _n in case["subs"]):
            print(f"  ✗ {case['tag']} 锚点未命中（原文里找不到要替换的串）"
                  f"——用例根本没跑，不是判据没报")
            bad += 1
            continue
        mod = orig
        for o, n in case["subs"]:
            mod = mod.replace(o, n)
        try:
            p.write_text(mod, encoding="utf-8")
            r = subprocess.run([sys.executable, str(Path(__file__).resolve()),
                                "--marks"],
                               capture_output=True, text=True, cwd=str(ROOT))
            out = r.stdout + r.stderr
            line = [l for l in out.splitlines() if l.startswith("MARKS ")]
            got = json.loads(line[0][6:]) if line else None
            if got is None:
                print(f"  ✗ {case['tag']} 没读到 MARKS 行（rc={r.returncode}）")
                print("      " + out[-400:].replace("\n", "\n      "))
                bad += 1
                continue
            same = got == sorted(case["want"])
            print(f"  {'✓' if same else '✗'} {case['tag']} 红的恰好是 "
                  f"{case['want']}" + ("" if same else f"　实测 {got}"))
            print(f"      —　{case['why']}")
            if not same:
                bad += 1
        finally:
            p.write_text(orig, encoding="utf-8")
    print(f"inject-test：{len(INJECTS) - bad}/{len(INJECTS)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
