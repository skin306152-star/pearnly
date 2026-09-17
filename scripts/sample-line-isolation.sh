#!/usr/bin/env bash
# LINE 每租户隔离验证（新模型的核心要求）。
#
# 验证四件事：
#   1. 用**本租户自己的**密钥签名 → 收下（200 ok:true）
#   2. 用**别的租户的**密钥签名 → 拒收（400 bad_signature）
#      —— 这条就是"每租户一条 LINE"的实际意义：凭据不同，别人的事件进不来
#   3. 绑定流程：生成一次性码 → 发给官方号 → 建绑定；同一个码不能用第二次
#   4. 幂等：同一条事件重投不会被处理第二遍
#
# 用法: scripts/sample-line-isolation.sh <站点域名> <端口> <运行组> <本租户密钥> [别的租户密钥]
set -euo pipefail

SITE_HOST="${1:?用法: $0 <站点域名> <端口> <运行组> <本租户密钥> [别租户密钥]}"
PORT="${2:?}"
GROUP="${3:?}"
SECRET="${4:?}"
OTHER_SECRET="${5:-test-secret-cowork-999}"

SITE="http://127.0.0.1:${PORT}"
COMPOSE=infrastructure/compose/01-bench.yml
CTX=colima-pearnly
FAILED=0

sign() {  # sign <secret> <body>  -> base64(HMAC-SHA256)
  python3 -c '
import base64, hashlib, hmac, sys
secret, body = sys.argv[1], sys.argv[2]
print(base64.b64encode(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()).decode())
' "$1" "$2"
}

post() {  # post <signature> <body> -> "HTTP/正文"
  local sig="$1" body="$2"
  curl -sS -o /tmp/line_resp.txt -w '%{http_code}' -X POST \
    -H "Host: ${SITE_HOST}" -H "Content-Type: application/json" \
    -H "X-Line-Signature: $sig" \
    --data-binary "$body" "$SITE/api/method/pearnly_core.line.webhook" \
    | tr -d '\n'
  echo -n " / "
  cat /tmp/line_resp.txt
  echo
}

event_json() {  # event_json <event_id> <user_id> [text]
  python3 -c '
import json, sys
event_id, user_id = sys.argv[1], sys.argv[2]
text = sys.argv[3] if len(sys.argv) > 3 else None
ev = {
    "type": "message" if text is not None else "follow",
    "replyToken": "dummy-reply-token",
    "source": {"type": "user", "userId": user_id},
    "timestamp": 1789666000000,
    "mode": "active",
    "webhookEventId": event_id,
    "deliveryContext": {"isRedelivery": False},
}
if text is not None:
    ev["message"] = {"type": "text", "id": "msg-1", "text": text}
print(json.dumps({"destination": "test", "events": [ev]}))
' "$@"
}

echo "=== 站点 ${SITE_HOST}:${PORT}（组 ${GROUP}）==="

echo "[1] 用本租户自己的密钥签名 → 应 200 且 ok:true"
BODY=$(event_json "evt-own-$(date +%s)" "U-own-$(date +%s)")
OUT=$(post "$(sign "$SECRET" "$BODY")" "$BODY")
echo "    $OUT"
case "$OUT" in
  200*) echo "    ✓ 收下" ;;
  *) echo "    ✗ 本该收下却被拒"; FAILED=1 ;;
esac

echo "[2] 用别的租户的密钥签名 → 应 400（隔离边界）"
BODY2=$(event_json "evt-other-$(date +%s)" "U-other-$(date +%s)")
OUT2=$(post "$(sign "$OTHER_SECRET" "$BODY2")" "$BODY2")
echo "    $OUT2"
case "$OUT2" in
  400*) echo "    ✓ 拒收（别人的事件进不来）" ;;
  *) echo "    ✗ 竟然收下了 —— 隔离没生效"; FAILED=1 ;;
esac

echo "[3] 幂等：同一条事件重投，不应被处理第二遍"
EID="evt-dup-$(date +%s)"
BODY3=$(event_json "$EID" "U-dup-$(date +%s)")
post "$(sign "$SECRET" "$BODY3")" "$BODY3" >/dev/null
post "$(sign "$SECRET" "$BODY3")" "$BODY3" >/dev/null
CNT=$(docker --context "$CTX" compose -f "$COMPOSE" exec -T "$GROUP-bench" \
      bench --site "$SITE_HOST" execute frappe.db.count \
      --args "[\"Pearnly LINE Event\", {\"event_id\": \"$EID\"}]" 2>/dev/null | tail -1)
echo "    该事件在库里的条数: ${CNT}（应为 1）"
[ "$(printf '%s' "$CNT" | tr -d '\"')" = "1" ] && echo "    ✓ 只登记一次" || { echo "    ✗ 重复登记"; FAILED=1; }

echo "[4] 绑定流程：生成码 → 发码 → 建绑定 → 同码再用应失败"
USER_ID="U-bind-$(date +%s)"
CODE=$(docker --context "$CTX" compose -f "$COMPOSE" exec -T "$GROUP-bench" \
       bench --site "$SITE_HOST" execute pearnly_core.line_binding.generate_bind_code \
       --kwargs '{"user": "Administrator"}' 2>/dev/null \
       | python3 -c 'import sys,json; print(json.loads(sys.stdin.read().strip().splitlines()[-1])["code"])')
echo "    生成的绑定码: $CODE"

BODY4=$(event_json "evt-bind-$(date +%s)" "$USER_ID" "$CODE")
post "$(sign "$SECRET" "$BODY4")" "$BODY4" >/dev/null
BOUND=$(docker --context "$CTX" compose -f "$COMPOSE" exec -T "$GROUP-bench" \
        bench --site "$SITE_HOST" execute frappe.db.get_value \
        --args "[\"Pearnly LINE Binding\", {\"line_user_id\": \"$USER_ID\"}, \"user\"]" 2>/dev/null | tail -1)
echo "    该 LINE 用户绑定到的账号: $BOUND"
[ "$(printf '%s' "$BOUND" | tr -d '\"')" = "Administrator" ] && echo "    ✓ 绑定成功" || { echo "    ✗ 绑定失败"; FAILED=1; }

BODY5=$(event_json "evt-bind2-$(date +%s)" "U-other2-$(date +%s)" "$CODE")
post "$(sign "$SECRET" "$BODY5")" "$BODY5" >/dev/null
BOUND2=$(docker --context "$CTX" compose -f "$COMPOSE" exec -T "$GROUP-bench" \
         bench --site "$SITE_HOST" execute frappe.db.get_value \
         --args "[\"Pearnly LINE Binding\", {\"line_user_id\": \"$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["events"][0]["source"]["userId"])' "$BODY5")\"}, \"user\"]" 2>/dev/null | tail -1)
echo "    用同一个码再绑一次的账号: ${BOUND2:-（无）}"
[ -z "$(printf '%s' "$BOUND2" | tr -d '\"')" ] || [ "$(printf '%s' "$BOUND2" | tr -d '\"')" = "None" ] \
  && echo "    ✓ 一次性码不能被复用" \
  || { echo "    ✗ 码被复用了"; FAILED=1; }

if [ "$FAILED" = "0" ]; then
  echo "[done] LINE 隔离验证全部通过"
else
  echo "[done] 有失败项（见上面 ✗）" >&2
  exit 1
fi
