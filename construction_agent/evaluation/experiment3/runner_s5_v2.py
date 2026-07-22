"""S5_CAICSCoTPlus v2 — 增加 accident_type/severity 提取。

v1 问题:
  - call_llm_s5() 只聚合5层编码，没有提取 accident_type/severity/deaths/injuries
  - TypeAcc=0, SevAcc=0.01

v2 修复:
  - 增加第6次API调用（综合汇总），在5层分析完成后提取基础字段
  - call_llm_s5_v2() 返回完整字段：codes + accident_type + severity + deaths + injuries + direct_cause

输出到 S5_CAICSCoTPlus_v2.json（不覆盖原始 S5_CAICSCoTPlus.json）
"""

import json
import re
import time
import os
import sys
from pathlib import Path
from collections import Counter
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=True)

from anthropic import Anthropic

GT_PATH = Path("data/ground_truth/expertA.json")
SAMPLES_PATH = Path("data/ground_truth/gt_samples_100.json")
OUT_DIR = Path("data/experiments/exp3_6strategies")
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = Anthropic(
    api_key=os.getenv("ANTHROPIC_API_KEY"),
    base_url=os.getenv("ANTHROPIC_BASE_URL")
)
MODEL = os.getenv("ANTHROPIC_MODEL")

# ── 5层 prompt（与原版相同）──
S5_LAYERS_V2 = [
    {
        "layer": "M", "prefix": "M",
        "system": """你是安全管理专家。从事故文本中识别管理根基(M层)致因。
M1现场监管缺失: 无人监管/监理失职/安全管理混乱/未及时发现制止？
M2隐患排查失效: 未排查/未整改/隐患长期存在/未进行风险评估？
M3安全培训不足: 未经培训/安全交底不到位/缺乏安全知识？
M4制度规程漏洞: 无安全方案/应急预案缺失/制度不健全/无施工方案？
M5市场行为违规: 违法分包/无资质/转包/抢工期/未办理报建？
输出JSON: {"M":["M1","M2"],"reasoning":"M1因为...M2因为..."}""",
    },
    {
        "layer": "F", "prefix": "F",
        "system": """你是安全防护专家。已知管理层分析结果，从事故文本中识别防线失效(F层)致因。
F1临边洞口防护缺失: 无防护/无护栏/临边未设防/洞口未盖？
F2支撑体系失稳: 模板坍塌/脚手架失稳/支撑不足/支架倒塌？
F3设备安全装置失效: 限位器被屏蔽/防坠器失效/安全装置损坏？
F4个体防护未使用: 未系安全带/未戴安全帽/无防护用品？
在前层分析基础上，输出JSON: {"F":["F2"],"reasoning":"F2因为..."}""",
    },
    {
        "layer": "T", "prefix": "T",
        "system": """你是事故分析专家。已知管理层和防护层分析结果，从文本中识别触发条件(T层)致因。
T1设备机具故障: 设备故障/塔吊倒塌/钢丝绳断裂/升降机故障？
T2物料状态异常: 堆载超限/材料缺陷/荷载过大/瓦斯积聚？
T3地质环境突变: 涌水/边坡失稳/地基沉降/土体滑移？
T4气象水文触发: 暴雨/大风/冰雪/雷电/连日降雨？
在前层分析基础上，输出JSON: {"T":["T2"],"reasoning":"T2因为..."}""",
    },
    {
        "layer": "H", "prefix": "H",
        "system": """你是人员行为分析专家。已知管理/防护/触发层分析结果，从文本中识别人的行为(H层)致因。
H1违反操作规程: 明确违反施工方案/操作规程/规范要求？
H2违规指挥决策: 管理人员强令冒险/明知风险仍施工/违章指挥？
H3风险辨识失败: 未意识到危险/安全意识薄弱/未检查确认/疏忽大意？
H4应急行为失当: 盲目施救/未及时撤离/救援不当扩大伤亡？
在前层分析基础上，输出JSON: {"H":["H1","H2"],"reasoning":"H1因为...H2因为..."}""",
    },
    {
        "layer": "C", "prefix": "C",
        "system": """你是应急救援分析专家。已知全部前层分析结果，从文本中识别后果放大(C层)致因。
C1救援响应迟缓: 救援不及时/无预案/二次坍塌/延误抢救？
C2信息迟报瞒报: 未及时上报/瞒报/迟报/漏报？
在前层分析基础上，输出JSON: {"C":["C1"],"reasoning":"C1因为..."}""",
    },
]

# ── 第6次调用：综合汇总 ──
SUMMARY_SYSTEM = """你是施工安全事故报告撰写专家。已知该事故完整的CAICS致因分析结果，请从事故文本中提取基础信息。

事故类型必须从以下选择：坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/火灾爆炸/中毒窒息/淹溺/车辆伤害/其他
事故等级必须从以下选择：一般事故/较大事故/重大事故/特别重大事故

输出纯JSON（不要其他文字）：
{"accident_type":"坍塌","severity":"较大事故","deaths":3,"injuries":5,"direct_cause":"简述直接原因"}"""


# ── Helper ──
def extract_json(text: str) -> dict:
    """从混合输出中提取JSON。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    lines = text.strip().split('\n')
    for line in reversed(lines):
        if line.strip().startswith('{'):
            try:
                return json.loads(line.strip())
            except Exception:
                pass
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
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        s = m.group(0)
        if s.count('{') > s.count('}'):
            s += '}' * (s.count('{') - s.count('}'))
        try:
            return json.loads(s)
        except Exception:
            pass
    return {"_error": "no_json", "_raw": text[:200]}


def call_llm_s5_v2(text: str) -> dict:
    """CAICS-CoT+ v2: 5层逐层分析 + 1次综合汇总 = 6次API调用。"""
    context = ""
    all_codes = []
    layer_details = {}

    # ── Step 1-5: 逐层分析 ──
    for layer_cfg in S5_LAYERS_V2:
        system = layer_cfg["system"]
        if context:
            system = f"{system}\n\n前层分析结果:\n{context}"

        user = f"事故文本:\n{text[:1500]}"

        try:
            resp = client.messages.create(
                model=MODEL, max_tokens=512, temperature=0.0,
                system=system, messages=[{"role": "user", "content": user}]
            )
            raw = ""
            for blk in resp.content:
                if hasattr(blk, "text"):
                    raw += blk.text

            layer_result = extract_json(raw)
            layer_key = layer_cfg["layer"]
            layer_codes = layer_result.get(layer_key, [])
            reasoning = layer_result.get("reasoning", "")

            if layer_codes:
                all_codes.extend(layer_codes)
                context += f"\n{layer_key}层: {', '.join(layer_codes)} — {reasoning}"
                layer_details[layer_key] = {"codes": layer_codes, "reasoning": reasoning}
            else:
                context += f"\n{layer_key}层: 无编码"
                layer_details[layer_key] = {"codes": [], "reasoning": reasoning}
        except Exception as e:
            context += f"\n{layer_cfg['layer']}层调用失败: {e}"
            layer_details[layer_cfg['layer']] = {"codes": [], "reasoning": f"ERROR: {e}"}

    # ── Step 6: 综合汇总（提取基础字段）──
    summary = {}
    try:
        summary_system = SUMMARY_SYSTEM
        if context:
            summary_system = f"{SUMMARY_SYSTEM}\n\nCAICS致因分析结果:\n{context}"

        resp = client.messages.create(
            model=MODEL, max_tokens=512, temperature=0.0,
            system=summary_system,
            messages=[{"role": "user", "content": f"事故文本:\n{text[:1500]}"}]
        )
        raw = ""
        for blk in resp.content:
            if hasattr(blk, "text"):
                raw += blk.text
        summary = extract_json(raw)
    except Exception as e:
        summary = {"_error": str(e)[:80]}

    return {
        "codes": all_codes,
        "accident_type": summary.get("accident_type", ""),
        "severity": summary.get("severity", ""),
        "deaths": summary.get("deaths", 0) if isinstance(summary.get("deaths"), (int, float)) else 0,
        "injuries": summary.get("injuries", 0) if isinstance(summary.get("injuries"), (int, float)) else 0,
        "direct_cause": summary.get("direct_cause", ""),
        "_s5_context": context,
        "_layer_details": layer_details,
        "_summary_raw": summary,
    }


def compute_metrics(predictions, ground_truth):
    """计算 Per-class P/R/F1/Acc + Ave/Ave+。"""
    all_classes = sorted(set(
        c for gt in ground_truth for c in (gt.get("codes", []) or [])
        if isinstance(gt, dict) and isinstance(c, str)
    ) | set(
        c for pred in predictions for c in (pred.get("codes", []) or [])
        if isinstance(pred, dict) and isinstance(c, str)
    ))

    results = {}
    for cls in all_classes:
        tp = sum(1 for i, gt in enumerate(ground_truth)
                 if isinstance(gt, dict) and i < len(predictions)
                 and cls in gt.get("codes", []) and cls in predictions[i].get("codes", []))
        fp = sum(1 for i, pred in enumerate(predictions)
                 if isinstance(pred, dict) and cls in pred.get("codes", [])
                 and (i >= len(ground_truth)
                      or not isinstance(ground_truth[i], dict)
                      or cls not in ground_truth[i].get("codes", [])))
        fn = sum(1 for i, gt in enumerate(ground_truth)
                 if isinstance(gt, dict) and cls in gt.get("codes", [])
                 and (i >= len(predictions)
                      or cls not in predictions[i].get("codes", [])))

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0
        acc = tp / len(ground_truth) if len(ground_truth) > 0 else 0
        results[cls] = {
            "P": prec, "R": rec, "F1": f1, "Acc": acc,
            "tp": tp, "fp": fp, "fn": fn,
            "count": sum(1 for gt in ground_truth
                         if isinstance(gt, dict) and cls in gt.get("codes", []))
        }

    vals = [v for v in results.values()]
    ave = {
        metric: sum(v[metric] for v in vals) / len(vals) if vals else 0
        for metric in ["P", "R", "F1", "Acc"]
    }
    vals_plus = [v for v in vals if v["count"] >= 5]
    ave_plus = {
        metric: sum(v[metric] for v in vals_plus) / len(vals_plus) if vals_plus else 0
        for metric in ["P", "R", "F1", "Acc"]
    }

    type_match = sum(1 for i, gt in enumerate(ground_truth)
                     if isinstance(gt, dict) and i < len(predictions)
                     and isinstance(predictions[i], dict)
                     and predictions[i].get("accident_type", "") == gt.get("accident_type", ""))
    sev_match = sum(1 for i, gt in enumerate(ground_truth)
                    if isinstance(gt, dict) and i < len(predictions)
                    and isinstance(predictions[i], dict)
                    and predictions[i].get("severity", "") == gt.get("severity", ""))

    return {
        "per_class": results,
        "ave": ave,
        "ave_plus": ave_plus,
        "type_acc": type_match / len(ground_truth) if ground_truth else 0,
        "sev_acc": sev_match / len(ground_truth) if ground_truth else 0,
    }


# ── Main ──
if __name__ == "__main__":
    print("=" * 60)
    print("S5_CAICSCoTPlus v2 — 增加基础字段提取")
    print("=" * 60)
    print(f"模型: {MODEL}")
    print(f"修复: 5层分析 + 1次综合汇总 = 6次API调用/条")
    print(f"注意: 此策略成本为 S0-S4 的 6× ({6*100}=600次调用)")

    # Load GT
    gt_data = json.loads(GT_PATH.read_text(encoding="utf-8"))
    samples = json.loads(SAMPLES_PATH.read_text(encoding="utf-8"))

    gt_labels = []
    for record in gt_data:
        codes = set()
        atype = sev = ""
        for ann in record.get("annotations", []):
            for res in ann.get("result", []):
                name = res["from_name"]
                val = res.get("value", {})
                if name in ('M', 'F', 'T', 'H', 'C'):
                    for v in val.get('choices', []):
                        codes.add(v.split('-')[0])
                elif name == 'accident_type':
                    atype = val.get('choices', [''])[0]
                elif name == 'severity':
                    sev = val.get('choices', [''])[0]
        gt_labels.append({"codes": sorted(codes), "accident_type": atype, "severity": sev})

    print(f"GT: {len(gt_labels)} 条, 平均{sum(len(g['codes']) for g in gt_labels)/len(gt_labels):.1f}编码/条\n")

    # Run
    predictions = []
    ok = 0
    t0 = time.time()

    for i, sample in enumerate(samples):
        text = re.sub(r'^#{1,4}\s+.*$', '', sample['data']['text'], flags=re.MULTILINE).strip()[:1500]
        result = call_llm_s5_v2(text)
        predictions.append(result)

        if result.get("codes"):
            ok += 1
        if (i + 1) % 10 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1:3d}/100] {ok}有编码 ({elapsed:.0f}s, {elapsed/(i+1):.1f}s/条)")

    elapsed = time.time() - t0
    metrics = compute_metrics(predictions, gt_labels)

    print(f"\n  ✅ {ok}/100 有编码 ({elapsed:.0f}s, {elapsed/100:.1f}s/条)")
    print(f"  Ave+(F1): {metrics['ave_plus']['F1']:.3f}")
    print(f"  Ave+(P):  {metrics['ave_plus']['P']:.3f}")
    print(f"  Ave+(R):  {metrics['ave_plus']['R']:.3f}")
    print(f"  TypeAcc:  {metrics['type_acc']:.3f} (v1=0.000)")
    print(f"  SevAcc:   {metrics['sev_acc']:.3f} (v1=0.010)")

    # Per-class
    print(f"\n  Per-Class F1 (GT>=5):")
    per_class = metrics["per_class"]
    main_classes = [(c, v) for c, v in per_class.items() if v["count"] >= 5]
    main_classes.sort(key=lambda x: -x[1]["F1"])
    for cls, vals in main_classes:
        print(f"    {cls} (gt={vals['count']:2d}): F1={vals['F1']:.3f} P={vals['P']:.3f} R={vals['R']:.3f}")

    # Stats
    no_type = sum(1 for p in predictions if not p.get("accident_type"))
    no_sev = sum(1 for p in predictions if not p.get("severity"))
    print(f"\n  无类型: {no_type}/100 (v1=100)")
    print(f"  无严重程度: {no_sev}/100 (v1=100)")

    # Save
    output = {
        "strategy": "S5_CAICSCoTPlus_v2",
        "description": "S5修复版：5层逐层分析 + 1次综合汇总(提取type/sev/deaths/injuries)，共6次API调用/条",
        "model": MODEL,
        "sample_size": 100,
        "metrics": metrics,
        "predictions": predictions,
        "elapsed": elapsed,
        "api_calls_per_sample": 6,
    }
    out_path = OUT_DIR / "S5_CAICSCoTPlus_v2.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 结果保存到: {out_path}")
