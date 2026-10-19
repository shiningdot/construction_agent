"""Construction Agent CLI 入口。"""

from __future__ import annotations

import argparse
import logging
import os
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

AGENT_LOGO = r"""
[bold cyan]
  ___ __ _ _ __   ___ _   _ _ __   ___  __ _ _ __   ___ _ __
 / __/ _` | '_ \ / __| | | | '_ \ / _ \/ _` | '_ \ / _ \ '__|
| (_| (_| | | | | (__| |_| | | | |  __/ (_| | | | |  __/ |
 \___\__,_|_| |_|\___|\__,_|_| |_|\___|\__,_|_| |_|\___|_|
[/bold cyan]
[bold yellow]   建筑施工事故信息自动化处理系统 v{}[/bold yellow]""".format(__version__)


# 旧理论链路的命令。主库 2,623 条数据**全部**由 S1–S4 CAICS 链路产出
# （实测 `pipeline_version` 恒为 `caics_v1.0`，编码前缀只有 C/F/H/M/T），
# 下面这几条走的却是 V1 四维度 + HFACS-CS 6 层：字段对不上、编码体系换了一套，
# 产出**无法并入主库**。留着出口只会让人以为它们等价——查 help 看到两个
# "处理单条"的命令，没有理由知道该选哪个。
#
# 所以默认直接拦下，要跑得显式开 `CAIEMS_ALLOW_LEGACY=1`。不删除是沿用项目
# 「先归档再移出生产路径、不做不可逆销毁」的约定（旧资产封存见任务 #34）。
_LEGACY_COMMANDS: dict[str, str] = {
    "process": "ExtractionPipeline（V1 四维度 + HFACS-CS 6 层编码）",
    "batch": "ExtractionPipeline（V1 四维度 + HFACS-CS 6 层编码）",
    "interactive": "ExtractionPipeline（V1 四维度 + HFACS-CS 6 层编码）",
    "hfacs-encode": "HFACS-CS v2.5 编码器",
    "dataset": "DatasetManager（JSON 快照 data/accidents.json，非主库 caiems_v2.db）",
}


def block_legacy(command: str) -> None:
    """拦下走旧理论链路的命令，除非显式放行。

    放行看环境变量而不是 `--legacy` 开关：这是个"确认知道自己要什么"的信号，
    一次性配在环境里比每次敲一串参数更不容易顺手带上。
    """
    what = _LEGACY_COMMANDS.get(command)
    if what is None or os.environ.get("CAIEMS_ALLOW_LEGACY") == "1":
        return

    console.print(Panel(
        f"`{command}` 走的是[bold]旧理论链路[/]：[yellow]{what}[/]\n\n"
        f"而主库里的 2,623 条数据全部由 [green]S1–S4 CAICS 链路[/]产出，"
        f"两条链路的字段与编码体系都不同构，旧链路的产出[bold]无法并入主库[/]。\n\n"
        f"[bold]生产链路请用：[/]\n"
        f"  [green]python -m construction_agent.main extract \"<事故文本>\"[/]\n"
        f"    → S1 三分类闸门 → S2 施工相关性 → S3 基础字段 → S4 CAICS 编码\n\n"
        f"确实需要跑旧链路（如复核封存前的历史结果），加环境变量放行：\n"
        f"  [dim]CAIEMS_ALLOW_LEGACY=1 python -m construction_agent.main {command} …[/]",
        title="[bold red]⛔ 该命令已废弃", border_style="red"))
    sys.exit(2)


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


def cmd_extract(args: argparse.Namespace):
    """用 S1–S4 主管道处理单条事故信息（CAICS 链路）。

    与 `process` 的分工：
      `process`  走旧链路 `ExtractionPipeline`（V1 四维度 + HFACS 编码），
                 服务于已封存的旧理论
      `extract`  走 S1–S4 `CaicsPipeline`（CAICS 5 层 19 类），**与主库
                 2,623 条数据同构**，是生产链路

    默认不落库——单条文本没有对应的 `raw_accidents` 记录，而工作库对
    raw_id 有外键约束。落库属于在线采集管道的场景（蓝图 02 阶段②）。
    """
    from construction_agent.extraction.caics_pipeline import CaicsPipeline

    title = args.title or args.text[:50]
    console.print(Panel(f"[bold]{title}[/]\n[dim]{args.text}[/]", title="📥 输入"))

    try:
        pipe = CaicsPipeline(enable_search_enhance=not args.no_search)
    except Exception as exc:
        console.print(f"[bold red]❌ 主管道初始化失败: {exc}[/]")
        sys.exit(1)

    # 提前把配置缺失摆到台面上。不检查的话，缺 key 时 S1 会把文本判为
    # other、整条被"正常地"过滤，看不出是环境问题。
    problems = pipe.preflight()
    if problems:
        console.print("[bold red]❌ 主管道无法执行：[/]")
        for p in problems:
            console.print(f"  [red]·[/] {p}")
        console.print("\n[dim]请在 construction_agent/.env 配置 ANTHROPIC_API_KEY 后重试[/]")
        sys.exit(1)

    with console.status("[bold cyan]🔄 执行 S1→S2→S3→S4..."):
        outcome = pipe.run_once(
            title=title, text=args.text,
            publish_date=args.publish_date or "",
            source_name=args.source or "",
        )

    _show_stage_table(outcome)

    # 终态分派
    if outcome.status == "purged":
        reason = {
            "text_type": f"文本类型为 {outcome.text_type_value}，非事故报道",
            "non_construction": "判定为非建筑施工事故",
        }.get(outcome.purge_reason, outcome.purge_reason)
        console.print(f"\n[yellow]⛔ 被闸门拦下: {reason}[/]")
        console.print(f"[dim]S1 理由: {outcome.text_type.reason if outcome.text_type else '-'}[/]")
        if outcome.classification:
            console.print(f"[dim]S2 理由: {outcome.classification.reason}[/]")
        return

    if outcome.status != "completed":
        console.print(f"\n[bold red]❌ 失败: {outcome.purge_reason}[/]")
        sys.exit(1)

    # 成功：展示提取结果与编码
    console.print("\n[bold green]✅ 提取与编码完成[/]")
    console.print(Panel(_json_dumps(outcome.basic.to_dict()),
                        title="📋 基础字段 (S3)", border_style="green"))

    if outcome.causes:
        _show_causes_table(outcome.causes)
    else:
        console.print("[yellow]⚠ S4 未产出致因编码（该条仍视为完成，编码为空）[/]")

    console.print(
        f"[dim]文本类型 {outcome.text_type_value} | "
        f"判定 {outcome.classification.verdict.value if outcome.classification else '-'} | "
        f"编码 {len(outcome.caics_codes)} 条 | 总耗时 {outcome.total_ms} ms[/]"
    )

    if args.json:
        Path(args.json).write_text(
            _json_dumps(outcome.to_dict()), encoding="utf-8")
        console.print(f"[green]✓ JSON 已导出: {args.json}[/]")


def _json_dumps(data) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _show_stage_table(outcome):
    """显示 S1–S4 的分段轨迹。"""
    table = Table(title="主管道轨迹")
    table.add_column("阶段", style="cyan", no_wrap=True)
    table.add_column("结果", no_wrap=True)
    table.add_column("耗时", justify="right", no_wrap=True)
    table.add_column("说明")

    labels = {
        "S1": "S1 类型闸门", "S2": "S2 相关性闸门", "S3": "S3 字段提取",
        "SEARCH": "搜索增强", "S4": "S4 致因编码",
    }
    for r in outcome.records:
        mark = "[green]✓[/]" if r.ok else "[red]✗[/]"
        note = r.error if r.error else r.detail
        table.add_row(labels.get(r.stage, r.stage), mark, f"{r.ms} ms", note or "-")

    console.print(table)


def _show_causes_table(causes):
    """显示 CAICS 致因编码。"""
    table = Table(title=f"CAICS 致因编码 ({causes.cause_count} 条)")
    table.add_column("编码", style="cyan", no_wrap=True)
    table.add_column("类名")
    table.add_column("证据")

    for c in causes.causes:
        table.add_row(c.code, c.name, (c.evidence or "-")[:60])

    console.print(table)
    if causes.primary_cause:
        console.print(f"[dim]主因: {causes.primary_cause}[/]")
    if causes.cause_chain:
        console.print(f"[dim]致因链: {causes.cause_chain}[/]")


def cmd_web(args: argparse.Namespace):
    """启动 Web UI。

    入口是 `web.factory.create_app()`（新展示层，读主库 `caiems_v2.db`），
    不是旧 `web.app` 的模块级单例。旧应用读的是 JSON 快照、重算 HFACS-CS，
    实测 23 个端点返回空、2 个 500 崩溃；两张应用的路由没有交集可依赖，
    换的是整个应用。
    """
    from construction_agent.web.factory import create_app

    app = create_app()

    console.print("[bold green]🌐 启动 Web UI...[/]")
    console.print(f"[dim]地址: http://{settings.FLASK_HOST}:{settings.FLASK_PORT}[/]")
    console.print("[dim]数据源: 主库 caiems_v2.db（CAICS 链路）[/]")

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

    # 致因总览
    causes = []
    if accident.human_factors:
        causes.append(f"[red]👤 人: {len(accident.human_factors)}项[/]")
    if accident.machine_factors:
        causes.append(f"[yellow]⚙️ 机: {len(accident.machine_factors)}项[/]")
    if accident.management_factors:
        causes.append(f"[blue]📋 管: {len(accident.management_factors)}项[/]")
    if accident.environmental_factors:
        causes.append(f"[green]🌤️ 环: {len(accident.environmental_factors)}项[/]")

    table.add_row("致因维度", " | ".join(causes) if causes else "[dim]无[dim]")

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
生产链路（与主库 caiems_v2.db 同构）:
  python -m construction_agent.main extract "某工地发生坍塌事故，造成3人死亡"
  python -m construction_agent.main extract "…" --publish-date 2024-03-16 --json out.json
  python -m construction_agent.main web

已废弃的旧链路命令（V1 四维度 + HFACS-CS，产出无法并入主库）:
  process · batch · interactive · hfacs-encode · dataset
  默认被拦下；确需运行请加环境变量 CAIEMS_ALLOW_LEGACY=1
        """,
    )
    parser.add_argument("--version", action="version", version=f"v{__version__}")
    parser.add_argument("--log-level", default=None, help="日志级别 (DEBUG/INFO/WARNING/ERROR)")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # process: 处理单条
    p_process = subparsers.add_parser(
        "process", help="[已废弃] 旧链路处理单条（V1 四维度 + HFACS）")
    p_process.add_argument("text", help="事故描述文本")
    p_process.add_argument("--json", help="输出 JSON 文件路径")
    p_process.add_argument("--csv", help="输出 CSV 文件路径")

    # batch: 批量处理
    p_batch = subparsers.add_parser(
        "batch", help="[已废弃] 旧链路批量处理（文件逐行读取）")
    p_batch.add_argument("file", help="事故信息文件路径（每行一条）")
    p_batch.add_argument("--json", help="输出 JSON 文件路径")
    p_batch.add_argument("--csv", help="输出 CSV 文件路径")

    # extract: S1–S4 主管道处理单条（CAICS 链路）
    p_extract = subparsers.add_parser(
        "extract", help="S1–S4 主管道处理单条事故信息（CAICS 链路，与主库同构）")
    p_extract.add_argument("text", help="事故描述文本")
    p_extract.add_argument("--title", help="事故标题（默认取正文前 50 字）")
    p_extract.add_argument("--publish-date", dest="publish_date",
                           help="原文发布日期 YYYY-MM-DD（用于 S3 的日期推断）")
    p_extract.add_argument("--source", help="来源名称（写入 S3 的 metadata）")
    p_extract.add_argument("--no-search", action="store_true",
                           help="跳过 S3 后的搜索增强（省一次网络往返，产出与新采集数据一致）")
    p_extract.add_argument("--json", help="输出 JSON 文件路径")

    # interactive: 交互模式
    subparsers.add_parser("interactive", help="[已废弃] 旧链路交互式命令行模式")

    # web: Web UI
    subparsers.add_parser("web", help="启动 Web UI")

    # dataset: 数据集管理
    p_dataset = subparsers.add_parser(
        "dataset", help="[已废弃] JSON 快照数据集管理（非主库）")
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
    p_hfacs = subparsers.add_parser(
        "hfacs-encode", help="[已废弃] 对数据集进行 HFACS-CS 编码")
    p_hfacs.add_argument("--method", choices=["llm", "keyword"], default="llm",
                         help="编码方法：llm（API）或 keyword（零成本）")
    p_hfacs.add_argument("--input", default="data/accidents_merged.json", help="输入数据集")
    p_hfacs.add_argument("--output", default=None, help="输出文件（默认覆盖输入）")
    p_hfacs.add_argument("--start", type=int, default=0, help="起始索引")
    p_hfacs.add_argument("--count", type=int, default=None, help="处理条数")
    p_hfacs.add_argument("--resume", action="store_true", help="断点续传")

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        sys.exit(0)

    return args


def main(argv: list[str] | None = None):
    """入口函数。"""
    args = parse_args(argv)
    setup_logging(args.log_level)
    block_legacy(args.command)

    commands = {
        "process": cmd_process,
        "extract": cmd_extract,
        "batch": cmd_batch,
        "interactive": cmd_interactive,
        "web": cmd_web,
        "dataset": cmd_dataset,
        "hfacs-encode": cmd_hfacs_encode,
    }

    cmd_fn = commands.get(args.command)
    if cmd_fn:
        cmd_fn(args)


if __name__ == "__main__":
    main()
