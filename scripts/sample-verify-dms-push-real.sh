#!/usr/bin/env bash
# DMS 建档验证（**真写**）：把一张身份证建成 DMS 客户，只对测试账号。
# 用法: scripts/sample-verify-dms-push-real.sh dms qd65956.localhost
#
# ⚠️ 这次会**真的往测试 DMS 写一个客户**（名字带 PEARNLY TEST 前缀，便于清理）。
#    不会碰客户账号，也不会建订车单。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-push-real.py"

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
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_push_real.py' < "$CHECK"

echo "真连 DMS 建档（起 chromium + 写测试客户，约 60–120 秒）…"
out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_push_real.py $site" 2>&1 || true)

clean=$(printf '%s\n' "$out" | grep -vE "^\s*(- |taking|waiting|fonts)")
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  |⚠️)" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 建档验证通过（写的是测试数据）"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -45 >&2
exit 1
