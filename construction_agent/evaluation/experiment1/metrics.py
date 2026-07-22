"""
实验一指标计算（优化版）

核心字段（聚焦模型真实理解能力）：
- accident_type, severity, deaths, injuries, location, city
- 致因四维度 F1

规范化处理：省/市后缀归一化、数值容忍度。
"""

import json
import os
from collections import defaultdict
from typing import Dict, List, Optional

# ========================================================
# 核心评测字段（去除了格式噪声大、依赖原文是否提及的字段）
# ========================================================

CORE_FIELDS = [
    {"field": "accident_type", "label": "事故类型", "type": "categorical"},
    {"field": "severity", "label": "事故等级", "type": "categorical"},
    {"field": "deaths", "label": "死亡人数", "type": "numeric"},
    {"field": "injuries", "label": "受伤人数", "type": "numeric"},
    {"field": "location", "label": "省级地点", "type": "location"},
    {"field": "city", "label": "地级市", "type": "city"},
]

CAUSE_DIMS = ["human_factors", "machine_factors", "management_factors", "environmental_factors"]


def _normalize_location(v: Optional[str]) -> Optional[str]:
    """地点归一化：去掉省/市后缀"""
    if v is None:
        return None
    v = v.strip()
    for suffix in ["省", "市", "自治区", "壮族", "回族", "维吾尔"]:
        if suffix in v and len(v) > 2:
            v = v.replace("省", "").replace("市", "")
            break
    # 特殊处理：广西/宁夏/新疆/内蒙古/西藏
    v = v.replace("广西壮族", "广西").replace("宁夏回族", "宁夏")
    v = v.replace("新疆维吾尔", "新疆").replace("内蒙古", "内蒙古")
    v = v.replace("西藏", "西藏")
    return v


def _get(pred: Dict, field: str):
    v = pred.get(field) if pred else None
    if v is None or (isinstance(v, str) and v.strip().lower() in ("", "null", "none")):
        return None
    return v


def _field_match(pv, gv, field: str) -> bool:
    """智能字段匹配（容忍合理的格式差异）"""
    if pv is None and gv is None:
        return True

    if field == "location":
        # 省名容忍：广东 == 广东省
        return _normalize_location(pv) == _normalize_location(gv)

    if field == "city":
        # 市名容忍：汕尾 == 汕尾市、深圳 == 深圳市
        # 县级市也接受：华蓥市如果GT是广安市，只要都在四川省
        # 更简单的方案：去掉"市"后缀比较
        pn = str(pv).replace("市", "").strip().lower() if pv else ""
        gn = str(gv).replace("市", "").strip().lower() if gv else ""
        return pn == gn

    # 数值字段：容忍±1（处理"失踪"算不算死亡的问题）
    if field in ("deaths", "injuries"):
        try:
            pn = int(float(pv)) if pv is not None else -1
            gn = int(float(gv)) if gv is not None else -1
        except (ValueError, TypeError):
            return False
        if pn < 0 and gn < 0:
            return True  # 两者都无
        if pn < 0 or gn < 0:
            return False
        # 核心：死亡人数容忍±1（失踪 vs 死亡争议）
        return abs(pn - gn) <= 1

    # 分类字段：精确匹配
    if isinstance(pv, str) and isinstance(gv, str):
        return pv.strip().lower() == gv.strip().lower()
    # 数值比较
    try:
        return float(pv) == float(gv)
    except (ValueError, TypeError):
        return str(pv).strip().lower() == str(gv).strip().lower()


def compute_all_metrics(predictions: List[Dict], ground_truth: List[Dict]) -> Dict:
    """计算所有评测指标"""
    n = len(predictions)
    if n == 0:
        return {"basic_fields": {}, "cause_dim_f1": {}, "cause_category_match": {}, "overall_score": 0}

    # ================================================
    # A. 核心基础字段
    # ================================================
    basic = {}
    for fc in CORE_FIELDS:
        field = fc["field"]
        correct = 0
        for p, g in zip(predictions, ground_truth):
            pv = _get(p, field)
            gv = _get(g, field)
            if _field_match(pv, gv, field):
                correct += 1
        acc = round(correct / n * 100, 1)
        basic[field] = {"accuracy": acc}

    basic["avg_accuracy"] = round(sum(v["accuracy"] for v in basic.values()) / len(basic), 1)

    # ================================================
    # B1. 致因二元 F1（有/无致因提取）
    # ================================================
    cause_bin = {}
    for dim in CAUSE_DIMS:
        tp = fp = fn = 0
        for p, g in zip(predictions, ground_truth):
            p_list = _get(p, dim) or []
            g_list = _get(g, dim) or []
            has_p = bool(p_list) and isinstance(p_list, list) and len(p_list) > 0
            has_g = bool(g_list) and isinstance(g_list, list) and len(g_list) > 0
            if has_p and has_g:
                tp += 1
            elif has_p and not has_g:
                fp += 1
            elif not has_p and has_g:
                fn += 1
        prec = tp / (tp + fp) if (tp + fp) > 0 else (1.0 if tp > 0 else 0.0)
        rec = tp / (tp + fn) if (tp + fn) > 0 else (1.0 if tp > 0 else 0.0)
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        cause_bin[dim] = {
            "precision": round(prec * 100, 1),
            "recall": round(rec * 100, 1),
            "f1": round(f1 * 100, 1),
            "tp": tp, "fp": fp, "fn": fn,
        }
    cause_bin["avg"] = {
        "f1": round(sum(v["f1"] for k, v in cause_bin.items() if k != "avg") / len(CAUSE_DIMS), 1),
        "precision": round(sum(v["precision"] for k, v in cause_bin.items() if k != "avg") / len(CAUSE_DIMS), 1),
        "recall": round(sum(v["recall"] for k, v in cause_bin.items() if k != "avg") / len(CAUSE_DIMS), 1),
    }

    # ================================================
    # B2. 致因类目匹配 F1（category 级别）
    # ================================================
    cause_cat = {}
    for dim in CAUSE_DIMS:
        cat_tp = cat_fp = cat_fn = 0
        for p, g in zip(predictions, ground_truth):
            p_list = _get(p, dim) or []
            g_list = _get(g, dim) or []
            p_cats = {str(i.get("category", "")).strip().lower() for i in p_list if isinstance(i, dict)}
            g_cats = {str(i.get("category", "")).strip().lower() for i in g_list if isinstance(i, dict)}
            cat_tp += len(p_cats & g_cats)
            cat_fp += len(p_cats - g_cats)
            cat_fn += len(g_cats - p_cats)
        prec = cat_tp / (cat_tp + cat_fp) if (cat_tp + cat_fp) > 0 else 0.0
        rec = cat_tp / (cat_tp + cat_fn) if (cat_tp + cat_fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        cause_cat[dim] = {"precision": round(prec * 100, 1), "recall": round(rec * 100, 1), "f1": round(f1 * 100, 1)}
    cause_cat["avg"] = {"f1": round(sum(v["f1"] for k, v in cause_cat.items() if k != "avg") / len(CAUSE_DIMS), 1)}

    # ================================================
    # 综合得分：核心字段（60%）+ 致因精确率（40%）
    # 致因提取优先精确率（Precision）：对于安全分析系统，
    # 误报（hallucinate 不存在的致因）比漏报更有害
    # ================================================
    overall = round(basic["avg_accuracy"] * 0.60 + cause_bin["avg"]["precision"] * 0.40, 1)

    return {
        "basic_fields": basic,
        "cause_dim_f1": cause_bin,
        "cause_category_match": cause_cat,
        "hfacs": compute_hfacs_metrics(predictions, ground_truth),
        "overall_score": overall,
    }


# ========================================================
# C. HFACS-CS 分类精度评估（新增）
# ========================================================

# 加载 HFACS-CS 分类体系
_HFACS_TAXONOMY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "data", "hfacs_cs_taxonomy.json"
)
_HFACS_LEVEL_MAP = {}
_HFACS_KEYWORD_MAP = {}

def _load_hfacs_taxonomy():
    """加载 HFACS-CS 分类体系，建立致因关键词到 HFACS 层级的映射"""
    global _HFACS_LEVEL_MAP, _HFACS_KEYWORD_MAP
    if _HFACS_LEVEL_MAP:
        return  # already loaded

    path = os.path.abspath(_HFACS_TAXONOMY_PATH)
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as f:
        taxonomy = json.load(f)

    # taxonomy 结构: {"levels": [{"level": 1, "code_prefix": "O", "name": "组织因素",
    #                             "categories": [{"code": "O1", "name": "安全文化", "keywords": [...]}]}]}
    levels = taxonomy if isinstance(taxonomy, list) else taxonomy.get("levels", [])

    for lvl in levels:
        level_num = lvl.get("level", 0)
        code_prefix = lvl.get("code_prefix", "")
        level_name = lvl.get("name", "")
        for cat in lvl.get("categories", []):
            code = cat.get("code", "")
            cat_name = cat.get("name_cn") or cat.get("name", "")
            keywords = cat.get("keywords", [])
            # 将关键词映射到 HFACS 编码
            for kw in keywords:
                kw_lower = kw.strip().lower()
                if kw_lower:
                    _HFACS_KEYWORD_MAP[kw_lower] = {
                        "code": code, "level": level_num,
                        "level_name": level_name, "category": cat_name,
                    }


def classify_cause_to_hfacs(cause_category: str, cause_description: str = "") -> List[str]:
    """
    将致因（category + description）映射到 HFACS-CS 编码列表。
    使用关键词匹配 + 维度推断。
    """
    _load_hfacs_taxonomy()
    if not _HFACS_KEYWORD_MAP:
        return []

    text = f"{cause_category or ''} {cause_description or ''}".strip().lower()
    matched_codes = set()

    for keyword, info in _HFACS_KEYWORD_MAP.items():
        if keyword in text:
            matched_codes.add(info["code"])

    return list(matched_codes)


def compute_hfacs_metrics(
    predictions: List[Dict],
    ground_truth: List[Dict],
) -> Dict[str, any]:
    """
    计算 HFACS-CS 层级分类精度。

    对每条记录，将模型提取的致因四维度的 category/description
    映射到 HFACS-CS 编码，与 GT 中的 hfacs_codes 比较。

    返回各层级的 Precision/Recall/F1。
    """
    _load_hfacs_taxonomy()

    # 收集 GT 中的 hfacs_codes
    gt_has_hfacs = any(g.get("hfacs_codes") for g in ground_truth if g)
    if not gt_has_hfacs:
        # 尝试从 cause 维度反向推断 HFACS 层级
        return _compute_hfacs_from_causes(predictions, ground_truth)

    # ---- 方法1：GT 有 hfacs_codes ----
    hfacs_levels = ["L1_组织", "L2_监督", "L3_前提", "L4_行为", "L5_现场", "L6_环境"]
    results = {}

    for lvl_key in hfacs_levels:
        tp = fp = fn = 0
        for pred, gt in zip(predictions, ground_truth):
            # 从模型输出中提取致因并映射到 HFACS
            pred_codes = set()
            for dim in CAUSE_DIMS:
                for item in (_get(pred, dim) or []):
                    if isinstance(item, dict):
                        codes = classify_cause_to_hfacs(
                            item.get("category", ""),
                            item.get("description", "")
                        )
                        pred_codes.update(codes)

            # GT 的 hfacs_codes
            gt_codes = set(gt.get("hfacs_codes", []) or [])

            # 确定哪些编码属于当前层级
            lvl_num = lvl_key.split("_")[0].replace("L", "")
            pred_has = any(c.startswith(lvl_num) or c.startswith(chr(64+int(lvl_num))) for c in pred_codes)
            gt_has = any(c.startswith(lvl_num) or c.startswith(chr(64+int(lvl_num))) for c in gt_codes)

            if pred_has and gt_has: tp += 1
            elif pred_has and not gt_has: fp += 1
            elif not pred_has and gt_has: fn += 1

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        results[lvl_key] = {
            "precision": round(prec * 100, 1),
            "recall": round(rec * 100, 1),
            "f1": round(f1 * 100, 1),
        }

    return results


def _compute_hfacs_from_causes(predictions, ground_truth):
    """
    方法2（备用）：当 GT 无 hfacs_codes 时，
    用"致因维度→HFACS层级"的对齐关系推算。

    维度-层级映射：
      human_factors → L3(Preconditions) + L4(Unsafe Acts)
      machine_factors → L5(Site Conditions)
      management_factors → L1(Organizational) + L2(Supervision)
      environmental_factors → L6(Physical Environment)
    """
    dim_to_levels = {
        "human_factors": ["L3_前提", "L4_行为"],
        "machine_factors": ["L5_现场"],
        "management_factors": ["L1_组织", "L2_监督"],
        "environmental_factors": ["L6_环境"],
    }

    hfacs_levels = ["L1_组织", "L2_监督", "L3_前提", "L4_行为", "L5_现场", "L6_环境"]
    results = {}

    for lvl_key in hfacs_levels:
        # 找到映射到该层级的维度
        mapped_dims = [dim for dim, levels in dim_to_levels.items() if lvl_key in levels]
        tp = fp = fn = 0

        for pred, gt in zip(predictions, ground_truth):
            for dim in mapped_dims:
                p_has = bool(_get(pred, dim))
                g_has = bool(_get(gt, dim))
                if p_has and g_has: tp += 1
                elif p_has and not g_has: fp += 1
                elif not p_has and g_has: fn += 1

        prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
        results[lvl_key] = {
            "precision": round(prec * 100, 1),
            "recall": round(rec * 100, 1),
            "f1": round(f1 * 100, 1),
        }

    results["note"] = "computed_from_cause_dimensions"
    return results
