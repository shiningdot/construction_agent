#!/usr/bin/env python3
"""material_involved LLM 优先枚举化。"""
import json, os, re, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PHASE1_SYSTEM = """你是建筑施工安全专家。为 material_involved（事故涉及的关键材料/物料）字段设计 MECE 枚举框架。

## 背景
- 1466条事故记录，755个不同值
- 注意: 该字段可能包含非材料的异常值（如积雪[5次]、燃气[6次]等环境/危险物质）
- 材料名同义多样: 混凝土≈砼，砖≈砖块≈砌块，钢模≈钢模板

## MECE 硬约束
1. 互斥: 每种材料只属一类
2. 穷举: "其他"≤10%
3. 统计功效: 每类≥30条
4. 分类维度: 按材料材质/功能分类（非按形状/规格）

## 输出格式 (严格 JSON, ≤1500字)
```json
{
  "design_rationale": "一句话",
  "decision_rules": ["规则1","规则2"],
  "categories": [
    {"code":"MAT01","name":"混凝土及水泥制品","definition":"≤30字"}
  ]
}
```
编码用 MAT01-MAT99。categories 只含 code/name/definition。只输出 JSON。"""

def build_input(rows):
    total = sum(r[1] for r in rows)
    top = rows[:150]
    lines = [f"## 高频值 Top 150 (覆盖 {sum(r[1] for r in top)}/{total} 条)"]
    for i, (v, c) in enumerate(top, 1):
        lines.append(f"{i:3d}. [{c:3d}次] {v}")
    lines += ["", f"## 统计: 总唯一值{len(rows)}, 总记录{total}, 频次=1共{sum(1 for r in rows if r[1]==1)}个"]
    return "\n".join(lines)

def call_llm(system, user_msg, max_tokens=8192, expect_array=False):
    from anthropic import Anthropic
    client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"),
                       base_url=os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com"))
    for attempt in range(3):
        model = ["deepseek-v4-pro", "deepseek-v4-flash", "deepseek-v4-pro"][attempt]
        mt = [8192, 16384, 8192][attempt]
        try:
            resp = client.messages.create(model=model, max_tokens=mt, temperature=0.0,
                                          system=system, messages=[{"role":"user","content":user_msg}])
            raw = ""
            for b in resp.content:
                if hasattr(b,'type') and b.type=='thinking': continue
                if hasattr(b,'text'): raw = b.text.strip(); break
            if not raw: continue
            if expect_array:
                r = _parse_array(raw)
            else:
                r = _parse_obj(raw)
            if r: return r
        except Exception as e:
            if attempt == 2: print(f"  LLM失败: {e}")
        time.sleep(1)
    return None

def _parse_obj(text):
    for _ in range(3):
        try: return json.loads(text)
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\{[\s\S]*\}', text)
        if m: text = m.group(0); continue
        break
    return None

def _parse_array(text):
    for _ in range(3):
        try:
            r = json.loads(text)
            if isinstance(r, list): return r
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\[[\s\S]*\]', text)
        if m: text = m.group(0); continue
        matches = re.findall(r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(MAT\d{2})"\s*\}', text)
        if matches: return [{"text": t, "code": c} for t, c in matches]
        break
    return None

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="data/caiems_v2.db")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute(): db_path = Path.cwd() / db_path
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT material_involved, COUNT(*) FROM accidents
        WHERE material_involved IS NOT NULL AND material_involved != ''
          AND material_involved != '未提及' AND material_involved != 'null'
        GROUP BY material_involved ORDER BY COUNT(*) DESC
    """).fetchall()
    conn.close()
    print(f"读取 {len(rows)} 个唯一值, {sum(r[1] for r in rows)} 条记录")

    # Phase 1
    print("Phase 1: LLM 设计框架...")
    framework = call_llm(PHASE1_SYSTEM, build_input(rows))
    if not framework:
        print("[FATAL] Phase 1 失败"); return

    out = PROJECT_ROOT / "docs" / "enums"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "material_llm_framework.json", "w") as f:
        json.dump(framework, f, ensure_ascii=False, indent=2)

    cats = framework.get('categories', [])
    print(f"Phase 1 完成: {len(cats)} 个类别")
    for c in cats:
        print(f"  {c['code']} {c['name']}: {c.get('definition','')}")
    print(f"规则: {framework.get('decision_rules',[])}")

    # Phase 2
    print(f"\nPhase 2: 批量映射 {len(rows)} 个值...")
    cat_lines = [f"- **{c['code']} {c['name']}**: {c.get('definition','')}" for c in cats]
    rules_lines = [f"- {r}" for r in framework.get('decision_rules', [])]
    phase2_sys = f"""将建筑材料/物料文本映射到枚举。

## 枚举
{chr(10).join(cat_lines)}

## 规则
{chr(10).join(rules_lines)}

输出 JSON 数组: [{{"text":"混凝土","code":"MAT01"}}]"""

    mapping = {}
    batch_size = 80
    all_vals = [(v, c) for v, c in rows]
    for bs in range(0, len(all_vals), batch_size):
        batch = all_vals[bs:bs+batch_size]
        vt = "\n".join(f"{i+1}. {val}" for i, (val, _) in enumerate(batch))
        parsed = call_llm(phase2_sys, f"映射 {len(batch)} 个物料:\n\n{vt}", max_tokens=8192, expect_array=True)
        if parsed:
            for item in parsed:
                t = re.sub(r'^\[\d+次\]\s*', '', item.get("text",""))
                c = item.get("code", "MAT99")
                cnt = next((cc for vv, cc in batch if vv == t), 1)
                mapping[t] = {"code": c, "count": cnt}
            print(f"  [{bs//batch_size+1}/{(len(all_vals)-1)//batch_size+1}] {bs+1}-{bs+len(batch)}: {len(parsed)}条")
        else:
            print(f"  [{bs+1}-{bs+len(batch)}] 失败 → MAT99")
            for v, c in batch:
                mapping.setdefault(v, {"code": "MAT99-其他", "count": c})
        time.sleep(0.3)

    missing = sum(1 for v, _ in rows if v not in mapping)
    print(f"映射: {len(mapping)}/{len(rows)}, 缺失{missing}")

    with open(out / "material_llm_mapping.json", "w") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)

    dist = defaultdict(int); dist_rec = defaultdict(int)
    for v, info in mapping.items():
        dist[info['code']] += 1; dist_rec[info['code']] += info.get('count',1)
    total = sum(dist_rec.values())
    print(f"\n=== MAT 枚举分布 ===")
    for code in sorted(dist_rec.keys(), key=lambda x: -dist_rec[x]):
        r = dist_rec[code]; u = dist[code]
        print(f"  {code:6s} {u:4d}值 / {r:4d}条 ({r/total*100:.1f}%)")

    if not args.dry_run:
        _backfill(db_path, mapping, framework)
    else:
        print("\n[Dry-run]")

def _backfill(db_path, mapping, framework):
    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE IF NOT EXISTS enum_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, enum_type TEXT NOT NULL, code TEXT NOT NULL,
        name TEXT NOT NULL, definition TEXT NOT NULL, decision_rule TEXT,
        version TEXT NOT NULL DEFAULT '1.0-llm', sort_order INTEGER NOT NULL DEFAULT 0,
        sample_count INTEGER DEFAULT 0, parent_code TEXT, is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(enum_type, code, version))""")
    updated = 0
    for orig, info in mapping.items():
        cur = conn.execute("UPDATE accidents SET material_involved=? WHERE material_involved=?", (info['code'], orig))
        updated += cur.rowcount
    conn.commit()

    for i, cat in enumerate(framework.get('categories', [])):
        sample = sum(info['count'] for v, info in mapping.items() if info['code'] == cat['code'])
        conn.execute("""INSERT OR REPLACE INTO enum_definitions (enum_type,code,name,definition,decision_rule,version,sort_order,sample_count)
            VALUES ('material_involved',?,?,?,?,'1.0-llm',?,?)""",
                     (cat['code'], cat['name'], cat.get('definition',''), '', i+1, sample))
    conn.commit()

    from datetime import datetime
    conn.execute("INSERT OR REPLACE INTO pipeline_metadata (key,value,created_at) VALUES ('material_involved_enum_version',?,?)",
                 (json.dumps({"version":"1.0-llm","migration_date":datetime.now().isoformat(),"total_categories":len(framework.get('categories',[])),"method":"LLM自主归纳+全量映射"}, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()

    rem = conn.execute("SELECT COUNT(*) FROM accidents WHERE material_involved IS NOT NULL AND material_involved!='' AND material_involved!='未提及' AND material_involved!='null' AND material_involved NOT LIKE 'MAT%'").fetchone()[0]
    eqp = conn.execute("SELECT COUNT(*) FROM accidents WHERE material_involved LIKE 'MAT%'").fetchone()[0]
    print(f"DB回填: {updated}条, EQP编码: {eqp}, 剩余: {rem}")
    dist = conn.execute("SELECT material_involved, COUNT(*) FROM accidents WHERE material_involved LIKE 'MAT%' GROUP BY material_involved ORDER BY COUNT(*) DESC").fetchall()
    for code, cnt in dist:
        name = conn.execute("SELECT name FROM enum_definitions WHERE enum_type='material_involved' AND code=?", (code,)).fetchone()
        print(f"  {code} {name[0] if name else '?':20s} {cnt:5d} ({cnt/max(eqp,1)*100:.1f}%)")
    conn.close()
    print("完成!")

if __name__ == "__main__":
    main()
