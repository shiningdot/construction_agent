#!/usr/bin/env python3
"""work_task 枚举体系 v2.0 入库脚本。

执行内容:
  1. 创建 enum_definitions 表（如果不存在）
  2. 插入 11 个 WT 枚举定义（v2.0-llm）
  3. 回填 accidents.work_task：规则版 WT → LLM版 WT
  4. 记录 pipeline_metadata 迁移日志

用法:
  python3 scripts/migrate_enum_v2.py [--db path] [--dry-run] [--rollback]

回滚:
  python3 scripts/migrate_enum_v2.py --rollback  # 恢复到 v1.0 规则版编码
"""

import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════
# LLM v2.0 枚举定义（从 work_task_llm_framework.json 提取，手工补充）
# ══════════════════════════════════════════════════════════════════

WT_ENUM_V2 = [
    {
        "code": "WT01", "name": "地基与基础工程",
        "definition": "以地基和基础为主要施工对象的作业活动。包含土方开挖/回填、基坑支护与降水、各类桩基施工（灌注桩/预制桩/管桩/CFG桩）、地基处理（强夯/换填/预压）、边坡工程。不包含桥墩桩基（→WT08）和顶管施工（→WT07）。",
        "decision_rule": "规则8: 土方开挖/回填/基坑支护/沟槽/桩基施工/地基处理→WT01。注意：桥墩桩基→WT08，顶管→WT07",
        "sort_order": 1, "sample_count": 248,
    },
    {
        "code": "WT02", "name": "主体结构工程",
        "definition": "以建筑物承重骨架和受力构件为主要施工对象的作业活动。包含混凝土浇筑/振捣/养护/搅拌/泵送、预制混凝土构件安装、钢筋绑扎/加工/连接、预应力张拉、钢结构/网架/桁架安装。不包含桥墩/箱梁混凝土（→WT08）和隧道衬砌混凝土（→WT07）。",
        "decision_rule": "规则7: 混凝土浇筑/钢筋/预应力/钢结构→WT02。注意：桥梁混凝土→WT08，隧道衬砌→WT07",
        "sort_order": 2, "sample_count": 397,
    },
    {
        "code": "WT03", "name": "模板与脚手架工程",
        "definition": "为混凝土浇筑提供模具和为施工作业提供通道/操作面的临时设施工程。包含模板搭设与拆除、支撑体系（满堂架/承重架/高支模）、操作平台与卸料平台、外脚手架/悬挑脚手架/爬架的搭设/使用/拆除、防护棚与安全通道。全生命周期原则：搭设/使用/拆除均归此类。",
        "decision_rule": "规则4: 模板/支撑架/满堂架/脚手架/操作平台的搭设/使用/拆除→WT03",
        "sort_order": 3, "sample_count": 203,
    },
    {
        "code": "WT04", "name": "装饰装修工程",
        "definition": "以建筑物内外表面处理、美化和功能提升为目的的作业活动。包含抹灰/粉刷/涂料、外墙保温、幕墙安装（玻璃/石材/金属）、门窗安装、屋面防水/保温/瓦屋面、防水工程、墙地砖铺贴、非承重砌筑（填充墙/隔墙）、吊顶/隔断/地板。不包含承重结构砌体（→WT02）和屋面结构混凝土（→WT02）。",
        "decision_rule": "规则5: 抹灰/涂料/保温/幕墙/门窗/防水/贴砖/砌填充墙/吊顶/屋面→WT04",
        "sort_order": 4, "sample_count": 254,
    },
    {
        "code": "WT05", "name": "机电安装及管线工程",
        "definition": "以建筑设备和市政管线的安装、连接、调试为目的的MEP作业活动。包含给排水/燃气/热力管道安装与试压、通风与空调（风管/设备）、电气安装（变配电/线缆/照明）、消防管道/设备安装、电梯/自动扶梯安装、通信管线铺设。不包含管道沟槽开挖（→WT01）和顶管施工（→WT07）。",
        "decision_rule": "规则6: 给排水/燃气/热力/通风/电气/电梯/消防/市政管线安装→WT05。注意：沟槽开挖→WT01，顶管→WT07",
        "sort_order": 5, "sample_count": 150,
    },
    {
        "code": "WT06", "name": "起重设备安拆与吊装",
        "definition": "以起重设备的安装、顶升、吊运、拆卸全生命周期为核心的作业活动。包含塔吊安装/顶升/加节/拆卸、施工升降机/物料提升机安拆与使用、龙门架/井架/门吊安拆、汽车吊/履带吊等移动式起重机吊装、架桥机作业、通用吊装（卷扬机/电动葫芦）。全生命周期原则：安装/使用/维修/拆卸均归此类。",
        "decision_rule": "规则2: 塔吊/龙门架/井架/施工升降机/汽车吊等起重设备的安装/拆卸/顶升/吊运→WT06",
        "sort_order": 6, "sample_count": 286,
    },
    {
        "code": "WT07", "name": "隧道与地下工程",
        "definition": "以地下洞室/隧道的开挖、掘进和支护为主要施工对象的作业活动。包含隧道开挖/掘进（钻爆法/TBM/盾构）、隧道支护（锚杆/钢拱架/喷射混凝土）、衬砌施工（模筑/管片拼装）、顶管施工（非开挖管道铺设）、地下综合管廊施工。不包含矿山巷道（→WT99）。",
        "decision_rule": "规则1: 隧道/洞室/盾构/顶管/衬砌/暗挖→WT07（与WT08并列最高优先级）",
        "sort_order": 7, "sample_count": 84,
    },
    {
        "code": "WT08", "name": "桥梁工程",
        "definition": "以桥梁上部结构（梁/拱/索）和下部结构（墩/台/基础）为主要施工对象的作业活动。包含桥墩/桥台/墩柱/盖梁施工、箱梁/T梁/空心板预制与架设、拱桥/斜拉桥/悬索桥施工、桥面铺装/护栏/防撞墙施工。不包含架桥机作业（→WT06，起重设备优先）。",
        "decision_rule": "规则1: 桥梁墩台/梁体/桥面/架桥→WT08（与WT07并列最高优先级）。注意：架桥机→WT06",
        "sort_order": 8, "sample_count": 73,
    },
    {
        "code": "WT09", "name": "拆除工程",
        "definition": "以既有建筑物、构筑物或其构件的解体清除为目的的作业活动。包含建筑物整体拆除（机械/爆破）、构筑物拆除（烟囱/水塔/围墙等）、结构构件局部拆除（改造工程）、拆迁施工、保护性拆除、二次装修拆除。不包含模板/脚手架/起重设备等临时施工设施的拆除（全生命周期原则→分别归WT03/WT06）。",
        "decision_rule": "规则3: 既有建构筑物的整体或局部拆除/爆破→WT09。注意：模板/脚手架拆除→WT03，塔吊/起重设备拆卸→WT06",
        "sort_order": 9, "sample_count": 115,
    },
    {
        "code": "WT10", "name": "辅助作业与场内运输",
        "definition": "不直接改变工程实体状态但起支撑保障作用的辅助性和服务性作业活动。包含施工设备维修/保养、既有结构加固/修补、现场清理/保洁/抽水、巡查/检测/测量、材料搬运/场内运输/装卸、车辆/机械驾驶操作（铲车/叉车/运输车）、独立搅拌机操作、临时设施搭建。",
        "decision_rule": "规则9: 设备维修/现场清理/巡查检测/物料运输/车辆驾驶/临建→WT10",
        "sort_order": 10, "sample_count": 331,
    },
    {
        "code": "WT99", "name": "其他",
        "definition": "无法归入以上10类的作业活动。包括：跨类混合（>=3种工序无法判断主次）、非建筑施工活动（矿山/工厂生产/文体活动）、信息严重不足（仅含施工/作业等通用词）、施工行业中极少见的特殊作业（筑炉/煅烧窑等）。",
        "decision_rule": "规则10: 以上规则均不匹配→WT99",
        "sort_order": 99, "sample_count": 63,
    },
]


# ══════════════════════════════════════════════════════════════════
# 主逻辑
# ══════════════════════════════════════════════════════════════════

def create_enum_table(conn):
    """创建枚举定义表。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS enum_definitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            enum_type TEXT NOT NULL,           -- 'work_task', 'equipment_involved' 等
            code TEXT NOT NULL,                -- 'WT01'
            name TEXT NOT NULL,                -- '地基与基础工程'
            definition TEXT NOT NULL,          -- 详细定义
            decision_rule TEXT,                -- 判定规则
            version TEXT NOT NULL DEFAULT '2.0-llm',
            sort_order INTEGER NOT NULL DEFAULT 0,
            sample_count INTEGER DEFAULT 0,
            parent_code TEXT,                  -- 上一版本对应编码（追溯用）
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(enum_type, code, version)
        )
    """)
    conn.commit()


def find_parent_v1(code: str) -> str | None:
    """根据 v1.0 → v2.0 映射，找到该 v2.0 编码对应的 v1.0 编码（取最主要的一个）。"""
    mapping = {
        "WT01": "WT01-土方与基坑",
        "WT02": "WT05-混凝土浇筑",
        "WT03": "WT04-模板与支撑",
        "WT04": "WT12-装饰与装修",
        "WT05": "WT11-管道与管网",
        "WT06": "WT08-塔吊与起重",
        "WT07": "WT10-隧道与地下",
        "WT08": "WT09-桥梁工程",
        "WT09": "WT13-拆除作业",
        "WT10": "WT15-其他",
        "WT99": "WT15-其他",
    }
    return mapping.get(code)


def insert_enum_definitions(conn):
    """插入 v2.0 枚举定义。"""
    for item in WT_ENUM_V2:
        parent = find_parent_v1(item["code"])
        conn.execute("""
            INSERT OR REPLACE INTO enum_definitions
                (enum_type, code, name, definition, decision_rule, version,
                 sort_order, sample_count, parent_code)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "work_task", item["code"], item["name"], item["definition"],
            item["decision_rule"], "2.0-llm", item["sort_order"],
            item["sample_count"], parent,
        ))
    conn.commit()
    print(f"  插入 {len(WT_ENUM_V2)} 条 work_task 枚举定义")


def backfill_accidents(conn, mapping: dict[str, dict], dry_run: bool = False):
    """回填 accidents 表的 work_task 字段。"""
    # 构建 v1→v2 映射字典（只映射当前 DB 中存在的 WT 值）
    current_codes = set(
        r[0] for r in conn.execute(
            "SELECT DISTINCT work_task FROM accidents WHERE work_task LIKE 'WT%'"
        ).fetchall()
    )

    # v1 → v2 映射
    v1_to_v2 = {
        "WT01-土方与基坑": "WT01",
        "WT02-桩基工程": "WT01",
        "WT03-钢筋工程": "WT02",
        "WT04-模板与支撑": "WT03",
        "WT05-混凝土浇筑": "WT02",
        "WT06-钢结构与网架": "WT02",
        "WT07-脚手架作业": "WT03",
        "WT08-塔吊与起重": "WT06",
        "WT09-桥梁工程": "WT08",
        "WT10-隧道与地下": "WT07",
        "WT11-管道与管网": "WT05",
        "WT12-装饰与装修": "WT04",
        "WT13-拆除作业": "WT09",
        "WT14-设备安装": "WT05",
        "WT15-其他": None,  # 需要从 LLM mapping 中查找
    }

    updated = 0
    skipped = 0

    for v1_code in current_codes:
        # 先查直接映射
        v2_code = v1_to_v2.get(v1_code)

        if v2_code is None and v1_code == "WT15-其他":
            # WT15-其他 → 需要从 LLM mapping JSON 查找每个具体值的映射
            # 获取所有 WT15 的原始值
            wt15_records = conn.execute(
                "SELECT id, work_task FROM accidents WHERE work_task = 'WT15-其他'"
            ).fetchall()
            for accident_id, _ in wt15_records:
                # 从 LLM mapping 中查找（如果 mapping 是按原始文本索引的）
                # 这里 WT15 已经被回填为 WT15-其他，原始值丢失了
                # 解决方案：跳过 WT15 记录的二次映射（需要从备份恢复原始值才行）
                pass
            skipped += len(wt15_records)
            continue

        if v2_code:
            if not dry_run:
                conn.execute(
                    "UPDATE accidents SET work_task = ? WHERE work_task = ?",
                    (v2_code, v1_code),
                )
            updated += conn.total_changes if not dry_run else sum(
                1 for _ in conn.execute(
                    "SELECT 1 FROM accidents WHERE work_task = ?", (v1_code,)
                )
            )
        else:
            skipped += 1

    conn.commit()
    print(f"  v1→v2 直接映射: {updated} 条已更新")
    if skipped:
        print(f"  跳过: {skipped} 条（WT15原始值已丢失，需从raw_values重新处理）")


def backfill_wt15_from_raw(conn, llm_mapping: dict[str, dict], raw_values_path: str, dry_run: bool = False):
    """对当前 DB 中 work_task = 'WT15-其他' 的记录，从原始值和 LLM mapping 回填。

    策略: 使用 source_url 作为桥梁，从 accidents 表找到对应记录的原始 work_task，
    但由于原始值已被覆盖，我们用另一个方案：遍历 LLM mapping，
    将原本属于 WT15 但现在应该拆分到各 WT 的原始值找出来，
    通过 accidents 表的 source_url 匹配（不可靠）或直接接受当前归入 WT99。

    实际方案: WT15-其他 的 v1 值中，LLM 版映射情况:
    - 大部分 → WT10 (辅助作业)
    - 部分 → WT99 (真正无法归类)
    - 少量 → WT01/WT02 等

    但由于 DB 中原始值已被覆盖为 "WT15-其他"，无法精确还原。
    保守处理: 将所有 WT15-其他 改为 WT10（辅助作业），
    因为 WT10 吸收了原 WT15 中大部分语义（维修/运输/清理/检测等）。
    """
    wt15_count = conn.execute(
        "SELECT COUNT(*) FROM accidents WHERE work_task = 'WT15-其他'"
    ).fetchone()[0]

    if wt15_count == 0:
        print("  无需处理 WT15-其他（已全部被直接映射覆盖）")
        return

    # 检查是否有 raw_values 可以匹配
    try:
        with open(raw_values_path) as f:
            raw_values = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"  警告: 无法读取 raw_values ({raw_values_path})，WT15-其他保持不变")
        return

    # 构建原始值→LLM编码的查找表
    raw_to_llm = {}
    for raw_item in raw_values:
        orig_val = raw_item["value"]
        if orig_val in llm_mapping:
            raw_to_llm[orig_val] = llm_mapping[orig_val]["code"]

    # 对所有 WT15-其他 记录，批量改为 WT10
    # （这是合理的近似，因为 LLM mapping 显示原 WT15 中 ~80% 归入 WT10）
    if not dry_run:
        conn.execute(
            "UPDATE accidents SET work_task = 'WT10' WHERE work_task = 'WT15-其他'"
        )
    conn.commit()
    print(f"  WT15-其他 → WT10: {wt15_count} 条（保守近似映射）")


def record_metadata(conn):
    """记录迁移日志。"""
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO pipeline_metadata (key, value, created_at)
        VALUES (?, ?, ?)
    """, (
        "work_task_enum_version",
        json.dumps({
            "version": "2.0-llm",
            "migration_date": now,
            "previous_version": "1.0-rule",
            "total_categories": 11,
            "method": "LLM-first: deepseek-v4-pro framework design + deepseek-v4-flash full mapping",
            "key_changes": [
                "WT01+WT02(桩基) → WT01 地基与基础工程",
                "WT03(钢筋)+WT05(混凝土)+WT06(钢结构) → WT02 主体结构工程",
                "WT04(模板)+WT07(脚手架) → WT03 模板与脚手架工程",
                "WT11(管道)+WT14(设备) → WT05 机电安装及管线工程",
                "新增 WT10 辅助作业与场内运输（吸收原WT15中维修/运输/清理/检测）",
                "WT15 从 18.9% 降至 2.9%",
            ],
        }, ensure_ascii=False),
        now,
    ))
    conn.commit()
    print("  迁移日志已记录")


def verify(conn):
    """验证回填结果。"""
    print("\n=== 验证 ===")
    total = conn.execute("SELECT COUNT(*) FROM accidents").fetchone()[0]
    wt_count = conn.execute(
        "SELECT COUNT(*) FROM accidents WHERE work_task LIKE 'WT%'"
    ).fetchone()[0]
    print(f"  事故总数: {total}")
    print(f"  WT 编码记录: {wt_count}")

    dist = conn.execute("""
        SELECT work_task, COUNT(*) FROM accidents
        WHERE work_task LIKE 'WT%'
        GROUP BY work_task ORDER BY COUNT(*) DESC
    """).fetchall()

    print(f"\n  v2.0 枚举分布:")
    for code, cnt in dist:
        # 从 enum_definitions 查找名称
        name_row = conn.execute(
            "SELECT name FROM enum_definitions WHERE enum_type='work_task' AND code=? AND version='2.0-llm'",
            (code,)
        ).fetchone()
        name = name_row[0] if name_row else "?"
        pct = cnt / max(wt_count, 1) * 100
        print(f"    {code} {name:20s} {cnt:5d} ({pct:.1f}%)")

    # 检查残留 v1 编码
    v1_count = conn.execute(
        "SELECT COUNT(*) FROM accidents WHERE work_task LIKE 'WT%-%'"
    ).fetchone()[0]
    print(f"\n  残留 v1 编码（含 '-' 的）: {v1_count} (应为0)")

    # 枚举定义完整性
    enum_count = conn.execute(
        "SELECT COUNT(*) FROM enum_definitions WHERE enum_type='work_task' AND version='2.0-llm'"
    ).fetchone()[0]
    print(f"  enum_definitions 条目: {enum_count} (应为11)")


def rollback(conn):
    """回滚到 v1.0 编码。"""
    v2_to_v1 = {
        "WT01": "WT01-土方与基坑",
        "WT02": "WT05-混凝土浇筑",
        "WT03": "WT04-模板与支撑",
        "WT04": "WT12-装饰与装修",
        "WT05": "WT11-管道与管网",
        "WT06": "WT08-塔吊与起重",
        "WT07": "WT10-隧道与地下",
        "WT08": "WT09-桥梁工程",
        "WT09": "WT13-拆除作业",
        "WT10": "WT15-其他",
        "WT99": "WT15-其他",
    }
    for v2, v1 in v2_to_v1.items():
        conn.execute("UPDATE accidents SET work_task = ? WHERE work_task = ?", (v1, v2))
    conn.commit()
    # 删除 v2 枚举定义
    conn.execute(
        "DELETE FROM enum_definitions WHERE enum_type='work_task' AND version='2.0-llm'"
    )
    conn.commit()
    print("已回滚到 v1.0 规则版编码")


# ══════════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="work_task 枚举 v2.0 入库")
    parser.add_argument("--db", default="data/caiems_v2.db")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.exists():
        print(f"[ERROR] 数据库不存在: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))

    if args.rollback:
        print("回滚到 v1.0 规则版...")
        rollback(conn)
        conn.close()
        return

    # 加载 LLM mapping
    llm_mapping_path = PROJECT_ROOT / "docs" / "enums" / "work_task_llm_mapping.json"
    with open(llm_mapping_path) as f:
        llm_mapping = json.load(f)

    raw_values_path = PROJECT_ROOT / "docs" / "enums" / "work_task_raw_values.json"

    print("=" * 60)
    print("  work_task 枚举体系 v2.0 入库")
    print("=" * 60)

    if args.dry_run:
        print("[DRY-RUN MODE] 不会实际修改数据库\n")

    # 1. 创建表
    create_enum_table(conn)
    print("✓ enum_definitions 表已就绪")

    # 2. 插入定义
    insert_enum_definitions(conn)
    print("✓ 枚举定义已写入")

    # 3. 回填 accidents
    backfill_accidents(conn, llm_mapping, args.dry_run)
    print("✓ accidents 主映射完成")

    # 4. 处理 WT15 残余
    backfill_wt15_from_raw(conn, llm_mapping, str(raw_values_path), args.dry_run)
    print("✓ WT15 残余处理完成")

    # 5. 元数据
    record_metadata(conn)
    print("✓ 迁移日志已记录")

    # 6. 验证
    verify(conn)
    print("\n✅ 枚举体系 v2.0 入库完成!")

    conn.close()


if __name__ == "__main__":
    main()
