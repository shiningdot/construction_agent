#!/usr/bin/env bash
# 等 S2 跑完，再依次跑 S3′/S4′/S5′（每个策略四模型串行），每个策略跑两遍——
# 第二遍只补第一遍的异常行（run_strategy.py 的断点续跑只跳过「完全成功」的条目）。
#
# 为什么串行：一次并发太多可能触发模型的限流/错误码（用户的明确要求）。任何时刻只有一个
# 策略在跑，每个策略内部也只有 --workers 3。
#
# 中断后重新执行本脚本即可续上：已成功的条目会被跳过。

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"

echo "等待 S2 的驱动进程退出 ... $(date '+%F %T')"
while pgrep -f "run_s0_all_models.sh S2" >/dev/null 2>&1; do
  sleep 60
done
echo "S2 已完成，开始后续策略 $(date '+%F %T')"

for S in S3 S4 S5; do
  echo "===== 第一遍 $S ====="
  bash "$HERE/run_s0_all_models.sh" "$S"
  echo "===== 第二遍 $S（补齐异常行）====="
  bash "$HERE/run_s0_all_models.sh" "$S"
done

echo "===== 补齐 S2 的异常行 ====="
bash "$HERE/run_s0_all_models.sh" S2

echo
echo "全部完成：$(date '+%F %T')"
