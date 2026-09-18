#!/usr/bin/env bash
# 浏览器编辑器页的**真实点击验收**（一条命令跑完整条链）：
#
#   1. 用真实 DMS 主档造一份草稿 + 绑定，发一张票据（`sample-dms-edit-fixture.py`）
#   2. Playwright 打开那一页：读草稿 → 切语言 → 改一格 → 点保存
#   3. 回读服务端状态：nonce 换了吗？改的值进去了吗？新卡发出去了吗？
#   4. 收工清掉本轮造的绑定 / 会话 / 镜像日志
#
# 用法: scripts/sample-verify-dms-edit-page.sh <组> <站点> [端口] [站点用户]
#   scripts/sample-verify-dms-edit-page.sh dms one761900.localhost 8103 onestop-761900@sample.local
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$HERE/scripts/sample-dms-edit-fixture.py"
STATE_CHECK="$HERE/scripts/sample-dms-edit-state-check.py"
PAGE="$HERE/scripts/sample-verify-dms-edit-page.mjs"

group="${1:?用法: $0 <组> <站点> [端口] [站点用户]}"
site="${2:?用法: $0 <组> <站点> [端口] [站点用户]}"
port="${3:-8103}"
site_user="${4:-onestop-761900@sample.local}"

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

# node：优先用仓库外那份能跑起来的东西（本机的 npm shim 指着一个已不存在的路径）。
# 这里只要求 `node` 能跑 mjs，Playwright 由 mjs 自己按 PEARNLY_PLAYWRIGHT_BASE 解析。
NODE="${PEARNLY_NODE:-$(command -v node)}"

run_fixture() {
  local mode="$1" uid="${2:-}" nonce="${3:-}" since="${4:-}"
  dc exec -T dms-bench bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/fixture.py' < "$FIXTURE"
  dc exec -T \
    -e "MODE=$mode" -e "SITE_USER=$site_user" -e "LINE_UID=$uid" -e "NONCE=$nonce" -e "SINCE=$since" \
    dms-bench bash -lc \
    "cd /home/frappe/frappe-bench && printf 'exec(open(\"/tmp/dmscheck/fixture.py\").read())\nmain()\n' | bench --site '$site' console 2>&1"
}

passes=0
failed=0
cleanup_args=()

run_pass() {
  local fixture_mode="$1" page_mode="$2"
  echo "── 模式 ${page_mode}：[1/4] 造草稿（真实 DMS 主档）+ 发票据"
  local prep fixture nonce ticket uid car since page_ok state_raw state state_ok
  prep="$(run_fixture "$fixture_mode")"
  fixture="$(printf '%s' "$prep" | grep -o 'FIXTURE {.*}' | head -1 | sed 's/^FIXTURE //')"
  if [ -z "$fixture" ]; then
    echo "[失败] 造夹具没过：" >&2
    printf '%s\n' "$prep" | tail -20 >&2
    failed=$((failed + 1))
    return 1
  fi
  since="$(date -u +'%Y-%m-%d %H:%M:%S')"
  nonce="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["nonce"])')"
  ticket="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["ticket"])')"
  uid="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["line_user_id"])')"
  car="$(printf '%s' "$fixture" | python3 -c 'import json,sys; print(json.load(sys.stdin)["car_label"])')"
  cleanup_args+=("$uid" "$since")
  echo "   草稿 ${nonce} · 车型 ${car} · 绑定 ${uid}"

  echo "   [2/4] 打开页面点一遍（Playwright）"
  page_ok=1
  "$NODE" "$PAGE" --base "http://${site}:${port}" --draft "$nonce" --ticket "$ticket" \
    --mode "$page_mode" || page_ok=0

  echo "   [3/4] 回读服务端状态"
  state_ok=1
  state_raw="$(run_fixture inspect "$uid")"
  state="$(printf '%s' "$state_raw" | grep -o 'STATE {.*}' | head -1 | sed 's/^STATE //')"
  if [ -z "$state" ]; then
    echo "[失败] 回读不到状态：" >&2
    printf '%s\n' "$state_raw" | tail -20 >&2
  elif MODE="$page_mode" STATE_JSON="$state" OLD_NONCE="$nonce" python3 "$STATE_CHECK"; then
    state_ok=0
  fi

  if [ "$page_ok" = 1 ] && [ "$state_ok" = 0 ]; then
    passes=$((passes + 1))
  else
    failed=$((failed + 1))
    echo "[失败] ${page_mode} 这一轮：页面=$page_ok 服务端回读=$state_ok" >&2
  fi
}

# 两种模式都点一遍：订车草稿（客户+订车+付款+附件）与客户档（只有客户资料）。
# 两轮之间停一下：本机的 DMS 测试账号只允许一个活跃会话，挨太近会被判成并发登录。
run_pass prepare booking
sleep 3
run_pass prepare-customer customer

echo "── [4/4] 收工清理（本轮夹具）"
if [ "${#cleanup_args[@]}" -gt 0 ]; then
  i=0
  while [ "$i" -lt "${#cleanup_args[@]}" ]; do
    run_fixture cleanup "${cleanup_args[i]}" "" "${cleanup_args[i + 1]}" | grep -E "CLEANED" || true
    i=$((i + 2))
  done
fi

if [ "$failed" = 0 ] && [ "$passes" -ge 2 ]; then
  echo "[done] 编辑器页面验收通过（订车 + 客户档：页面点击 + 服务端回读）"
  exit 0
fi
echo "[失败] 通过 $passes 轮 / 失败 $failed 轮" >&2
exit 1
