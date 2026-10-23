# 19 编码 F1 表的论文排版方案

原始表是 24 行 × 23 列（4 模型 × 6 策略 × 19 编码 + Ave/Ave+）。19 个编码作为
**列**时，即使 6pt 也会超出 A4 纵向正文宽度。核心原则：

> **把 19 个编码放到行上，把 24 个“模型×策略”格子拆到分面、分表或附录。**

本目录给出三种可直接使用的布局。

## 1. 本目录文件

| 文件 | 用途 | 尺寸 |
|---|---|---|
| `f1_summary.docx` / `.png` | **正文主表**：表 1 Ave+、表 2 Ave，各 6 行 × 6 列 | 纵向，约 8 cm 高 |
| `f1_heatmap_panels.png` / `.pdf` | **正文图**：2×2 分面热图，每模型 19 编码 × 6 策略 | 通栏，约 8×8.8 in |
| `f1_by_model_appendix.docx` | **附录表**：每模型一张 19 行 × 7 列（编码 + S0–S5），表末 Ave/Ave+ | 纵向，每模型 1 页 |

推荐正文结构：

- 正文表 1（Ave+）回答“哪种策略最好”；
- 正文图 1（分面热图）回答“哪些编码提升、哪些编码仍弱”；
- 附录表 A1–A4 保留全部逐编码数值，供审稿人核对。

这样正文只占约 1 张表 + 1 张图，且不超版面；19 编码的完整信息不丢。

## 2. 若坚持保留原始 24×23 表

### 2.1 横向页（最省事）

LaTeX：

```latex
\usepackage{rotating}
\begin{sidewaystable}[p]
  \centering
  \caption{六种提示策略 × 四模型的 19 类编码 F1（\%）}
  \scriptsize
  \setlength{\tabcolsep}{2pt}
  \renewcommand{\arraystretch}{1.05}
  \begin{tabular}{ll*{21}{c}}
    \toprule
    模型 & 提示 & M1 & M2 & ... & C2 & Ave & Ave+ \\
    \midrule
    ...
    \bottomrule
  \end{tabular}
\end{sidewaystable}
```

Word：在表前插入“分节符（下一页）”，将该节设为**横向**、页边距 1.5 cm，
表格字号 7–7.5pt，编码列宽 0.6–0.65 cm。

### 2.2 按编码层拆成两张纵向表（推荐用于“必须保留原方向”）

| 表 | 列 | 列数 | 说明 |
|---|---|---:|---|
| 表 A | 模型、提示、M1–M5、F1–F4、Ave、Ave+ | 13 | 管理根基 + 防线失效 |
| 表 B | 模型、提示、T1–T4、H1–H4、C1–C2、Ave、Ave+ | 12 | 触发 + 人的行为 + 后果 |

每张 24 行 × 12–13 列，7pt、列宽 0.8 cm 时约 14–15 cm，可放进 A4 纵向正文。
两张表都保留 Ave/Ave+，读者仍能横向比较策略。

### 2.3 强行压缩（不推荐）

```latex
\resizebox{\textwidth}{!}{%
  \begin{tabular}{...} ... \end{tabular}}
```

或 `\scriptsize` + `\tabcolsep=1.5pt`。23 列压到 15–16 cm 时字号约 5.5–6pt，
低于多数期刊 7pt 的下限；且缩放过猛会导致数字难读，审稿意见风险高。
如必须用，优先 2.1 的横向页。

## 3. 正文只报层级汇总

若正文篇幅紧张，可只报 5 个层级（M/F/T/H/C）的 F1：

- 行 = 5 层，列 = 6 策略，每个模型一张小表（5×7），四张并排或分面；
- 或行 = 5 层 × 4 模型，列 = 6 策略（20 行 × 7 列），一张纵向表即可。

逐编码结果放补充材料。缺点是掩盖了层内差异（例如 F1 与 F3 的策略敏感性完全不同）。

## 4. 插入图片时的注意事项

- 不要把 23 列的 PNG 直接缩放到 `\textwidth`：原图 5242 px 宽，缩到 16 cm 后
  字号只剩 3–4pt，印刷不可读。
- 热力图优先插 **PDF/SVG 矢量图**；位图至少 300 dpi，且按最终物理宽度出图。
- 本目录的 `f1_heatmap_panels.pdf` 已是矢量图，可直接 `\includegraphics`。

## 5. 复现

```bash
cd /home/suntianhao/construction_agent
.venv/bin/python3 scripts/golden_s1/make_compact_f1_layouts.py
```

脚本读取 `../f1_per_code.csv`，不会重新跑模型。
