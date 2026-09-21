#!/usr/bin/env bash
# DMS LINE 卡片构造验证。
# 用法: scripts/sample-verify-dms-cards.sh dms [站点]
#
# ⚠️ 这里走 `bench console`（要站点上下文），不是裸 python：卡片里那个编辑按钮现在是
# **深链**（线上行为），它的地址来自站点配置与站点地址 —— 裸 python 没有站点上下文，
# `frappe.conf` 直接 "object is not bound"（换成 console 之前真踩过）。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-cards.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> [站点]}"
site="${2:-localhost}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_cards.py' < "$CHECK"

out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench && printf 'exec(open(\"/tmp/dmscheck/verify_cards.py\").read())\nmain()\n' | PYTHONUNBUFFERED=1 bench --site '$site' console 2>&1 | grep -E 'PASS|FAIL|RESULT|合计' || true")
printf '%s\n' "$out"

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 卡片构造验证通过"
  exit 0
fi
echo "[失败] 见上面输出" >&2
exit 1
