#!/usr/bin/env bash
# OIDC 端到端验证：站点发起 → 身份服务登录 → 回调 → 建立会话。
#
# 用法: scripts/sample-oidc-e2e.sh <Keycloak用户名> <密码>
#
# 为什么需要重写地址：Keycloak 的签发方固定为 http://keycloak:8080（容器内可达），
# 而宿主机是通过 127.0.0.1:8105 访问它的。所以脚本把返回的 URL 换成宿主机地址，
# 但请求里始终带 Host: keycloak:8080，让 Keycloak 认为自己在被按配置的域名访问。
set -euo pipefail

SITE_HOST=cowork-a.localhost
SITE=http://127.0.0.1:8101
KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

KC_USER="${1:?用法: $0 <用户名> <密码>}"
KC_PASS="${2:?用法: $0 <用户名> <密码>}"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }
location_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }

echo "[1] 站点发起登录"
LOC=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - \
        -H "Host: $SITE_HOST" \
        "$SITE/api/method/pearnly_core.identity.begin_login" | location_of)
echo "    重定向到: ${LOC%%\?*}"

echo "[2] 打开身份服务登录页"
PAGE=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" \
        "$(printf '%s' "$LOC" | to_host)")
ACTION=$(printf '%s' "$PAGE" | grep -o 'action="[^"]*"' | head -1 \
        | sed 's/action="//; s/"$//; s/&amp;/\&/g')
if [ -z "$ACTION" ]; then
  echo "    失败：没解析到登录表单"
  exit 1
fi

echo "[3] 提交账号密码"
FINAL=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - \
        -H "Host: keycloak:8080" \
        --data-urlencode "username=$KC_USER" \
        --data-urlencode "password=$KC_PASS" \
        --data-urlencode "credentialId=" \
        "$(printf '%s' "$ACTION" | to_host)" | location_of)
echo "    身份服务回跳: ${FINAL%%\?*}"

CALLBACK=$(printf '%s' "$FINAL" | sed "s|$SITE|$SITE|g")
if [ -z "$CALLBACK" ]; then
  echo "    失败：身份服务没有回跳"
  exit 1
fi

echo "[4] 回到站点回调（走本地会话，不经身份服务）"
CB_CODE=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $SITE_HOST" "$CALLBACK" | location_of)
echo "    回调后跳转: ${CB_CODE:-（无）}"

echo "[5] 用建立的会话查当前用户"
ME=$(curl -sS -b "$JAR" -H "Host: $SITE_HOST" "$SITE/api/method/frappe.auth.get_logged_user")
echo "    $ME"
