#!/usr/bin/env bash
# 给一个站点配置统一身份接入 + 登记人设 + 绑定身份。
#
# 每个部署各有一套配置与一个 Keycloak 客户端——这是设计要的，不是重复劳动。
#
# 用法:
#   scripts/sample-configure-site.sh <组> <站点> <client_id> <client_secret> <回调端口> [subject]
#
# 必须用 bash 执行（zsh 下 set -- 不分词，会把键值拼成一串，踩过）。
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
ISSUER=http://keycloak:8080/realms/pearnly-business
ADMIN_CID=pearnly-admin
PWCHECK_CID=pearnly-password-check
SUBJECT_DEFAULT=f549a7e4-e64f-4344-b269-8be1d8a9f274

group="${1:?用法: $0 <组> <站点> <client_id> <client_secret> <回调端口> [subject]}"
site="${2:?}"
client_id="${3:?}"
client_secret="${4:?}"
port="${5:?}"
subject="${6:-$SUBJECT_DEFAULT}"

adm_secret="${PEARNLY_ADMIN_CLIENT_SECRET:?需要环境变量 PEARNLY_ADMIN_CLIENT_SECRET}"
pw_secret="${PEARNLY_PWCHECK_CLIENT_SECRET:?需要环境变量 PEARNLY_PWCHECK_CLIENT_SECRET}"

bench() { docker --context "$CTX" compose -f "$COMPOSE" exec -T "$group-bench" bench --site "$site" "$@"; }

echo "[$site] 写站点配置"
bench set-config pearnly_oidc_issuer "$ISSUER" >/dev/null
bench set-config pearnly_oidc_client_id "$client_id" >/dev/null
bench set-config pearnly_oidc_client_secret "$client_secret" >/dev/null
bench set-config pearnly_oidc_redirect_uri "http://127.0.0.1:${port}/api/method/pearnly_core.identity.callback" >/dev/null
bench set-config pearnly_oidc_admin_client_id "$ADMIN_CID" >/dev/null
bench set-config pearnly_oidc_admin_client_secret "$adm_secret" >/dev/null
bench set-config pearnly_oidc_password_check_client_id "$PWCHECK_CID" >/dev/null
bench set-config pearnly_oidc_password_check_client_secret "$pw_secret" >/dev/null

# 本机样例站点**没有 LINE 通道**，于是"保存草稿 → 卡片送达 LINE"这一步在本机永远失败
# （`dms_booking_edit.save` 要的是真回执），用户就没法在本机验收任何涉及 LINE 的闭环。
# 打开本地镜像：消息原文记进推送日志（`trigger=line_mirror`），发送算成功。
# 生产**不会**有这个键；而且 `pearnly_core.line.mirroring()` 还要求"本站点没配 LINE 凭据"
# 才生效 —— 两道锁，误开也吞不掉真消息。
bench set-config pearnly_line_mirror 1 >/dev/null

# 配对校验：键与值必须分成两列，拼在一起就说明没设对
if ! bench show-config 2>/dev/null | grep -qE "^\|\s+pearnly_oidc_issuer\s+\|\s+http"; then
  echo "  [错误] 站点配置没写对（键值可能被拼成一列）：" >&2
  bench show-config 2>/dev/null | grep pearnly_oidc | head -4 >&2
  exit 1
fi
echo "  配置校验通过"

echo "[$site] 建人设"
docker --context "$CTX" compose -f "$COMPOSE" exec -T "$group-bench" \
  bench --site "$site" console < scripts/sample-seed-personas.py 2>/dev/null \
  | grep -oE 'total users: [0-9]+' | tail -1 | sed 's/^/  /'

echo "[$site] 绑定身份"
printf 'import frappe\nfn = frappe.get_attr("pearnly_core.identity.bind_identity")\ntry:\n    print("BIND=" + str(fn(user="tenant-admin@sample.local", subject="%s", deployment_id="%s")))\nexcept Exception as e:\n    print("BIND_ALREADY" if "已绑定" in str(e) else ("BIND_ERR=" + str(e)[:120]))\n' "$subject" "$site" \
  | docker --context "$CTX" compose -f "$COMPOSE" exec -T "$group-bench" bench --site "$site" console 2>/dev/null \
  | grep -oE 'BIND=[a-z0-9]+|BIND_ALREADY|BIND_ERR=.*' | tail -1 | sed 's/^/  /' || true

echo "[$site] 登录探测"
loc=$(curl -sS -o /dev/null -D - -H "Host: $site" "http://127.0.0.1:${port}/api/method/pearnly_core.identity.begin_login" \
      | awk 'tolower($1)=="location:"{print $2}' | tr -d '\r' | tail -1)
if [ -z "$loc" ]; then echo "  [错误] 拿不到登录跳转" >&2; exit 1; fi
echo "  跳转到身份服务：正常"
