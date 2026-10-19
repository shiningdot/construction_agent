#!/usr/bin/env python3
"""equipment_involved LLM 优先枚举化。

两阶段:
  Phase 1: LLM 审视全部数据 → 自主设计 MECE 枚举框架
  Phase 2: LLM 批量映射全部值

用法:
  python3 scripts/enum_llm_equipment.py --db /path/to/caiems_v2.db [--dry-run]
"""

import json, os, re, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ══════════════════════════════════════════════════════════════════
# Phase 1: LLM 设计框架
# ══════════════════════════════════════════════════════════════════

PHASE1_SYSTEM = """你是建筑施工安全专家。为 equipment_involved（事故涉及的施工机械/设备/设施）字段设计 MECE 枚举框架。

## 背景
- 1374 条事故记录，762 个不同值（高度发散），需要归纳为枚举
- 注意: 该字段不仅包含"机械"，还有非机械的施工设施（脚手架、模板支撑、卸料平台等）
- 命名差异大: 同一设备有多种称呼（塔吊≈塔式起重机，吊车≈汽车吊≈起重机）

## MECE 硬约束
1. 互斥: 每个设备/设施只能属于一个类别
2. 穷举: 所有值都能找到归属，"其他"类≤10%
3. 统计功效: 每类预计≥30条
4. 分类维度建议: 按设备功能/工艺用途分类，而非按动力/大小分类

## 设计要求
- 命名统一化: 同类设备的多种称呼归入同一编码
- 编码格式: EQP01, EQP02, ...
- 总类别建议 10-14 个（含"其他"）
- 需包含: 定义、关键词列表（含同义词）、边界判定规则

## 输出格式 (严格 JSON, ≤2000 字)
```json
{
  "design_rationale": "一句话",
  "decision_rules": ["规则1","规则2"],
  "categories": [
    {"code":"EQP01","name":"起重设备","definition":"≤30字"},
    {"code":"EQP02","name":"土方机械","definition":"≤30字"}
  ]
}
```
只输出 JSON。categories 只含 code/name/definition 三个字段。"""


def build_phase1_input(rows):
    total = sum(r[1] for r in rows)
    top_150 = rows[:150]
    lines = [f"## 高频值 Top 150 (覆盖 {sum(r[1] for r in top_150)}/{total} 条)"]
    for i, (v, c) in enumerate(top_150, 1):
        lines.append(f"{i:3d}. [{c:3d}次] {v}")
    lines += [
        "", "## 统计",
        f"总唯一值: {len(rows)}, 总记录: {total}",
        f"频次=1: {sum(1 for r in rows if r[1]==1)}个, >=5: {sum(1 for r in rows if r[1]>=5)}个",
    ]
    return "\n".join(lines)


def call_phase1(rows):
    from anthropic import Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    user_msg = build_phase1_input(rows)
    print(f"Phase 1: 发送 {len(user_msg)} 字符...")
    t0 = time.time()

    for attempt in range(3):
        model = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-pro"][attempt]
        mt = [8192, 16384, 8192][attempt]
        try:
            resp = client.messages.create(
                model=model, max_tokens=mt, temperature=0.0,
                system=PHASE1_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw_text = ""
            for block in resp.content:
                if hasattr(block, 'type') and block.type == 'thinking': continue
                if hasattr(block, 'text'): raw_text = block.text.strip(); break
            if raw_text:
                framework = _parse_json(raw_text)
                if framework:
                    print(f"Phase 1 完成 ({time.time()-t0:.0f}s): {len(framework.get('categories',[]))} 个类别")
                    return framework
        except Exception as e:
            print(f"  Attempt {attempt+1}: {e}")
            time.sleep(1)
    return None


# ══════════════════════════════════════════════════════════════════
# Phase 2: LLM 批量映射
# ══════════════════════════════════════════════════════════════════

def call_phase2(framework, rows):
    from anthropic import Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))

    cats = framework['categories']
    rules = framework.get('decision_rules', [])
    cat_lines = [f"- **{c['code']} {c['name']}**: {c.get('definition','')}" for c in cats]
    rules_lines = [f"- {r}" for r in rules]

    system_prompt = f"""你是建筑施工安全专家。将事故涉及的设备/设施文本映射到枚举代码。

## 枚举定义
{chr(10).join(cat_lines)}

## 决策规则
{chr(10).join(rules_lines)}

## 输出格式
严格输出 JSON 数组: [{{"text": "塔吊", "code": "EQP01"}}, ...]
只输出 JSON。"""

    results = {}
    batch_size = 80
    all_vals = [(v, c) for v, c in rows]
    total_batches = (len(all_vals) - 1) // batch_size + 1

    for bs in range(0, len(all_vals), batch_size):
        batch = all_vals[bs:bs + batch_size]
        values_text = "\n".join(f"{i+1}. {val}" for i, (val, _) in enumerate(batch))
        user_msg = f"映射 {len(batch)} 个设备:\n\n{values_text}"

        for attempt in range(3):
            try:
                resp = client.messages.create(
                    model="deepseek-v4-flash", max_tokens=8192, temperature=0.0,
                    system=system_prompt,
                    messages=[{"role": "user", "content": user_msg}],
                )
                raw_text = ""
                for block in resp.content:
                    if hasattr(block, 'type') and block.type == 'thinking': continue
                    if hasattr(block, 'text'): raw_text = block.text.strip(); break

                parsed = _parse_json_array(raw_text)
                if not parsed:
                    time.sleep(1); continue

                for item in parsed:
                    t = re.sub(r'^\[\d+次\]\s*', '', item.get("text", ""))
                    c = item.get("code", "EQP99")
                    cnt = next((c_ for v_, c_ in batch if v_ == t), 1)
                    results[t] = {"code": c, "count": cnt}

                bn = bs // batch_size + 1
                print(f"  [{bn}/{total_batches}] {bs+1}-{bs+len(batch)}: {len(parsed)}条")
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  失败 [{bs+1}-{bs+len(batch)}]: {str(e)[:100]}")
                    for v, c in batch:
                        results.setdefault(v, {"code": "EQP99-其他", "count": c})
                time.sleep(1)
        time.sleep(0.3)

    return results


# ══════════════════════════════════════════════════════════════════
# 工具函数
# ══════════════════════════════════════════════════════════════════

def _parse_json(text):
    for _ in range(3):
        try: return json.loads(text)
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\{[\s\S]*\}', text)
        if m: text = m.group(0); continue
        break
    return None

def _parse_json_array(text):
    for _ in range(3):
        try:
            r = json.loads(text)
            if isinstance(r, list): return r
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\[[\s\S]*\]', text)
        if m: text = m.group(0); continue
        matches = re.findall(r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(EQP\d{2})"\s*\}', text)
        if matches: return [{"text": t, "code": c} for t, c in matches]
        break
    return None


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default="data/caiems_v2.db")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute(): db_path = Path.cwd() / db_path
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}"); sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
    rows = conn.execute("""
        SELECT equipment_involved, COUNT(*) FROM accidents
        WHERE equipment_involved IS NOT NULL AND equipment_involved != ''
          AND equipment_involved != '未提及' AND equipment_involved != 'null'
        GROUP BY equipment_involved ORDER BY COUNT(*) DESC
    """).fetchall()
    conn.close()

    print(f"读取 {len(rows)} 个唯一值, {sum(r[1] for r in rows)} 条记录")

    # Phase 1
    framework = call_phase1(rows)
    if not framework:
        print("[FATAL] Phase 1 失败"); sys.exit(1)

    output_dir = PROJECT_ROOT / "docs" / "enums"
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(output_dir / "equipment_llm_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)
    print(f"框架已保存")

    # 展示框架
    print(f"\n设计思路: {framework.get('design_rationale','')}")
    for cat in framework.get('categories', []):
        print(f"  {cat['code']} {cat['name']}: {cat.get('definition','')}")

    # Phase 2
    print(f"\nPhase 2: 批量映射 {len(rows)} 个值...")
    mapping = call_phase2(framework, rows)

    missing = sum(1 for v, _ in rows if v not in mapping)
    print(f"\n映射完成: {len(mapping)}/{len(rows)}, 缺失 {missing}")

    with open(output_dir / "equipment_llm_mapping.json", "w") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    # 分布
    dist = defaultdict(int)
    dist_rec = defaultdict(int)
    for v, info in mapping.items():
        dist[info['code']] += 1
        dist_rec[info['code']] += info.get('count', 1)

    total = sum(dist_rec.values())
    print(f"\n=== EQP 枚举分布 ===")
    for code in sorted(dist_rec.keys(), key=lambda x: -dist_rec[x]):
        r = dist_rec[code]; u = dist[code]
        print(f"  {code:6s} {u:4d}值 / {r:4d}条 ({r/total*100:.1f}%)")

    if args.dry_run:
        print("\n[Dry-run] 映射已保存，未回填DB。")
    else:
        _backfill(db_path, mapping)
    print("\n完成!")


def _backfill(db_path, mapping):
    conn = sqlite3.connect(str(db_path))
    updated = 0
    for orig_val, info in mapping.items():
        cursor = conn.execute("UPDATE accidents SET equipment_involved = ? WHERE equipment_involved = ?",
                              (info['code'], orig_val))
        updated += cursor.rowcount
    conn.commit()

    # 创建枚举定义表（如果不存在）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enum_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enum_type TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL,
            definition TEXT NOT NULL, decision_rule TEXT,
            version TEXT NOT NULL DEFAULT '2.0-llm',
            sort_order INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER DEFAULT 0, parent_code TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(enum_type, code, version)
        )
    """)
    conn.commit()

    remaining = conn.execute("""
        SELECT COUNT(*) FROM accidents
        WHERE equipment_involved IS NOT NULL AND equipment_involved != ''
          AND equipment_involved != '未提及' AND equipment_involved != 'null'
          AND equipment_involved NOT LIKE 'EQP%'
    """).fetchone()[0]

    print(f"DB回填: {updated} 条, 剩余自由文本: {remaining}")
    conn.close()


if __name__ == "__main__":
    main()
