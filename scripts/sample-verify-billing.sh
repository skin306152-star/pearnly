#!/usr/bin/env bash
# 验计费底座：**余额为 0 拦住 → 充值申请 → 管理员审核 → 立即到账 → 扣费**。
#
# 跑在真环境上：产品站点真调控制面（HTTP + 服务令牌），钱真的落在控制面的钱包与台账里。
#
# 用法: scripts/sample-verify-billing.sh <组> <站点> <租户号> [金额]
#   scripts/sample-verify-billing.sh dms one938744.localhost T-2026-00xx 300
#
# 会真充一笔测试金额进去，并真扣一笔 —— 不是只读脚本，心里有数再跑。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-billing.py"
SAMPLES="${PEARNLY_OCR_SAMPLES_DIR:-$HERE/samples/ocr-id-card}"
CONTROL="http://127.0.0.1:8104"
CONTROL_HOST="control.localhost"
USER_NAME="${PEARNLY_OPS_USER:-earn}"
USER_PASS="${PEARNLY_OPS_PASS:-earn}"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

group="${1:?用法: $0 <组> <站点> <租户号> [金额]}"
site="${2:?}"
tenant="${3:?}"
amount="${4:-300}"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT
FAILED=0
py() { python3 -c "$@"; }

api() {  # api <method> [json-body]  —— 带控制面的登录会话
  if [ $# -gt 1 ]; then
    curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
      -X POST "$CONTROL/api/method/$1" --data-binary "$2"
  else
    curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/$1"
  fi
}

site_conf() {  # site_conf <key>
  dc exec -T "$group-bench" bash -lc "bench --site $site show-config" 2>/dev/null \
    | rg " $1 " | awk -F'|' '{print $3}' | tr -d ' '
}

phase() {  # phase <gate|status|charge>
  local out
  out=$(dc exec -T -e "PHASE=$1" "$group-bench" bash -lc \
    "printf 'exec(open(\"/tmp/verify_billing.py\").read())\nmain()\n' | PYTHONUNBUFFERED=1 bench --site $site console 2>&1" || true)
  printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //' | grep -E "^(PASS|FAIL|RESULT|  )" || true
  printf '%s' "$out" | grep -q "RESULT: ALL PASS"
}

echo "[1/6] 送样本与检查脚本进容器 $group-bench"
dc exec -T "$group-bench" bash -lc 'mkdir -p /tmp/ocr-real/images'
if [ -d "$SAMPLES/images" ]; then
  dc cp "$SAMPLES/images/." "$group-bench:/tmp/ocr-real/images" >/dev/null 2>&1 || true
fi
dc exec -T "$group-bench" bash -lc 'cat > /tmp/verify_billing.py' < "$CHECK"

echo "[2/6] 登录控制面"
curl -sS -c "$JAR" -o /dev/null -X POST "$CONTROL/api/method/login" \
  -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
  --data-binary "$(py 'import json,sys; print(json.dumps({"usr":sys.argv[1],"pwd":sys.argv[2]}))' "$USER_NAME" "$USER_PASS")"
who=$(api frappe.auth.get_logged_user | py 'import sys,json; print(json.load(sys.stdin).get("message") or "")')
if [ -z "$who" ]; then echo "[失败] 控制面登录不了" >&2; exit 1; fi
echo "    操作人: $who"

echo "[3/6] 余额为 0 时：识别必须被拦在模型之前"
before=$(api "pearnly_control.billing_api.admin_summary?tenant=$tenant" \
  | py 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("balance_thb"))')
echo "    当前余额: ฿$before"
# 这一条的前提是"余额为 0"。跑过一次之后余额就不是 0 了，所以先把状态摆回去 ——
# 否则第二次跑必然失败，而失败的原因跟被测的东西毫无关系。用人工调账归零（会留台账）。
if py 'import sys; sys.exit(0 if abs(float(sys.argv[1] or 0)) > 0.001 else 1)' "$before"; then
  zero=$(api pearnly_control.billing_api.admin_adjust \
    "$(py 'import json,sys; print(json.dumps({"tenant":sys.argv[1],"amount_thb":-float(sys.argv[2]),"note":"验证前置：余额归零"}))' "$tenant" "$before")" \
    | py 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("new_balance") or "")')
  echo "    已归零（原 ฿$before → ฿${zero:-失败}）"
fi
# 归零之后"充值前余额"就是 0 —— 后面那步拿它当比较基准，不能用归零之前的旧值
before=0
if phase gate; then :; else FAILED=1; fi

echo "[4/6] 租户提交充值 ฿${amount}（只落申请，不动余额）"
token=$(site_conf pearnly_billing_token)
if [ -z "$token" ]; then
  echo "    ✗ 站点上还没有计费令牌 —— 这个租户是加计费之前开通的，重跑一次开通（plan）就会补上" >&2
  exit 1
fi
raise_req=$(curl -sS -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
  -H "X-Pearnly-Billing-Token: $token" \
  -X POST "$CONTROL/api/method/pearnly_control.billing_api.service_topup_request" \
  --data-binary "$(py 'import json,sys; print(json.dumps({"tenant":sys.argv[1],"amount_thb":float(sys.argv[2]),"note":"计费底座验证","payer_name":"验证"}))' "$tenant" "$amount")")
reqname=$(printf '%s' "$raise_req" | py 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("request") or "")')
if [ -n "$reqname" ]; then
  echo "    ✓ 申请已落库: ${reqname}"
  pending=$(api pearnly_control.billing_api.admin_pending_topups \
    | py 'import sys,json; ms=json.load(sys.stdin).get("message") or {}; print(len(ms.get("items") or []))')
  echo "    ✓ 审核台看得到待审核: $pending 条"
else
  echo "    ✗ 充值申请没落库: $(printf '%s' "$raise_req" | head -c 220)"; FAILED=1
fi

echo "[5/6] 管理员同意 → 余额立即到账"
if [ -n "$reqname" ]; then
  appr=$(api pearnly_control.billing_api.admin_approve \
    "$(py 'import json,sys; print(json.dumps({"request":sys.argv[1],"actual_amount_thb":float(sys.argv[2]),"note":"验证到账"}))' "$reqname" "$amount")")
  newbal=$(printf '%s' "$appr" | py 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("new_balance") or "")')
  if [ -n "$newbal" ] && py 'import sys; sys.exit(0 if float(sys.argv[1])>float(sys.argv[2]) else 1)' "$newbal" "${before:-0}"; then
    echo "    ✓ 到账后余额 ฿${newbal}（原 ฿${before}）"
  else
    echo "    ✗ 没到账: $(printf '%s' "$appr" | head -c 250)"; FAILED=1
  fi
else
  echo "    - 没有申请可审，跳过"; FAILED=1
fi

echo "[6/6] 到账后：余额闸放行 + 真扣一笔"
if phase status; then :; else FAILED=1; fi
if phase charge; then :; else FAILED=1; fi

echo ""
echo "落库回读（控制面）:"
api "pearnly_control.billing_api.admin_summary?tenant=$tenant" | py '
import sys, json
m = json.load(sys.stdin).get("message") or {}
print("    余额 ฿%s  本月已用 %s 页" % (m.get("balance_thb"), m.get("pages_used_this_month")))
for t in (m.get("transactions") or [])[:5]:
    print("    [%-10s] %+8s -> ฿%-9s %s" % (
        t["type"], t["amount_thb"], t["balance_after"], (t.get("description") or "")[:38]))
'

if [ "$FAILED" = "0" ]; then
  echo "[done] 计费底座全过"
else
  echo "[done] 有失败项，见上面" >&2
  exit 1
fi
