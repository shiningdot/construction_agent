#!/usr/bin/env bash
# 把指定策略在四个模型上「串行」跑全量 300 条。
#
# 为什么串行：一次并发太多可能触发模型的限流/错误码，故四模型一个一个来，
# 每个模型内部也只用 --workers 3（可用 $WORKERS 覆盖）。
# 逐条落盘 + 断点续跑由 run_strategy.py 保证，中断后重跑本脚本即可续上。
#
# 用法：
#   bash scripts/golden_s1/run_s0_all_models.sh S0        # 默认策略 S0
#   STRATEGY=S2 WORKERS=2 bash scripts/golden_s1/run_s0_all_models.sh

set -u
PY=/home/suntianhao/construction_agent/.venv/bin/python3
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$HERE/run_strategy.py"
STRATEGY="${1:-${STRATEGY:-S0}}"
WORKERS="${WORKERS:-3}"

run_one () {  # $1=provider  $2=model  $3=中文说明
  echo "=================================================================="
  echo ">>> $STRATEGY × $3（$2）  开始：$(date '+%F %T')"
  echo "=================================================================="
  "$PY" -I "$RUNNER" --strategy "$STRATEGY" --provider "$1" --model "$2" --workers "$WORKERS"
  echo ">>> $STRATEGY × $3 结束：$(date '+%F %T')  exit=$?"
}

# 顺序：先快后慢，便于尽早拿到部分结果
run_one glm      glm-5.2            "智谱 GLM-5.2"
run_one deepseek deepseek-v4-flash  "DeepSeek V4 Flash"
run_one hunyuan  hy3                "腾讯混元 hy3"
run_one deepseek deepseek-v4-pro    "DeepSeek V4 Pro"

echo
echo "全部完成：$(date '+%F %T')"
