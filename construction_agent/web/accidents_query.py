"""事故列表查询：`/api/accidents` 的参数化查询。

## 为什么不用 `sqlite_repository.py`

那个 SQLAlchemy 仓储是给写路径用的通用 CRUD，`get_all()` 会把整表物化到
内存，没有分页、没有编码数聚合、也没有证据筛选。展示层的查询全部下推到
SQLite，本模块是唯一入口。

## 参数契约

| 参数 | 类型 | 说明 |
|---|---|---|
| `q` | str | 匹配 title / summary / direct_cause 的**子串** |
| `province` | 多值 | 省级全称或 `__overseas__` / `__unnormalized__` / `__empty__` |
| `severity` | 多值 | 空值用 `__empty__` |
| `accident_type` | 多值 | 同上 |
| `project_type` | 多值 | 同上 |
| `construction_phase` | 多值 | 同上 |
| `year_from` / `year_to` | int | 按 `date` 前四位的字符串比较 |
| `has_causes` | bool | `true` 只留有编码的，`false` 只留没编码的 |
| `sort` | enum | 见 `SORTS`，非法值退回默认 |
| `page` / `size` | int | page 从 1 起；size 上限 200 |

多值参数支持 ``?k=a&k=b`` 与 ``?k=a,b`` 两种写法。

## 三条实现约定

1. **全部值走占位符**。只有排序用的是 SQL 片段，且来自 `SORTS` 白名单，
   请求里的 `sort` 值永远不拼接进 SQL。
2. **`total` 用独立 `COUNT(*)`**。不靠 `len(items)` 推算，也不依赖
   SQLite 的窗口函数——分页元信息必须与实际总行数无关地正确。
3. **`instr()` 而非 `LIKE`**。用户输入的 `%` 和 `_` 在 `LIKE` 下是通配符，
   会让「100%」这类查询返回错误结果；`instr()` 是字面子串匹配。

排序键一律以 `a.id` 收尾。`date` 有大量重复（同一天多起事故），
不加唯一列会让同一行在翻页时重复出现或消失。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import db, province

# 空值筛选哨兵。与 `province.SENTINEL_EMPTY` 同值，语义一致：
# 「这个字段没有值」本身是一种可筛选的状态，不该被藏起来。
SENTINEL_EMPTY = province.SENTINEL_EMPTY

DEFAULT_SORT = "date_desc"
DEFAULT_SIZE = 50
MAX_SIZE = 200

# ══════════════════════════════════════════════════════════════
# 排序白名单
# ══════════════════════════════════════════════════════════════

_SEVERITY_RANK_SQL = (
    "CASE a.severity "
    "WHEN '特别重大事故' THEN 1 "
    "WHEN '重大事故'     THEN 2 "
    "WHEN '较大事故'     THEN 3 "
    "WHEN '一般事故'     THEN 4 "
    "ELSE 5 END"
)

SORTS: dict[str, str] = {
    "date_desc":     "a.date DESC, a.id",
    "date_asc":      "a.date ASC, a.id",
    "deaths_desc":   "a.deaths DESC, a.date DESC, a.id",
    "deaths_asc":    "a.deaths ASC, a.date DESC, a.id",
    "injuries_desc": "a.injuries DESC, a.date DESC, a.id",
    "severity_desc": f"{_SEVERITY_RANK_SQL}, a.date DESC, a.id",
    "causes_desc":   "cause_count DESC, a.date DESC, a.id",
}

SORT_LABELS: dict[str, str] = {
    "date_desc": "日期（新→旧）",
    "date_asc": "日期（旧→新）",
    "deaths_desc": "死亡人数（多→少）",
    "deaths_asc": "死亡人数（少→多）",
    "injuries_desc": "受伤人数（多→少）",
    "severity_desc": "事故等级（重→轻）",
    "causes_desc": "致因数（多→少）",
}

# 可多选筛选的字段 → 列名。province 单独处理（要反向展开）。
MULTI_FIELDS: dict[str, str] = {
    "severity": "a.severity",
    "accident_type": "a.accident_type",
    "project_type": "a.project_type",
    "construction_phase": "a.construction_phase",
}

LIST_COLUMNS = """
    a.id, a.title, a.date, a.accident_time,
    a.province, a.city, a.district,
    a.accident_type, a.severity, a.deaths, a.injuries, a.trapped_count,
    a.project_type, a.construction_phase, a.source_name,
    a.work_task,
    (SELECT COUNT(*) FROM caics_causes c WHERE c.accident_id = a.id) AS cause_count
"""


# ══════════════════════════════════════════════════════════════
# 枚举中文名
# ══════════════════════════════════════════════════════════════

# ⚠️ 工序只读 `accidents.work_task`，**不是** `work_task_enum`。
#
# 两列的实测关系（2026-09-30，主库 2,623 条）：
#   work_task        有值 2,202 条，取值 WT01–WT10 + WT99，与 `enum_definitions`
#                    的 11 条 2.0-llm 枚举逐项吻合（WT01=248 / WT02=397 / …）
#   work_task_enum   有值 1,538 条，其中 703 条是 WT11–WT15——**枚举表里没有这些编码**
#   两列同时有值 1,538 条，其中 1,392 条互相矛盾
#
# 判据是 `pipeline_metadata.work_task_enum_version`（迁移日期 2026-07-28）：
#   total_categories=11、key_merges 含 "WT11+WT14→WT05"、new_category=WT10。
#   2.0-llm 迁移把旧编码合并进新体系，**产物写在 `work_task` 列**；
#   `work_task_enum` 是 1.0 旧体系的残留列。
#
# 该元数据的 key 名是 `work_task_enum_version`，指的是「work_task 枚举的版本」，
# 并不指 `work_task_enum` 这一列——按 key 名去取列，恰好会取到旧值。

_ENUM_CACHE: dict[tuple, dict[str, str]] = {}


def enum_names(enum_type: str, db_path: str | Path | None = None) -> dict[str, str]:
    """`enum_definitions` 的 code → 中文名（只取 `is_active`）。按库指纹缓存。"""
    fp = (enum_type, *db.db_fingerprint(db_path))
    cached = _ENUM_CACHE.get(fp)
    if cached is None:
        rows = db.query(
            "SELECT code, name FROM enum_definitions "
            "WHERE enum_type = ? AND is_active = 1",
            (enum_type,),
            db_path=db_path,
        )
        # 库指纹变了，其它枚举的缓存同样作废——留着会返回上一个库的中文名
        _ENUM_CACHE.clear()
        cached = _ENUM_CACHE[fp] = {r["code"]: r["name"] for r in rows}
    return cached


def reset_cache() -> None:
    """清空枚举名缓存。测试切换库文件时调用。"""
    _ENUM_CACHE.clear()


# ══════════════════════════════════════════════════════════════
# 参数解析
# ══════════════════════════════════════════════════════════════


def _multi(source: Mapping[str, Any], key: str) -> list[str]:
    """取多值参数。支持 ``?k=a&k=b`` 与 ``?k=a,b``。"""
    if hasattr(source, "getlist"):
        raw: list[Any] = list(source.getlist(key))  # type: ignore[attr-defined]
    else:
        value = source.get(key)
        if value is None:
            return []
        raw = list(value) if isinstance(value, (list, tuple)) else [value]

    out: list[str] = []
    for item in raw:
        out.extend(str(item).split(","))
    return [s.strip() for s in out if s.strip()]


def _single(source: Mapping[str, Any], key: str, default: str = "") -> str:
    value = source.get(key)
    return default if value is None else str(value).strip()


def _int(source: Mapping[str, Any], key: str) -> int | None:
    raw = _single(source, key)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _bool3(source: Mapping[str, Any], key: str) -> bool | None:
    raw = _single(source, key).lower()
    if raw in ("1", "true", "yes", "y"):
        return True
    if raw in ("0", "false", "no", "n"):
        return False
    return None


@dataclass
class ListParams:
    """一次列表查询的全部输入。"""

    q: str = ""
    province: list[str] = field(default_factory=list)
    severity: list[str] = field(default_factory=list)
    accident_type: list[str] = field(default_factory=list)
    project_type: list[str] = field(default_factory=list)
    construction_phase: list[str] = field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    has_causes: bool | None = None
    sort: str = DEFAULT_SORT
    page: int = 1
    size: int = DEFAULT_SIZE

    @classmethod
    def from_request(cls, source: Mapping[str, Any]) -> "ListParams":
        """从请求参数构造。所有异常输入都退回默认值，不抛给调用方。"""
        sort = _single(source, "sort", DEFAULT_SORT)
        if sort not in SORTS:
            sort = DEFAULT_SORT

        # 非正数（含负数、0、非数字）一律退回默认值，而不是截断成 1——
        # `?size=-5` 是调用方写错了，给 1 条会让人以为数据只有一条。
        page = _int(source, "page") or 0
        size = _int(source, "size") or 0

        return cls(
            q=_single(source, "q"),
            province=_multi(source, "province"),
            severity=_multi(source, "severity"),
            accident_type=_multi(source, "accident_type"),
            project_type=_multi(source, "project_type"),
            construction_phase=_multi(source, "construction_phase"),
            year_from=_int(source, "year_from"),
            year_to=_int(source, "year_to"),
            has_causes=_bool3(source, "has_causes"),
            sort=sort,
            page=max(1, page),
            size=min(MAX_SIZE, size) if size > 0 else DEFAULT_SIZE,
        )

    def normalized(self, db_path: str | Path | None = None) -> dict[str, Any]:
        """回显给前端的实际生效条件（未知省份键已被剔除）。"""
        return {
            "q": self.q,
            "province": self.province,
            "severity": self.severity,
            "accident_type": self.accident_type,
            "project_type": self.project_type,
            "construction_phase": self.construction_phase,
            "year_from": self.year_from,
            "year_to": self.year_to,
            "has_causes": self.has_causes,
            "sort": self.sort,
            "page": self.page,
            "size": self.size,
        }


# ══════════════════════════════════════════════════════════════
# 条件构造
# ══════════════════════════════════════════════════════════════


def _empty_clause(column: str) -> str:
    return f"({column} IS NULL OR TRIM({column}) = '')"


def build_where(
    params: ListParams, db_path: str | Path | None = None
) -> tuple[str, list[Any]]:
    """把筛选条件翻译成 ``(WHERE 片段, 参数)``。无条件时返回 ``("", [])``。"""
    conds: list[str] = []
    args: list[Any] = []

    if params.q:
        conds.append(
            "(instr(a.title, ?) > 0"
            " OR instr(IFNULL(a.summary, ''), ?) > 0"
            " OR instr(IFNULL(a.direct_cause, ''), ?) > 0)"
        )
        args.extend([params.q] * 3)

    if params.province:
        frag, vals = province.filter_clause(params.province, db_path)
        if frag:
            conds.append(frag)
            args.extend(vals)

    for key, column in MULTI_FIELDS.items():
        selected = getattr(params, key)
        if not selected:
            continue
        values = [v for v in selected if v != SENTINEL_EMPTY]
        parts: list[str] = []
        if values:
            parts.append(f"{column} IN ({','.join('?' * len(values))})")
            args.extend(values)
        if SENTINEL_EMPTY in selected:
            parts.append(_empty_clause(column))
        if parts:
            conds.append("(" + " OR ".join(parts) + ")")

    if params.year_from is not None:
        conds.append("substr(a.date, 1, 4) >= ?")
        args.append(f"{params.year_from:04d}")
    if params.year_to is not None:
        conds.append("substr(a.date, 1, 4) <= ?")
        args.append(f"{params.year_to:04d}")

    if params.has_causes is not None:
        exists = "EXISTS (SELECT 1 FROM caics_causes c WHERE c.accident_id = a.id)"
        conds.append(exists if params.has_causes else f"NOT {exists}")

    if not conds:
        return "", []
    return " WHERE " + " AND ".join(conds), args


# ══════════════════════════════════════════════════════════════
# 行加工
# ══════════════════════════════════════════════════════════════


def decorate(
    row: dict[str, Any],
    *,
    work_task_names: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """补上省份归一结果与工序中文名，供前端直接展示。

    ``province_key`` 是筛选键（省名或哨兵），``province_label`` 是展示名。
    当原始值与展示名不同（地址级脏值、境外写法）时额外给出
    ``province_raw``，前端据此加「由地址归一」的提示。

    工序同理：``work_task_code`` 是库里的编码，``work_task_label`` 是中文名。
    编码查不到名字时**回退显示编码本身**，不写「未标注」或「未知」——
    异常编码该自己冒出来，不该被一个兜底词盖住。
    """
    raw = row.pop("province", None)
    key = province.classify(raw)
    label = province.label_of(key)

    row["province_key"] = key
    row["province_label"] = label
    row["province_is_standard"] = key in province.MAINLAND
    if raw is not None and str(raw) != label:
        row["province_raw"] = str(raw)

    code = row.pop("work_task", None)
    code = str(code) if code is not None and str(code).strip() else None
    row["work_task_code"] = code
    row["work_task_label"] = (
        None if code is None else (work_task_names or {}).get(code, code)
    )
    return row


# ══════════════════════════════════════════════════════════════
# 查询
# ══════════════════════════════════════════════════════════════


def run(
    params: ListParams,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """执行查询，返回 ``{"items": [...], "meta": {...}}``。"""
    where_sql, args = build_where(params, db_path)

    total = db.scalar(
        f"SELECT COUNT(*) FROM accidents a{where_sql}",
        args,
        default=0,
        db_path=db_path,
    )

    order = SORTS.get(params.sort, SORTS[DEFAULT_SORT])
    offset = (params.page - 1) * params.size
    rows = db.query(
        f"SELECT {LIST_COLUMNS} FROM accidents a{where_sql} "
        f"ORDER BY {order} LIMIT ? OFFSET ?",
        [*args, params.size, offset],
        db_path=db_path,
    )

    # 枚举名整页查一次，不是逐行查——`enum_names` 内部按库指纹缓存
    names = enum_names("work_task", db_path)

    pages = math.ceil(total / params.size) if params.size else 0
    return {
        "items": [decorate(r, work_task_names=names) for r in rows],
        "meta": {
            "total": total,
            "page": params.page,
            "size": params.size,
            "pages": pages,
            "sort": params.sort,
            "filters": params.normalized(db_path),
            "has_more": params.page < pages,
        },
    }


def filter_options(db_path: str | Path | None = None) -> dict[str, Any]:
    """筛选器各字段的可选值（带计数）。

    取值一律来自数据本身——新增一个 `accident_type` 枚举值后重新加载页面
    即可筛选，不需要改代码。空值若存在则作为 `__empty__` 选项列出。
    """
    out: dict[str, Any] = {"province": province.options(db_path)}

    for key, column in MULTI_FIELDS.items():
        rows = db.query(
            f"SELECT {column} AS v, COUNT(*) AS c "
            f"FROM accidents a GROUP BY 1 ORDER BY c DESC, v",
            db_path=db_path,
        )
        # NULL 与空字符串在 SQL 里是两组，但对用户是同一件事「没填」。
        # 这里就地把两者都换成哨兵，后面的合并才按 value 归并得起来——
        # 若只在合并的键上做替换，输出里的 value 仍是空串，
        # 前端拼出的 `?severity=` 会被解析成「不筛选」，筛选器直接失效。
        merged: dict[str, dict[str, Any]] = {}
        for r in rows:
            raw = r["v"]
            is_empty = raw is None or str(raw).strip() == ""
            value = SENTINEL_EMPTY if is_empty else str(raw)
            slot = merged.get(value)
            if slot is None:
                merged[value] = {
                    "value": value,
                    "label": "未标注" if is_empty else value,
                    "count": r["c"],
                }
            else:
                slot["count"] += r["c"]
        out[key] = list(merged.values())

    out["sort"] = [{"value": k, "label": v} for k, v in SORT_LABELS.items()]
    return out
