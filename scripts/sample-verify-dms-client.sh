#!/usr/bin/env bash
# DMS 业务客户端验证（A 部分）：假管道验纯逻辑 —— 不起浏览器、不连网络。
#
# 用法: scripts/sample-verify-dms-client.sh dms
#
# 不需要站点、不需要凭据：这批模块不 import frappe，纯 python 就能跑。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-client.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组>}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_client.py' < "$CHECK"

# ⚠️ `|| true`：见 sample-verify-dms-intake.sh 里同一个坑的说明。
out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc '/home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_client.py' 2>&1 || true)
printf '%s\n' "$out"

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 业务客户端纯逻辑验证通过"
  exit 0
fi
echo "[失败] 见上面输出" >&2
exit 1
