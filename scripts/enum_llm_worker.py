#!/usr/bin/env python3
"""worker_type LLM 优先枚举化。"""
import json, os, re, sqlite3, sys, time
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PHASE1_SYSTEM = """你是建筑施工安全专家。为 worker_type（伤亡人员工种/角色）字段设计 MECE 枚举框架。

## 背景
- 1905条记录，589个不同值
- 核心问题: 同义高度发散——普工(428)/民工(87)/施工人员(85)/工人(76)/建筑工人(25)/农民工(19)/临时工(16)/力工(8)都是"通用劳务工"
- 特殊: 路人(30)/村民(10)等非施工人员
- 部分值为多工种并列: "木工、瓦工"

## MECE 硬约束
1. 互斥: 每类有明确区分标准
2. 穷举: "其他"≤8%
3. 统计功效: 每类≥30条
4. 分类维度: 按工种技能/执业资格分类（非按雇佣形式）

## 输出格式 (严格 JSON, ≤1500字)
```json
{
  "design_rationale": "一句话",
  "decision_rules": ["规则1","规则2"],
  "categories": [
    {"code":"WKR01","name":"通用劳务工","definition":"≤30字"},
    {"code":"WKR02","name":"木工及模板工","definition":"≤30字"}
  ]
}
```
编码 WKR01-WKR99。categories 只含 code/name/definition。只输出 JSON。"""

def build_input(rows):
    total = sum(r[1] for r in rows)
    top = rows[:150]
    lines = [f"## Top 150 (覆盖 {sum(r[1] for r in top)}/{total} 条)"]
    for i, (v, c) in enumerate(top, 1):
        lines.append(f"{i:3d}. [{c:3d}次] {v}")
    lines += ["", f"统计: {len(rows)}唯一值, {total}条, 频次=1共{sum(1 for r in rows if r[1]==1)}个"]
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
            r = _parse_array(raw) if expect_array else _parse_obj(raw)
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
        try: r = json.loads(text); return r if isinstance(r, list) else None
        except: pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m: text = m.group(1); continue
        m = re.search(r'\[[\s\S]*\]', text)
        if m: text = m.group(0); continue
        matches = re.findall(r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(WKR\d{2})"\s*\}', text)
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
        SELECT worker_type, COUNT(*) FROM accidents WHERE worker_type IS NOT NULL AND worker_type != ''
          AND worker_type != '未提及' AND worker_type != 'null'
        GROUP BY worker_type ORDER BY COUNT(*) DESC
    """).fetchall()
    conn.close()
    print(f"读取 {len(rows)} 值, {sum(r[1] for r in rows)} 条")

    print("Phase 1: LLM 设计框架...")
    framework = call_llm(PHASE1_SYSTEM, build_input(rows))
    if not framework: print("[FATAL]"); return

    out = PROJECT_ROOT / "docs" / "enums"; out.mkdir(parents=True, exist_ok=True)
    with open(out / "worker_llm_framework.json", "w") as f: json.dump(framework, f, ensure_ascii=False, indent=2)
    cats = framework.get('categories', [])
    print(f"Phase 1: {len(cats)} 类")
    for c in cats: print(f"  {c['code']} {c['name']}: {c.get('definition','')}")

    print(f"\nPhase 2: 映射 {len(rows)} 值...")
    cat_lines = [f"- **{c['code']} {c['name']}**: {c.get('definition','')}" for c in cats]
    rules_lines = [f"- {r}" for r in framework.get('decision_rules', [])]
    phase2_sys = f"""将伤亡人员工种/角色映射到枚举。\n## 枚举\n{chr(10).join(cat_lines)}\n## 规则\n{chr(10).join(rules_lines)}\n输出 JSON 数组: [{{"text":"普工","code":"WKR01"}}]"""

    mapping = {}
    all_vals = [(v, c) for v, c in rows]
    for bs in range(0, len(all_vals), 80):
        batch = all_vals[bs:bs+80]
        vt = "\n".join(f"{i+1}. {val}" for i, (val, _) in enumerate(batch))
        parsed = call_llm(phase2_sys, f"映射 {len(batch)} 个工种:\n\n{vt}", max_tokens=8192, expect_array=True)
        if parsed:
            for item in parsed:
                t = re.sub(r'^\[\d+次\]\s*', '', item.get("text",""))
                c = item.get("code", "WKR99")
                cnt = next((cc for vv, cc in batch if vv == t), 1)
                mapping[t] = {"code": c, "count": cnt}
            print(f"  [{bs//80+1}/{(len(all_vals)-1)//80+1}] {bs+1}-{bs+len(batch)}: {len(parsed)}条")
        else:
            print(f"  [{bs+1}] 失败 → WKR99")
            for v, c in batch: mapping.setdefault(v, {"code": "WKR99-其他", "count": c})
        time.sleep(0.3)

    missing = sum(1 for v, _ in rows if v not in mapping)
    print(f"映射: {len(mapping)}/{len(rows)}, 缺失{missing}")
    with open(out / "worker_llm_mapping.json", "w") as f: json.dump(mapping, f, ensure_ascii=False, indent=2)

    dist = defaultdict(int); dist_rec = defaultdict(int)
    for v, info in mapping.items(): dist[info['code']] += 1; dist_rec[info['code']] += info.get('count',1)
    total = sum(dist_rec.values())
    print(f"\n=== WKR 分布 ===")
    for code in sorted(dist_rec.keys(), key=lambda x: -dist_rec[x]):
        print(f"  {code:6s} {dist[code]:4d}值 / {dist_rec[code]:4d}条 ({dist_rec[code]/total*100:.1f}%)")

    if not args.dry_run: _backfill(db_path, mapping, framework)

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
        cur = conn.execute("UPDATE accidents SET worker_type=? WHERE worker_type=?", (info['code'], orig))
        updated += cur.rowcount
    conn.commit()
    for i, cat in enumerate(framework.get('categories', [])):
        sample = sum(info['count'] for v, info in mapping.items() if info['code'] == cat['code'])
        conn.execute("""INSERT OR REPLACE INTO enum_definitions (enum_type,code,name,definition,decision_rule,version,sort_order,sample_count)
            VALUES ('worker_type',?,?,?,?,'1.0-llm',?,?)""",
                     (cat['code'], cat['name'], cat.get('definition',''), '', i+1, sample))
    conn.commit()
    from datetime import datetime
    conn.execute("INSERT OR REPLACE INTO pipeline_metadata (key,value,created_at) VALUES ('worker_type_enum_version',?,?)",
                 (json.dumps({"version":"1.0-llm","migration_date":datetime.now().isoformat(),"total_categories":len(framework.get('categories',[])),"method":"LLM自主归纳+全量映射"}, ensure_ascii=False), datetime.now().isoformat()))
    conn.commit()
    rem = conn.execute("SELECT COUNT(*) FROM accidents WHERE worker_type IS NOT NULL AND worker_type!='' AND worker_type!='未提及' AND worker_type!='null' AND worker_type NOT LIKE 'WKR%'").fetchone()[0]
    wkr = conn.execute("SELECT COUNT(*) FROM accidents WHERE worker_type LIKE 'WKR%'").fetchone()[0]
    print(f"DB回填: {updated}条, WKR编码: {wkr}, 剩余: {rem}")
    dist = conn.execute("SELECT worker_type, COUNT(*) FROM accidents WHERE worker_type LIKE 'WKR%' GROUP BY worker_type ORDER BY COUNT(*) DESC").fetchall()
    for code, cnt in dist:
        name = conn.execute("SELECT name FROM enum_definitions WHERE enum_type='worker_type' AND code=?", (code,)).fetchone()
        print(f"  {code} {name[0] if name else '?':20s} {cnt:5d} ({cnt/max(wkr,1)*100:.1f}%)")
    conn.close()
    print("完成!")

if __name__ == "__main__": main()
