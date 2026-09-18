#!/usr/bin/env bash
# 从容器里真登一次 MR.ERP DMS（含会话锁互斥验证）。
#
# 用法: scripts/sample-verify-dms-login.sh dms qd65956.localhost
#
# 前置：站点里要有那条 DMS 连接（`scripts/sample-seed-dms-endpoint.sh` 种进去的）。
#
# ⚠️ 会真连 mrerp4sme.com、真占一次 `dmstest` 登录槽（退出时会登出）。
#    老产品文档：该账号只允许一个活跃登录，别和别的会话同时跑。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-login.py"

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
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

echo "[1/2] 送检查脚本进容器"
dc exec -T "$group-bench" bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_login.py' < "$CHECK"

# ⚠️ 用独立 python 进程，不用 `bench console`：IPython 里起 Playwright 会把进程打死
#    （见脚本头部注释）。这也是更真实的形态 —— 生产上跑 DMS 的是 web 请求/后台任务。
echo "[2/2] 真连 DMS（要起 chromium，约 20–60 秒）"
out=$(dc exec -T -e PYTHONUNBUFFERED=1 "$group-bench" bash -lc \
  "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_login.py $site" 2>&1)

clean=$(printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //')
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  )" || true

if printf '%s' "$clean" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 容器内真登 DMS 通过"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -40 >&2
exit 1
