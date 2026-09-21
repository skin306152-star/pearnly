#!/usr/bin/env bash
# LINE 会话层验证：状态 / TTL / nonce 防双写。不连网络。
# 用法: scripts/sample-verify-line-session.sh dms qd65956.localhost
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-line-session.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> <站点>}"
site="${2:?用法: $0 <组> <站点>}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_line_session.py' < "$CHECK"

out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_line_session.py $site" 2>&1 || true)
printf '%s\n' "$out" | grep -E "^(PASS|FAIL|RESULT|Traceback|  )" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] LINE 会话层验证通过"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$out" | tail -40 >&2
exit 1
