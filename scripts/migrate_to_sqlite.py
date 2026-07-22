#!/usr/bin/env python3
"""JSON → SQLite 数据迁移脚本。

将 data/accidents_merged.json 中的所有记录迁移到 SQLite 数据库，
含精确类型转换和关联表拆分。

用法:
    python scripts/migrate_to_sqlite.py
    python scripts/migrate_to_sqlite.py --source data/accidents_merged.json
    python scripts/migrate_to_sqlite.py --stats  # 迁移后对比统计
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from collections import Counter

# 确保项目根目录在 path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from construction_agent.database.sqlite_repository import SqliteAccidentRepository
from construction_agent.models.accident import Accident

DB_URL = "sqlite:///data/caiems.db"
JSON_PATH = "data/accidents_merged.json"


def load_json(path: str) -> list[dict]:
    """加载 JSON 数据集。"""
    p = Path(path)
    if not p.exists():
        print(f"❌ JSON 文件不存在: {p}")
        sys.exit(1)
    data = json.loads(p.read_text(encoding="utf-8"))
    print(f"📂 加载 {len(data)} 条记录 from {p}")
    return data


def migrate(repo: SqliteAccidentRepository, records: list[dict], dry_run: bool = False):
    """执行迁移。"""
    start = time.time()
    success = 0
    errors = []

    for i, rec in enumerate(records):
        try:
            # 类型转换
            converted = convert_record(rec)
            accident = Accident(**converted)
            accident.auto_derive_fields()

            if not dry_run:
                result = repo.add(accident)
            else:
                result = type("R", (), {"action": "dry_run", "accident_id": accident.id})()

            success += 1
            if (i + 1) % 200 == 0:
                print(f"  已处理 {i+1}/{len(records)}...")

        except Exception as e:
            errors.append({"index": i, "title": rec.get("title", "?")[:50], "error": str(e)})
            print(f"  ⚠ 第{i+1}条失败: {rec.get('title','?')[:50]} → {e}")

    elapsed = time.time() - start
    print(f"\n✅ 迁移完成: {success}/{len(records)} 条成功, {len(errors)} 条失败")
    print(f"⏱ 耗时: {elapsed:.1f} 秒 ({elapsed/len(records)*1000:.0f} ms/条)")

    if errors:
        print(f"\n❌ 失败详情:")
        for e in errors[:10]:
            print(f"  [{e['index']+1}] {e['title']}: {e['error'][:80]}")

    if not dry_run:
        stats = repo.stats()
        print(f"\n📊 迁移后统计:")
        print(f"  总记录: {stats.total}")
        print(f"  总死亡: {stats.total_deaths}")
        print(f"  总受伤: {stats.total_injuries}")


def convert_record(rec: dict) -> dict:
    """将 JSON 记录转换为符合 Pydantic Accident 构造的 dict。

    处理类型转换:
      - date: "" → None, str → str
      - building_completion_year: "" → "", str → str (Pydantic 接受 str)
      - deaths/injuries/trapped_count: 保证 int
      - direct_economic_loss: null/"" → None
      - hfacs_codes: 可能是 list 或 JSON str
    """
    converted = dict(rec)

    # deaths/injuries → int
    for key in ("deaths", "injuries", "trapped_count"):
        val = converted.get(key)
        if val == "" or val is None:
            converted[key] = 0
        else:
            try:
                converted[key] = int(val)
            except (ValueError, TypeError):
                converted[key] = 0

    # direct_economic_loss → float or None
    loss = converted.get("direct_economic_loss")
    if loss == "" or loss is None or loss == 0:
        converted["direct_economic_loss"] = None
    else:
        try:
            converted["direct_economic_loss"] = float(loss)
        except (ValueError, TypeError):
            converted["direct_economic_loss"] = None

    # date: "" → 保持为 ""（Pydantic 处理 None）
    if converted.get("date") == "":
        converted["date"] = None

    # building_completion_year
    year = converted.get("building_completion_year")
    if year == "" or year is None:
        converted["building_completion_year"] = ""  # Pydantic str 字段

    # hfacs_codes: JSON str → list
    codes = converted.get("hfacs_codes", [])
    if isinstance(codes, str):
        try:
            converted["hfacs_codes"] = json.loads(codes)
        except (json.JSONDecodeError, TypeError):
            converted["hfacs_codes"] = []

    # hfacs_details: 确保是 list[dict]，空值补为 []
    details = converted.get("hfacs_details", [])
    if not details or not isinstance(details, list):
        converted["hfacs_details"] = []

    # human/machine/management/environmental_factors: 确保是 list[dict]，空值补为 []
    for dim in [
        "human_factors", "machine_factors",
        "management_factors", "environmental_factors",
    ]:
        factors = converted.get(dim, [])
        if not factors or not isinstance(factors, list):
            converted[dim] = []

    # search_snippets: 保持 list[dict]
    snippets = converted.get("search_snippets", [])
    if isinstance(snippets, str):
        try:
            converted["search_snippets"] = json.loads(snippets)
        except (json.JSONDecodeError, TypeError):
            converted["search_snippets"] = []

    return converted


def compare_stats(json_path: str, repo: SqliteAccidentRepository):
    """对比 JSON 文件和 SQLite 数据库的统计。"""
    records = load_json(json_path)
    stats = repo.stats()

    # JSON 统计
    json_total = len(records)
    json_deaths = sum(r.get("deaths", 0) or 0 for r in records)
    json_injuries = sum(r.get("injuries", 0) or 0 for r in records)

    json_by_type = Counter(r.get("accident_type", "未知") for r in records)
    json_by_severity = Counter(r.get("severity", "未知") for r in records)

    print(f"\n📊 统计对比:")
    print(f"  {'指标':<20} {'JSON':>8} {'SQLite':>8} {'状态':>6}")
    print(f"  {'-'*42}")
    ok = "✅" if json_total == stats.total else "❌"
    print(f"  {'总记录':<20} {json_total:>8} {stats.total:>8} {ok:>6}")

    ok = "✅" if json_deaths == stats.total_deaths else "❌"
    print(f"  {'总死亡':<20} {json_deaths:>8} {stats.total_deaths:>8} {ok:>6}")

    ok = "✅" if json_injuries == stats.total_injuries else "❌"
    print(f"  {'总受伤':<20} {json_injuries:>8} {stats.total_injuries:>8} {ok:>6}")

    # 按类型对比
    print(f"\n  按事故类型:")
    all_types = set(list(json_by_type.keys()) + list(stats.by_type.keys()))
    for t in sorted(all_types):
        jc = json_by_type.get(t, 0)
        sc = stats.by_type.get(t, 0)
        ok = "✅" if jc == sc else f"❌ (Δ={jc-sc})"
        if jc != sc:
            print(f"    {t:<15} JSON={jc:>4}  SQLite={sc:>4}  {ok}")


def main():
    parser = argparse.ArgumentParser(description="JSON → SQLite 数据迁移")
    parser.add_argument("--source", default=JSON_PATH, help="JSON 源文件路径")
    parser.add_argument("--db-url", default=DB_URL, help="SQLite 数据库 URL")
    parser.add_argument("--dry-run", action="store_true", help="仅试运行，不实际写入")
    parser.add_argument("--stats", action="store_true", help="迁移后对比统计")
    parser.add_argument("--reset", action="store_true", help="先清空数据库再迁移")
    args = parser.parse_args()

    repo = SqliteAccidentRepository(args.db_url)
    repo.initialize()

    if args.reset:
        repo.clear()
        print("🗑 数据库已清空")

    if args.stats:
        compare_stats(args.source, repo)
        return

    records = load_json(args.source)
    migrate(repo, records, dry_run=args.dry_run)

    # 自动对比
    compare_stats(args.source, repo)


if __name__ == "__main__":
    main()
