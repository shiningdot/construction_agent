"""重建 `image_manifest`（以文件系统为真源）。蓝图 06 §3、复核见蓝图 08。

背景（实测，2026-09-30；2026-10-01 复核更正）
-------------------------------------------
最终库 `image_files`（1,037 行）的 `local_path` **三版混存**：

    imageV2/  550 条 · images/  276 条 · imageV3/  211 条

而磁盘上是另一番景象：`images/` 263 个文件、`imageV2/` 464 个、`imageV3/`
226 个。这张对照表当初被读成了「路径错配、按 local_path 取图会大面积
404」，并据此把展示层截到只剩 `imageV3/`。

**那个结论是错的**：逐条 stat 复核得到 `data/<local_path>` 命中
1,037/1,037，缺失 0——`local_path` 本就是相对 `data/` 的，三版目录一直
躺在磁盘上。当初的「247 条找不到目录」是拿 `local_path` 的 basename 去
`imageV3/` 一个目录里找的结果，口径错了。为躲开 19 组跨版本重复图而丢掉
725 张图、366 个有图事故，代价是收益的 38 倍。详见
`docs/web/08_图片资产复核与更正.md`。

所以本脚本**不动最终库**（那是论文数据，改动无法验证），而是：

1. 扫三版图片目录（默认 `images/` + `imageV2/` + `imageV3/`；目录名 =
   `accident_id`，实测 427/427 全部能在 accidents 表里找到，命名规则可靠），
   每条记录带 `version` 短名（`v1`/`v2`/`v3`）
2. 用 `(accident_id, filename)` 从旧 `image_files` / `image_analyses`
   **尽力回填** `source_url` 与 VLM 结论——能对上就填，**对不上留空，不猜**
3. 写到**工作库** `caiems_staging.db` 的 `image_manifest` 表

最终库的 `image_files` 保持原样，作为历史审计留痕。

用法
----
    python3 scripts/build_image_manifest.py --dry-run     # 只扫描与报告，不写库
    python3 scripts/build_image_manifest.py               # 三版重建 + 写工作库
    python3 scripts/build_image_manifest.py --version-dir imageV3   # 只重建最终版
    python3 scripts/build_image_manifest.py --report r.json
    python3 scripts/build_image_manifest.py --data-dir /path/to/data \\
        --final-db /path/to/caiems_v2.db

`--version-dir` 可给多个目录名；只重建某几版时**不会**动其它版本已有的行。
`--data-dir` 同时决定图片根目录与默认的报告输出位置。工作库不存在时会提
醒先跑 `scripts/create_staging_db.py`。

两条容易吃亏的地方（2026-10-01 补）：

- **`--data-dir` 默认走主仓库根**（上溯 `.git`），不是 `settings.DATA_DIR`。
  后者在 worktree 里指向 worktree 自己的 `data/`——那里只有一个空的 `images/`，
  照着扫会得到一份 0 张的清单。**扫到 0 张现在退 `2`**，不再安静地"成功"。
- **报告默认写在 `--data-dir` 下**。于是从 worktree 跑，报告会落到**主仓库**的
  `data/` 里（因为图片资产在那儿）。只存疑不想留下痕迹时，记得显式 `--report`。
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
sys.path.insert(0, str(Path(__file__).resolve().parent))        # scripts/_common

from _common import main_repo_root  # noqa: E402
from construction_agent.web import staging_db as sdb  # noqa: E402

# 只认这些扩展名。imageV3 实测只有 jpg(168) / png(55) / webp(3) 三种，
# 多列几个是防御——真出现了会在报告里的 by_ext 看到。
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

SCAN_CHUNK = 1 << 20  # 1 MiB

# 文件头魔数白名单。`RIFF` 一并收下 webp 与 wav——两者的容器头相同，
# 真实扩展名已由 IMAGE_EXTS 把关，这里只负责把「内容根本不是图片」的挑出来。
MAGIC_PREFIXES = (b"\x89PNG", b"\xff\xd8\xff", b"RIFF", b"GIF8", b"BM")

# 同一张图覆盖多少个事故目录，就判定为「疑似通用图」。取 3 而不是 2：
# 实测 23 组重复里 15 组只有 2 条，那可能真的是两条事故共用一张图（同一
# 工程的分支事故、转载配图）；而覆盖 ≥3 条的有 8 组，事故还按地域聚成一
# 堆，那种模式不可能是巧合。
SUSPECT_GENERIC_MIN = 3

# 版本目录名 → 表里的版本短名。**必须显式映射**，不能从目录名推：原先那句
# `args.version_dir.lower().replace("image", "")` 对 `imageV2`/`imageV3` 恰好
# 得到 `v2`/`v3`，但对 `images` 得到的是 `s`（"images" 去掉 "image"），
# 一个既不在 `VERSION_BADGES` 里、也没人认识的短名。
VERSION_DIRS: dict[str, str] = {"images": "v1", "imageV2": "v2", "imageV3": "v3"}


# --------------------------------------------------------------------------
# 扫描
# --------------------------------------------------------------------------


def probe(path: Path) -> tuple[str, bytes]:
    """一次读盘拿到内容哈希与文件头 4 字节。

    哈希用于识别同一张图出现在多个事故目录；文件头用于**验真**——实测有
    3 个文件扩展名是 `.webp`、内容却是 HTML（下载失败存了错误页）或
    MP4/ISO-BMFF。只看扩展名会把它们当图片送进浏览器，结果是三个破图。
    反正整份文件都要读来算哈希，取前 4 字节不额外花代价。
    """
    h = hashlib.sha1()
    head = b""
    with path.open("rb") as f:
        block = f.read(SCAN_CHUNK)
        head = block[:4]
        while block:
            h.update(block)
            block = f.read(SCAN_CHUNK)
    return h.hexdigest(), head


def scan_version_dir(images_dir: Path, data_dir: Path) -> dict:
    """扫一个版本目录，返回记录与诊断。

    目录名直接当 `accident_id`——蓝图 06 §2.3 实测 427/427 全部命中，
    不做任何猜测性映射。
    """
    records: list[dict] = []
    empty_dirs: list[str] = []
    weird: list[str] = []      # 非图片扩展名，跳过但在报告里点名
    bad_magic: list[str] = []  # 扩展名像图片、内容不是

    # 跳过下划线开头的目录：那是管线的工作区（images/_rejected 存被 CLIP
    # 剔除的图），不是事故目录，不该参与归属校验，也不该进清单
    for sub in sorted(p for p in images_dir.iterdir()
                      if p.is_dir() and not p.name.startswith("_")):
        files = sorted(p for p in sub.iterdir() if p.is_file())
        if not files:
            # 空目录是有价值的过程信息：曾计划为这些事故配图，最终没有可用图片
            empty_dirs.append(sub.name)
            continue
        kept = 0
        for p in files:
            if p.suffix.lower() not in IMAGE_EXTS:
                weird.append(p.relative_to(data_dir).as_posix())
                continue
            sha1, head = probe(p)
            if not head.startswith(MAGIC_PREFIXES):
                bad_magic.append(p.relative_to(data_dir).as_posix())
                continue
            kept += 1
            records.append({
                "accident_id": sub.name,
                "rel_path": p.relative_to(data_dir).as_posix(),
                "filename": p.name,
                "ext": p.suffix.lower().lstrip("."),
                "bytes": p.stat().st_size,
                "sha1": sha1,
            })
        # 目录里全是坏文件时，它事实上就是空的——配了图但一张都放不出来，
        # 与「没配图」在展示上是同一件事，归入空目录而不是留一条空记录。
        if not kept:
            empty_dirs.append(sub.name)

    return {"records": records, "empty_dirs": empty_dirs, "weird": weird,
            "bad_magic": bad_magic}


# --------------------------------------------------------------------------
# 从最终库取回填来源
# --------------------------------------------------------------------------


def load_backfill(final_db: Path | None) -> tuple[dict, dict]:
    """从最终库读两份回填字典，键都是 `(accident_id, 文件名)`。

    为什么用文件名而不是整条路径：`image_files` 的 `local_path` 按版本混存，
    同一张图在不同版本目录下路径不同；按 `(事故, 文件名)` 匹配才能把旧登记
    的来源链接接到三版的图上。实测 951 条里对上 834 条（87.7%）。
    """
    if final_db is None or not final_db.exists():
        return {}, {}

    con = sqlite3.connect(f"file:{final_db.as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA foreign_keys = ON")  # 连接级开关，SQLite 默认 OFF
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
                    help="data 根目录（图片目录在其中）。默认按主仓库根解析"
                         "（上溯 .git），与七条只读核查同一口径")
    ap.add_argument("--version-dir", nargs="+", default=list(VERSION_DIRS),
                    help="要扫描的版本目录名，可给多个；默认三版全部"
                         f"（{'、'.join(VERSION_DIRS)}）。只想重建最终版就 "
                         f"传 --version-dir imageV3")
    ap.add_argument("--final-db", default=None,
                    help="最终库路径（只读，用于回填与归属校验）")
    ap.add_argument("--staging-db", default=None, help="工作库路径")
    ap.add_argument("--report", default=None, help="报告 JSON 的输出路径")
    ap.add_argument("--dry-run", action="store_true",
                    help="只扫描与报告，不写工作库")
    args = ap.parse_args()

    # 默认走**主仓库**的 data/，不用 `settings.DATA_DIR`。两套解析在 worktree 里
    # 指向不同的目录：后者是 `config.py` 的上两级（worktree 自己的 `data/`，那里
    # 只有一份空的 `images/`，没有 `imageV2`/`imageV3`），照它扫会得到一份 0 张的
    # 「成功」清单。图片资产在真仓库里，脚本就该往那儿看。
    data_dir = (Path(args.data_dir).expanduser().resolve()
                if args.data_dir else main_repo_root() / "data")
    unknown_dirs_arg = [v for v in args.version_dir if v not in VERSION_DIRS]
    if unknown_dirs_arg:
        print(f"未知的版本目录：{'、'.join(unknown_dirs_arg)}"
              f"（可选 {'、'.join(VERSION_DIRS)}）", file=sys.stderr)
        return 2

    final_db = (Path(args.final_db).expanduser().resolve()
                if args.final_db
                else main_repo_root() / "data" / "caiems_v2.db")
    staging_path = (Path(args.staging_db).expanduser().resolve()
                    if args.staging_db
                    else sdb.resolve_staging_path())

    print(f"data 根目录  : {data_dir}")
    print(f"扫描目录     : {'、'.join(args.version_dir)}")
    print(f"最终库（只读）: {final_db}"
          f"{'' if final_db.exists() else '  ← 不存在，回填与校验将跳过'}")
    print(f"工作库       : {staging_path}")

    print("\n── 扫描 ─────────────────────────────────────")
    records: list[dict] = []
    empty_dirs: list[str] = []
    weird: list[str] = []
    bad_magic: list[str] = []
    by_version: dict[str, int] = {}     # 短名 → 文件数
    scans: dict[str, dict] = {}         # 短名 → 扫描结果，供 build_rows 分组
    for vdir in args.version_dir:
        d = data_dir / vdir
        if not d.is_dir():
            print(f"  {vdir:<9} : 目录不存在，跳过")
            continue
        scanned = scan_version_dir(d, data_dir)
        short = VERSION_DIRS[vdir]
        scans[short] = scanned
        records += scanned["records"]
        empty_dirs += scanned["empty_dirs"]
        weird += scanned["weird"]
        bad_magic += scanned["bad_magic"]
        by_version[short] = len(scanned["records"])
        note = (f"，剔除内容非图片 {len(scanned['bad_magic'])} 个"
                if scanned["bad_magic"] else "")
        print(f"  {vdir:<9} : {len(scanned['records'])} 张"
              f"（空目录 {len(scanned['empty_dirs'])}{note}）")
    if not scans:
        print("三版图片目录都不存在，无从扫描。", file=sys.stderr)
        return 2
    if not records:
        # 「目录在、里面一张图都没有」和「目录不存在」是同一件事的两副面孔，
        # 原先只有后者退非 0，前者会安静地走完全程、把一份空清单写进工作库。
        # 空清单不是重建成功，是扫错了地方——这类判据两头都真，等于没有判据。
        print(f"\n✗ 扫到 0 张图——这不是「重建成功」，是「扫错了地方」。", file=sys.stderr)
        print(f"  data 根目录：{data_dir}", file=sys.stderr)
        print("  真图片资产在**主仓库根**的 data/ 下（images/ + imageV2/ + imageV3/）。",
              file=sys.stderr)
        print("  若确实要扫别处，显式传 --data-dir。", file=sys.stderr)
        return 2
    print(f"  图片文件 : {len(records)}")
    print(f"  扩展名   : {Counter(r['ext'] for r in records).most_common()}")
    print(f"  空目录   : {len(empty_dirs)}")
    if weird:
        print(f"  非图片文件（跳过）: {len(weird)}")
    if bad_magic:
        # 不进 manifest：它们的扩展名骗过了目视检查，内容却是错误页或视频，
        # 送进浏览器只会得到破图。列出来是为了让「为什么这条事故少一张图」
        # 有据可查。
        print(f"  内容不是图片（剔除）: {len(bad_magic)}")
        for x in bad_magic[:5]:
            print(f"    {x}")

    # 归属校验：目录名 = accident_id 这条规则实测成立，但要留下证据。
    # 空目录一起校验——它们同样要参与 coverage 统计，若目录名不可信，
    # 「234 个空目录」这个数字也就不可信。
    all_dirs = {r["accident_id"] for r in records} | set(empty_dirs)
    ids = known_accident_ids(final_db)
    unknown_dirs: list[str] = []
    if ids is not None:
        unknown_dirs = sorted(d for d in all_dirs if d not in ids)
        print(f"  归属校验 : {len(all_dirs)} 个事故目录（含 {len(empty_dirs)} 个空目录），"
              f"不在 accidents 表的 {len(unknown_dirs)} 个")
        if unknown_dirs:
            print(f"    ⚠ {unknown_dirs[:10]}")
    else:
        print("  归属校验 : 跳过（最终库不可读）")

    print("\n── 回填 ─────────────────────────────────────")
    files, analyses = load_backfill(final_db)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 按版本分组构建：同一条 `(accident_id, filename)` 回填键在三版里指向不同
    # 文件，version 是行的属性，只能整组传。
    rows: list[tuple] = []
    fill = {"source_url_matched": 0, "vlm_matched": 0}
    for short, scan in scans.items():
        part, f = build_rows(scan["records"], files, analyses, stamp, short)
        rows += part
        for k in fill:
            fill[k] += f[k]
    n = len(rows) or 1
    print(f"  来源链接 : {fill['source_url_matched']}/{len(rows)}"
          f"  ({fill['source_url_matched'] / n:.1%})")
    print(f"  VLM 结论 : {fill['vlm_matched']}/{len(rows)}"
          f"  ({fill['vlm_matched'] / n:.1%})")
    print("  （对不上的留空，不猜——它们正是旧登记里缺的那部分）")

    # 跨目录重复：同一张图出现在多个事故目录。三版合并后实测 92 组，涉及
    # 356 条记录（951 条里的 37.4%）。其中 44 组覆盖 ≥3 个事故——武汉一批、
    # 广州一批、沈阳一批，事故按地域聚在一起，而且来源 URL 里有
    # `n.sinaimg.cn/default/`，基本可以断定是**图床返回的默认占位图**，
    # 不是现场照片。
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
        "version_dirs": list(args.version_dir),
        "by_version": by_version,
        "final_db": str(final_db),
        "staging_db": str(staging_path),
        "files": len(records),
        "accidents_with_images": covered,
        "empty_dirs": len(empty_dirs),
        "empty_dir_ids": sorted(empty_dirs),
        "by_ext": dict(Counter(r["ext"] for r in records)),
        "total_bytes": sum(r["bytes"] for r in records),
        "backfill": fill,
        "unknown_accident_dirs": unknown_dirs,
        "skipped_non_image": weird,
        "non_image_content": bad_magic,
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
            #
            # 清空范围是**本次实际扫到的版本**，不是全部：只跑
            # `--version-dir imageV3` 时不能顺手把 v1/v2 的行删了——它们的
            # 磁盘文件还在，删了就再也展示不出来。
            marks = ",".join("?" * len(by_version))
            con.execute(
                f"DELETE FROM image_manifest WHERE version IN ({marks})",
                tuple(by_version))
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
