"""
实验一入口

用法:
  # 运行评测
  python -m construction_agent.evaluation.experiment1.main run --models qwen3 glm52

  # 生成报告
  python -m construction_agent.evaluation.experiment1.main report <result.json>

  # 查看样本
  python -m construction_agent.evaluation.experiment1.main sample
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def cmd_run(args):
    from construction_agent.evaluation.experiment1.experiment_runner import Experiment1Runner
    r = Experiment1Runner(models=args.models, sample_size=args.sample_size)
    r.run_all()
    r.save()


def cmd_report(args):
    from construction_agent.evaluation.experiment1.comparison_report import run_report
    run_report(args.result_file)


def cmd_sample(args):
    from construction_agent.evaluation.experiment1.experiment_runner import Experiment1Runner
    r = Experiment1Runner(sample_size=args.sample_size)
    r.prepare_sample()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd")

    rp = sp.add_parser("run")
    rp.add_argument("--models", nargs="+")
    rp.add_argument("--sample-size", type=int, default=200)

    rep = sp.add_parser("report")
    rep.add_argument("result_file")

    sp.add_parser("sample").add_argument("--sample-size", type=int, default=200)

    args = p.parse_args()
    {"run": cmd_run, "report": cmd_report, "sample": cmd_sample}[args.cmd](args)
