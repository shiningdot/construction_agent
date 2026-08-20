#!/usr/bin/env python3
"""building_structure LLM 优先枚举化。含误填值自动清空(BLD_NULL→null)。"""
import json, os, re, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PHASE1_SYSTEM = """你是建筑结构工程师。为 building_structure 字段设计 MECE 枚举。

## 背景
- 455条记录, 164个值. 含约49条误填(不是结构类型, 如"主体结构""桥梁""隧道")
- 同义: 框架结构≈混凝土框架≈现浇框架≈全框架; 砖混≈砖混结构(预制板)
- 参考 GB 50011 结构体系分类

## MECE约束
1. 互斥穷举, "其他"≤10%. 误填→BLD_NULL(清空为null)
2. 编码: BLD01-BLD99, BLD_NULL

## 输出(纯JSON, ≤1200字)
```json
{
  "design_rationale": "一句话",
  "decision_rules": ["规则1"],
  "categories": [
    {"code":"BLD01","name":"钢结构","definition":"≤25字"},
    {"code":"BLD_NULL","name":"非结构类型","definition":"误填值应清空"}
  ]
}
```"""

def build_input(rows):
    total = sum(r[1] for r in rows)
    top = rows[:80]
    lines = [f"## Top 80 (覆盖 {sum(r[1] for r in top)}/{total} 条)"]
    for i, (v, c) in enumerate(top, 1):
        lines.append(f"{i:3d}. [{c:3d}次] {v}")
    lines.append("\n## 剩余低频值")
    for v, c in rows[80:]:
        lines.append(f"  [{c}次] {v}")
    lines += ["", f"统计: {len(rows)}值/{total}条, 约49条误填需清理"]
    return "\n".join(lines)

def call_llm(system, user_msg, expect_array=False):
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
            r = parse_array(raw) if expect_array else parse_obj(raw)
            if r: return r
        except Exception as e:
            if attempt == 2: print(f"  LLM失败: {e}")
        time.sleep(1)
    return None

def parse_obj(text):
    for _ in range(3):
        try: return json.loads(text)
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\{[\s\S]*\}', text)
        if m: text = m.group(0); continue
        break
    return None

def parse_array(text):
    for _ in range(3):
        try:
            r = json.loads(text)
            if isinstance(r, list): return r
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\[[\s\S]*\]', text)
        if m: text = m.group(0); continue
        matches = re.findall(r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(BLD[^"]*)"\s*\}', text)
        if matches: return [{"text": t, "code": c} for t, c in matches]
        break
    return None

def main():
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--db", default="data/caiems_v2.db"); p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    db_path = Path(args.db)
    if not db_path.is_absolute(): db_path = Path.cwd() / db_path
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("""
        SELECT building_structure, COUNT(*) FROM accidents
        WHERE building_structure IS NOT NULL AND building_structure != ''
          AND building_structure != '未提及' AND building_structure != 'null'
        GROUP BY building_structure ORDER BY COUNT(*) DESC
    """).fetchall()
    conn.close()
    print(f"读取 {len(rows)} 值, {sum(r[1] for r in rows)} 条")

    # Phase 1
    print("Phase 1...")
    framework = call_llm(PHASE1_SYSTEM, build_input(rows))
    if not framework: print("[FATAL]"); return
    cats = framework.get('categories', [])
    print(f"Phase 1: {len(cats)} 类")
    for c in cats: print(f"  {c['code']} {c['name']}: {c.get('definition','')}")
    print(f"  规则: {framework.get('decision_rules',[])}")

    out = PROJECT_ROOT / "docs" / "enums"; out.mkdir(parents=True, exist_ok=True)
    with open(out / "building_structure_llm_framework.json", "w") as f: json.dump(framework, f, ensure_ascii=False, indent=2)

    # Phase 2
    cat_lines = [f"- **{c['code']} {c['name']}**: {c.get('definition','')}" for c in cats]
    rules_lines = [f"- {r}" for r in framework.get('decision_rules', [])]
    phase2_sys = f"将建筑结构类型文本映射到枚举。\n## 枚举\n{chr(10).join(cat_lines)}\n## 规则\n{chr(10).join(rules_lines)}\n输出 JSON 数组: [{{\"text\":\"钢结构\",\"code\":\"BLD01\"}}]"

    mapping = {}
    all_vals = [(v, c) for v, c in rows]
    total_batches = (len(all_vals) - 1) // 60 + 1
    for bs in range(0, len(all_vals), 60):
        batch = all_vals[bs:bs+60]
        vt = "\n".join(f"{i+1}. {val}" for i, (val, _) in enumerate(batch))
        parsed = call_llm(phase2_sys, f"映射 {len(batch)} 个:\n\n{vt}", expect_array=True)
        if parsed:
            for item in parsed:
                t = re.sub(r'^\[\d+次\]\s*', '', item.get("text",""))
                c = item.get("code", "BLD99")
                cnt = next((cc for vv, cc in batch if vv == t), 1)
                mapping[t] = {"code": c, "count": cnt}
            print(f"  [{bs//60+1}/{total_batches}] {bs+1}-{bs+len(batch)}: {len(parsed)}条")
        else:
            print(f"  [{bs+1}] 失败, → BLD99")
            for v, c in batch: mapping.setdefault(v, {"code": "BLD99-其他", "count": c})
        time.sleep(0.3)

    print(f"映射: {len(mapping)}/{len(rows)}")
    with open(out / "building_structure_llm_mapping.json", "w") as f: json.dump(mapping, f, ensure_ascii=False, indent=2)

    dist = defaultdict(int); dist_rec = defaultdict(int)
    for v, info in mapping.items(): dist[info['code']] += 1; dist_rec[info['code']] += info.get('count',1)
    ttl = sum(dist_rec.values())
    print(f"\n=== BLD 分布 ===")
    for code in sorted(dist_rec.keys(), key=lambda x: -dist_rec[x]):
        print(f"  {code:10s} {dist[code]:4d}值 / {dist_rec[code]:4d}条 ({dist_rec[code]/ttl*100:.1f}%)")

    if not args.dry_run: _backfill(db_path, mapping, framework, cats)

def _backfill(db_path, mapping, framework, cats):
    conn = sqlite3.connect(str(db_path))
    updated = 0; nulled = 0
    for orig, info in mapping.items():
        code = info['code']
        if code == 'BLD_NULL':
            conn.execute("UPDATE accidents SET building_structure = NULL WHERE building_structure = ?", (orig,))
            nulled += 1
        else:
            cur = conn.execute("UPDATE accidents SET building_structure = ? WHERE building_structure = ?", (code, orig))
            updated += cur.rowcount
    conn.commit()

    conn.execute("""CREATE TABLE IF NOT EXISTS enum_definitions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, enum_type TEXT NOT NULL, code TEXT NOT NULL,
        name TEXT NOT NULL, definition TEXT NOT NULL, decision_rule TEXT,
        version TEXT NOT NULL DEFAULT '1.0-llm', sort_order INTEGER NOT NULL DEFAULT 0,
        sample_count INTEGER DEFAULT 0, parent_code TEXT, is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(enum_type, code, version))""")
    for i, cat in enumerate(cats):
        sample = sum(info['count'] for v, info in mapping.items() if info['code'] == cat['code'])
        conn.execute("""INSERT OR REPLACE INTO enum_definitions (enum_type,code,name,definition,decision_rule,version,sort_order,sample_count)
            VALUES ('building_structure',?,?,?,?,'1.0-llm',?,?)""",
                     (cat['code'], cat['name'], cat.get('definition',''), '', i+1, sample))
    conn.commit()

    from datetime import datetime
    conn.execute("INSERT OR REPLACE INTO pipeline_metadata (key,value,created_at) VALUES ('building_structure_enum_version',?,?)",
                 (json.dumps({"version":"1.0-llm","migration_date":datetime.now().isoformat(),"total_categories":len(cats)}, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()

    rem = conn.execute("SELECT COUNT(*) FROM accidents WHERE building_structure IS NOT NULL AND building_structure != '' AND building_structure NOT LIKE 'BLD%'").fetchone()[0]
    bld = conn.execute("SELECT COUNT(*) FROM accidents WHERE building_structure LIKE 'BLD%'").fetchone()[0]
    null_cnt = conn.execute("SELECT COUNT(*) FROM accidents WHERE building_structure IS NULL").fetchone()[0]
    print(f"\nDB: {updated}条枚举 + {nulled}条清空, BLD编码:{bld}, null总计:{null_cnt}, 残留:{rem}")
    dist = conn.execute("SELECT building_structure, COUNT(*) FROM accidents WHERE building_structure LIKE 'BLD%' GROUP BY building_structure ORDER BY COUNT(*) DESC").fetchall()
    for code, cnt in dist:
        name = conn.execute("SELECT name FROM enum_definitions WHERE enum_type='building_structure' AND code=?", (code,)).fetchone()
        print(f"  {code:10s} {name[0] if name else '?':20s} {cnt:4d}")
    conn.close()
    print("完成!")

if __name__ == "__main__": main()
