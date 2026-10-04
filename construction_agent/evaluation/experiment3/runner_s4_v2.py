"""S4_CAICSCoT v2 — 优化 Prompt，修复过度保守问题。

v1 问题:
  - 1500字 prompt 采用"文本提到关键词X？"条件检查格式 → LLM 过度保守
  - Recall=0.319, 30%空编码率, Precision=0.819
  - 9/100 解析失败

v2 优化:
  1. 从"条件检查"改为"线索引导+两步判断（显性匹配+隐性推断）"
  2. 缩短到 ~600 字
  3. 授权 LLM 根据事故性质推断隐性致因
  4. 安全分析原则：宁可多标不漏标
  5. 明确列出所有 accident_type/severity 枚举值
  6. 要求严格输出纯JSON

输出到 S4_CAICSCoT_v2.json（不覆盖原始 S4_CAICSCoT.json）
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

# ── 优化后的 S4 Prompt ──
S4_V2_SYSTEM = """你是建筑施工安全事故分析专家。使用CAICS框架（5层19类）从事故文本中提取致因编码。

## CAICS 致因框架

M.管理根基：M1现场监管缺失 M2隐患排查失效 M3安全培训不足 M4制度规程漏洞 M5市场行为违规
F.防线失效：F1临边洞口防护缺失 F2支撑体系失稳 F3设备安全装置失效 F4个体防护未使用
T.触发条件：T1设备机具故障 T2物料状态异常 T3地质环境突变 T4气象水文触发
H.人的行为：H1违反操作规程 H2违规指挥决策 H3风险辨识失败 H4应急行为失当
C.后果放大：C1救援响应迟缓 C2信息迟报瞒报

## 编码指南

每个编码按两步判断：

第一步（显性匹配）：文中是否直接出现相关表述？常见线索词见下方——但线索词只是参考，同义表述也算匹配。
第二步（隐性推断）：即使文中没有明确表述，根据事故类型、严重程度和施工安全常识，该致因是否必然存在？建筑施工事故极少由孤立原因造成——管理缺陷（M层）几乎普遍存在，人的行为（H层）通常与管理失效互为因果。

原则：安全分析宁可多标不漏标。即使线索词没出现，只要事故性质暗示该致因可能存在，就输出编码。

## 线索参考

M层（管理根基）：
- M1现场监管缺失：监管不到位/监理失职/未验收/未制止/安全管理混乱/以包代管
- M2隐患排查失效：未排查/隐患未整改/未评估风险/安全检查走过场
- M3安全培训不足：未经培训/无证上岗/交底不到位/不熟悉操作规程/缺乏安全知识
- M4制度规程漏洞：无方案施工/制度缺失/操作规程不健全/应急预案缺失
- M5市场行为违规：违法分包/无资质/超资质/转包/抢工期/未办理报建/未取得施工许可

F层（防线失效）：
- F1临边洞口防护缺失：无防护栏杆/临边无防护/洞口未覆盖/无安全网/未设置防护
- F2支撑体系失稳：模板坍塌/脚手架失稳/支撑体系不足/未按方案搭设/支架倒塌
- F3设备安全装置失效：限位器失效/防坠器损坏/安全装置被屏蔽/保险装置失灵
- F4个体防护未使用：未系安全带/未戴安全帽/无PPE/防护用品缺失

T层（触发条件）：
- T1设备机具故障：设备故障/钢丝绳断裂/机械失灵/塔吊倒塌/升降机故障
- T2物料状态异常：堆载超限/材料有缺陷/荷载过大/物料失稳/瓦斯积聚/珍珠岩浸水
- T3地质环境突变：涌水/边坡失稳/地基沉降/土体失稳/基坑坍塌/土方滑坡
- T4气象水文触发：大风/暴雨/暴雪/雷电/高温/连日降雨/台风

H层（人的行为）：
- H1违反操作规程：违反操作规程/违反施工方案/违反规范要求/违规操作
- H2违规指挥决策：违章指挥/强令冒险作业/明知风险仍施工/违章决策
- H3风险辨识失败：未意识到危险/安全意识薄弱/疏忽大意/未检查确认/判断失误
- H4应急行为失当：盲目施救/未及时撤离/救援不当扩大伤亡/应急处理错误

C层（后果放大）：
- C1救援响应迟缓：救援不及时/无应急预案/二次坍塌/延误抢救/救援力量不足
- C2信息迟报瞒报：瞒报/迟报/漏报/未及时上报/隐瞒不报

## 输出格式

严格只输出一行JSON（不要任何其他文字，不要markdown代码块）：
{"codes":["M1","F2","T2","H1"],"accident_type":"坍塌","severity":"较大事故","deaths":3,"injuries":5,"direct_cause":"简述直接原因"}

- codes: 本次分析选出的所有CAICS编码（无编码时为空列表[]）
- accident_type 必须从以下选择：坍塌/高处坠落/触电/机械伤害/起重伤害/物体打击/土方坍塌/火灾爆炸/中毒窒息/淹溺/车辆伤害/其他
- severity 必须从以下选择：一般事故/较大事故/重大事故/特别重大事故
- deaths, injuries: 整数，未知则为0"""

S4_V2_USER_PREFIX = "请为以下事故提取CAICS编码：\n\n"


# ── Helper functions (复用 runner.py 逻辑) ──
def extract_json(text: str) -> dict:
    """从混合输出中提取JSON。"""
    try:
        return json.loads(text)
    except Exception:
        pass
    # ```json 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    # 从最后一行开始找
    lines = text.strip().split('\n')
    for line in reversed(lines):
        if line.strip().startswith('{'):
            try:
                return json.loads(line.strip())
            except Exception:
                pass
    # Smart brace
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
    # 贪婪 fallback
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        s = m.group(0)
        if s.count('{') > s.count('}'):
            s += '}' * (s.count('{') - s.count('}'))
        try:
            return json.loads(s)
        except Exception:
            pass
    return {"codes": [], "_error": "no_json", "_raw": text[:200]}


def call_llm(system: str, user: str) -> dict:
    """调用 DeepSeek 并解析 JSON。"""
    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=2048, temperature=0.0,
            system=system, messages=[{"role": "user", "content": user}]
        )
        raw = ""
        for blk in resp.content:
            if hasattr(blk, "text"):
                raw += blk.text
        result = extract_json(raw)
        if isinstance(result, list):
            result = {"codes": result}
        if "_error" in result:
            result["_raw"] = raw[:300]
        return result
    except Exception as e:
        return {"codes": [], "_error": str(e)[:80]}


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
    print("S4_CAICSCoT v2 — 优化 Prompt 重跑")
    print("=" * 60)
    print(f"模型: {MODEL}")
    print(f"优化点: 条件检查→线索引导+两步判断, 1500→600字, 授权推断")

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
        text = sample['data']['text']
        clean = re.sub(r'^#{1,4}\s+.*$', '', text, flags=re.MULTILINE).strip()[:1500]

        result = call_llm(S4_V2_SYSTEM, S4_V2_USER_PREFIX + clean)
        predictions.append(result)

        if result.get("codes"):
            ok += 1
        if (i + 1) % 20 == 0:
            elapsed = time.time() - t0
            print(f"  [{i+1:3d}/100] {ok}有编码 ({elapsed:.0f}s)")

    elapsed = time.time() - t0
    metrics = compute_metrics(predictions, gt_labels)

    print(f"\n  ✅ {ok}/100 有编码 ({elapsed:.0f}s, {elapsed/100:.1f}s/条)")
    print(f"  Ave+(F1): {metrics['ave_plus']['F1']:.3f}")
    print(f"  Ave+(P):  {metrics['ave_plus']['P']:.3f}")
    print(f"  Ave+(R):  {metrics['ave_plus']['R']:.3f}")
    print(f"  TypeAcc:  {metrics['type_acc']:.3f}")
    print(f"  SevAcc:   {metrics['sev_acc']:.3f}")

    # Per-class summary
    print(f"\n  Per-Class F1 (GT>=5):")
    per_class = metrics["per_class"]
    main_classes = [(c, v) for c, v in per_class.items() if v["count"] >= 5]
    main_classes.sort(key=lambda x: -x[1]["F1"])
    for cls, vals in main_classes:
        print(f"    {cls} (gt={vals['count']:2d}): F1={vals['F1']:.3f} P={vals['P']:.3f} R={vals['R']:.3f}")

    # Count errors and empty
    errors = sum(1 for p in predictions if "_error" in p)
    empty = sum(1 for p in predictions if not p.get("codes"))
    no_type = sum(1 for p in predictions if not p.get("accident_type"))
    print(f"\n  解析错误: {errors}/100")
    print(f"  空编码: {empty}/100")
    print(f"  无类型: {no_type}/100")

    # Save
    output = {
        "strategy": "S4_CAICSCoT_v2",
        "description": "S4优化版：条件检查→线索引导+两步判断，缩短prompt到600字，授权推断",
        "model": MODEL,
        "sample_size": 100,
        "metrics": metrics,
        "predictions": predictions,
        "elapsed": elapsed,
        "prompt": {
            "system": S4_V2_SYSTEM,
            "user_prefix": S4_V2_USER_PREFIX,
        }
    }
    out_path = OUT_DIR / "S4_CAICSCoT_v2.json"
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ 结果保存到: {out_path}")
