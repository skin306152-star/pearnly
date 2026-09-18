#!/usr/bin/env bash
# 「删除租户」的验收：造一个**确实存在**的一次性租户，走完整拆除流程，再逐项复核全都没了。
#
# 为什么必须真造一个：拆除是不可逆的（站点、数据库、身份账号一起没），不能拿真租户试。
# 这里造的是 `zzdel<时间>.localhost` 这种一次性的东西，用完要么被拆掉、要么被兜底清掉。
#
# 用法: scripts/sample-verify-tenant-teardown.sh [控制面站点]
#   scripts/sample-verify-tenant-teardown.sh control.localhost
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$HERE/scripts/sample-teardown-fixture.py"
CHECKS="$HERE/scripts/sample-teardown-checks.py"
UI="$HERE/scripts/sample-verify-tenant-delete-ui.mjs"
NODE="${PEARNLY_NODE:-$(command -v node)}"

site="${1:-control.localhost}"
STAMP="zzdel$(date +%H%M%S)"
DBSITE="${STAMP}.localhost"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

DB_ROOT_PW="$(sed -n 's/.*MARIADB_ROOT_PASSWORD:[[:space:]]*//p' "$HERE/infrastructure/compose/00-base.yml" | head -1 | tr -d '"'"'"' \r')"

run_fixture() {  # <mode> [tenant] [issue] [deployment]
  dc exec -T control-bench bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/teardown_fixture.py' < "$FIXTURE"
  dc exec -T \
    -e "MODE=$1" -e "TENANT=${2:-}" -e "ISSUE=${3:-}" -e "DEPLOYMENT=${4:-}" -e "STAMP=$STAMP" \
    control-bench bash -lc \
    "cd /home/frappe/frappe-bench && printf 'exec(open(\"/tmp/dmscheck/teardown_fixture.py\").read())\nmain()\n' | bench --site '$site' console 2>&1"
}

# ⚠️ 先把常驻执行器**停下来**：它每几秒轮询一次，一旦步骤排进去就会立刻开拆 ——
# 那样浏览器还没打开表单，租户就已经没了（真踩过）。
echo "[0/6] 暂停常驻执行器（拆完再放它继续）"
bash "$HERE/scripts/provision-runner.sh" stop >/dev/null 2>&1 || true

echo "[1/6] 造一次性租户（站点 + 数据库 + 身份账号 + OIDC 客户端 + 控制面记录）"
dc exec -T dms-bench bash -lc \
  "bench new-site $DBSITE --db-host mariadb --db-port 3306 --db-root-username root \
   --db-root-password '$DB_ROOT_PW' --mariadb-user-host-login-scope=% --admin-password admin-local-only 2>&1 | tail -2"
prep="$(run_fixture prepare)"
fixture="$(printf '%s' "$prep" | grep -o 'FIXTURE {.*}' | head -1 | sed 's/^FIXTURE //')"
if [ -z "$fixture" ]; then
  echo "[失败] 造夹具没过：" >&2
  printf '%s\n' "$prep" | tail -20 >&2
  exit 1
fi
TENANT="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["tenant"])')"
ISSUE="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["issue"])')"
DEPLOY="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["deployment"])')"
echo "  租户 $TENANT · 单号 $ISSUE · 部署 $DEPLOY · 站点 $DBSITE"

cleanup_on_failure() {
  echo "[收尾] 清掉落下的夹具"
  run_fixture cleanup "$TENANT" "$ISSUE" "$DEPLOY" | grep -E "CLEANED|身份服务" || true
  dc exec -T dms-bench bash -lc \
    "test -d /home/frappe/frappe-bench/sites/$DBSITE && { bench drop-site $DBSITE \
     --db-root-username root --db-root-password '$DB_ROOT_PW' --force --no-backup >/dev/null 2>&1 || true; \
     rm -rf /home/frappe/frappe-bench/sites/$DBSITE; } || true"
  dc exec -T dms-bench bash -lc "rm -rf /home/frappe/frappe-bench/archived/sites/$DBSITE" || true
}
trap 'if [ "${finished:-0}" != "1" ]; then cleanup_on_failure; fi' EXIT

echo "[2/6] 调按钮用的那个接口（api_plan_teardown）"
plan="$(run_fixture plan "$TENANT" "$ISSUE" "$DEPLOY")"
plan_ok=0
PLAN_JSON="$(printf '%s' "$plan" | grep -o 'PLAN {.*}' | head -1 | sed 's/^PLAN //')" \
  STEP=plan python3 "$CHECKS" || plan_ok=1

echo "[3/6] 在 Desk 上真点一次那个按钮"
ui_ok=0
"$NODE" "$UI" "$TENANT" "$DBSITE" || ui_ok=1

echo "[4/6] 放执行器继续跑（它自己轮询，不需要人催）"
bash "$HERE/scripts/provision-runner.sh" start >/dev/null 2>&1 || true
state=""
for i in $(seq 1 60); do
  state="$(run_fixture inspect "$TENANT" "$ISSUE" "$DEPLOY" | grep -o 'STATE {.*}' | head -1 | sed 's/^STATE //')"
  if [ -n "$state" ] && printf '%s' "$state" | python3 -c 'import json,sys; print("done" if not json.load(sys.stdin)["tenant_exists"] else "")' | grep -q done; then
    echo "  执行器已完成（第 ${i} 次轮询）"
    break
  fi
  sleep 3
done

echo "[5/5] 复核：站点目录 / 数据库 / 归档 / 控制面 / 身份服务"
left_dir="$(dc exec -T dms-bench bash -lc "test -d /home/frappe/frappe-bench/sites/$DBSITE && echo yes" | tr -d '\r' || true)"
left_archive="$(dc exec -T dms-bench bash -lc "test -d /home/frappe/frappe-bench/archived/sites/$DBSITE && echo yes" | tr -d '\r' || true)"
left_db="$(dc exec -T dms-bench bash -lc "mysql -h mariadb -uroot -p'$DB_ROOT_PW' -N -e \"show databases like '$DBSITE'\"" 2>/dev/null | tr -d '\r' || true)"
[ -z "$left_dir" ] && echo "PASS 站点目录已删除" || echo "FAIL 站点目录还在：$DBSITE"
[ -z "$left_db" ] && echo "PASS 数据库已删除" || echo "FAIL 数据库还在：$left_db"
[ -z "$left_archive" ] && echo "PASS 归档副本也没留（不备份）" || echo "FAIL 归档副本还在：archived/sites/$DBSITE"

verify_ok=0
if [ -n "$state" ]; then
  STATE_JSON="$state" STEP=verify python3 "$CHECKS" || verify_ok=1
else
  echo "FAIL 读不到复核数据" >&2
  verify_ok=1
fi

echo "[6/6] 收尾"
if [ "$plan_ok" = 0 ] && [ "$ui_ok" = 0 ] && [ "$verify_ok" = 0 ] && [ -z "$left_dir" ] && [ -z "$left_db" ] && [ -z "$left_archive" ]; then
  finished=1
  echo "[done] 删除租户（连同站点、数据库、账号与控制面记录）验证通过"
  exit 0
fi
echo "[失败] 见上面 FAIL 行（夹具已在收尾里清掉）" >&2
exit 1
