"""HFACS-CS: Human Factors Analysis and Classification System for Construction Safety.

建筑施工事故人因分析与分类体系。
基于 440 条实际数据构建，支持：
- 加载分类本体（taxonomy）
- 将现有致因文本映射到 HFACS-CS 类别
- 统计各层级/类别的数据覆盖
- 生成映射报告
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


# ── 分类本体 ──

class HfacsCategory:
    """单个 HFACS-CS 类别。"""
    def __init__(self, code: str, name_cn: str, keywords: list[str]):
        self.code = code
        self.name_cn = name_cn
        self.keywords = [kw.lower() for kw in keywords]

    def match(self, text: str) -> bool:
        """判断文本是否匹配本类别（基于关键词）。"""
        text_lower = text.lower()
        # 检查关键词覆盖率
        matched_kws = [kw for kw in self.keywords if kw in text_lower]
        # 至少匹配一个关键词，且匹配的关键词占总字符比例合理
        return len(matched_kws) >= 1

    def __repr__(self):
        return f"{self.code}:{self.name_cn}"


class HfacsLevel:
    """单个 HFACS-CS 层级。"""
    def __init__(self, level: int, name_cn: str, categories: list[HfacsCategory]):
        self.level = level
        self.name_cn = name_cn
        self.categories = {cat.code: cat for cat in categories}

    def classify(self, text: str) -> list[HfacsCategory]:
        """对文本进行分类，返回匹配的类别列表。"""
        matched = []
        for cat in self.categories.values():
            if cat.match(text):
                matched.append(cat)
        return matched


class HfacsTaxonomy:
    """HFACS-CS 完整分类本体。"""

    def __init__(self, filepath: str | Path = "data/hfacs_cs_taxonomy.json"):
        self.filepath = Path(filepath)
        self.levels: list[HfacsLevel] = []
        self._loaded = False

    def load(self) -> bool:
        """加载分类本体。"""
        if not self.filepath.exists():
            return False
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        for ldata in data["levels"]:
            categories = []
            for cdata in ldata["subcategories"]:
                categories.append(HfacsCategory(
                    code=cdata["code"],
                    name_cn=cdata["name_cn"],
                    keywords=cdata["keywords"],
                ))
            self.levels.append(HfacsLevel(
                level=ldata["level"],
                name_cn=ldata["name_cn"],
                categories=categories,
            ))
        self._loaded = True
        return True

    def classify(self, category_text: str, description_text: str = "") -> list[tuple[str, str, str]]:
        """对一段致因文本进行完整的 HFACS-CS 分类。

        Args:
            category_text: 当前提取的"类别"字段（如"监管缺失"）
            description_text: 具体描述文本

        Returns:
            [(hfacs_code, level_name_cn, category_name_cn), ...]
        """
        if not self._loaded:
            self.load()

        combined = f"{category_text} {description_text}"
        results = []

        for level in self.levels:
            matched = level.classify(combined)
            for cat in matched:
                results.append((cat.code, level.name_cn, cat.name_cn))

        return results

    def get_data_coverage(self) -> dict:
        """获取本体中记录的数据覆盖信息。"""
        if not self._loaded:
            self.load()
            if not self._loaded:
                return {}
        with open(self.filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("data_coverage", {})

    def get_level_stats(self) -> list[dict]:
        """统计各层级类别数量。"""
        if not self._loaded:
            self.load()
        stats = []
        for level in self.levels:
            stats.append({
                "level": level.level,
                "name_cn": level.name_cn,
                "category_count": len(level.categories),
                "categories": list(level.categories.keys()),
            })
        return stats


# ── 数据映射 ──

def map_v1_to_hfacs(records: list[dict], taxonomy: Optional[HfacsTaxonomy] = None) -> dict:
    """将 V1 数据集的致因字段映射到 HFACS-CS。

    Args:
        records: V1 数据集记录列表
        taxonomy: HFACS-CS 本体实例

    Returns:
        映射统计字典
    """
    if taxonomy is None:
        taxonomy = HfacsTaxonomy()
        taxonomy.load()

    # 统计：每层的匹配次数
    level_counts: dict[int, int] = {l.level: 0 for l in taxonomy.levels}
    category_counts: dict[str, int] = {}
    code_map: dict[str, list[str]] = {}  # v1_category -> [hfacs_codes]
    mapped_records = []

    for r in records:
        record_mapping = {
            "id": r.get("id", ""),
            "title": (r.get("title", "") or "")[:40],
            "hfacs_codes": [],
        }
        for v1_field in ["human_factors", "machine_factors", "management_factors", "environmental_factors"]:
            factors = r.get(v1_field) or []
            if isinstance(factors, list):
                for f in factors:
                    if isinstance(f, dict):
                        cat = f.get("category", "").strip()
                        desc = f.get("description", "").strip()
                        if cat:
                            hfacs_results = taxonomy.classify(cat, desc)
                            for code, lname, cname in hfacs_results:
                                category_counts[code] = category_counts.get(code, 0) + 1
                                level_counts.get(int(code[1]) if code[1].isdigit() else 0, 0)
                                # 修正：从 level number
                                level_num = int(code[0]) if code[0].isdigit() else {
                                    "O": 1, "S": 2, "P": 3, "A": 4, "W": 5, "E": 6
                                }.get(code[0], 0)
                                if level_num > 0:
                                    level_counts[level_num] = level_counts.get(level_num, 0) + 1
                                record_mapping["hfacs_codes"].append(code)
                                if cat not in code_map:
                                    code_map[cat] = []
                                if code not in code_map[cat]:
                                    code_map[cat].append(code)
        mapped_records.append(record_mapping)

    return {
        "total_records": len(records),
        "records_with_causes": len([r for r in mapped_records if r["hfacs_codes"]]),
        "level_counts": dict(sorted(level_counts.items())),
        "category_counts": dict(sorted(category_counts.items(), key=lambda x: -x[1])),
        "v1_to_hfacs_mapping": {
            cat: codes for cat, codes in sorted(code_map.items())
        },
    }


def generate_mapping_report(records: list[dict], output_path: str = "data/hfacs_cs_mapping_report.md") -> str:
    """生成 V1 → HFACS-CS 映射报告。"""
    taxonomy = HfacsTaxonomy()
    taxonomy.load()
    result = map_v1_to_hfacs(records, taxonomy)

    lines = [
        "# V1 → HFACS-CS 致因映射报告",
        "",
        f"**数据集**: {result['total_records']} 条事故记录",
        f"**含致因的记录**: {result['records_with_causes']} 条",
        f"**生成日期**: 2025-06-18",
        "",
        "---",
        "",
        "## 各层级匹配统计",
        "",
        "| Level | 层级 | 匹配次数 |",
        "|-------|------|---------|",
    ]

    level_names = {
        1: "组织因素", 2: "不安全监督", 3: "不安全行为的前提条件",
        4: "不安全行为", 5: "施工现场条件", 6: "物理环境"
    }
    for lvl in sorted(result["level_counts"].keys()):
        name = level_names.get(lvl, "")
        lines.append(f"| Level {lvl} | {name} | {result['level_counts'][lvl]} |")

    lines.extend([
        "",
        "## 子类别匹配统计",
        "",
        "| 编码 | 类别 | 匹配次数 |",
        "|------|------|---------|",
    ])

    for code, count in result["category_counts"].items():
        lines.append(f"| {code} | — | {count} |")

    lines.extend([
        "",
        "## V1 四维度 → HFACS 六层映射表",
        "",
        "| V1 类别 | 映射到 HFACS 编码 |",
        "|---------|-----------------|",
    ])

    for cat, codes in result["v1_to_hfacs_mapping"].items():
        lines.append(f"| {cat} | {', '.join(codes)} |")

    report = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report)

    return report
