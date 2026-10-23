"""用一份 GT 评测「六策略 × 四模型」的 19 类 CAICS 编码 F1，并生成三线表。

口径与 ``scripts/golden_s1/eval_matrix.py`` 一致：
- 预测按 id 去重，同一 id 取最后一条成功行（跳过 error / parse_error / schema_errors）；
- 每个编码在 300 条事故上做二分类，F1 = 2TP / (2TP + FP + FN)；
- Ave = 19 类宏平均；Ave+ = GT 中正例数 ≥ 5 的类做宏平均；
  Ave17 = 排除 C1、C2 后的 17 类宏平均（论文纵向表使用）。

输出（默认 ``experiments/golden_standard/eval_suntianhao/``）：
- ``f1_per_code.csv``：24 格 × 19 编码的 F1 / P / R 与 Ave、Ave+、Micro-F1；
- ``f1_three_line.md``：Markdown 三线表（模型分组，19 编码 + Ave + Ave+）；
- ``f1_three_line.docx``：A4 横向 DOCX 三线表；
- ``f1_three_line.png``：三线表位图（300 dpi）；
- ``eval_report.md``：数据口径、GT 分布、最佳策略与排名。

用法：
    python3 scripts/golden_s1/eval_gt_f1_table.py \
        --gt experiments/golden_standard/gt_manual/suntianhao.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + [
    f"T{i}" for i in range(1, 5)
] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]
# C1 / C2 正例极少且 F1 波动大，论文表不纳入平均；Ave17 为其余 17 类宏平均。
AVE_CODES_17 = [code for code in ALL_CODES if code not in {"C1", "C2"}]
LABELS = ["glm5.2", "flash", "hy3", "pro"]
STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]


def per_class_metrics(gt: list[set], pred: list[set]) -> dict[str, dict]:
    """逐编码二分类 P/R/F1（与 scripts/golden_s1/eval_matrix.py 同口径）。"""
    result = {}
    for code in ALL_CODES:
        tp = sum(1 for g, p in zip(gt, pred) if code in g and code in p)
        fp = sum(1 for g, p in zip(gt, pred) if code not in g and code in p)
        fn = sum(1 for g, p in zip(gt, pred) if code in g and code not in p)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        result[code] = {
            "P": precision,
            "R": recall,
            "F1": f1,
            "n_gt": sum(1 for g in gt if code in g),
            "n_pred": sum(1 for p in pred if code in p),
        }
    return result


def summarize(per_class: dict[str, dict]) -> dict:
    present = [v for v in per_class.values() if v["n_gt"] > 0]
    plus = [v for v in present if v["n_gt"] >= AVG_PLUS_MIN]

    def mean(values, key):
        return sum(v[key] for v in values) / len(values) if values else 0.0

    return {
        "ave_P": mean(present, "P"),
        "ave_R": mean(present, "R"),
        "ave_F1": mean(present, "F1"),
        "ave_plus_F1": mean(plus, "F1"),
        "n_classes": len(present),
        "n_classes_plus": len(plus),
    }


def micro_f1(gt: list[set], pred: list[set]) -> tuple[float, float, float]:
    tp = sum(1 for g, p in zip(gt, pred) for _ in (g & p))
    fp = sum(1 for g, p in zip(gt, pred) for _ in (p - g))
    fn = sum(1 for g, p in zip(gt, pred) for _ in (g - p))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1

MODEL_DISPLAY = {
    "glm5.2": "GLM-5.2",
    "flash": "DeepSeek-V4-Flash",
    "hy3": "混元 hy3",
    "pro": "DeepSeek-V4-Pro",
}
STRATEGY_DISPLAY = {
    "S0": "S0 零样本",
    "S1": "S1 少样本",
    "S2": "S2 自动链式思维",
    "S3": "S3 规划与求解",
    "S4": "S4 CAICS-CoT",
    "S5": "S5 CAICS-CoT+",
}
AVG_PLUS_MIN = 5
DEFAULT_GT = ROOT / "experiments/golden_standard/gt_manual/suntianhao.jsonl"
DEFAULT_PRED_ROOT = ROOT / "experiments/golden_standard/strategies"
DEFAULT_OUT = ROOT / "experiments/golden_standard/eval_suntianhao"


# ---------------------------------------------------------------- 载入

def load_preds(strategy: str, label: str, root: Path) -> dict[str, dict]:
    """按 id 去重、同 id 取最后一条成功行（与 eval_matrix.load_preds 一致）。"""
    path = root / strategy / label / "results.jsonl"
    if not path.exists():
        return {}
    ok: dict[str, dict] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("error") or row.get("parse_error") or row.get("schema_errors"):
                continue
            ok[row["id"]] = row.get("parsed") or {}
    return ok


def load_gt(path: Path) -> dict[str, set[str]]:
    """读取 {accident_id: set(codes)}，忽略不在 19 类体系内的编码。"""
    out: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            aid = row.get("accident_id") or row.get("id")
            if not aid:
                raise ValueError(f"GT 行缺少 accident_id：{line[:80]}")
            out[str(aid)] = {c for c in (row.get("codes") or []) if c in ALL_CODES}
    if len(out) != len({aid for aid in out}):
        raise ValueError("GT 中存在重复 accident_id")
    return out


def evaluate(gt: dict[str, set[str]], pred_root: Path, strategies: list[str],
             models: list[str]) -> tuple[dict, dict]:
    """返回 {(strategy, model): metrics} 与逐类正例数。"""
    ids = sorted(gt)
    code_set = set(ALL_CODES)
    results: dict[tuple[str, str], dict] = {}
    for strategy in strategies:
        for model in models:
            preds = load_preds(strategy, model, pred_root)
            missing = [aid for aid in ids if aid not in preds]
            if missing:
                raise ValueError(
                    f"{strategy}/{model} 缺少 {len(missing)} 条预测，例如 {missing[:3]}"
                )
            g = [gt[aid] for aid in ids]
            p = [
                {c for c in (preds[aid].get("codes") or []) if c in code_set}
                for aid in ids
            ]
            pc = per_class_metrics(g, p)
            summary = summarize(pc)
            results[(strategy, model)] = {
                "n": len(ids),
                "per_class": pc,
                "ave_P": summary["ave_P"],
                "ave_R": summary["ave_R"],
                "ave_F1": summary["ave_F1"],
                "ave_plus_F1": summary["ave_plus_F1"],
                "ave17": sum(pc[code]["F1"] for code in AVE_CODES_17) / len(AVE_CODES_17),
                "micro_F1": micro_f1(g, p)[2],
                "n_gt": {code: sum(1 for row in g if code in row) for code in ALL_CODES},
                "n_pred": {code: sum(1 for row in p if code in row) for code in ALL_CODES},
            }
    n_gt = results[(strategies[0], models[0])]["n_gt"]
    return results, n_gt


def avg_plus_codes(n_gt: dict[str, int]) -> list[str]:
    return [code for code in ALL_CODES if n_gt.get(code, 0) >= AVG_PLUS_MIN]


# ---------------------------------------------------------------- 明细表

def table_rows(results: dict, models: list[str], strategies: list[str]) -> list[list[str]]:
    """构造 24 行 × (2 + 19 + 2) 的字符串矩阵。"""
    rows: list[list[str]] = []
    for model in models:
        for index, strategy in enumerate(strategies):
            cell = results[(strategy, model)]
            values = [f"{cell['per_class'][code]['F1'] * 100:.2f}" for code in ALL_CODES]
            rows.append(
                [
                    MODEL_DISPLAY[model] if index == 0 else "",
                    STRATEGY_DISPLAY[strategy],
                    *values,
                    f"{cell['ave_F1'] * 100:.2f}",
                    f"{cell['ave_plus_F1'] * 100:.2f}",
                ]
            )
    return rows


HEADERS = ["模型", "提示", *ALL_CODES, "Ave", "Ave+"]


def _numeric_values(rows: list[list[str]], col: int) -> list[float]:
    values = []
    for row in rows:
        try:
            values.append(float(row[col]))
        except ValueError:
            continue
    return values


def _best_two(rows: list[list[str]], col: int) -> tuple[float | None, float | None]:
    values = sorted(set(_numeric_values(rows, col)), reverse=True)
    best = values[0] if values else None
    second = values[1] if len(values) > 1 else None
    return best, second


def write_csv(results: dict, n_gt: dict[str, int], path: Path,
              models: list[str], strategies: list[str]) -> None:
    fields = ["model", "strategy", "n", *ALL_CODES, "Ave", "Ave+", "Ave17"]
    for code in ALL_CODES:
        fields += [f"{code}_P", f"{code}_R"]
    fields.append("Micro_F1")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for model in models:
            for strategy in strategies:
                cell = results[(strategy, model)]
                row = {
                    "model": MODEL_DISPLAY[model],
                    "strategy": strategy,
                    "n": cell["n"],
                    **{code: round(cell["per_class"][code]["F1"] * 100, 2) for code in ALL_CODES},
                    "Ave": round(cell["ave_F1"] * 100, 2),
                    "Ave+": round(cell["ave_plus_F1"] * 100, 2),
                    "Ave17": round(cell["ave17"] * 100, 2),
                    "Micro_F1": round(cell["micro_F1"] * 100, 2),
                }
                for code in ALL_CODES:
                    row[f"{code}_P"] = round(cell["per_class"][code]["P"] * 100, 2)
                    row[f"{code}_R"] = round(cell["per_class"][code]["R"] * 100, 2)
                writer.writerow(row)
    # 单独写一份 GT 正例分布，避免主表每行重复
    with (path.parent / "gt_code_distribution.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["code", "n_gt", "share_pct", "in_ave_plus"])
        plus = set(avg_plus_codes(n_gt))
        for code in ALL_CODES:
            writer.writerow(
                [code, n_gt[code], f"{n_gt[code] / 300 * 100:.2f}", int(code in plus)]
            )


def write_markdown(rows: list[list[str]], path: Path, gt_name: str,
                   n_plus: int) -> None:
    lines = [
        f"# 表 1　六种提示策略 × 四模型的 19 类 CAICS 编码 F1（%，GT：{gt_name}，n=300）",
        "",
        "| " + " | ".join(HEADERS) + " |",
        "|" + "|".join([":--:"] * len(HEADERS)) + "|",
    ]
    for row in rows:
        lines.append("| " + " | ".join(row) + " |")
    lines += [
        "",
        "注：F1 为逐编码二分类 F1；Ave 为 19 类宏平均；Ave+ 为 GT 正例数 ≥5 的 "
        f"{n_plus} 类宏平均；每列最优加粗、次优加下划线（本 Markdown 版未编码样式，"
        "样式见 DOCX/PNG）。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------- DOCX

def write_docx(rows: list[list[str]], path: Path, gt_name: str,
               n_plus: int, models: list[str], strategies: list[str]) -> None:
    try:
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
        from docx.shared import Cm, Pt
    except ImportError as exc:  # pragma: no cover
        print(f"跳过 DOCX（缺少 python-docx：{exc}）")
        return

    def font(run, size=7.5, bold=False):
        run.font.name = "宋体"
        run.font.size = Pt(size)
        run.bold = bold
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def style_cell(cell, text, size=7.5, align=WD_ALIGN_PARAGRAPH.CENTER):
        cell.text = text
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        for run in paragraph.runs:
            font(run, size)

    def replace_tbl_child(tbl_pr, tag):
        for child in tbl_pr.findall(qn(tag)):
            tbl_pr.remove(child)
        element = OxmlElement(tag)
        tbl_pr.append(element)
        return element

    def three_line_borders(table):
        tbl_pr = table._tbl.tblPr
        borders = replace_tbl_child(tbl_pr, "w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            element = OxmlElement(f"w:{edge}")
            if edge in ("top", "bottom"):
                element.set(qn("w:val"), "single")
                element.set(qn("w:sz"), "12")
            else:
                element.set(qn("w:val"), "none")
                element.set(qn("w:sz"), "0")
            element.set(qn("w:color"), "000000")
            borders.append(element)

    def cell_border(cell, edge="bottom", sz=6, color="000000"):
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_borders = tc_pr.first_child_found_in("w:tcBorders")
        if tc_borders is None:
            tc_borders = OxmlElement("w:tcBorders")
            tc_pr.append(tc_borders)
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(sz))
        element.set(qn("w:color"), color)
        tc_borders.append(element)

    def clear_cell_borders(cell):
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_borders = tc_pr.first_child_found_in("w:tcBorders")
        if tc_borders is not None:
            tc_pr.remove(tc_borders)

    def fixed_width(table, total_cm):
        tbl_pr = table._tbl.tblPr
        width = replace_tbl_child(tbl_pr, "w:tblW")
        width.set(qn("w:w"), str(int(total_cm * 567)))
        width.set(qn("w:type"), "dxa")
        layout = replace_tbl_child(tbl_pr, "w:tblLayout")
        layout.set(qn("w:type"), "fixed")

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width = Cm(29.7)
    section.page_height = Cm(21.0)
    section.left_margin = section.right_margin = Cm(1.2)
    section.top_margin = section.bottom_margin = Cm(1.2)

    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.LEFT
    title.paragraph_format.space_after = Pt(4)
    font(
        title.add_run(
            f"表 1　六种提示策略 × 四模型的 19 类 CAICS 编码 F1（%，"
            f"GT：{gt_name}，n=300）"
        ),
        size=10.5,
        bold=True,
    )

    widths = [1.9, 3.6] + [0.95] * len(ALL_CODES) + [1.15, 1.15]
    table = doc.add_table(rows=1, cols=len(HEADERS))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    fixed_width(table, sum(widths))

    for index, header in enumerate(HEADERS):
        cell = table.rows[0].cells[index]
        style_cell(cell, header)
        font(cell.paragraphs[0].runs[0], bold=True)
        cell_border(cell, "top", sz=6)
        cell_border(cell, "bottom", sz=6)
    three_line_borders(table)

    for row_values in rows:
        cells = table.add_row().cells
        for index, value in enumerate(row_values):
            style_cell(cells[index], value)

    # 表体最后一行下边线
    for cell in table.rows[-1].cells:
        cell_border(cell, "bottom", sz=12)

    # 模型分组之间的细线
    for group_index, model in enumerate(models):
        last_row = 1 + group_index * len(strategies) + len(strategies) - 1
        if group_index < len(models) - 1:
            for cell in table.rows[last_row].cells:
                cell_border(cell, "bottom", sz=4, color="808080")

    # 竖向合并模型名
    for group_index, model in enumerate(models):
        start = 1 + group_index * len(strategies)
        end = start + len(strategies) - 1
        merged = table.cell(start, 0).merge(table.cell(end, 0))
        merged.text = ""
        paragraph = merged.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        font(paragraph.add_run(MODEL_DISPLAY[model]), bold=True)
        merged.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # 每列最优加粗、次优加下划线（跳过模型/提示列）
    for col in range(2, len(HEADERS)):
        best, second = _best_two(rows, col)
        for row_index, row_values in enumerate(rows, start=1):
            try:
                value = float(row_values[col])
            except ValueError:
                continue
            for run in table.cell(row_index, col).paragraphs[0].runs:
                if best is not None and value == best:
                    run.bold = True
                elif second is not None and value == second:
                    run.underline = True

    for row in table.rows:
        for index, width in enumerate(widths):
            row.cells[index].width = Cm(width)

    # 表头跨页重复
    tr_pr = table.rows[0]._tr.get_or_add_trPr()
    header_flag = OxmlElement("w:tblHeader")
    header_flag.set(qn("w:val"), "true")
    tr_pr.append(header_flag)

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(4)
    font(
        note.add_run(
            "注：F1 为逐编码二分类 F1；Ave 为 19 类宏平均；Ave+ 为 GT 正例数 ≥5 的 "
            f"{n_plus} 类宏平均；每列最优加粗、次优加下划线。"
        ),
        size=8,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


# ---------------------------------------------------------------- PNG

def write_png(rows: list[list[str]], path: Path, gt_name: str, n_plus: int) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, rcParams

    # 显式注册 Regular/Bold，避免 matplotlib 选到可变字体里的 Thin 字重
    for font_path in (
        "/home/suntianhao/.fonts/NotoSansSC-Regular.ttf",
        "/home/suntianhao/.fonts/NotoSansSC-Bold.ttf",
    ):
        if Path(font_path).is_file():
            font_manager.fontManager.addfont(font_path)
    available = {f.name for f in font_manager.fontManager.ttflist}
    cjk = [f for f in ("Noto Sans SC", "WenQuanYi Zen Hei", "SimHei") if f in available]
    latin = [f for f in ("Arial", "Liberation Sans", "DejaVu Sans") if f in available]
    rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": cjk + latin or ["DejaVu Sans"],
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )

    n_rows = len(rows) + 1
    n_cols = len(HEADERS)
    fig_w, fig_h = 19.5, 0.30 * n_rows + 1.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=300)
    ax.axis("off")

    width_fracs = [1.9, 3.6] + [0.95] * len(ALL_CODES) + [1.15, 1.15]
    total = sum(width_fracs)
    col_widths = [w / total for w in width_fracs]

    table = ax.table(
        cellText=[HEADERS] + rows,
        cellLoc="center",
        loc="center",
        colWidths=col_widths,
    )
    table.auto_set_font_size(False)
    table.set_fontsize(7.2)
    table.scale(1, 1.28)

    best_second = {
        col: _best_two(rows, col) for col in range(2, n_cols)
    }
    underlined_cells = []

    for (row_index, col_index), cell in table.get_celld().items():
        cell.set_linewidth(0)
        cell.visible_edges = ""
        cell.set_facecolor("white")
        text = cell.get_text()
        text.set_fontsize(7.2)
        if row_index == 0:
            text.set_fontweight("bold")
            cell.visible_edges = "BT"
            cell.set_linewidth(1.3)
        elif row_index == len(rows):
            cell.visible_edges = "B"
            cell.set_linewidth(1.3)
        elif row_index % len(STRATEGIES) == 0:
            # 每个模型分组最后一行：细灰线
            cell.visible_edges = "B"
            cell.set_linewidth(0.5)
            cell.set_edgecolor("#888888")
        if row_index > 0 and col_index >= 2:
            value_text = text.get_text()
            if value_text:
                value = float(value_text)
                best, second = best_second.get(col_index, (None, None))
                if best is not None and value == best:
                    text.set_fontweight("bold")
                elif second is not None and value == second:
                    underlined_cells.append(cell)

    # 真正的文字下划线：先绘制，再按文字包围盒画线
    from matplotlib.lines import Line2D

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    inverse = fig.transFigure.inverted()
    for cell in underlined_cells:
        bbox = cell.get_text().get_window_extent(renderer=renderer)
        (x0, y0), (x1, _) = inverse.transform(
            [[bbox.x0, bbox.y0 - 0.8], [bbox.x1, bbox.y0 - 0.8]]
        )
        fig.add_artist(
            Line2D([x0, x1], [y0, y0], transform=fig.transFigure,
                   color="black", linewidth=0.6)
        )

    ax.set_title(
        f"六种提示策略 × 四模型的 19 类 CAICS 编码 F1（%，GT：{gt_name}，n=300）",
        fontsize=11,
        loc="left",
        pad=8,
    )
    fig.text(
        0.01,
        0.015,
        f"注：F1 为逐编码二分类 F1；Ave 为 19 类宏平均；Ave+ 为 GT 正例数 ≥5 的 "
        f"{n_plus} 类宏平均；每列最优加粗、次优加下划线。",
        fontsize=7.5,
        ha="left",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.06, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 报告

def write_report(results: dict, n_gt: dict[str, int], path: Path, gt_name: str,
                 gt_avg_codes: float, models: list[str], strategies: list[str],
                 without_c2: bool) -> None:
    plus = avg_plus_codes(n_gt)
    excluded = [c for c in ALL_CODES if c not in plus]
    lines = [
        f"# GT 评测报告：{gt_name}",
        "",
        "## 1. 数据与口径",
        "",
        f"- GT：`{gt_name}`，300 条事故，平均 {gt_avg_codes:.2f} 个编码/条。",
        "- 该文件仅含 accident_id / title / codes / provenance，未声明 synthetic；"
        "本报告按 codes 为正例直接评测。若它是模拟专家标注，结论仅作敏感性分析。",
        "- 预测：`experiments/golden_standard/strategies/{S0..S5}/{glm5.2,flash,hy3,pro}/results.jsonl`。",
        "- 去重：同一 accident_id 取最后一条成功行；六策略 × 四模型 × 300 条全部对齐。",
        "- 指标：逐编码二分类 F1 = 2TP / (2TP + FP + FN)，在 300 条上汇总。",
        "- Ave：19 类宏平均；Ave+：GT 正例数 ≥5 的 "
        f"{len(plus)} 类宏平均（排除 {', '.join(excluded) if excluded else '无'}）。",
        "- Ave17：排除 C1、C2 后的 17 类宏平均（M1–M5、F1–F4、T1–T4、H1–H4），"
        "论文纵向表（Letter，15.24 cm 正文宽）使用该口径。",
        "- 每列最优加粗、次优加下划线（DOCX/PNG 中体现）。",
        "",
        "## 2. GT 编码分布",
        "",
        "| 编码 | 正例数 | 占比 | 计入 Ave+ | 计入 Ave17 |",
        "|---|---:|---:|:--:|:--:|",
    ]
    for code in ALL_CODES:
        lines.append(
            f"| {code} | {n_gt[code]} | {n_gt[code] / 300 * 100:.2f}% | "
            f"{'是' if code in plus else '否'} | "
            f"{'是' if code in AVE_CODES_17 else '否'} |"
        )

    lines += [
        "",
        "## 3. 各模型最佳策略（按 Ave+ F1）",
        "",
        "| 模型 | 最佳策略 | Ave+ | Ave | 该模型六策略 Ave+ 均值 |",
        "|---|---|---:|---:|---:|",
    ]
    for model in models:
        ranking = sorted(
            strategies,
            key=lambda s: -results[(s, model)]["ave_plus_F1"],
        )
        best = ranking[0]
        mean_plus = sum(results[(s, model)]["ave_plus_F1"] for s in strategies) / len(strategies)
        lines.append(
            f"| {MODEL_DISPLAY[model]} | {STRATEGY_DISPLAY[best]} "
            f"| {results[(best, model)]['ave_plus_F1'] * 100:.2f} "
            f"| {results[(best, model)]['ave_F1'] * 100:.2f} "
            f"| {mean_plus * 100:.2f} |"
        )

    lines += [
        "",
        "若按论文纵向表使用的 Ave17（排除 C1/C2）排序：",
        "",
        "| 模型 | 最佳策略 | Ave17 |",
        "|---|---|---:|",
    ]
    for model in models:
        best = max(strategies, key=lambda s: results[(s, model)]["ave17"])
        lines.append(
            f"| {MODEL_DISPLAY[model]} | {STRATEGY_DISPLAY[best]} "
            f"| {results[(best, model)]['ave17'] * 100:.2f} |"
        )
    lines += [
        "",
        "注意：排除 C1/C2 后，混元 hy3 由 Ave+ 口径下的 S4 > S5 变为 S5 > S4"
        "（77.48 vs 76.10），正文需说明该口径差异。",
        "",
        "## 4. 24 格按 Ave+ F1 排名",
        "",
        "| 排名 | 模型 | 策略 | Ave+ | Ave |",
        "|---:|---|---|---:|---:|",
    ]
    cells = sorted(
        ((s, m) for m in models for s in strategies),
        key=lambda key: -results[key]["ave_plus_F1"],
    )
    for rank, (strategy, model) in enumerate(cells, start=1):
        cell = results[(strategy, model)]
        lines.append(
            f"| {rank} | {MODEL_DISPLAY[model]} | {STRATEGY_DISPLAY[strategy]} "
            f"| {cell['ave_plus_F1'] * 100:.2f} | {cell['ave_F1'] * 100:.2f} |"
        )

    lines += [
        "",
        "## 5. 逐编码最优结果",
        "",
        "| 编码 | GT 正例 | 最优 F1 | 模型 | 策略 |",
        "|---|---:|---:|---|---|",
    ]
    for code in ALL_CODES:
        key = max(
            ((s, m) for m in models for s in strategies),
            key=lambda k: results[k]["per_class"][code]["F1"],
        )
        cell = results[key][ "per_class"][code]
        lines.append(
            f"| {code} | {n_gt[code]} | {cell['F1'] * 100:.2f} "
            f"| {MODEL_DISPLAY[key[1]]} | {STRATEGY_DISPLAY[key[0]]} |"
        )

    if without_c2:
        lines += [
            "",
            "## 6. 说明",
            "",
            "- `suntianhao.jsonl` 中 C2 仅 2 个正例，Ave+ 已按 ≥5 规则排除；"
            "Ave 仍按 19 类计算。",
        ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 主流程

def main() -> None:
    parser = argparse.ArgumentParser(description="GT × 六策略 × 四模型 19 类 F1 三线表")
    parser.add_argument("--gt", default=str(DEFAULT_GT), help="GT jsonl 路径")
    parser.add_argument("--pred-root", default=str(DEFAULT_PRED_ROOT), help="策略结果根目录")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--gt-name", default=None, help="表格中显示的 GT 文件名")
    parser.add_argument("--models", default=",".join(LABELS), help="模型顺序，逗号分隔")
    parser.add_argument("--strategies", default=",".join(STRATEGIES), help="策略顺序")
    parser.add_argument("--skip-png", action="store_true", help="跳过 PNG 渲染")
    args = parser.parse_args()

    gt_path = Path(args.gt).expanduser().resolve()
    pred_root = Path(args.pred_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    gt_name = args.gt_name or gt_path.name
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    strategies = [s.strip() for s in args.strategies.split(",") if s.strip()]
    for model in models:
        if model not in LABELS:
            raise SystemExit(f"未知模型：{model}（可选 {LABELS}）")
    for strategy in strategies:
        if strategy not in STRATEGIES:
            raise SystemExit(f"未知策略：{strategy}（可选 {STRATEGIES}）")

    gt = load_gt(gt_path)
    if len(gt) != 300:
        print(f"警告：GT 为 {len(gt)} 条（预期 300）")
    results, n_gt = evaluate(gt, pred_root, strategies, models)
    plus = avg_plus_codes(n_gt)
    rows = table_rows(results, models, strategies)
    out_dir.mkdir(parents=True, exist_ok=True)

    write_csv(results, n_gt, out_dir / "f1_per_code.csv", models, strategies)
    write_markdown(rows, out_dir / "f1_three_line.md", gt_name, len(plus))
    write_docx(rows, out_dir / "f1_three_line.docx", gt_name, len(plus), models, strategies)
    if not args.skip_png:
        write_png(rows, out_dir / "f1_three_line.png", gt_name, len(plus))
    write_report(
        results, n_gt, out_dir / "eval_report.md", gt_name,
        sum(len(v) for v in gt.values()) / len(gt), models, strategies,
        without_c2=(len(plus) == 18),
    )

    print(f"GT：{gt_path}（{len(gt)} 条，平均 {sum(len(v) for v in gt.values()) / len(gt):.2f} 编码/条）")
    print(f"Ave+ 类别（正例 ≥{AVG_PLUS_MIN}）：{len(plus)} 类；排除 {[c for c in ALL_CODES if c not in plus]}")
    print("\n模型级最佳策略（Ave+ F1，%）：")
    for model in models:
        best = max(strategies, key=lambda s: results[(s, model)]["ave_plus_F1"])
        print(
            f"  {MODEL_DISPLAY[model]:18s} {STRATEGY_DISPLAY[best]:16s} "
            f"Ave+={results[(best, model)]['ave_plus_F1'] * 100:.2f} "
            f"Ave={results[(best, model)]['ave_F1'] * 100:.2f}"
        )
    print(f"\n已写入：{out_dir}")


if __name__ == "__main__":
    main()
