#!/usr/bin/env bash
# 「等多久」这套口径到底生效没有 —— 起假 DMS、用真浏览器真适配器登一遍。
# 用法: scripts/sample-verify-dms-login-budget.sh dms
#
# 走 `bench console`（需要站点上下文：会话锁走 Frappe 缓存），浏览器用镜像里的 Playwright。
# 实验室容器只挂了部分 App，所以把本工作树的 pearnly_dms 拷进临时目录放到 PYTHONPATH 前面 ——
# 验的是**正在改的这份代码**，不是镜像里那份旧副本。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-login-budget.py"

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

docker --context "$CTX" compose "${args[@]}" exec -T -u root "$group-bench" \
  bash -lc 'rm -rf /tmp/dmsbudget_pkg /tmp/verify_login_budget.py && mkdir -p /tmp/dmsbudget_pkg'
docker --context "$CTX" compose "${args[@]}" cp \
  "$HERE/apps/pearnly_dms/pearnly_dms" "$group-bench:/tmp/dmsbudget_pkg/pearnly_dms" >/dev/null
docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'cat > /tmp/verify_login_budget.py' < "$CHECK"

out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONPATH=/tmp/dmsbudget_pkg \
  "$group-bench" bash -lc "cd /home/frappe/frappe-bench && printf 'exec(compile(open(\"/tmp/verify_login_budget.py\").read(),\"budget\",\"exec\"), globals())\nmain()\n' | bench --site erp-pos-a.localhost console 2>&1 | grep -E 'PASS|FAIL|RESULT|合计' || true")
printf '%s\n' "$out"

docker --context "$CTX" compose "${args[@]}" exec -T -u root "$group-bench" \
  bash -lc 'rm -rf /tmp/dmsbudget_pkg /tmp/verify_login_budget.py' >/dev/null 2>&1 || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 登录预算验证通过"
  exit 0
fi
echo "[失败] 见上面输出" >&2
exit 1
