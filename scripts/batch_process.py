"""批量处理 data_STH.csv 中的事故数据。

增强功能：
- 自动重试（指数退避），区分 API 错误与其他异常
- 自动保存 checkpoint，支持断电续传
- 完成时生成错误分类报告

用法:
    # 首次运行 0-199 条
    python scripts/batch_process.py --start 0 --count 200

    # 续传（跳过已有记录） 0-399 条（补跑第一批失败10条 + 第二批200条）
    python scripts/batch_process.py --start 0 --end 400 --resume --retry 3

    # 指定范围
    python scripts/batch_process.py --start 200 --count 200 --retry 3
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
import traceback
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field

# 【重要】必须先配置 logging，再导入项目模块（确保子模块的 logger 继承配置）
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
logger = logging.getLogger(__name__)

# 确保能找到项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from construction_agent.config import settings
from construction_agent.extraction.claude_extractor import ClaudeExtractor
from construction_agent.database.dataset_manager import DatasetManager
from construction_agent.search.search_integration import SearchIntegrator


# ── 错误分类 ──

class ErrorCategory:
    """错误分类常量。"""
    API_ERROR = "API错误"         # 网络超时、限流、500等
    PARSE_ERROR = "解析错误"       # API返回了但JSON解析失败
    EMPTY_RESULT = "空结果"        # extract返回None但无异常
    VALIDATION_ERROR = "校验错误"  # Pydantic校验失败
    EMPTY_INPUT = "空输入"        # CSV中爬虫字段为空
    OTHER = "其他异常"            # 未分类异常


@dataclass
class FailureRecord:
    """单条失败记录。"""
    seq: int
    global_idx: int
    title: str
    error_category: str
    error_message: str
    attempts: int


@dataclass
class BatchReport:
    """批次完成报告。"""
    start: int
    end: int
    total: int
    success: int = 0
    skipped: int = 0
    failures: list[FailureRecord] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""

    @property
    def failed(self) -> int:
        return len(self.failures)

    def print_report(self):
        """打印最终报告。"""
        duration = "?"
        if self.start_time and self.end_time:
            fmt = "%Y-%m-%d %H:%M:%S"
            try:
                s = datetime.strptime(self.start_time, fmt)
                e = datetime.strptime(self.end_time, fmt)
                delta = e - s
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                duration = f"{hours}时{minutes}分{seconds}秒"
            except ValueError:
                pass

        logger.info("=" * 60)
        logger.info("📊 批处理完成报告")
        logger.info("=" * 60)
        logger.info(f"  范围:     第 {self.start} ~ {self.end - 1} 条")
        logger.info(f"  总处理:   {self.total} 条")
        logger.info(f"  ✅ 成功:   {self.success} 条")
        logger.info(f"  ⏭️  跳过:   {self.skipped} 条")
        logger.info(f"  ❌ 失败:   {self.failed} 条")
        logger.info(f"  耗时:     {duration}")

        if self.failures:
            logger.info("")
            logger.info("─" * 60)
            logger.info("失败明细（按类型汇总）:")
            logger.info("─" * 60)

            # 按类型分组
            by_category: dict[str, list[FailureRecord]] = {}
            for f in self.failures:
                by_category.setdefault(f.error_category, []).append(f)

            for cat, items in sorted(by_category.items()):
                logger.info(f"  [{cat}] {len(items)} 条:")
                for f in items:
                    logger.info(f"    · 第{f.seq}条 (全局{f.global_idx}) "
                                f"「{f.title[:30]}」- {f.error_message[:80]}")
                logger.info("")

            # 给出建议
            api_fails = [f for f in self.failures if f.error_category == ErrorCategory.API_ERROR]
            if api_fails:
                logger.info("💡 建议: API错误较多，可能原因：")
                logger.info("   1) API Key 额度不足或已达速率限制")
                logger.info("   2) 网络不稳定")
                logger.info("   3) 尝试增加 --retry 参数值或降低 --count 分批运行")
            parse_fails = [f for f in self.failures if f.error_category == ErrorCategory.PARSE_ERROR]
            if parse_fails:
                logger.info("💡 建议: 解析错误较多，可能需要调整提示词或检查文本质量")

        # 保存报告到文件
        report_path = Path("data/batch_report.txt")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"批处理报告 {self.start_time}\n")
            f.write(f"{'='*60}\n")
            f.write(f"范围: 第{self.start}~{self.end-1}条 | "
                    f"成功:{self.success} 跳过:{self.skipped} 失败:{self.failed}\n")
            for fr in self.failures:
                f.write(f"  [{fr.error_category}] 第{fr.seq}条: {fr.title[:50]} | {fr.error_message[:80]}\n")
            f.write("\n")
        logger.info(f"报告已追加保存到: {report_path}")


# ── Checkpoint 管理 ──

CHECKPOINT_FILE = Path("data/.batch_checkpoint.json")


def save_checkpoint(global_idx: int, seq: str, title: str):
    """保存处理进度 checkpoint。"""
    data = {
        "last_global_idx": global_idx,
        "last_seq": seq,
        "last_title": title[:50],
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_checkpoint() -> dict | None:
    """加载上次 checkpoint。"""
    if CHECKPOINT_FILE.exists():
        try:
            with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, Exception):
            return None
    return None


# ── 带重试的提取 ──

def extract_with_retry(
    extractor: ClaudeExtractor,
    text: str,
    max_retries: int = 3,
) -> tuple:
    """带指数退避重试的提取。

    Returns:
        (result_or_None, error_category, error_message, attempts)
    """
    last_exception = None

    for attempt in range(1, max_retries + 1):
        try:
            result = extractor.extract(text)

            if result is not None:
                return result, None, None, attempt

            # extract 返回 None 但无异常 -> 可能是 API 返回了空
            if attempt < max_retries:
                delay = 3 ** (attempt - 1)  # 1, 3, 9 秒
                logger.warning(f"  第{attempt}次返回空，{delay}s后重试...")
                time.sleep(delay)
            else:
                return None, ErrorCategory.EMPTY_RESULT, "提取返回None", attempt

        except Exception as e:
            last_exception = e
            err_str = f"{type(e).__name__}: {e}"

            # 判断是否是 API 类错误
            err_lower = err_str.lower()
            if any(kw in err_lower for kw in [
                "timeout", "rate_limit", "429", "503", "502", "500",
                "connection", "api_error", "internal_server",
            ]):
                category = ErrorCategory.API_ERROR
            else:
                category = ErrorCategory.OTHER

            if attempt < max_retries:
                delay = 3 ** (attempt - 1)
                logger.warning(f"  ⚠ 第{attempt}次失败 [{category}]: {err_str[:60]}, {delay}s后重试...")
                time.sleep(delay)
            else:
                return None, category, err_str, attempt

    return None, ErrorCategory.OTHER, str(last_exception or "未知错误"), max_retries


def classify_parse_error(e: Exception) -> str:
    """分类解析错误。"""
    name = type(e).__name__
    if any(kw in name.lower() or kw in str(e).lower() for kw in
           ["validation", "pydantic"]):
        return ErrorCategory.VALIDATION_ERROR
    return ErrorCategory.PARSE_ERROR


# ── 主流程 ──

def main():
    parser = argparse.ArgumentParser(description="批量处理事故数据（增强版）")
    parser.add_argument("--start", type=int, default=0, help="起始行号（从0开始）")
    parser.add_argument("--count", type=int, default=None, help="处理条数")
    parser.add_argument("--end", type=int, default=None, help="结束行号（不含），与--count二选一")
    parser.add_argument("--resume", action="store_true", help="续传模式：跳过数据集中已存在的标题")
    parser.add_argument("--retry", type=int, default=3, help="失败重试次数 (默认3)")
    parser.add_argument("--checkpoint", action="store_true", help="启用 checkpoint 断点续传")
    parser.add_argument("--csv", default="原始数据/data_STH.csv", help="输入CSV路径")
    parser.add_argument("--no-search", action="store_true", help="跳过联网文本搜索")
    parser.add_argument("--no-images", action="store_true", help="跳过图片搜索与下载")
    parser.add_argument("--no-search", action="store_true", help="跳过联网文本搜索")
    parser.add_argument("--no-images", action="store_true", help="跳过图片搜索与下载")
    args = parser.parse_args()

    # 兼容 --end 参数
    if args.end is not None and args.count is not None:
        logger.error("--end 和 --count 不能同时使用")
        sys.exit(1)

    csv_path = Path(args.csv)
    if not csv_path.exists():
        logger.error(f"CSV 文件不存在: {csv_path}")
        sys.exit(1)

    # 读取 CSV
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        all_rows = list(reader)

    total = len(all_rows)

    # 确定结束行
    start = args.start
    if args.end is not None:
        end = min(args.end, total)
    elif args.count is not None:
        end = min(start + args.count, total)
    else:
        end = total

    if start >= total:
        logger.error(f"起始行 {start} 超出文件总行数 {total}")
        sys.exit(1)

    batch = all_rows[start:end]
    logger.info(f"CSV 共 {total} 条，处理第 {start}~{end-1} 条（共 {len(batch)} 条）")

    # ── Checkpoint 恢复 ──
    if args.checkpoint:
        ckpt = load_checkpoint()
        if ckpt:
            last_global = ckpt.get("last_global_idx", -1)
            if start <= last_global < end:
                # 找到 checkpoint 位置，跳过已处理部分
                skip_count = last_global - start + 1
                logger.info(f"🔁 检测到上次 checkpoint: 已处理到全局第 {last_global} 条 "
                            f"(「{ckpt.get('last_title', '')}」)")
                logger.info(f"   跳过 {skip_count} 条，从全局第 {last_global + 1} 条继续")
                start = last_global + 1
                batch = all_rows[start:end]
            elif last_global >= end:
                logger.info(f"✅ Checkpoint 显示已全部处理完毕 (上次到 {last_global}，本次目标到 {end})")
                return

    # ── 加载已有数据集（用于续传去重） ──
    db = DatasetManager()
    existing_titles: set[str] = set()
    if args.resume:
        for r in db.get_all():
            title = (r.get("title") or "").strip()
            if title:
                existing_titles.add(title)
        logger.info(f"数据集中已有 {len(existing_titles)} 条记录，将跳过匹配项")

    extractor = ClaudeExtractor()
    if not extractor.client:
        logger.error("API Key 未配置，无法提取")
        sys.exit(1)

    # ── 初始化统计 ──
    report = BatchReport(
        start=args.start,
        end=end,
        total=len(batch),
        start_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    success = 0
    skipped = 0

    for i, row in enumerate(batch):
        global_idx = start + i
        seq = row.get("序列", "").strip()
        title_csv = (row.get("事故名") or "").strip()
        text = (row.get("爬虫") or "").strip()
        url = (row.get("来源网址") or "").strip()

        if not text:
            logger.warning(f"[{global_idx}] 第{seq}条 爬虫字段为空，跳过")
            report.failures.append(FailureRecord(
                seq=seq, global_idx=global_idx,
                title=title_csv,
                error_category=ErrorCategory.EMPTY_INPUT,
                error_message="爬虫字段为空", attempts=0,
            ))
            skipped += 1
            continue

        # 续传：跳过已存在的标题
        if args.resume and title_csv in existing_titles:
            logger.info(f"[{global_idx}] 第{seq}条 「{title_csv[:20]}」已存在，跳过")
            skipped += 1
            continue

        logger.info(
            f"[{global_idx}/{total}] 第{seq}条 "
            f"「{title_csv[:30]}」(文本{len(text)}字) 开始提取..."
        )

        # ── 搜索集成（文本搜索 + 图片搜索） ──
        search_integrator = None
        search_data = None
        extract_text = text  # 默认用原始文本

        if not args.no_search or not args.no_images:
            search_integrator = SearchIntegrator()

        if not args.no_search:
            logger.info(f"  🔍 联网搜索相关信息...")
            sr, queries, snippets = search_integrator.search_text(title_csv or text)
            if sr:
                extract_text = search_integrator.build_enhanced_text(text, sr)
                logger.info(f"    找到 {len(sr)} 条补充信息")
            search_data = {
                "search_results": sr,
                "search_queries": queries,
                "snippets_meta": snippets,
                "image_results": [],
                "image_local_paths": [],
                "image_sources": [],
            }

        # ── 带重试的提取（使用增强后的文本） ──
        result, err_cat, err_msg, attempts = extract_with_retry(
            extractor, extract_text, max_retries=args.retry,
        )

        if result is None:
            report.failures.append(FailureRecord(
                seq=seq, global_idx=global_idx,
                title=title_csv,
                error_category=err_cat or ErrorCategory.OTHER,
                error_message=err_msg or "提取失败",
                attempts=attempts,
            ))
            logger.error(f"  ❌ 第{attempts}次均失败 [{err_cat}]: {err_msg[:80]}")
            continue

        # ── 注入来源 URL ──
        if url and url not in result.source_urls:
            result.source_urls.append(url)

        # ── 转换并保存 ──
        try:
            accident = result.to_accident(raw_input=text)

            # ── 图片搜索（在知道事故ID后执行，用于文件名） ──
            if not args.no_images and search_integrator:
                img_keyword = accident.title or title_csv or text[:60]
                logger.info(f"  📷 搜索事故现场图片...")
                img_results, local_paths, img_sources = search_integrator.search_images(
                    img_keyword, str(accident.id), max_images=3
                )
                if search_data:
                    search_data["image_results"] = img_results
                    search_data["image_local_paths"] = local_paths
                    search_data["image_sources"] = img_sources
                else:
                    search_data = {
                        "search_results": [],
                        "search_queries": [],
                        "snippets_meta": [],
                        "image_results": img_results,
                        "image_local_paths": local_paths,
                        "image_sources": img_sources,
                    }

            # ── 整合搜索元数据到 Accident ──
            if search_data:
                search_integrator.integrate_into_accident(
                    accident=accident,
                    search_results=search_data["search_results"],
                    search_queries=search_data["search_queries"],
                    snippets_meta=search_data["snippets_meta"],
                    image_sources=search_data["image_sources"],
                    image_local_paths=search_data["image_local_paths"],
                )

            result_info = db.add(accident)
            success += 1

            # Checkpoint：记录当前进度
            if args.checkpoint:
                save_checkpoint(global_idx, seq, title_csv)

            action_icon = {"added": "✅ 新增", "updated": "🔄 更新", "skipped": "⏭️ 跳过"}
            logger.info(f"  {action_icon.get(result_info['action'], '✅')}: {accident.title} "
                        f"(ID:{accident.id})")
            logger.info(f"     (类型:{accident.accident_type}, "
                        f"等级:{accident.severity}, "
                        f"死亡:{accident.deaths}, "
                        f"受伤:{accident.injuries})")

        except Exception as e:
            err_cat2 = classify_parse_error(e)
            tb = traceback.format_exc()
            report.failures.append(FailureRecord(
                seq=seq, global_idx=global_idx,
                title=title_csv,
                error_category=err_cat2,
                error_message=f"{type(e).__name__}: {e}",
                attempts=attempts,
            ))
            logger.error(f"  ❌ 转换/保存异常 [{err_cat2}]: {e}")
            logger.debug(tb)

        # 每条间隔 0.5 秒，避免请求过密
        time.sleep(0.5)

    # ── 最终统计 ──
    report.success = success
    report.skipped = skipped
    report.end_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report.print_report()

    dataset_stats = db.stats()
    logger.info(f"数据集总记录数: {dataset_stats['total']}")


if __name__ == "__main__":
    main()
