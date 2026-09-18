#!/usr/bin/env bash
# 编辑深链验证（需要站点上下文：读站点配置 + 站点地址，所以走 bench console）。
# 用法: scripts/sample-verify-dms-edit-link.sh <组> <站点>
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-edit-link.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> <站点>}"
site="${2:?用法: $0 <组> <站点>}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_edit_link.py' < "$CHECK"

out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench && printf 'exec(open(\"/tmp/dmscheck/verify_edit_link.py\").read())\nmain()\n' | PYTHONUNBUFFERED=1 bench --site '$site' console 2>&1 | grep -E 'PASS|FAIL|RESULT|合计' || true")
printf '%s\n' "$out"

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 编辑深链验证通过"
  exit 0
fi
echo "[失败] 见上面输出" >&2
exit 1
