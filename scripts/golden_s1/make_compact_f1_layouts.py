"""把 24×19 的宽 F1 表改造成适合论文纵向版面的三种布局。

输入：``experiments/golden_standard/eval_suntianhao/f1_per_code.csv``
输出（``.../eval_suntianhao/layouts/``）：

- ``f1_summary.docx`` / ``f1_summary.png``：
  正文主表拆成两张 6 列小表——表 1 Ave+、表 2 Ave；
  行 = 6 种策略，列 = 4 个模型 + 四模型均值。
- ``f1_heatmap_panels.png`` / ``.pdf``：
  2×2 分面热图，每个模型 19 编码（行）× 6 策略（列），格内标数值。
- ``f1_by_model_appendix.docx``：
  附录表，每个模型一张 19 行 × 7 列纵向表（编码 + S0–S5），
  表末附 Ave / Ave+ 两行。

用法：
    python3 scripts/golden_s1/make_compact_f1_layouts.py
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IN_DIR = ROOT / "experiments/golden_standard/eval_suntianhao"
CSV_PATH = IN_DIR / "f1_per_code.csv"
OUT_DIR = IN_DIR / "layouts"

ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + [
    f"T{i}" for i in range(1, 5)
] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]
STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]
MODELS = ["GLM-5.2", "DeepSeek-V4-Flash", "混元 hy3", "DeepSeek-V4-Pro"]
SHORT_MODELS = ["GLM-5.2", "DeepSeek Flash", "混元 hy3", "DeepSeek Pro"]
STRATEGY_LABELS = {
    "S0": "零样本",
    "S1": "少样本",
    "S2": "自动链式思维",
    "S3": "规划与求解",
    "S4": "CAICS-CoT",
    "S5": "CAICS-CoT+",
}
STRATEGY_NOTE = (
    "S0 零样本、S1 少样本、S2 自动链式思维、S3 规划与求解、"
    "S4 CAICS-CoT、S5 CAICS-CoT+。"
)


def load_rows() -> dict[tuple[str, str], dict[str, float]]:
    data: dict[tuple[str, str], dict[str, float]] = {}
    with CSV_PATH.open(encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            key = (row["model"], row["strategy"])
            data[key] = {code: float(row[code]) for code in ALL_CODES}
            data[key]["Ave"] = float(row["Ave"])
            data[key]["Ave+"] = float(row["Ave+"])
    missing = [
        (model, strategy)
        for model in MODELS
        for strategy in STRATEGIES
        if (model, strategy) not in data
    ]
    if missing:
        raise ValueError(f"CSV 缺少 {len(missing)} 格：{missing[:3]}")
    return data


def setup_matplotlib():
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
    available = {f.name for f in font_manager.fontManager.ttflist}
    cjk = [f for f in ("Noto Sans SC", "WenQuanYi Zen Hei") if f in available]
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
    return plt


# ---------------------------------------------------------------- DOCX 小工具

def docx_helpers():
    from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    def font(run, size=8, bold=False):
        run.font.name = "宋体"
        run.font.size = Pt(size)
        run.bold = bold
        run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    def style_cell(cell, text, size=8, bold=False):
        cell.text = text
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        for run in paragraph.runs:
            font(run, size, bold)

    def replace_tbl_child(tbl_pr, tag):
        for child in tbl_pr.findall(qn(tag)):
            tbl_pr.remove(child)
        element = OxmlElement(tag)
        tbl_pr.append(element)
        return element

    def three_line_borders(table):
        borders = replace_tbl_child(table._tbl.tblPr, "w:tblBorders")
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

    def set_widths(table, widths_cm):
        for row in table.rows:
            for index, width in enumerate(widths_cm):
                row.cells[index].width = Cm(width)

    def new_header(table, headers, size, widths_cm):
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        table.autofit = False
        for index, header in enumerate(headers):
            cell = table.rows[0].cells[index]
            style_cell(cell, header, size, bold=True)
            cell_border(cell, "top", 6)
            cell_border(cell, "bottom", 6)
        three_line_borders(table)
        set_widths(table, widths_cm)

    def highlight(table, rows, columns, start_row=1):
        for col in columns:
            values = sorted({float(row[col]) for row in rows}, reverse=True)
            best = values[0]
            second = values[1] if len(values) > 1 else None
            for row_index, row_values in enumerate(rows, start=start_row):
                value = float(row_values[col])
                for run in table.cell(row_index, col).paragraphs[0].runs:
                    if value == best:
                        run.bold = True
                    elif second is not None and value == second:
                        run.underline = True

    return (
        font,
        style_cell,
        three_line_borders,
        cell_border,
        set_widths,
        new_header,
        highlight,
        WD_ALIGN_PARAGRAPH,
    )


# ---------------------------------------------------------------- 正文主表

def summary_rows(data: dict, metric: str) -> list[list[str]]:
    rows = []
    for strategy in STRATEGIES:
        values = [data[(model, strategy)][metric] for model in MODELS]
        mean = sum(values) / len(values)
        rows.append(
            [f"{strategy} {STRATEGY_LABELS[strategy]}"]
            + [f"{value:.2f}" for value in values]
            + [f"{mean:.2f}"]
        )
    return rows


def write_summary(data: dict, plt) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Cm, Pt

    (
        font,
        style_cell,
        _three_line_borders,
        cell_border,
        set_widths,
        new_header,
        highlight,
        _align,
    ) = docx_helpers()

    headers = ["策略", *SHORT_MODELS, "均值"]
    widths = [3.8, 2.1, 2.6, 2.0, 2.4, 1.6]
    specs = [
        (
            "Ave+",
            "表 1　六种提示策略在四个模型上的 Ave+ F1（%）",
            "注：Ave+ 为 GT 正例数 ≥5 的 18 类宏平均（排除仅 2 个正例的 C2）；"
            "每列最优加粗、次优加下划线。DeepSeek Flash / Pro 分别指 "
            "DeepSeek-V4-Flash / DeepSeek-V4-Pro。",
        ),
        (
            "Ave",
            "表 2　六种提示策略在四个模型上的 Ave F1（%）",
            "注：Ave 为 19 类宏平均；每列最优加粗、次优加下划线。",
        ),
    ]

    doc = Document()
    section = doc.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.0)
    section.top_margin = section.bottom_margin = Cm(2.0)

    for index, (metric, caption, note) in enumerate(specs):
        if index:
            spacer = doc.add_paragraph()
            spacer.paragraph_format.space_after = Pt(18)
        title = doc.add_paragraph()
        title.paragraph_format.space_after = Pt(4)
        font(title.add_run(caption), 10.5, True)

        rows = summary_rows(data, metric)
        table = doc.add_table(rows=1, cols=len(headers))
        new_header(table, headers, 8.5, widths)
        for row_values in rows:
            cells = table.add_row().cells
            for cell_index, value in enumerate(row_values):
                style_cell(cells[cell_index], value, 8.5)
        for cell in table.rows[-1].cells:
            cell_border(cell, "bottom", 12)
        highlight(table, rows, range(1, len(headers)))
        set_widths(table, widths)

        note_paragraph = doc.add_paragraph()
        note_paragraph.paragraph_format.space_before = Pt(4)
        font(note_paragraph.add_run(note), 8)

    doc.save(OUT_DIR / "f1_summary.docx")

    # ---- PNG：上下两张小表 ----
    fig = plt.figure(figsize=(8.6, 5.4), dpi=300)
    positions = [(0.02, 0.53, 0.96, 0.40), (0.02, 0.05, 0.96, 0.40)]
    for (metric, caption, _note), bounds in zip(specs, positions):
        ax = fig.add_axes(bounds)
        ax.axis("off")
        rows = summary_rows(data, metric)
        col_widths = [0.31, 0.15, 0.18, 0.14, 0.17, 0.10]
        table = ax.table(
            cellText=[headers] + rows,
            cellLoc="center",
            loc="center",
            colWidths=col_widths,
        )
        table.auto_set_font_size(False)
        table.set_fontsize(8.4)
        table.scale(1, 1.55)
        best_second = {
            col: sorted({float(row[col]) for row in rows}, reverse=True)[:2]
            for col in range(1, len(headers))
        }
        for (row_index, col_index), cell in table.get_celld().items():
            cell.set_linewidth(0)
            cell.visible_edges = ""
            cell.set_facecolor("white")
            text = cell.get_text()
            text.set_fontsize(8.4)
            if row_index == 0:
                text.set_fontweight("bold")
                cell.visible_edges = "BT"
                cell.set_linewidth(1.0)
            elif row_index == len(rows):
                cell.visible_edges = "B"
                cell.set_linewidth(1.0)
            if row_index > 0 and col_index >= 1:
                value = float(text.get_text())
                vals = best_second[col_index]
                if value == vals[0]:
                    text.set_fontweight("bold")
        ax.set_title(caption, fontsize=10, loc="left", pad=4)
    fig.text(
        0.02,
        0.005,
        "注：Ave 为 19 类宏平均；Ave+ 为 GT 正例数 ≥5 的 18 类宏平均（排除 C2）。"
        "每列最优加粗。",
        fontsize=7,
    )
    fig.savefig(OUT_DIR / "f1_summary.png", dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 分面热图

def write_heatmap(data: dict, plt) -> None:
    import numpy as np
    from matplotlib.colors import LinearSegmentedColormap

    cmap = LinearSegmentedColormap.from_list(
        "f1", ["#f7fbff", "#c6dbef", "#6baed6", "#2171b5", "#08306b"]
    )
    fig, axes = plt.subplots(2, 2, figsize=(8.0, 8.8), dpi=300, sharey=True)
    vmin, vmax = 20, 90
    images = []
    for ax, model, short in zip(axes.flat, MODELS, SHORT_MODELS):
        matrix = np.array(
            [[data[(model, strategy)][code] for strategy in STRATEGIES] for code in ALL_CODES]
        )
        image = ax.imshow(matrix, cmap=cmap, vmin=vmin, vmax=vmax, aspect="auto")
        images.append(image)
        ax.set_xticks(range(len(STRATEGIES)))
        ax.set_xticklabels(STRATEGIES, fontsize=8)
        ax.set_yticks(range(len(ALL_CODES)))
        ax.set_yticklabels(ALL_CODES, fontsize=7.5)
        ax.set_title(short, fontsize=10, pad=6)
        ax.tick_params(length=0)
        for spine in ax.spines.values():
            spine.set_visible(False)
        for i in range(len(ALL_CODES)):
            for j in range(len(STRATEGIES)):
                value = matrix[i, j]
                color = "white" if value >= 62 else "#222222"
                ax.text(
                    j, i, f"{value:.0f}", ha="center", va="center",
                    fontsize=5.9, color=color,
                )
        ax.set_xticks(np.arange(-0.5, len(STRATEGIES), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(ALL_CODES), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=0.6)
        ax.tick_params(which="minor", length=0)

    fig.suptitle(
        "19 类 CAICS 编码 F1（%）：四个模型 × 六种提示策略",
        fontsize=11, x=0.02, ha="left", y=0.995,
    )
    fig.subplots_adjust(left=0.10, right=0.90, top=0.94, bottom=0.06, hspace=0.18, wspace=0.08)
    cbar_ax = fig.add_axes([0.92, 0.15, 0.015, 0.70])
    colorbar = fig.colorbar(images[0], cax=cbar_ax)
    colorbar.set_label("F1 (%)", fontsize=8.5)
    colorbar.ax.tick_params(labelsize=7)
    fig.text(
        0.10, 0.012,
        f"注：数值为逐编码二分类 F1（%）；颜色越深表示 F1 越高。{STRATEGY_NOTE}"
        "C2 仅 2 个正例，其 F1 仅供参考。",
        fontsize=7, ha="left",
    )
    fig.savefig(OUT_DIR / "f1_heatmap_panels.png", dpi=300, facecolor="white")
    fig.savefig(OUT_DIR / "f1_heatmap_panels.pdf", facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 附录分模型表

def write_appendix_tables(data: dict) -> None:
    from docx import Document
    from docx.shared import Cm, Pt

    (
        font,
        style_cell,
        _three_line_borders,
        cell_border,
        set_widths,
        new_header,
        highlight,
        _align,
    ) = docx_helpers()

    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = section.right_margin = Cm(2.2)
    section.top_margin = section.bottom_margin = Cm(2.0)

    headers = ["编码", *STRATEGIES]
    widths = [2.4] + [2.2] * len(STRATEGIES)

    for table_index, (model, short) in enumerate(zip(MODELS, SHORT_MODELS), start=1):
        if table_index > 1:
            doc.add_page_break()
        title = doc.add_paragraph()
        title.paragraph_format.space_after = Pt(4)
        font(
            title.add_run(
                f"表 A{table_index}　{short} 在六种提示策略下的 19 类编码 F1（%）"
            ),
            10.5,
            True,
        )

        rows = [
            [code] + [f"{data[(model, strategy)][code]:.2f}" for strategy in STRATEGIES]
            for code in ALL_CODES
        ]
        table = doc.add_table(rows=1, cols=len(headers))
        new_header(table, headers, 8.5, widths)
        for row_values in rows:
            cells = table.add_row().cells
            for cell_index, value in enumerate(row_values):
                style_cell(cells[cell_index], value, 8.5)
        highlight(table, rows, range(1, len(headers)), start_row=1)

        # 表末 Ave / Ave+ 两行
        for metric in ("Ave", "Ave+"):
            values = [
                f"{data[(model, strategy)][metric]:.2f}" for strategy in STRATEGIES
            ]
            cells = table.add_row().cells
            style_cell(cells[0], metric, 8.5, bold=True)
            for cell_index, value in enumerate(values, start=1):
                style_cell(cells[cell_index], value, 8.5, bold=True)
            for cell in cells:
                cell_border(cell, "top", 4)

        for cell in table.rows[-1].cells:
            cell_border(cell, "bottom", 12)
        set_widths(table, widths)

        note = doc.add_paragraph()
        note.paragraph_format.space_before = Pt(4)
        font(
            note.add_run(
                f"注：{STRATEGY_NOTE} 表末两行为该模型在 19 类上的 Ave / Ave+；"
                "每列最优加粗、次优加下划线（仅对 19 个编码行）。"
                "C2 仅 2 个正例，其 F1 仅供参考。"
            ),
            7.5,
        )

    doc.save(OUT_DIR / "f1_by_model_appendix.docx")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    data = load_rows()
    plt = setup_matplotlib()
    write_summary(data, plt)
    write_heatmap(data, plt)
    write_appendix_tables(data)
    print(f"已写入：{OUT_DIR}")
    for path in sorted(OUT_DIR.iterdir()):
        print(f"  {path.name}")


if __name__ == "__main__":
    main()
