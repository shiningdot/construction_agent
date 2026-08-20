#!/usr/bin/env python3
"""work_task LLM 优先枚举化 v2：LLM 自主归纳类别定义 + 全量映射。

两阶段:
  Phase 1: LLM 审视高频值 + 分布 → 自主设计 MECE 枚举框架
  Phase 2: LLM 按新框架批量映射全部 1963 个值

对比: 与规则优先版本 (docs/enums/work_task_mapping.json) 的差异

用法:
  python3 scripts/enum_llm_work_task.py --db /path/to/caiems_v2.db [--dry-run]
"""

import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════
# Phase 1: LLM 自主设计枚举框架
# ══════════════════════════════════════════════════════════════════

PHASE1_SYSTEM = """你是一位建筑施工安全领域的资深专家，正在为"建筑施工事故数据集"设计一套施工作业类型（work_task）的分类枚举体系。

## 背景
- 数据集包含 2204 条中国建筑施工事故记录
- 每条的 work_task 字段是从事故文本中提取的自由文本，描述事故发生时的具体施工作业
- 当前有 1963 个不同的自由文本值（LLM 输出高度发散），需要归纳为 MECE 互斥穷举的枚举

## 你的任务
1. 审视下面的高频值列表和完整分布统计
2. 设计一套 MECE 的枚举分类框架（建议 12-18 个类别）
3. 每个类别需要: 编码、名称、定义、关键词、典型示例
4. 特别关注: 当前"其他"类约占 19%，请尝试将其拆解到更合理的程度

## 设计要求（严格遵守）

### MECE 互斥性
- 任意一条施工作业文本，经过判定规则后，只能属于唯一一个类别
- 类别之间不允许有语义重叠（如"模板支撑"和"脚手架"如果事故特征无法区分则必须合并）
- 检查方法: 对每对相邻类别，能否举出一个反例让其同时满足两个类？能举出 → 说明不互斥，需合并或重定义

### MECE 穷举性
- 所有 1963 个值必须都能找到归属，不得有"无法分类"的遗漏
- "其他"类占比不得超过 12%（目标值），越低越好
- 如果某类语义在数据中高频出现但无对应类别 → 必须新增类别

### 统计功效
- 每个类别预计记录数 ≥ 30 条（基于频次估算）
- 如果某类预计 < 30 条，必须合并到相邻类别或扩大定义范围
- "桩基工程"当前仅 20 条 → 必须合并

### 专业合理
- 类别定义符合《建筑施工安全统一规范》(GB 50870)的工序分类逻辑
- 类别名称使用行业通用术语，避免自创词汇

## 参考: 当前使用的 15 类框架
WT01-土方与基坑、WT02-桩基工程、WT03-钢筋工程、WT04-模板与支撑、
WT05-混凝土浇筑、WT06-钢结构与网架、WT07-脚手架作业、WT08-塔吊与起重、
WT09-桥梁工程、WT10-隧道与地下、WT11-管道与管网、WT12-装饰与装修、
WT13-拆除作业、WT14-设备安装、WT15-其他

已知问题:
- WT02(20条)样本量太小
- WT04(模板支撑)和WT07(脚手架)在实际文本中难以区分
- WT15(其他)占比过高(18.9%)，其中含50+条"维修/加固"类
- 缺少"场内运输""检测试验"等辅助作业类别

## 输出格式
严格输出 JSON，不要 Markdown。格式如下（注意：categories 数组只包含 code/name/definition 三个字段以节省空间，不要输出 keywords/examples/notes）:

```json
{
  "design_rationale": "设计思路（一句话）",
  "decision_rules": [
    "规则1: xxx",
    "规则2: xxx"
  ],
  "categories": [
    {"code":"WT01","name":"土方基坑与桩基","definition":"基坑开挖支护、土方回填、各类桩基础施工"},
    {"code":"WT02","name":"钢筋与混凝土","definition":"钢筋绑扎加工、混凝土搅拌浇筑振捣养护"}
  ]
}
```

注意:
- categories 数组只含 code/name/definition，不要加其他字段
- 每个 definition 控制在 40 字以内
- decision_rules 每条 30 字以内
- 总输出控制在 3000 字以内，确保 JSON 不被截断"""


def build_phase1_input(rows: list[tuple[str, int]]) -> str:
    """构建 Phase 1 的输入：高频值 + 分布统计。"""
    total_records = sum(r[1] for r in rows)
    top_200 = rows[:200]
    top_records = sum(r[1] for r in top_200)

    lines = [
        "## 高频值 Top 200（带频次）",
        f"（覆盖 {top_records}/{total_records} 条记录，{top_records/total_records*100:.1f}%）",
        "",
    ]
    for i, (val, cnt) in enumerate(top_200, 1):
        lines.append(f"{i:3d}. [{cnt:3d}次] {val}")

    lines += [
        "",
        "## 完整分布统计",
        f"- 总唯一值: {len(rows)}",
        f"- 总记录数: {total_records}",
        f"- 频次=1的值: {sum(1 for r in rows if r[1]==1)} 个 ({sum(1 for r in rows if r[1]==1)/len(rows)*100:.1f}%)",
        f"- 频次>=2的值: {sum(1 for r in rows if r[1]>=2)} 个",
        f"- 频次>=5的值: {sum(1 for r in rows if r[1]>=5)} 个",
        f"- 频次>=10的值: {sum(1 for r in rows if r[1]>=10)} 个",
    ]

    return "\n".join(lines)


def call_phase1(rows: list[tuple[str, int]]) -> dict | None:
    """调用 LLM 自主设计枚举框架。"""
    from anthropic import Anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.getenv("ANTHROPIC_MODEL", "deepseek-v4-flash")

    if not api_key:
        print("[ERROR] 未设置 ANTHROPIC_API_KEY")
        return None

    client = Anthropic(api_key=api_key, base_url=base_url)
    user_msg = build_phase1_input(rows)

    print(f"Phase 1: 发送 {len(user_msg)} 字符给 LLM 设计枚举框架...")
    t0 = time.time()

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0.0,
            system=PHASE1_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )

        # 解析响应
        raw_text = ""
        for block in resp.content:
            if hasattr(block, 'type') and block.type == 'thinking':
                continue
            if hasattr(block, 'text'):
                raw_text = block.text.strip()
                break

        if not raw_text:
            print("[ERROR] 响应无 text 内容")
            return None

        # 多级 JSON 解析
        framework = _parse_json(raw_text)
        if framework:
            elapsed = time.time() - t0
            print(f"Phase 1 完成 ({elapsed:.0f}s), "
                  f"设计了 {framework.get('total_categories', '?')} 个类别")
            return framework
        else:
            print(f"[ERROR] 无法解析框架 JSON: {raw_text[:500]}")
            return None

    except Exception as e:
        print(f"[ERROR] Phase 1 失败: {e}")
        return None


# ══════════════════════════════════════════════════════════════════
# Phase 2: LLM 全量映射
# ══════════════════════════════════════════════════════════════════

PHASE2_SYSTEM_TEMPLATE = """你是建筑施工安全专家。请将以下施工作业类型自由文本映射到枚举代码。

## 枚举定义
{framework_text}

## 判定规则
{decision_rules}

## 边界案例
{boundary_cases}

## 输出格式
严格输出 JSON 数组:
```json
[{{"text": "浇筑混凝土", "code": "WT05"}}, ...]
```
只输出 JSON，不要其他内容。"""


def build_phase2_system(framework: dict) -> str:
    """根据 LLM 设计的框架构建 Phase 2 的 system prompt。"""
    cat_lines = []
    for cat in framework.get("categories", []):
        cat_lines.append(
            f"- **{cat['code']} {cat['name']}**: {cat.get('definition', '')}"
        )

    rules_lines = []
    for rule in framework.get("decision_rules", []):
        if isinstance(rule, dict):
            rules_lines.append(f"- {rule.get('rule', '')}: {rule.get('description', '')}")
        else:
            rules_lines.append(f"- {rule}")

    return PHASE2_SYSTEM_TEMPLATE.format(
        framework_text="\n".join(cat_lines),
        decision_rules="\n".join(rules_lines),
        boundary_cases="（按决策规则自行判断边界情况）",
    )


def call_phase2(framework: dict, values: list[tuple[str, int]]) -> dict[str, dict]:
    """用 LLM 框架批量映射所有值。"""
    from anthropic import Anthropic

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    base_url = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = os.getenv("ANTHROPIC_MODEL", "deepseek-v4-flash")

    if not api_key:
        print("[ERROR] 未设置 ANTHROPIC_API_KEY")
        return {}

    client = Anthropic(api_key=api_key, base_url=base_url)
    system_prompt = build_phase2_system(framework)

    results = {}
    batch_size = 80
    max_retries = 2

    for batch_start in range(0, len(values), batch_size):
        batch = values[batch_start:batch_start + batch_size]
        values_text = "\n".join(
            f"{i+1}. {val}" for i, (val, cnt) in enumerate(batch)
        )
        user_msg = f"请将以下 {len(batch)} 个施工作业文本映射到枚举:\n\n{values_text}"

        for attempt in range(max_retries + 1):
            try:
                resp = client.messages.create(
                    model=model,
                    max_tokens=8192,
                    temperature=0.0,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )

                raw_text = ""
                for block in resp.content:
                    if hasattr(block, 'type') and block.type == 'thinking':
                        continue
                    if hasattr(block, 'text'):
                        raw_text = block.text.strip()
                        break

                llm_results = _parse_json_array(raw_text)
                if not llm_results:
                    raise ValueError(f"解析失败: {raw_text[:200]}")

                for item in llm_results:
                    orig_text = item.get("text", "")
                    orig_text = re.sub(r'^\[\d+次\]\s*', '', orig_text)
                    code = item.get("code", "WT99")
                    # 找到 count
                    cnt = 1
                    for v, c in batch:
                        if v == orig_text:
                            cnt = c
                            break
                    results[orig_text] = {"code": code, "count": cnt}

                print(f"  Phase 2 [{batch_start+1}-{batch_start+len(batch)}/{len(values)}] "
                      f"完成 {len(llm_results)} 条")
                break  # 成功，跳出重试

            except Exception as e:
                if attempt < max_retries:
                    print(f"  重试 {attempt+1}: {str(e)[:100]}")
                    time.sleep(1)
                else:
                    print(f"  批次失败 [{batch_start}:{batch_start+batch_size}]: {str(e)[:100]}")
                    for val, cnt in batch:
                        results.setdefault(val, {"code": "WT99-未分类", "count": cnt})

        time.sleep(0.3)

    return results


# ══════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════

def _parse_json(text: str) -> dict | None:
    """多级 JSON 解析。"""
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # 提取 {...}
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _parse_json_array(text: str) -> list[dict] | None:
    """多级 JSON 数组解析。"""
    # 直接解析
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass
    # 代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    # 提取数组
    m = re.search(r'\[[\s\S]*\]', text)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
    # 逐行正则
    pattern = r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(WT\d{2}[^"]*)"\s*\}'
    matches = re.findall(pattern, text)
    if matches:
        return [{"text": t, "code": c} for t, c in matches]
    return None


# ══════════════════════════════════════════════════════════════════
# 对比分析
# ══════════════════════════════════════════════════════════════════

def compare_versions(llm_mapping: dict, rule_mapping: dict, rows):
    """对比 LLM 优先 vs 规则优先的差异。"""
    # 加载规则映射
    disagreements = []
    llm_dist = defaultdict(int)
    rule_dist = defaultdict(int)

    for val, _ in rows:
        llm_info = llm_mapping.get(val, {})
        rule_info = rule_mapping.get(val, {})
        llm_code = llm_info.get("code", "MISSING")
        rule_code = rule_info.get("code", "MISSING")

        llm_dist[llm_code] += 1
        rule_dist[rule_code] += 1

        if llm_code != rule_code:
            disagreements.append({
                "value": val,
                "llm_code": llm_code,
                "rule_code": rule_code,
                "count": rule_info.get("count", 1),
            })

    return {
        "total_values": len(rows),
        "disagreement_count": len(disagreements),
        "disagreement_rate": len(disagreements) / max(len(rows), 1),
        "llm_distribution": dict(llm_dist),
        "rule_distribution": dict(rule_dist),
        "disagreements": sorted(disagreements, key=lambda x: -x["count"])[:30],
    }


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="work_task LLM 优先枚举化")
    parser.add_argument("--db", default="data/caiems_v2.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        sys.exit(1)

    # 读取数据
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT work_task, COUNT(*) as cnt
        FROM accidents
        WHERE work_task IS NOT NULL AND work_task != ''
          AND work_task != '未提及' AND work_task != 'null'
        GROUP BY work_task
        ORDER BY cnt DESC
    """).fetchall()
    conn.close()

    print(f"读取 {len(rows)} 个唯一值, {sum(r[1] for r in rows)} 条记录")

    # ══ Phase 1: LLM 设计框架 ══
    framework = call_phase1(rows)
    if not framework:
        print("[FATAL] Phase 1 失败, 终止")
        sys.exit(1)

    # 保存框架
    output_dir = PROJECT_ROOT / "docs" / "enums"
    output_dir.mkdir(parents=True, exist_ok=True)
    framework_path = output_dir / "work_task_llm_framework.json"
    with open(framework_path, "w", encoding="utf-8") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)
    print(f"LLM 框架已保存: {framework_path}")

    # ══ Phase 2: LLM 批量映射 ══
    print(f"\nPhase 2: LLM 批量映射 {len(rows)} 个值...")
    all_values = [(v, c) for v, c in rows]
    llm_mapping = call_phase2(framework, all_values)

    # 验证完整性
    missing = sum(1 for v, _ in rows if v not in llm_mapping)
    print(f"\n映射完成: {len(llm_mapping)}/{len(rows)}, 缺失 {missing}")

    # 保存 LLM 映射
    llm_mapping_path = output_dir / "work_task_llm_mapping.json"
    with open(llm_mapping_path, "w", encoding="utf-8") as f:
        json.dump(llm_mapping, f, ensure_ascii=False, indent=2)
    print(f"LLM 映射已保存: {llm_mapping_path}")

    # ══ 对比分析 ══
    rule_mapping_path = output_dir / "work_task_mapping.json"
    if rule_mapping_path.exists():
        with open(rule_mapping_path) as f:
            rule_mapping = json.load(f)

        comparison = compare_versions(llm_mapping, rule_mapping, rows)

        comparison_path = output_dir / "work_task_llm_vs_rule_comparison.json"
        with open(comparison_path, "w", encoding="utf-8") as f:
            json.dump(comparison, f, ensure_ascii=False, indent=2)

        print(f"\n═══ LLM vs 规则 对比 ═══")
        print(f"分歧率: {comparison['disagreement_count']}/{comparison['total_values']} "
              f"({comparison['disagreement_rate']*100:.1f}%)")
        print(f"\nLLM 分布:")
        for code, cnt in sorted(comparison['llm_distribution'].items(), key=lambda x: -x[1]):
            print(f"  {code}: {cnt}")
        print(f"\n规则分布:")
        for code, cnt in sorted(comparison['rule_distribution'].items(), key=lambda x: -x[1]):
            print(f"  {code}: {cnt}")
        print(f"\nTop 10 分歧案例:")
        for d in comparison['disagreements'][:10]:
            print(f"  [{d['count']}次] {d['value']:30s} LLM→{d['llm_code']:12s} 规则→{d['rule_code']}")
        print(f"对比报告: {comparison_path}")

    print("\n完成!")


if __name__ == "__main__":
    main()
