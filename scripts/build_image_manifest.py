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

4. 顺带把 `data/images/` **顶层**的平铺文件（不属于任何事故子目录）纳入
   清单，版本短名 `flat`

平铺池（2026-10-01 补）
-----------------------
`data/images/` 顶层还躺着 3,701 个文件（273 MB），不在任何事故子目录里——
初版管线「按件落盘」的产物，从未进过清单，网页上一张都不显示。文件名形态
是 `<事故标识>_<序号>.<ext>`，标识分三类：

- **8 位 hex**（旧库 `accident_id` 的形态）1,838 种。两道桥接回主库：
  旧库 `accidents.title` 归一化后与主库标题唯一对上（**语义证据**），
  或主库 12 位 id 的前 8 位与它相同（**结构证据**，实测 2,623 个 id
  前 8 位零碰撞，两桥重叠部分结论零冲突）。两桥互补——标题桥救得了
  「旧库与主库 id 不同源但标题相同」的，前缀桥救得了「标题被改写或
  为空」的。
- **中文事故名** 3 种。主库标题与它既不相等、也不构成子串（「齐齐哈尔
  体育馆」对「齐齐哈尔市第三十四中学体育馆屋顶坍塌事故」），只能人工
  判定，依据逐条写在 `FLAT_MANUAL_OWNER`。
- **第三套 8 位 hex** 668 种。既不在旧库 `accidents` 表，也不在主库 id
  前缀里，归属线索为零。

归属不上的**留空不猜**。`7_22广州塔吊倒塌事故` 两组共 6 张属明确排除，
理由写在 `FLAT_DENIED`（主库含「广州」的 28 条、含「塔吊」的 10 条里，
没有一条是它描述的事故）。同一事故内按 `sha1` 去重——平铺池 3,701 个
文件只有 1,323 个唯一 sha1，不去重会让详情页出现十张一样的图。

用法
----
    python3 scripts/build_image_manifest.py --dry-run     # 只扫描与报告，不写库
    python3 scripts/build_image_manifest.py               # 三版 + 平铺池重建，写工作库
    python3 scripts/build_image_manifest.py --version-dir imageV3   # 只重建最终版
    python3 scripts/build_image_manifest.py --no-flat-pool          # 不要平铺池
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
import re
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

# 平铺池的版本短名。`data/images/` 顶层还躺着 3,701 个**不在任何事故子目录
# 里**的文件（273 MB）——它们是初版管线「按件落盘」的产物，从未进过清单，
# 于是网页上一张都不显示（2026-10-01 查证，详见报告里的 flat_pool 段）。
# 给它们一个独立短名，既不冒充 v1，也让前端能区分来源。
FLAT_VERSION = "flat"

# 平铺文件名的形态是 `<事故标识>_<序号>.<ext>`，标识本身才是归属线索。
_FLAT_TAIL = re.compile(r"^(.*)_\d+$")
# 旧库事故 id 是 8 位 hex；主库是 12 位。
_HEX8 = re.compile(r"^[0-9a-f]{8}$")
# 标题归一化：只去标点、空白与全半角引号，**不做同义替换**——换成"市"、
# 补全"事故"这类动作会制造本不存在的匹配。
_TITLE_NOISE = re.compile(r"[\s_·、,，。.（）()「」【】\-—\"'“”]")

# 平铺池里只有 5 个标识不是 8 位 hex，其中 3 个只能人工判定：主库标题与它
# 既不相等、也不构成子串（「齐齐哈尔体育馆」对「齐齐哈尔市第三十四中学
# 体育馆屋顶坍塌事故」，中间隔着"市第三十四中学"）。**逐条写明依据**，
# 不做批量模糊匹配——模糊匹配在这批数据上会静默错配。
FLAT_MANUAL_OWNER: dict[str, str] = {
    "齐齐哈尔体育馆": "ae16d12d2c7f",
    "齐齐哈尔体育馆坍塌事故": "ae16d12d2c7f",
    "湖南邵东一水库隧洞施工事故_致3死": "56086afa9706",
}

# 不归属的标识与原因，写进报告备查。
FLAT_DENIED: dict[str, str] = {
    "7_22广州塔吊倒塌事故":
        "主库无对应：2,623 条里含「广州」的 28 条、含「塔吊」的 10 条，"
        "没有一条是 7·22 广州塔吊倒塌事故",
    "广州7_22塔吊倒塌事故":
        "同上，与上一组是同一事故的两种命名",
}

# 默认旧库。它是 8 位 accident_id 能接回主库的唯一桥梁：平铺文件的 hex 前缀
# 是旧库 id，旧库 accidents.title 归一化后与主库 title 对得上，才谈得上归属。
DEFAULT_LEGACY_DB = "db_backups/caiems.db.bak.20260803"


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
# 平铺池（data/images/ 顶层，不属于任何事故目录）
# --------------------------------------------------------------------------


def flat_ident(stem: str) -> str:
    """`<标识>_<序号>` → 标识。没有序号后缀时原样返回。

    「湖南邵东一水库隧洞施工事故_致3死」的 `_致3死` 不会被剥掉——正则要求
    下划线之后**全是数字**，`致3死` 不满足。
    """
    m = _FLAT_TAIL.match(stem)
    return m.group(1) if m else stem


def norm_title(t: str | None) -> str:
    return _TITLE_NOISE.sub("", t or "")


def build_owner_map(final_db: Path, legacy_db: Path | None,
                    idents: set[str]) -> tuple[dict, dict]:
    """平铺标识 → 主库 `accident_id`，外加一份诊断。

    三条来源，优先级从高到低：手工表 → 旧库 title 桥（8 位 hex）→ 主库
    title 直接匹配（中文式标识）。

    **唯一命中才收**。主库有 4 组归一化后重名的 title（杭州地铁塌陷、
    黔西南义龙一中、云南澄江宽澄鼎元、甘肃甘州 7·2 升降机，各 ×2～×3），
    它们对应的是不同事故——把图随便挂给其中一条就是伪造归属，宁可留空。
    """
    main = sqlite3.connect(f"file:{final_db.as_posix()}?mode=ro", uri=True)
    try:
        titles = [(r[0], r[1]) for r in main.execute(
            "SELECT id, title FROM accidents")]
    finally:
        main.close()

    by_norm: dict[str, list[str]] = defaultdict(list)
    by_prefix: dict[str, list[str]] = defaultdict(list)
    for aid, t in titles:
        by_norm[norm_title(t)].append(aid)
        by_prefix[aid[:8]].append(aid)
    known_ids = {aid for aid, _ in titles}

    # 手工表先做存在性校验：指向一个不存在的事故 id 是脚本 bug，不是数据
    # 问题，不能悄悄咽下去——它会让「这几张图有归属」变成一句空话。
    # 只校验**本次真的要判定**的标识：手工表是项目级常量，而调用方可能只是
    # 拿一个小库试跑，库里没有那些事故并不代表映射写错了。
    bad = {i: a for i, a in FLAT_MANUAL_OWNER.items()
           if i in idents and a not in known_ids}
    if bad:
        raise SystemExit(f"手工映射指向不存在的事故 id：{bad}")

    old_title: dict[str, str] = {}
    legacy_ok = legacy_db is not None and legacy_db.exists()
    if legacy_ok:
        con = sqlite3.connect(f"file:{legacy_db.as_posix()}?mode=ro", uri=True)
        try:
            old_title = {r[0]: r[1] for r in con.execute(
                "SELECT id, title FROM accidents")}
        finally:
            con.close()

    # 平铺池里出现过的标识，交给本函数逐条判定
    owner: dict[str, str] = {}
    diag: dict[str, object] = {
        "legacy_db": str(legacy_db) if legacy_ok else None,
        "legacy_db_readable": legacy_ok,
        "manual": {}, "bridged": {}, "by_prefix": {}, "by_title": {},
        "ambiguous": {}, "prefix_ambiguous": {}, "no_main_match": [],
        "unresolved_hex": [], "no_legacy_title": [],
    }

    for ident in sorted(idents):
        if ident in FLAT_DENIED:
            continue
        if ident in FLAT_MANUAL_OWNER:
            owner[ident] = FLAT_MANUAL_OWNER[ident]
            diag["manual"][ident] = FLAT_MANUAL_OWNER[ident]
            continue

        if _HEX8.match(ident):
            t = old_title.get(ident) if legacy_ok else None

            # ① 语义证据：旧库 title → 主库 title 归一化后唯一命中。
            #    这条最强，优先用。
            if t and norm_title(t):
                cands = by_norm.get(norm_title(t), [])
                if len(cands) == 1:
                    owner[ident] = cands[0]
                    diag["bridged"][ident] = cands[0]
                    continue
                if len(cands) > 1:
                    diag["ambiguous"][ident] = cands
                    continue

            # ② 结构证据：主库 12 位 id 的前 8 位就是旧库那个 8 位 id。
            #    实测主库 2,623 个 id 前 8 位**零碰撞**，所以前缀唯一命中
            #    不会挂错；与 ① 有 51 种重叠且**结论零冲突**，互为旁证。
            #    它救回的正是 ① 够不着的那批——旧库重建时标题被改写
            #    （「武汉在建长江大桥引桥发生坍塌事故」→「武汉天兴洲大桥
            #    引桥钢梁断裂坍塌事故」）或干脆为空的记录。
            pfx = by_prefix.get(ident, [])
            if len(pfx) == 1:
                owner[ident] = pfx[0]
                diag["by_prefix"][ident] = pfx[0]
                continue
            if len(pfx) > 1:
                diag["prefix_ambiguous"][ident] = pfx
                continue

            # ③ 两桥都不通：记下原因，不归属。
            if t is None:
                # 第三套 id 体系（711 种 / 1,422 张）——旧库 accidents 表里
                # 根本没有它，主库前缀里也没有
                diag["unresolved_hex"].append(ident)
            elif not norm_title(t):
                diag["no_legacy_title"].append(ident)
            else:
                diag["no_main_match"].append(ident)
        else:
            cands = by_norm.get(norm_title(ident), [])
            if len(cands) == 1:
                owner[ident] = cands[0]
                diag["by_title"][ident] = cands[0]
            elif len(cands) > 1:
                diag["ambiguous"][ident] = cands
            else:
                diag["no_main_match"].append(ident)

    return owner, diag


def scan_flat_pool(images_dir: Path, data_dir: Path,
                   owner_map: dict) -> dict:
    """扫 `data/images/` 顶层的平铺文件，返回结构与 `scan_version_dir` 同构。

    与版本目录扫描的两点不同：

    1. `accident_id` **不是目录名**，而是文件名前缀经 `owner_map` 归属到的
       主库 id。归属不上的文件直接丢弃——留空比挂错强。
    2. 同一事故内按 `sha1` 去重。平铺池实测 3,701 个文件只有 1,323 个唯一
       sha1，重复率极高（初版管线一张图按多个来源页各存一份），不去重的
       话详情页会出现十张一模一样的图。

    跨事故的重复**保留**——那是 `cross_accident_duplicates` 要统计的，前端
    会打 `shared` 标记。
    """
    records: list[dict] = []
    weird: list[str] = []
    bad_magic: list[str] = []
    denied_hits: Counter = Counter()

    seen: dict[tuple[str, str], dict] = {}
    for p in sorted(q for q in images_dir.iterdir() if q.is_file()):
        ident = flat_ident(p.stem)
        if ident in FLAT_DENIED:
            denied_hits[ident] += 1
            continue
        aid = owner_map.get(ident)
        if aid is None:
            continue
        if p.suffix.lower() not in IMAGE_EXTS:
            weird.append(p.relative_to(data_dir).as_posix())
            continue
        sha1, head = probe(p)
        if not head.startswith(MAGIC_PREFIXES):
            bad_magic.append(p.relative_to(data_dir).as_posix())
            continue
        key = (aid, sha1)
        if key in seen:
            continue
        rec = {
            "accident_id": aid,
            "rel_path": p.relative_to(data_dir).as_posix(),
            "filename": p.name,
            "ext": p.suffix.lower().lstrip("."),
            "bytes": p.stat().st_size,
            "sha1": sha1,
        }
        seen[key] = rec
        records.append(rec)

    return {"records": records, "empty_dirs": [], "weird": weird,
            "bad_magic": bad_magic, "denied_hits": dict(denied_hits)}


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
    ap.add_argument("--legacy-db", default=None,
                    help="旧库路径（只读）。平铺文件的 8 位 hex 前缀要靠它"
                         "的 accidents.title 才接得回主库。默认 "
                         f"<data-dir>/{DEFAULT_LEGACY_DB}")
    ap.add_argument("--no-flat-pool", action="store_true",
                    help="跳过 data/images/ 顶层的平铺文件（默认纳入）")
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

    # 平铺池：与版本目录各自独立，但在清单里同样是「一批记录 + 一个短名」，
    # 所以塞进同一个 scans 字典，清空与写库都走同一套逻辑。
    flat_scan: dict | None = None
    if not args.no_flat_pool:
        flat_dir = data_dir / "images"
        if flat_dir.is_dir():
            idents = {flat_ident(q.stem) for q in flat_dir.iterdir() if q.is_file()}
            legacy_db = (Path(args.legacy_db).expanduser().resolve()
                         if args.legacy_db else data_dir / DEFAULT_LEGACY_DB)
            owner_map, owner_diag = build_owner_map(final_db, legacy_db, idents)
            flat_scan = scan_flat_pool(flat_dir, data_dir, owner_map)
            flat_scan["ident_total"] = len(idents)
            flat_scan["owner_total"] = len(owner_map)
            flat_scan["owner_diag"] = owner_diag
            scans[FLAT_VERSION] = flat_scan
            records += flat_scan["records"]
            weird += flat_scan["weird"]
            bad_magic += flat_scan["bad_magic"]
            by_version[FLAT_VERSION] = len(flat_scan["records"])

            print("\n── 平铺池（data/images/ 顶层，不属于任何事故目录）─────")
            print(f"  平铺文件 : {sum(1 for q in flat_dir.iterdir() if q.is_file())} 个"
                  f" / {len(idents)} 种标识")
            print(f"  可归属   : {len(owner_map)} 种标识"
                  f" → 收录 {len(flat_scan['records'])} 张"
                  f"（同事故同内容已去重）")
            d = owner_diag
            print(f"  归属明细 : 手工表 {len(d['manual'])}"
                  f" · 旧库标题桥 {len(d['bridged'])}"
                  f" · id 前缀桥 {len(d['by_prefix'])}"
                  f" · 主库标题直配 {len(d['by_title'])}")
            print(f"  未归属   : 旧库无此 id {len(d['unresolved_hex'])} 种"
                  f" · 旧库标题为空 {len(d['no_legacy_title'])} 种"
                  f" · 两桥都不通 {len(d['no_main_match'])} 种")
            if d["ambiguous"] or d["prefix_ambiguous"]:
                print(f"  ⚠ 歧义（已跳过）: 标题 {len(d['ambiguous'])} 种"
                      f" · 前缀 {len(d['prefix_ambiguous'])} 种")
            if flat_scan["denied_hits"]:
                for k, v in flat_scan["denied_hits"].items():
                    print(f"  明确排除 : {k} {v} 张 —— {FLAT_DENIED[k]}")

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
        "flat_pool": ({
            "version": FLAT_VERSION,
            "idents": flat_scan["ident_total"],
            "owned_idents": flat_scan["owner_total"],
            "records": len(flat_scan["records"]),
            "covered_accidents": len({r["accident_id"]
                                      for r in flat_scan["records"]}),
            "denied": FLAT_DENIED,
            "denied_hits": flat_scan["denied_hits"],
            "owner_diag": flat_scan["owner_diag"],
        } if flat_scan else None),
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
