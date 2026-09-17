#!/usr/bin/env bash
# 会话登记与撤权验证：
#   1) 同一站点两个"设备"各自登录 -> 会话登记表应有两行，且互不踢
#   2) 从其中一个调用"退出全部" -> 两个会话都失效
#
# 用法: scripts/sample-session-revoke.sh <用户名> <密码>
set -euo pipefail

SITE_HOST=cowork-a.localhost
SITE=http://127.0.0.1:8101
KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

USERNAME="${1:?用法: $0 <用户名> <密码>}"
PASSWORD="${2:?用法: $0 <用户名> <密码>}"

JAR_A=$(mktemp); JAR_B=$(mktemp)
trap 'rm -f "$JAR_A" "$JAR_B"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

login() {  # $1 = cookie jar
  local jar="$1" loc page action back
  loc=$(curl -sS -c "$jar" -b "$jar" -o /dev/null -D - -H "Host: $SITE_HOST" \
        "$SITE/api/method/pearnly_core.identity.begin_login" | loc_of)
  page=$(curl -sS -c "$jar" -b "$jar" -L -H "Host: keycloak:8080" "$(printf '%s' "$loc" | to_host)")
  action=$(printf '%s' "$page" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  back=$(curl -sS -c "$jar" -b "$jar" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" \
        --data-urlencode "credentialId=" "$(printf '%s' "$action" | to_host)" | loc_of)
  curl -sS -c "$jar" -b "$jar" -o /dev/null -H "Host: $SITE_HOST" "$back"
}

who() { curl -sS -b "$1" -H "Host: $SITE_HOST" "$SITE/api/method/frappe.auth.get_logged_user" | head -c 90; }

echo "[1] 设备 A 登录"
login "$JAR_A"; echo "    A: $(who "$JAR_A")"
echo "[2] 设备 B 登录"
login "$JAR_B"; echo "    B: $(who "$JAR_B")"

echo "[3] 会话登记表（当前账号的活跃会话）"
curl -sS -b "$JAR_A" -H "Host: $SITE_HOST" \
  "$SITE/api/method/pearnly_core.sessions.my_sessions" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); m=d.get("message") or []; print("    活跃会话数:", len(m)); [print("     -", r["deployment"], (r.get("user_agent") or "")[:38]) for r in m]'

echo "[4] 确认 B 仍然有效（A 的登录没有踢掉 B）"
echo "    B: $(who "$JAR_B")"

echo "[5] 从 A 调用退出全部"
CSRF=$(curl -sS -b "$JAR_A" -H "Host: $SITE_HOST" "$SITE/pearnly-password" \
       | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)
curl -sS -b "$JAR_A" -H "Host: $SITE_HOST" -H "Content-Type: application/json" \
     -H "X-Frappe-CSRF-Token: $CSRF" -X POST \
     "$SITE/api/method/pearnly_core.sessions.logout_everywhere" -d '{}' | head -c 200
echo

echo "[6] 两个会话现在都应失效"
echo "    A: $(who "$JAR_A")"
echo "    B: $(who "$JAR_B")"
