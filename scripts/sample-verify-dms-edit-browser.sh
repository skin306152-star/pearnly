#!/usr/bin/env bash
# 订车/客户档浏览器编辑器（LINE 内那一页）的后端验证。
# 需要站点上下文（会话 / 推送日志 / 票据都挂在站点上），所以走 bench console。
# 用法: scripts/sample-verify-dms-edit-browser.sh <组> <站点>
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-edit.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> <站点> [端口]}"
site="${2:?用法: $0 <组> <站点> [端口]}"
port="${3:-8103}"

docker --context "$CTX" compose "${args[@]}" exec -T "$group-bench" \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_edit.py' < "$CHECK"

out=$(docker --context "$CTX" compose "${args[@]}" exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench && printf 'exec(open(\"/tmp/dmscheck/verify_edit.py\").read())\nmain()\n' | PYTHONUNBUFFERED=1 bench --site '$site' console 2>&1 | grep -E 'PASS|FAIL|RESULT|合计|（' || true")
printf '%s\n' "$out"

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[ok] 站点逻辑层通过（91 项）"
else
  echo "[失败] 站点逻辑层见上面输出" >&2
  exit 1
fi

# ── HTTP 层：白名单接口与那两页真的能被浏览器拿到 ──────────────────────────
# `bench console` 里没有真实请求（取不到 csrf、也验不到 allow_guest / 路由），
# 这几条必须走真的 HTTP。
API=http://127.0.0.1:${port}
fail=0

config=$(curl -sS -H "Host: $site" "$API/api/method/pearnly_dms.editor.browser_api.browser_config")
if printf '%s' "$config" | grep -q '"available": *false' \
   && printf '%s' "$config" | grep -qE '"csrf_token": *"[^"]+"'; then
  echo "PASS 真实请求里 browser_config 下发 csrf、并如实说本机没有 LIFF"
else
  echo "FAIL browser_config 的 HTTP 返回不对：$config" >&2
  fail=1
fi

texts=$(curl -sS -H "Host: $site" \
  "$API/api/method/pearnly_dms.editor.browser_api.browser_text?keys=%5B%22dms.edit.save%22%5D")
if printf '%s' "$texts" | grep -q 'บันทึกและส่งสรุปใหม่'; then
  echo "PASS 游客能取到页面文案（allow_guest 生效，且只给 dms.* 命名空间）"
else
  echo "FAIL browser_text 的 HTTP 返回不对：$texts" >&2
  fail=1
fi

# 页面是 JS 拼接口路径的（`API + "browser_auth"`），所以分开匹配，别指望出现完整点分名。
page=$(curl -sS -H "Host: $site" "$API/liff/dms-booking?draft=n1&editor=customer")
if printf '%s' "$page" | grep -q '/assets/pearnly_core/pearnly-app/index.html' \
   && printf '%s' "$page" | grep -q 'static.line-scdn.net/liff' \
   && printf '%s' "$page" | grep -q 'pearnly_dms.editor.browser_api.' \
   && printf '%s' "$page" | grep -q '"browser_auth"' \
   && printf '%s' "$page" | grep -q 'pearnly_dms_ticket'; then
  echo "PASS /liff/dms-booking 真能打开（LIFF 落地页 → 换票据 → 交给前端产物）"
else
  echo "FAIL /liff/dms-booking 页面不对（路由没生效或内容缺件）" >&2
  fail=1
fi

if [ "$fail" = 0 ]; then
  echo "[done] 编辑器（站点逻辑 + HTTP 路由）验证通过"
  exit 0
fi
exit 1
