#!/usr/bin/env bash
# Cowork 纵向切片验证：录入 -> 未确认不许推送 -> 确认 -> 排队 -> 列表 -> 汇总 -> 去重
#
# 用法: scripts/sample-cowork-slice.sh <用户名> <密码>
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

USERNAME="${1:?用法: $0 <用户名> <密码>}"
PASSWORD="${2:?用法: $0 <用户名> <密码>}"

COWORK_HOST=cowork-a.localhost
COWORK=http://127.0.0.1:8101

JAR=$(mktemp); BODY_FILE=$(mktemp)
trap 'rm -f "$JAR" "$BODY_FILE"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

login() {
  local loc page action back
  loc=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $COWORK_HOST" \
        "$COWORK/api/method/pearnly_core.identity.begin_login" | loc_of)
  page=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$loc" | to_host)")
  action=$(printf '%s' "$page" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  back=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" \
        --data-urlencode "credentialId=" "$(printf '%s' "$action" | to_host)" | loc_of)
  curl -sS -c "$JAR" -b "$JAR" -o /dev/null -H "Host: $COWORK_HOST" "$back"
}

BASE="$COWORK/api/method/pearnly_core"
# 请求体走 stdin（heredoc），不再经 shell 转义拼字符串——那样极易多出或少掉
# 一个花括号，而服务端只会回一句看不见细节的 "Invalid request body"。
# 发出前先在本地校验一次 JSON。
api() {
  local path="$1"
  cat > "$BODY_FILE"
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BODY_FILE" 2>/dev/null; then
    echo "  [错误] 请求体不是合法 JSON，内容：" >&2
    cat "$BODY_FILE" >&2
    echo >&2
    return 1
  fi
  curl -sS -b "$JAR" -H "Host: $COWORK_HOST" -H "Content-Type: application/json" \
    -H "X-Frappe-CSRF-Token: $T" -X POST "$COWORK/api/method/$path" \
    --data-binary "@$BODY_FILE"
}
brief() {
  python3 -c '
import sys, json
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    print("    原始响应:", raw[:300]); raise SystemExit
msg = d.get("exception") or d.get("exc_type") or json.dumps(d.get("message", d), ensure_ascii=False)
print("   ", str(msg)[:160])
if d.get("exc_type") and not d.get("exception"):
    print("    服务器消息:", str(d.get("_server_messages"))[:220])
'
}

echo "[1] 登录 cowork-a"
login
echo "    身份: $(curl -sS -b "$JAR" -H "Host: $COWORK_HOST" "$COWORK/api/method/frappe.auth.get_logged_user" | head -c 60)"
T=$(curl -sS -b "$JAR" -H "Host: $COWORK_HOST" "$COWORK/pearnly-password" | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)

RUN_KEY="$(date +%s)"
echo "[2] 录一张进项票（识别完成，尚未确认）"
api "pearnly_cowork.intake.api_create_intake" <<JSON | brief
{"direction":"purchase","source":"line","doc_no":"IV-${RUN_KEY}","doc_date":"2026-09-17","counterparty_name":"清迈纸业","counterparty_tax_id":"0505559999999","company_tax_id":"0105551234567","net_amount":1000,"vat_amount":70,"recognition_raw":"{\"engine\":\"sample\"}","status":"recognized","dedupe_key":"iv-${RUN_KEY}"}
JSON

NAME=$(curl -sS -b "$JAR" -H "Host: $COWORK_HOST" \
  "$COWORK/api/method/pearnly_cowork.intake.api_list_intake?limit=1" \
  | python3 -c 'import sys,json; print((json.load(sys.stdin).get("message") or [{}])[0].get("name",""))')
echo "    记录编号: $NAME"

echo "[3] 未确认就排队推送（应被拒）"
api "pearnly_cowork.intake.api_queue_for_push" <<JSON | brief
{"name":"${NAME}","channel":"companion"}
JSON

echo "[4] 人工确认"
api "pearnly_cowork.intake.api_confirm_intake" <<JSON | brief
{"name":"${NAME}","counterparty_name":"清迈纸业有限公司","category":"办公耗材"}
JSON

echo "[5] 排队走小助手通道"
api "pearnly_cowork.intake.api_queue_for_push" <<JSON | brief
{"name":"${NAME}","channel":"companion"}
JSON

echo "[6] 列表"
curl -sS -b "$JAR" -H "Host: $COWORK_HOST" \
  "$COWORK/api/method/pearnly_cowork.intake.api_list_intake?limit=5" \
  | python3 -c '
import sys, json
for r in (json.load(sys.stdin).get("message") or []):
    print("   ", r["name"], r["direction"], r["status"], r["counterparty_name"], r["grand_total"], "渠道", r["push_channel"])
'

echo "[7] 汇总"
curl -sS -b "$JAR" -H "Host: $COWORK_HOST" \
  "$COWORK/api/method/pearnly_cowork.intake.api_summary" | head -c 260; echo

echo "[8] 同一张票再录一次（应被去重拦下）"
api "pearnly_cowork.intake.api_create_intake" <<JSON | brief
{"direction":"purchase","doc_no":"IV-${RUN_KEY}","net_amount":1,"dedupe_key":"iv-${RUN_KEY}"}
JSON
