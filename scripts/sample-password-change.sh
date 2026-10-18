#!/usr/bin/env bash
# 改密功能验证：先走一遍 OIDC 登录拿会话，再测改密的三种情况。
#
# 用法: scripts/sample-password-change.sh <Keycloak用户名> <当前密码> <新密码> [站点host] [端口]
#   默认打在样板站 cowork-a.localhost:8101；要验真租户站就把站点和端口传进来，
#   例如: ... prov-65956@sample.local Tenant-Test-2026 abc123 qc65956.localhost 8101
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

USERNAME="${1:?用法: $0 <用户名> <当前密码> <新密码>}"
OLD_PASS="${2:?}"
NEW_PASS="${3:?}"
SITE_HOST="${4:-cowork-a.localhost}"
SITE="http://127.0.0.1:${5:-8101}"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

echo "[1] OIDC 登录建立会话（用当前密码）"
LOC=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $SITE_HOST" \
        "$SITE/api/method/pearnly_core.identity.begin_login" | loc_of)
PAGE=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$LOC" | to_host)")
ACTION=$(printf '%s' "$PAGE" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
BACK=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$OLD_PASS" \
        --data-urlencode "credentialId=" "$(printf '%s' "$ACTION" | to_host)" | loc_of)
curl -sS -c "$JAR" -b "$JAR" -o /dev/null -H "Host: $SITE_HOST" "$BACK"
WHO=$(curl -sS -b "$JAR" -H "Host: $SITE_HOST" "$SITE/api/method/frappe.auth.get_logged_user")
echo "    当前身份: $WHO"
case "$WHO" in *"$USERNAME"*|*sample.local*) ;; *) echo "    登录未成功，终止"; exit 1 ;; esac

# Frappe 的 frappe.sessions.get_csrf_token 没有开放成 API，所以从我们自己那个
# 改密页面里取（页面把 csrf token 渲染进 JS）。这同时验证了页面能渲染。
echo "[2] 打开改密页并取 CSRF"
PAGE_HTML=$(curl -sS -b "$JAR" -H "Host: $SITE_HOST" "$SITE/pearnly-password")
CSRF=$(printf '%s' "$PAGE_HTML" | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)
if [ -z "$CSRF" ]; then
  echo "    失败：页面里没取到 CSRF（页面是否渲染？）"
  printf '%s' "$PAGE_HTML" | head -c 300
  exit 1
fi
printf '%s' "$PAGE_HTML" | grep -q 'id="pw-submit"' && echo "    页面渲染正常，表单字段齐全"

call_change() {
  curl -sS -b "$JAR" -H "Host: $SITE_HOST" \
    -H "Content-Type: application/json" -H "X-Frappe-CSRF-Token: $CSRF" \
    -X POST "$SITE/api/method/pearnly_core.password.change_password" \
    -d "{\"current_password\":\"$1\",\"new_password\":\"$2\",\"confirm_password\":\"$3\"}"
}

echo "[3] 当前密码填错 → 应被拒"
call_change "definitely-wrong" "$NEW_PASS" "$NEW_PASS" | head -c 200; echo

echo "[4] 两次新密码不一致 → 应被拒"
call_change "$OLD_PASS" "$NEW_PASS" "mismatch-123" | head -c 200; echo

echo "[5] 正确改密 → 应成功"
call_change "$OLD_PASS" "$NEW_PASS" "$NEW_PASS" | head -c 200; echo

echo "[6] 用新密码重新走一遍 OIDC 登录 → 应成功"
NJAR=$(mktemp)
LOC2=$(curl -sS -c "$NJAR" -b "$NJAR" -o /dev/null -D - -H "Host: $SITE_HOST" \
        "$SITE/api/method/pearnly_core.identity.begin_login" | loc_of)
PAGE2=$(curl -sS -c "$NJAR" -b "$NJAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$LOC2" | to_host)")
ACTION2=$(printf '%s' "$PAGE2" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
BACK2=$(curl -sS -c "$NJAR" -b "$NJAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$NEW_PASS" \
        --data-urlencode "credentialId=" "$(printf '%s' "$ACTION2" | to_host)" | loc_of)
curl -sS -c "$NJAR" -b "$NJAR" -o /dev/null -H "Host: $SITE_HOST" "$BACK2"
WHO2=$(curl -sS -b "$NJAR" -H "Host: $SITE_HOST" "$SITE/api/method/frappe.auth.get_logged_user")
echo "    新密码登录结果: $WHO2"
rm -f "$NJAR"
