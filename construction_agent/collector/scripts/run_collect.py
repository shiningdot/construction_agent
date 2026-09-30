#!/usr/bin/env python3
"""数据采集脚本 —— 一键运行所有采集器。

用法:
    # 运行所有采集器
    python -m construction_agent.collector.scripts.run_collect

    # 仅运行指定采集器
    python -m construction_agent.collector.scripts.run_collect --source safehoo
    python -m construction_agent.collector.scripts.run_collect --source academic_import,open_gov

    # 启用 LLM 增强提取
    python -m construction_agent.collector.scripts.run_collect --llm

    # 仅采集不启用 LLM（省钱模式，默认）
    python -m construction_agent.collector.scripts.run_collect --no-llm

    # 查看可用采集器
    python -m construction_agent.collector.scripts.run_collect --list
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from rich.console import Console
from rich.logging import RichHandler
from rich.panel import Panel
from rich.table import Table

from construction_agent.collector.pipeline import CollectorPipeline
from construction_agent.config import settings

console = Console()


def setup_logging(level: str = "INFO"):
    """配置日志。"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True, console=console)],
    )


# ── 所有可用采集器注册表 ──
COLLECTOR_REGISTRY: dict[str, tuple[str, callable]] = {}


def _register():
    """注册所有采集器（延迟导入）。

    注意：使用 .update() 而非赋值 (=)，确保外部通过 from import
    获取的引用也能看到更新后的内容。
    """
    COLLECTOR_REGISTRY.clear()
    COLLECTOR_REGISTRY.update({
        "academic_import": (
            "学术数据集导入 (Mendeley/GitHub)",
            lambda: _import_collector("academic_import", "AcademicDatasetImporter"),
        ),
        "open_gov": (
            "政府开放数据平台 (沈阳/咸宁/菏泽等)",
            lambda: _import_collector("open_gov", "OpenGovCollector"),
        ),
        "safehoo": (
            "安全管理网 safehoo.com",
            lambda: _import_collector("safehoo", "SafehooCollector"),
        ),
        "mohurd": (
            # 这里原先写着「mohurd.gov.cn + mem.gov.cn」——`MohurdCollector`
            # 早期兼管两个部委。应急部现已独立成 `mem` 这个源（`MemCollector`
            # 覆盖 2005–2025 全部年份，是它的超集），registry 已把
            # `collect_mem` 关掉，理由见 `_MOHURD_ONLY` 的注释。
            "住建部事故通报 (mohurd.gov.cn)",
            lambda: _import_collector("mohurd", "MohurdCollector"),
        ),
        "mem": (
            "应急管理部事故通报 (mem.gov.cn)",
            lambda: _import_collector("mem", "MemCollector"),
        ),
        "news_aggregator": (
            "新闻搜索引擎聚合 (百度搜索)",
            lambda: _import_collector("news_aggregator", "NewsAggregator"),
        ),
        "beijing_em": (
            "北京市应急管理局事故调查 (yjglj.beijing.gov.cn)",
            lambda: _import_collector("beijing_em", "BeijingEMCollector"),
        ),
        "esafety": (
            "易安网事故报告 (esafety.cn)",
            lambda: _import_collector("esafety", "EsafetyCollector"),
        ),
        "gd_em": (
            "广东省应急管理厅事故调查 (yjgl.gd.gov.cn)",
            lambda: _import_collector("gd_em", "GdEMCollector"),
        ),
        "ln_em": (
            "辽宁省应急管理厅事故调查 (yjgl.ln.gov.cn)",
            lambda: _import_collector("ln_em", "LnEMCollector"),
        ),
        "henan_em": (
            "河南省应急管理厅事故信息 (yjglt.henan.gov.cn)",
            lambda: _import_collector("henan_em", "HenanEMCollector"),
        ),
        "nmg_em": (
            "内蒙古应急管理厅事故查处 (yjglt.nmg.gov.cn)",
            lambda: _import_collector("nmg_em", "NmgEMCollector"),
        ),
        "hebei_em": (
            "河北省应急管理厅事故调查 (yjgl.hebei.gov.cn)",
            lambda: _import_collector("hebei_em", "HebeiEMCollector"),
        ),
        "tj_em": (
            "天津市应急管理局事故调查 (yjgl.tj.gov.cn)",
            lambda: _import_collector("tj_em", "TjEMCollector"),
        ),
        "sx_em": (
            "山西省应急管理厅事故调查 (yjt.shanxi.gov.cn)",
            lambda: _import_collector("sx_em", "SxEMCollector"),
        ),
        "sh_em": (
            "上海市应急管理局事故调查 (yjglj.sh.gov.cn)",
            lambda: _import_collector("sh_em", "ShEMCollector"),
        ),
        "js_em": (
            "江苏省应急管理厅事故调查 (yjglt.jiangsu.gov.cn)",
            lambda: _import_collector("js_em", "JsEMCollector"),
        ),
        "hain_em": (
            "海南省应急管理厅事故调查 (yjglt.hainan.gov.cn)",
            lambda: _import_collector("hain_em", "HainEMCollector"),
        ),
        "cq_em": (
            "重庆市应急管理局事故调查 (yjj.cq.gov.cn)",
            lambda: _import_collector("cq_em", "CqEMCollector"),
        ),
        "gz_em": (
            "贵州省应急管理厅事故调查 (yjgl.guizhou.gov.cn)",
            lambda: _import_collector("gz_em", "GzEMCollector"),
        ),
        "shaanxi_em": (
            "陕西省应急管理厅事故调查 (yjt.shaanxi.gov.cn)",
            lambda: _import_collector("shaanxi_em", "ShaanxiEMCollector"),
        ),
        "jx_em": (
            "江西省应急管理厅事故调查 (yjglt.jiangxi.gov.cn)",
            lambda: _import_collector("jx_em", "JxEMCollector"),
        ),
        "hub_em": (
            "湖北省应急管理厅事故调查 (yjt.hubei.gov.cn)",
            lambda: _import_collector("hub_em", "HubEMCollector"),
        ),
        "jl_em": (
            "吉林省应急管理厅事故调查 (xxgk.jl.gov.cn)",
            lambda: _import_collector("jl_em", "JlEMCollector"),
        ),
        "sc_em": (
            "四川省应急管理厅事故调查 (yjt.sc.gov.cn)",
            lambda: _import_collector("sc_em", "ScEMCollector"),
        ),
    })


def _import_collector(module_name: str, class_name: str):
    """动态导入采集器类并实例化。

    返回的实例必须继承 BaseCollector，否则拒绝注册。
    """
    import importlib
    from construction_agent.collector.base import BaseCollector

    try:
        module = importlib.import_module(
            f"construction_agent.collector.sources.{module_name}"
        )
        cls = getattr(module, class_name)
        instance = cls()
        if not isinstance(instance, BaseCollector):
            console.print(
                f"[red]✗ {class_name} 未继承 BaseCollector，"
                f"collect() 可能与 CollectorPipeline 不兼容，已跳过[/]"
            )
            return None
        return instance
    except ImportError as e:
        console.print(f"[red]导入 {module_name} 失败: {e}[/]")
        return None


def parse_args():
    parser = argparse.ArgumentParser(
        description="建筑施工事故数据采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python -m construction_agent.collector.scripts.run_collect              # 运行全部
  python -m construction_agent.collector.scripts.run_collect --source safehoo  # 单个
  python -m construction_agent.collector.scripts.run_collect --list       # 列出可用
  python -m construction_agent.collector.scripts.run_collect --llm        # 启用LLM
        """,
    )
    parser.add_argument(
        "--source", "-s",
        help="指定采集器（逗号分隔多个），默认全部",
        default=None,
    )
    parser.add_argument(
        "--llm", action="store_true", default=False,
        help="启用 LLM 增强提取（默认禁用以节省 API 成本）",
    )
    parser.add_argument(
        "--no-llm", action="store_true", default=True,
        help="禁用 LLM（默认）",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="列出所有可用采集器",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        help="日志级别 (DEBUG/INFO/WARNING/ERROR)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="采集统计报告输出路径",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    setup_logging(args.log_level)

    _register()

    # 列出可用采集器
    if args.list:
        _list_collectors()
        return

    # 构建采集器列表
    if args.source:
        source_names = [s.strip() for s in args.source.split(",")]
    else:
        source_names = list(COLLECTOR_REGISTRY.keys())

    # 验证采集器名称
    invalid = [n for n in source_names if n not in COLLECTOR_REGISTRY]
    if invalid:
        console.print(f"[red]未知采集器: {invalid}[/]")
        console.print(f"可用采集器: {list(COLLECTOR_REGISTRY.keys())}")
        sys.exit(1)

    # 实例化采集器
    collectors = []
    for name in source_names:
        desc, factory = COLLECTOR_REGISTRY[name]
        console.print(f"🔧 初始化: {desc}")
        instance = factory()
        if instance:
            collectors.append(instance)
        else:
            console.print(f"[red]  ✗ {name} 初始化失败，跳过[/]")

    if not collectors:
        console.print("[red]没有可用的采集器[/]")
        sys.exit(1)

    # 创建流水线
    enable_llm = args.llm and not args.no_llm
    pipeline = CollectorPipeline(enable_llm=enable_llm)

    for c in collectors:
        pipeline.register_collector(c)

    # 运行
    console.print(Panel(
        f"[bold]启动 {len(collectors)} 个采集器[/]\n"
        f"LLM 增强: {'[green]启用[/]' if enable_llm else '[yellow]禁用（省钱模式）[/]'}\n"
        f"数据存储: {settings.DATASET_FILE}",
        title="🚀 数据采集",
        border_style="blue",
    ))

    stats = pipeline.run()

    # 保存统计报告
    pipeline.save_stats(
        Path(args.output) if args.output else None
    )

    # 显示汇总表格
    _show_summary(stats)


def _list_collectors():
    """列出所有可用采集器。"""
    table = Table(title="可用数据源采集器")
    table.add_column("名称", style="cyan")
    table.add_column("描述")

    for name, (desc, _) in COLLECTOR_REGISTRY.items():
        table.add_row(name, desc)

    console.print(table)
    console.print("\n[dim]用法: python -m construction_agent.collector.scripts.run_collect --source <名称>[/]")


def _show_summary(stats: dict):
    """显示采集汇总表格。"""
    table = Table(title="📊 采集汇总")
    table.add_column("指标", style="cyan")
    table.add_column("数值", style="green")

    table.add_row("总采集数", str(stats.get("total_collected", 0)))
    table.add_row("清洗后有效", str(stats.get("total_after_clean", 0)))
    table.add_row("去重过滤", str(stats.get("duplicates_filtered", 0)))
    table.add_row("新增入库", str(stats.get("added", 0)))
    table.add_row("合并已有", str(stats.get("merged", 0)))
    table.add_row("LLM增强", str(stats.get("llm_enhanced", 0)))
    table.add_row("错误数", str(stats.get("errors", 0)))
    table.add_row("耗时", f"{stats.get('duration_seconds', 0)}s")

    # 去重层级分布
    layers = stats.get("by_layer", {})
    if layers:
        layer_str = " | ".join(f"{k}: {v}" for k, v in sorted(layers.items()) if v > 0)
        table.add_row("去重层级分布", layer_str)

    console.print(table)

    # 按数据源统计
    by_source = stats.get("by_source", {})
    if by_source:
        source_table = Table(title="📋 数据源明细")
        source_table.add_column("数据源")
        source_table.add_column("采集数")
        source_table.add_column("新增")
        source_table.add_column("去重")
        source_table.add_column("合并")

        for name, s in by_source.items():
            source_table.add_row(
                name,
                str(s.get("collected", 0)),
                str(s.get("added", 0)),
                str(s.get("duplicates", 0)),
                str(s.get("merged", 0)),
            )

        console.print(source_table)


if __name__ == "__main__":
    main()
