"""Construction Agent CLI 入口。"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table
from rich.markdown import Markdown

from construction_agent import __version__
from construction_agent.config import settings

console = Console()

AGENT_LOGO = """
[bold cyan]
  ___ __ _ _ __   ___ _   _ _ __   ___  __ _ _ __   ___ _ __
 / __/ _` | '_ \ / __| | | | '_ \ / _ \/ _` | '_ \ / _ \ '__|
| (_| (_| | | | | (__| |_| | | | |  __/ (_| | | | |  __/ |
 \___\__,_|_| |_|\___|\__,_|_| |_|\___|\__,_|_| |_|\___|_|
[/bold cyan]
[bold yellow]   建筑施工事故信息自动化处理系统 v{}[/bold yellow]""".format(__version__)


def setup_logging(level: str = None):
    """配置日志。"""
    level = level or settings.LOG_LEVEL
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )


def cmd_process(args: argparse.Namespace):
    """处理单条事故信息。"""
    from construction_agent.extraction import ExtractionPipeline
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.output.csv_formatter import CSVFormatter
    from construction_agent.database import DatasetManager

    text = args.text
    console.print(Panel(f"[bold]输入事故信息:[/]\n{text}", title="📥 输入"))

    pipeline = ExtractionPipeline()
    accident = pipeline.process(text)

    if not accident:
        console.print("[bold red]❌ 提取失败，请检查 API Key 配置和网络连接[/]")
        sys.exit(1)

    # 输出 JSON
    console.print("\n[bold green]✅ 提取完成！[/]")
    console.print(Panel(
        accident.to_json(),
        title="📋 结构化数据 (JSON)",
        border_style="green",
    ))

    # 保存到数据集
    db = DatasetManager()
    db.add(accident)
    console.print(f"[dim]已保存到数据集 ({db.stats()['total']} 条)[/dim]")

    # 导出
    if args.json:
        JSONFormatter.save_single(accident, Path(args.json))
        console.print(f"[green]✓ JSON 已导出: {args.json}[/]")
    if args.csv:
        CSVFormatter.save_single(accident, Path(args.csv))
        console.print(f"[green]✓ CSV 已导出: {args.csv}[/]")

    # 显示总览表格
    _show_accident_table(accident)


def cmd_batch(args: argparse.Namespace):
    """批量处理事故信息。"""
    from construction_agent.extraction import ExtractionPipeline
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.output.csv_formatter import CSVFormatter
    from construction_agent.database import DatasetManager

    filepath = Path(args.file)
    if not filepath.exists():
        console.print(f"[bold red]❌ 文件不存在: {filepath}[/]")
        sys.exit(1)

    with open(filepath, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    console.print(f"[bold]📂 从 {filepath} 读取到 {len(lines)} 条事故信息[/]")

    pipeline = ExtractionPipeline()
    accidents = pipeline.process_batch(lines)

    if not accidents:
        console.print("[bold red]❌ 全部处理失败[/]")
        sys.exit(1)

    # 保存到数据集
    db = DatasetManager()
    db.add_batch(accidents)

    console.print(f"\n[bold green]✅ 成功处理 {len(accidents)}/{len(lines)} 条事故[/]")

    # 导出
    if args.json:
        JSONFormatter.save_batch(accidents, Path(args.json))
    if args.csv:
        CSVFormatter.save_batch(accidents, Path(args.csv))

    # 统计
    _show_dataset_stats(db)


def cmd_interactive(args: argparse.Namespace):
    """交互模式。"""
    from construction_agent.extraction import ExtractionPipeline
    from construction_agent.database import DatasetManager
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.output.csv_formatter import CSVFormatter

    pipeline = ExtractionPipeline()
    db = DatasetManager()

    console.print(AGENT_LOGO)
    console.print("\n[bold]🔧 交互模式[/] （输入事故信息进行分析，输入 [red]exit[/] 退出）")
    console.print("[dim]提示：你可以输入简短的事故描述，Agent 会自动联网搜索补充信息[/]\n")

    while True:
        try:
            text = console.input("[bold cyan]➤ [/]").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[bold]再见！[/]")
            break

        if not text:
            continue
        if text.lower() in ("exit", "quit", "q"):
            console.print("[bold]再见！[/]")
            break

        # 特殊命令
        if text.lower().startswith("/"):
            _handle_special_command(text, db)
            continue

        with console.status("[bold cyan]🔄 正在处理..."):
            accident = pipeline.process(text)

        if accident:
            db.add(accident)
            console.print(Panel(
                accident.to_json(),
                title="✅ 提取结果",
                border_style="green",
            ))
            _show_accident_table(accident)
        else:
            console.print("[bold red]❌ 提取失败，请重试[/]")


def cmd_web(args: argparse.Namespace):
    """启动 Web UI。"""
    from construction_agent.web.app import app

    console.print("[bold green]🌐 启动 Web UI...[/]")
    console.print(f"[dim]地址: http://{settings.FLASK_HOST}:{settings.FLASK_PORT}[/]")

    app.run(
        host=settings.FLASK_HOST,
        port=settings.FLASK_PORT,
        debug=settings.FLASK_DEBUG,
    )


def cmd_dataset(args: argparse.Namespace):
    """数据集管理命令。"""
    from construction_agent.database import DatasetManager
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.output.csv_formatter import CSVFormatter

    db = DatasetManager()

    if args.action == "stats":
        _show_dataset_stats(db)

    elif args.action == "list":
        records = db.get_all()
        if not records:
            console.print("[yellow]数据集为空[/]")
            return
        table = Table(title=f"事故数据集 ({len(records)} 条)")
        table.add_column("#", style="dim")
        table.add_column("日期")
        table.add_column("标题")
        table.add_column("类型")
        table.add_column("等级")
        table.add_column("死亡")
        table.add_column("受伤")
        for i, r in enumerate(records, 1):
            table.add_row(
                str(i),
                r.get("date", "") or "-",
                r.get("title", "")[:30],
                r.get("accident_type", ""),
                r.get("severity", ""),
                str(r.get("deaths", 0)),
                str(r.get("injuries", 0)),
            )
        console.print(table)

    elif args.action == "search":
        keyword = args.keyword
        results = db.search(keyword)
        console.print(f"搜索「{keyword}」找到 {len(results)} 条结果")
        for r in results:
            console.print(f"- {r.get('date', '?')} | {r.get('title', '?')}")

    elif args.action == "export":
        fmt = args.format
        output = Path(args.output)
        if db.export(output, fmt):
            console.print(f"[green]✓ 已导出 {fmt.upper()} 到 {output}[/]")
        else:
            console.print("[red]❌ 导出失败[/]")

    elif args.action == "clear":
        if args.force or console.input("[red]确定清空所有数据？(yes/no): [/]") == "yes":
            db.clear()
            console.print("数据集已清空")
        else:
            console.print("已取消")

def cmd_hfacs_encode(args: argparse.Namespace):
    """对数据集进行 HFACS-CS 编码。"""
    from construction_agent.versions.v2_5.encoder import HfacsEncoder
    from construction_agent.database import DatasetManager

    console.print("[bold cyan]🔬 V2.5 HFACS-CS 编码[/]")
    console.print(f"  方法: {args.method}")
    console.print(f"  输入: {args.input}")

    encoder = HfacsEncoder()
    db = DatasetManager(filepath=args.input)
    records = db.get_all()

    total = len(records)
    start = args.start
    end = min(args.start + args.count, total) if args.count else total
    batch = records[start:end]

    with console.status(f"[cyan]正在编码 {len(batch)} 条记录..."):
        encoded = 0
        for i, record in enumerate(batch):
            global_idx = start + i
            cause_dims = {}
            for dim in ["human_factors","machine_factors","management_factors","environmental_factors"]:
                factors = record.get(dim) or []
                cause_dims[dim] = [f for f in factors if isinstance(f, dict) and f.get("category","").strip()]

            total_causes = sum(len(v) for v in cause_dims.values())
            if total_causes == 0:
                record["hfacs_codes"] = []
                record["hfacs_details"] = []
                record["hfacs_encoding_version"] = f"v2.5_{args.method}_v1"
                continue

            codes, details = encoder.encode(
                title=record.get("title",""),
                summary=record.get("summary",""),
                **{dim: cause_dims[dim] for dim in cause_dims},
                method=args.method,
            )
            record["hfacs_codes"] = codes
            record["hfacs_details"] = [d.model_dump() for d in details]
            record["hfacs_encoding_version"] = f"v2.5_{args.method}_v1"
            encoded += 1

        # 保存
        output = args.output or args.input
        import json
        from pathlib import Path
        with open(output, "w", encoding="utf-8") as f:
            json.dump(records, f, indent=2, ensure_ascii=False)

    console.print(f"[green]✅ 编码完成: {encoded}/{len(batch)} 条")


def cmd_pdf(args: argparse.Namespace):
    """处理单个 PDF 事故文档。"""
    from pathlib import Path
    from construction_agent.input.pdf_processor import PDFProcessor
    from construction_agent.extraction import ExtractionPipeline
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.output.csv_formatter import CSVFormatter
    from construction_agent.database import DatasetManager

    pdf_path = Path(args.file)
    if not pdf_path.exists():
        console.print(f"[bold red]❌ PDF 文件不存在: {pdf_path}[/]")
        sys.exit(1)

    token = settings.MINERU_API_TOKEN or None
    processor = PDFProcessor(token=token)

    with console.status(f"[bold cyan]🔍 正在解析 PDF（{args.mode} 模式）...[/]"):
        try:
            segments = processor.process(pdf_path, mode=args.mode, language=args.language)
        except Exception as e:
            console.print(f"[bold red]❌ PDF 解析失败: {e}[/]")
            sys.exit(1)

    console.print(f"[green]✅ 解析完成，提取到 {len(segments)} 个事故文本片段[/]\n")

    # 预览片段
    for i, seg in enumerate(segments):
        preview = seg.text[:200] + ("..." if len(seg.text) > 200 else "")
        console.print(f"[bold cyan]── 片段 {i+1}/{len(segments)} (源: {seg.source})[/]")
        console.print(preview)
        console.print()

    # 可选：执行提取
    if args.extract:
        console.print("[bold]🔬 开始信息提取...[/]")
        pipeline = ExtractionPipeline()
        db = DatasetManager()
        accidents = []
        for i, seg in enumerate(segments):
            with console.status(f"[cyan]提取中 {i+1}/{len(segments)}...[/]"):
                try:
                    acc = pipeline.process(seg.text)
                    if acc:
                        accidents.append(acc)
                        console.print(f"  [green]✓[/] 片段 {i+1}: {acc.title[:40]}")
                    else:
                        console.print(f"  [yellow]⚠[/] 片段 {i+1}: 提取失败")
                except Exception as e:
                    console.print(f"  [red]✗[/] 片段 {i+1}: {e}")

        console.print(f"\n[green]✅ 成功提取 {len(accidents)}/{len(segments)} 条事故[/]")

        if accidents:
            db.add_batch(accidents)
            console.print(f"[dim]已保存到数据集 ({db.stats()['total']} 条)[/dim]")

        # 导出
        if args.json:
            JSONFormatter.save_batch(accidents, Path(args.json))
            console.print(f"[green]✓ JSON 已导出: {args.json}[/]")
        if args.csv:
            CSVFormatter.save_batch(accidents, Path(args.csv))
            console.print(f"[green]✓ CSV 已导出: {args.csv}[/]")


def cmd_pdf_batch(args: argparse.Namespace):
    """批量处理 PDF 事故文档。"""
    from pathlib import Path
    from construction_agent.input.pdf_processor import PDFProcessor
    from construction_agent.extraction import ExtractionPipeline
    from construction_agent.output.json_formatter import JSONFormatter
    from construction_agent.database import DatasetManager

    # 收集 PDF 文件
    pdf_files = []
    for f in args.files:
        p = Path(f)
        if p.is_dir():
            pdf_files.extend(sorted(p.glob("*.pdf")))
            pdf_files.extend(sorted(p.glob("*.PDF")))
        elif p.is_file():
            pdf_files.append(p)
        else:
            console.print(f"[yellow]⚠ 跳过不存在的路径: {f}[/]")

    if not pdf_files:
        console.print("[bold red]❌ 未找到任何 PDF 文件[/]")
        sys.exit(1)

    console.print(f"[bold]📂 找到 {len(pdf_files)} 个 PDF 文件[/]")

    token = settings.MINERU_API_TOKEN or None
    processor = PDFProcessor(token=token)

    all_segments = []
    for i, pdf_path in enumerate(pdf_files):
        console.print(f"\n[bold cyan][{i+1}/{len(pdf_files)}] 解析: {pdf_path.name}[/]")
        try:
            segments = processor.process(pdf_path, mode=args.mode, language=args.language)
            console.print(f"  [green]✓[/] 提取 {len(segments)} 个片段")
            all_segments.extend(segments)
        except Exception as e:
            console.print(f"  [red]✗[/] 失败: {e}")

    console.print(f"\n[green]✅ 批量解析完成: {len(all_segments)} 个文本片段[/]")

    # 可选：执行提取
    if args.extract:
        console.print(f"\n[bold]🔬 开始批量信息提取（{len(all_segments)} 个片段）...[/]")
        pipeline = ExtractionPipeline()
        db = DatasetManager()
        accidents = []
        for i, seg in enumerate(all_segments):
            with console.status(f"[cyan]提取中 {i+1}/{len(all_segments)}...[/]"):
                try:
                    acc = pipeline.process(seg.text)
                    if acc:
                        accidents.append(acc)
                except Exception as e:
                    console.print(f"  [red]✗[/] 片段 {i+1}: {e}")

        console.print(f"[green]✅ 成功提取 {len(accidents)}/{len(all_segments)} 条事故[/]")

        if accidents:
            db.add_batch(accidents)
            console.print(f"[dim]已保存到数据集 ({db.stats()['total']} 条)[/dim]")

        # 导出
        output_dir = Path(args.output_dir) if args.output_dir else Path("data")
        output_dir.mkdir(parents=True, exist_ok=True)
        json_path = output_dir / "pdf_extracted.json"
        JSONFormatter.save_batch(accidents, json_path)
        console.print(f"[green]✓ 已导出到 {json_path}[/]")


def _show_accident_table(accident):
    """显示事故信息的表格。"""
    table = Table(title="事故概要")

    table.add_column("项目", style="cyan")
    table.add_column("内容")

    table.add_row("标题", accident.title)
    table.add_row("日期", str(accident.date) if accident.date else "-")
    table.add_row("具体时刻", accident.accident_time or "-")
    if accident.day_of_week or accident.season:
        meta = []
        if accident.day_of_week: meta.append(accident.day_of_week)
        if accident.season: meta.append(accident.season)
        if accident.time_period: meta.append(accident.time_period)
        if accident.lighting: meta.append(accident.lighting)
        table.add_row("时间属性", " | ".join(meta))
    table.add_row("地点", accident.location or "-")
    if accident.city:
        table.add_row("城市", accident.city)
    if accident.accident_location_detail:
        table.add_row("具体部位", accident.accident_location_detail)
    table.add_row("项目", accident.project_name or "-")
    if accident.project_type:
        table.add_row("工程类型", accident.project_type)
    if accident.building_structure:
        table.add_row("建筑结构", accident.building_structure)
    if accident.building_completion_year:
        table.add_row("建成年份", accident.building_completion_year)
    if accident.weather:
        table.add_row("气象情况", accident.weather)
    if accident.direct_cause:
        table.add_row("直接原因", accident.direct_cause[:60])
    if accident.contractor:
        table.add_row("施工单位", accident.contractor)
    if accident.trapped_count:
        table.add_row("被困人数", f"{accident.trapped_count} 人")
    table.add_row("类型", accident.accident_type)
    table.add_row("等级", accident.severity)
    table.add_row("死亡/受伤", f"{accident.deaths} 人 / {accident.injuries} 人")
    table.add_row("经济损失", f"{accident.direct_economic_loss} 万元" if accident.direct_economic_loss else "-")

    # 图片信息
    if accident.images:
        table.add_row("现场图片", f"📷 {len(accident.images)} 张 (data/images/)")

    # CAICS 致因链
    if accident.causes:
        chain = " → ".join(f"[{c['code']}]{c['name']}" for c in accident.causes[:8])
        table.add_row("CAICS致因链", chain)
        if len(accident.causes) > 8:
            table.add_row("", f"...等{len(accident.causes)}项")
    else:
        table.add_row("CAICS致因链", "[dim]无[/dim]")

    if accident.summary:
        table.add_row("事故摘要", accident.summary[:100] + ("..." if len(accident.summary) > 100 else ""))

    console.print(table)


def _show_dataset_stats(db):
    """显示数据集统计。"""
    stats = db.stats()
    if stats["total"] == 0:
        console.print("[yellow]数据集为空[/]")
        return

    console.print(Panel(
        f"[bold]总记录: {stats['total']} 条[/]\n"
        f"总死亡: {stats['total_deaths']} 人 | 总受伤: {stats['total_injuries']} 人\n\n"
        f"[bold]按事故类型:[/]\n" + "\n".join(
            f"  {t}: {c} 起" for t, c in stats["by_type"].items()
        ) + "\n\n"
        f"[bold]按事故等级:[/]\n" + "\n".join(
            f"  {s}: {c} 起" for s, c in stats["by_severity"].items()
        ),
        title="📊 数据集统计",
        border_style="blue",
    ))


def _handle_special_command(cmd: str, db):
    """处理交互模式中的特殊命令。"""
    from construction_agent.database import DatasetManager

    parts = cmd.split(maxsplit=1)
    command = parts[0].lower()

    if command == "/stats":
        _show_dataset_stats(db)
    elif command == "/list":
        records = db.get_all()
        for i, r in enumerate(records, 1):
            console.print(f"[dim]{i}.[/] {r.get('date', '?')} | {r.get('title', '?')}")
    elif command == "/help":
        console.print(Panel(
            "[bold]可用命令:[/]\n"
            "  /stats   - 数据集统计\n"
            "  /list    - 列出所有记录\n"
            "  /export  json|csv <文件路径>  - 导出数据\n"
            "  exit     - 退出\n"
            "  直接输入事故信息即可分析",
            title="📖 帮助",
        ))
    elif command == "/export":
        export_format = "json"
        export_path = "data/export.json"
        if len(parts) > 1:
            args = parts[1].split()
            if args[0] in ("json", "csv"):
                export_format = args[0]
            if len(args) > 1:
                export_path = args[1]

        from construction_agent.output.json_formatter import JSONFormatter
        from construction_agent.output.csv_formatter import CSVFormatter
        records = db.get_all()
        from construction_agent.models.accident import Accident
        accidents = [Accident(**r) for r in records]
        path = Path(export_path)
        if export_format == "csv":
            CSVFormatter.save_batch(accidents, path)
        else:
            JSONFormatter.save_batch(accidents, path)
        console.print(f"[green]✓ 已导出 {len(accidents)} 条到 {path}[/]")
    else:
        console.print(f"[red]未知命令: {command} (输入 /help 查看可用命令)[/]")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(
        description="Construction Agent - 建筑施工事故信息自动化处理系统",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python -m construction_agent.main process "某工地发生坍塌事故，造成3人死亡"
  python -m construction_agent.main batch accidents.txt --json output.json --csv output.csv
  python -m construction_agent.main interactive
  python -m construction_agent.main web
  python -m construction_agent.main collect                        # 从所有数据源采集
  python -m construction_agent.main collect --source safehoo      # 指定采集器
  python -m construction_agent.main collect --list                # 列出可用采集器
  python -m construction_agent.main dataset stats
  python -m construction_agent.main dataset search "坍塌"
  python -m construction_agent.main dataset export --format csv data.csv
        """,
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("--log-level", default=None, help="日志级别 (DEBUG/INFO/WARNING/ERROR)")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # process: 处理单条
    p_process = subparsers.add_parser("process", help="处理单条事故信息")
    p_process.add_argument("text", help="事故描述文本")
    p_process.add_argument("--json", help="输出 JSON 文件路径")
    p_process.add_argument("--csv", help="输出 CSV 文件路径")

    # batch: 批量处理
    p_batch = subparsers.add_parser("batch", help="批量处理事故信息（文件逐行读取）")
    p_batch.add_argument("file", help="事故信息文件路径（每行一条）")
    p_batch.add_argument("--json", help="输出 JSON 文件路径")
    p_batch.add_argument("--csv", help="输出 CSV 文件路径")

    # interactive: 交互模式
    subparsers.add_parser("interactive", help="交互式命令行模式")

    # web: Web UI
    subparsers.add_parser("web", help="启动 Web UI")

    # dataset: 数据集管理
    p_dataset = subparsers.add_parser("dataset", help="数据集管理")
    dataset_sub = p_dataset.add_subparsers(dest="action", help="数据集操作")

    p_stats = dataset_sub.add_parser("stats", help="数据集统计")
    p_list = dataset_sub.add_parser("list", help="列出所有记录")
    p_search = dataset_sub.add_parser("search", help="搜索记录")
    p_search.add_argument("keyword", help="搜索关键词")
    p_export = dataset_sub.add_parser("export", help="导出数据集")
    p_export.add_argument("--format", "-f", choices=["json", "csv"], default="json", help="导出格式")
    p_export.add_argument("output", nargs="?", default=None, help="输出文件路径")
    p_clear = dataset_sub.add_parser("clear", help="清空数据集")
    p_clear.add_argument("--force", "-f", action="store_true", help="强制清空，不确认")

    # hfacs-encode: V2.5 HFACS 编码
    p_hfacs = subparsers.add_parser("hfacs-encode", help="对数据集进行 HFACS-CS 编码")
    p_hfacs.add_argument("--method", choices=["llm", "keyword"], default="llm",
                         help="编码方法：llm（API）或 keyword（零成本）")
    p_hfacs.add_argument("--input", default="data/datasets/accidents_merged.json", help="输入数据集")
    p_hfacs.add_argument("--output", default=None, help="输出文件（默认覆盖输入）")
    p_hfacs.add_argument("--start", type=int, default=0, help="起始索引")
    p_hfacs.add_argument("--count", type=int, default=None, help="处理条数")
    p_hfacs.add_argument("--resume", action="store_true", help="断点续传")

    # pdf: 处理单个 PDF
    p_pdf = subparsers.add_parser("pdf", help="处理单个 PDF 事故文档（MinerU 解析）")
    p_pdf.add_argument("file", help="PDF 文件路径")
    p_pdf.add_argument("--mode", choices=["auto", "flash", "precision"], default="auto",
                       help="解析模式: auto=自动选择 / flash=免认证轻量 / precision=高精度（需Token）")
    p_pdf.add_argument("--language", default="ch", help="文档语言，默认 ch（中文）")
    p_pdf.add_argument("--extract", action="store_true", help="解析后自动执行信息提取")
    p_pdf.add_argument("--json", help="提取后导出 JSON 文件路径")
    p_pdf.add_argument("--csv", help="提取后导出 CSV 文件路径")

    # pdf-batch: 批量处理 PDF
    p_pdf_batch = subparsers.add_parser("pdf-batch", help="批量处理 PDF 事故文档")
    p_pdf_batch.add_argument("files", nargs="+", help="PDF 文件路径或目录（支持通配符）")
    p_pdf_batch.add_argument("--mode", choices=["auto", "flash", "precision"], default="auto",
                             help="解析模式: auto=自动选择 / flash=免认证轻量 / precision=高精度（需Token）")
    p_pdf_batch.add_argument("--language", default="ch", help="文档语言，默认 ch（中文）")
    p_pdf_batch.add_argument("--extract", action="store_true", help="解析后自动执行信息提取")
    p_pdf_batch.add_argument("--output-dir", default=None, help="提取结果输出目录")

    # collect: 数据采集
    p_collect = subparsers.add_parser("collect", help="从多个数据源主动采集建筑施工事故数据")
    p_collect.add_argument("--source", "-s", default=None,
                          help="指定采集器（逗号分隔多个），默认全部")
    p_collect.add_argument("--llm", action="store_true", default=False,
                          help="启用 LLM 增强提取（默认禁用，节省 API 成本）")
    p_collect.add_argument("--list", action="store_true",
                          help="列出所有可用采集器")
    p_collect.add_argument("--output", "-o", default=None,
                          help="统计报告输出路径")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    return args


def cmd_collect(args: argparse.Namespace):
    """数据采集命令：从多个数据源主动采集事故数据。"""
    from construction_agent.collector.scripts.run_collect import _register, COLLECTOR_REGISTRY

    _register()

    # 列出可用采集器
    if args.list:
        table = Table(title="可用数据源采集器")
        table.add_column("名称", style="cyan")
        table.add_column("描述")
        for name, (desc, _) in COLLECTOR_REGISTRY.items():
            table.add_row(name, desc)
        console.print(table)
        console.print("\n[dim]用法: python -m construction_agent.main collect --source <名称>[/]")
        return

    # 构建采集器列表
    if args.source:
        source_names = [s.strip() for s in args.source.split(",")]
    else:
        source_names = list(COLLECTOR_REGISTRY.keys())

    # 验证
    invalid = [n for n in source_names if n not in COLLECTOR_REGISTRY]
    if invalid:
        console.print(f"[red]未知采集器: {invalid}[/]")
        console.print(f"可用: {list(COLLECTOR_REGISTRY.keys())}")
        sys.exit(1)

    # 实例化
    collectors = []
    for name in source_names:
        _, factory = COLLECTOR_REGISTRY[name]
        instance = factory()
        if instance:
            collectors.append(instance)
        else:
            console.print(f"[red]✗ {name} 初始化失败，跳过[/]")

    if not collectors:
        console.print("[red]没有可用的采集器[/]")
        sys.exit(1)

    # 创建流水线并注册采集器
    from construction_agent.collector.pipeline import CollectorPipeline
    pipeline = CollectorPipeline(enable_llm=args.llm)
    for c in collectors:
        pipeline.register_collector(c)

    console.print(Panel(
        f"[bold]启动 {len(collectors)} 个采集器[/]\n"
        f"LLM 增强: {'[green]启用[/]' if args.llm else '[yellow]禁用（省钱模式）[/]'}\n"
        f"数据存储: {settings.DATASET_FILE}",
        title="🚀 数据采集",
        border_style="blue",
    ))

    stats = pipeline.run()
    pipeline.save_stats()

    # 显示汇总
    table = Table(title="📊 采集汇总")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")
    table.add_row("总采集数", str(stats.get("total_collected", 0)))
    table.add_row("清洗后有效", str(stats.get("total_after_clean", 0)))
    table.add_row("去重过滤", str(stats.get("duplicates_filtered", 0)))
    table.add_row("新增入库", str(stats.get("added", 0)))
    table.add_row("合并已有", str(stats.get("merged", 0)))
    table.add_row("LLM增强", str(stats.get("llm_enhanced", 0)))
    table.add_row("耗时", f"{stats.get('duration_seconds', 0)}s")
    console.print(table)


def main(argv: list[str] | None = None):
    """入口函数。"""
    args = parse_args(argv)
    setup_logging(args.log_level)

    commands = {
        "process": cmd_process,
        "batch": cmd_batch,
        "interactive": cmd_interactive,
        "web": cmd_web,
        "dataset": cmd_dataset,
        "collect": cmd_collect,
        "pdf": cmd_pdf,
        "pdf-batch": cmd_pdf_batch,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(args)


if __name__ == "__main__":
    main()
