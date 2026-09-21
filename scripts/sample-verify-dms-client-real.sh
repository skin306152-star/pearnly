#!/usr/bin/env bash
# DMS 业务客户端验证（B 部分）：对真系统只读验证（登录 + 查/读，不写任何东西）。
#
# 用法: scripts/sample-verify-dms-client-real.sh dms qd65956.localhost
#
# ⚠️ 会真连 mrerp4sme.com 并占用一次 `dmstest` 登录（退出时登出）。
#    只做读操作：不建客户、不建单。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-client-real.py"

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
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_client_real.py' < "$CHECK"

echo "真连 DMS 做只读验证（起 chromium，约 30–90 秒）…"
out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_client_real.py $site" 2>&1 || true)

clean=$(printf '%s\n' "$out" | grep -vE "^\s*(- |taking|waiting|fonts)")
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  )" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 对真系统只读验证通过"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -40 >&2
exit 1
