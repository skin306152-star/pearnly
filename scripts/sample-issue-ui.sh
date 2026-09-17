#!/usr/bin/env bash
# 发号页验证：页面可打开且无未渲染模板 -> 登录 -> 取上下文与令牌 -> 用页面同一条接口发号
#
# 用法: scripts/sample-issue-ui.sh <平台用户名> <平台密码>
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080
CONTROL_HOST=control.localhost
CONTROL=http://127.0.0.1:8104

USER_NAME="${1:?用法: $0 <平台用户名> <平台密码>}"
USER_PASS="${2:?}"
SUFFIX="$(date +%s)"

JAR=$(mktemp); BODY_FILE=$(mktemp)
trap 'rm -f "$JAR" "$BODY_FILE"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

echo "[1] 未登录打开发号页：应能打开，但页面里不能有未渲染的模板变量"
# 改过 www 页面后必须清站点网页缓存，否则会拿到旧的渲染结果（踩过：表现为
# 页面里残留未渲染的 {{ }}，看着像模板没生效，其实是缓存）。
docker --context colima-pearnly compose -f infrastructure/compose/01-bench.yml \
  exec -T control-bench bench --site control.localhost clear-website-cache >/dev/null 2>&1 || true
PAGE=$(curl -sS -H "Host: $CONTROL_HOST" "$CONTROL/pearnly-issue")
echo "    页面字节数: ${#PAGE}"
echo "    未渲染模板变量个数: $(printf '%s' "$PAGE" | grep -c '{{' || true)"
echo "    表单字段 present: $(printf '%s' "$PAGE" | grep -c 'pi-tenant')"
echo "    未登录调 api_context: $(curl -sS -H "Host: $CONTROL_HOST" "$CONTROL/api/method/pearnly_control.issues.api_context" | grep -oE 'PermissionError|Not permitted' | head -1)"

echo "[2] 登录控制面（平台 realm）"
LOC=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $CONTROL_HOST" \
      "$CONTROL/api/method/pearnly_core.identity.begin_login" | loc_of)
PAGE2=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$LOC" | to_host)")
ACT=$(printf '%s' "$PAGE2" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
BACK=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
      --data-urlencode "username=$USER_NAME" --data-urlencode "password=$USER_PASS" \
      --data-urlencode "credentialId=" "$(printf '%s' "$ACT" | to_host)" | loc_of)
curl -sS -c "$JAR" -b "$JAR" -o /dev/null -H "Host: $CONTROL_HOST" "$BACK"
echo "    当前操作人: $(curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/frappe.auth.get_logged_user" | head -c 60)"

echo "[3] 取上下文（页面加载时做的同一件事）"
CTX=$(curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/pearnly_control.issues.api_context")
echo "$CTX" | python3 -c '
import sys, json
m = json.load(sys.stdin).get("message") or {}
print("    操作人:", m.get("operator"), "| 令牌长度:", len(m.get("csrf_token") or ""))
open("/tmp/ctl_csrf.txt", "w").write(m.get("csrf_token") or "")
'

echo "[4] 用页面同一条接口发号"
T=$(cat /tmp/ctl_csrf.txt)
cat > "$BODY_FILE" <<JSON
{"tenant_name":"界面测试租户 ${SUFFIX}","account_email":"ui-${SUFFIX}@sample.local","account_name":"界面测试","apps":[{"app":"cowork","domain":"ui-cowork-${SUFFIX}.pearnly.com"},{"app":"dms","domain":"ui-dms-${SUFFIX}.pearnly.com"}]}
JSON
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BODY_FILE"
curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" -H "Content-Type: application/json" \
  -H "X-Frappe-CSRF-Token: $T" -X POST \
  "$CONTROL/api/method/pearnly_control.issues.api_issue" --data-binary "@$BODY_FILE" \
  | python3 -c '
import sys, json
d = json.load(sys.stdin)
m = d.get("message")
if not m:
    print("    失败:", str(d)[:200]); raise SystemExit(1)
print("    发号单:", m["issue"], "| 租户:", m["tenant"])
print("    账号:", m["account_email"], "| 一次性密码长度:", len(m["initial_password"]))
for dep in m["deployments"]:
    print("    待开通:", dep["app"], "->", dep["domain"])
'

echo "[5] 发号单列表（控制面能查到自己发的号）"
curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" \
  "$CONTROL/api/method/pearnly_control.issues.api_list_issues?limit=3" \
  | python3 -c '
import sys, json
for r in (json.load(sys.stdin).get("message") or []):
'
