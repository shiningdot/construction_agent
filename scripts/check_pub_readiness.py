#!/usr/bin/env python3
"""发表前公开性风险核查：论文 §7 的承诺 ↔ 仓库 / 数据库实况。

本节命题（与前面四十余个 web 核查脚本不同的一节）
--------------------------------------------------
前面的核查脚本问的都是「**系统实现得对不对**」（路由通不通、字段发没发、
对比度够不够）。本节换对象：问**「论文自己签下的欠条，今天兑现了几条」**。

`eswa_paper_v2.md` §7 Declarations 逐条写着 "Before any public release,
the following will be completed: …"，随后列出七项——逐源许可评估、保留原始
URL 与采集时间戳、移除或遮蔽姓名与可识别人脸、按请求撤回删除、图形内容的
访问控制、无法确立再分发权的第三方图片排除、开源许可发布。

**这不是「声明不实」**：论文用的是将来时，承诺本身是准确的。问题是
**仓库里没有任何一处记录这些承诺的执行状态**——它们写在那里，不会自己过期，
也没人回头核过（§2.5.96「指向面」在论文声明上的形态）。本节把这份欠条
变成一本**会过期的账本**。

四段
----
A 声明面   论文 §7 七段声明与六句关键承诺是否仍在（改稿时被误删要能抓到）
B 实况面   逐条承诺配一条可复跑的验收判据，实测仓库 / 数据库的当下状态
C 登记面    §2.5.74 的双向核：实测不符的必须已登记；已登记的若实测已符合
            则报「登记过期」（§2.5.67：登记表不能成永久豁免）
D 文档对账  本节写进对账记录的数字 ↔ 实测

判据设计（承前）
----------------
* §2.5.63 判据的宽度决定结论的强度 —— 身份证号不停在「形态像」而停在
  「**结构成立**」（地区码 + 合法日期 + ISO 7064:1983 MOD 11-2 校验位，
  随机数字串通过率仅 1/11）；
* §2.5.66 判据必须收窄接收者 —— 「字段字典」不核文件名里有没有「字典」
  二字，核**内容是否真的覆盖 accidents 全部列名**；
* §2.5.67 白名单只写理由不验证＝永久豁免 —— 登记表双向核，过期也报红；
* §2.5.68 注入式自证要断言红的恰好是哪几条，两个注入面都要测；
* §2.5.74 登记表与实测面须双向相等；
* §2.5.90 改完必须立刻复跑；
* §2.5.94 适用范围要写明 —— 输出里显式打印读了哪一份库。

⚠️ 本脚本的**基线是绿的**（全部已知欠条都在 `STATE` 里登记为 `open`），
但**脚本绿 ≠ 可以发表**：绿只说明「欠条账本没丢项」。真正的欠条逐条打印在
输出里，那是作者要裁决的清单。判据有能力报红由 `--drift-test` 三例证明
（§2.5.64：退出码 0 只有在脚本有能力报 1 时才可信）。

用法
----
    .venv/bin/python3 scripts/check_pub_readiness.py            # 主检
    .venv/bin/python3 scripts/check_pub_readiness.py --self-test
    .venv/bin/python3 scripts/check_pub_readiness.py --drift-test
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import main_db_path, main_repo_root  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAPER = Path(os.environ.get('C111_PAPER') or ROOT / 'docs' / 'paper_v2' / 'eswa_paper_v2.md')
DB_PATH = Path(os.environ.get('C111_DB') or main_db_path())
RECORD = ROOT / 'docs' / 'paper_v2' / '修订对账记录.md'
REGISTRY = ROOT / 'construction_agent' / 'collector' / 'registry.py'

TEXT_COLS = ('title', 'summary', 'direct_cause', 'raw_text')


# ══════════════════════════════════════════════════════════════════
# 身份证结构校验（三段，第三段决定性）
# ══════════════════════════════════════════════════════════════════
PROV = {'11', '12', '13', '14', '15', '21', '22', '23', '31', '32', '33',
        '34', '35', '36', '37', '41', '42', '43', '44', '45', '46',
        '50', '51', '52', '53', '54', '61', '62', '63', '64', '65'}
W17 = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CK = '10X98765432'


def id18_ok(s: str) -> bool:
    """18 位：地区码 + 合法出生日期 + MOD 11-2 校验位，三者同时成立。"""
    s = s.upper()
    if not re.fullmatch(r'\d{17}[\dX]', s) or s[:2] not in PROV:
        return False
    try:
        d = datetime.strptime(s[6:14], '%Y%m%d').date()
    except ValueError:
        return False
    if not (date(1900, 1, 1) <= d <= date(2026, 12, 31)):
        return False
    return CK[sum(int(c) * w for c, w in zip(s[:17], W17)) % 11] == s[17]


def id15_ok(s: str) -> bool:
    """15 位：无校验位，判地区码 + 合法日期两段。"""
    if not re.fullmatch(r'\d{15}', s) or s[:2] not in PROV:
        return False
    try:
        datetime.strptime('19' + s[6:12], '%Y%m%d')
    except ValueError:
        return False
    return True


def find_ids(rows) -> list[tuple[str, str]]:
    """在 (id, 文本) 序列里找结构成立的身份证号。"""
    r18 = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
    r15 = re.compile(r'(?<!\d)\d{15}(?!\d)')
    out = []
    for rid, text in rows:
        t = text or ''
        for m in r18.finditer(t):
            if id18_ok(m.group(0)):
                out.append((rid, m.group(0)))
        for m in r15.finditer(t):
            if id15_ok(m.group(0)):
                out.append((rid, m.group(0)))
    return out


# ══════════════════════════════════════════════════════════════════
# 库读取（全程只读）
# ══════════════════════════════════════════════════════════════════
def q(sql: str, args=(), db: Path | None = None):
    con = sqlite3.connect(f'file:{db or DB_PATH}?mode=ro', uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def columns(table: str, db: Path | None = None) -> list[str]:
    return [r[1] for r in q(f'PRAGMA table_info({table})', db=db)]


def all_text_rows(db: Path | None = None):
    """四个自由文本列合并成 (id, 文本) 序列，按行去重。"""
    cols = [c for c in TEXT_COLS if c in columns('accidents', db)]
    rows = q(f'SELECT id, {", ".join(cols)} FROM accidents', db=db)
    out = []
    for r in rows:
        joined = '\n'.join(str(v) for v in r[1:] if v)
        if joined:
            out.append((r[0], joined))
    return out


def registry_sources() -> list[tuple[str, str]]:
    """AST 取 registry 的 (key, display_name)——不 import（避免依赖副作用）。"""
    out = []
    for node in ast.walk(ast.parse(REGISTRY.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', '') == '_s':
            a = [x.value for x in node.args if isinstance(x, ast.Constant)]
            if len(a) >= 2:
                out.append((a[0], a[1]))
    return out


def tracked_files() -> list[str]:
    """版本库跟踪的文件清单（git 子进程，不经 harness 的 Bash 工具）。"""
    p = subprocess.run(['git', 'ls-files'], cwd=ROOT,
                       capture_output=True, text=True)
    return [l for l in p.stdout.split('\n') if l]


def is_ignored(path: str, cwd: Path | None = None) -> bool:
    p = subprocess.run(['git', 'check-ignore', '-q', path], cwd=cwd or ROOT)
    return p.returncode == 0


def ignored_shape(rel: str, cwd: Path | None = None) -> str:
    """该**合成路径**命中哪条忽略规则（空串＝未忽略）。

    与 `is_ignored` 的区别：本函数**不要求路径真实存在**。G12 探针读的是磁盘上
    有哪些库文件，而 worktree 的 `data/` 里根本没有 `db_backups/` ⇒ 改动 .gitignore
    前后 G12 的数字**一模一样**（5 个文件 / 未忽略 0），看不出规则有没有生效。
    规则本身只能用合成路径核（§2.5.89：注入没做与判据没报长得一样）。
    """
    p = subprocess.run(['git', 'check-ignore', '-v', rel], cwd=cwd or ROOT,
                       capture_output=True, text=True)
    if p.returncode != 0:
        return ''
    parts = p.stdout.strip().split(':')
    return parts[2].strip() if len(parts) >= 3 else '?'


# 本次修复**之前**的规则集（逐字录自修复前的 .gitignore 第 22–33 行）。
# 用途只有一个：证明新增规则不是"写了但没有一格因它而变"。
OLD_PATTERNS = ('data/*.json', 'data/*.csv', '!data/.gitkeep', 'data/*.db',
                'data/*.db-wal', 'data/*.db-shm', 'data/*.db.bak*',
                '*.sqlite', '*.sqlite3')

# 交付前必须被忽略的形态（合成路径，与磁盘无关）。
BACKUP_SHAPES = ('data/db_backups/x.db.bak.20260803', 'data/db_backups/x.db',
                 'data/db_backups/x.db-wal', 'data/db_backups/sub/deep.db.bak.1',
                 'data/nested/deep/x.db.bak.1')


def head_data_block(text: str) -> tuple[str, ...]:
    """从 .gitignore 全文里取「Data / 数据库」那一段的非注释行。

    收窄到这一段而不是全文：全文还含 `__pycache__/`、`*.py[cod]` 等几十行，
    拿全文去比 OLD_PATTERNS 恒不相等（§2.5.63：判据的宽度决定结论的强度）。
    """
    out, inside = [], False
    for l in text.split('\n'):
        s = l.strip()
        if s.startswith('# Data'):
            inside = True
            continue
        if inside and s.startswith('# TeX'):
            break
        if inside and s and not s.startswith('#'):
            out.append(s)
    return tuple(out)


def repo_text_files() -> list[Path]:
    """可能承载「字段字典」「来源清单」的文本文件。"""
    out = []
    for pat in ('docs/**/*.md', 'docs/**/*.json', '*.md', 'data/*.md', '*.json'):
        out.extend(ROOT.glob(pat))
    return [p for p in out if p.is_file() and '.git' not in p.parts]


# 内部过程文档：本项目的核查记录、稿件、设计蓝图——**不是**面向审稿人的交付物。
# 排除它们有原则依据：论文承诺交付的是「字段字典 / 来源清单」这两件东西本身，
# 而 docs/web/ 下的核查记录与论文自身都只是**提到**了这些列名/来源名。
# 不排除则判据会在一份核查文档上误判「已交付」（本节首跑实测报了 G9/G10 两条假绿）。
INTERNAL_DIRS = ('docs/web', 'docs/paper', 'docs/paper_v2', 'docs/specs',
                 'docs/superpowers')


def deliverable_files() -> list[Path]:
    """候选**交付物**文件（排除内部过程文档）。"""
    return [p for p in repo_text_files()
            if not any(str(p.relative_to(ROOT)).startswith(d + '/')
                       for d in INTERNAL_DIRS)]


def covers_all(path: Path, tokens: list[str]) -> bool:
    """文件内容是否覆盖全部 token（按词边界，避免子串误判）。"""
    try:
        txt = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False
    return all(re.search(r'(?<![\w-])' + re.escape(t) + r'(?![\w-])', txt)
               for t in tokens)


# ══════════════════════════════════════════════════════════════════
# 承诺清单（id → 承诺原文 / 验收判据）
# ══════════════════════════════════════════════════════════════════
PROBES: dict[str, dict] = {}


def probe(cid: str, promise: str, evidence: str):
    def deco(fn):
        PROBES[cid] = dict(id=cid, promise=promise, evidence=evidence, fn=fn)
        return fn
    return deco


@probe('G1', 'per-source permission or fair-use assessment',
       '被评估的来源须落成文件（逐源一行结论）')
def p_g1():
    keys = [k for k, _ in registry_sources()]
    for p in repo_text_files():
        if 'permission' in p.name.lower() or '许可' in p.name or 'fair' in p.name.lower():
            return True, f'{p.relative_to(ROOT)}'
    # 弱一档：是否存在任何文件同时给出多个来源的「许可/授权」结论
    hit = [p for p in repo_text_files() if re.search(r'(fair[- ]use|许可|授权|permission)',
                                                     p.read_text(encoding='utf-8', errors='replace'))]
    del keys
    return False, f'无逐源许可评估文件（含该措辞的文件 {len(hit)} 个，均非评估表）'


@probe('G2', 'retention of original URLs and crawl timestamps for every record',
       'accidents.source_url 须为原始 URL 且 crawl_date 须为采集时刻')
def p_g2():
    rows = q('SELECT source_url, crawl_date FROM accidents')
    bad_url = sum(1 for u, _ in rows if not str(u or '').startswith('http'))
    # 采集时刻 = 带时分秒；纯日期（10 位）判为生成日期而非采集时刻
    bad_ts = sum(1 for _, c in rows if not re.match(r'^\d{4}-\d{2}-\d{2}[ T]\d{2}:', str(c or '')))
    if bad_url or bad_ts:
        return False, f'{bad_url}/{len(rows)} 条 source_url 非 http(s)；{bad_ts}/{len(rows)} 条 crawl_date 无时刻'
    return True, f'{len(rows)} 条均为 http(s) URL 且带时刻'


@probe('G3', 'removal or masking of names and identifiable faces appearing in images',
       '须有可执行的人脸/人名遮蔽能力，或落盘的人脸检测记录')
def p_g3():
    n = q("SELECT COUNT(*) FROM image_analyses WHERE phase1_people IS NOT NULL "
          "AND TRIM(phase1_people) != ''")[0][0]
    tot = q('SELECT COUNT(*) FROM image_analyses')[0][0]
    # 有「人物记录」是可作遮蔽依据的前提，但记录 ≠ 已遮蔽
    return False, f'phase1_people 有记录 {n}/{tot}（可作依据），但仓库无遮蔽实现'


@probe('G4', 'a takedown and deletion mechanism on request', '撤回入口与删除记录')
def p_g4():
    tabs = [r[0] for r in q("SELECT name FROM sqlite_master WHERE type='table'")]
    hit = [t for t in tabs if re.search(r'(takedown|remove|deletion|withdraw)', t, re.I)]
    scripts = [p.name for p in (ROOT / 'scripts').glob('*') if re.search(r'(takedown|retract|撤回)', p.name, re.I)]
    if hit or scripts:
        return True, f'表 {hit} / 脚本 {scripts}'
    return False, f'{len(tabs)} 张表、{len(list((ROOT / "scripts").glob("*.py")))} 个脚本中均无撤回机制'


@probe('G5', 'access controls for graphic or traumatic imagery', '图形内容须有访问控制或分级标记')
def p_g5():
    cols = columns('image_files')
    hit = [c for c in cols if re.search(r'(sensitiv|graphic|trauma|restrict|access|rating)', c, re.I)]
    if hit:
        return True, f'image_files 分级列 {hit}'
    return False, f'image_files 无分级/访问控制列（现有列 {len(cols)} 个）'


@probe('G6', 'no human subjects or identifiable personal data in the structured records',
       '四个自由文本列不得含结构成立的身份证号')
def p_g6():
    ids = find_ids(all_text_rows())
    uniq = {s for _, s in ids}
    if ids:
        return False, f'结构成立的身份证号 {len(uniq)} 个（出现 {len(ids)} 处）'
    return True, '四个文本列均无结构成立的身份证号'


@probe('G7', 'Third-party images … will be excluded from any public release',
       '版本库不得跟踪来源不明/无再分发权的第三方图片')
def p_g7():
    tracked = [f for f in tracked_files() if f.startswith('data/images/')]
    if tracked:
        return False, f'版本库跟踪 data/images/ 下 {len(tracked)} 个文件（该目录为初版未筛选池）'
    return True, 'data/images/ 未被版本库跟踪'


@probe('G8', 'released under an open-source license together with the data',
       '仓库根须有 LICENSE / COPYING')
def p_g8():
    for p in ROOT.glob('*'):
        if p.is_file() and re.match(r'(licen[cs]e|copying)', p.name, re.I):
            return True, p.name
    return False, '仓库根无 LICENSE / COPYING'


@probe('G9', 'the field dictionary … will be provided', '须存在覆盖 accidents 全部列名的字段字典')
def p_g9():
    cols = columns('accidents')
    for p in deliverable_files():
        if covers_all(p, cols):
            return True, f'{p.relative_to(ROOT)} 覆盖全部 {len(cols)} 列'
    return False, f'无交付物文件覆盖 accidents 全部 {len(cols)} 列名'


@probe('G10', 'the source inventory … will be provided', '须存在覆盖库内全部 source_name 的来源清单')
def p_g10():
    names = [r[0] for r in q('SELECT DISTINCT source_name FROM accidents '
                             'WHERE source_name IS NOT NULL')]
    for p in deliverable_files():
        if covers_all(p, names):
            return True, f'{p.relative_to(ROOT)} 覆盖全部 {len(names)} 个来源'
    return False, f'无交付物文件覆盖库内全部 {len(names)} 个 source_name'


@probe('G12', '（版本库卫生）备份库与临时产物须被 .gitignore 覆盖，不得随仓库公开',
       '每个 checkout 的 data/ 下全部 *.db.bak* 逐文件核')
def p_g12():
    # ⚠ 两份 checkout 各核一遍：worktree 的 data/ 与主仓库的 data/ **不是同一个目录**
    # （§2.5.94 的当场重演——首版只核了 ROOT/data，而那里根本没有 db_backups/，
    #  于是 128 MB 备份会被报成「全部被忽略」）。两端必须同源。
    roots, seen, parts, leaks = [], set(), [], []
    for tag, r in (('worktree', ROOT), ('main', main_repo_root())):
        d = (r / 'data')
        if str(d.resolve()) in seen:
            continue
        seen.add(str(d.resolve()))
        roots.append((tag, d))
    for tag, d in roots:
        if not d.is_dir():
            parts.append(f'{tag}: 源不可达')
            continue
        cand = [str(p.relative_to(d.parent)) for p in d.rglob('*')
                if p.is_file() and (p.name.endswith(('.db', '.db-wal', '.db-shm'))
                                    or '.db.bak' in p.name or p.name.endswith('.bak'))]
        bad = [c for c in sorted(cand) if not is_ignored(c, cwd=d.parent)]
        leaks += [f'{tag}:{c}' for c in bad]
        parts.append(f'{tag} {len(cand)} 个库文件 / 未忽略 {len(bad)}')
    detail = '；'.join(parts)
    if leaks:
        return False, f'{detail} ⇒ 泄漏 {leaks[:4]}'
    if any('源不可达' in p for p in parts):
        return False, detail
    return True, detail


@probe('G11', 'An anonymous read-only snapshot of the database … will be provided',
       '须存在可复跑的匿名只读快照导出能力')
def p_g11():
    cand = [p.name for p in (ROOT / 'scripts').glob('*.py')
            if re.search(r'(snapshot|anonym|release|export)', p.name, re.I)]
    if cand:
        return True, f'脚本 {cand}'
    return False, 'scripts/ 下无 snapshot / anonym / export 命名的导出脚本'


# 状态登记（§2.5.74：双向核——实测不符须已登记；登记 done 而实测不符报红）
STATE = {
    'G1': 'open', 'G2': 'open', 'G3': 'open', 'G4': 'open', 'G5': 'open',
    'G6': 'open', 'G7': 'open', 'G8': 'open', 'G9': 'open', 'G10': 'open',
    'G11': 'open', 'G12': 'open',
}


# ══════════════════════════════════════════════════════════════════
# A 段：声明面
# ══════════════════════════════════════════════════════════════════
A_BLOCKS = ['**Data availability.**', '**Code availability.**',
            '**Ethics and data governance.**', '**CRediT author statement.**',
            '**Declaration of competing interest.**', '**Funding.**',
            '**Use of AI tools.**']
A_PHRASES = ['Before any public release, the following will be completed',
             'per-source permission or fair-use assessment',
             'retention of original URLs and crawl timestamps for every record',
             'removal or masking of names and identifiable faces',
             'a takedown and deletion mechanism on request',
             'access controls for graphic or traumatic imagery',
             'excluded from any public release',
             'released under an open-source license',
             'it involves no human subjects or identifiable personal data',
             'the field dictionary', 'the source inventory']


def part_a() -> list[tuple[str, str]]:
    txt = PAPER.read_text(encoding='utf-8')
    fails = []
    for b in A_BLOCKS:
        if b not in txt:
            fails.append(('A-decl', f'§7 声明段缺失：{b}'))
    for s in A_PHRASES:
        if s not in txt:
            fails.append(('A-promise', f'承诺句缺失：{s[:48]}'))
    return fails


# ══════════════════════════════════════════════════════════════════
# B / C 段：实况面 + 登记面
# ══════════════════════════════════════════════════════════════════
def part_bc(verbose: bool = True):
    reds, rows = [], []
    for cid in sorted(PROBES, key=lambda x: int(x[1:])):
        spec = PROBES[cid]
        try:
            ok, detail = spec['fn']()
        except Exception as e:                      # §2.5.73 取不到判红不降级
            ok, detail = False, f'探针异常：{type(e).__name__}: {e}'
        st = STATE.get(cid)
        if st is None:
            verdict, red = '未登记', True
        elif st == 'done' and not ok:
            verdict, red = '声称已做但实测不符', True
        elif st == 'open' and ok:
            verdict, red = '登记过期（已符合，该销账）', True
        elif st == 'open':
            verdict, red = '欠条（已登记，未兑现）', False
        else:
            verdict, red = '已兑现', False
        if red:
            reds.append(cid)
        rows.append((cid, spec['promise'], ok, detail, verdict, red))
        if verbose:
            mark = '🔴' if red else ('✅' if ok else '·')
            print(f'  {mark} {cid:4s} {verdict:22s} {detail}')
    return reds, rows


# ══════════════════════════════════════════════════════════════════
# D 段：文档对账
# ══════════════════════════════════════════════════════════════════
D_ANCHORS = [
    '§7 的七段声明', '12 条验收判据',
]


def part_d(verbose: bool = True) -> list[str]:
    fails = []
    if not RECORD.exists():
        return [f'对账记录不存在：{RECORD}']
    txt = RECORD.read_text(encoding='utf-8')
    if '第三十三节' not in txt:
        fails.append('D1 对账记录缺第三十三节')
    for a in D_ANCHORS:
        if a not in txt:
            fails.append(f'D2 锚点缺失：{a}')
    if verbose:
        for f in fails:
            print(f'  🔴 {f}')
        if not fails:
            print('  ✅ D 段文档锚点齐备')
    return fails


# ══════════════════════════════════════════════════════════════════
# 自测与注入式自证
# ══════════════════════════════════════════════════════════════════
def self_test() -> int:
    cases = []
    # 身份证校验位正反例（真值来自公开算法，校验位可手算复核）
    cases += [
        ('id18 校验位正确', id18_ok('11010519491231002X') is True),
        ('id18 校验位错误', id18_ok('110105194912310021') is False),
        ('id18 地区码非法', id18_ok('99110519491231002X') is False),
        ('id18 日期非法', id18_ok('11010519491331002X') is False),
        ('id18 位数不足', id18_ok('1101051949123100') is False),
        ('id15 合法', id15_ok('110105491231002') is True),
        ('id15 月份非法', id15_ok('110105491331002') is False),
        ('id15 地区码非法', id15_ok('991105491231002') is False),
    ]
    # covers_all 的正反例（判据收窄：词边界，不认子串）
    tmp = Path(tempfile.mkdtemp())
    f = tmp / 'x.md'
    f.write_text('alpha beta gamma\n', encoding='utf-8')
    cases += [
        ('covers_all 全覆盖', covers_all(f, ['alpha', 'gamma']) is True),
        ('covers_all 缺一', covers_all(f, ['alpha', 'delta']) is False),
        ('covers_all 子串不算', covers_all(f, ['alph']) is False),
        ('covers_all 后缀不算', covers_all(f, ['beta2']) is False),
    ]
    # find_ids 只在结构成立时命中
    rows = [('r1', '编号 11010519491231002X 结束'),
            ('r2', '编号 110105194912310021 结束'),
            ('r3', '编号 123456789012345678 结束')]
    got = {s for _, s in find_ids(rows)}
    cases += [
        ('find_ids 只留结构成立项', got == {'11010519491231002X'}),
    ]
    # A 段判据能报红（把一份副本的承诺句删掉）
    shutil.rmtree(tmp, ignore_errors=True)

    # ── G12 规则面：合成路径核规则本身（不依赖磁盘上有没有 db_backups/）
    def old_hits(rel: str) -> bool:
        """旧规则集会不会命中——`*` 按 gitignore 语义**不跨 `/`**（fnmatch 会跨）。"""
        for pat in OLD_PATTERNS:
            rx = '^' + re.escape(pat).replace(r'\*', '[^/]*').replace(r'\?', '[^/]') + '$'
            if re.match(rx, rel):
                return True
        return False

    shapes = {rel: ignored_shape(rel) for rel in BACKUP_SHAPES}
    # 元判据（§2.5.75 立的规矩）：`OLD_PATTERNS` 是本文件里的**手抄常量**，
    # 抄错了下面那条「非空洞」就变成空话。故拿 git 里的真实旧版逐行对账——
    # 它是活的（HEAD 一动、常量与历史一分叉就转红），不是自我循环。
    head_gi = subprocess.run(['git', 'show', 'HEAD:.gitignore'], cwd=ROOT,
                             capture_output=True, text=True).stdout
    head_pat = head_data_block(head_gi)
    cases += [
        ('G12 备份目录形态全部被忽略', all(shapes.values())),
        # 非空洞：这些形态**不能**本来就被旧规则覆盖，否则新增规则等于没写
        ('G12 规则面非空洞（旧规则本不覆盖它）',
         any(old_hits(rel) for rel in BACKUP_SHAPES) is False),
        ('G12 OLD_PATTERNS 与 git HEAD 逐行一致（元判据）',
         head_pat == OLD_PATTERNS),
        # 反向：旧规则覆盖的形态今天仍须覆盖（回归，防"改坏别处"）
        ('G12 旧形态未被改坏', bool(ignored_shape('data/x.db.bak.1'))
         and bool(ignored_shape('data/x.db'))),
    ]

    bad = [n for n, ok in cases if not ok]
    print('=' * 72)
    print(f'--self-test：{len(cases) - len(bad)}/{len(cases)}')
    print('=' * 72)
    for n, ok in cases:
        print(f'  {"✅" if ok else "🔴"} {n}')
    return 1 if bad else 0


def _scrub_ids(db_path: Path) -> int:
    """把副本里四个文本列的 18/15 位数字串全部掩码，返回改动处数。

    用于 C5：让 G6 探针从「不符」翻到「符合」，证明它读的是真数据、能翻转
    （而不是"恰好 0 条所以永远绿"）。
    """
    pat18 = re.compile(r'(?<!\d)\d{17}[\dXx](?!\d)')
    pat15 = re.compile(r'(?<!\d)\d{15}(?!\d)')
    con = sqlite3.connect(str(db_path))
    n = 0
    try:
        for col in TEXT_COLS:
            rows = con.execute(
                f"SELECT id, {col} FROM accidents WHERE {col} IS NOT NULL").fetchall()
            for aid, v in rows:
                new = pat18.sub(lambda m: 'X' * len(m.group(0)), v)
                new = pat15.sub(lambda m: 'X' * len(m.group(0)), new)
                if new != v:
                    n += 1
                    con.execute(f'UPDATE accidents SET {col} = ? WHERE id = ?',
                                (new, aid))
        con.commit()
    finally:
        con.close()
    return n


def _copy_db(dst: Path) -> None:
    """WAL 库不能用 shutil.copy2，用 SQLite backup() API（§2.5.86）。"""
    src = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def drift_test() -> int:
    """六例注入式自证，**按判决表的分支逐个覆盖**，断言红的**恰好**是哪几条（§2.5.68）。

    设计要点（首版在此翻过车）：注入必须**跨过判决边界**才算用例。
    首版 C2「往库副本灌一个身份证号 → G6 红」在结构上不可能成立——G6 基线本就不符，
    再灌一个号码不改变任何判决，而 `open + 不符` 的判决是「欠条」，不判红。
    这是 §2.5.64「用例条数 ≠ 判据覆盖的格数」的又一形态：**用例跑了，但没跑在判据上**。

    现按判决表五分支覆盖：
      P1 未登记(红)      → C2
      P2 声称已做但实测不符(红) → C3   ← 论文诚信上最要紧的一支，此前无任何失败路径
      P3 登记过期(红)    → C4(G2) + C5(G6)，两个不同数据面
      P4 欠条(不判红)    → C0 基线
      P5 已兑现(不判红)  → C6
    """
    tmp = Path(tempfile.mkdtemp(prefix='c111_'))
    cases = []

    # ── C0 基线（判决 P4：欠条不判红）
    r0, _ = part_bc(verbose=False)
    cases.append(('C0 基线无红', r0 == []))

    # ── C1 论文面：删掉论文副本里的一句承诺 → A 段恰好报该句
    pc = tmp / 'paper.md'
    pc.write_text(PAPER.read_text(encoding='utf-8'), encoding='utf-8')
    s = pc.read_text(encoding='utf-8')
    victim = 'a takedown and deletion mechanism on request'
    pc.write_text(s.replace(victim, 'XXXX', 1), encoding='utf-8')
    env = dict(os.environ, C111_PAPER=str(pc))
    out = subprocess.run([sys.executable, __file__, '--marks'], cwd=ROOT,
                         capture_output=True, text=True, env=env)
    marks = _marks(out.stdout)
    cases.append(('C1 删承诺句 → A 段恰好红 1 条',
                  marks.get('a') == 1 and marks.get('bc') == []))

    # ── C2 判决 P1「未登记」：注册一个 STATE 里没有的探针 → 恰好它红
    PROBES['G13'] = dict(id='G13', promise='（探针）', evidence='（探针）',
                         fn=lambda: (True, 'ok'))
    try:
        r, _ = part_bc(verbose=False)
        cases.append(('C2 未登记的探针 → 恰好 G13 红（未登记）', r == ['G13']))
    finally:
        PROBES.pop('G13', None)

    # ── C3 判决 P2「声称已做但实测不符」：把实测不符的 G8 登记成 done → 恰好它红
    old = STATE['G8']
    STATE['G8'] = 'done'
    try:
        r, _ = part_bc(verbose=False)
        cases.append(('C3 登记 done 而实测不符 → 恰好 G8 红', r == ['G8']))
    finally:
        STATE['G8'] = old

    # ── C4 数据面（判决 P3，G2）：把 895 条 source_url 修成 http → 恰好 G2 红
    dc = tmp / 'c4.db'
    _copy_db(dc)
    con = sqlite3.connect(str(dc))
    con.execute("UPDATE accidents SET source_url = 'https://example.org/x' "
                "WHERE source_url NOT LIKE 'http%'")
    con.execute("UPDATE accidents SET crawl_date = crawl_date || ' 09:00:00' "
                "WHERE crawl_date NOT LIKE '%:%'")
    con.commit()
    con.close()
    cases.append(('C4 不符消失（G2）→ 恰好 G2 红（登记过期）',
                  _marks(subprocess.run(
                      [sys.executable, __file__, '--marks'], cwd=ROOT,
                      capture_output=True, text=True,
                      env=dict(os.environ, C111_DB=str(dc))).stdout).get('bc') == ['G2']))

    # ── C5 数据面（判决 P3，G6）：脱敏副本里的全部身份证号 → 恰好 G6 红
    dc5 = tmp / 'c5.db'
    _copy_db(dc5)
    n = _scrub_ids(dc5)
    cases.append((f'C5 脱敏 {n} 处身份证号（G6）→ 恰好 G6 红（登记过期）',
                  n > 0 and _marks(subprocess.run(
                      [sys.executable, __file__, '--marks'], cwd=ROOT,
                      capture_output=True, text=True,
                      env=dict(os.environ, C111_DB=str(dc5))).stdout).get('bc') == ['G6']))

    # ── C6 判决 P5「已兑现」：探针 ok 且登记 done → 不判红（且不是被别的红顶掉）
    r, _ = part_bc(verbose=False)
    ok_id = None
    old8 = STATE['G8']
    STATE['G8'] = 'done'
    oldfn = PROBES['G8']['fn']
    PROBES['G8']['fn'] = lambda: (True, '（探针）已兑现')
    try:
        r2, _ = part_bc(verbose=False)
        cases.append(('C6 登记 done 且实测符合 → 不判红（已兑现）',
                      'G8' not in r2 and len(r2) == len(r)))
    finally:
        PROBES['G8']['fn'] = oldfn
        STATE['G8'] = old8
    del ok_id

    # ── C7 规则面：把真实 .gitignore 退回 git HEAD 那版 → 自检里的 G12 恰好 1 条转红
    # 这一支是**唯一**能证明 G12 规则面判据能失败的用例：G12 探针读磁盘，而
    # worktree 的 data/ 里没有 db_backups/ ⇒ 改回旧规则后探针数字分毫不动。
    # 回退源取 `git show HEAD:.gitignore`（真实历史），不做字符串手术——按
    # "删掉哪两行"的猜法一旦行文本变了，注入就静默落空（§2.5.89）。
    gi = ROOT / '.gitignore'
    orig = gi.read_text(encoding='utf-8')
    reverted = subprocess.run(['git', 'show', 'HEAD:.gitignore'], cwd=ROOT,
                              capture_output=True, text=True).stdout
    try:
        gi.write_text(reverted, encoding='utf-8')
        out = subprocess.run([sys.executable, __file__, '--self-test'], cwd=ROOT,
                             capture_output=True, text=True).stdout
        reds = sorted(l.strip()[2:].split('（')[0].strip()
                      for l in out.split('\n') if l.strip().startswith('🔴 G12'))
        # 期望集合实测得来（首版写 2 条是**用例错**：那条「非空洞」判的是
        # OLD_PATTERNS 这个常量，不随 .gitignore 变，本就不该跟着转红）。
        cases.append(('C7 退回旧 .gitignore → G12 恰好 1 条转红',
                      reds == ['G12 备份目录形态全部被忽略']
                      and gi.read_text(encoding='utf-8') != orig))
    finally:
        gi.write_text(orig, encoding='utf-8')
    cases.append(('C7 恢复后 .gitignore 逐字还原',
                  gi.read_text(encoding='utf-8') == orig))

    shutil.rmtree(tmp, ignore_errors=True)

    bad = [n for n, ok in cases if not ok]
    print('=' * 72)
    print(f'--drift-test：{len(cases) - len(bad)}/{len(cases)}')
    print('=' * 72)
    for n, ok in cases:
        print(f'  {"✅" if ok else "🔴"} {n}')
    if bad:
        print(f'  失败：{bad}')
    return 1 if bad else 0


def _marks(stdout: str) -> dict:
    for line in stdout.split('\n'):
        if line.startswith('MARKS '):
            try:
                return json.loads(line[6:])
            except json.JSONDecodeError:
                pass
    return {}


# ══════════════════════════════════════════════════════════════════
def main(argv: list[str]) -> int:
    if '--self-test' in argv:
        return self_test()
    if '--drift-test' in argv:
        return drift_test()
    quiet = '--marks' in argv

    if not quiet:
        print('=' * 72)
        print('发表前公开性风险核查：论文 §7 承诺 ↔ 仓库/数据库实况')
        print('=' * 72)
        print(f'  论文    : {PAPER}')
        print(f'  数据库  : {DB_PATH}')          # §2.5.94：适用范围要写明
        print(f'  登记表  : {len(STATE)} 条')
        print()
        print('A 段：声明面（§7 七段 + 关键承诺句）')
        a = part_a()
        for _, m in a:
            print(f'  🔴 {m}')
        if not a:
            print(f'  ✅ {len(A_BLOCKS)} 段声明、{len(A_PHRASES)} 句承诺全部在位')
        print()
        print('B/C 段：实况面 + 登记面')
    reds, rows = part_bc(verbose=not quiet)
    a = part_a()
    if not quiet:
        print()
        print('D 段：文档对账')
    d = part_d(verbose=not quiet)

    if not quiet:
        open_gaps = [r for r in rows if r[4] == '欠条（已登记，未兑现）']
        print()
        print('=' * 72)
        print(f'未兑现的欠条 {len(open_gaps)} 条（已登记，不判红；须作者逐条裁决或补做）')
        print('=' * 72)
        for cid, promise, _, detail, _, _ in open_gaps:
            print(f'  {cid:4s} 【{promise[:46]}】')
            print(f'        {detail}')
        print()
        total_red = len(reds) + len(a) + len(d)
        print('=' * 72)
        print(f'结论：待处置 {total_red} 条（A 段 {len(a)} + B/C 段 {len(reds)}'
              f' + D 段 {len(d)}）；已登记欠条 {len(open_gaps)} 条')
        print('=' * 72)

    print('MARKS ' + json.dumps({'a': len(a), 'bc': reds, 'd': len(d)},
                                ensure_ascii=False))
    return 1 if (reds or a or d) else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
