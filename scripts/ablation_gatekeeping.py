#!/usr/bin/env python3
"""P0-1 词汇门控效应 2×2 消融实验

设计: 措辞(条件检查 vs 两步判断) × 输出政策(保守 vs 全覆盖)
  v1  = 条件检查 + 保守指令   (原始失败版本)
  v2  = 两步判断 + 全覆盖偏好 (生产版本)
  v2a = 两步判断 + 保守指令   (隔离措辞贡献)
  v2b = 条件检查 + 全覆盖偏好 (隔离输出政策贡献)

样本: 从当前 DB 分层抽取 100 条 (accident_type × severity)
分析: 发射行为分布 (编码数/条、空输出率、各编码发射率) — 无需专家GT
"""
import json, os, sys, time, sqlite3, random, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, '/home/suntianhao/construction_agent')
from dotenv import load_dotenv
load_dotenv('/home/suntianhao/construction_agent/.env', override=True)
from anthropic import Anthropic

DB = '/home/suntianhao/construction_agent/data/caiems_v2.db'
OUT_DIR = Path('/home/suntianhao/construction_agent/.claude/worktrees/classifier-mod/experiments/ablation_gatekeeping')
OUT_DIR.mkdir(parents=True, exist_ok=True)
MODEL = 'deepseek-v4-pro'
MAX_TOKENS = 16384  # 思考块会消耗输出预算，需留足空间给最终 JSON
TEMPERATURE = 0.0
WORKERS = 4
MAX_RETRY = 1

# ============================================================
# 19 类编码清单（两版本共用，措辞不同）
# ============================================================
JSON_SPEC = """输出最终JSON。字段要求:
- codes: 选出的所有CAICS编码（如["M1","F2","H1"]）
- accident_type: 必须从以下12类严格选择: 坍塌|高处坠落|触电|机械伤害|起重伤害|物体打击|土方坍塌|车辆伤害|火灾爆炸|中毒窒息|淹溺|其他
- severity: 必须从以下4级严格选择: 一般事故|较大事故|重大事故|特别重大事故
- deaths: 整数
- injuries: 整数
- direct_cause: 一句话简述
若无相关编码codes可为空数组[]。
分析完毕后，在最后一行输出最终JSON。"""

# v1/v2b 共用: 条件检查措辞（疑问式逐编码检查）
CHECKS_BODY = """CAICS框架 (5层19类):

M.管理根基 — 系统性安全管理缺陷：
   M1现场监管缺失: 文本提到"无人监管""监理失职""安全管理混乱""未及时发现制止""现场无安全员"？
   M2隐患排查失效: 文本提到"未排查""未整改""隐患长期存在""未进行风险评估""检查走过场"？
   M3安全培训不足: 文本提到"未经培训""安全交底不到位""交底走过场""缺乏安全知识""三级教育不足"？
   M4制度规程漏洞: 文本提到"无安全方案""应急预案缺失""制度不健全""无施工方案""未编制专项方案"？
   M5市场行为违规: 文本提到"违法分包""无资质""转包""抢工期""未办理报建""挂靠"？

F.防线失效 — 物理屏障/防护的缺失或失效：
   F1临边洞口防护缺失: 文本提到"无防护""无护栏""临边未设防""洞口未盖""未设置安全网""防护栏杆缺失"？
   F2支撑体系失稳: 文本提到"模板坍塌""脚手架失稳""支撑不足""支架倒塌""未按方案搭设""扣件松动"？
   F3设备安全装置失效: 文本提到"限位器被屏蔽""防坠器失效""安全装置损坏""保险装置失灵""力矩限制器缺失"？
   F4个体防护未使用: 文本提到"未系安全带""未戴安全帽""无防护用品""未使用PPE""安全带未挂点"？

T.触发条件 — 直接触发事故的物质或环境条件：
   T1设备机具故障: 文本提到"设备故障""塔吊倒塌""钢丝绳断裂""机械失灵""升降机故障""制动失效"？
   T2物料状态异常: 文本提到"堆载超限""材料缺陷""荷载过大""物料失稳""珍珠岩浸水""瓦斯积聚"？
   T3地质环境突变: 文本提到"涌水""边坡失稳""地基沉降""基坑坍塌""土体滑移""流沙"？
   T4气象水文触发: 文本提到"暴雨""大风""冰雪""雷电""六级风""连日降雨""高温""台风"？

H.人的行为 — 直接导致事故的人为行为：
   H1违反操作规程: 文本提到明确违反施工方案/操作规程/规范要求的行为？
   H2违规指挥决策: 文本提到管理人员强令冒险/明知风险仍施工/违章指挥/要求工人冒险进入？
   H3风险辨识失败: 文本提到"未意识到危险""安全意识薄弱""未检查确认""疏忽大意""以为没问题"？
   H4应急行为失当: 文本提到"盲目施救""未及时撤离""救援不当导致伤亡扩大""二次坍塌时仍在施救"？

C.后果放大 — 使事故后果超出预期的因素：
   C1救援响应迟缓: 文本提到"救援不及时""无预案""二次坍塌""延误抢救""救援力量不足"？
   C2信息迟报瞒报: 文本提到"未及时上报""瞒报""迟报""漏报""未按规定报告"？

请逐项检查文本，输出所有匹配的编码。"""

# v2/v2a 共用: 两步判断措辞（线索引导 + 授权推断）
TWOSTEP_BODY = """CAICS框架 (5层19类):

核心原则: 同时运用显性证据匹配和隐性领域推断。文本中有关键词→标记"explicit"，基于事故性质和行业规律合理推断→标记"inferred"。

M.管理根基（几乎所有严重事故都存在管理缺陷）：
   M1现场监管缺失: 线索→无人监管/监理失职/安全管理混乱。**推断→发生死亡事故本身即说明现场监管缺失，即使文本未提也应输出M1[inferred]**
   M2隐患排查失效: 线索→未排查/未整改/隐患长期存在。**推断→事故涉及结构缺陷/设备问题未被发现→M2[inferred]**
   M3安全培训不足: 线索→未经培训/交底走过场。**推断→工人明显违章但文本未提培训→M3[inferred]**
   M4制度规程漏洞: 线索→无安全方案/应急预案缺失/制度不健全
   M5市场行为违规: 线索→违法分包/无资质/转包/抢工期/未办理报建

F.防线失效（事故=防线被突破，按事故类型反推）：
   F1临边洞口防护缺失: 线索→无防护/无护栏。**推断→高处坠落事故→F1[inferred]**
   F2支撑体系失稳: 线索→模板坍塌/脚手架失稳/支撑不足。**推断→坍塌涉及混凝土/模板→F2[inferred]**
   F3设备安全装置失效: 线索→限位器被屏蔽/防坠器失效/安全装置损坏
   F4个体防护未使用: 线索→未系安全带/未戴安全帽。**推断→高处坠落/物体打击伤亡→F4[inferred]**

T.触发条件（直接触发事故的物质或环境条件）：
   T1设备机具故障: 线索→设备故障/钢丝绳断裂/机械失灵
   T2物料状态异常: 线索→堆载超限/荷载过大/珍珠岩浸水/瓦斯积聚。**推断→坍塌未见明确触发物→T2[inferred]**
   T3地质环境突变: 线索→涌水/边坡失稳/地基沉降。**推断→基坑/沟槽/隧道事故→T3[inferred]**
   T4气象水文触发: 线索→暴雨/大风/冰雪/连日降雨

H.人的行为（几乎每条事故都涉及人的行为因素）：
   H1违反操作规程: 线索→违反方案/规程/规范。**推断→工人行为违背基本安全常识→H1[inferred]**
   H2违规指挥决策: 线索→强令冒险/违章指挥。**推断→多人同时作业往往存在指挥问题→H2[inferred]**
   H3风险辨识失败: 线索→未意识到危险/安全意识薄弱。**推断→工人进入本应避开的危险区域→H3[inferred]**
   H4应急行为失当: 线索→盲目施救/未及时撤离/救援不当

C.后果放大（使后果超出预期的因素）：
   C1救援响应迟缓: 线索→救援不及时/无预案/二次坍塌。**推断→死亡≥3人且未提救援细节→C1[inferred]**
   C2信息迟报瞒报: 线索→未及时上报/瞒报/迟报/漏报

请分析文本，输出所有explicit和inferred的编码。"""

CONSERVATIVE = "注意: 不要编造文本不支持的编码。只输出有明确依据的编码，宁可漏标不可多标。"
COVERAGE = "注意: 安全分析宁可多标不漏标，漏掉一个致因比多报一个成本更高。"

PROMPTS = {
    'v1':  {'body': CHECKS_BODY,   'policy': CONSERVATIVE, 'label': '条件检查+保守'},
    'v2a': {'body': TWOSTEP_BODY,  'policy': CONSERVATIVE, 'label': '两步判断+保守'},
    'v2b': {'body': CHECKS_BODY,   'policy': COVERAGE,     'label': '条件检查+全覆盖'},
    'v2':  {'body': TWOSTEP_BODY,  'policy': COVERAGE,     'label': '两步判断+全覆盖'},
}

USER_PREFIX = "请按CAICS因果链逐层分析以下事故：\n\n"

ALL_CODES = [f'{L}{i}' for L, n in [('M',5),('F',4),('T',4),('H',4),('C',2)] for i in range(1, n+1)]


def load_sample():
    """从 DB 分层抽取 100 条（accident_type × severity 比例分配）"""
    db = sqlite3.connect(DB)
    rows = db.execute("""
        SELECT id, title, raw_text, accident_type, severity
        FROM accidents
        WHERE raw_text IS NOT NULL AND length(raw_text) > 200
          AND accident_type IS NOT NULL AND accident_type != ''
          AND severity IS NOT NULL AND severity != ''
    """).fetchall()
    db.close()
    # 分层: accident_type × severity 格子
    cells = {}
    for r in rows:
        cells.setdefault((r[3], r[4]), []).append(r)
    # 按总体比例分配 100
    n_total = len(rows)
    sample = []
    for (at, sv), recs in sorted(cells.items()):
        n_cell = max(1, round(len(recs) / n_total * 100))
        random.Random(42).shuffle(recs)
        sample.extend(recs[:n_cell])
    random.Random(42).shuffle(sample)
    sample = sample[:100]
    print(f'样本: {len(sample)} 条 (从 {n_total} 条候选分层抽取)')
    return [(r[0], r[1], r[2]) for r in sample]


def call_llm(client, system, user_text):
    for attempt in range(MAX_RETRY + 1):
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                system=system,
                messages=[{'role': 'user', 'content': user_text}],
            )
            raw = ''
            for blk in resp.content:
                if hasattr(blk, 'text'):
                    raw += blk.text
            return raw, None
        except Exception as e:
            if attempt < MAX_RETRY:
                time.sleep(3)
            else:
                return None, str(e)[:120]


def extract_json(text):
    """从混合输出(推理文字+JSON)中提取JSON — 与原始实验 runner 一致"""
    if not text:
        return None
    # 尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        pass
    # 找 ```json 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 从最后一行开始找 — 多数LLM把JSON放在最后
    lines = text.strip().split('\n')
    for line in reversed(lines):
        if line.strip().startswith('{'):
            try:
                return json.loads(line.strip())
            except Exception:
                pass
    # Smart brace: 从最后一个 { 开始匹配
    last_brace = text.rfind('{')
    if last_brace >= 0:
        depth = 0
        end = -1
        for i in range(last_brace, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end > 0:
            try:
                return json.loads(text[last_brace:end])
            except Exception:
                pass
    return None


def run_cell(cell, prompts, sample, checkpoint_path):
    """运行一个单元，断点续传"""
    done = {}
    if os.path.exists(checkpoint_path):
        done = {d['idx']: d for d in json.load(open(checkpoint_path))}
    client = Anthropic()
    spec = PROMPTS[cell]
    system = f"你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。\n\n{spec['body']}\n\n{spec['policy']}\n\n{JSON_SPEC}"

    def process(i, rec):
        if i in done:
            return None
        acc_id, title, text = rec
        out, err = call_llm(client, system, USER_PREFIX + text[:3500])
        parsed = extract_json(out) if out else None
        if isinstance(parsed, list):
            parsed = {'codes': parsed}
        codes = parsed.get('codes', []) if parsed else []
        return {'idx': i, 'accident_id': acc_id, 'title': title[:30],
                'codes': codes, 'n_codes': len(codes),
                'parse_error': not bool(parsed), 'api_error': err}

    results = list(done.values())
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(process, i, rec): i for i, rec in enumerate(sample)}
        for k, fut in enumerate(as_completed(futs)):
            r = fut.result()
            if r:
                results.append(r)
                if (k + 1) % 20 == 0:
                    json.dump(results, open(checkpoint_path, 'w'), ensure_ascii=False)
                    print(f'  [{cell}] {len(results)}/100', flush=True)
    json.dump(results, open(checkpoint_path, 'w'), ensure_ascii=False)
    return results


def analyze(results, label):
    n = len(results)
    if n == 0:
        print(f'  {label}: 无结果')
        return None
    n_codes = [r['n_codes'] for r in results]
    empty = sum(1 for r in results if r['n_codes'] == 0)
    emit = {}
    for r in results:
        for c in set(r['codes']):
            emit[c] = emit.get(c, 0) + 1
    stats = {
        'label': label, 'n': n,
        'avg_codes': sum(n_codes) / n,
        'empty_rate': empty / n,
        'parse_error_rate': sum(1 for r in results if r.get('parse_error')) / n,
        'top_emission': dict(sorted(emit.items(), key=lambda x: -x[1])[:8]),
        'm1_rate': emit.get('M1', 0) / n,
        'm3_rate': emit.get('M3', 0) / n,
        'h3_rate': emit.get('H3', 0) / n,
        't2_rate': emit.get('T2', 0) / n,
    }
    print(f'  {label}: 平均编码 {stats["avg_codes"]:.2f}/条 | 空输出 {stats["empty_rate"]*100:.0f}% | '
          f'M1发射率 {stats["m1_rate"]:.2f} | M3 {stats["m3_rate"]:.2f} | H3 {stats["h3_rate"]:.2f} | T2 {stats["t2_rate"]:.2f}')
    return stats


def main():
    sample = load_sample()
    client_test = Anthropic()
    # 冒烟测试: 每单元 1 条
    print('冒烟测试 (每单元1条)...', flush=True)
    test_sample = sample[:1]
    for cell in PROMPTS:
        spec = PROMPTS[cell]
        system = f"你是建筑施工安全事故分析专家。使用CAICS框架按M→F→T→H→C因果链逐层分析。\n\n{spec['body']}\n\n{spec['policy']}\n\n{JSON_SPEC}"
        ok = 0
        for i, (acc_id, title, text) in enumerate(test_sample):
            out, err = call_llm(client_test, system, USER_PREFIX + text[:3500])
            parsed = extract_json(out) if out else None
            if parsed:
                ok += 1
        print(f'  {cell} ({spec["label"]}): {ok}/1 解析成功', flush=True)
    # 正式运行
    print('\n正式运行 4 单元 × 100 条...')
    all_stats = {}
    for cell in PROMPTS:
        cp = OUT_DIR / f'{cell}_checkpoint.json'
        t0 = time.time()
        results = run_cell(cell, PROMPTS, sample, str(cp))
        stats = analyze(results, f'{cell} ({PROMPTS[cell]["label"]})')
        if stats:
            all_stats[cell] = stats
        print(f'  耗时 {time.time()-t0:.0f}s', flush=True)
    # 汇总
    summary = {
        'model': MODEL, 'temperature': TEMPERATURE, 'n_sample': len(sample),
        'cells': all_stats,
        'design': 'phrasing(checks vs twostep) x policy(conservative vs coverage)',
    }
    json.dump(summary, open(OUT_DIR / 'summary.json', 'w'), ensure_ascii=False, indent=1)
    # 因果分解
    if all(k in all_stats for k in ('v1', 'v2a', 'v2b', 'v2')):
        print('\n=== 因果分解（相对 v1 的发射增量） ===')
        v1 = all_stats['v1']
        for k, name in [('v2a', '措辞贡献 Δ(phrasing)'), ('v2b', '政策贡献 Δ(policy)'), ('v2', '联合贡献')]:
            s = all_stats[k]
            print(f'  {name}: 平均编码 +{s["avg_codes"]-v1["avg_codes"]:+.2f} | '
                  f'空输出率 {s["empty_rate"]-v1["empty_rate"]:+.0%} | '
                  f'M1发射率 +{s["m1_rate"]-v1["m1_rate"]:+.2f}')
    print(f'\n完成 → {OUT_DIR}')


if __name__ == '__main__':
    main()
