#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""论文采集侧断言 ↔ 代码实测对账（散文数字核验的第五类）。

## 为什么需要它

既有九项核验里，**数字项**（check_numbers_v2.py）只管「清单登记过的统一值」，
**表格项**只查九张表、**图项**只查六张图。而论文 §3.2、§3.3 里有整整一段
**采集侧定量断言**（24 个采集器 / 四族构成 / 17 条查询模板 / 19 个新闻域名 /
1–5 秒间隔 / 15–60 秒超时 / 三次尝试两秒退避 / 未实现四项），它们的源**不在
清单上、不在表里、不在图里**，而在 `construction_agent/collector/` 的代码里。
九项没有任何一项管得到它们——改了代码不跟、改了正文也不跟，两边都发现不了。

本脚本补这一格：把正文断言与代码实测**双向**核。

## 判据分两档

A 段（**硬判据，判红**）：能从代码直接取出、有唯一正确答案的定值断言。
    每条既核「正文写了没有（中英各 ≥1 次）」，也核「代码实测是不是这个值」。
    这一档的判据边界：只核「值相等」，核不到「这句话说得对不对」。

B 段（**登记式弱判据，不判红**）：正文给的是**区间**，而实测区间与正文**不完全
    重合**的三处。它们不是「正文错了」也不是「判据错了」，而是**口径选择**
    （该报哪个子集、语境是哪一轮采集），属作者裁决——按项目惯例（见
    check_tables_v2.py 的 t7_unbacked_cols()）**每次跑动态打印、不参与退出码**。

    ⚠️ 但「不判红」不等于「永久豁免」（§2.5.67）：每条登记都配一条**可证伪
    判据**（REGRESSION），若将来代码真变成了正文写的那个样子，说明登记**已
    过期**，本脚本当场报红——那时该做的是撤登记、而不是继续放着。

用法：python3 scripts/check_paper_source_claims.py [--self-test] [--drift-test]
退出码 0 = A 段全绿且 B 段登记仍成立。
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PAPER = ROOT / 'docs' / 'paper_v2'
FILES = {'EN': PAPER / 'eswa_paper_v2.md', 'ZH': PAPER / 'eswa_paper_v2_zh.md'}
DOC = PAPER / '修订对账记录.md'
# 本节在对账记录里的标题行。C 段的扫描面**收窄到本节**（见 anchors() docstring）：
# 全文档搜索会让第一节对照表的旧文顶包（实测 `17 条查询模板` 就是被它命中的）。
# 截取范围由 `section_body()` 按**二级标题**切段，与本节在文档里的先后无关。
SECTION = '## 第三十七节　采集侧定量声明的逐条实测（2026-10-06）'
SRC = ROOT / 'construction_agent' / 'collector'

# ── 主库：论文数据的源在主仓库，不在本 checkout（项目既有约定） ─────────────
from scripts._common import main_db_path  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# A9 的扫描面：collector/ **整棵树**
# ═══════════════════════════════════════════════════════════════════════════
# ⚠️ 扫描面收窄过一次并付出代价。首版只拼「24 个 sources/*.py + trs_cms_base.py」，
# 于是 A9 报「技术库缺席 ['trafilatura']」—— 而 trafilatura 的**唯一落点**在
# cleaners/text_cleaner.py（sources/ 下零命中）。这正是项目反复记过的
# 「零命中 ≠ 没有，前提是『全仓』真含那份文件」（§2.5.61 / §2.5.90 / §2.5.91）：
# 症状是判据报红，根因在扫描面。MUST_SCAN 是它的**元判据**。
MUST_SCAN = {
    'cleaners/text_cleaner.py',   # trafilatura 的唯一落点
    'pdf_text.py',                # fitz 的主要落点
    'sources/trs_cms_base.py',    # TRS 系公共基类（重试/退避在上游）
}

# ── A9b：运行时可导入性 ─────────────────────────────────────────────────
# A9 核「源码里用了没有」，A9b 核「**当前解释器装得上没有**」—— 两件事：
# 源码写了 import trafilatura 不代表装得上；而装不上时会不会出事，
# 取决于那处 import **有没有 ImportError 兜底**（AST 判，正则看不出 try 结构）。
RUNTIME_LIBS = [
    ('httpx', 'httpx'), ('BeautifulSoup', 'bs4'),
    ('trafilatura', 'trafilatura'), ('PyMuPDF', 'fitz'),
]
FALLBACK_HANDLERS = {'ImportError', 'ModuleNotFoundError', 'Exception', 'BaseException'}

# 运行时**装不上**但不算缺陷的库（可选依赖）。登记不是豁免（§2.5.67）：
# 每条都有可证伪判据 —— 装上之日登记即过期（报红），兜底锚点消失之日登记理由失效（报红）。
KNOWN_ABSENT = {
    'trafilatura': dict(
        module='trafilatura',
        guard=('cleaners/text_cleaner.py', 'trafilatura'),
        why='可选依赖：唯一的 `import trafilatura` 写在带 `except ImportError: pass` 的 '
            'try 内，失败即回退 BeautifulSoup（cleaners/text_cleaner.py:92-120）。'
            '由此带出**两处作者裁决项**：①正文写「extraction prefers trafilatura where '
            'page structure permits」，而代码的分支条件是**模块可导入**、不是「页面结构允许」；'
            '②requirements.txt 未声明它，本环境实测缺席 ⇒ 正文描述的那条优先分支'
            '**从未执行过**（一直走回退分支）。',
    ),
}

# ── A9c：依赖声明面（B 段登记式，不判红） ────────────────────────────────
# 论文点名的四个库 + text_cleaner 用的 lxml 后端，在 requirements.txt 里各声明了没有。
DECLARED_PKGS = ['httpx', 'beautifulsoup4', 'lxml', 'PyMuPDF', 'trafilatura']


# ═══════════════════════════════════════════════════════════════════════════
# A 段：正文锚点表（中英各须出现 ≥1 次）
# ═══════════════════════════════════════════════════════════════════════════
CLAIMS = [
    ('A1', '采集器总数 24',
     r'Twenty-four collectors', r'二十四个采集器'),
    ('A2', '四族构成 1 聚合器 / 18 省级 / 2 国家级 / 3 其余',
     r'one search-engine aggregator', r'一个搜索引擎聚合器'),
    ('A2b', '四族：18 个省级',
     r'eighteen provincial emergency-management', r'十八个省级应急管理'),
    ('A2c', '四族：2 个国家级',
     r'two national-level collectors', r'两个国家级采集器'),
    ('A3', '两个门户合计 1,636 条（62.4%）',
     r'1,636 of the 2,623 records', r'2,623 条中的 1,636 条'),
    ('A4', '17 条预定义查询模板',
     r'17 predefined query templates', r'17 条预定义查询模板'),
    ('A5', '19 个新闻域名白名单',
     r'19 news domains', r'19 个新闻域名'),
    ('A6', '外加任意 gov.cn 主机',
     r'plus any `gov\.cn` host', r'外加任意 `gov\.cn` 主机'),
    ('A7', '每个 URL 三次尝试',
     r'three retrieval attempts per URL', r'每个 URL 三次尝试'),
    ('A8', '两秒退避',
     r'two-second backoff', r'两秒退避'),
    ('A9', '统一客户端库 httpx',
     r'\(httpx, timeouts of', r'（httpx，超时'),
    ('A10', '未实现四项（代理轮换/UA 轮换/JS 渲染/验证码）',
     r'proxy rotation, user-agent rotation, JavaScript rendering, or CAPTCHA',
     r'代理轮换、User-Agent 轮换、JavaScript 渲染或验证码'),
    ('A11', '完全串行而非分布式',
     r'entirely serial rather than distributed', r'完全串行而非分布式'),
    ('A12', 'HTML 以 BeautifulSoup 解析',
     r'HTML is parsed with BeautifulSoup', r'以 BeautifulSoup 解析'),
    ('A13', '正文抽取优先 trafilatura',
     r'extraction prefers trafilatura', r'抽取优先使用 trafilatura'),
    ('A14', 'PDF 以 PyMuPDF 抽取',
     r'PDFs are extracted with PyMuPDF', r'以 PyMuPDF 抽取'),

    # 区间断言（值本身由 B 段核，这里只核正文写了没有）
    ('B1', '超时区间 15–60 秒',
     r'timeouts of 15–60 s', r'超时 15–60 秒'),
    ('B2', '请求间隔 1–5 秒',
     r'a serial inter-request delay of 1–5 s', r'1–5 秒的串行请求间隔'),
]

# 若这些锚点在正文里出现 0 次或只在一版出现 ⇒ A 段报红。
# 除外：区间断言的**值**已在 B 段登记（正文数字可能与实测不符，但锚点本身必须在位）。


# ═══════════════════════════════════════════════════════════════════════════
# 实测取值
# ═══════════════════════════════════════════════════════════════════════════
def read_paper():
    out = {}
    for tag, p in FILES.items():
        out[tag] = p.read_text(encoding='utf-8') if p.exists() else ''
    return out


def load_sources():
    """返回 (源列表, 每源的实例, 失败明细)。"""
    from construction_agent.collector.registry import all_sources
    srcs = all_sources()
    insts, fail = {}, []
    for s in srcs:
        key, mod, cls = s.key, s.module, s.cls
        last = ''
        for full in (mod, f'construction_agent.collector.sources.{mod}'):
            try:
                m = __import__(full, fromlist=['x'])
                insts[key] = (getattr(m, cls)(), full)
                break
            except Exception as e:  # noqa: BLE001
                last = f'{type(e).__name__}: {e}'
        else:
            fail.append((key, f'{mod}: {last}'))
    return srcs, insts, fail


def source_files(srcs):
    """24 源各自的源码文件路径（A1–A8 的逐源扫描用；A9/A10 用 tree_files()）。"""
    out = {}
    for s in srcs:
        mod = s.module
        for cand in (SRC / 'sources' / f'{mod}.py',):
            if cand.exists():
                out[s.key] = cand
    return out


def tree_files():
    """collector/ 整棵树的 .py（**不是**只要 sources/）。见 MUST_SCAN 上方注释。"""
    return sorted(p for p in SRC.rglob('*.py') if '__pycache__' not in p.parts)


def scan_face_ok(tree):
    """元判据：扫描面须含 MUST_SCAN 那几处。返回**缺失**的项（空 = 通过）。

    防的是文件搬家把扫描面**静默缩小** —— 上一次的教训就是扫描面少了半棵树而
    没有任何东西吭声。搬家后此判据报红，逼人重看扫描面，而不是让 A9 再报一次假缺陷。
    """
    rel = {p.relative_to(SRC).as_posix() for p in tree}
    return sorted(MUST_SCAN - rel)


def import_sites(tree):
    """{模块名: [(相对路径, 行号, 是否在带 ImportError 兜底的 try 体内), …]}。

    兜底判定用 **AST 而非正则** —— 「这个 import 写在 try 里、那个 try 里有没有
    ImportError handler」是**结构**，正则看不出；而它决定 A9b 判「硬依赖缺席（真缺陷）」
    还是「可选依赖缺席（登记即可）」。逐行号收 try.body 的全部行号（import 可能嵌在
    内层语句里）。
    """
    sites = {}
    for p in tree:
        rel = p.relative_to(SRC).as_posix()
        try:
            node = ast.parse(p.read_text(encoding='utf-8'))
        except SyntaxError:      # 语法坏的文件不冒充"没有 import"
            sites.setdefault('<语法错误>', []).append((rel, 0, False))
            continue
        guarded = set()
        for t in ast.walk(node):
            if not isinstance(t, ast.Try):
                continue
            names = set()
            bare = False
            for h in t.handlers:
                if h.type is None:
                    bare = True
                elif isinstance(h.type, ast.Name):
                    names.add(h.type.id)
                elif isinstance(h.type, ast.Attribute):
                    names.add(h.type.attr)
            if not (bare or names & FALLBACK_HANDLERS):
                continue
            for st in t.body:
                for n in ast.walk(st):
                    if hasattr(n, 'lineno'):
                        guarded.add(n.lineno)
        for n in ast.walk(node):
            mods = []
            if isinstance(n, ast.Import):
                mods = [a.name.split('.')[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module:
                mods = [n.module.split('.')[0]]
            for m in mods:
                sites.setdefault(m, []).append((rel, n.lineno, n.lineno in guarded))
    return sites


def runtime_availability():
    """当前解释器里这几个库装得上吗。find_spec 只查得到与否，不执行模块。"""
    out = {}
    for label, mod in RUNTIME_LIBS:
        try:
            out[label] = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):
            out[label] = False
    return out


def declared_packages():
    """requirements.txt 里声明了 DECLARED_PKGS 中的哪几个（找不到文件返回 None）。"""
    req = ROOT / 'requirements.txt'
    if not req.exists():
        return None
    txt = req.read_text(encoding='utf-8').lower()
    return [p for p in DECLARED_PKGS if p.lower() in txt]


def runtime_facts(m):
    """A9b 四向判据。返回与 part_a 同形的 (cid, ok, got, want) 列表。

    「可选依赖缺席」与「硬依赖缺席」不是一回事，故两档分开判：
      · 不可导入 ∧ 有一处 import **无兜底** ⇒ 红（裸 import 会直接 ImportError）
      · 不可导入 ∧ 全部 import 都有 ImportError 兜底 ∧ **未登记** ⇒ 红
        （缺席成立了但没人记录它 —— 登记不是为了豁免，是为了让「缺席」有一个会过期的交代）
      · 可导入 ∧ **已在 KNOWN_ABSENT 登记** ⇒ 红（登记过期，双向核，§2.5.67）
      · 登记项的兜底锚点（文件 + import 名）在源码里找不到 ⇒ 红（登记理由已失效）
    """
    facts = []
    known = m['known_absent']
    for label, mod in RUNTIME_LIBS:
        sites = m['import_sites'].get(mod, [])
        ok_import = m['runtime'].get(label, False)
        reg = known.get(label)
        if not sites and not ok_import and not reg:
            continue            # 库压根没被引用、也没登记 ⇒ 不归本判据管
        if ok_import:
            facts.append((f'A9b/{label}', not reg,
                          '可导入' if not reg else
                          f'已可导入，但登记为「缺席」（{reg["guard"][0]}）',
                          '可导入' if not reg else '登记过期须撤，或说明为何仍缺席'))
            continue
        unguarded = [f'{f}:{ln}' for f, ln, g in sites if not g]
        if unguarded:
            facts.append((f'A9b/{label}', False,
                          f'不可导入且有 {len(unguarded)} 处**无兜底**的 import'
                          f'（{unguarded[0]}）',
                          '硬依赖缺席：裸 import 会 ImportError'))
            continue
        if not reg:
            facts.append((f'A9b/{label}', False,
                          '不可导入，但全部 import 都有 ImportError 兜底，**未登记**',
                          '缺席须登记（登记 + 可证伪判据，才谈得上"可接受"）'))
            continue
        gf, gm = reg['guard']
        anchor = SRC / gf
        txt = anchor.read_text(encoding='utf-8') if anchor.exists() else ''
        if gm not in txt:
            facts.append((f'A9b/{label}', False,
                          f'登记的兜底锚点已失效：{gf} 里找不到 {gm!r}',
                          '登记理由不再成立，该重新裁决'))
        else:
            facts.append((f'A9b/{label}', True,
                          f'不可导入但有兜底且已登记（{gf}）', '可选依赖缺席，已登记'))
    return facts


def scan_literals(files, pattern):
    """在源文件里扫正则，返回 [(key, 值, 文件:行号, 所在行)]。"""
    rx = re.compile(pattern)
    hits = []
    for key, p in sorted(files.items()):
        for i, line in enumerate(p.read_text(encoding='utf-8').split('\n'), 1):
            for m in rx.finditer(line):
                hits.append((key, float(m.group(1)), f'{p.name}:{i}', line.strip()))
    return hits


def measure():
    """全部实测值。任一步失败都如实抛错，不降级成空集合（§2.5.73）。"""
    m = {}
    srcs, insts, fail = load_sources()
    files = source_files(srcs)
    m['n_sources'], m['inst_fail'], m['n_inst'] = len(srcs), fail, len(insts)

    # ── 四族构成：按 registry 的 group 计 ──────────────────────────────
    grp = {}
    for s in srcs:
        grp[s.group] = grp.get(s.group, 0) + 1
    m['groups'] = grp
    # 正文四族：1 聚合器 + 18 省级 + 2 国家级 + 3 其余（2 门户 + 1 开放数据）
    m['family_counts'] = (
        1,                                     # search-engine aggregator
        grp.get('province', 0),                # 省级应急厅
        grp.get('national', 0),                # 国家级
        grp.get('media', 0) - 1 + grp.get('open_data', 0),   # 其余 = media−聚合器 + open_data
    )

    # ── 查询模板 / 新闻域名（news_aggregator 的模块级常量） ─────────────
    from construction_agent.collector.sources import news_aggregator as na
    m['n_queries'] = len(na.SEARCH_QUERIES)
    m['n_domains'] = len(na.NEWS_DOMAINS)
    gov = SRC / 'sources' / 'news_aggregator.py'
    gt = gov.read_text(encoding='utf-8')
    m['govcn_gate'] = bool(re.search(r'gov\.cn', gt))
    # 白名单里混着政府域：正文统称 news domains（措辞精度，登记）
    m['domains_gov'] = sorted(d for d in na.NEWS_DOMAINS if 'gov.cn' in d)

    # ── 重试次数与退避 ────────────────────────────────────────────────
    trs = (SRC / 'sources' / 'trs_cms_base.py').read_text(encoding='utf-8')
    mm = re.search(r'max_retries:\s*int\s*=\s*(\d+)', trs)
    m['trs_max_retries'] = int(mm.group(1)) if mm else None
    m['expo_backoff'] = bool(re.search(r'time\.sleep\(\s*2\s*\*\*\s*attempt\s*\)', trs))
    # 7 个非 TRS 源：固定 sleep(2)
    fixed = []
    for key, p in sorted(files.items()):
        t = p.read_text(encoding='utf-8')
        if 'trs_cms_base' in t or p.name == 'trs_cms_base.py':
            continue
        if re.search(r'if\s+attempt\s*<\s*2:\s*\n\s*time\.sleep\(\s*2(?:\.0)?\s*\)', t):
            fixed.append(key)
    m['fixed_backoff_sources'] = sorted(fixed)

    # ── 技术库存在性（扫描面 = collector/ 整棵树，见 MUST_SCAN 上方注释） ──
    tree = tree_files()
    m['scan_n'] = len(tree)
    m['scan_missing'] = scan_face_ok(tree)
    allsrc = '\n'.join(p.read_text(encoding='utf-8') for p in tree)
    m['libs'] = {lib: bool(re.search(pat, allsrc)) for lib, pat in (
        ('httpx', r'\bimport\s+httpx|\bfrom\s+httpx\b'),
        ('BeautifulSoup', r'\bBeautifulSoup\b'),
        ('trafilatura', r'\btrafilatura\b'),
        ('PyMuPDF', r'\bfitz\b|\bPyMuPDF\b'),
    )}

    # ── 未实现四项：采集器源码里的零命中 ──────────────────────────────
    m['unimpl'] = {k: bool(re.search(pat, allsrc, re.I)) for k, pat in (
        ('proxy rotation', r'prox(?:y|ies)\s*=\s*(?:\[|\{)'),
        ('user-agent rotation', r'USER_AGENTS?\s*=|rotate.*user.?agent'),
        ('JavaScript rendering', r'\bplaywright\b|\bselenium\b|webdriver'),
        ('CAPTCHA', r'captcha'),
    )}

    # ── A9b：源码用了 ≠ 装得上；装不上时看那处 import 有没有兜底 ────────
    m['import_sites'] = import_sites(tree)
    m['runtime'] = runtime_availability()
    m['known_absent'] = KNOWN_ABSENT
    m['req_declared'] = declared_packages()
    m['req_missing'] = ([p for p in DECLARED_PKGS if p not in m['req_declared']]
                        if m['req_declared'] is not None else None)

    # ── B1 超时区间：扫描 24 源源码 + TRS 基类的 timeout 实参 ─────────
    tf = dict(files)
    tf['__trs_base__'] = SRC / 'sources' / 'trs_cms_base.py'
    to_hits = scan_literals(tf, r'timeout\s*=\s*([\d.]+)')
    m['timeout_hits'] = to_hits
    to_vals = sorted({v for _, v, _, _ in to_hits})
    # 构造器里的 cfg.timeout 默认值也计入（TRS 系）
    mm = re.search(r'timeout:\s*int\s*=\s*(\d+)', trs)
    if mm:
        to_vals = sorted(set(to_vals) | {float(mm.group(1))})
    m['timeout_vals'] = to_vals

    # 未启用的源（不在 registry）若含更大值，单独列出以自证「已剔除」
    acad = SRC / 'sources' / 'academic_import.py'
    m['academic_timeout'] = None
    if acad.exists():
        a = re.findall(r'timeout\s*=\s*([\d.]+)', acad.read_text(encoding='utf-8'))
        m['academic_timeout'] = sorted({float(x) for x in a}) or None
    m['academic_in_registry'] = any(s.module == 'academic_import' for s in srcs)

    # ── B2 间隔区间：实例属性 + 字面量 sleep ──────────────────────────
    delays = {}
    for key, (obj, _full) in insts.items():
        d = getattr(obj, '_delay', None)
        if d is None:
            cfg = getattr(obj, 'cfg', None)
            d = getattr(cfg, 'delay', None)
        if isinstance(d, (int, float)):
            delays[key] = float(d)
    m['delay_by_source'] = dict(sorted(delays.items(), key=lambda x: x[1]))
    m['delay_main'] = (min(delays.values()), max(delays.values())) if delays else None
    m['delay_getcrawl'] = sorted({round(float(o.get_crawl_delay()), 3)
                                 for o, _ in insts.values()
                                 if callable(getattr(o, 'get_crawl_delay', None))})
    sl = scan_literals(tf, r'time\.sleep\(\s*([\d.]+)\s*\)')
    m['sleep_literals'] = [(k, v, loc) for k, v, loc, _ in sl]

    # ── B3 采集并发形态 ───────────────────────────────────────────────
    ex = (SRC / 'executor.py').read_text(encoding='utf-8')
    mm = re.search(r'DEFAULT_CONCURRENCY\s*=\s*(\d+)', ex)
    m['exec_default_concurrency'] = int(mm.group(1)) if mm else None
    m['exec_pool'] = bool(re.search(r'ThreadPoolExecutor\(\s*max_workers\s*=', ex))
    m['runbatch_pool'] = bool(re.search(r'ThreadPoolExecutor',
                                        (ROOT / 'scripts' / 'run_batch.py').read_text(encoding='utf-8')))
    # 跨源并行的作者自陈（注释）
    m['exec_comment'] = '跨源并行' in ex

    # ── 主库：两个门户的条数与占比 ────────────────────────────────────
    m['db'] = None
    try:
        con = sqlite3.connect(f'file:{main_db_path()}?mode=ro', uri=True)
        rows = dict(con.execute(
            'SELECT source_name, COUNT(*) FROM accidents GROUP BY source_name'))
        total = con.execute('SELECT COUNT(*) FROM accidents').fetchone()[0]
        con.close()
        port = {k: v for k, v in rows.items()
                if 'safehoo' in k.lower() or 'esafety' in k.lower()}
        m['db'] = {'total': total, 'portals': port,
                   'portal_sum': sum(port.values()),
                   'portal_pct': round(sum(port.values()) / total * 100, 1) if total else None}
    except Exception as e:  # noqa: BLE001
        m['db'] = {'error': f'{type(e).__name__}: {e}'}
    return m


# ═══════════════════════════════════════════════════════════════════════════
# A 段判据：锚点在位（中英对称）+ 代码实测与正文相符
# ═══════════════════════════════════════════════════════════════════════════
def part_a(paper, m):
    reds, lines = [], []
    for cid, desc, en, zh in CLAIMS:
        ne = len(re.findall(en, paper['EN']))
        nz = len(re.findall(zh, paper['ZH']))
        if ne == 0 and nz == 0:
            reds.append(f'{cid} 锚点两版均缺：{desc}')
        elif ne == 0 or nz == 0:
            reds.append(f'{cid} 锚点仅{"ZH" if ne == 0 else "EN"}版有：{desc}')
        lines.append((cid, desc, ne, nz))

    # 代码实测 ↔ 正文定值
    facts = []
    n = m['n_sources']
    facts.append(('A1', n == 24, f'all_sources() = {n}', '正文写 24'))
    fam = m['family_counts']
    facts.append(('A2', fam == (1, 18, 2, 3), f'四族实测 {fam[0]}/{fam[1]}/{fam[2]}/{fam[3]}',
                  '正文写 1 聚合器 / 18 省级 / 2 国家级 / 3 其余'))
    db = m['db'] or {}
    if db.get('error'):
        facts.append(('A3', False, f'主库不可读：{db["error"]}', '正文写 1,636 条 / 62.4%'))
    else:
        ok3 = db['portal_sum'] == 1636 and db['portal_pct'] == 62.4
        facts.append(('A3', ok3,
                      f'门户合计 {db["portal_sum"]} 条 / {db["portal_pct"]}%',
                      '正文写 1,636 条 / 62.4%'))
    facts.append(('A4', m['n_queries'] == 17, f'SEARCH_QUERIES = {m["n_queries"]}', '正文写 17'))
    facts.append(('A5', m['n_domains'] == 19, f'NEWS_DOMAINS = {m["n_domains"]}', '正文写 19'))
    facts.append(('A6', m['govcn_gate'], f'gov.cn 放行规则 = {m["govcn_gate"]}', '正文写「任何 gov.cn 主机」'))
    ok7 = m['trs_max_retries'] == 3 and m['fixed_backoff_sources']
    facts.append(('A7', ok7,
                  f'TRS max_retries={m["trs_max_retries"]}；固定退避源 {len(m["fixed_backoff_sources"])} 个',
                  '正文写「三次尝试」'))
    # 「两秒退避」的**退避路径上**须真有一处 2 秒：两种形态任一成立即可——
    # 非 TRS 源的固定 `time.sleep(2)`，或 TRS 的指数退避 2**attempt（1s→2s，第二跳是 2 秒）。
    # ⚠️ 首版此处写成 `('A8', True, …)`：恒真的判据等于没有判据（§2.5.64）。
    has_fixed_2s = 2.0 in {v for _, v, _ in m['sleep_literals']}
    ok8 = has_fixed_2s or m['expo_backoff']
    facts.append(('A8', ok8,
                  f'固定 sleep(2) 字面量 {has_fixed_2s}；TRS 指数退避 {m["expo_backoff"]}',
                  '正文写「两秒退避」'))
    facts.append(('A9s', not m['scan_missing'],
                  f'扫描面 {m["scan_n"]} 个 .py，缺失 {m["scan_missing"] or "无"}',
                  '扫描面须覆盖 collector/ 整棵树（元判据）'))
    bad = [k for k, v in m['libs'].items() if not v]
    facts.append(('A9', not bad, f'技术库缺席 {bad or "无"}', '正文写 httpx/BS4/trafilatura/PyMuPDF'))
    # A9b：源码用了（A9 已核）**且**当前解释器装得上吗。两件事分开判，见 runtime_facts。
    facts.extend(runtime_facts(m))
    hit = [k for k, v in m['unimpl'].items() if v]
    facts.append(('A10', not hit, f'未实现四项中有命中的 {hit or "无"}', '正文写「未实现」'))
    for cid, ok, got, want in facts:
        if not ok:
            reds.append(f'{cid} 实测不符：{got}（{want}）')

    return reds, lines, facts


# ═══════════════════════════════════════════════════════════════════════════
# B 段：登记项（**不判红**，但每条配可证伪判据 —— 登记成立/过期双向核，§2.5.67）
# ═══════════════════════════════════════════════════════════════════════════
REGISTERED = [
    dict(cid='B1', title='超时区间',
         paper='15–60 s',
         why='24 源里上界只到 30（TRS 基类 cfg.timeout 与各构造器），'
             '60 的唯一出处是 academic_import.py（registry.py:33 明写「本期没有源落进去」）；'
             '旁证 60 另见于图片分析 API 与 executor 的 futures 超时，都**不是 collector**。'
             'git log -S 证明 60 从快照 1c14cde 时就在，不是后来被改掉。',
         get=lambda m: m['timeout_vals'],
         cond=lambda m: max(m['timeout_vals']) <= 30,
         desc=lambda m: f'实测 {min(m["timeout_vals"])}–{max(m["timeout_vals"])}'),
    dict(cid='B2', title='请求间隔',
         paper='1–5 s',
         why='每源主间隔实测下限 0.5（esafety），上界 5.0（open_gov 的 get_crawl_delay()'
             '覆写，全仓唯一调用点）；'
             '把翻页/详情页/查询间隔一并算入则区间更宽（hebei_em 列表翻页硬编码 0.2、'
             'news_aggregator 查询间隔 _delay*2 = 6.0）——文案「per collector」的口径'
             '决定了该取哪一档，属作者裁决。',
         get=lambda m: (m['delay_main'], m['sleep_literals']),
         cond=lambda m: m['delay_main'] and m['delay_main'][0] < 1.0,
         desc=lambda m: f'每源主间隔 {m["delay_main"][0]}–{m["delay_main"][1]}'
                        f'（{len(m["delay_by_source"])} 源）'),
    dict(cid='B3', title='entirely serial',
         paper='collection is entirely serial rather than distributed',
         why='executor.py:50 的作者注释明写「而 24 个源各自是独立站点，跨源并行不会给'
             '任一站点加压」，紧接 DEFAULT_CONCURRENCY = 3。'
             '⚠️ 这一条是**时序**问题：语料（2,623 条）采于 2026-07/08，'
             '而 web 采集执行器是 2026-09-30 之后建的——正文描述的是**采集当期**的形态，'
             '而系统的**今天**是跨源并发。改与不改取决于论文想描述哪一个时点。',
         get=lambda m: (m['exec_default_concurrency'], m['exec_pool'], m['exec_comment']),
         cond=lambda m: (m['exec_default_concurrency'] or 1) > 1,
         desc=lambda m: f'executor 默认并发 {m["exec_default_concurrency"]}'
                        f'；线程池 {m["exec_pool"]}；注释自陈跨源并行 {m["exec_comment"]}'),
    # B4 与前三条**性质不同**：前三条是「正文写了一个数、实测对不上」，
    # 这一条正文一个字都没提（依赖清单不进论文），它是**仓库卫生项**。
    # 放进 B 段而不是 A 段，是因为「requirements.txt 缺声明」不构成论文缺陷；
    # 但它必须**可证伪**——补全之日就是登记过期之日（§2.5.67：登记不是豁免）。
    dict(cid='B4', title='依赖声明面',
         paper='（正文未涉及；本条是仓库卫生项）',
         why='collector/ 整树用到四个第三方库，requirements.txt 只声明了 httpx。'
             '⚠️ 与 A9b 分工：A9b 判「装不上会不会出事」（trafilatura 有 ImportError 兜底故不判红），'
             '本条判「`pip install -r requirements.txt` 能不能把这个仓库跑起来」——'
             '缺声明 ≠ 今天跑不动（本环境 bs4/lxml/fitz 实测都在），'
             '但换台机器重建环境就会缺，且缺的正是解析与 PDF 抽取这两个必经环节。'
             '另：正文写 trafilatura 在抽取链里「优先」，而 requirements.txt 根本没提它，'
             '两处说法的一致性属作者裁决（与 KNOWN_ABSENT 里那条是同一个问题的一面）。',
         get=lambda m: (m['req_declared'], m['req_missing']),
         cond=lambda m: m['req_declared'] is not None and bool(m['req_missing']),
         desc=lambda m: (f'requirements.txt 声明 {len(m["req_declared"] or [])}'
                         f'/{len(DECLARED_PKGS)} 个，未声明 {m["req_missing"] or "无"}'),
         # cond=False 有两种成因、消息不能共用（文件不在 ≠ 登记过期）：
         # 前者是**源不可达**，按 §2.5.73 不得降级成「无待办」。
         fail=lambda m: ('requirements.txt 不存在 —— 源不可达，'
                         '不降级成"无待办"（§2.5.73）'
                         if m['req_declared'] is None else
                         f'四个库已在 requirements.txt 全部声明（{m["req_declared"]}）'
                         '—— 登记过期，该撤')),
]


def part_b(m):
    reds, lines = [], []
    for r in REGISTERED:
        held = bool(r['cond'](m))
        lines.append((r['cid'], r['title'], r['paper'], r['desc'](m), r['why'], held))
        if not held:
            msg = (r['fail'](m) if 'fail' in r else
                   f'正文写「{r["paper"]}」，而实测已变成登记所描述的反面'
                   f'（{r["desc"](m)}）——该撤登记、重新裁决')
            reds.append(f'{r["cid"]} 登记已过期：{msg}')
    return reds, lines


# ═══════════════════════════════════════════════════════════════════════════
# C 段：文档对账
# ═══════════════════════════════════════════════════════════════════════════
def anchors(m):
    """本节在「修订对账记录」里应出现的**带语境的**锚点串。

    ⚠️ 首版核的是裸数字（`str(m['n_sources'])` 等），那是**恒真判据**：
    `24` / `17` / `19` 在这份 ~3 000 行文档里各出现几十次（实测 22 / 56 / 31），
    「出现」必然成立 ⇒ 就算本节整个删掉也照样绿（§2.5.64
    「恒真判据等于没有判据」）。故锚点一律带语境词。
    代价是脆——文档措辞一改就判红；这是**宁可报红不可假绿**的取舍，
    且报红时消息里会印出期望串，改文档照着对齐即可。

    ⚠️ 但**带语境还不够**：实测 `17 条查询模板` 被**第一节对照表的旧文**命中
    （那一行写着「25 个采集器…15 域名白名单」，本已与实测 24/19 不符却仍含该串），
    ⇒ 本节即便不登记也照样绿。故 `part_c` 另把扫描面**收窄到本节**（§2.5.66
    「判据必须收窄接收者」；§2.5.90 的 `scan_lines()` 是同款动作）。
    """
    return [
        ('本脚本已登记', 'check_paper_source_claims'),
        ('采集器总数', f'{m["n_sources"]} 个采集器'),
        ('查询模板', f'{m["n_queries"]} 条查询模板'),
        ('新闻域名', f'{m["n_domains"]} 个新闻域名'),
        ('扫描面文件数', f'collector/ 整树 {m["scan_n"]} 个 .py'),
        ('依赖声明面', f'requirements.txt 只声明 {len(m["req_declared"] or [])}'),
    ]


def section_body(t, head):
    """从文档全文里取出 `head` 这一节的正文（到**下一个二级标题**为止；末节取到末尾）。

    ⚠️ 抽成纯函数是为了能被 `--self-test` 直接喂合成文本验——旧版把切片写死在
    `part_c` 里，只能靠"改真实文档"来验，而那条路径正是本轮被证伪的
    （它要求本节是文档末节）。

    ⚠️ `'\\n## '` 不会误匹配 `'\\n### '`（后者第三个字符是 `#` 而不是空格），
    故子标题不会把本节提前截断。
    """
    i0 = t.index(head)
    nxt = t.find('\n## ', i0 + 1)
    return t[i0:] if nxt < 0 else t[i0:nxt]


def part_c(m):
    reds = []
    if not DOC.exists():
        return [f'缺文档：{DOC} —— 源不可达，不降级成"无待办"（§2.5.73）']
    t = DOC.read_text(encoding='utf-8')

    # 接收者收窄（见 anchors() docstring）：锚点必须是**本节**写的。
    heads = [ln.rstrip() for ln in t.split('\n') if ln.startswith('## ')]
    if SECTION not in heads:
        return [f'修订对账记录里没有 `{SECTION}` 标题 —— 本节尚未登记'
                f'（当前末节：{heads[-1] if heads else "无"}）']
    # 截取范围 = **本节内容**，到下一个二级标题为止（见 section_body()）。
    #
    # ⚠️ 旧版写 `t[t.index(SECTION):]`（截到文件末尾），另配一条元判据要求
    # 「SECTION 必须是末节」。那条元判据的**前提本身**是脆的：它把"本节恰好
    # 排在文档最后"这个**偶然的文档位置**当成了判据成立的必要条件，于是每加
    # 一个新节都要回头改一次 SECTION 常量（2026-10-07 写第三十八节时正是如此）。
    # 病根在截取方式：截到末尾会把**后面的新节**一起扫进 body（新节若也写了
    # 「N 个采集器」就会假绿），而元判据只是提醒人去改常量、没治病。
    # 按二级标题切段后 body 恒等于本节内容，与它的位置无关
    # （§2.5.75「写死的固定值本身可能就是响应式的」）。
    body = section_body(t, SECTION)
    for label, val in anchors(m):
        if val not in body:
            reds.append(f'第三十七节缺锚点：{label} ⇒ 期望串「{val}」')
    return reds


# ═══════════════════════════════════════════════════════════════════════════
# D 段：--self-test（判据自身的正反用例）
# ═══════════════════════════════════════════════════════════════════════════
def self_test():
    cases = []
    # 锚点正反例：用**真实正文**（真值面），不用把正则串再喂给它自己的自指写法
    # ——首版此处写成 `... or True`，是恒真的废话用例（§2.5.64）。
    real = {tag: p.read_text(encoding='utf-8') for tag, p in FILES.items()}
    miss = [(cid, tag) for cid, _, en, zh in CLAIMS for tag, pat in
            (('EN', en), ('ZH', zh)) if not re.search(pat, real[tag])]
    cases.append((f'锚点正例：{len(CLAIMS)} 条 × 中英各 1 在真实正文命中', miss == [], miss))
    cases.append(('锚点反例：把 24 改成 23 ⇒ A1 的 EN 锚点不再命中',
                  not re.search(CLAIMS[0][2], real['EN'].replace('Twenty-four', 'Twenty-three')),
                  None))
    # 判据函数正反
    fake_ok = {'n_sources': 24, 'family_counts': (1, 18, 2, 3), 'n_queries': 17, 'n_domains': 19,
               'govcn_gate': True, 'trs_max_retries': 3, 'fixed_backoff_sources': ['a'],
               # A8 的判据（2026-10-06 由恒真改为真判据）读两个键，假 m 里必须都给：
               # 漏掉 expo_backoff 会让 part_a 直接 KeyError ⇒ 自测本身崩掉，
               # 而"崩掉"与"判据报红"在输出上都是非 0，分不清是脚本坏了还是判据生效。
               'expo_backoff': True, 'sleep_literals': [],
               'libs': {'httpx': True, 'BeautifulSoup': True, 'trafilatura': True, 'PyMuPDF': True},
               'unimpl': {'a': False, 'b': False}, 'timeout_vals': [15.0, 30.0],
               'db': {'total': 2623, 'portals': {'safehoo': 1247, 'esafety': 389},
                      'portal_sum': 1636, 'portal_pct': 62.4},
               'delay_main': (0.5, 5.0), 'delay_by_source': {},
               'exec_default_concurrency': 3, 'exec_pool': True, 'exec_comment': True,
               # ── A9s / A9b / B4 读的新键（2026-10-06 补）──────────────────
               # ⚠ 与上面 expo_backoff 那条同款：判据读了新键而 fake_ok 没给，
               # part_a 直接 KeyError ⇒ 自测崩掉；而"崩掉"与"判据报红"在输出上
               # 都是非 0，分不清是脚本坏了还是判据生效。
               'scan_n': 46, 'scan_missing': [],
               'import_sites': {
                   'httpx': [('sources/beijing_em.py', 12, False)],
                   'bs4': [('sources/beijing_em.py', 13, False)],
                   'trafilatura': [('cleaners/text_cleaner.py', 98, True)],
                   'fitz': [('pdf_text.py', 30, True)],
               },
               'runtime': {'httpx': True, 'BeautifulSoup': True,
                           'trafilatura': False, 'PyMuPDF': True},
               'known_absent': KNOWN_ABSENT,
               'req_declared': ['httpx'],
               'req_missing': ['beautifulsoup4', 'lxml', 'PyMuPDF', 'trafilatura']}
    # 正例的正文一律用**真值面** `real`，不用把 CLAIMS 里的**正则串**拼起来冒充正文：
    # 锚点存的是正则（含转义，如 A6 的 `gov\.cn`），拼出来的"正文"里带反斜杠，
    # 正则自然匹配不上 —— 这四处首次跑全部报假红（2026-10-06），根因是用例错、
    # 不是判据错，且假红会把人引去改本来正确的锚点。
    #
    # ⚠ 下面**所有** part_a 用例的正文也一律用 `real`，不能图省事写 `{'EN':'x','ZH':'x'}`：
    # 内容判据与锚点判据**共用同一个 ID 前缀**（锚点缺失报的是 `A8 锚点两版均缺：…`，
    # 同样 `startswith('A8')`），空正文会让锚点集体缺失、把每条 `any(x.startswith('An'))`
    # 撑成**真空绿**——判据内容坏掉、甚至整段删掉，用例照样通过（同 §2.5.64
    # 「恒真判据」与 §2.5.68「断言要精确到集合」）。用 real ⇒ 锚点全中，
    # 唯一可能的红就只剩被测的那一条内容判据。
    r, _, _ = part_a(real, fake_ok)
    cases.append(('A 段全正例 ⇒ 无红', r == [], r))
    r, _ = part_b(fake_ok)
    cases.append(('B 段四登记全成立 ⇒ 无红', r == [], r))

    # 反例：次数不符
    bad = dict(fake_ok, n_queries=16)
    r, _, _ = part_a(real, bad)
    cases.append(('反例 n_queries=16 ⇒ 报 A4', any(x.startswith('A4') for x in r), r))
    bad = dict(fake_ok, n_domains=18)
    r, _, _ = part_a(real, bad)
    cases.append(('反例 n_domains=18 ⇒ 报 A5', any(x.startswith('A5') for x in r), r))
    bad = dict(fake_ok, family_counts=(1, 17, 2, 3))
    r, _, _ = part_a(real, bad)
    cases.append(('反例 省级 17 ⇒ 报 A2', any(x.startswith('A2 ') for x in r), r))
    bad = dict(fake_ok, db={'total': 2623, 'portals': {}, 'portal_sum': 0, 'portal_pct': 0.0})
    r, _, _ = part_a(real, bad)
    cases.append(('反例 门户 0 条 ⇒ 报 A3', any(x.startswith('A3') for x in r), r))
    # ⚠ A9 的断言必须写到**空格**为止：A9s / A9b 都 `startswith('A9')`，
    # 用旧写法时只要 A9 旁边那两条里任意一条报红，本用例就假通过
    # ——「断言要精确到集合」（§2.5.68）。A9 的红消息是 `A9 实测不符：…`。
    bad = dict(fake_ok, libs={'httpx': True, 'BeautifulSoup': False})
    r, _, _ = part_a(real, bad)
    cases.append(('反例 库缺席 ⇒ 报 A9（且未误伤 A9s/A9b）',
                  any(x.startswith('A9 ') for x in r)
                  and not any(x.startswith(('A9s', 'A9b')) for x in r), r))
    # A9s：判据的**元判据**（扫描面须覆盖 collector/ 整棵树）。这一条的存在理由
    # 写在 MUST_SCAN 上方——首版扫描面太窄，trafilatura 的落点不在 sources/ 里，
    # 于是判「技术库缺席」而根因在扫描面。
    bad = dict(fake_ok, scan_missing=['cleaners/text_cleaner.py'])
    r, _, _ = part_a(real, bad)
    cases.append(('反例 扫描面缺 text_cleaner ⇒ 报 A9s（且不报 A9）',
                  any(x.startswith('A9s') for x in r)
                  and not any(x.startswith('A9 ') for x in r), r))
    # A9b 四向：可导入与否 × 有无兜底 × 登记与否。「源码用了」≠「装得上」，
    # 「装不上」也分两档（有兜底 = 可选依赖，无兜底 = 硬依赖缺席）。
    bad = dict(fake_ok, import_sites=dict(fake_ok['import_sites'],
                                          trafilatura=[('x.py', 1, False)]))
    r, _, _ = part_a(real, bad)
    cases.append(('反例 不可导入且 import 无兜底 ⇒ 报 A9b（登记也拦不住）',
                  any(x.startswith('A9b/trafilatura') and '无兜底' in x for x in r), r))
    bad = dict(fake_ok, known_absent={})
    r, _, _ = part_a(real, bad)
    cases.append(('反例 不可导入、有兜底但未登记 ⇒ 报 A9b',
                  any(x.startswith('A9b/trafilatura') and '未登记' in x for x in r), r))
    bad = dict(fake_ok, runtime=dict(fake_ok['runtime'], trafilatura=True))
    r, _, _ = part_a(real, bad)
    cases.append(('反例 库已装上但仍登记为缺席 ⇒ 报 A9b（登记过期）',
                  any(x.startswith('A9b/trafilatura') and '登记过期' in x for x in r), r))
    bad = dict(fake_ok, known_absent={'trafilatura': dict(
        module='trafilatura', guard=('cleaners/text_cleaner.py', 'zzz_never'), why='x')})
    r, _, _ = part_a(real, bad)
    cases.append(('反例 登记挂的兜底锚点已失效 ⇒ 报 A9b',
                  any(x.startswith('A9b/trafilatura') and '锚点已失效' in x for x in r), r))
    bad = dict(fake_ok, unimpl={'CAPTCHA': True})
    r, _, _ = part_a(real, bad)
    cases.append(('反例 CAPTCHA 有命中 ⇒ 报 A10', any(x.startswith('A10') for x in r), r))
    bad = dict(fake_ok, trs_max_retries=2)
    r, _, _ = part_a(real, bad)
    cases.append(('反例 TRS 只重试 2 次 ⇒ 报 A7', any(x.startswith('A7') for x in r), r))
    # A8 是「两秒退避」，判据为 `固定 sleep(2) 字面量 or TRS 指数退避` 两条路取或。
    # 两条路各测一次：只测其中一条，等于把「或」的另一半交出去不管。
    # （这一格是 2026-10-06 由恒真判据 `('A8', True, …)` 改造成真判据时补的——
    #   恒真判据下这三条用例无论怎么写都「通过」，所以此前一条都没有。）
    bad = dict(fake_ok, sleep_literals=[], expo_backoff=False)
    r, _, _ = part_a(real, bad)
    cases.append(('反例 两条退避路都断 ⇒ 报 A8', any(x.startswith('A8') for x in r), r))
    bad = dict(fake_ok, sleep_literals=[('x', 2.0, 'f:1')], expo_backoff=False)
    r, _, _ = part_a(real, bad)
    cases.append(('正例 只有固定 sleep(2) ⇒ A8 不报', not any(x.startswith('A8') for x in r), r))
    bad = dict(fake_ok, sleep_literals=[], expo_backoff=True)
    r, _, _ = part_a(real, bad)
    cases.append(('正例 只有 TRS 指数退避 ⇒ A8 不报', not any(x.startswith('A8') for x in r), r))
    # 锚点：两版均缺 / 仅一版有
    r, _, _ = part_a({'EN': '', 'ZH': ''}, fake_ok)
    cases.append(('反例 正文为空 ⇒ 锚点全缺', len([x for x in r if '锚点' in x]) == len(CLAIMS), r))
    r, _, _ = part_a({'EN': real['EN'], 'ZH': ''}, fake_ok)
    cases.append(('反例 ZH 空 ⇒ 锚点仅 EN 版有',
                  len([x for x in r if '锚点仅EN版有' in x]) == len(CLAIMS), r))
    # B 段：登记过期
    bad = dict(fake_ok, timeout_vals=[15.0, 60.0])
    r, _ = part_b(bad)
    cases.append(('反例 实测上界真到 60 ⇒ B1 登记过期',
                  any(x.startswith('B1') for x in r), r))
    bad = dict(fake_ok, delay_main=(1.0, 5.0))
    r, _ = part_b(bad)
    cases.append(('反例 实测下界不再 <1 ⇒ B2 登记过期',
                  any(x.startswith('B2') for x in r), r))
    bad = dict(fake_ok, exec_default_concurrency=1)
    r, _ = part_b(bad)
    cases.append(('反例 并发降到 1 ⇒ B3 登记过期',
                  any(x.startswith('B3') for x in r), r))
    # B4 的 cond=False 有**两种成因**、消息不能共用，两条各测一次：
    # 前者是「登记过期」（补齐声明了），后者是「源不可达」（文件不在）——
    # 若把后者也写成"登记过期"，人就去找一条不存在的登记去撤。
    bad = dict(fake_ok, req_declared=list(DECLARED_PKGS), req_missing=[])
    r, _ = part_b(bad)
    cases.append(('反例 四个库全已声明 ⇒ B4 报"登记过期"',
                  any(x.startswith('B4') and '登记过期' in x for x in r), r))
    bad = dict(fake_ok, req_declared=None, req_missing=None)
    r, _ = part_b(bad)
    cases.append(('反例 requirements.txt 不在 ⇒ B4 报"源不可达"（不降级成无待办）',
                  any(x.startswith('B4') and '源不可达' in x for x in r)
                  and not any(x.startswith('B4') and '登记过期' in x for x in r), r))

    # ── na_first_elem：D2 的定位函数（2026-10-06 加）────────────────────
    # ⚠️ 这一组用例的由来：D2 曾**整条静默不执行**（定义行带类型注解而正则不
    # 容忍 ⇒ `m` 为 None ⇒ `if m:` 整块跳过），而 `--drift-test` 照常打印
    # 「1/1 全绿」。N_DRIFT 那条元判据只能发现「用例少了」，**发现不了
    # 「定位逻辑写坏了」**——两组用例分工不同，都要有。
    real_na = (SRC / 'sources' / 'news_aggregator.py').read_text(encoding='utf-8')
    idx = na_first_elem(real_na)
    lines_na = real_na.split('\n')
    cases.append(('正例 na_first_elem 在真实源码上定位到首个元素行（定义行带类型注解）',
                  idx is not None and lines_na[idx].strip().startswith('("建筑施工'), idx))
    if idx is not None:
        cases.append(('正例 删掉该行后定位到下一个元素',
                      na_first_elem('\n'.join(lines_na[:idx] + lines_na[idx + 1:]))
                      == idx and lines_na[idx + 1].strip().startswith('("工地坍塌'), None))
    cases.append(('反例 空列表 ⇒ 返回 None（确实没有元素可删，不是"没找到"）',
                  na_first_elem('SEARCH_QUERIES: list[tuple[str, str]] = []\n') is None, None))
    cases.append(('反例 去掉类型注解仍可定位（注解不是必要条件）',
                  na_first_elem('SEARCH_QUERIES = [\n    ("a", "b"),\n]\n') == 1, None))

    # ── section_body：C 段的截取范围（2026-10-07 加）─────────────────────
    # ⚠️ 这一组用例的由来：旧版写 `t[t.index(SECTION):]`（截到文件末尾），另配
    # 一条元判据要求「SECTION 必须是末节」。2026-10-07 往对账记录追加第三十八节
    # 时，那条元判据当场转红——病根是**把偶然的文档位置当成了判据的必要条件**：
    # 判据该管的是"本节写了什么"，与它排在第几个无关（§2.5.75「写死的固定值
    # 本身可能就是响应式的」）。改用二级标题切段后，这类脆性不复存在。
    _t = ('## 甲节\n甲的字面串 24 个采集器\n### 甲.1 子标题\n子标题下的字\n'
          '## 乙节\n乙的字面串 24 个采集器\n')
    cases.append(('正例 section_body 取到本节，且三级子标题不截断',
                  '甲的字面串' in section_body(_t, '## 甲节')
                  and '子标题下的字' in section_body(_t, '## 甲节'), None))
    cases.append(('正例 后一节的内容不进 body（旧版截到末尾，正是被它污染的）',
                  '乙的字面串' not in section_body(_t, '## 甲节'), None))
    cases.append(('正例 本节是末节 ⇒ 取到文档末尾',
                  section_body(_t, '## 乙节').endswith('乙的字面串 24 个采集器\n'), None))
    # 对照组：旧写法在**同一段文本**上确实会把乙节扫进来 —— 证明上面那条
    # 「不含乙」不是恒真。少了这一条，用例可能因锚点写错而空转（§2.5.89
    # 「注入没做与判据没报在输出上一模一样」）。
    cases.append(('对照 旧写法 t[t.index(head):] 在同一文本上确实含乙节',
                  '乙的字面串' in _t[_t.index('## 甲节'):], None))

    okn = sum(1 for _, ok, _ in cases if ok)
    for name, ok, got in cases:
        print(f'  {"✓" if ok else "✗"} {name}')
        if not ok:
            print(f'      got={got}')
    print(f'--self-test {okn}/{len(cases)}')
    return 0 if okn == len(cases) else 1


# ═══════════════════════════════════════════════════════════════════════════
# E 段：--drift-test（注入自证：改真实文件，断言红的恰好是哪几条）
# ═══════════════════════════════════════════════════════════════════════════
def _run():
    p = subprocess.run([sys.executable, __file__], capture_output=True, text=True)
    return p.returncode, p.stdout


# 漂移用例的**期望条数**。任何一条静默不执行都必须报错——否则
# 「注入没做」与「判据没报」在输出上一模一样（§2.5.89 立的规矩），
# 而 `okn == len(cases)` 在一个用例**从 cases 里消失**时恒真（§2.5.64 的同族）。
N_DRIFT = 2

# SEARCH_QUERIES 的定义行**带类型注解**（`SEARCH_QUERIES: list[tuple[str, str]] = [`）。
# 抽成纯函数是为了能被 `--self-test` 直接喂文本验——`N_DRIFT` 那条元判据只能
# 发现「用例少了」，发现不了「定位逻辑写坏了」。
NA_HEAD = re.compile(r'^SEARCH_QUERIES\b[^\n=]*=\s*\[', re.M)


def na_first_elem(text):
    """返回 `SEARCH_QUERIES` 首个元素行的下标；定位不到返回 None。

    ⚠️ 首版这段是**就地写死**的，且正则写作 `^SEARCH_QUERIES\\s*=\\s*\\[`
    ⇒ 真实定义行中间夹着 `: list[tuple[str, str]]` ⇒ **不命中** ⇒ `m` 为 None
    ⇒ `if m:` 整块被跳过 ⇒ **D2 从 cases 里消失**，`--drift-test` 照常打印
    「1/1 全绿」。这比「锚点未命中」更隐蔽：后者至少还有一行 ✗。
    元素行的判据同样要与真实形态对齐——真实元素是 `    ("…", "…"),`，
    **以 `(` 开头、不是以引号开头**。
    """
    m = NA_HEAD.search(text)
    if not m:
        return None
    lines = text.split('\n')
    for j in range(text[:m.start()].count('\n') + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith(']'):          # 空列表：确实没有元素可删
            return None
        if s and not s.startswith('#') and s.startswith('('):
            return j
    return None


def drift_test():
    """往真实文件注入漂移，断言红的**恰好**是哪几条，try/finally 恢复。"""
    paper_en = FILES['EN']
    src_na = SRC / 'sources' / 'news_aggregator.py'
    cases = []

    def one(name, path, old, new, want_prefix):
        orig = path.read_text(encoding='utf-8')
        if orig.count(old) < 1:
            cases.append((name, False, f'锚点未命中：{old!r}'))
            return
        try:
            path.write_text(orig.replace(old, new), encoding='utf-8')
            code, out = _run()
            reds = _parse_reds(out)
            got = sorted({r.split()[0] for r in reds if r.split()})
            ok = code != 0 and got == sorted(want_prefix)
            cases.append((name, ok, f'rc={code} reds={got}'))
        finally:
            path.write_text(orig, encoding='utf-8')

    # 注入 1：正文把 24 改成 23 ⇒ 只 A1 红（锚点仍在位）
    one('D1 正文 24→23', paper_en, 'Twenty-four collectors', 'Twenty-three collectors', ['A1'])
    # 注入 2：删掉一个查询模板 ⇒ A4 + C 段「查询模板」锚点
    #
    # ⚠️ 期望是**两条**、不是一条，且这个连带有讲究（首版写 `['A4']` 报红，
    # 是「期望集合照着判据实际覆盖的格来写」这条规矩的第 N 次应验）：
    # 删的是**源码事实**（`SEARCH_QUERIES` 少一条）⇒ ① A4 拿实测 16 对正文的
    # 17 ⇒ 红；② C 段拿实测 16 去核文档里那句「17 条查询模板」⇒ 对不上 ⇒ 红。
    # 两者是**同一既成事实的两个独立视角**（§2.5.86：正确的连带不是遮蔽）。
    # 对照组 D1 不连带：它改的是**论文声称**、`n_sources` 实测仍是 24，
    # 故 C 段那句「24 个采集器」照样命中。
    orig = src_na.read_text(encoding='utf-8')
    del_at = na_first_elem(orig)
    if del_at is None:
        # **不静默跳过**：定位失败也必须进 cases 并判 ✗（首版正是栽在这里）
        cases.append(('D2 删一个查询模板', False,
                      f'锚点未命中：定义行可定位={bool(NA_HEAD.search(orig))}、首个元素行={del_at}'))
    else:
        lines = orig.split('\n')
        try:
            del lines[del_at]
            src_na.write_text('\n'.join(lines), encoding='utf-8')
            code, out = _run()
            got = sorted({r.split()[0] for r in _parse_reds(out) if r.split()})
            cases.append(('D2 删一个查询模板',
                          code != 0 and got == ['A4', '第三十七节缺锚点：查询模板'],
                          f'rc={code} reds={got}'))
        finally:
            src_na.write_text(orig, encoding='utf-8')

    for name, ok, got in cases:
        print(f'  {"✓" if ok else "✗"} {name}   {got}')
    okn = sum(1 for _, ok, _ in cases if ok)
    # 元判据：用例**条数**须与期望一致。整条用例消失时 `okn == len(cases)` 恒真，
    # 故这一条是「漂移自证自己也要自证」（§2.5.89）在**条数**面上的落地。
    if len(cases) != N_DRIFT:
        print(f'  ✗ 漂移用例条数 {len(cases)} ≠ 期望 {N_DRIFT}（有用例没执行）')
    print(f'--drift-test {okn}/{len(cases)}')
    return 0 if (okn == len(cases) and len(cases) == N_DRIFT) else 1


def _parse_reds(out):
    """从脚本 stdout 里取红集合（机器可读出口 REDS）。**

    接收输出串而不是自己再跑一遍——`_run()` 每次是一条完整子进程，
    调两次就是每个注入用例跑两遍脚本（首版如此，白等一倍时间）。
    """
    reds = []
    grab = False
    for line in out.split('\n'):
        if line.startswith('REDS'):
            grab = True
            continue
        if grab:
            if line.strip() == '' or line.startswith(('===', 'A.', 'B.', 'C.')):
                break
            reds.append(line.strip())
    return reds


# ═══════════════════════════════════════════════════════════════════════════
def main():
    if '--self-test' in sys.argv:
        return self_test()
    if '--drift-test' in sys.argv:
        return drift_test()

    paper = read_paper()
    try:
        m = measure()
    except Exception as e:  # noqa: BLE001
        print(f'❌ 实测失败（不降级成空集合）：{type(e).__name__}: {e}')
        return 1

    print('A. 正文锚点（中英各须出现 ≥1 次）')
    print('-' * 78)
    reds_a, lines, facts = part_a(paper, m)
    for cid, desc, ne, nz in lines:
        flag = '✅' if (ne and nz) else '❌'
        print(f'  {flag} {cid:<5}{desc:<34}EN={ne} ZH={nz}')

    print('\nA. 代码实测 ↔ 正文定值')
    print('-' * 78)
    for cid, ok, got, want in facts:
        print(f'  {"✅" if ok else "❌"} {cid:<5}{got:<52}{want}')
    if m['inst_fail']:
        print(f'  ⚠ 实例化失败 {len(m["inst_fail"])} 个：{m["inst_fail"]}')
    if m['academic_timeout']:
        print(f'  · 已剔除的未启用源 academic_import：timeout {m["academic_timeout"]}'
              f'（在 registry 里 = {m["academic_in_registry"]}）')
    print(f'  · 技术库命中：{m["libs"]}')
    print(f'  · 未实现四项命中（应为全 False）：{m["unimpl"]}')
    if m['db'] and not m['db'].get('error'):
        print(f'  · 主库：总数 {m["db"]["total"]}，门户 {m["db"]["portals"]}')

    print('\nB. 登记项（不判红；配可证伪判据，过期即报红）')
    print('-' * 78)
    reds_b, blines = part_b(m)
    for cid, title, pv, got, why, held in blines:
        print(f'  [{cid}] {title}：正文「{pv}」')
        print(f'        实测：{got}')
        print(f'        登记状态：{"仍成立（不判红）" if held else "🔴 已过期"}')
        for i, seg in enumerate(re.split(r'(?<=。)|(?<=；)', why)):
            if seg.strip():
                print(f'        {"   成因：" if i == 0 else "         "}{seg.strip()}')

    print('\nC. 文档对账')
    print('-' * 78)
    reds_c = part_c(m)
    for r in reds_c:
        print(f'  ❌ {r}')
    if not reds_c:
        print(f'  ✅ 修订对账记录已登记本脚本与 {len(anchors(m))} 条带语境锚点')

    reds = reds_a + reds_b + reds_c
    print('\n' + '=' * 78)
    print('REDS')
    for r in reds:
        print(f'  {r}')
    print('=' * 78)
    if reds:
        print(f'❌ {len(reds)} 项待处置')
        return 1
    print(f'✅ A 段全绿（{len(CLAIMS)} 条锚点中英对称 + {len(facts)} 项定值相符）；'
          f'B 段 {len(REGISTERED)} 条登记全部仍成立（属作者裁决，不判红）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
