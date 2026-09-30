"""三级证据等级的**唯一计算出口**。

`evidence_quality` 是 CAICS 体系的一部分，但它**不由 S4 编码器产出**——
`CauseItem` 只有 `code` / `name` / `evidence` / `inference` 四个字段，
等级是**入库时按关键词现算**的。这段现算逻辑曾经被复制到 6 个脚本里，
本模块把它上收为一处。

## 权威口径为什么是「不含『指向』」的那一版

复制品并不逐字相同（蓝图 02 §2.4 原写「六份逐字相同」，实测不成立）：

| 文件 | `indirect_infer` 的触发词 |
|---|---|
| `run_batch.py` · `batch_runner.py` · `process_pdf.py` · `process_pdf3.py` · `process_ebook800.py` | `推断` / `表明` |
| `run_batch_pipeline.py` | `推断` / `表明` / **`指向`** ← 已分叉 |

拿主库 16,323 条逐条回放两种口径，结果没有含糊余地：

```
不含「指向」  →  16,323 / 16,323 与库中已存值一致（100%）
含「指向」    →  16,284 / 16,323 一致（差 39 条）
```

所以 **5 份多数派 + 与主库全量吻合**，权威口径取它。`run_batch_pipeline.py`
的那份是后来被改过、又没重新产出过数据的变体——本项目此前有过一次
「HFACS 统计恒为空却不报错」的静默失效，这里是同类的分叉，早点收敛掉。

## 行为绝对不可改

论文的三级证据数字（`direct_quote` 14,784 / `indirect_infer` 1,117 /
`weak_infer` 422）就是这段逻辑算出来的。改动判定顺序或词表等于换一个
理论版本，属于独立决策——**本模块只做搬家，不做修正**。
"""

from __future__ import annotations

# 判定顺序即优先级：先看 inference 是否带推断口吻，再看是否明确声明"原文未直接"。
# weak_infer 排在后面覆盖前面，这是原逻辑的顺序，不要调整。
_WEAK_MARKER = "原文未直接"
_INFER_MARKERS = ("推断", "表明")

DIRECT_QUOTE = "direct_quote"
INDIRECT_INFER = "indirect_infer"
WEAK_INFER = "weak_infer"

LEVELS: tuple[str, ...] = (DIRECT_QUOTE, INDIRECT_INFER, WEAK_INFER)

# 展示层与论文表格共用的中文名。放在这里而不是各页面各写一份，
# 免得又出现「同一档位两种叫法」。
LEVEL_LABELS: dict[str, str] = {
    DIRECT_QUOTE: "直引",
    INDIRECT_INFER: "推断",
    WEAK_INFER: "弱推断",
}


def compute_evidence_quality(evidence: str, inference: str) -> str:
    """现算三级证据等级。

    Args:
        evidence: S4 给出的证据文本（可为 None）
        inference: S4 给出的推断文本（可为 None）

    Returns:
        `direct_quote` | `indirect_infer` | `weak_infer`

    **行为与 6 处既有复制品（不含「指向」的版本）完全一致，勿改判定顺序。**
    """
    ev = evidence or ""
    inf = inference or ""

    eq = DIRECT_QUOTE
    if any(m in inf for m in _INFER_MARKERS):
        eq = INDIRECT_INFER
    if _WEAK_MARKER in ev or _WEAK_MARKER in inf:
        eq = WEAK_INFER
    return eq


def compute_sort_order(code: str) -> int:
    """CAICS 码 → 展示用排序号。

    层序固定为 M → F → T → H → C（与论文图表、致因分析页的层顺序一致），
    层内按码尾数字升序。同段逻辑原先也在 6 处各写一遍。

    未知前缀排到最后（600+），**不抛异常**——新码进来时应该看得见它，
    而不是让整批写入失败。
    """
    base = {"M": 100, "F": 200, "T": 300, "H": 400, "C": 500}.get(
        (code or " ")[0], 600)
    tail = (code or "")[1:]
    return base + (int(tail) if tail.isdigit() else 0)
