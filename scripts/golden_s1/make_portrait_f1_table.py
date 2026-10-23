"""生成 Letter 纵向、15.20 cm 宽的 4 模型 × 6 策略 × 17 编码 F1 三线表（Word）。

论文稿 ``paper/eswa_paper_10.22`` 为 Letter 纸、左右页边距 3.17 cm，
正文宽 15.24 cm；本表按 15.20 cm 设计，留 0.04 cm 余量。

设计要点：
- 删除后果放大层（C1、C2），只保留 M/F/T/H 四层 17 个编码；
- 20 列（模型、提示、17 编码、Ave(17)）× 26 行（2 行表头 + 24 行数据）；
- 总宽严格 15.20 cm，编码列各 0.615 cm，单元格左右边距为 0；
- 删除 C 层后横向空间增加，F1 恢复为 2 位小数（7pt）；
- 双层表头：M/F/T/H 四个层组跨列；
- Ave(17) = 17 类宏平均（C 层不列入，表注说明）；
- 每列最优加粗、次优下划线；模型名纵向合并 6 行；
- 三线边框，模型组之间可选 0.25pt 浅灰细线。

输出：
- ``paper/f1_three_line_portrait.docx``
- ``paper/f1_three_line_portrait_preview.png``（15.2 cm 宽预览）
- ``paper/README.md``（列宽与 Word 操作说明）

用法：
    python3 scripts/golden_s1/make_portrait_f1_table.py
"""
from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EVAL_DIR = ROOT / "experiments/golden_standard/eval_suntianhao"
CSV_PATH = EVAL_DIR / "f1_per_code.csv"
OUT_DIR = EVAL_DIR / "paper"

ALL_CODES = [f"M{i}" for i in range(1, 6)] + [f"F{i}" for i in range(1, 5)] + [
    f"T{i}" for i in range(1, 5)
] + [f"H{i}" for i in range(1, 5)] + ["C1", "C2"]
AVE_CODES_17 = [code for code in ALL_CODES if code not in {"C1", "C2"}]
# 论文表删除后果放大层（C1、C2），只展示 17 个编码。
TABLE_CODES = AVE_CODES_17
STRATEGIES = ["S0", "S1", "S2", "S3", "S4", "S5"]
STRATEGY_LABELS = {
    "S0": "S0 零样本",
    "S1": "S1 少样本",
    "S2": "S2 自动链式思维",
    "S3": "S3 规划与求解",
    "S4": "S4 CAICS-CoT",
    "S5": "S5 CAICS-CoT+",
}
MODEL_ORDER = [
    ("GLM-5.2", ["GLM-5.2"]),
    ("DeepSeek-V4-Flash", ["DeepSeek-V4", "Flash"]),
    ("混元 hy3", ["混元 hy3"]),
    ("DeepSeek-V4-Pro", ["DeepSeek-V4", "Pro"]),
]
LAYERS = [
    ("管理 M", ["M1", "M2", "M3", "M4", "M5"]),
    ("防线 F", ["F1", "F2", "F3", "F4"]),
    ("触发 T", ["T1", "T2", "T3", "T4"]),
    ("行为 H", ["H1", "H2", "H3", "H4"]),
]

# 20 列：模型、提示、17 编码、Ave(17)
# 论文实际为 Letter 纸、左右 3.17 cm 页边距 → 正文宽 15.24 cm；表宽留 0.03 cm 余量。
COL_WIDTHS = [1.55, 2.12] + [0.615] * 17 + [1.08]
assert abs(sum(COL_WIDTHS) - 15.205) < 1e-9, sum(COL_WIDTHS)

HEADER_ROWS = 2
DATA_ROWS = 24
TABLE_ROWS = HEADER_ROWS + DATA_ROWS
DATA_ROW_HEIGHT_CM = 0.48
HEADER_ROW_HEIGHT_CM = 0.42
TABLE_WIDTH_CM = sum(COL_WIDTHS)
TEXT_WIDTH_CM = 15.24
# 召回率存在大量 100.00，6 个字符在 0.615 cm 列内会重叠；
# F1 与召回率统一保留 1 位小数，完整 2 位小数见 f1_per_code.csv。
DECIMALS = 1
BODY_FONT_PT = 7.0
LABEL_FONT_PT = 7.0
HEADER_FONT_PT = 6.5

METRIC_SPECS = {
    "F1": {
        "label": "F1",
        "caption": "F1（%）",
        "stem": "f1",
        "code_key": lambda code: code,
        "ave": lambda row: float(row["Ave17"]),
    },
    "Recall": {
        "label": "召回率",
        "caption": "召回率（%）",
        "stem": "recall",
        "code_key": lambda code: f"{code}_R",
        "ave": lambda row: sum(
            float(row[f"{code}_R"]) for code in TABLE_CODES
        ) / len(TABLE_CODES),
    },
}


def note_text(label: str) -> str:
    return (
        f"注：数值为逐编码二分类{label}（%），保留 1 位小数，在 300 条事故上计算。Ave(17) 为 "
        "M1–M5、F1–F4、T1–T4、H1–H4 共 17 类的未加权宏平均；每列最优加粗、"
        "次优加下划线。后果放大层 C1、C2 因 GT 正例数偏少或极少（C1=40、C2=2）、"
        f"{label}波动大，未列入本表。M 管理根基、F 防线失效、T 触发条件、"
        "H 人的行为。DeepSeek-V4 Flash/Pro 即 DeepSeek-V4-Flash/Pro。"
    )


def note_lines(label: str) -> list[str]:
    return [
        f"注：数值为逐编码二分类{label}（%），保留 1 位小数，在 300 条事故上计算。Ave(17) 为 M1–M5、",
        "F1–F4、T1–T4、H1–H4 共 17 类的未加权宏平均；每列最优加粗、次优加下划线。后果放大层 C1、C2",
        f"因 GT 正例数偏少或极少（C1=40、C2=2）、{label}波动大，未列入本表。M 管理根基、F 防线失效、",
        "T 触发条件、H 人的行为。DeepSeek-V4 Flash/Pro 即 DeepSeek-V4-Flash/Pro。",
    ]


def load_rows() -> list[dict]:
    with CSV_PATH.open(encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    expected = len(MODEL_ORDER) * len(STRATEGIES)
    if len(rows) != expected:
        raise ValueError(f"CSV 行数 {len(rows)} != {expected}")
    return rows


def get_row(rows: list[dict], model: str, strategy: str) -> dict:
    for row in rows:
        if row["model"] == model and row["strategy"] == strategy:
            return row
    raise KeyError((model, strategy))


def matrix_values(rows: list[dict], metric: str) -> list[list[float]]:
    """24 行 × 18 数值列（17 编码 + Ave17），按模型块、S0–S5 顺序。"""
    spec = METRIC_SPECS[metric]
    out = []
    for model, _lines in MODEL_ORDER:
        for strategy in STRATEGIES:
            row = get_row(rows, model, strategy)
            values = [float(row[spec["code_key"](code)]) for code in TABLE_CODES]
            values.append(spec["ave"](row))
            out.append(values)
    return out


# ---------------------------------------------------------------- DOCX 工具

def docx_helpers():
    from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt

    def set_run_font(run, size=7, bold=False, latin="Times New Roman", east="宋体"):
        run.font.name = latin
        run.font.size = Pt(size)
        run.bold = bold
        run._element.rPr.rFonts.set(qn("w:eastAsia"), east)

    def set_cell_text(cell, text, size=7, bold=False, align=WD_ALIGN_PARAGRAPH.CENTER):
        cell.text = ""
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = align
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        run = paragraph.add_run(text)
        set_run_font(run, size, bold)
        return run

    def set_cell_lines(cell, lines, size=7, bold=False):
        cell.text = ""
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        paragraph.paragraph_format.space_before = Pt(0)
        paragraph.paragraph_format.space_after = Pt(0)
        paragraph.paragraph_format.line_spacing = 1.0
        for index, line in enumerate(lines):
            run = paragraph.add_run(line)
            set_run_font(run, size, bold)
            if index < len(lines) - 1:
                run.add_break()

    def set_cell_width(cell, width_cm):
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_w = tc_pr.find(qn("w:tcW"))
        if tc_w is None:
            tc_w = OxmlElement("w:tcW")
            tc_pr.append(tc_w)
        tc_w.set(qn("w:w"), str(int(round(width_cm * 567))))
        tc_w.set(qn("w:type"), "dxa")

    def set_grid_widths(table, widths_cm):
        """同步 w:tblGrid/gridCol；固定布局下 Word 按 grid 渲染列宽。"""
        grid = table._tbl.tblGrid
        cols = grid.findall(qn("w:gridCol"))
        if len(cols) != len(widths_cm):
            raise ValueError(f"gridCol 数量 {len(cols)} != {len(widths_cm)}")
        for col, width_cm in zip(cols, widths_cm):
            col.set(qn("w:w"), str(int(round(width_cm * 567))))

    def set_table_layout(table, total_cm):
        tbl_pr = table._tbl.tblPr
        # 固定宽度
        for tag in ("w:tblW", "w:tblLayout", "w:tblCellMar", "w:tblBorders"):
            for child in tbl_pr.findall(qn(tag)):
                tbl_pr.remove(child)
        # 顺序遵循 OOXML：tblW → tblBorders → tblLayout → tblCellMar
        tbl_w = OxmlElement("w:tblW")
        tbl_w.set(qn("w:w"), str(int(round(total_cm * 567))))
        tbl_w.set(qn("w:type"), "dxa")
        tbl_pr.append(tbl_w)
        borders = OxmlElement("w:tblBorders")
        for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
            element = OxmlElement(f"w:{edge}")
            element.set(qn("w:val"), "none")
            element.set(qn("w:sz"), "0")
            element.set(qn("w:color"), "000000")
            borders.append(element)
        tbl_pr.append(borders)
        layout = OxmlElement("w:tblLayout")
        layout.set(qn("w:type"), "fixed")
        tbl_pr.append(layout)
        cell_mar = OxmlElement("w:tblCellMar")
        for edge, value in (("top", 11), ("left", 0), ("bottom", 11), ("right", 0)):
            element = OxmlElement(f"w:{edge}")
            element.set(qn("w:w"), str(value))
            element.set(qn("w:type"), "dxa")
            cell_mar.append(element)
        tbl_pr.append(cell_mar)
        # 按 OOXML schema 重排 tblPr 子元素，避免严格校验报错
        order = [
            "tblStyle", "tblpPr", "tblOverlap", "bidiVisual",
            "tblStyleRowBandSize", "tblStyleColBandSize", "tblW", "jc",
            "tblCellSpacing", "tblInd", "tblBorders", "shd", "tblLayout",
            "tblCellMar", "tblLook", "tblCaption", "tblDescription",
        ]
        children = list(tbl_pr)
        children.sort(
            key=lambda child: order.index(child.tag.split("}")[-1])
            if child.tag.split("}")[-1] in order else len(order)
        )
        for child in children:
            tbl_pr.remove(child)
            tbl_pr.append(child)

    def set_row_height(row, height_cm):
        tr_pr = row._tr.get_or_add_trPr()
        # OOXML 顺序：cantSplit → trHeight → tblHeader
        if tr_pr.find(qn("w:cantSplit")) is None:
            tr_pr.append(OxmlElement("w:cantSplit"))
        for child in tr_pr.findall(qn("w:trHeight")):
            tr_pr.remove(child)
        tr_height = OxmlElement("w:trHeight")
        tr_height.set(qn("w:val"), str(int(round(height_cm * 567))))
        tr_height.set(qn("w:hRule"), "exact")
        tr_pr.append(tr_height)

    def set_repeat_header(row):
        tr_pr = row._tr.get_or_add_trPr()
        if tr_pr.find(qn("w:tblHeader")) is None:
            tr_pr.append(OxmlElement("w:tblHeader"))

    def cell_border(cell, edge, sz, color="000000"):
        tc_pr = cell._tc.get_or_add_tcPr()
        tc_borders = tc_pr.first_child_found_in("w:tcBorders")
        if tc_borders is None:
            tc_borders = OxmlElement("w:tcBorders")
            tc_pr.append(tc_borders)
        for child in tc_borders.findall(qn(f"w:{edge}")):
            tc_borders.remove(child)
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(sz))
        element.set(qn("w:color"), color)
        tc_borders.append(element)

    return {
        "Cm": Cm,
        "Pt": Pt,
        "WD_ALIGN_PARAGRAPH": WD_ALIGN_PARAGRAPH,
        "WD_TABLE_ALIGNMENT": WD_TABLE_ALIGNMENT,
        "set_cell_text": set_cell_text,
        "set_cell_lines": set_cell_lines,
        "set_run_font": set_run_font,
        "set_cell_width": set_cell_width,
        "set_grid_widths": set_grid_widths,
        "set_table_layout": set_table_layout,
        "set_row_height": set_row_height,
        "set_repeat_header": set_repeat_header,
        "cell_border": cell_border,
    }


def write_docx(rows: list[dict], values: list[list[float]], path: Path,
               metric: str) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.enum.table import WD_TABLE_ALIGNMENT

    spec = METRIC_SPECS[metric]
    h = docx_helpers()
    Cm = h["Cm"]
    Pt = h["Pt"]
    set_cell_text = h["set_cell_text"]
    set_cell_lines = h["set_cell_lines"]
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
    # 与论文稿 paper/eswa_paper_10.22 保持一致：Letter，左右 3.17 cm，上下 2.54 cm
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
            f"表 X　四种模型 × 六种提示策略在 17 类 CAICS 致因编码上的 {spec['caption']}"
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
    table.cell(0, 0).merge(table.cell(1, 0))  # 模型
    table.cell(0, 1).merge(table.cell(1, 1))  # 提示
    table.cell(0, 19).merge(table.cell(1, 19))  # Ave(17)
    start = 2
    for label, codes in LAYERS:
        end = start + len(codes) - 1
        table.cell(0, start).merge(table.cell(0, end))
        start = end + 1

    # 模型列纵向合并
    for block in range(len(MODEL_ORDER)):
        first = HEADER_ROWS + block * len(STRATEGIES)
        last = first + len(STRATEGIES) - 1
        table.cell(first, 0).merge(table.cell(last, 0))

    # 表头文字
    set_cell_text(table.cell(0, 0), "模型", LABEL_FONT_PT, True)
    set_cell_text(table.cell(0, 1), "提示", LABEL_FONT_PT, True)
    start = 2
    for label, codes in LAYERS:
        set_cell_text(table.cell(0, start), label, 6.0, True)
        start += len(codes)
    set_cell_text(table.cell(0, 19), "Ave(17)", HEADER_FONT_PT, True)
    for index, code in enumerate(TABLE_CODES):
        set_cell_text(table.cell(1, 2 + index), code, HEADER_FONT_PT, True)

    # 数据
    for block, (model, model_lines) in enumerate(MODEL_ORDER):
        first = HEADER_ROWS + block * len(STRATEGIES)
        model_cell = table.cell(first, 0)
        set_cell_lines(model_cell, model_lines, LABEL_FONT_PT, True)
        for offset, strategy in enumerate(STRATEGIES):
            row_index = first + offset
            set_cell_text(
                table.cell(row_index, 1), STRATEGY_LABELS[strategy],
                LABEL_FONT_PT, False, CENTER,
            )
            for col, value in enumerate(values[row_index - HEADER_ROWS], start=2):
                set_cell_text(
                    table.cell(row_index, col), f"{value:.{DECIMALS}f}",
                    BODY_FONT_PT, False, CENTER,
                )

    # 行高 / 防跨页 / 表头重复
    for row in table.rows[:HEADER_ROWS]:
        set_row_height(row, HEADER_ROW_HEIGHT_CM)
        set_repeat_header(row)
    for row in table.rows[HEADER_ROWS:]:
        set_row_height(row, DATA_ROW_HEIGHT_CM)

    # 三线：顶线、表头下线、底线
    for cell in table.rows[0].cells:
        cell_border(cell, "top", 12)
    for cell in table.rows[1].cells:
        cell_border(cell, "bottom", 6)
    for cell in table.rows[-1].cells:
        cell_border(cell, "bottom", 12)

    # 模型组之间：0.25pt 浅灰细线（三个分隔，最后一组不加）
    for block in range(len(MODEL_ORDER) - 1):
        last = HEADER_ROWS + block * len(STRATEGIES) + len(STRATEGIES) - 1
        for cell in table.rows[last].cells:
            cell_border(cell, "bottom", 2, "A6A6A6")

    # 每列最优加粗、次优下划线（19 编码 + Ave(17)）
    for col_index in range(2, len(COL_WIDTHS)):
        column_values = [round(row[col_index - 2], DECIMALS) for row in values]
        unique = sorted(set(column_values), reverse=True)
        best = unique[0]
        second = unique[1] if len(unique) > 1 else None
        for data_index, value in enumerate(column_values):
            cell = table.cell(HEADER_ROWS + data_index, col_index)
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    if value == best:
                        run.bold = True
                    elif second is not None and value == second:
                        run.underline = True

    note = doc.add_paragraph()
    note.paragraph_format.space_before = Pt(4)
    note.paragraph_format.line_spacing = 1.0
    set_run_font(note.add_run(note_text(spec["label"])), 7.0, False)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)


# ---------------------------------------------------------------- 预览图

def write_preview(rows: list[dict], values: list[list[float]], path: Path,
                  metric: str) -> None:
    spec = METRIC_SPECS[metric]
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
    note_h = 2.7
    top_margin = 0.2
    total_height = top_margin + caption_h + table_height + note_h

    fig = plt.figure(figsize=((TABLE_WIDTH_CM + 0.2) / 2.54, total_height / 2.54), dpi=300)
    ax = fig.add_axes([0.01, 0.01, 0.98, 0.98])
    ax.axis("off")
    ax.set_xlim(0, TABLE_WIDTH_CM)
    ax.set_ylim(total_height, 0)

    # x 边界
    x_edges = [0.0]
    for width in COL_WIDTHS:
        x_edges.append(x_edges[-1] + width)
    y_top = top_margin + caption_h
    y_header_mid = y_top + HEADER_ROW_HEIGHT_CM
    y_data_top = y_top + header_total
    y_bottom = y_data_top + data_total

    # 标题
    ax.text(
        TABLE_WIDTH_CM / 2, top_margin + 0.35,
        f"表 X　四种模型 × 六种提示策略在 17 类 CAICS 致因编码上的 {spec['caption']}",
        ha="center", va="center", fontsize=10.5, fontweight="bold",
    )

    # 三线
    ax.plot([0, TABLE_WIDTH_CM], [y_top, y_top], color="black", linewidth=1.5)
    ax.plot([0, TABLE_WIDTH_CM], [y_data_top, y_data_top], color="black", linewidth=0.75)
    ax.plot([0, TABLE_WIDTH_CM], [y_bottom, y_bottom], color="black", linewidth=1.5)
    # 模型组分隔线
    for block in range(len(MODEL_ORDER) - 1):
        y = y_data_top + (block + 1) * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
        ax.plot([0, TABLE_WIDTH_CM], [y, y], color="#A6A6A6", linewidth=0.25)

    # 表头
    ax.text(
        (x_edges[0] + x_edges[1]) / 2,
        y_top + header_total / 2,
        "模型", ha="center", va="center", fontsize=LABEL_FONT_PT, fontweight="bold",
    )
    ax.text(
        (x_edges[1] + x_edges[2]) / 2,
        y_top + header_total / 2,
        "提示", ha="center", va="center", fontsize=LABEL_FONT_PT, fontweight="bold",
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
        (x_edges[19] + x_edges[20]) / 2,
        y_top + header_total / 2,
        "Ave(17)", ha="center", va="center", fontsize=6.5, fontweight="bold",
    )
    for index, code in enumerate(TABLE_CODES):
        ax.text(
            (x_edges[2 + index] + x_edges[3 + index]) / 2,
            y_header_mid + HEADER_ROW_HEIGHT_CM / 2,
            code, ha="center", va="center", fontsize=6.5, fontweight="bold",
        )

    # 数据
    for block, (model, model_lines) in enumerate(MODEL_ORDER):
        y_block_top = y_data_top + block * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
        ax.text(
            (x_edges[0] + x_edges[1]) / 2,
            y_block_top + (len(STRATEGIES) * DATA_ROW_HEIGHT_CM) / 2,
            "\n".join(model_lines),
            ha="center", va="center", fontsize=LABEL_FONT_PT, fontweight="bold",
            linespacing=1.0,
        )
        for offset, strategy in enumerate(STRATEGIES):
            y_center = y_block_top + (offset + 0.5) * DATA_ROW_HEIGHT_CM
            ax.text(
                (x_edges[1] + x_edges[2]) / 2,
                y_center,
                STRATEGY_LABELS[strategy],
                ha="center", va="center", fontsize=LABEL_FONT_PT,
            )
            for col, value in enumerate(values[block * len(STRATEGIES) + offset], start=2):
                x_center = (x_edges[col] + x_edges[col + 1]) / 2
                ax.text(
                    x_center, y_center, f"{value:.{DECIMALS}f}",
                    ha="center", va="center", fontsize=BODY_FONT_PT,
                    fontfamily="Liberation Serif",
                )

    # 最优加粗 / 次优下划线
    for col_index in range(2, len(COL_WIDTHS)):
        column_values = [round(row[col_index - 2], DECIMALS) for row in values]
        unique = sorted(set(column_values), reverse=True)
        best = unique[0]
        second = unique[1] if len(unique) > 1 else None
        for data_index, value in enumerate(column_values):
            block = data_index // len(STRATEGIES)
            offset = data_index % len(STRATEGIES)
            y_center = (
                y_data_top
                + block * len(STRATEGIES) * DATA_ROW_HEIGHT_CM
                + (offset + 0.5) * DATA_ROW_HEIGHT_CM
            )
            x_center = (x_edges[col_index] + x_edges[col_index + 1]) / 2
            if value == best:
                ax.text(
                    x_center, y_center, f"{value:.{DECIMALS}f}",
                    ha="center", va="center", fontsize=BODY_FONT_PT,
                    fontweight="bold", fontfamily="Liberation Serif",
                )
            elif second is not None and value == second:
                ax.plot(
                    [x_center - 0.19, x_center + 0.19],
                    [y_center + 0.12, y_center + 0.12],
                    color="black", linewidth=0.55,
                )

    # 表注（手动分行，宽度按 15.2 cm 估算）
    for index, line in enumerate(note_lines(spec["label"])):
        ax.text(
            0.1, y_bottom + 0.45 + index * 0.36, line,
            ha="left", va="center", fontsize=6.8,
        )

    fig.savefig(path, dpi=300, facecolor="white")
    plt.close(fig)


# ---------------------------------------------------------------- 校验

def validate(rows: list[dict], values: list[list[float]], metric: str) -> None:
    assert len(values) == 24
    for data_index, row_values in enumerate(values):
        mean17 = sum(row_values[:17]) / 17
        csv_row = rows[data_index]
        # F1 的 Ave17 由未四舍五入的 F1 计算，和表中 2 位小数的重新平均允许 ±0.02
        if abs(row_values[-1] - mean17) > 0.02:
            raise AssertionError(
                f"{metric} Ave17 不一致：{csv_row['model']}/{csv_row['strategy']}"
            )
        if metric == "F1" and abs(float(csv_row["Ave17"]) - row_values[-1]) > 1e-9:
            raise AssertionError(
                f"F1 Ave17 与 CSV 不一致：{csv_row['model']}/{csv_row['strategy']}"
            )
    assert abs(sum(COL_WIDTHS) - 15.205) < 1e-9


def main() -> None:
    rows_in_file = load_rows()
    # 按模型块、S0–S5 重新排列 CSV 行，保持与 values 对齐
    ordered_rows = []
    for model, _lines in MODEL_ORDER:
        for strategy in STRATEGIES:
            ordered_rows.append(get_row(rows_in_file, model, strategy))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for metric in ("F1", "Recall"):
        spec = METRIC_SPECS[metric]
        values = matrix_values(ordered_rows, metric)
        validate(ordered_rows, values, metric)
        stem = spec["stem"]
        write_docx(
            ordered_rows, values,
            OUT_DIR / f"{stem}_three_line_portrait.docx", metric,
        )
        write_preview(
            ordered_rows, values,
            OUT_DIR / f"{stem}_three_line_portrait_preview.png", metric,
        )
        print(f"已写入 {spec['label']} 表")
    print(f"输出目录：{OUT_DIR}")


if __name__ == "__main__":
    main()
