#!/usr/bin/env bash
# 把「策略 × 模型」矩阵剩下的格子按顺序跑满。
#
# 每个策略跑两遍：第二遍只补第一遍的异常行——run_strategy.py 的断点续跑**只跳过
# 「完全成功」的条目**，所以已成功的会被秒过，失败/缺失的自动重跑。
#
# 为什么串行：一次并发太多可能触发模型的限流/错误码（用户的明确要求）。任何时刻只有
# 一个策略在跑，每个策略内部也只有 --workers 3。
#
# **本脚本可反复执行**：断电、断网、额度中断后直接重跑即可续上，不会重复已完成的工作。
# 策略顺序由参数给定，默认 S2 S3 S4 S5（S2 上次被断电打断，hy3 只跑了 8/300、pro 未开跑）。
#
# 用法：
#   bash scripts/golden_s1/run_matrix_remaining.sh              # 默认 S2 S3 S4 S5
#   bash scripts/golden_s1/run_matrix_remaining.sh S4 S5        # 只跑指定的

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
STRATEGIES=("$@")
[ ${#STRATEGIES[@]} -eq 0 ] && STRATEGIES=(S2 S3 S4 S5)

echo "开始时间 $(date '+%F %T')｜本次要跑：${STRATEGIES[*]}"
echo

for S in "${STRATEGIES[@]}"; do
  echo "===== 第一遍 $S  $(date '+%F %T') ====="
  bash "$HERE/run_s0_all_models.sh" "$S"
  echo "===== 第二遍 $S（只补异常/缺失行）  $(date '+%F %T') ====="
  bash "$HERE/run_s0_all_models.sh" "$S"
  echo
done

echo "全部完成：$(date '+%F %T')"
