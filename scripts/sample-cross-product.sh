#!/usr/bin/env bash
# 跨产品验证：同一账号同时登录两个产品站点，互不影响。
#
# 用法: scripts/sample-cross-product.sh <用户名> <密码>
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080
KCID=pearnly-business

USERNAME="${1:?用法: $0 <用户名> <密码>}"
PASSWORD="${2:?用法: $0 <用户名> <密码>}"

COWORK_HOST=cowork-a.localhost; COWORK=http://127.0.0.1:8101
ERP_HOST=erp-a.localhost;       ERP=http://127.0.0.1:8102

JAR_C=$(mktemp); JAR_E=$(mktemp)
trap 'rm -f "$JAR_C" "$JAR_E"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

login() {  # jar host base
  local jar="$1" host="$2" base="$3" loc page action back
  loc=$(curl -sS -c "$jar" -b "$jar" -o /dev/null -D - -H "Host: $host" \
        "$base/api/method/pearnly_core.identity.begin_login" | loc_of)
  page=$(curl -sS -c "$jar" -b "$jar" -L -H "Host: keycloak:8080" "$(printf '%s' "$loc" | to_host)")
  action=$(printf '%s' "$page" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  back=$(curl -sS -c "$jar" -b "$jar" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" \
        --data-urlencode "credentialId=" "$(printf '%s' "$action" | to_host)" | loc_of)
  curl -sS -c "$jar" -b "$jar" -o /dev/null -H "Host: $host" "$back"
}

who() { curl -sS -b "$1" -H "Host: $2" "$3/api/method/frappe.auth.get_logged_user" | head -c 80; }

echo "[1] 在 Cowork 站点登录"
login "$JAR_C" "$COWORK_HOST" "$COWORK"
echo "    cowork-a: $(who "$JAR_C" "$COWORK_HOST" "$COWORK")"

echo "[2] 同一个身份，再在 ERP 站点登录"
login "$JAR_E" "$ERP_HOST" "$ERP"
echo "    erp-a:    $(who "$JAR_E" "$ERP_HOST" "$ERP")"

echo "[3] 两个会话应同时有效（互不踢）"
echo "    cowork-a: $(who "$JAR_C" "$COWORK_HOST" "$COWORK")"
echo "    erp-a:    $(who "$JAR_E" "$ERP_HOST" "$ERP")"

echo "[4] 各自的会话登记表"
for pair in "$JAR_C|$COWORK_HOST|$COWORK|cowork-a" "$JAR_E|$ERP_HOST|$ERP|erp-a"; do
  IFS='|' read -r jar host base label <<< "$pair"
  curl -sS -b "$jar" -H "Host: $host" "$base/api/method/pearnly_core.sessions.my_sessions" \
    | python3 -c "import sys,json;d=json.load(sys.stdin);m=d.get('message') or [];print('    $label 登记会话数:', len(m), [r['deployment'] for r in m])"
done

echo "[5] 在 Cowork 站点调用退出全部，只应影响 Cowork"
CSRF=$(curl -sS -b "$JAR_C" -H "Host: $COWORK_HOST" "$COWORK/pearnly-password" \
       | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)
curl -sS -b "$JAR_C" -H "Host: $COWORK_HOST" -H "Content-Type: application/json" \
     -H "X-Frappe-CSRF-Token: $CSRF" -X POST \
     "$COWORK/api/method/pearnly_core.sessions.logout_everywhere" -d '{}' | head -c 120
echo
echo "    Cowork 现在: $(who "$JAR_C" "$COWORK_HOST" "$COWORK")"
echo "    ERP 现在:    $(who "$JAR_E" "$ERP_HOST" "$ERP")"
