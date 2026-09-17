#!/usr/bin/env bash
# ERP 纵向切片验证：登录 -> 建采购单 -> 查回 -> 汇总 -> 去重 -> 跨站点隔离
#
# 用法: scripts/sample-erp-slice.sh <用户名> <密码>
set -euo pipefail

KC_PUBLIC=http://127.0.0.1:8105
KC_INTERNAL=http://keycloak:8080

USERNAME="${1:?用法: $0 <用户名> <密码>}"
PASSWORD="${2:?用法: $0 <用户名> <密码>}"

ERP_HOST=erp-a.localhost;  ERP=http://127.0.0.1:8102
ERP2_HOST=erp-b.localhost; ERP2=http://127.0.0.1:8102

JAR=$(mktemp); JAR2=$(mktemp)
trap 'rm -f "$JAR" "$JAR2"' EXIT

loc_of() { awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1; }
to_host() { sed "s|$KC_INTERNAL|$KC_PUBLIC|g"; }

login() {
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

csrf() { curl -sS -b "$1" -H "Host: $2" "$3/pearnly-password" \
         | sed -n 's/.*var CSRF = "\([^"]*\)".*/\1/p' | head -1; }

echo "[1] 登录 erp-a"
login "$JAR" "$ERP_HOST" "$ERP"
echo "    身份: $(curl -sS -b "$JAR" -H "Host: $ERP_HOST" "$ERP/api/method/frappe.auth.get_logged_user" | head -c 60)"
T=$(csrf "$JAR" "$ERP_HOST" "$ERP")

echo "[2] 建一张含税采购单（两行，带整单折扣与预扣税）"
# 去重键带时间戳，脚本可重复执行；同一张票重复上传的场景在 [5] 单独验
RUN_KEY="$(date +%s)"
BODY=$(printf '{"supplier_name":"曼谷五金有限公司","supplier_tax_id":"0105551234567","doc_no":"INV-%s","doc_date":"2026-09-17","due_date":"2026-10-17","currency":"THB","has_vat":true,"discount_total":50,"wht_amount":30,"dedupe_key":"inv-%s","lines":[{"description":"不锈钢螺丝 M6","qty":100,"unit":"个","unit_price":2.5,"vat_rate":7},{"description":"安装服务","item_type":"service","qty":1,"unit":"次","unit_price":800,"vat_rate":7}]}' "$RUN_KEY" "$RUN_KEY")
curl -sS -b "$JAR" -H "Host: $ERP_HOST" -H "Content-Type: application/json" \
     -H "X-Frappe-CSRF-Token: $T" -X POST \
     "$ERP/api/method/pearnly_erp_portal.purchase.api_create_purchase" -d "$BODY" | head -c 200; echo

echo "[3] 查回该单（看金额是否被重算）"
curl -sS -b "$JAR" -H "Host: $ERP_HOST" \
  "$ERP/api/method/pearnly_erp_portal.purchase.api_list_purchases?limit=5" \
  | python3 -c '
import sys, json
rows = json.load(sys.stdin).get("message") or []
for r in rows:
    print("   ", r["name"], r["supplier_name"], r["doc_no"], "含税合计", r["grand_total"], r["currency"], r["payment_status"])
'

echo "[4] 汇总"
curl -sS -b "$JAR" -H "Host: $ERP_HOST" \
  "$ERP/api/method/pearnly_erp_portal.purchase.api_summary" | head -c 260; echo

echo "[5] 同一张票再传一次（应被去重拦下）"
curl -sS -b "$JAR" -H "Host: $ERP_HOST" -H "Content-Type: application/json" \
     -H "X-Frappe-CSRF-Token: $T" -X POST \
     "$ERP/api/method/pearnly_erp_portal.purchase.api_create_purchase" -d "$BODY" \
     | python3 -c 'import sys,json; d=json.load(sys.stdin); print("   ", (d.get("_server_messages") or d.get("exception") or d)[:120])'

echo "[6] 另一个租户站点 erp-b 应看不到这张单（跨部署隔离）"
login "$JAR2" "$ERP2_HOST" "$ERP2"
curl -sS -b "$JAR2" -H "Host: $ERP2_HOST" \
  "$ERP2/api/method/pearnly_erp_portal.purchase.api_list_purchases?limit=5" \
  | python3 -c 'import sys,json; rows=json.load(sys.stdin).get("message") or []; print("    erp-b 看到的单数:", len(rows))'
