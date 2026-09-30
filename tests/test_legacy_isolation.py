"""生产路径不许拖着旧理论一起加载。

## 为什么用 `sys.modules` 而不是 grep

静态地看 import 语句分不清三种写法：

    from .claude_extractor import ExtractionPipeline      # 模块级 —— 一定会执行
    from .claude_extractor import ExtractionPipeline      # if TYPE_CHECKING 里 —— 永不执行
    return importlib.import_module("...claude_extractor") # PEP 562 —— 取了才执行

三者都写作 `from ... import ...` 或含同样的模块名，`grep` 一视同仁。而
"生产路径有没有被旧理论压着"恰恰取决于它是哪一种。

所以判据只能是运行时的 `sys.modules`：真 import 一遍，看它在不在。

## 为什么用子进程

pytest 进程里 `sys.modules` 早就被别的测试塞满了，同进程测什么都"已加载"。
每个探针起一个干净的子进程，结论才可信。

## 这条测试守的是什么

`construction_agent/collector/__init__.py` 原先模块级导出 `CollectorPipeline`，
而 `web/views.py` 有 `from ..collector import executor`——于是新 web 的每一个
页面都顺带把旧编排器、`DatasetManager`、`ExtractionPipeline` 拉进内存。
这类耦合不会报错，只是悄悄地让每次请求都多背一层旧代码。改回模块级导入，
这里就会红。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# 旧理论/旧链路的模块：不该被生产入口顺带加载
LEGACY = {
    "construction_agent.hfacs": "HFACS-CS 6 层体系",
    "construction_agent.extraction.claude_extractor": "ExtractionPipeline（V1 四维度）",
    "construction_agent.database.dataset_manager": "DatasetManager（JSON 快照）",
    "construction_agent.collector.pipeline": "CollectorPipeline（旧采集编排）",
    "construction_agent.output.csv_formatter": "CSVFormatter",
    "construction_agent.output.json_formatter": "JSONFormatter",
    "construction_agent.models.accident": "V1 四维度 dataclass",
    "construction_agent.database.sqlite_repository": "SQLAlchemy ORM 仓储",
    "construction_agent.database.models": "SQLAlchemy ORM 模型",
}

# 生产入口：用户真会走到的地方
PROBES = [
    ("新 web 起应用", "construction_agent.web.factory"),
    ("页面蓝图（导入了 collector）", "construction_agent.web.views"),
    ("S1–S4 主管道", "construction_agent.extraction.caics_pipeline"),
    ("暂存执行器", "construction_agent.extraction.staging_executor"),
    ("采集执行器", "construction_agent.collector.executor"),
    ("并入最终库", "construction_agent.review.promote"),
    ("采集源注册表", "construction_agent.collector.registry"),
]

_LOADED = """
import sys
sys.path.insert(0, {root!r})
import importlib
importlib.import_module({mod!r})
print("|".join(sorted(m for m in sys.modules if m in {legacy!r})))
"""


def _run(code: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", code],
                          capture_output=True, text=True, timeout=180)


@pytest.mark.parametrize("label,module", PROBES, ids=[p[0] for p in PROBES])
def test_production_entry_does_not_load_legacy(label, module):
    r = _run(_LOADED.format(root=str(ROOT), mod=module,
                            legacy=set(LEGACY)))
    assert r.returncode == 0, f"{label} 导入失败：{r.stderr[-500:]}"
    hits = [h for h in r.stdout.strip().split("|") if h]
    assert not hits, (
        f"{label} 顺带加载了 {len(hits)} 个旧模块：\n"
        + "\n".join(f"  · {h}  —— {LEGACY[h]}" for h in hits))


_LAZY = """
import sys
sys.path.insert(0, {root!r})
import importlib
m = importlib.import_module({pkg!r})
before = {target!r} in sys.modules
getattr(m, {attr!r})
print(f"{{before}}|{{{target!r} in sys.modules}}")
"""


@pytest.mark.parametrize("pkg,attr,target", [
    ("construction_agent.extraction", "ExtractionPipeline",
     "construction_agent.extraction.claude_extractor"),
    ("construction_agent.collector", "CollectorPipeline",
     "construction_agent.collector.pipeline"),
    ("construction_agent.database", "DatasetManager",
     "construction_agent.database.dataset_manager"),
    ("construction_agent.output", "CSVFormatter",
     "construction_agent.output.csv_formatter"),
    ("construction_agent.models", "Accident",
     "construction_agent.models.accident"),
])
def test_lazy_export_still_works(pkg, attr, target):
    """惰性导出要两头都对：不主动加载，但取得到。

    只测前一头是不够的——把导出整段删掉同样能让 `sys.modules` 干净，
    却会顺手打断 `collector/scripts/run_collect.py` 这些还用它的人。
    """
    r = _run(_LAZY.format(root=str(ROOT), pkg=pkg, attr=attr, target=target))
    assert r.returncode == 0, (
        f"{pkg}.{attr} 取不到：{r.stderr.strip().splitlines()[-1]}")
    before, after = r.stdout.strip().split("|")
    assert before == "False", f"{pkg} 在取 {attr} 之前就加载了 {target}"
    assert after == "True", (
        f"{pkg}.{attr} 拿到了，但 {target} 没被加载——"
        f"说明它其实来自别处，上面的「不加载」不是惰性的功劳")
