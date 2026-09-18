#!/usr/bin/env bash
# 给**已经开通的**租户站补 realm 级身份凭据。
#
# 为什么需要这个脚本：改密用的两个客户端（`pearnly-admin`、`pearnly-password-check`）
# 是 **realm 级**的，同 realm 下所有租户共用一套。开通流程在 2026-09-18 之前
# **从来没有下发过**它们（只有样板站当年手工补过），所以那之前开出来的租户站
# 都是"**能登录、改不了密码**"，报错是"取管理令牌失败"（读出来是 None）。
#
# 新开的站点由开通流程的 `set_oidc` 步骤自动写（见 scripts/sample-provision.sh）；
# 本脚本只负责把**已经存在**的站点补上，顺便重跑一次建站收尾 ——
# `pearnly_core.provisioning.finalize_site()` 是幂等的，会把新增的站点侧设置
# （例如关掉密码强度开关）补齐。
#
# 凭据来源是**控制面的开户目标**（Pearnly Target），和开通流程读的是同一份，
# 不另抄一份，免得两边漂移。
#
# 用法: scripts/sample-backfill-realm-credentials.sh <组> <站点...>
#   组跟 sample-finalize-site.sh 一样是**不带 -bench 后缀**的那个名字
#   （cowork / dms / erp / control），脚本内部自己拼。
#   例: scripts/sample-backfill-realm-credentials.sh cowork qc65956.localhost
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
CONTROL_BENCH=control-bench
CONTROL_SITE=control.localhost

group=${1:?用法: $0 <组> <站点...>}
shift
if [ $# -eq 0 ]; then
  echo "至少给一个站点名" >&2
  exit 2
fi

d()  { docker --context "$CTX" "$@"; }
dc() { d compose -f "$COMPOSE" "$@"; }

# ── 取凭据 ──────────────────────────────────────────────────────────────────

echo "[凭据] 从控制面开户目标读取 realm 级凭据"
raw=$(dc exec -T "$CONTROL_BENCH" bench --site "$CONTROL_SITE" execute \
        pearnly_control.provisioning.target_credentials 2>&1 | tail -1)

# bench execute 对 dict 的输出形式不完全固定，JSON 与 Python 字面量都认，
# 免得因为打印格式变化把"读不到"误报成"目标没配"。
creds=$(printf '%s' "$raw" | python3 -c '
import ast, json, sys
text = sys.stdin.read().strip()
if not text:
    sys.exit("控制面没有返回内容")
try:
    data = json.loads(text)
except json.JSONDecodeError:
    try:
        data = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        sys.exit("无法解析控制面返回：" + text[:200])
if not isinstance(data, dict):
    sys.exit("控制面返回的不是字典：" + text[:200])
print(json.dumps(data))
') || { echo "[错误] 读凭据失败：$raw" >&2; exit 1; }

get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
admin_id=$(python3 -c "$get" "$creds" admin_client_id)
admin_secret=$(python3 -c "$get" "$creds" admin_client_secret)
check_id=$(python3 -c "$get" "$creds" password_check_client_id)
check_secret=$(python3 -c "$get" "$creds" password_check_client_secret)

for pair in "管理客户端 ID:$admin_id" "管理客户端密钥:$admin_secret" \
            "密码校验客户端 ID:$check_id" "密码校验客户端密钥:$check_secret"; do
  name="${pair%%:*}"; value="${pair#*:}"
  [ -n "$value" ] || { echo "[错误] 开户目标上缺 $name —— 先在控制面补齐开户目标" >&2; exit 1; }
done
echo "  管理客户端=$admin_id  密码校验客户端=$check_id"

# ── 写入各站点 ──────────────────────────────────────────────────────────────

for site in "$@"; do
  echo "[补配置] $site"
  dc exec -T "$group-bench" bench --site "$site" set-config pearnly_oidc_admin_client_id "$admin_id" >/dev/null
  dc exec -T "$group-bench" bench --site "$site" set-config pearnly_oidc_admin_client_secret "$admin_secret" >/dev/null
  dc exec -T "$group-bench" bench --site "$site" set-config pearnly_oidc_password_check_client_id "$check_id" >/dev/null
  dc exec -T "$group-bench" bench --site "$site" set-config pearnly_oidc_password_check_client_secret "$check_secret" >/dev/null

  # 重启 web：site_config 是 gunicorn 启动时读的（--preload，踩过），不重启不生效
  d compose -f "$COMPOSE" restart "$group-web" >/dev/null 2>&1 || true

  # ${site} 要带花括号：紧跟中文时 shell 会把中文当成变量名的一部分（踩过）
  echo "[收尾] ${site}（幂等，补齐站点侧设置）"
  out=$(dc exec -T "$group-bench" bench --site "$site" execute \
        pearnly_core.provisioning.finalize_site 2>&1 | tail -2)
  echo "  $out"
  printf '%s' "$out" | grep -q "home_page" || {
    echo "  [错误] 收尾没成功" >&2
    exit 1
  }
done

echo "[done] 补配置完成；改密验证：node scripts/sample-verify-frontend-slice.mjs"
