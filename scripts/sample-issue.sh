#!/usr/bin/env bash
# 发号验证：控制面登录（平台 realm）-> 发号 -> 拿到开通清单与一次性初始密码
#           -> 用该账号到业务站点登录（证明账号真建在业务 realm）
#           -> 域名冲突应被拒
#
# 用法: scripts/sample-issue.sh <平台用户名> <平台密码> [租户名后缀]
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

PLATFORM_USER="${1:?用法: $0 <平台用户名> <平台密码> [租户名后缀]}"
PLATFORM_PASS="${2:?}"
SUFFIX="${3:-$(date +%s)}"

CONTROL_HOST=control.localhost;  CONTROL=http://127.0.0.1:8104
COWORK_HOST=cowork-a.localhost;  COWORK=http://127.0.0.1:8101

JAR=$(mktemp); BODY_FILE=$(mktemp)
trap 'rm -f "$JAR" "$BODY_FILE"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

# 登录：realm 用平台那条
login_platform() {
  local loc page action back
  loc=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $CONTROL_HOST" \
        "$CONTROL/api/method/pearnly_core.identity.begin_login" | loc_of)
  [ -n "$loc" ] || { echo "  [错误] 控制面拿不到登录跳转" >&2; exit 1; }
  page=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$loc" | to_host)")
  action=$(printf '%s' "$page" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  back=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$PLATFORM_USER" --data-urlencode "password=$PLATFORM_PASS" \
        --data-urlencode "credentialId=" "$(printf '%s' "$action" | to_host)" | loc_of)
  curl -sS -c "$JAR" -b "$JAR" -o /dev/null -H "Host: $CONTROL_HOST" "$back"
}

api() {
  local path="$1"
  cat > "$BODY_FILE"
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BODY_FILE" 2>/dev/null; then
    echo "  [错误] 请求体不是合法 JSON：" >&2; cat "$BODY_FILE" >&2; echo >&2; return 1
  fi
  curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" -H "Content-Type: application/json" \
    -H "X-Frappe-CSRF-Token: $T" -X POST "$CONTROL/api/method/$path" --data-binary "@$BODY_FILE"
}

echo "[1] 控制面登录（平台 realm）"
login_platform
WHO=$(curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/frappe.auth.get_logged_user")
echo "    身份: $WHO"

echo "[2] 平台账号不能进业务站点（跨 realm 隔离）"
BIZ=$(curl -sS -b "$JAR" -H "Host: $COWORK_HOST" "$COWORK/api/method/frappe.auth.get_logged_user" | head -c 90)
echo "    在 cowork-a 上的身份: $BIZ"

echo "[3] 发号：一次开三个产品，各自域名，B 档 + A 档 OCR"
T=$(curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/pearnly-password" | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)
RESULT=$(api "pearnly_control.issues.api_issue" <<JSON
{"tenant_name":"测试租户 ${SUFFIX}","account_email":"owner-${SUFFIX}@sample.local","account_name":"测试老板","contact_phone":"0800000000","ocr_economy":true,"ocr_enterprise":true,"apps":[{"app":"cowork","domain":"acme-cowork-${SUFFIX}.pearnly.com"},{"app":"erp","domain":"acme-erp-${SUFFIX}.pearnly.com"},{"app":"dms","domain":"acme-dms-${SUFFIX}.pearnly.com"}]}
JSON
)
echo "$RESULT" | python3 -c '
import sys, json
d = json.load(sys.stdin)
m = d.get("message")
if not m:
    print("    失败:", str(d)[:200]); raise SystemExit(1)
print("    发号单:", m["issue"], "| 租户:", m["tenant"])
print("    账号:", m["account_email"], "| 身份主体:", m["identity_subject"])
print("    初始密码（只出现一次）:", m["initial_password"])
print("    OCR:", m["ocr"])
for dep in m["deployments"]:
    print("    待开通:", dep["app"], "->", dep["domain"])
open("/tmp/issue_pwd.txt","w").write(m["initial_password"])
open("/tmp/issue_email.txt","w").write(m["account_email"])
'

echo "[4] 该账号能到业务站点登录（证明账号建在业务 realm）"
PWD_NEW=$(cat /tmp/issue_pwd.txt); EMAIL_NEW=$(cat /tmp/issue_email.txt)
JAR2=$(mktemp)
LOC2=$(curl -sS -c "$JAR2" -b "$JAR2" -o /dev/null -D - -H "Host: $COWORK_HOST" \
      "$COWORK/api/method/pearnly_core.identity.begin_login" | loc_of)
PAGE2=$(curl -sS -c "$JAR2" -b "$JAR2" -L -H "Host: keycloak:8080" "$(printf '%s' "$LOC2" | to_host)")
ACT2=$(printf '%s' "$PAGE2" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
BACK2=$(curl -sS -c "$JAR2" -b "$JAR2" -o /dev/null -D - -H "Host: keycloak:8080" \
  --data-urlencode "username=$EMAIL_NEW" --data-urlencode "password=$PWD_NEW" \
  --data-urlencode "credentialId=" "$(printf '%s' "$ACT2" | to_host)" | loc_of)
CB="$COWORK/api/method/pearnly_core.identity.callback"
case "$BACK2" in
  *"$CB"*)
    echo "    身份服务认证通过，已回跳本站回调"
    BODY=$(curl -sS -c "$JAR2" -b "$JAR2" -H "Host: $COWORK_HOST" "$BACK2")
    printf '    本站回调结果: %s\n' "$(printf '%s' "$BODY" | head -c 160)"
    echo "    会话是否建立: $(curl -sS -b "$JAR2" -H "Host: $COWORK_HOST" "$COWORK/api/method/frappe.auth.get_logged_user" | head -c 80)"
    ;;
  "")
    echo "    身份服务认证未通过（密码或账号有问题）"
    ;;
  *)
    echo "    未回跳本站回调，实际跳转: ${BACK2%%\?*}"
    ;;
esac
rm -f "$JAR2"

echo "[5] 同一域名再发一次（应被拒）"
api "pearnly_control.issues.api_issue" <<JSON | python3 -c 'import sys,json; d=json.load(sys.stdin); print("   ", str(d.get("exception") or d.get("message"))[:150])'
{"tenant_name":"撞域名 ${SUFFIX}","account_email":"clash-${SUFFIX}@sample.local","apps":[{"app":"cowork","domain":"acme-cowork-${SUFFIX}.pearnly.com"}]}
JSON

echo "[6] 同一个账号再发一次（应被拒）"
api "pearnly_control.issues.api_issue" <<JSON | python3 -c 'import sys,json; d=json.load(sys.stdin); print("   ", str(d.get("exception") or d.get("message"))[:150])'
{"tenant_name":"重复账号 ${SUFFIX}","account_email":"owner-${SUFFIX}@sample.local","apps":[{"app":"dms","domain":"other-${SUFFIX}.pearnly.com"}]}
JSON
