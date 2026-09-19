#!/usr/bin/env bash
# DMS 纵向切片验证：上传 -> 未匹配不许确认 -> 匹配（新建）-> 确认 -> 推送
#                     -> 同一证件号再录 -> 匹配到已有客户（不重复建档）
#
# 用法: scripts/sample-dms-slice.sh <用户名> <密码>
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

USERNAME="${1:?用法: $0 <用户名> <密码>}"
PASSWORD="${2:?用法: $0 <用户名> <密码>}"

DMS_HOST=dms-a.localhost
DMS=http://127.0.0.1:8103

JAR=$(mktemp); BODY_FILE=$(mktemp)
trap 'rm -f "$JAR" "$BODY_FILE"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

login() {
  local loc page action back
  loc=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: $DMS_HOST" \
        "$DMS/api/method/pearnly_core.identity.begin_login" | loc_of)
  page=$(curl -sS -c "$JAR" -b "$JAR" -L -H "Host: keycloak:8080" "$(printf '%s' "$loc" | to_host)")
  action=$(printf '%s' "$page" | grep -o 'action="[^"]*"' | head -1 | sed 's/action="//; s/"$//; s/&amp;/\&/g')
  back=$(curl -sS -c "$JAR" -b "$JAR" -o /dev/null -D - -H "Host: keycloak:8080" \
        --data-urlencode "username=$USERNAME" --data-urlencode "password=$PASSWORD" \
        --data-urlencode "credentialId=" "$(printf '%s' "$action" | to_host)" | loc_of)
  curl -sS -c "$JAR" -b "$JAR" -o /dev/null -H "Host: $DMS_HOST" "$back"
}

# 请求体走 heredoc，发出前本地校验 JSON（见 Cowork 切片里踩过的坑）
api() {
  local path="$1"
  cat > "$BODY_FILE"
  if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BODY_FILE" 2>/dev/null; then
    echo "  [错误] 请求体不是合法 JSON：" >&2; cat "$BODY_FILE" >&2; echo >&2; return 1
  fi
  curl -sS -b "$JAR" -H "Host: $DMS_HOST" -H "Content-Type: application/json" \
    -H "X-Frappe-CSRF-Token: $T" -X POST "$DMS/api/method/$path" \
    --data-binary "@$BODY_FILE"
}
brief() {
  python3 -c '
import sys, json
raw = sys.stdin.read()
try:
    d = json.loads(raw)
except Exception:
    print("    原始响应:", raw[:200]); raise SystemExit
msg = d.get("exception") or d.get("exc_type") or json.dumps(d.get("message", d), ensure_ascii=False)
print("   ", str(msg)[:150])
'
}

echo "[1] 登录 dms-a"
login
echo "    身份: $(curl -sS -b "$JAR" -H "Host: $DMS_HOST" "$DMS/api/method/frappe.auth.get_logged_user" | head -c 60)"
T=$(curl -sS -b "$JAR" -H "Host: $DMS_HOST" "$DMS/pearnly-password" | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1)

ID="1101700000001"
RUN_KEY="$(date +%s)"

echo "[2] 上传一张证件（识别完成，未匹配）"
api "pearnly_dms.intake.id_card.api_create_intake" <<JSON | brief
{"id_number":"${ID}","full_name":"สมชาย ใจดี","birth_date":"1990-05-12","address":"ระยอง","endpoint":"mrerp-dms-a","status":"recognized","recognition_raw":"{\"engine\":\"sample\"}"}
JSON

NAME=$(curl -sS -b "$JAR" -H "Host: $DMS_HOST" \
  "$DMS/api/method/pearnly_dms.intake.id_card.api_list_intake?limit=1" \
  | python3 -c 'import sys,json; print((json.load(sys.stdin).get("message") or [{}])[0].get("name",""))')
echo "    记录编号: $NAME"

echo "[3] 未匹配就想确认（应被拒）"
api "pearnly_dms.intake.id_card.api_confirm_intake" <<JSON | brief
{"name":"${NAME}"}
JSON

echo "[4] 客户匹配：匹配不到、给出 DMS 引用 → 记为新建客户"
api "pearnly_dms.intake.id_card.api_match_customer" <<JSON | brief
{"name":"${NAME}","external_customer_ref":"DMS-CUST-9001"}
JSON

echo "[5] 资料确认"
api "pearnly_dms.intake.id_card.api_confirm_intake" <<JSON | brief
{"name":"${NAME}","full_name":"สมชาย ใจดี (ยืนยันแล้ว)"}
JSON

echo "[6] 推送到 DMS"
api "pearnly_dms.intake.id_card.api_queue_push" <<JSON | brief
{"name":"${NAME}"}
JSON

echo "[7] 列表（证件号不应出现在返回里）"
curl -sS -b "$JAR" -H "Host: $DMS_HOST" \
  "$DMS/api/method/pearnly_dms.intake.id_card.api_list_intake?limit=3" \
  | python3 -c '
import sys, json
rows = json.load(sys.stdin).get("message") or []
for r in rows:
    print("   ", r["name"], r["status"], r["match_result"], r.get("full_name"), "证件号字段:", "id_number" in r)
'

echo "[8] 同一个证件号再录一条 → 应匹配到已有客户，不重复建档"
api "pearnly_dms.intake.id_card.api_create_intake" <<JSON | brief
{"id_number":"${ID}","full_name":"สมชาย ใจดี","endpoint":"mrerp-dms-a","status":"recognized"}
JSON
NAME2=$(curl -sS -b "$JAR" -H "Host: $DMS_HOST" \
  "$DMS/api/method/pearnly_dms.intake.id_card.api_list_intake?limit=1" \
  | python3 -c 'import sys,json; print((json.load(sys.stdin).get("message") or [{}])[0].get("name",""))')
api "pearnly_dms.intake.id_card.api_match_customer" <<JSON | brief
{"name":"${NAME2}"}
JSON

echo "[9] 汇总（新建客户数应只算一次）"
curl -sS -b "$JAR" -H "Host: $DMS_HOST" "$DMS/api/method/pearnly_dms.intake.id_card.api_summary" | head -c 260; echo
