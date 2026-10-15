#!/usr/bin/env bash
# 把指定策略在多个模型上「串行」跑全量 300 条。
#
# 为什么串行：一次并发太多可能触发模型的限流/错误码，故几个模型一个一个来，
# 每个模型内部也只用 --workers 3（可用 $WORKERS 覆盖）。
# 逐条落盘 + 断点续跑由 run_strategy.py 保证，中断后重跑本脚本即可续上
# （已成功的条目会被秒过，只重跑失败/缺失的）。
#
# 用法：
#   bash scripts/golden_s1/run_s0_all_models.sh S0                    # 默认策略 S0，全部模型
#   STRATEGY=S2 WORKERS=2 bash scripts/golden_s1/run_s0_all_models.sh
#   MODELS="glm hy3" bash scripts/golden_s1/run_s0_all_models.sh S3   # 只跑 GLM 与混元
#
# MODELS 可选值（默认顺序即下面的排列，先快后慢，便于尽早拿到部分结果）：
#   glm   → 智谱 GLM-5.2
#   flash → DeepSeek V4 Flash
#   hy3   → 腾讯混元 hy3
#   pro   → DeepSeek V4 Pro
# 服务商故障时可只挑可用模型，例如 MODELS="glm hy3"（DeepSeek 官网故障期间即如此用）。

set -u
PY=/home/suntianhao/construction_agent/.venv/bin/python3
HERE="$(cd "$(dirname "$0")" && pwd)"
RUNNER="$HERE/run_strategy.py"
STRATEGY="${1:-${STRATEGY:-S0}}"
WORKERS="${WORKERS:-3}"
MODELS="${MODELS:-glm flash hy3 pro}"

run_glm()   { "$PY" -I "$RUNNER" --strategy "$STRATEGY" --provider glm      --model glm-5.2           --workers "$WORKERS"; }
run_flash() { "$PY" -I "$RUNNER" --strategy "$STRATEGY" --provider deepseek --model deepseek-v4-flash --workers "$WORKERS"; }
run_hy3()   { "$PY" -I "$RUNNER" --strategy "$STRATEGY" --provider hunyuan  --model hy3               --workers "$WORKERS"; }
run_pro()   { "$PY" -I "$RUNNER" --strategy "$STRATEGY" --provider deepseek --model deepseek-v4-pro   --workers "$WORKERS"; }

for M in $MODELS; do
  case "$M" in
    glm|flash|hy3|pro) ;;
    *) echo "未知模型代号：$M（可选 glm / flash / hy3 / pro）"; exit 1 ;;
  esac
  echo "=================================================================="
  echo ">>> $STRATEGY × $M  开始：$(date '+%F %T')"
  echo "=================================================================="
  "run_$M"
  echo ">>> $STRATEGY × $M 结束：$(date '+%F %T')"
done

echo
echo "全部完成：$(date '+%F %T')"
