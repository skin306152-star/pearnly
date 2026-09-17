#!/usr/bin/env bash
# 跨 realm 验证：平台 realm 的账号不能登入业务站点。
#
# 做法：先从站点拿到一次合法的 state / nonce / PKCE 挑战，然后把授权请求
# 指向**平台 realm** 而不是业务 realm，用平台账号登录，把拿到的授权码交回
# 站点回调。站点只认业务 realm（发现文档、令牌端点都取自它），所以这一步
# 必然失败——失败本身就是我们要的证据。
#
# 用法: scripts/sample-oidc-cross-realm.sh <平台用户名> <密码>
set -euo pipefail

SITE_HOST=cowork-a.localhost
SITE=http://127.0.0.1:8101
KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080
PLATFORM_REALM=pearnly-platform

KC_USER="${1:?用法: $0 <用户名> <密码>}"
KC_PASS="${2:?用法: $0 <用户名> <密码>}"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }
param() { printf '%s' "$1" | sed -n "s/.*[?&]$2=\([^&]*\).*/\1/p"; }

echo "[1] 站点发起登录，取到本次的 state / nonce / PKCE 挑战"
LOC=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - \
        -H "Host: $SITE_HOST" \
        "$SITE/api/method/pearnly_core.identity.begin_login" | loc_of)
STATE=$(param "$LOC" state)
NONCE=$(param "$LOC" nonce)
CHALLENGE=$(param "$LOC" code_challenge)
echo "    state=${STATE:0:12}… 已取到"

echo "[2] 把授权请求指向**平台 realm**，用平台账号登录"
AUTH="$KC_PUBLIC/realms/$PLATFORM_REALM/protocol/openid-connect/auth?client_id=cowork-a&redirect_uri=$SITE/api/method/pearnly_core.identity.callback&response_type=code&scope=openid%20profile%20email&state=$STATE&nonce=$NONCE&code_challenge=$CHALLENGE&code_challenge_method=S256"
PAGE=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$AUTH")
ACTION=$(printf '%s' "$PAGE" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
[ -n "$ACTION" ] || { echo "    失败：没解析到平台 realm 的登录表单"; exit 1; }

BACK=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$KC_USER" \
        --data-urlencode "password=$KC_PASS" \
        --data-urlencode "credentialId=" \
        "$(printf '%s' "$ACTION" | to_host)" | loc_of)
echo "    平台 realm 回跳: ${BACK%%\?*}"

case "$BACK" in
  *"$SITE/api/method/pearnly_core.identity.callback"*) ;;
  *) echo "    平台 realm 未回跳到本站回调，链路中断"; exit 0 ;;
esac

echo "[3] 把平台 realm 的授权码交回站点回调（站点只认业务 realm）"
BODY=$(curl -sS -b "$JAR" -H "Host: $SITE_HOST" "$BACK")
printf '%s' "$BODY" | head -c 400
echo
echo "[4] 检查会话是否被建立"
ME=$(curl -sS -b "$JAR" -H "Host: $SITE_HOST" "$SITE/api/method/frappe.auth.get_logged_user")
case "$ME" in
  *"platform-admin"*) echo "    ✗ 危险：平台账号竟然进了业务站点"; exit 1 ;;
  *"tenant-admin"*)   echo "    ✗ 危险：会话被错误建立"; exit 1 ;;
  *) echo "    ✓ 未建立会话（预期）：$ME" ;;
esac
