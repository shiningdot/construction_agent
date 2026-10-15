#!/usr/bin/env bash
# 错峰调度：非 DeepSeek 模型立刻跑，DeepSeek 等到 18:00 之后再跑（省成本）。
#
# 为什么这样排：DeepSeek 在错峰时段有折扣，白天跑不划算。而 GLM 与混元没有这类时段价，
# 让它们先占住时间；到点后再起第二条链，两条链各自跑各自的模型、写各自的目录，
# 互不冲突（同一个策略的不同模型结果落在不同文件里）。
#
# 并发控制：两条链各 3 / 2 个并发，合计 5 个在途请求，仍在用户要求的「别一次开太多」范围内。
#
# 本脚本可反复执行：断电、断网、额度中断后直接重跑即可续上，已成功的条目会被秒过。
#
# 用法：
#   bash scripts/golden_s1/run_offpeak.sh              # 默认 18:00 切 DeepSeek
#   OFFPEAK=1900 bash scripts/golden_s1/run_offpeak.sh # 改用 19:00

set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
OFFPEAK="${OFFPEAK:-1800}"          # HHMM，DeepSeek 最早开跑时间
STRATEGIES=(S2 S3 S4 S5)

hhmm() { date +%H%M; }

echo "开始时间 $(date '+%F %T')｜非 DeepSeek 模型立刻开跑；DeepSeek 等到 $OFFPEAK 之后"

# ---- 链 A：GLM + 混元（现在就跑）----
MODELS="glm hy3" bash "$HERE/run_matrix_remaining.sh" "${STRATEGIES[@]}" &
PID_A=$!
echo "链 A（glm + hy3）已启动，PID=$PID_A"

# ---- 等到错峰时段 ----
while [ "$(hhmm)" -lt "$OFFPEAK" ]; do
  sleep 60
done
echo "已进入错峰时段 $(date '+%F %T')，启动链 B（DeepSeek）"

# ---- 链 B：DeepSeek（错峰时段才跑）----
MODELS="flash pro" WORKERS=2 bash "$HERE/run_matrix_remaining.sh" "${STRATEGIES[@]}" &
PID_B=$!
echo "链 B（flash + pro）已启动，PID=$PID_B"

wait $PID_A
echo "链 A 结束：$(date '+%F %T')"
wait $PID_B
echo "链 B 结束：$(date '+%F %T')"

echo
echo "全部完成：$(date '+%F %T')"
