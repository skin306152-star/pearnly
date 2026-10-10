#!/usr/bin/env bash
# Cloudflare 侧开关：把测试租户的两个域名在"线上"与"测试环境"之间切换。
#
# 现状（2026-09-24 读回）：testdms/testerp 走通配记录 `*.pearnly.com` → 正式服务器。
# 因此切换 = 给这两个名字建/删自己的 CNAME 记录：
#   切到测试环境：CNAME <tunnel-id>.cfargotunnel.com（proxied）→ 走隧道到那台 Windows
#   切回线上    ：删除该记录 → 自动回落到通配记录，指回正式服务器
#
# 凭据只从本机文件读：~/.config/pearnly-testenv/cloudflare.token（600，不进仓库）
#
# 用法：
#   bash scripts/testenv-cloudflare.sh status [dms] [erp] [cowork]
#   bash scripts/testenv-cloudflare.sh tunnel-create     # 只做一次
#   bash scripts/testenv-cloudflare.sh switch-lab  dms    # 只把 DMS 测试租户切过去
#   bash scripts/testenv-cloudflare.sh switch-prod erp    # 只把 ERP 测试租户切回来
#
# 2026-09-26 起加上 Cowork 的测试租户 testcowork.pearnly.com（Cowork 前端 8181）。
# 不带产品名 = 全部都切。隧道本身同时带这几个域名，
# 所以"切哪个产品"只是"给哪个域名建/删自己的 CNAME"。
set -euo pipefail

config_dir=${PEARNLY_TESTENV_CFG:-$HOME/.config/pearnly-testenv}
token_file=$config_dir/cloudflare.token
env_file=$config_dir/cloudflare.env
zone_name=${PEARNLY_TESTENV_ZONE:-pearnly.com}
hosts=(testdms.pearnly.com testerp.pearnly.com testcowork.pearnly.com testai.pearnly.com)

host_for() {
  case "$1" in
    dms) echo testdms.pearnly.com ;;
    erp) echo testerp.pearnly.com ;;
    cowork) echo testcowork.pearnly.com ;;
    ai) echo testai.pearnly.com ;;  # 智能体入口（ADR-019），在本栈账本容器上
    *) echo "unknown app: $1 (expected dms|erp|cowork|ai)" >&2; exit 2 ;;
  esac
}

# 本次要操作的域名：不带参数=全部；带产品名=只这些产品。
targets() {
  if (( $# == 0 )); then
    printf '%s\n' "${hosts[@]}"
    return
  fi
  for app in "$@"; do host_for "$app"; done
}

# records() 收的是 python 列表字面量，这里按同样的选择生成。
targets_py() {
  printf '[%s]' "$(targets "$@" | sed 's/.*/"&"/' | paste -sd, -)"
}

[[ -s "$token_file" ]] || { echo "missing $token_file" >&2; exit 2; }
token=$(cat "$token_file")
[[ -s "$env_file" ]] && . "$env_file"

api() {
  local method=$1 path=$2; shift 2
  curl -s -X "$method" -H "Authorization: Bearer $token" \
    -H 'Content-Type: application/json' "$@" "https://api.cloudflare.com/client/v4$path"
}

json() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

zone_id() {
  [[ -n "${CF_ZONE_ID:-}" ]] && { echo "$CF_ZONE_ID"; return; }
  api GET "/zones?name=$zone_name" | json "print(d['result'][0]['id'])"
}

account_id() {
  [[ -n "${CF_ACCOUNT_ID:-}" ]] && { echo "$CF_ACCOUNT_ID"; return; }
  echo "set CF_ACCOUNT_ID in $env_file" >&2; exit 2
}

tunnel_id() {
  api GET "/accounts/$(account_id)/cfd_tunnel?name=pearnly-testenv&is_deleted=false" \
    | json "print(d['result'][0]['id'] if d['result'] else '')"
}

records() {
  api GET "/zones/$(zone_id)/dns_records?per_page=100" | json "
for r in d['result']:
    if r['name'] in $1: print(r['id'], r['type'], r['name'], r['content'], 'proxied=' + str(r['proxied']))"
}

cmd_status() {
  echo "== 当前解析 =="
  records "$(targets_py "$@")" || true
  echo "== 通配记录 =="
  records "['*.pearnly.com']" || true
}

cmd_tunnel_create() {
  local existing; existing=$(tunnel_id)
  if [[ -z "$existing" ]]; then
    echo "creating tunnel pearnly-testenv"
    existing=$(api POST "/accounts/$(account_id)/cfd_tunnel" \
      --data '{"name":"pearnly-testenv","config_src":"cloudflare"}' | json "print(d['result']['id'])")
  fi
  echo "tunnel_id=$existing"
  api PUT "/accounts/$(account_id)/cfd_tunnel/$existing/configurations" --data @- <<EOF | json "print('config success=', d['success'])"
{
  "config": {
    "ingress": [
      {"hostname": "testdms.pearnly.com", "service": "http://127.0.0.1:8183"},
      {"hostname": "testerp.pearnly.com", "service": "http://127.0.0.1:8182"},
      {"hostname": "testcowork.pearnly.com", "service": "http://127.0.0.1:8181"},
      {"hostname": "testai.pearnly.com", "path": "^/assets/", "service": "http://127.0.0.1:8181"},
      {"hostname": "testai.pearnly.com", "service": "http://127.0.0.1:8184"},
      {"service": "http_status:404"}
    ]
  }
}
EOF
  printf '%s\n' "$existing" > "$config_dir/tunnel.id"
  chmod 600 "$config_dir/tunnel.id"
  api GET "/accounts/$(account_id)/cfd_tunnel/$existing/token" \
    | json "open('$config_dir/tunnel.token','w').write(d['result'])" 
  chmod 600 "$config_dir/tunnel.token"
  echo "run token written to $config_dir/tunnel.token（600）"
}

cmd_switch_lab() {
  local tid; tid=$(tunnel_id)
  [[ -n "$tid" ]] || { echo 'tunnel pearnly-testenv not found; run tunnel-create first' >&2; exit 2; }
  while read -r host; do
    local existing
    existing=$(api GET "/zones/$(zone_id)/dns_records?type=CNAME&name=$host" | json "print(d['result'][0]['id'] if d['result'] else '')")
    local body
    body=$(printf '{"type":"CNAME","name":"%s","content":"%s.cfargotunnel.com","proxied":true,"ttl":1,"comment":"pearnly testenv lab"}' "$host" "$tid")
    if [[ -n "$existing" ]]; then
      api PUT "/zones/$(zone_id)/dns_records/$existing" --data "$body" | json "print('updated', d['success'])"
    else
      api POST "/zones/$(zone_id)/dns_records" --data "$body" | json "print('created', d['success'])"
    fi
    echo "  $host -> tunnel"
  done < <(targets "$@")
  echo '以上域名已切到测试环境；跑 `status` 复核。'
}

cmd_switch_prod() {
  local zid; zid=$(zone_id)
  while read -r host; do
    local ids
    ids=$(api GET "/zones/$zid/dns_records?type=CNAME&name=$host" | json "print(' '.join(r['id'] for r in d['result']))")
    if [[ -z "$ids" ]]; then
      echo "  $host 本来就没自己的记录（走通配 → 线上）"
      continue
    fi
    for id in $ids; do
      api DELETE "/zones/$zid/dns_records/$id" | json "print('  deleted', d['success'])"
    done
  done < <(targets "$@")
  echo '以上域名已切回线上；跑 `status` 复核回落通配记录。'
}

case "${1:-}" in
  status) shift; cmd_status "$@" ;;
  tunnel-create) cmd_tunnel_create ;;
  switch-lab) shift; cmd_switch_lab "$@" ;;
  switch-prod) shift; cmd_switch_prod "$@" ;;
  *) echo 'usage: testenv-cloudflare.sh {status|tunnel-create|switch-lab|switch-prod} [dms] [erp] [cowork]'; exit 2 ;;
esac
