#!/usr/bin/env bash
# DMS 编排层验证（A 部分）：纯逻辑 —— 不起浏览器、不连网络。
# 用法: scripts/sample-verify-dms-intake.sh dms
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-intake.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组>}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_intake.py' < "$CHECK"

# ⚠️ `|| true`：被验的脚本失败时它自己就是非零退出，`set -e` 会在打印之前把整个
#    包装脚本干掉 —— 那样你只看到"什么都没有"，连报错都看不到（踩过）。
out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc '/home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_intake.py' 2>&1 || true)
printf '%s\n' "$out"

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 编排层纯逻辑验证通过"
  exit 0
fi
echo "[失败] 见上面输出" >&2
exit 1
