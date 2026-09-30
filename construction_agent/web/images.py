"""图片资产：`image_manifest` 的读取与 `/media` 的路径校验。蓝图 06。

数据源是**工作库**的 `image_manifest`，不是最终库的 `image_files`。

为什么不在最终库里取图：`image_files` 的 `local_path` 三版混存
（`imageV2/` 550 + `images/` 276 + `imageV3/` 211），与磁盘对不上的情况是
「247 条登记找不到目录、198 个目录没登记」。按它直接拼 URL 会大面积 404。
重建后的 `image_manifest` 以文件系统为真源，226 个文件一条不多一条不少。

工作库不可用时**降级不报错**：详情页照常渲染，图片区给一句提示说明需要先跑
`scripts/build_image_manifest.py`。展示层的其它部分不依赖工作库。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from ..config import settings
from . import db as rodb
from . import staging_db

# 只服务最终版。`images/` 与 `imageV2/` 不给——三版里同一事故可能有重复
# 图片，放出来读者没法判断哪张是最终采用的。
MEDIA_ROOT_DIRNAME = "imageV3"

# VLM 的返回里夹着 Markdown 强调标记（`.phase1_scene_type` 实测形如
# `**坍塌 / 土方坍塌**`）。那是模型的输出格式，不是字段语义，展示前剥掉。
# 只处理强调标记，不动文本本身——括号里的补充说明（如「实为人工拆除作业被
# 误报为坍塌」）是分析结论的一部分，必须留着。
_EMPHASIS = re.compile(r"\*{1,3}|_{2,}")


def clean_vlm_text(value: Any) -> str:
    if value is None:
        return ""
    return _EMPHASIS.sub("", str(value)).strip()


def media_root() -> Path:
    return (Path(settings.DATA_DIR) / MEDIA_ROOT_DIRNAME).resolve()


def _conn() -> sqlite3.Connection | None:
    """工作库只读连接。库不存在时返回 None，由调用方降级。"""
    try:
        return staging_db.connect(readonly=True)
    except (staging_db.StagingDBNotInitialized, sqlite3.Error, OSError):
        return None


def available() -> bool:
    """`image_manifest` 是否可用（库存在且表里有数据）。"""
    con = _conn()
    if con is None:
        return False
    try:
        return con.execute(
            "SELECT COUNT(*) FROM image_manifest").fetchone()[0] > 0
    except sqlite3.Error:
        return False
    finally:
        con.close()


def for_accident(accident_id: str) -> list[dict[str, Any]]:
    """某事故的图片条目。工作库不可用时返回空列表。

    `analyzed` 为真的条目带上 VLM 结论——只有这些能在灯箱里说明场景。
    """
    con = _conn()
    if con is None:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT rel_path, filename, ext, bytes, sha1, source_url, "
            "       source_page_url, width, height, "
            "       phase1_scene_type, phase2_consistency, phase2_confidence, "
            "       vlm_model, analyzed "
            "FROM image_manifest WHERE accident_id = ? "
            "ORDER BY filename",
            (accident_id,),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()

    items = []
    for r in rows:
        d = dict(r)
        d["url"] = "/media/" + d["rel_path"]
        d["analyzed"] = bool(d["analyzed"])
        d["phase1_scene_type"] = clean_vlm_text(d["phase1_scene_type"])
        d["phase2_consistency"] = clean_vlm_text(d["phase2_consistency"])
        items.append(d)
    return items


def suspicious_sha1s(min_accidents: int = 3) -> set[str]:
    """被 ≥N 个事故共用的图片内容哈希。

    实测 226 条里有 70 条（31%）的内容出现在多个事故目录，其中 8 组覆盖
    ≥3 个事故——事故还按地域聚成一堆（武汉一批、广州一批），来源 URL 里
    有 `n.sinaimg.cn/default/`。那基本是图床返回的默认占位图，不是现场照片。

    展示层拿它给图片打角标「多起事故共用」，**不隐藏**：数据里就是这样，
    藏起来反而让人以为每条事故都有独立的现场照片。
    """
    con = _conn()
    if con is None:
        return set()
    try:
        rows = con.execute(
            "SELECT sha1 FROM image_manifest WHERE sha1 IS NOT NULL "
            "GROUP BY sha1 HAVING COUNT(DISTINCT accident_id) >= ?",
            (min_accidents,),
        ).fetchall()
    except sqlite3.Error:
        return set()
    finally:
        con.close()
    return {r[0] for r in rows}


def stats() -> dict[str, Any]:
    """图片覆盖统计（概览页与 `/api/images/stats`）。"""
    empty: dict[str, Any] = {
        "files": 0, "accidents_with_images": 0, "accidents_total": 0,
        "coverage": 0.0, "by_ext": [], "analyzed": 0, "generated_at": None,
        "available": False,
    }
    con = _conn()
    if con is None:
        return empty
    con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT COUNT(*) AS files, "
            "       COUNT(DISTINCT accident_id) AS accs, "
            "       SUM(COALESCE(analyzed, 0)) AS analyzed, "
            "       MAX(manifest_at) AS at "
            "FROM image_manifest").fetchone()
        by_ext = [dict(r) for r in con.execute(
            "SELECT ext, COUNT(*) AS count FROM image_manifest "
            "GROUP BY ext ORDER BY count DESC")]
    except sqlite3.Error:
        return empty
    finally:
        con.close()

    files = row["files"] or 0
    if not files:
        return empty

    accs = row["accs"] or 0
    total = int(rodb.scalar("SELECT COUNT(*) FROM accidents", default=0) or 0)

    return {
        "files": files,
        "accidents_with_images": accs,
        "accidents_total": total,
        # 覆盖率只有 7.4%（193/2,623）。这个数字**必须显式呈现**——它是采集
        # 完整性的真实反映，藏起来才像缺陷。
        "coverage": (accs / total) if total else 0.0,
        "by_ext": by_ext,
        "analyzed": row["analyzed"] or 0,
        "generated_at": row["at"],
        "available": True,
    }


def resolve_media_path(rel_path: str) -> Path | None:
    """把 `/media/<rel_path>` 映射到磁盘文件，越界或不存在都返回 None。

    两道关：`rel_path` 必须以 `imageV3/` 开头（不接受绝对路径、`..`、其它
    版本目录），且 `realpath` 之后仍要落在 `imageV3/` 之内。只做前者挡不住
    符号链接，只做后者挡不住 `imageV2/` 被顺手放进来。
    """
    if not rel_path:
        return None
    rel = rel_path.replace("\\", "/").lstrip("/")
    root = media_root()
    prefix = MEDIA_ROOT_DIRNAME + "/"
    if not rel.startswith(prefix):
        return None

    candidate = (root.parent / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate
