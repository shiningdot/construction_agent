#!/usr/bin/env python3
"""work_task 枚举映射脚本 v2: 将 DB 中所有自由文本值映射到 15 类 WT 枚举。

策略: 级联关键词规则（目标 ~85%覆盖） + LLM 判断（剩余 ~15%）
级联顺序: 精确匹配 → 拆除/安装规则 → 关键词优先级匹配 → LLM → WT15兜底

输出:
  - docs/enums/work_task_mapping.json  -- 完整映射表 {原始值: {code, method, confidence, count}}
  - docs/enums/work_task_mapping_report.md -- 映射统计报告

用法:
  python3 scripts/enum_mapping_work_task.py --db /path/to/caiems_v2.db [--llm] [--dry-run]
"""

import json
import logging
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════
# WT 枚举关键词定义 (优先级从高到低排列)
# ══════════════════════════════════════════════════════════════════

WT_CATEGORIES: dict[str, dict] = {
    # 工程类型级别（最高优先级）—— 按工程整体属性判定
    "WT09-桥梁工程": {
        "priority": 10,
        "pos": ["桥梁", "桥墩", "桥面", "箱梁", "T梁", "墩柱", "盖梁", "桥台",
                "预制梁", "梁板", "拱桥", "斜拉桥", "悬索桥", "架桥", "桥上",
                "桥桩", "桥基"],
        "neg": ["拆除桥"],
    },
    "WT10-隧道与地下": {
        "priority": 10,
        "pos": ["隧道", "隧洞", "掘进", "盾构", "TBM", "管廊", "暗挖",
                "管片", "衬砌", "锚喷", "出碴", "出渣", "洞身", "洞门"],
        "neg": [],
    },

    # 结构/设备级别 —— 按具体施工对象判定
    "WT06-钢结构与网架": {
        "priority": 9,
        "pos": ["钢结构", "钢网架", "网架安装", "钢桁架", "钢屋架", "钢屋盖",
                "钢构", "钢柱", "钢梁", "钢框架", "彩钢板", "钢架", "钢檩",
                "钢拱", "钢箱梁"],
        "neg": ["钢筋", "钢模板"],
    },
    "WT02-桩基工程": {
        "priority": 8,
        "pos": ["打桩", "桩基", "灌注桩", "预制桩", "管桩", "搅拌桩", "CFG桩",
                "钻孔桩", "压桩", "挖孔桩", "冲孔桩", "方桩", "桩孔",
                "桩施工", "桩基础"],
        "neg": [],
    },
    "WT08-塔吊与起重": {
        "priority": 8,
        "pos": ["塔吊", "塔式起重机", "起重机", "吊车", "吊装", "吊运",
                "龙门架", "井架", "井字架", "卷扬机", "顶升", "架桥机",
                "施工升降机", "物料提升机", "电动葫芦", "起重", "吊篮",
                "吊盘", "吊钩", "吊索", "升降机", "门吊", "桅杆吊",
                "汽车吊", "履带吊", "随车吊"],
        "neg": ["吊顶"],
    },

    # 工法/材料级别
    "WT07-脚手架作业": {
        "priority": 7,
        "pos": ["脚手架", "外架", "脚手板", "悬挑架", "扣件式", "碗扣式",
                "盘扣式", "脚手搭设", "脚手拆除", "防护棚", "卸料平台",
                "脚手", "钢管架"],
        "neg": ["满堂", "支撑架", "承重架", "操作平台"],
    },
    "WT04-模板与支撑": {
        "priority": 7,
        "pos": ["模板", "支模", "拆模", "支撑架", "支模架", "满堂架", "排架",
                "操作平台", "模板支撑", "滑模", "爬模", "顶模", "承重架",
                "模架", "钢模板"],
        "neg": ["外脚手架", "防护脚手架"],
    },
    "WT03-钢筋工程": {
        "priority": 6,
        "pos": ["绑钢筋", "钢筋绑扎", "绑扎钢筋", "钢筋笼", "钢筋加工", "钢筋网",
                "钢筋焊接", "直螺纹", "预应力", "张拉", "钢筋", "箍筋",
                "预应力筋"],
        "neg": ["混凝土", "塔吊", "转运钢筋", "运输钢筋", "吊运钢筋"],
    },
    "WT11-管道与管网": {
        "priority": 5,
        "pos": ["管道", "管网", "管线", "给排水", "排水管", "污水管", "供水管",
                "燃气管", "暖通", "试压", "水管", "排污", "风管", "电缆",
                "光缆", "线缆", "顶管施工", "清淤", "疏浚"],
        "neg": ["隧道", "掘进"],
    },
    "WT12-装饰与装修": {
        "priority": 5,
        "pos": ["装修", "装饰", "抹灰", "粉刷", "涂料", "油漆", "贴砖",
                "幕墙", "外墙保温", "外墙涂料", "内墙", "吊顶", "地板",
                "屋面防水", "外墙粉刷", "外墙装修", "外立面", "外墙整修",
                "外墙抹灰", "贴瓷砖", "腻子", "防水", "砌砖", "砌体",
                "砌筑", "贴面", "瓷砖", "石材", "屋面瓦", "水磨石",
                "踢脚线", "屋面", "屋顶", "房顶", "保温板", "门窗",
                "玻璃安装", "铺瓦"],
        "neg": ["结构", "主体"],
    },
    "WT14-设备安装": {
        "priority": 5,
        "pos": ["设备安装", "机电安装", "电梯安装", "电气安装", "设备调试",
                "通风安装", "空调安装", "消防安装", "风机安装", "水泵安装",
                "设备", "调试", "电梯"],
        "neg": ["塔吊", "龙门架", "井架", "脚手架", "模板"],
    },

    # 通用工艺级别
    "WT01-土方与基坑": {
        "priority": 4,
        "pos": ["土方", "基坑", "沟槽", "边坡", "挖土", "挖方", "深基坑",
                "回填", "地基处理", "地基施工", "开挖", "降水", "挖地基",
                "挖孔", "挖", "取土", "清基", "强夯"],
        "neg": ["挖孔桩", "隧道", "掘进", "桩", "管道", "管网"],
    },
    "WT05-混凝土浇筑": {
        "priority": 3,
        "pos": ["混凝土浇", "浇筑混凝土", "浇灌混凝土", "浇注混凝土", "混凝土振捣",
                "泵送混凝土", "封顶", "现浇", "浇捣", "浇筑", "浇灌", "浇注",
                "搅拌", "混凝土", "楼板", "预制板", "灌缝", "找平层",
                "养护", "水泥", "砂浆", "振捣", "打灰", "打混凝土"],
        "neg": ["桥墩", "桥梁", "钢结构", "隧道", "搅拌桩"],
    },
    "WT13-拆除作业": {
        "priority": 2,
        "pos": ["拆除", "拆迁", "拆房", "拆楼", "拆墙", "爆破拆除",
                "房屋拆除", "建筑拆除", "旧楼拆除", "拆建", "拆旧",
                "拆改", "破拆", "凿除"],
        "neg": [],
    },

    # 兜底（最低优先级）
    "WT15-其他": {
        "priority": 1,
        "pos": ["测量放线", "检测", "绿化", "临建", "临时设施", "围挡",
                "清洁", "抽水", "清理", "搬运", "巡查", "试验", "维修",
                "改造", "加固", "高空作业", "巡检", "勘查", "勘察",
                "修缮", "修补", "施工", "作业", "值班", "看守",
                "材料", "运送", "运输", "转运", "递送", "堆放",
                "驾驶", "装车", "卸料", "倒运", "上料", "下料",
                "运砖", "运土", "运石", "运料", "搬运工"],
        "neg": [],
    },
}


# ══════════════════════════════════════════════════════════════════
# 级联规则匹配引擎
# ══════════════════════════════════════════════════════════════════

def match_work_task(value: str) -> tuple[str | None, str, float]:
    """级联规则匹配. 返回 (WT编码, 匹配方法, 置信度).

    级联顺序:
      0. 精确枚举匹配
      1. "拆除/拆卸/凿除 + X" → 根据X的设备类型分流
      2. "安装/搭设/组装 + X" → 根据X的设备类型分流
      3. 优先级关键词匹配
      4. 未匹配返回 None
    """
    text = value.strip()

    # ── 第0层: 精确匹配已映射的值 ──
    for code in WT_CATEGORIES:
        if text == code or text == code[6:] or text == code[:5]:
            return (code, "exact_enum", 1.0)

    # ── 第1层: "拆除/拆卸/凿除 + X" 规则 ──
    #  设备拆除归入设备自身类别(全生命周期原则)
    dm = re.match(r'^(拆除|拆卸|拆旧|拆改|搭拆|凿除|破拆)(.+)', text)
    if dm:
        rest = dm.group(2)
        if any(kw in rest for kw in ["脚手架", "外架", "脚手"]):
            return ("WT07-脚手架作业", "拆除+X规则", 0.95)
        if any(kw in rest for kw in ["塔吊", "塔式起重机", "龙门架", "井架",
                                       "井字架", "吊车", "起重", "升降机", "门吊"]):
            return ("WT08-塔吊与起重", "拆除+X规则", 0.95)
        if any(kw in rest for kw in ["模板", "支模", "支撑架", "支模架",
                                       "排架", "承重架", "模架"]):
            return ("WT04-模板与支撑", "拆除+X规则", 0.95)
        if any(kw in rest for kw in ["桥梁", "桥", "隧道"]):
            return ("WT13-拆除作业", "拆除+X规则", 0.90)
        # 一般拆除 → WT13
        return ("WT13-拆除作业", "拆除+X规则", 0.85)

    # ── 第2层: "安装/搭设/组装 + X" 规则 ──
    im = re.match(r'^(安装|搭设|搭|装|拆卸|组装)(.+)', text)
    if im:
        rest = im.group(2)
        if any(kw in rest for kw in ["塔吊", "塔式起重机", "龙门架", "井架",
                                       "井字架", "门吊"]):
            return ("WT08-塔吊与起重", "安装+X规则", 0.95)
        if any(kw in rest for kw in ["脚手架", "外架", "脚手", "防护棚", "钢管架"]):
            return ("WT07-脚手架作业", "安装+X规则", 0.95)
        if any(kw in rest for kw in ["模板", "支模", "支撑", "操作平台",
                                       "满堂架", "模架"]):
            return ("WT04-模板与支撑", "安装+X规则", 0.95)
        if any(kw in rest for kw in ["玻璃", "门窗", "幕墙"]):
            return ("WT12-装饰与装修", "安装+X规则", 0.90)
        if any(kw in rest for kw in ["管道", "水管", "排水", "风管"]):
            return ("WT11-管道与管网", "安装+X规则", 0.85)
        if any(kw in rest for kw in ["电梯", "设备", "风机", "水泵"]):
            return ("WT14-设备安装", "安装+X规则", 0.90)
        if any(kw in rest for kw in ["楼板", "预制板"]):
            return ("WT05-混凝土浇筑", "安装+X规则", 0.85)
        if any(kw in rest for kw in ["钢结构", "钢梁", "钢柱", "网架", "钢架"]):
            return ("WT06-钢结构与网架", "安装+X规则", 0.90)

    # ── 第3层: 优先级关键词匹配 ──
    best_code = None
    best_priority = -1
    best_score = 0

    for code, rules in sorted(WT_CATEGORIES.items(),
                               key=lambda x: -x[1]["priority"]):
        pos_score = sum(1 for kw in rules["pos"] if kw in text)
        neg_score = sum(1 for kw in rules["neg"] if kw in text)

        if pos_score > 0 and neg_score == 0:
            if (rules["priority"] > best_priority or
                    (rules["priority"] == best_priority and pos_score > best_score)):
                best_code = code
                best_priority = rules["priority"]
                best_score = pos_score

    if best_code:
        confidence = min(0.95, 0.5 + best_score * 0.1)
        return (best_code, "keyword", round(confidence, 2))

    return (None, "no_match", 0.0)


# ══════════════════════════════════════════════════════════════════
# LLM 辅助映射
# ══════════════════════════════════════════════════════════════════

LLM_MAPPING_SYSTEM = """你是建筑施工安全专家。请将施工作业类型自由文本映射到对应的 WT 枚举代码。

## 枚举定义

| 代码 | 名称 | 覆盖范围 |
|------|------|---------|
| WT01 | 土方与基坑 | 土方开挖、基坑支护、沟槽开挖、地基处理 |
| WT02 | 桩基工程 | 打桩、灌注桩、管桩等各类桩基础施工 |
| WT03 | 钢筋工程 | 钢筋加工、绑扎、连接、安装、预应力张拉 |
| WT04 | 模板与支撑 | 模板搭拆、支撑体系、操作平台、满堂架 |
| WT05 | 混凝土浇筑 | 混凝土搅拌、运输、浇筑、振捣、养护、预制构件安装 |
| WT06 | 钢结构与网架 | 钢结构安装、网架拼装、桁架、钢屋架施工 |
| WT07 | 脚手架作业 | 防护脚手架搭设、使用、拆除全生命周期 |
| WT08 | 塔吊与起重 | 塔吊/龙门架/井架/吊车的安装、顶升、吊运、拆卸全过程 |
| WT09 | 桥梁工程 | 桥墩、箱梁、桥面等桥梁主体结构施工 |
| WT10 | 隧道与地下 | 隧道掘进、盾构、顶管、地下管廊 |
| WT11 | 管道与管网 | 市政给排水/燃气/热力/电力管道安装 |
| WT12 | 装饰与装修 | 内外墙装修、防水保温、幕墙门窗、砌筑、屋面 |
| WT13 | 拆除作业 | 建筑物/构筑物结构拆除（不含设备/脚手架/模板拆除） |
| WT14 | 设备安装 | 机电设备、电梯、暖通等专用设备安装调试 |
| WT15 | 其他 | 辅助作业、材料运输、临时设施、罕见类型、信息不足 |

## 判定规则
1. 全生命周期原则: 某设备的安装/使用/拆除归入同一类（塔吊拆除→WT08，模板拆除→WT04）
2. 工程类型优先: "桥墩混凝土浇筑"→WT09(桥梁优先)，非WT05
3. 设备优先: "塔吊吊运钢筋"→WT08，非WT03
4. 主要目的原则: 多步骤作业以主要施工目的为准
5. 不确定时选 WT15-其他

请严格输出 JSON 数组，每个元素包含 text(原始文本)和 code(WT编码):
```json
[{"text": "浇筑混凝土", "code": "WT05-混凝土浇筑"}, ...]
```"""


def _parse_llm_json_array(raw_text: str) -> list[dict] | None:
    """鲁棒解析 LLM 返回的 JSON 数组，支持多级回退。

    回退策略:
      1. 直接解析 JSON
      2. 提取 ```json ... ``` 代码块
      3. 提取 [...] 数组
      4. 逐行正则提取 {"text": "...", "code": "WTXX-..."} 模式
    """
    # 策略1: 直接解析
    try:
        result = json.loads(raw_text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # 策略2: 提取代码块
    m = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw_text)
    if m:
        try:
            result = json.loads(m.group(1))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 策略3: 提取数组
    m = re.search(r'\[[\s\S]*\]', raw_text)
    if m:
        try:
            result = json.loads(m.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # 策略4: 逐行正则提取（容错性最强）
    # 匹配模式: {"text": "XXX", "code": "WTXX-XXX"} 或类似格式
    pattern = r'\{\s*"text"\s*:\s*"([^"]*)"\s*,\s*"code"\s*:\s*"(WT\d{2}[^"]*)"\s*\}'
    matches = re.findall(pattern, raw_text)
    if matches:
        return [{"text": t, "code": c} for t, c in matches]

    return None


def llm_classify_unmatched(values: list[tuple[str, int]]) -> dict[str, dict]:
    """使用 LLM 批量映射未匹配值。

    Args:
        values: [(原始值, 频次), ...]

    Returns:
        {原始值: {code, method, confidence, count}}
    """
    import os as _os
    from anthropic import Anthropic

    # 从环境变量读取配置（兼容不同版本 config）
    api_key = _os.getenv("ANTHROPIC_API_KEY", "")
    base_url = _os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    model = _os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    if not api_key:
        logger.error("未设置 ANTHROPIC_API_KEY 环境变量")
        # 全部回退为 WT15
        results = {}
        for val, cnt in values:
            results[val] = {"code": "WT15-其他", "method": "llm_no_key", "confidence": 0.1, "count": cnt}
        return results

    client = Anthropic(api_key=api_key, base_url=base_url)

    results = {}
    batch_size = 50  # 减小批次，降低 JSON 截断概率

    for batch_start in range(0, len(values), batch_size):
        batch = values[batch_start:batch_start + batch_size]
        values_text = "\n".join(
            f"{i+1}. [{cnt}次] {val}"
            for i, (val, cnt) in enumerate(batch)
        )

        user_msg = f"请将以下 {len(batch)} 个施工作业文本映射到 WT 枚举：\n\n{values_text}"

        try:
            resp = client.messages.create(
                model=model,
                max_tokens=8192,  # 增大输出限制，避免 JSON 截断
                temperature=0.0,
                system=LLM_MAPPING_SYSTEM,
                messages=[{"role": "user", "content": user_msg}],
            )

            # 跳过 thinking blocks (DeepSeek 特有)
            raw_text = ""
            for block in resp.content:
                if hasattr(block, 'type') and block.type == 'thinking':
                    continue
                if hasattr(block, 'text'):
                    raw_text = block.text.strip()
                    break
            if not raw_text:
                raise ValueError("响应中无 text 内容")
            # 提取 JSON 数组（多级回退解析）
            llm_results = _parse_llm_json_array(raw_text)

            if not llm_results:
                raise ValueError(f"无法从响应中解析JSON: {raw_text[:300]}...")

            for item in llm_results:
                orig_text = item.get("text", "")
                # 去除 LLM 可能保留的计数前缀 "[N次] "
                orig_text = re.sub(r'^\[\d+次\]\s*', '', orig_text)
                code = item.get("code", "WT15-其他")
                # 验证 code 有效性
                if code not in WT_CATEGORIES:
                    logger.warning(f"LLM 返回无效code: {code}, 回退WT15")
                    code = "WT15-其他"
                # 找到对应的 count
                cnt = 1
                for v, c in batch:
                    if v == orig_text:
                        cnt = c
                        break
                results[orig_text] = {
                    "code": code,
                    "method": "llm",
                    "confidence": 0.75,
                    "count": cnt,
                }

            logger.info(f"LLM批次 {batch_start//batch_size+1}: "
                        f"{batch_start+1}-{batch_start+len(batch)}/{len(values)}, "
                        f"完成 {len(llm_results)} 条")

        except Exception as e:
            logger.error(f"LLM批次失败 [{batch_start}:{batch_start+batch_size}]: {e}")
            # 失败的全部 → WT15
            for val, cnt in batch:
                results[val] = {
                    "code": "WT15-其他",
                    "method": "llm_error",
                    "confidence": 0.2,
                    "count": cnt,
                }

        time.sleep(0.3)  # 避免限流

    return results


# ══════════════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="work_task 枚举映射")
    parser.add_argument("--db", default="data/caiems_v2.db",
                        help="数据库路径 (默认 data/caiems_v2.db)")
    parser.add_argument("--llm", action="store_true",
                        help="对未匹配值启用LLM辅助映射")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅生成映射表，不回写DB")
    parser.add_argument("--output-dir", default=None,
                        help="输出目录 (默认 docs/enums/)")
    args = parser.parse_args()

    # 1. 读取 DB
    db_path = Path(args.db)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.exists():
        logger.error(f"数据库不存在: {db_path}")
        sys.exit(1)

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

    total_records = sum(r[1] for r in rows)
    logger.info(f"读取 {len(rows)} 个唯一值, 共 {total_records} 条记录")

    # 2. 规则映射
    mapping: dict[str, dict] = {}
    no_match: list[tuple[str, int]] = []

    for value, count in rows:
        code, method, confidence = match_work_task(value)
        if code:
            mapping[value] = {
                "code": code,
                "method": method,
                "confidence": confidence,
                "count": count,
            }
        else:
            no_match.append((value, count))

    matched_records = sum(m["count"] for m in mapping.values())
    logger.info(f"规则映射: {len(mapping)}唯一值 / {matched_records}条记录 "
                f"({matched_records/total_records*100:.1f}%)")

    # 3. LLM 处理未匹配值
    if args.llm and no_match:
        unmatched_records = sum(c for _, c in no_match)
        logger.info(f"LLM处理 {len(no_match)} 个未匹配值 ({unmatched_records}条记录)")
        llm_results = llm_classify_unmatched(no_match)
        mapping.update(llm_results)
        logger.info(f"LLM完成, 总计映射 {len(mapping)} 个唯一值")
    elif not args.llm and no_match:
        logger.info(f"未启用LLM, {len(no_match)} 个未匹配值 → WT15")
        for value, count in no_match:
            mapping[value] = {
                "code": "WT15-其他",
                "method": "fallback",
                "confidence": 0.3,
                "count": count,
            }

    # 4. 验证
    assert len(mapping) == len(rows), f"映射遗漏: {len(mapping)}/{len(rows)}"
    logger.info("验证通过: 所有值已映射")

    # 5. 统计
    by_method = defaultdict(lambda: {"unique": 0, "records": 0})
    by_code = defaultdict(lambda: {"unique": 0, "records": 0})
    for v, m in mapping.items():
        by_method[m["method"]]["unique"] += 1
        by_method[m["method"]]["records"] += m["count"]
        by_code[m["code"]]["unique"] += 1
        by_code[m["code"]]["records"] += m["count"]

    # 6. 输出
    output_dir = Path(args.output_dir) if args.output_dir else Path(__file__).resolve().parent.parent / "docs" / "enums"
    output_dir.mkdir(parents=True, exist_ok=True)

    # JSON 映射表
    mapping_path = output_dir / "work_task_mapping.json"
    with open(mapping_path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    logger.info(f"映射表: {mapping_path}")

    # Markdown 报告
    report_path = output_dir / "work_task_mapping_report.md"
    _write_report(report_path, mapping, rows, by_method, by_code, no_match, args.llm)
    logger.info(f"报告: {report_path}")

    # 7. DB 回填 (非 dry-run)
    if not args.dry_run:
        _backfill_db(db_path, mapping)
    else:
        logger.info("[dry-run] 跳过DB回填")

    _print_summary(mapping, rows, by_method, by_code, no_match)


def _write_report(path, mapping, rows, by_method, by_code, no_match, used_llm):
    total_records = sum(r[1] for r in rows)
    matched_by_rule = sum(
        m["count"] for m in mapping.values()
        if m["method"] not in ("llm", "llm_error", "fallback")
    )

    lines = [
        "# work_task 枚举映射报告",
        "",
        f"- 处理日期: {time.strftime('%Y-%m-%d %H:%M')}",
        f"- 唯一值总数: {len(rows)}",
        f"- 总记录数: {total_records}",
        f"- 规则匹配: {matched_by_rule} 条 ({matched_by_rule/total_records*100:.1f}%)",
        f"- LLM辅助: {'是' if used_llm else '否'}",
        "",
        "## 按匹配方法统计",
        "",
        "| 方法 | 唯一值数 | 记录数 |",
        "|------|:------:|:-----:|",
    ]
    for method in ["exact_enum", "拆除+X规则", "安装+X规则", "keyword",
                    "llm", "llm_error", "fallback"]:
        stats = by_method.get(method)
        if stats:
            lines.append(f"| {method} | {stats['unique']} | {stats['records']} |")

    lines += [
        "",
        "## 按 WT 类别分布",
        "",
        "| 代码 | 类别 | 唯一值数 | 记录数 |",
        "|------|------|:------:|:-----:|",
    ]
    for code in sorted(WT_CATEGORIES.keys()):
        stats = by_code.get(code, {"unique": 0, "records": 0})
        lines.append(f"| {code} | {code[6:]} | {stats['unique']} | {stats['records']} |")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def _backfill_db(db_path: Path, mapping: dict[str, dict]):
    """将映射后的枚举值回写到 DB 的 accidents 表。"""
    conn = sqlite3.connect(str(db_path))
    updated = 0
    for orig_value, info in mapping.items():
        code = info["code"]
        conn.execute(
            "UPDATE accidents SET work_task = ? WHERE work_task = ?",
            (code, orig_value),
        )
        updated += conn.rowcount if hasattr(conn, 'rowcount') else 1
    conn.commit()
    conn.close()
    logger.info(f"DB回填完成: {updated} 条记录已更新")


def _print_summary(mapping, rows, by_method, by_code, no_match):
    """打印映射汇总。"""
    total_records = sum(r[1] for r in rows)
    print("\n" + "=" * 60)
    print("   work_task 枚举映射完成")
    print("=" * 60)
    print(f"  唯一值: {len(rows)} → 15 类枚举")
    print(f"  记录数: {total_records}")
    print(f"\n  按方法:")
    for method in ["exact_enum", "拆除+X规则", "安装+X规则", "keyword",
                    "llm", "llm_error", "fallback"]:
        stats = by_method.get(method)
        if stats:
            pct = stats["records"] / total_records * 100
            print(f"    {method:16s} {stats['unique']:4d}值 / {stats['records']:4d}条 ({pct:.1f}%)")
    print(f"\n  按类别 Top 10:")
    sorted_codes = sorted(by_code.items(), key=lambda x: -x[1]["records"])
    for code, stats in sorted_codes[:10]:
        pct = stats["records"] / total_records * 100
        print(f"    {code:20s} {stats['unique']:4d}值 / {stats['records']:4d}条 ({pct:.1f}%)")
    print("=" * 60)


if __name__ == "__main__":
    main()
