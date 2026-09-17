#!/usr/bin/env bash
# 控制面「租户开通」表单的端到端验证：走的是表单用的同一条接口。
#
# 覆盖：
#   1) 正常开通（域名只填前缀 → 服务端补成 前缀.pearnly.com + 功能模版 + 邀请邮件）
#   2) 一批必须被拒绝的输入（前缀格式、保留名、重复、功能挂错产品、账号已存在）
#
# 用法: scripts/sample-issue-form.sh [接收邀请的邮箱]
#   不给邮箱时用 form-<时间戳>@sample.local（不会真投递，适合反复跑）。
#   给真实邮箱时会真的发一封邀请邮件 —— 用于人工确认收件。
set -euo pipefail

CONTROL_HOST=control.localhost
CONTROL=http://127.0.0.1:8104
USER_NAME="${PEARNLY_OPS_USER:-earn}"
USER_PASS="${PEARNLY_OPS_PASS:-earn}"
INVITE_TO="${1:-}"
SUFFIX="$(date +%s)"
EMAIL="${INVITE_TO:-form-${SUFFIX}@sample.local}"

JAR=$(mktemp); BODY=$(mktemp)
trap 'rm -f "$JAR" "$BODY"' EXIT

api() {  # api <method> [--post]  ; 请求体从 $BODY 读
  local method="$1"; shift || true
  if [ "${1:-}" = "--post" ]; then
    curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
      -H "X-Frappe-CSRF-Token: $CSRF" -X POST \
      "$CONTROL/api/method/$method" --data-binary "@$BODY"
  else
    curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/$method"
  fi
}

# 断言一次调用成功，并把结果交给调用方判断（用于"应被规范化"这类用例）
issue_ok() {  # issue_ok <标题> <python 判断表达式，用 m 指代 message>
  local title="$1" check="$2" out
  out=$(api pearnly_control.issues.api_issue --post)
  if printf '%s' "$out" | python3 -c '
import sys, json
d = json.load(sys.stdin)
m = d.get("message")
sys.exit(0 if m else 1)
'; then
    printf '%s' "$out" | python3 -c "
import sys, json
m = json.load(sys.stdin)['message']
print('    ✓ ${title}' if (${check}) else '    ✗ ${title} —— 通过了但结果不对: ' + json.dumps(m.get('deployments'), ensure_ascii=False))
sys.exit(0 if (${check}) else 1)
" || FAILED=1
  else
    echo "    ✗ ${title} —— 本该通过却被拒: $(printf '%s' "$out" | head -c 200)"
    FAILED=1
  fi
}

# 断言一次调用被拒绝，且错误信息里有期望的关键词
expect_reject() {  # expect_reject <标题> <关键词>
  local title="$1" needle="$2" out
  out=$(api pearnly_control.issues.api_issue --post)
  if printf '%s' "$out" | python3 -c '
import sys, json
d = json.load(sys.stdin)
ok = not d.get("message") and ("exc" in d or "exception" in d or "_server_messages" in d)
sys.exit(0 if ok else 1)
'; then
    if printf '%s' "$out" | grep -q "$needle"; then
      echo "    ✓ $title —— 被拒绝，且提示对得上"
    else
      echo "    ✗ $title —— 被拒绝了，但提示里没有「${needle}」：$(printf '%s' "$out" | head -c 160)"
      FAILED=1
    fi
  else
    echo "    ✗ $title —— 竟然通过了：$(printf '%s' "$out" | head -c 160)"
    FAILED=1
  fi
}

FAILED=0

# 基础域名是一项配置（本机是 localhost，生产是 pearnly.com），测试不该写死。
# 从站点配置读出来，断言才能同时适配两种环境。
BASE_DOMAIN=$(docker --context colima-pearnly exec pearnly-sample-control-bench-1 bash -lc \
  "cd ~/frappe-bench && bench --site control.localhost execute pearnly_control.domains.base_domain 2>/dev/null | tail -1" \
  | tr -d '"' | tr -d '\r')
BASE_DOMAIN="${BASE_DOMAIN:-pearnly.com}"
echo "    基础域名（从站点配置读）: ${BASE_DOMAIN}"

echo "[1] 登录控制面（本地账号）"
curl -sS -c "$JAR" -o /dev/null -X POST "$CONTROL/api/method/login" \
  -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
  --data-binary "$(python3 -c 'import json,sys; print(json.dumps({"usr":sys.argv[1],"pwd":sys.argv[2]}))' "$USER_NAME" "$USER_PASS")"
curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/frappe.auth.get_logged_user" \
  | python3 -c 'import sys,json; print("    当前操作人:", json.load(sys.stdin).get("message"))'

CSRF=$(curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" \
  "$CONTROL/api/method/pearnly_control.issues.api_context" \
  | python3 -c 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("csrf_token") or "")')
echo "    CSRF 令牌长度: ${#CSRF}"

echo "[2] 正常开通：前缀 + 功能模版 + 邀请邮件"
cat > "$BODY" <<JSON
{
  "tenant_name": "表单验证租户 ${SUFFIX}",
  "account_email": "${EMAIL}",
  "account_name": "开通验证",
  "contact_phone": "+66000000000",
  "delivery": "invite",
  "apps": [
    {"app": "cowork", "domain_prefix": "fcwo${SUFFIX}"},
    {"app": "dms", "domain_prefix": "fdms${SUFFIX}"}
  ],
  "features": {
    "cowork": ["home_subscription", "intake", "recon"],
    "dms": ["home_subscription", "id_card_ocr", "quota"]
  }
}
JSON
python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$BODY"
RESP=$(api pearnly_control.issues.api_issue --post)
printf '%s' "$RESP" | BASE_DOMAIN="$BASE_DOMAIN" python3 -c '
import os, sys, json
BASE = os.environ.get("BASE_DOMAIN") or "pearnly.com"
d = json.load(sys.stdin)
m = d.get("message")
if not m:
    print("    ✗ 开通失败:", str(d)[:300]); raise SystemExit(1)
print("    发号单:", m["issue"], "| 租户:", m["tenant"])
print("    账号:", m["account_email"], "| 交付方式:", m["delivery"])
for dep in m["deployments"]:
    print("    待开通:", dep["app"], "->", dep["domain"])
for product, feats in m["features"].items():
    if feats:
        print("    功能", product, ":", ", ".join(feats))
print("    一次性密码:", "（未返回，走邀请邮件）" if "initial_password" not in m else "有")
ok = all(dep["domain"].endswith("." + BASE) and dep["domain"].startswith(dep["domain_prefix"] + ".")
         for dep in m["deployments"])
print(f"    域名规则（前缀+.{BASE}）:", "✓" if ok else "✗")
if not ok: raise SystemExit(1)
if m["delivery"] != "invite":
    print("    ✗ 交付方式不是 invite"); raise SystemExit(1)
'
ISSUE_NO=$(printf '%s' "$RESP" | python3 -c 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("issue") or "")')
if [ -z "$ISSUE_NO" ]; then echo "    ✗ 拿不到发号单号"; exit 1; fi

echo "[3] 必须被拒绝的输入"

cat > "$BODY" <<JSON
{"tenant_name":"大写前缀","account_email":"norm-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"Foo${SUFFIX}"}]}
JSON
issue_ok "前缀含大写字母会被规范成小写（DNS 本就大小写不敏感）" \
  "m['deployments'][0]['domain']=='foo${SUFFIX}.${BASE_DOMAIN}'"

cat > "$BODY" <<JSON
{"tenant_name":"下划线前缀","account_email":"bad1-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"foo_bar"}]}
JSON
expect_reject "前缀含下划线" "前缀不合法"

cat > "$BODY" <<JSON
{"tenant_name":"全数字前缀","account_email":"bad6-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"123456"}]}
JSON
expect_reject "前缀全是数字" "不能全是数字"

cat > "$BODY" <<JSON
{"tenant_name":"保留名","account_email":"bad2-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"www"}]}
JSON
expect_reject "前缀是保留名 www" "保留名"

cat > "$BODY" <<JSON
{"tenant_name":"同单重复域名","account_email":"bad3-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"dup${SUFFIX}"},
         {"app":"dms","domain_prefix":"dup${SUFFIX}"}]}
JSON
expect_reject "同一张单里域名重复" "重复"

cat > "$BODY" <<JSON
{"tenant_name":"功能挂错产品","account_email":"bad4-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"wrong${SUFFIX}"}],
 "features":{"cowork":["sales"]}}
JSON
expect_reject "把 ERP 的功能勾进 Cowork" "属于 ERP"

cat > "$BODY" <<JSON
{"tenant_name":"功能不存在","account_email":"bad7-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"nofeat${SUFFIX}"}],
 "features":{"cowork":["no_such_feature"]}}
JSON
expect_reject "功能代码不存在" "没有这个功能"

cat > "$BODY" <<JSON
{"tenant_name":"功能无对应产品","account_email":"bad5-${SUFFIX}@sample.local","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"noprod${SUFFIX}"}],
 "features":{"dms":["id_card_ocr"]}}
JSON
expect_reject "勾了 DMS 功能但没开 DMS" "没有选"

cat > "$BODY" <<JSON
{"tenant_name":"账号已存在","account_email":"${EMAIL}","delivery":"password",
 "apps":[{"app":"cowork","domain_prefix":"again${SUFFIX}"}]}
JSON
expect_reject "同一账号重复开通" "已存在"

echo "[4] 落库回读（确认存的是算出来的完整域名，不是用户输入）"
docker --context colima-pearnly exec pearnly-sample-control-bench-1 bash -lc \
  "cd ~/frappe-bench && bench --site control.localhost execute frappe.db.get_all --kwargs '{\"doctype\":\"Pearnly Issue App\",\"filters\":{\"parent\":\"${ISSUE_NO}\"},\"fields\":[\"app\",\"domain_prefix\",\"domain\"],\"order_by\":\"idx\"}'" \
  2>&1 | grep -v '^[[:space:]]*$' | tail -1

if [ "$FAILED" = "0" ]; then
  echo "[done] 全部通过"
else
  echo "[done] 有失败项，见上面 ✗" >&2
  exit 1
fi
