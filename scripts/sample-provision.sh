#!/usr/bin/env bash
# 受限开通代理（本机样板版）。
#
# 它在宿主机上跑，按控制面给出的步骤，用**原生 bench 命令**把站点真的建出来。
# 控制面容器里没有 docker、也不给它 docker socket —— 建站这一步只能在外面做。
# 它只认识下面这几个动作，不接受任意命令。
#
# 用法:
#   scripts/sample-provision.sh plan   <发号单号>
#   scripts/sample-provision.sh run    [发号单号]   # 给单号则先 plan 再执行
#   scripts/sample-provision.sh status <发号单号>
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
CONTROL=http://127.0.0.1:8104
CONTROL_HOST=control.localhost
DB_ROOT_PW=sample-root-local-only
ADMIN_PW=admin-local-only

USER_NAME="${PEARNLY_OPS_USER:-earn}"
USER_PASS="${PEARNLY_OPS_PASS:-earn}"

JAR=$(mktemp)
trap 'rm -f "$JAR"' EXIT

d()  { docker --context "$CTX" "$@"; }
dc() { d compose -f "$COMPOSE" "$@"; }

py() { python3 "$@"; }

api_get()  { curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" "$CONTROL/api/method/$1"; }
api_post() { curl -sS -b "$JAR" -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
               -H "X-Frappe-CSRF-Token: $CSRF" -X POST "$CONTROL/api/method/$1" --data-binary "$2"; }

login() {
  curl -sS -c "$JAR" -o /dev/null -X POST "$CONTROL/api/method/login" \
    -H "Host: $CONTROL_HOST" -H 'Content-Type: application/json' \
    --data-binary "$(py -c 'import json,sys; print(json.dumps({"usr":sys.argv[1],"pwd":sys.argv[2]}))' "$USER_NAME" "$USER_PASS")"
  CSRF=$(api_get pearnly_control.issues.api_context \
         | py -c 'import sys,json; print((json.load(sys.stdin).get("message") or {}).get("csrf_token") or "")')
  [ -n "$CSRF" ] || { echo "登录失败或取不到 CSRF 令牌" >&2; exit 1; }
}

# ── 动作实现（只做这几件，别的一概不认）────────────────────────────────────

bench_of()    { echo "$1-bench"; }
site_exists() { dc exec -T "$(bench_of "$1")" bash -lc "test -d /home/frappe/frappe-bench/sites/$2"; }

act_new_site() {  # <group> <site>
  if site_exists "$1" "$2"; then echo "站点已存在，跳过创建（幂等）"; return 0; fi
  dc exec -T "$(bench_of "$1")" bench new-site "$2" \
    --db-host mariadb --db-port 3306 \
    --db-root-username root --db-root-password "$DB_ROOT_PW" \
    --mariadb-user-host-login-scope=% \
    --admin-password "$ADMIN_PW" 2>&1 | tail -4
}

act_install_app() {  # <group> <site> <app>
  # apps.txt 要整份存在该行：镜像里那份没有结尾换行，直接追加会拼串（踩过）
  dc exec -T "$(bench_of "$1")" bash -lc \
    "grep -qx '$3' /home/frappe/frappe-bench/sites/apps.txt || printf '$3\n' >> /home/frappe/frappe-bench/sites/apps.txt"
  if dc exec -T "$(bench_of "$1")" bench --site "$2" list-apps 2>/dev/null | grep -qE "[[:space:]]*$3[[:space:]]"; then
    echo "$3 已安装，跳过（幂等）"; return 0
  fi
  dc exec -T "$(bench_of "$1")" bench --site "$2" install-app "$3" 2>&1 | tail -4
}

act_set_config() {  # <group> <site> <key> <value>
  dc exec -T "$(bench_of "$1")" bench --site "$2" set-config "$3" "$4" >/dev/null
  echo "已写配置 $3=$4"
}

act_restart_web() {  # <group>
  d compose -f "$COMPOSE" restart "$1-web" >/dev/null 2>&1 || true
  echo "已重启 $1-web"
}

act_finalize_site() {  # <group> <site>
  # 新建站点的 desk 首页默认是 setup-wizard，不摘掉就会"点首页一直闪"（踩过）
  local out
  out=$(dc exec -T "$(bench_of "$1")" bench --site "$2" execute \
        pearnly_core.provisioning.finalize_site 2>&1 | tail -2)
  echo "$out"
  printf '%s' "$out" | grep -q "home_page" || return 1
}

act_set_line() {  # <payload-json>
  # 每租户一条 LINE：把该部署自己的渠道凭据写到它自己的站点上。
  # 站点只认自己这条密钥，别的租户的签名验不过 —— 隔离靠凭据本身。
  local payload="$1"
  local configured get
  get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  configured=$(py -c "$get" "$payload" configured)
  if [ "$configured" != "True" ]; then
    echo "SKIPPED: 该租户还没配 LINE 渠道"
    return 0
  fi

  local group site cid secret token liff
  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  cid=$(py -c "$get" "$payload" channel_id)
  secret=$(py -c "$get" "$payload" channel_secret)
  token=$(py -c "$get" "$payload" channel_access_token)
  liff=$(py -c "$get" "$payload" liff_id)

  dc exec -T "$(bench_of "$group")" bash -lc "
    set -e
    B='bench --site $site'
    \$B set-config pearnly_line_channel_id '$cid' >/dev/null
    \$B set-config pearnly_line_channel_secret '$secret' >/dev/null
    \$B set-config pearnly_line_channel_access_token '$token' >/dev/null
    \$B set-config pearnly_line_liff_id '$liff' >/dev/null
    \$B set-config pearnly_line_product '$(py -c "$get" "$payload" product)' >/dev/null
  "
  echo "已写 LINE 渠道配置 channel=$cid"
}

act_set_oidc() {  # <group> <site> <product> <issuer> <client_id> <secret> <redirect>
  # 一次写完身份相关的整组配置。刷新配置后必须重启：gunicorn 是 --preload（踩过）
  dc exec -T "$(bench_of "$1")" bash -lc "
    set -e
    B='bench --site $2'
    \$B set-config pearnly_oidc_issuer '$4' >/dev/null
    \$B set-config pearnly_oidc_client_id '$5' >/dev/null
    \$B set-config pearnly_oidc_client_secret '$6' >/dev/null
    \$B set-config pearnly_oidc_redirect_uri '$7' >/dev/null
    \$B set-config pearnly_product '$3' >/dev/null
  "
  echo "已写身份配置 issuer=$4 client=$5"
}

act_bind_user() {  # <group> <site> <email> <full_name> <subject> <deployment>
  # 用 bench execute 调服务端函数：bench console 会吞多行脚本（踩过）。
  # 本地 User 由开通服务预建（P3 合同）：不按邮箱合并已有账号，也不给本地密码。
  local kwargs out
  kwargs=$(py -c '
import json, sys
email, name, subject, dep = sys.argv[1:5]
print(json.dumps({"email": email, "full_name": name, "subject": subject, "deployment_id": dep}))
' "$3" "$4" "$5" "$6")
  out=$(dc exec -T "$(bench_of "$1")" bench --site "$2" execute \
        pearnly_core.provisioning.ensure_local_user --kwargs "$kwargs" 2>&1 | tail -3)
  echo "$out"
  # 只有服务端确实返回了结果才算成功，否则这一步就是失败 —— 不许报成完成
  printf '%s' "$out" | grep -q "created" || return 1
}

act_mark_active() {  # <deployment>
  api_post pearnly_control.provisioning.api_mark_active \
    "$(py -c 'import json,sys; print(json.dumps({"deployment": sys.argv[1]}))' "$1")" \
    | py -c 'import sys,json; d=json.load(sys.stdin); print("部署状态 ->", (d.get("message") or {}).get("status") or d)'
}

run_step() {  # <action> <payload-json>
  local action="$1" payload="$2"
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  case "$action" in
    new_site)    act_new_site    "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" ;;
    install_app) act_install_app "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" "$(py -c "$get" "$payload" app)" ;;
    set_config)  act_set_config  "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" "$(py -c "$get" "$payload" key)" "$(py -c "$get" "$payload" value)" ;;
    set_oidc)    act_set_oidc    "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" \
                                 "$(py -c "$get" "$payload" product)" "$(py -c "$get" "$payload" issuer)" \
                                 "$(py -c "$get" "$payload" client_id)" "$(py -c "$get" "$payload" client_secret)" \
                                 "$(py -c "$get" "$payload" redirect_uri)" ;;
    restart_web) act_restart_web "$(py -c "$get" "$payload" group)" ;;
    finalize_site) act_finalize_site "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" ;;
    set_line)      act_set_line "$payload" ;;
    bind_user)   act_bind_user   "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" \
                                 "$(py -c "$get" "$payload" email)" "$(py -c "$get" "$payload" full_name)" \
                                 "$(py -c "$get" "$payload" subject)" "$(py -c "$get" "$payload" deployment)" ;;
    mark_active) act_mark_active "$(py -c "$get" "$payload" deployment)" ;;
    *) echo "未知动作：$action" >&2; return 2 ;;
  esac
}

report() {  # report <step_key> <status> <output>
  local body
  body=$(py -c 'import json,sys; print(json.dumps({"step_key":sys.argv[1],"status":sys.argv[2],"output":sys.argv[3]}))' "$1" "$2" "${3:-}")
  api_post pearnly_control.provisioning.api_report "$body" >/dev/null
}

# ── 主流程 ─────────────────────────────────────────────────────────────────

cmd="${1:?用法: $0 plan|run|status …}"; shift || true
login
echo "[登录] 操作人 $(api_get frappe.auth.get_logged_user | py -c 'import sys,json; print(json.load(sys.stdin).get("message"))')"

case "$cmd" in
  plan)
    issue="${1:?给发号单号}"
    py -c 'import json,sys; print(json.dumps({"issue": sys.argv[1]}))' "$issue" \
      | { body=$(cat); api_post pearnly_control.provisioning.api_plan "$body" | py -c '
import sys, json
d = json.load(sys.stdin); m = d.get("message")
if not m:
    print("规划失败:", json.dumps(d, ensure_ascii=False)[:400]); raise SystemExit(1)
print("请求:", m["request"], "| 状态:", m["status"], "| 步骤:", m["counts"]["total"])
for dep in m["deployments"]:
    print("  部署:", dep)
'; }
    ;;

  status)
    issue="${1:?给发号单号}"
    api_get "pearnly_control.provisioning.api_state_for_issue?issue=$issue" | py -c '
import sys, json
d = json.load(sys.stdin); m = d.get("message")
if not m:
    print("没有该发号单的开通请求:", json.dumps(d, ensure_ascii=False)[:300]); raise SystemExit(0)
print("请求:", m["request"], "| 状态:", m["status"], "|", m["counts"])
for s in m["steps"]:
    print(f"   [{s[\"status\"]:7}] {s[\"step_key\"]}")
for dep in m["deployments"]:
    print("  部署:", dep["site_name"], dep["status"], dep["url"] or "")
'
    ;;

  run)
    if [ "${1:-}" ]; then
      "$0" plan "$1" >/dev/null
    fi
    guard=0
    while :; do
      guard=$((guard + 1))
      [ "$guard" -gt 200 ] && { echo "步数异常，停止" >&2; exit 1; }
      step=$(api_get "pearnly_control.provisioning.api_next_steps?limit=1" \
             | py -c 'import sys,json; m=json.load(sys.stdin).get("message") or []; print(json.dumps(m[0]) if m else "")')
      [ -z "$step" ] && { echo "[完成] 没有待办步骤了"; break; }
      key=$(py -c 'import json,sys; print(json.loads(sys.argv[1])["step_key"])' "$step")
      action=$(py -c 'import json,sys; print(json.loads(sys.argv[1])["action"])' "$step")
      payload=$(py -c 'import json,sys; print(json.dumps(json.loads(sys.argv[1])["payload"]))' "$step")
      echo "[执行] $key  ($action)"
      report "$key" running ""
      if out=$(run_step "$action" "$payload" 2>&1); then
        echo "$out" | sed 's/^/         /'
        # 动作可以用 SKIPPED: 开头表示"这一步对本次开通不适用"（例如租户还没配 LINE）。
        # 显式记 skipped，不要含糊成 done —— 看进度的人要能分清"不用做"和"做完了"。
        case "$out" in
          SKIPPED:*)
            report "$key" skipped "$out"
            echo "         （记为 skipped）"
            ;;
          *) report "$key" done "$out" ;;
        esac
      else
        echo "$out" | sed 's/^/         /' >&2
        report "$key" failed "$out"
        echo "[失败] $key —— 停下来，不往下跳" >&2
        exit 1
      fi
    done
    ;;

  *) echo "未知命令：$cmd" >&2; exit 2 ;;
esac
