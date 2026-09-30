"""图片资产：`image_manifest` 的读取与 `/media` 的路径校验。蓝图 06。

数据源是**工作库**的 `image_manifest`，不是最终库的 `image_files`。

为什么不在最终库里取图：`image_files` 的 `local_path` 三版混存
（`imageV2/` 550 + `images/` 276 + `imageV3/` 211），当初据此判断「按它直接
拼 URL 会大面积 404」——**那个判断是错的**：逐条 stat 命中 1,037/1,037，
一条都没错配（复核见 `docs/web/08_图片资产复核与更正.md`）。真正的理由更
朴素：那份登记是旧的审计留痕，与磁盘差了 123 张 / 83 个事故，不该拿它当
展示的真源。重建后的 `image_manifest` 以文件系统为真源，三版共 951 个可
展示文件、559 个有图事故。

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

# 三版都服务。原先只给 `imageV3/`（最终版），理由是「三版里同一事故可能有
# 重复图片，读者没法判断哪张是最终采用的」——实测真重复（同事故 + 同内容 +
# 跨版本）**只有 19 组 / 38 张**，却因此丢掉 725 张图、366 个有图事故（65%）
# 完全没图。改用版本角标让读者自己判断，比隐藏更符合「让数据说话」。复核与
# 数据见 `docs/web/08_图片资产复核与更正.md`。
MEDIA_ROOT_DIRNAMES: tuple[str, ...] = ("images", "imageV2", "imageV3")

# 版本短名 → 展示标签。`v3` 是最终版；旧两版保留是因为图片本身在磁盘上、
# 也在数据库里登记着，藏起来只是让数据库显得比实际更空。
VERSION_BADGES = {"v1": "历史版本", "v2": "历史版本", "v3": "最终版"}

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
    """图片资产的根（`data/`）。

    `rel_path` 自带版本目录段（`imageV3/xxx/yyy.jpg`），所以根就是 `data/`，
    不再往版本目录里下沉一层。
    """
    return Path(settings.DATA_DIR).resolve()


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
            "       source_page_url, width, height, version, "
            "       phase1_scene_type, phase2_consistency, phase2_confidence, "
            "       vlm_model, analyzed "
            "FROM image_manifest WHERE accident_id = ? "
            # 最终版排前：同一事故的图先看到 v3，历史版本在后
            "ORDER BY CASE version WHEN 'v3' THEN 0 WHEN 'v2' THEN 1 ELSE 2 END, "
            "         filename",
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
        d["version_badge"] = VERSION_BADGES.get(d.get("version"), "历史版本")
        d["phase1_scene_type"] = clean_vlm_text(d["phase1_scene_type"])
        d["phase2_consistency"] = clean_vlm_text(d["phase2_consistency"])
        items.append(d)
    return items


def for_accident_marked(accident_id: str) -> list[dict]:
    """`for_accident` 的结果 + 跨事故共用的 `shared` 标记。

    这段原先在详情接口和画廊接口 `/api/accidents/<id>/images` 里各写了一份，
    **逐字重复**。两份拷贝一旦漂移，同一个事故经两条路拿到的标记就不一样。
    画廊接口已删除（无人调用；蓝图 06 §4 自己写明它是「详情接口已返回 images」
    之后的备用通道，而单事故实测最多 7 张，「分页」这个理由也不成立），这段
    收敛成单一出口。
    """
    items = for_accident(accident_id)
    suspicious = suspicious_sha1s()
    for it in items:
        it["shared"] = bool(it.get("sha1") and it["sha1"] in suspicious)
    return items


def suspicious_sha1s(min_accidents: int = 3) -> set[str]:
    """被 ≥N 个事故共用的图片内容哈希。

    三版合并后实测 951 条里有 356 条（37.4%）的内容出现在多个事故目录，
    其中 44 组覆盖 ≥3 个事故——事故还按地域聚成一堆（武汉一批、广州一批），
    来源 URL 里有 `n.sinaimg.cn/default/`。那基本是图床返回的默认占位图，
    不是现场照片。

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
        "accidents_with_images_legacy_only": 0,
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
        # 「只有历史版本有图」的事故数：它们一张最终版图都没有。截断到
        # imageV3 的时候，这批事故在界面上是「无图片」——这个数字就是
        # 截断代价的直接度量。
        legacy_only = con.execute(
            "SELECT COUNT(*) FROM (SELECT accident_id FROM image_manifest "
            "GROUP BY accident_id HAVING SUM(version = 'v3') = 0)"
        ).fetchone()[0]
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
        "accidents_with_images_legacy_only": legacy_only,
        # 覆盖率是**三版合并 + 以文件系统为真源**的口径：559/2,623 ≈ 21.3%。
        # 原先这里的注释写「7.4%（193/2,623）」，那是只扫 imageV3 时的值——
        # 它不是采集完整性的反映，只是取材范围被截断的反映（把偏高的事实
        # 显示成了偏低）。数字仍**必须显式呈现**，藏起来才像缺陷。
        #
        # 注意 559 与 `image_files` 口径的 476 不是一回事：前者数磁盘目录，
        # 后者数旧登记里的 accident_id。差 83 个事故是「磁盘有图、登记里
        # 没有」——旧登记漏登的规模。两个数都对，别混用。
        "coverage": (accs / total) if total else 0.0,
        "by_ext": by_ext,
        "analyzed": row["analyzed"] or 0,
        "generated_at": row["at"],
        "available": True,
    }


def resolve_media_path(rel_path: str) -> Path | None:
    """把 `/media/<rel_path>` 映射到磁盘文件，越界或不存在都返回 None。

    两道关：第一段必须是白名单里的版本目录（挡掉绝对路径、`..` 开头，以及
    `datasets/` 这类非图片目录），且 `realpath` 之后仍要落在某个版本目录之内。
    两条缺一不可——只看第一段挡不住符号链接；只看 realpath 挡不住
    `imageV3/../datasets/x` 这种用 `..` 绕出去、结果仍落在 `data/` 里的写法。
    """
    if not rel_path:
        return None
    rel = rel_path.replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p]
    # manifest 里的 rel_path 由 relative_to() 生成，正常绝不含 `..`；出现了
    # 就是有人手改了 URL。直接拒，不靠 resolve 之后的判断去兜——那条路对
    # `imageV3/../imageV2/x` 这种"绕出去又落回白名单目录"的写法是放行的。
    if ".." in parts or len(parts) < 2 or parts[0] not in MEDIA_ROOT_DIRNAMES:
        return None

    root = media_root()
    candidate = (root / rel).resolve()
    if not candidate.is_file():
        return None

    for name in MEDIA_ROOT_DIRNAMES:
        try:
            candidate.relative_to((root / name).resolve())
            return candidate
        except ValueError:
            continue
    return None
