"""绘制论文核心三图（Nature 风格，英文标签，300 DPI）。

三张图：
1. fig1：6 策略 × 4 模型 的 Ave+ F1 热力图（附均值行列）；
2. fig2：19 类 × 6 策略 的 F1 热力图（4 模型均值，按 M/F/T/H/C 分层，
   右侧标注每类 GT 正例数）；
3. fig3：策略级多指标对比（Ave+ F1 / R / P、Micro-F1、Exact-match、
   13 字段基础特征准确率）。

本脚本只读 figure_data.json，不重新计算任何指标。

用法：
    python scripts/golden_s1/plot_paper_figures.py \
        --data-json experiments/golden_standard/figures/figure_data.json
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # 无 GUI 环境；必须在 pyplot 之前
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager, rcParams  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

# 图 1 的模型列标签：两行，避免旋转
MODEL_TICK = {
    "glm5.2": "GLM-5.2",
    "flash": "DeepSeek\nV4 Flash",
    "hy3": "Hunyuan\nhy3",
    "pro": "DeepSeek\nV4 Pro",
}
# 图 3 的模型配色与标记（Okabe–Ito 色盲友好配色）
MODEL_COLORS = {
    "glm5.2": "#0072B2",
    "flash": "#E69F00",
    "hy3": "#009E73",
    "pro": "#CC79A7",
}
MODEL_MARKERS = {"glm5.2": "o", "flash": "s", "hy3": "^", "pro": "D"}
# 图 3 的六个面板
METRICS = [
    ("ave_plus_F1", ("ave_plus", "F1"), "Ave+ F1 (%)", (60, 100)),
    ("ave_plus_R", ("ave_plus", "R"), "Ave+ recall (%)", (60, 100)),
    ("ave_plus_P", ("ave_plus", "P"), "Ave+ precision (%)", (60, 100)),
    ("micro_F1", ("micro", "F1"), "Micro-F1 (%)", (60, 100)),
    ("exact", ("exact",), "Exact-match rate (%)", (0, 30)),
    ("basic_acc", ("basic", "acc"), "Basic-feature accuracy (%)", (90, 100)),
]
# 图 2 中重点标注的类别与策略
HIGHLIGHT_CODES = {"F1", "F3", "F4", "C1", "M2", "H3"}
HIGHLIGHT_STRATEGIES = ("S4", "S5")
HIGHLIGHT_MIN_PP = 2.0


def setup_nature_style() -> None:
    """Nature 风格 rcParams；字体按本机可用情况回退。"""
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
            "axes.titlesize": 7.0,
            "axes.labelsize": 7.0,
            "xtick.labelsize": 6.3,
            "ytick.labelsize": 6.3,
            "legend.fontsize": 6.3,
            "figure.dpi": 300,
            "savefig.dpi": 300,
            "savefig.bbox": "tight",
            "savefig.pad_inches": 0.02,
            "axes.linewidth": 0.6,
            "xtick.major.width": 0.5,
            "ytick.major.width": 0.5,
            "xtick.major.size": 2.0,
            "ytick.major.size": 2.0,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": False,
            "axes.unicode_minus": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "text.usetex": False,
            "mathtext.fontset": "dejavusans",
        }
    )


def get_metric(cell: dict, path: tuple[str, ...]) -> float:
    value = cell
    for key in path:
        value = value[key]
    return float(value)


def mean_sem(values: list[float]) -> tuple[float, float]:
    n = len(values)
    mean = sum(values) / n
    sem = statistics.stdev(values) / math.sqrt(n) if n > 1 else 0.0
    return mean, sem


def save_figure(
    fig, out_dir: Path, stem: str, formats: tuple[str, ...], dpi: int
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for fmt in formats:
        path = out_dir / f"{stem}.{fmt}"
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


def _hide_heatmap_spines(ax) -> None:
    for spine in ax.spines.values():
        spine.set_visible(False)


def plot_fig1(doc: dict, out_dir: Path, formats: tuple[str, ...], dpi: int) -> list[Path]:
    """6 策略 × 4 模型 Ave+ F1 热力图。"""
    strategies = doc["labels"]["strategies"]
    models = doc["labels"]["models"]

    values = np.array(
        [
            [
                doc["cells"][strategy][model]["ave_plus"]["F1"] * 100
                for model in models
            ]
            for strategy in strategies
        ]
    )
    row_mean = values.mean(axis=1, keepdims=True)
    matrix = np.column_stack([values, row_mean])
    matrix = np.vstack([matrix, matrix.mean(axis=0, keepdims=True)])

    # 色标外扩并取整，避免最值贴边
    vmin = max(0, math.floor((values.min() - 1.5) / 2) * 2)
    vmax = min(100, math.ceil((values.max() + 1.5) / 2) * 2)

    fig, ax = plt.subplots(figsize=(4.4, 3.3), dpi=dpi)
    mesh = ax.pcolormesh(
        np.arange(matrix.shape[1] + 1) - 0.5,
        np.arange(matrix.shape[0] + 1) - 0.5,
        matrix,
        cmap="YlGnBu",
        vmin=vmin,
        vmax=vmax,
        shading="flat",
        edgecolors="white",
        linewidth=0.5,
    )

    # 数值标注：深色底用白字
    text_threshold = vmin + 0.62 * (vmax - vmin)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(
                j,
                i,
                f"{matrix[i, j]:.1f}",
                ha="center",
                va="center",
                fontsize=6.2,
                color="white" if matrix[i, j] > text_threshold else "#222222",
            )

    # 6×4 区域的最优格（允许并列）
    best = values.max()
    for i, j in np.argwhere(np.isclose(values, best)):
        ax.add_patch(
            Rectangle(
                (j - 0.5, i - 0.5),
                1,
                1,
                fill=False,
                edgecolor="black",
                linewidth=1.2,
                zorder=6,
            )
        )

    # Mean 行/列分隔
    ax.axvline(3.5, color="white", linewidth=2.0, zorder=5)
    ax.axhline(5.5, color="white", linewidth=2.0, zorder=5)

    ax.set_xticks(range(len(models) + 1))
    ax.set_xticklabels([MODEL_TICK[m] for m in models] + ["Mean"], fontsize=6.0)
    ax.set_yticks(range(len(strategies) + 1))
    ax.set_yticklabels(
        [doc["labels"]["strategy_display"][s] for s in strategies] + ["Mean"],
        fontsize=6.4,
    )
    ax.set_xlim(-0.5, len(models) + 0.5)
    ax.set_ylim(len(strategies) + 0.5, -0.5)
    ax.set_xlabel("Model")
    ax.set_ylabel("Strategy")
    ax.tick_params(length=0)
    _hide_heatmap_spines(ax)

    colorbar = fig.colorbar(mesh, ax=ax, fraction=0.046, pad=0.03)
    colorbar.set_label("Ave+ F1 (%)", fontsize=6.5)
    colorbar.ax.tick_params(labelsize=6.0)

    return save_figure(fig, out_dir, "fig1_strategy_model_aveplus_f1", formats, dpi)


def plot_fig2(doc: dict, out_dir: Path, formats: tuple[str, ...], dpi: int) -> list[Path]:
    """19 类 × 6 策略 F1 热力图（4 模型均值）。"""
    strategies = doc["labels"]["strategies"]
    models = doc["labels"]["models"]
    layers = doc["labels"]["layers"]
    codes = [code for layer in layers for code in layer["codes"]]

    matrix = np.array(
        [
            [
                statistics.mean(
                    doc["cells"][strategy][model]["per"]["F1"][code]
                    for model in models
                )
                * 100
                for strategy in strategies
            ]
            for code in codes
        ]
    )

    fig = plt.figure(figsize=(7.4, 5.8), dpi=dpi)
    grid = fig.add_gridspec(
        1,
        4,
        width_ratios=[0.62, 1.0, 0.15, 0.035],
        wspace=0.035,
        left=0.02,
        right=0.97,
        top=0.92,
        bottom=0.08,
    )
    ax_labels = fig.add_subplot(grid[0, 0])
    ax = fig.add_subplot(grid[0, 1])
    ax_counts = fig.add_subplot(grid[0, 2])
    ax_colorbar = fig.add_subplot(grid[0, 3])

    mesh = ax.pcolormesh(
        np.arange(len(strategies) + 1) - 0.5,
        np.arange(len(codes) + 1) - 0.5,
        matrix,
        cmap="YlGnBu",
        vmin=0,
        vmax=100,
        shading="flat",
        edgecolors="white",
        linewidth=0.4,
    )

    for i in range(len(codes)):
        for j in range(len(strategies)):
            ax.text(
                j,
                i,
                f"{matrix[i, j]:.1f}",
                ha="center",
                va="center",
                fontsize=5.7,
                color="white" if matrix[i, j] > 55 else "#222222",
            )

    # 图层分隔线
    start = 0
    for layer in layers[:-1]:
        start += len(layer["codes"])
        ax.axhline(start - 0.5, color="white", linewidth=1.8, zorder=5)

    ax.set_xticks(range(len(strategies)))
    ax.set_xticklabels([f"{s}'" for s in strategies], fontsize=6.5)
    ax.set_yticks([])
    ax.set_xlim(-0.5, len(strategies) - 0.5)
    ax.set_ylim(len(codes) - 0.5, -0.5)
    ax.set_xlabel("Strategy")
    ax.tick_params(length=0)
    _hide_heatmap_spines(ax)

    # 左侧：图层名（竖排）+ 编码 + 英文类别名
    ax_labels.set_xlim(0, 1)
    ax_labels.set_ylim(len(codes) - 0.5, -0.5)
    ax_labels.axis("off")
    start = 0
    for layer in layers:
        n = len(layer["codes"])
        center = start + (n - 1) / 2
        ax_labels.text(
            0.10,
            center,
            layer["name"],
            rotation=90,
            ha="center",
            va="center",
            fontsize=5.5,
            color="#444444",
        )
        start += n
    for i, code in enumerate(codes):
        ax_labels.text(
            0.98,
            i,
            f"{code}  {doc['labels']['class_display'][code]}",
            ha="right",
            va="center",
            fontsize=5.7,
        )

    # 右侧：GT 正例数
    ax_counts.set_xlim(0, 1)
    ax_counts.set_ylim(len(codes) - 0.5, -0.5)
    ax_counts.axis("off")
    ax_counts.text(
        0.05,
        -0.78,
        "GT n",
        ha="left",
        va="center",
        fontsize=6.2,
        fontweight="bold",
        clip_on=False,
    )
    for i, code in enumerate(codes):
        ax_counts.text(
            0.05,
            i,
            str(doc["gt"]["n_gt"][code]),
            ha="left",
            va="center",
            fontsize=5.7,
        )

    # 图 2 的强调标记：S4/S5 相对 S0–S3 均值的变化
    baseline = matrix[:, :4].mean(axis=1)
    for i, code in enumerate(codes):
        if code not in HIGHLIGHT_CODES:
            continue
        for j, strategy in enumerate(strategies):
            if strategy not in HIGHLIGHT_STRATEGIES:
                continue
            delta = matrix[i, j] - baseline[i]
            if abs(delta) < HIGHLIGHT_MIN_PP:
                continue
            ax.scatter(
                j + 0.30,
                i - 0.30,
                marker="v" if delta < 0 else "^",
                s=14,
                color="#111111",
                linewidths=0,
                zorder=7,
            )

    handles = [
        Line2D(
            [],
            [],
            marker="v",
            linestyle="",
            color="#111111",
            markersize=3.2,
            label="Lower than S0'-S3' mean",
        ),
        Line2D(
            [],
            [],
            marker="^",
            linestyle="",
            color="#111111",
            markersize=3.2,
            label="Higher than S0'-S3' mean",
        ),
    ]
    fig.legend(
        handles=handles,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.985),
        ncol=2,
        frameon=False,
        fontsize=5.8,
    )

    colorbar = fig.colorbar(mesh, cax=ax_colorbar)
    colorbar.set_label("Mean class F1 (%)", fontsize=6.5)
    colorbar.ax.tick_params(labelsize=6.0)

    return save_figure(fig, out_dir, "fig2_class_strategy_f1", formats, dpi)


def plot_fig3(doc: dict, out_dir: Path, formats: tuple[str, ...], dpi: int) -> list[Path]:
    """策略级多指标对比。"""
    strategies = doc["labels"]["strategies"]
    models = doc["labels"]["models"]

    fig, axes = plt.subplots(
        2, 3, figsize=(7.4, 4.4), dpi=dpi, constrained_layout=True
    )

    for ax, (key, path, title, ylim), letter in zip(axes.flat, METRICS, "abcdef"):
        means: list[float] = []
        sems: list[float] = []
        # per_model[模型下标][策略下标]，便于逐模型画散点
        per_model: list[list[float]] = [
            [0.0] * len(strategies) for _ in models
        ]
        for strategy_index, strategy in enumerate(strategies):
            values = [
                get_metric(doc["cells"][strategy][model], path) * 100
                for model in models
            ]
            mean, sem = mean_sem(values)
            means.append(mean)
            sems.append(sem)
            for model_index, value in enumerate(values):
                per_model[model_index][strategy_index] = value

        x = np.arange(len(strategies))
        ax.bar(
            x,
            means,
            width=0.62,
            color="#C9D6DF",
            edgecolor="#5B7185",
            linewidth=0.5,
            zorder=2,
        )
        ax.errorbar(
            x,
            means,
            yerr=sems,
            fmt="none",
            ecolor="#222222",
            elinewidth=0.7,
            capsize=2.0,
            capthick=0.7,
            zorder=4,
        )

        offsets = np.linspace(-0.24, 0.24, len(models))
        for index, model in enumerate(models):
            ax.scatter(
                x + offsets[index],
                per_model[index],
                s=8,
                marker=MODEL_MARKERS[model],
                color=MODEL_COLORS[model],
                edgecolors="white",
                linewidths=0.25,
                zorder=5,
                label=doc["labels"]["model_display"][model],
            )

        # 不在柱顶标数值：均值在正文表格中给出，避免与模型散点/误差棒重叠
        ax.set_xticks(x)
        ax.set_xticklabels([f"{s}'" for s in strategies])
        ax.set_ylim(*ylim)
        ax.set_title(title, loc="left", fontsize=7.0, pad=3)
        ax.grid(axis="y", color="#DDDDDD", linewidth=0.4, zorder=0)
        ax.set_axisbelow(True)
        ax.text(
            -0.14,
            1.04,
            letter,
            transform=ax.transAxes,
            fontsize=9,
            fontweight="bold",
            va="bottom",
            ha="left",
        )

    handles, labels = axes.flat[0].get_legend_handles_labels()
    try:
        fig.legend(
            handles,
            labels,
            loc="outside lower center",
            ncol=len(models),
            frameon=False,
            fontsize=6.3,
        )
    except (ValueError, TypeError):
        # 兼容不支持 outside loc 的旧版 matplotlib
        fig.legend(
            handles,
            labels,
            loc="lower center",
            bbox_to_anchor=(0.5, -0.02),
            ncol=len(models),
            frameon=False,
            fontsize=6.3,
        )

    return save_figure(fig, out_dir, "fig3_strategy_multimetric", formats, dpi)


def plot_fig4(doc: dict, out_dir: Path, formats: tuple[str, ...], dpi: int) -> list[Path]:
    """19 个致因编码在 300 条事故中的频率分布（水平堆叠条形图）。

    仿照例图：每行一个编码，蓝色段 = 该编码被标注（1），
    橙色段 = 未被标注（0），每行总长固定为 300。
    编码按 CAICS 层级分组，层内按频次降序，便于看出高频与长尾编码。
    """
    total = doc["gt"]["n_ids"]
    layers = doc["labels"]["layers"]

    rows: list[tuple[str, str]] = []
    for layer in layers:
        codes = sorted(
            layer["codes"],
            key=lambda code: doc["gt"]["n_gt"][code],
            reverse=True,
        )
        rows.extend((layer["key"], code) for code in codes)

    y = np.arange(len(rows))
    present = np.array([doc["gt"]["n_gt"][code] for _, code in rows])
    absent = total - present

    fig, ax = plt.subplots(figsize=(7.4, 5.8), dpi=dpi)
    ax.barh(
        y,
        present,
        height=0.72,
        color="#9DC3E6",
        edgecolor="white",
        linewidth=0.4,
        label="1",
    )
    ax.barh(
        y,
        absent,
        left=present,
        height=0.72,
        color="#F4B183",
        edgecolor="white",
        linewidth=0.4,
        label="0",
    )

    ax.set_yticks(y)
    ax.set_yticklabels(
        [
            f"{code}  {doc['labels']['class_display'][code]}"
            for _, code in rows
        ],
        fontsize=6.0,
    )
    ax.invert_yaxis()
    ax.set_xlim(0, 345)
    ax.set_xticks([0, 50, 100, 150, 200, 250, 300])
    ax.set_xlabel("Number of accidents (n = 300)")
    ax.grid(axis="x", color="#DDDDDD", linewidth=0.4)
    ax.set_axisbelow(True)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    # 右侧标注频次与占比
    for yi, (_, code), count in zip(y, rows, present):
        ax.text(
            total + 6,
            yi,
            f"{count} ({count / total * 100:.1f}%)",
            va="center",
            ha="left",
            fontsize=5.7,
        )

    # 层分隔线（M / F / T / H / C）
    start = 0
    for layer in layers[:-1]:
        start += len(layer["codes"])
        ax.axhline(start - 0.5, color="#999999", linewidth=0.6)

    ax.legend(
        loc="lower center",
        bbox_to_anchor=(0.5, -0.12),
        ncol=2,
        frameon=False,
        fontsize=6.3,
    )
    return save_figure(fig, out_dir, "fig4_cause_distribution", formats, dpi)


def main() -> None:
    parser = argparse.ArgumentParser(description="绘制论文图表")
    parser.add_argument("--data-json", required=True, help="figure_data.json 路径")
    parser.add_argument("--out-dir", default=None, help="输出目录")
    parser.add_argument("--formats", default="pdf,png", help="输出格式，逗号分隔")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--figs", default="1,2,3,4", help="要绘制的图，逗号分隔")
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
    wanted = {item.strip() for item in args.figs.split(",") if item.strip()}

    with data_path.open(encoding="utf-8") as fh:
        doc = json.load(fh)

    setup_nature_style()
    print(f"读取 {data_path}")
    print(f"输出目录：{out_dir}")
    if "1" in wanted:
        print("fig1: 6 策略 × 4 模型 Ave+ F1 热力图")
        plot_fig1(doc, out_dir, formats, args.dpi)
    if "2" in wanted:
        print("fig2: 19 类 × 6 策略 F1 热力图")
        plot_fig2(doc, out_dir, formats, args.dpi)
    if "3" in wanted:
        print("fig3: 策略级多指标对比")
        plot_fig3(doc, out_dir, formats, args.dpi)
    if "4" in wanted:
        print("fig4: 19 类致因频率分布")
        plot_fig4(doc, out_dir, formats, args.dpi)


if __name__ == "__main__":
    main()
