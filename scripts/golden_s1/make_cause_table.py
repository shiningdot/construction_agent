"""生成 19 类致因编码的科研三线表。

同一份数据输出三种可直接插入论文的形式：
1. LaTeX booktabs 代码（.tex）；
2. Markdown 表格（.md）；
3. 三线表图片（.pdf / .png，可用 Word 直接插入）。

数据源：experiments/golden_standard/figures/figure_data.json 的 `gt.n_gt`，
即专家 GT 在 300 条事故上的逐类正例数。

用法：
    python scripts/golden_s1/make_cause_table.py \
        --data-json experiments/golden_standard/figures/figure_data.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无 GUI 环境
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager, rcParams  # noqa: E402

# 三线表列位置（figure 坐标，0–1）
X_CODE = 0.04
X_CAUSE = 0.12
X_LAYER = 0.56
X_N_RIGHT = 0.82
X_FREQ_RIGHT = 0.97
LINE_LEFT = 0.03
LINE_RIGHT = 0.98


def setup_style() -> None:
    """英文三线表样式；字体按本机可用情况回退。"""
    available = {font.name for font in font_manager.fontManager.ttflist}
    sans = [
        font
        for font in ("Arial", "Helvetica", "Liberation Sans", "DejaVu Sans")
        if font in available
    ]
    rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": sans or ["DejaVu Sans"],
            "font.size": 7,
            "figure.dpi": 300,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "axes.unicode_minus": False,
        }
    )


def build_rows(doc: dict) -> list[dict]:
    """按 CAICS 五层分组，层内按频次降序。"""
    rows: list[dict] = []
    total_ids = doc["gt"]["n_ids"]
    for layer in doc["labels"]["layers"]:
        codes = sorted(
            layer["codes"],
            key=lambda code: doc["gt"]["n_gt"][code],
            reverse=True,
        )
        for code in codes:
            count = doc["gt"]["n_gt"][code]
            rows.append(
                {
                    "code": code,
                    "cause": doc["labels"]["class_display"][code],
                    "layer": layer["name"],
                    "n": count,
                    "freq": count / total_ids * 100 if total_ids else 0.0,
                }
            )
    return rows


def latex_escape(text: str) -> str:
    """转义 LaTeX 特殊字符；当前类别名不含这些字符，保留以防后续替换。"""
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(char, char) for char in text)


def summary(doc: dict, rows: list[dict]) -> tuple[int, float]:
    total = sum(row["n"] for row in rows)
    mean = total / doc["gt"]["n_ids"] if doc["gt"]["n_ids"] else 0.0
    return total, mean


def write_tex(doc: dict, rows: list[dict], path: Path) -> None:
    total, mean = summary(doc, rows)
    lines = [
        "% 需要导言区：\\usepackage{booktabs}",
        "\\begin{table}[htbp]",
        "\\centering",
        "\\caption{Frequency distribution of the 19 CAICS cause codes across "
        f"{doc['gt']['n_ids']} accidents. A value of 1 indicates that the code "
        "was identified as a contributing factor to the accident. "
        f"There are {total:,} code assignments in total "
        f"(mean {mean:.2f} per accident).}}",
        "\\label{tab:caics_cause_distribution}",
        "\\begin{tabular}{@{}lllrr@{}}",
        "\\toprule",
        "Code & Cause & Layer & $n$ & Frequency (\\%) \\\\",
        "\\midrule",
    ]
    for index, row in enumerate(rows):
        if index > 0 and row["layer"] != rows[index - 1]["layer"]:
            lines.append("\\addlinespace")
        lines.append(
            f"{row['code']} & {latex_escape(row['cause'])} & {row['layer']} "
            f"& {row['n']} & {row['freq']:.1f} \\\\"
        )
    lines.extend(
        [
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{table}",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_markdown(doc: dict, rows: list[dict], path: Path) -> None:
    total, mean = summary(doc, rows)
    lines = [
        "| Code | Cause | Layer | n | Frequency (%) |",
        "|---|---|---|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['code']} | {row['cause']} | {row['layer']} "
            f"| {row['n']} | {row['freq']:.1f} |"
        )
    lines.extend(
        [
            "",
            f"Note: n = number of accidents in which the code was identified "
            f"(N = {doc['gt']['n_ids']}). There are {total:,} code assignments "
            f"in total (mean {mean:.2f} per accident).",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def render_image(
    doc: dict,
    rows: list[dict],
    out_dir: Path,
    formats: tuple[str, ...],
    dpi: int,
) -> list[Path]:
    total, mean = summary(doc, rows)
    fig = plt.figure(figsize=(7.2, 5.9), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    # 表题
    ax.text(
        0.04,
        0.975,
        "Table 1. Frequency distribution of the 19 CAICS cause codes.",
        ha="left",
        va="top",
        fontsize=8.2,
        fontweight="bold",
    )

    # 顶线
    header_y = 0.918
    top_y = header_y + 0.024
    ax.plot(
        [LINE_LEFT, LINE_RIGHT],
        [top_y, top_y],
        color="black",
        linewidth=1.2,
        solid_capstyle="butt",
    )

    # 表头
    header_style = {"fontsize": 7.0, "fontweight": "bold", "va": "center"}
    ax.text(X_CODE, header_y, "Code", ha="left", **header_style)
    ax.text(X_CAUSE, header_y, "Cause", ha="left", **header_style)
    ax.text(X_LAYER, header_y, "Layer", ha="left", **header_style)
    ax.text(X_N_RIGHT, header_y, "$n$", ha="right", **header_style)
    ax.text(X_FREQ_RIGHT, header_y, "Frequency (%)", ha="right", **header_style)

    # 表头下线
    mid_y = header_y - 0.030
    ax.plot(
        [LINE_LEFT, LINE_RIGHT],
        [mid_y, mid_y],
        color="black",
        linewidth=0.7,
        solid_capstyle="butt",
    )

    # 数据行
    y = mid_y - 0.034
    row_height = 0.036
    cell_style = {"fontsize": 6.8, "va": "center"}
    for index, row in enumerate(rows):
        if index > 0 and row["layer"] != rows[index - 1]["layer"]:
            y -= 0.012
        ax.text(X_CODE, y, row["code"], ha="left", **cell_style)
        ax.text(X_CAUSE, y, row["cause"], ha="left", **cell_style)
        ax.text(X_LAYER, y, row["layer"], ha="left", **cell_style)
        ax.text(X_N_RIGHT, y, str(row["n"]), ha="right", **cell_style)
        ax.text(
            X_FREQ_RIGHT,
            y,
            f"{row['freq']:.1f}",
            ha="right",
            **cell_style,
        )
        y -= row_height

    # 底线
    bottom_y = y + row_height * 0.55
    ax.plot(
        [LINE_LEFT, LINE_RIGHT],
        [bottom_y, bottom_y],
        color="black",
        linewidth=1.2,
        solid_capstyle="butt",
    )

    # 表注
    ax.text(
        0.04,
        bottom_y - 0.028,
        f"Note: $n$ = number of accidents in which the code was identified "
        f"($N$ = {doc['gt']['n_ids']}). There are {total:,} code assignments "
        f"in total\n(mean {mean:.2f} per accident).",
        ha="left",
        va="top",
        fontsize=5.8,
        linespacing=1.35,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        path = out_dir / f"table_cause_distribution.{fmt}"
        fig.savefig(
            path,
            format=fmt,
            dpi=dpi,
            bbox_inches="tight",
            pad_inches=0.02,
            facecolor="white",
        )
        written.append(path)
        print(f"  wrote {path} ({path.stat().st_size / 1024:.0f} KB)")
    plt.close(fig)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description="生成 19 类致因编码三线表")
    parser.add_argument("--data-json", required=True, help="figure_data.json 路径")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--formats", default="pdf,png", help="图片格式，逗号分隔")
    parser.add_argument("--dpi", type=int, default=300)
    args = parser.parse_args()

    data_path = Path(args.data_json).expanduser().resolve()
    if not data_path.is_file():
        raise SystemExit(f"找不到数据文件：{data_path}")
    out_dir = (
        Path(args.out_dir).expanduser().resolve()
        if args.out_dir
        else data_path.parent
    )
    formats = tuple(
        item.strip().lower() for item in args.formats.split(",") if item.strip()
    )

    with data_path.open(encoding="utf-8") as fh:
        doc = json.load(fh)

    setup_style()
    rows = build_rows(doc)
    if len(rows) != 19:
        raise SystemExit(f"期望 19 个编码，实际 {len(rows)} 个")

    tex_path = out_dir / "table_cause_distribution.tex"
    md_path = out_dir / "table_cause_distribution.md"
    write_tex(doc, rows, tex_path)
    write_markdown(doc, rows, md_path)
    print(f"  wrote {tex_path}")
    print(f"  wrote {md_path}")
    render_image(doc, rows, out_dir, formats, args.dpi)


if __name__ == "__main__":
    main()
