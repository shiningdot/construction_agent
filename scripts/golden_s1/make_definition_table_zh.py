"""生成 CAICS 五层十九类致因编码的中文操作定义表。

数据来源：docs/paper_v2/eswa_paper_v2_zh.md 的「表 2. CAICS 编码方案：操作定义」。
输出四列：层、编码、定义、文本示例；不包含频次与样本量。

输出：
- table_caics_definitions_zh.md   Markdown 表格
- table_caics_definitions_zh.csv  UTF-8 BOM，Excel/Word 可直接打开
- table_caics_definitions_zh.docx Word 表格（同层单元格合并）

用法：
    python scripts/golden_s1/make_definition_table_zh.py
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE = ROOT / "docs/paper_v2/eswa_paper_v2_zh.md"
DEFAULT_OUT = ROOT / "experiments/golden_standard/figures"
HEADERS = ["层", "编码", "定义", "文本示例"]


def parse_definition_table(path: Path) -> list[dict[str, str]]:
    """解析论文表 2；层列在后续行留空，需要向下继承。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = next(
            index
            for index, line in enumerate(lines)
            if line.strip().startswith("| 层 | 码 | 类别 |")
        )
    except StopIteration as exc:
        raise SystemExit(f"在 {path} 中找不到表 2 表头") from exc

    rows: list[dict[str, str]] = []
    current_layer = ""
    for line in lines[start + 2 :]:
        if not line.strip().startswith("|"):
            break
        cols = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cols) < 5:
            continue
        layer = cols[0].replace("**", "").strip()
        if layer:
            current_layer = layer
        rows.append(
            {
                "layer": current_layer,
                "code": cols[1],
                "name": cols[2],
                "definition": cols[3],
                "examples": cols[4],
            }
        )
    if len(rows) != 19:
        raise SystemExit(f"期望解析到 19 个编码，实际 {len(rows)} 个")
    return rows


def write_markdown(rows: list[dict[str, str]], path: Path) -> None:
    lines = [
        "# 表 1. CAICS 五层十九类致因编码操作定义",
        "",
        "| 层 | 编码 | 定义 | 文本示例 |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['layer']} | {row['code']} | {row['name']} | {row['examples']} |"
        )
    lines.extend(
        [
            "",
            "注：文本示例为语料中典型证据关键词的概括；完整操作定义见 "
            "`docs/paper_v2/eswa_paper_v2_zh.md` 表 2。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    # utf-8-sig 带 BOM，Excel 直接打开不会乱码
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADERS)
        for row in rows:
            writer.writerow(
                [row["layer"], row["code"], row["name"], row["examples"]]
            )


def _set_run_font(run, size: float = 10.5, bold: bool = False) -> None:
    run.font.name = "宋体"
    run.font.size = Pt(size)
    run.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")


def write_docx(rows: list[dict[str, str]], path: Path) -> None:
    doc = Document()

    # 正文默认宋体五号
    style = doc.styles["Normal"]
    style.font.name = "宋体"
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")

    # 表题
    title = doc.add_paragraph()
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _set_run_font(
        title.add_run("表 1. CAICS 五层十九类致因编码操作定义"),
        size=10.5,
        bold=True,
    )

    table = doc.add_table(rows=1, cols=len(HEADERS))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False

    # 表头
    for index, header in enumerate(HEADERS):
        cell = table.rows[0].cells[index]
        cell.text = header
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
        paragraph = cell.paragraphs[0]
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        for run in paragraph.runs:
            _set_run_font(run, bold=True)

    # 数据行；层名只写在每组首行，后续留空供合并
    for row_index, row in enumerate(rows):
        cells = table.add_row().cells
        layer_text = (
            row["layer"]
            if row_index == 0 or row["layer"] != rows[row_index - 1]["layer"]
            else ""
        )
        values = [layer_text, row["code"], row["name"], row["examples"]]
        for cell, value in zip(cells, values):
            cell.text = value
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    _set_run_font(run)

    # 合并同层单元格
    data_start = 1
    index = 0
    while index < len(rows):
        end = index
        while (
            end + 1 < len(rows)
            and rows[end + 1]["layer"] == rows[index]["layer"]
        ):
            end += 1
        if end > index:
            merged = table.cell(index + data_start, 0).merge(
                table.cell(end + data_start, 0)
            )
            # 合并后重写层名，清掉合并带来的空段落
            merged.text = rows[index]["layer"]
            merged.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for paragraph in merged.paragraphs:
                for run in paragraph.runs:
                    _set_run_font(run)
        index = end + 1

    # 列宽
    widths = [Cm(2.6), Cm(1.2), Cm(3.4), Cm(9.0)]
    for table_row in table.rows:
        for index, width in enumerate(widths):
            table_row.cells[index].width = width

    # 表头跨页重复
    header_row = table.rows[0]._tr
    row_properties = header_row.get_or_add_trPr()
    header_flag = OxmlElement("w:tblHeader")
    header_flag.set(qn("w:val"), "true")
    row_properties.append(header_flag)

    # 表注
    note = doc.add_paragraph()
    _set_run_font(
        note.add_run(
            "注：文本示例为语料中典型证据关键词的概括；完整操作定义见 "
            "docs/paper_v2/eswa_paper_v2_zh.md 表 2。"
        ),
        size=9,
    )

    doc.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 CAICS 中文操作定义表")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="论文表 2 所在 md")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT), help="输出目录")
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = parse_definition_table(source)
    md_path = out_dir / "table_caics_definitions_zh.md"
    csv_path = out_dir / "table_caics_definitions_zh.csv"
    docx_path = out_dir / "table_caics_definitions_zh.docx"

    write_markdown(rows, md_path)
    write_csv(rows, csv_path)
    write_docx(rows, docx_path)

    print(f"解析 {len(rows)} 个编码：{source}")
    for path in (md_path, csv_path, docx_path):
        print(f"  wrote {path} ({path.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
