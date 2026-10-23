"""生成 DeepSeek-V4-Flash 在六策略 × 17 编码上的 F1/召回/精确/准确率三线表。

表结构：
- 20 列 × 26 行：`指标`、`策略`、17 个编码（M1–M5、F1–F4、T1–T4、H1–H4）、`Ave(17)`；
- 4 个指标块（F1、召回率、精确率、准确率），每块 6 个策略（S0–S5）；
- 指标列纵向合并 6 行；删除后果放大层 C1、C2；
- 每个指标块内，每列最优加粗、次优下划线；
- Letter 纵向，表宽 15.20 cm，数值 1 位小数、7pt。

指标定义（逐编码二分类，N=300）：
- F1 = 2TP / (2TP + FP + FN)
- Recall = TP / (TP + FN)
- Precision = TP / (TP + FP)
- Accuracy = (TP + TN) / N
- Ave(17) = 17 个编码的未加权宏平均

输出：
- ``paper/flash_metrics_17codes.docx``
- ``paper/flash_metrics_17codes_preview.png``
- ``paper/flash_metrics_17codes.csv``（完整 2 位小数，含宏平均行）

用法：
    python3 scripts/golden_s1/make_flash_metric_table.py \
        --gt /home/suntianhao/construction_agent/experiments/golden_standard/gt_manual/suntianhao.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from make_portrait_f1_table import (  # noqa: E402
    LAYERS,
    STRATEGIES,
    STRATEGY_LABELS,
    TABLE_CODES,
    docx_helpers,
)

MODEL_KEY = "flash"
MODEL_NAME = "DeepSeek-V4-Flash"
METRICS = [
    ("F1", "F1"),
    ("Recall", "召回率"),
    ("Precision", "精确率"),
    ("Accuracy", "准确率"),
]
# 20 列：指标、策略、17 编码、Ave(17)
COL_WIDTHS = [1.20, 1.95] + [0.65] * len(TABLE_CODES) + [1.00]
assert abs(sum(COL_WIDTHS) - 15.20) < 1e-9, sum(COL_WIDTHS)

HEADER_ROWS = 2
DATA_ROWS = len(METRICS) * len(STRATEGIES)
TABLE_ROWS = HEADER_ROWS + DATA_ROWS
DATA_ROW_HEIGHT_CM = 0.48
HEADER_ROW_HEIGHT_CM = 0.42
DECIMALS = 1
BODY_FONT_PT = 7.0
LABEL_FONT_PT = 7.0
HEADER_FONT_PT = 6.5

DEFAULT_GT = ROOT / "experiments/golden_standard/gt_manual/suntianhao.jsonl"
DEFAULT_PRED_ROOT = ROOT / "experiments/golden_standard/strategies"
OUT_DIR = ROOT / "experiments/golden_standard/eval_suntianhao/paper"


def load_gt(path: Path) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            aid = row.get("accident_id") or row.get("id")
            out[str(aid)] = {c for c in (row.get("codes") or []) if c in TABLE_CODES}
    if not out:
        raise ValueError(f"GT 为空：{path}")
    return out


def load_preds(strategy: str, model: str, root: Path) -> dict[str, set[str]]:
    path = root / strategy / model / "results.jsonl"
    if not path.exists():
        raise FileNotFoundError(path)
    ok: dict[str, set[str]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("error") or row.get("parse_error") or row.get("schema_errors"):
                continue
            ok[row["id"]] = {
                c for c in ((row.get("parsed") or {}).get("codes") or [])
                if c in TABLE_CODES
            }
    return ok


def binary_metrics(gt: list[set[str]], pred: list[set[str]], code: str) -> dict[str, float]:
    tp = sum(1 for g, p in zip(gt, pred) if code in g and code in p)
    fp = sum(1 for g, p in zip(gt, pred) if code not in g and code in p)
    fn = sum(1 for g, p in zip(gt, pred) if code in g and code not in p)
    tn = len(gt) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    accuracy = (tp + tn) / len(gt) if gt else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "F1": f1, "Recall": recall, "Precision": precision, "Accuracy": accuracy,
    }


def evaluate(gt: dict[str, set[str]], pred_root: Path) -> dict:
    ids = sorted(gt)
    g = [gt[aid] for aid in ids]
    data: dict[tuple[str, str], dict[str, float]] = {}
    macro: dict[tuple[str, str], float] = {}
    for strategy in STRATEGIES:
        preds = load_preds(strategy, MODEL_KEY, pred_root)
        missing = [aid for aid in ids if aid not in preds]
        if missing:
            raise ValueError(f"{strategy} 缺少 {len(missing)} 条预测")
        p = [preds[aid] for aid in ids]
        for code in TABLE_CODES:
            data[(strategy, code)] = binary_metrics(g, p, code)
        for metric_key, _label in METRICS:
            macro[(strategy, metric_key)] = sum(
                data[(strategy, code)][metric_key] for code in TABLE_CODES
            ) / len(TABLE_CODES)
    return {"data": data, "macro": macro, "n": len(ids)}


def _display(value: float) -> str:
    """先按 CSV 的 2 位小数定值，再显示 1 位，避免双重舍入造成 0.1 差异。"""
    return f"{round(value * 100, 2):.{DECIMALS}f}"


def table_values(evaluation: dict) -> list[list[str]]:
    rows: list[list[str]] = []
    for metric_key, metric_label in METRICS:
        for strategy in STRATEGIES:
            values = [
                _display(evaluation['data'][(strategy, code)][metric_key])
                for code in TABLE_CODES
            ]
            values.append(_display(evaluation['macro'][(strategy, metric_key)]))
            rows.append([metric_label, STRATEGY_LABELS[strategy], *values])
    return rows


# ---------------------------------------------------------------- DOCX

def write_docx(rows: list[list[str]], path: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT

    h = docx_helpers()
    Cm = h["Cm"]
    Pt = h["Pt"]
    set_cell_text = h["set_cell_text"]
    set_run_font = h["set_run_font"]
    set_cell_width = h["set_cell_width"]
    set_grid_widths = h["set_grid_widths"]
    set_table_layout = h["set_table_layout"]
    set_row_height = h["set_row_height"]
    set_repeat_header = h["set_repeat_header"]
    cell_border = h["cell_border"]
    CENTER = h["WD_ALIGN_PARAGRAPH"].CENTER

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Cm(21.59)
    section.page_height = Cm(27.94)
    section.left_margin = Cm(3.17)
    section.right_margin = Cm(3.17)
    section.top_margin = Cm(2.54)
    section.bottom_margin = Cm(2.54)

    caption = doc.add_paragraph()
    caption.alignment = CENTER
    caption.paragraph_format.space_after = Pt(4)
    caption.paragraph_format.keep_with_next = True
    set_run_font(
        caption.add_run(
            "表 X　DeepSeek-V4-Flash 在六种提示策略下的 17 类 CAICS 致因指标（%）"
        ),
        10.5,
        True,
    )

    table = doc.add_table(rows=TABLE_ROWS, cols=len(COL_WIDTHS))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    set_grid_widths(table, COL_WIDTHS)
    grid_twips = sum(int(round(width * 567)) for width in COL_WIDTHS)
    set_table_layout(table, grid_twips / 567)
    for row in table.rows:
        for index, width in enumerate(COL_WIDTHS):
            set_cell_width(row.cells[index], width)

    # 表头合并
    table.cell(0, 0).merge(table.cell(1, 0))  # 指标
    table.cell(0, 1).merge(table.cell(1, 1))  # 策略
    table.cell(0, 19).merge(table.cell(1, 19))  # Ave(17)
    start = 2
    for _label, codes in LAYERS:
        end = start + len(codes) - 1
        table.cell(0, start).merge(table.cell(0, end))
        start = end + 1

    # 指标列纵向合并 6 行
    for block in range(len(METRICS)):
        first = HEADER_ROWS + block * len(STRATEGIES)
        last = first + len(STRATEGIES) - 1
        table.cell(first, 0).merge(table.cell(last, 0))

    # 表头文字
    set_cell_text(table.cell(0, 0), "指标", LABEL_FONT_PT, True)
    set_cell_text(table.cell(0, 1), "策略", LABEL_FONT_PT, True)
    start = 2
    for label, codes in LAYERS:
        set_cell_text(table.cell(0, start), label, 6.0, True)
        start += len(codes)
    set_cell_text(table.cell(0, 19), "Ave(17)", HEADER_FONT_PT, True)
    for index, code in enumerate(TABLE_CODES):
        set_cell_text(table.cell(1, 2 + index), code, HEADER_FONT_PT, True)

    # 数据
    for row_index, values in enumerate(rows, start=HEADER_ROWS):
        metric_label = values[0]
        if (row_index - HEADER_ROWS) % len(STRATEGIES) == 0:
            set_cell_text(table.cell(row_index, 0), metric_label, LABEL_FONT_PT, True)
        set_cell_text(
            table.cell(row_index, 1), values[1], LABEL_FONT_PT, False, CENTER
        )
        for col, value in enumerate(values[2:], start=2):
            set_cell_text(
                table.cell(row_index, col), value, BODY_FONT_PT, False, CENTER
            )

    # 行高 / 防跨页 / 表头重复
    for row in table.rows[:HEADER_ROWS]:
        set_row_height(row, HEADER_ROW_HEIGHT_CM)
        set_repeat_header(row)
    for row in table.rows[HEADER_ROWS:]:
        set_row_height(row, DATA_ROW_HEIGHT_CM)

    # 三线
    for cell in table.rows[0].cells:
        cell_border(cell, "top", 12)
    for cell in table.rows[1].cells:
        cell_border(cell, "bottom", 6)
    for cell in table.rows[-1].cells:
        cell_border(cell, "bottom", 12)

    # 指标块之间：0.25pt 浅灰细线
    for block in range(len(METRICS) - 1):
        last = HEADER_ROWS + block * len(STRATEGIES) + len(STRATEGIES) - 1
        for cell in table.rows[last].cells:
            cell_border(cell, "bottom", 2, "A6A6A6")

    # 每个指标块内，每列最优加粗、次优下划线
    for block in range(len(METRICS)):
        first_row = HEADER_ROWS + block * len(STRATEGIES)
        block_rows = rows[block * len(STRATEGIES):(block + 1) * len(STRATEGIES)]
        for col in range(2, len(COL_WIDTHS)):
            values = [float(row[col]) for row in block_rows]
            unique = sorted(set(values), reverse=True)
            best = unique[0]
            second = unique[1] if len(unique) > 1 else None
            for offset, value in enumerate(values):
                cell = table.cell(first_row + offset, col)
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        if value == best:
                            run.bold = True
                        elif second is not None and value == second:
                            run.underline = True

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(4)
    note.paragraph_format.line_spacing = 1.0
    set_run_font(
        note.add_run(
            "注：数值为逐编码二分类指标（%），在 300 条事故上计算。"
            "F1 = 2TP/(2TP+FP+FN)，召回率 = TP/(TP+FN)，精确率 = TP/(TP+FP)，"
            "准确率 = (TP+TN)/300。Ave(17) 为 M1–M5、F1–F4、T1–T4、H1–H4 共 17 类的"
            "未加权宏平均；每个指标块内每列最优加粗、次优加下划线。"
            "后果放大层 C1、C2 因 GT 正例数偏少或极少（C1=40、C2=2）未列入本表。"
        ),
        7.0,
        False,
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


# ---------------------------------------------------------------- 预览

def write_preview(rows: list[list[str]], path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager, rcParams

    for font_path in (
        "/home/suntianhao/.fonts/NotoSansSC-Regular.ttf",
        "/home/suntianhao/.fonts/NotoSansSC-Bold.ttf",
    ):
        if Path(font_path).is_file():
            font_manager.fontManager.addfont(font_path)
    rcParams["font.family"] = "sans-serif"
    rcParams["font.sans-serif"] = ["Noto Sans SC", "DejaVu Sans"]

    header_total = HEADER_ROWS * HEADER_ROW_HEIGHT_CM
    data_total = DATA_ROWS * DATA_ROW_HEIGHT_CM
    table_height = header_total + data_total
    caption_h = 0.9
    note_h = 2.9
    top_margin = 0.2
    total_height = top_margin + caption_h + table_height + note_h
    table_width = sum(COL_WIDTHS)

    fig = plt.figure(figsize=((table_width + 0.2) / 2.54, total_height / 2.54), dpi=300)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98])
    ax.axis("off")
    ax.set_xlim(0, table_width)
    ax.set_ylim(total_height, 0)

    x_edges = [0.0]
    for width in COL_WIDTHS:
        x_edges.append(x_edges[-1] + width)
    y_top = top_margin + caption_h
    y_header_mid = y_top + HEADER_ROW_HEIGHT_CM
    y_data_top = y_top + header_total
    y_bottom = y_data_top + data_total

    ax.text(
        table_width / 2, top_margin + 0.35,
        "表 X　DeepSeek-V4-Flash 在六种提示策略下的 17 类 CAICS 致因指标（%）",
        ha="center", va="center", fontsize=10.5, fontweight="bold",
    )

    ax.plot([0, table_width], [y_top, y_top], color="black", linewidth=1.5)
    ax.plot([0, table_width], [y_data_top, y_data_top], color="black", linewidth=0.75)
    ax.plot([0, table_width], [y_bottom, y_bottom], color="black", linewidth=1.5)
    for block in range(len(METRICS) - 1):
        y = y_data_top + (block + 1) * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
        ax.plot([0, table_width], [y, y], color="#A6A6A6", linewidth=0.25)

    # 表头
    ax.text(
        (x_edges[0] + x_edges[1]) / 2, y_top + header_total / 2,
        "指标", ha="center", va="center", fontsize=LABEL_FONT_PT, fontweight="bold",
    )
    ax.text(
        (x_edges[1] + x_edges[2]) / 2, y_top + header_total / 2,
        "策略", ha="center", va="center", fontsize=LABEL_FONT_PT, fontweight="bold",
    )
    start = 2
    for label, codes in LAYERS:
        end = start + len(codes)
        ax.text(
            (x_edges[start] + x_edges[end]) / 2,
            y_top + HEADER_ROW_HEIGHT_CM / 2,
            label, ha="center", va="center", fontsize=6.0, fontweight="bold",
        )
        start = end
    ax.text(
        (x_edges[19] + x_edges[20]) / 2, y_top + header_total / 2,
        "Ave(17)", ha="center", va="center", fontsize=HEADER_FONT_PT, fontweight="bold",
    )
    for index, code in enumerate(TABLE_CODES):
        ax.text(
            (x_edges[2 + index] + x_edges[3 + index]) / 2,
            y_header_mid + HEADER_ROW_HEIGHT_CM / 2,
            code, ha="center", va="center", fontsize=HEADER_FONT_PT, fontweight="bold",
        )

    # 数据
    for block, (_metric_key, metric_label) in enumerate(METRICS):
        y_block_top = y_data_top + block * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
        ax.text(
            (x_edges[0] + x_edges[1]) / 2,
            y_block_top + (len(STRATEGIES) * DATA_ROW_HEIGHT_CM) / 2,
            metric_label, ha="center", va="center",
            fontsize=LABEL_FONT_PT, fontweight="bold",
        )
        for offset, strategy in enumerate(STRATEGIES):
            y_center = y_block_top + (offset + 0.5) * DATA_ROW_HEIGHT_CM
            ax.text(
                (x_edges[1] + x_edges[2]) / 2, y_center,
                STRATEGY_LABELS[strategy], ha="center", va="center",
                fontsize=LABEL_FONT_PT,
            )
            for col, value in enumerate(
                rows[block * len(STRATEGIES) + offset][2:], start=2
            ):
                ax.text(
                    (x_edges[col] + x_edges[col + 1]) / 2, y_center, value,
                    ha="center", va="center", fontsize=BODY_FONT_PT,
                    fontfamily="Liberation Serif",
                )

    # 每个指标块内最优加粗、次优下划线
    for block in range(len(METRICS)):
        block_rows = rows[block * len(STRATEGIES):(block + 1) * len(STRATEGIES)]
        for col in range(2, len(COL_WIDTHS)):
            values = [float(row[col]) for row in block_rows]
            unique = sorted(set(values), reverse=True)
            best = unique[0]
            second = unique[1] if len(unique) > 1 else None
            for offset, value in enumerate(values):
                y_center = (
                    y_data_top
                    + block * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
                    + (offset + 0.5) * DATA_ROW_HEIGHT_CM
                )
                x_center = (x_edges[col] + x_edges[col + 1]) / 2
                if value == best:
                    ax.text(
                        x_center, y_center, f"{value:.{DECIMALS}f}",
                        ha="center", va="center", fontsize=BODY_FONT_PT,
                        fontweight="bold", fontfamily="Liberation Serif",
                    )
                elif second is not None and value == second:
                    ax.plot(
                        [x_center - 0.20, x_center + 0.20],
                        [y_center + 0.12, y_center + 0.12],
                        color="black", linewidth=0.55,
                    )

    note_lines = [
        "注：数值为逐编码二分类指标（%），在 300 条事故上计算。F1 = 2TP/(2TP+FP+FN)，",
        "召回率 = TP/(TP+FN)，精确率 = TP/(TP+FP)，准确率 = (TP+TN)/300。Ave(17) 为",
        "M1–M5、F1–F4、T1–T4、H1–H4 共 17 类的未加权宏平均；每个指标块内每列最优",
        "加粗、次优加下划线。C1、C2 因 GT 正例数偏少或极少（C1=40、C2=2）未列入本表。",
    ]
    for index, line in enumerate(note_lines):
        ax.text(
            0.1, y_bottom + 0.45 + index * 0.36, line,
            ha="left", va="center", fontsize=6.8,
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=300, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- CSV / 校验

def write_csv(evaluation: dict, path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["strategy", "code", "F1", "Recall", "Precision", "Accuracy"])
        for strategy in STRATEGIES:
            for code in TABLE_CODES:
                row = evaluation["data"][(strategy, code)]
                writer.writerow([
                    strategy, code,
                    f"{row['F1'] * 100:.2f}",
                    f"{row['Recall'] * 100:.2f}",
                    f"{row['Precision'] * 100:.2f}",
                    f"{row['Accuracy'] * 100:.2f}",
                ])
            writer.writerow([
                strategy, "ALL_17",
                f"{evaluation['macro'][(strategy, 'F1')] * 100:.2f}",
                f"{evaluation['macro'][(strategy, 'Recall')] * 100:.2f}",
                f"{evaluation['macro'][(strategy, 'Precision')] * 100:.2f}",
                f"{evaluation['macro'][(strategy, 'Accuracy')] * 100:.2f}",
            ])


def validate(evaluation: dict, rows: list[list[str]], pred_root: Path) -> None:
    # 宏平均 = 17 个编码的算术平均
    for strategy in STRATEGIES:
        for metric_key, _label in METRICS:
            values = [
                evaluation["data"][(strategy, code)][metric_key]
                for code in TABLE_CODES
            ]
            macro = evaluation["macro"][(strategy, metric_key)]
            if abs(macro - sum(values) / len(values)) > 1e-12:
                raise AssertionError(f"{strategy} {metric_key} 宏平均不一致")
    # 与既有 CSV 的 F1/P/R 交叉校验（2 位小数）
    existing = ROOT / "experiments/golden_standard/eval_suntianhao/f1_per_code.csv"
    if existing.is_file():
        with existing.open(encoding="utf-8-sig") as handle:
            old = {
                (r["model"], r["strategy"], c): r
                for r in csv.DictReader(handle)
                for c in TABLE_CODES
            }
        for strategy in STRATEGIES:
            for code in TABLE_CODES:
                row = old.get((MODEL_NAME, strategy, code))
                if row is None:
                    continue
                for metric_key, col in (("F1", code), ("Recall", f"{code}_R"),
                                        ("Precision", f"{code}_P")):
                    got = evaluation["data"][(strategy, code)][metric_key] * 100
                    if abs(got - float(row[col])) > 0.011:
                        raise AssertionError(
                            f"{strategy}/{code}/{metric_key} 与既有 CSV 不一致："
                            f"{got:.2f} vs {row[col]}"
                        )
    assert len(rows) == DATA_ROWS
    assert abs(sum(COL_WIDTHS) - 15.20) < 1e-9


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepSeek Flash 四指标三线表")
    parser.add_argument("--gt", default=str(DEFAULT_GT))
    parser.add_argument("--pred-root", default=str(DEFAULT_PRED_ROOT))
    parser.add_argument("--out-dir", default=str(OUT_DIR))
    args = parser.parse_args()

    gt_path = Path(args.gt).expanduser().resolve()
    pred_root = Path(args.pred_root).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()

    gt = load_gt(gt_path)
    evaluation = evaluate(gt, pred_root)
    rows = table_values(evaluation)
    validate(evaluation, rows, pred_root)

    out_dir.mkdir(parents=True, exist_ok=True)
    write_docx(rows, out_dir / "flash_metrics_17codes.docx")
    write_preview(rows, out_dir / "flash_metrics_17codes_preview.png")
    write_csv(evaluation, out_dir / "flash_metrics_17codes.csv")

    print(f"GT：{gt_path}（{len(gt)} 条）")
    print(f"模型：{MODEL_NAME}；指标：F1 / 召回率 / 精确率 / 准确率")
    print(f"已写入：{out_dir}")


if __name__ == "__main__":
    main()
