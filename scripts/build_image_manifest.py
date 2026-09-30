"""重建 `image_manifest`（以文件系统为真源）。蓝图 06 §3。

背景（实测，2026-09-30）
-----------------------
最终库 `image_files`（1,037 行）的 `local_path` **三版混存**：

    imageV2/  550 条 · images/  276 条 · imageV3/  211 条

而磁盘上是另一番景象：`images/` 263 个文件、`imageV2/` 464 个、`imageV3/`
226 个。两边对不上的程度：

    imageV3 磁盘文件              226
    image_files 指向 imageV3 的    211     ← 差值 15，即「少注册 15 条」
    image_files 有登记但目录不存在  247
    imageV3 有目录但没登记         198
    两边都能对上的非空目录         178

**任何「按 local_path 直接取图」的方案都会大面积 404。** 但它不是靠改路径
字符串能修的——三版混存，替换规则没法统一。

所以本脚本**不动最终库**（那是论文数据，改动无法验证），而是：

1. 扫 `imageV3/`（目录名 = `accident_id`，实测 427/427 全部能在 accidents
   表里找到，命名规则可靠）
2. 用 `(accident_id, filename)` 从旧 `image_files` / `image_analyses`
   **尽力回填** `source_url` 与 VLM 结论——能对上就填，**对不上留空，不猜**
3. 写到**工作库** `caiems_staging.db` 的 `image_manifest` 表

最终库的 `image_files` 保持原样，作为历史审计留痕。

用法
----
    python3 scripts/build_image_manifest.py --dry-run     # 只扫描与报告，不写库
    python3 scripts/build_image_manifest.py               # 重建 + 写工作库
    python3 scripts/build_image_manifest.py --report r.json
    python3 scripts/build_image_manifest.py --data-dir /path/to/data \\
        --final-db /path/to/caiems_v2.db

`--data-dir` 同时决定图片根目录与默认的报告输出位置。工作库不存在时会提
醒先跑 `scripts/create_staging_db.py`。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from construction_agent.config import settings  # noqa: E402
from construction_agent.web import staging_db as sdb  # noqa: E402
from construction_agent.web.db import resolve_db_path  # noqa: E402

# 只认这些扩展名。imageV3 实测只有 jpg(168) / png(55) / webp(3) 三种，
# 多列几个是防御——真出现了会在报告里的 by_ext 看到。
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

SCAN_CHUNK = 1 << 20  # 1 MiB

# 同一张图覆盖多少个事故目录，就判定为「疑似通用图」。取 3 而不是 2：
# 实测 23 组重复里 15 组只有 2 条，那可能真的是两条事故共用一张图（同一
# 工程的分支事故、转载配图）；而覆盖 ≥3 条的有 8 组，事故还按地域聚成一
# 堆，那种模式不可能是巧合。
SUSPECT_GENERIC_MIN = 3


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------


def sha1_of(path: Path) -> str:
    """算内容哈希。用于识别同一张图出现在多个事故目录的情况。"""
    h = hashlib.sha1()
    with path.open("rb") as f:
        while True:
            block = f.read(SCAN_CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def scan_version_dir(images_dir: Path, data_dir: Path) -> dict:
    """扫一个版本目录，返回记录与诊断。

    目录名直接当 `accident_id`——蓝图 06 §2.3 实测 427/427 全部命中，
    不做任何猜测性映射。
    """
    records: list[dict] = []
    empty_dirs: list[str] = []
    weird: list[str] = []      # 非图片扩展名，跳过但在报告里点名

    for sub in sorted(p for p in images_dir.iterdir() if p.is_dir()):
        files = sorted(p for p in sub.iterdir() if p.is_file())
        if not files:
            # 空目录是有价值的过程信息：曾计划为这些事故配图，最终没有可用图片
            empty_dirs.append(sub.name)
            continue
        for p in files:
            if p.suffix.lower() not in IMAGE_EXTS:
                weird.append(p.relative_to(data_dir).as_posix())
                continue
            records.append({
                "accident_id": sub.name,
                "rel_path": p.relative_to(data_dir).as_posix(),
                "filename": p.name,
                "ext": p.suffix.lower().lstrip("."),
                "bytes": p.stat().st_size,
                "sha1": sha1_of(p),
            })

    return {"records": records, "empty_dirs": empty_dirs, "weird": weird}


# --------------------------------------------------------------------------
# 从最终库取回填来源
# --------------------------------------------------------------------------


def load_backfill(final_db: Path | None) -> tuple[dict, dict]:
    """从最终库读两份回填字典，键都是 `(accident_id, 文件名)`。

    为什么用文件名而不是整条路径：`image_files.local_path` 里 550 条指向
    imageV2、276 条指向 images，那些路径在本轮方案里根本不采用；但**同一张
    图在旧登记里的来源链接仍然有效**。按 `(事故, 文件名)` 匹配，实测 226 个
    文件中能对上 205 个，比按路径匹配高得多。
    """
    if final_db is None or not final_db.exists():
        return {}, {}

    con = sqlite3.connect(f"file:{final_db.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        files: dict[tuple[str, str], dict] = {}
        for r in con.execute(
            "SELECT accident_id, local_path, source_url, source_page_url, "
            "       width, height FROM image_files"
        ):
            if r["local_path"]:
                files[(r["accident_id"], Path(r["local_path"]).name)] = dict(r)

        analyses: dict[tuple[str, str], dict] = {}
        for r in con.execute(
            "SELECT accident_id, local_path, phase1_scene_type, "
            "       phase2_consistency, phase2_confidence, vlm_model "
            "FROM image_analyses"
        ):
            if r["local_path"]:
                # 同一 (事故, 文件名) 可能有多行分析（实测 282 行 / 204 键），
                # 后写的覆盖先写的——同一张图重复分析时取最后一次结论
                analyses[(r["accident_id"], Path(r["local_path"]).name)] = dict(r)
    finally:
        con.close()

    return files, analyses


def known_accident_ids(final_db: Path | None) -> set[str] | None:
    """最终库里的全部事故 id。库不可读时返回 None（跳过归属校验）。"""
    if final_db is None or not final_db.exists():
        return None
    con = sqlite3.connect(f"file:{final_db.as_posix()}?mode=ro", uri=True)
    try:
        return {r[0] for r in con.execute("SELECT id FROM accidents")}
    finally:
        con.close()


# --------------------------------------------------------------------------
# 组装
# --------------------------------------------------------------------------


def build_rows(records: list[dict], files: dict, analyses: dict,
               stamp: str, version: str) -> tuple[list[tuple], dict]:
    """把扫描结果与回填合并成待插入的行。"""
    rows: list[tuple] = []
    filled_url = filled_vlm = 0

    for rec in records:
        key = (rec["accident_id"], rec["filename"])

        old = files.get(key) or {}
        src_url = old.get("source_url")
        src_page = old.get("source_page_url")
        width = old.get("width")
        height = old.get("height")
        if src_url:
            filled_url += 1

        ana = analyses.get(key) or {}
        scene = ana.get("phase1_scene_type")
        consistency = ana.get("phase2_consistency")
        confidence = ana.get("phase2_confidence")
        model = ana.get("vlm_model")
        analyzed = 1 if ana else 0
        if analyzed:
            filled_vlm += 1

        rows.append((
            rec["accident_id"], rec["rel_path"], rec["filename"], rec["ext"],
            rec["bytes"], rec["sha1"],
            src_url, src_page, width, height,
            scene, consistency, confidence, model, analyzed,
            version, stamp,
        ))

    stats = {
        "source_url_matched": filled_url,
        "vlm_matched": filled_vlm,
    }
    return rows, stats


INSERT_SQL = """
INSERT OR REPLACE INTO image_manifest (
    accident_id, rel_path, filename, ext, bytes, sha1,
    source_url, source_page_url, width, height,
    phase1_scene_type, phase2_consistency, phase2_confidence, vlm_model,
    analyzed, version, manifest_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 image_manifest（蓝图 06 §3）")
    ap.add_argument("--data-dir", default=None,
                    help="data 根目录（图片目录在其中）。默认 settings.DATA_DIR")
    ap.add_argument("--version-dir", default="imageV3",
                    help="要扫描的版本目录名，默认 imageV3（最终版）")
    ap.add_argument("--final-db", default=None,
                    help="最终库路径（只读，用于回填与归属校验）")
    ap.add_argument("--staging-db", default=None, help="工作库路径")
    ap.add_argument("--report", default=None, help="报告 JSON 的输出路径")
    ap.add_argument("--dry-run", action="store_true",
                    help="只扫描与报告，不写工作库")
    args = ap.parse_args()

    data_dir = (Path(args.data_dir).expanduser().resolve()
                if args.data_dir else Path(settings.DATA_DIR).resolve())
    images_dir = data_dir / args.version_dir
    if not images_dir.is_dir():
        print(f"找不到图片目录：{images_dir}", file=sys.stderr)
        return 2

    final_db = (Path(args.final_db).expanduser().resolve()
                if args.final_db else resolve_db_path())
    staging_path = (Path(args.staging_db).expanduser().resolve()
                    if args.staging_db
                    else sdb.resolve_staging_path())

    print(f"data 根目录  : {data_dir}")
    print(f"扫描目录     : {images_dir}")
    print(f"最终库（只读）: {final_db}"
          f"{'' if final_db.exists() else '  ← 不存在，回填与校验将跳过'}")
    print(f"工作库       : {staging_path}")

    print("\n── 扫描 ─────────────────────────────────────")
    scanned = scan_version_dir(images_dir, data_dir)
    records = scanned["records"]
    print(f"  图片文件 : {len(records)}")
    print(f"  扩展名   : {Counter(r['ext'] for r in records).most_common()}")
    print(f"  空目录   : {len(scanned['empty_dirs'])}")
    if scanned["weird"]:
        print(f"  非图片文件（跳过）: {len(scanned['weird'])}")

    # 归属校验：目录名 = accident_id 这条规则实测成立，但要留下证据。
    # 空目录一起校验——它们同样要参与 coverage 统计，若目录名不可信，
    # 「234 个空目录」这个数字也就不可信。
    all_dirs = {r["accident_id"] for r in records} | set(scanned["empty_dirs"])
    ids = known_accident_ids(final_db)
    unknown_dirs: list[str] = []
    if ids is not None:
        unknown_dirs = sorted(d for d in all_dirs if d not in ids)
        print(f"  归属校验 : {len(all_dirs)} 个事故目录（含 {len(scanned['empty_dirs'])} 个空目录），"
              f"不在 accidents 表的 {len(unknown_dirs)} 个")
        if unknown_dirs:
            print(f"    ⚠ {unknown_dirs[:10]}")
    else:
        print("  归属校验 : 跳过（最终库不可读）")

    print("\n── 回填 ─────────────────────────────────────")
    files, analyses = load_backfill(final_db)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 表里的 version 存 'v3' 这种短名（列定义 DEFAULT 'v3'），从目录名推出来
    version = args.version_dir.lower().replace("image", "") or "v3"
    rows, fill = build_rows(records, files, analyses, stamp, version)
    n = len(rows) or 1
    print(f"  来源链接 : {fill['source_url_matched']}/{len(rows)}"
          f"  ({fill['source_url_matched'] / n:.1%})")
    print(f"  VLM 结论 : {fill['vlm_matched']}/{len(rows)}"
          f"  ({fill['vlm_matched'] / n:.1%})")
    print("  （对不上的留空，不猜——它们正是旧登记里缺的那部分）")

    # 跨目录重复：同一张图出现在多个事故目录。实测 23 组，涉及 70 条记录
    # （226 条里的 31%）。其中 8 组覆盖 ≥3 个事故——武汉一批、广州一批、
    # 沈阳一批，事故按地域聚在一起，而且来源 URL 里有 `n.sinaimg.cn/default/`，
    # 基本可以断定是**图床返回的默认占位图**，不是现场照片。
    # 判据只认内容（同一个 sha1），不猜 URL 模式——URL 会变，内容不会。
    by_sha: dict[str, set[str]] = defaultdict(set)
    for r in records:
        by_sha[r["sha1"]].add(r["accident_id"])
    cross = {k: sorted(v) for k, v in by_sha.items() if len(v) > 1}
    suspect = {k: v for k, v in cross.items() if len(v) >= SUSPECT_GENERIC_MIN}
    cross_records = sum(len(v) for v in cross.values())
    suspect_records = sum(len(v) for v in suspect.values())
    print(f"\n  跨事故重复的图片 : {len(cross)} 组 / {cross_records} 条记录"
          f"（占全部 {len(records)} 条的 {cross_records / n:.1%}）")
    print(f"    其中覆盖 ≥{SUSPECT_GENERIC_MIN} 个事故的 {len(suspect)} 组 / "
          f"{suspect_records} 条：同一张图配给了多条事故，**疑似图床占位图**")

    covered = len({r["accident_id"] for r in records})
    report = {
        "generated_at": stamp,
        "data_dir": str(data_dir),
        "version_dir": args.version_dir,
        "final_db": str(final_db),
        "staging_db": str(staging_path),
        "files": len(records),
        "accidents_with_images": covered,
        "empty_dirs": len(scanned["empty_dirs"]),
        "empty_dir_ids": sorted(scanned["empty_dirs"]),
        "by_ext": dict(Counter(r["ext"] for r in records)),
        "total_bytes": sum(r["bytes"] for r in records),
        "backfill": fill,
        "unknown_accident_dirs": unknown_dirs,
        "skipped_non_image": scanned["weird"],
        "cross_accident_duplicates": cross,
        "suspect_generic": {
            "min_accidents": SUSPECT_GENERIC_MIN,
            "groups": len(suspect),
            "records": suspect_records,
            "by_sha": {k: v for k, v in sorted(suspect.items())},
        },
        "dry_run": bool(args.dry_run),
    }

    if args.dry_run:
        print("\n--dry-run：不写工作库。")
    else:
        if not sdb.is_initialized(staging_path):
            print(f"\n工作库未初始化：{staging_path}\n"
                  f"先跑：python3 scripts/create_staging_db.py", file=sys.stderr)
            return 3

        print("\n── 写工作库 ─────────────────────────────────")
        with sdb.write_tx(staging_path) as con:
            # 先按版本清空再插入，而不是只靠 INSERT OR REPLACE：本脚本的定位
            # 是「以文件系统为真源重建」，磁盘上删掉的图必须从表里消失。
            # 只留 INSERT OR REPLACE 的话，旧行会一直躺在表里。
            con.execute("DELETE FROM image_manifest WHERE version = ?",
                        (version,))
            con.executemany(INSERT_SQL, rows)
            total = con.execute(
                "SELECT COUNT(*) FROM image_manifest").fetchone()[0]
        print(f"  已写入 {len(rows)} 行（表内合计 {total} 行）")

    report_path = (Path(args.report).expanduser().resolve() if args.report
                   else data_dir / "image_manifest_report.json")
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入：{report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
