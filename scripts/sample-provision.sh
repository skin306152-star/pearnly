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
#   scripts/sample-provision.sh retry  <发号单号>   # 把失败步骤重置后重跑
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

# 把 spec 包成 {"spec_json": "<字符串>", "admin_user": "..."}
# 用字符串而不是嵌套 dict 传，理由见 act_set_roles
WRAP='import json, sys; print(json.dumps({"spec_json": sys.argv[1], "admin_user": sys.argv[2]}))'
WRAP2='import json, sys; print(json.dumps({"spec_json": sys.argv[1]}))'

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

act_set_billing() {  # <payload-json>
  # 计费底座的地址 / 令牌 / 自己是哪个租户，写给这个站点。
  # 余额与台账在控制面（三套产品共用一份），站点只带着身份去问，不自己算钱。
  local payload="$1" group site tenant api host token
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  tenant=$(py -c "$get" "$payload" tenant)
  api=$(py -c "$get" "$payload" api)
  host=$(py -c "$get" "$payload" host_header)
  token=$(py -c "$get" "$payload" token)

  if [ -z "$tenant" ] || [ -z "$token" ]; then
    echo "[错误] 计费配置不全（租户或令牌为空）：站点会拿不到余额，OCR 会全被拦下" >&2
    return 1
  fi

  dc exec -T "$(bench_of "$group")" bash -lc "
    set -e
    B='bench --site $site'
    \$B set-config pearnly_tenant '$tenant' >/dev/null
    \$B set-config pearnly_billing_api '$api' >/dev/null
    \$B set-config pearnly_billing_host_header '$host' >/dev/null
    \$B set-config pearnly_billing_token '$token' >/dev/null
  "
  echo "已写计费配置 tenant=$tenant api=$api"
}

act_set_ocr() {  # <payload-json>
  # OCR 是三产品共用的底座，每个站点都要能识别。
  #
  # 服务账号**从宿主机拷进容器**、不放进步骤 payload —— 私钥不进控制面的库。
  # 拷到 sites 外面（/home/frappe/ocr-sa.json）：站点是会被重建/搬迁的，
  # 凭据不该跟着站点目录走。
  local payload="$1" group site sa
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  sa="${PEARNLY_OCR_SA:-$HOME/.config/pearnly/credentials/pearnly-ocr-sa-20260902.json}"

  if [ ! -f "$sa" ]; then
    # 不算失败：站点照样能用，只是识别会 503。如实说明原因，别让人以为是识别坏了。
    echo "SKIPPED: 本机没有 OCR 服务账号（$sa），该站点还不能识别"
    return 0
  fi
  # ⚠️ 用 `cat >` 从 stdin 写进去，**不要**用 docker cp：
  # cp 进来的文件属主是宿主用户（501），容器里的 frappe 用户（1000）读不了，
  # 而且 chmod 也会因为"不是文件属主"而失败 —— 这一步就白做了。
  dc exec -T "$(bench_of "$group")" bash -lc \
    'rm -f /home/frappe/ocr-sa.json && cat > /home/frappe/ocr-sa.json \
     && chmod 600 /home/frappe/ocr-sa.json && head -c 20 /home/frappe/ocr-sa.json >/dev/null' < "$sa" \
    || return 1
  # 每一步都要能失败：`set -e` 在命令替换里是**不生效**的（这个函数就是在 $() 里跑的），
  # 不显式判断的话，写失败也会一路走到最后那句 echo，被记成"完成"（真踩过）。
  dc exec -T "$(bench_of "$group")" bash -lc "
    set -e
    B='bench --site $site'
    \$B set-config pearnly_ocr_backend vertex >/dev/null
    \$B set-config pearnly_ocr_credentials /home/frappe/ocr-sa.json >/dev/null
    \$B set-config pearnly_ocr_project pearnly >/dev/null
    \$B set-config pearnly_ocr_location asia-southeast1 >/dev/null
    grep -q pearnly_ocr_backend sites/$site/site_config.json
  " || return 1
  echo "已写 OCR 配置 backend=vertex project=pearnly"
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

act_set_oidc() {  # <payload-json>
  # 一次写完身份相关的整组配置。刷新配置后必须重启：gunicorn 是 --preload（踩过）
  #
  # 除了本部署**自己的** client，还要写 **realm 级**的两个客户端凭据：
  #   pearnly-admin          —— 用 client_credentials 换管理员令牌（改密要用）
  #   pearnly-password-check —— 只开密码授权，用来校验"当前密码"填得对不对
  #
  # 少写这两个的后果是**"能登录、改不了密码"**（改密会报"取管理令牌失败"，
  # 因为读出来是 None）。样板站当年是手工补的，开通流程从来没下发过 ——
  # 真租户站一直是坏的。这两个是 realm 级、同 realm 所有租户共用一套，
  # 所以由控制面按"开户目标"下发（见 pearnly_control/provisioning.target_credentials）。
  local payload="$1"
  local get group site product issuer cid secret redirect
  local admin_id admin_secret check_id check_secret
  get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'

  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  product=$(py -c "$get" "$payload" product)
  issuer=$(py -c "$get" "$payload" issuer)
  cid=$(py -c "$get" "$payload" client_id)
  secret=$(py -c "$get" "$payload" client_secret)
  redirect=$(py -c "$get" "$payload" redirect_uri)
  admin_id=$(py -c "$get" "$payload" admin_client_id)
  admin_secret=$(py -c "$get" "$payload" admin_client_secret)
  check_id=$(py -c "$get" "$payload" password_check_client_id)
  check_secret=$(py -c "$get" "$payload" password_check_client_secret)

  # 缺了就直接失败，不许"写一半"然后报成功 —— 那种站点能登录但改不了密码，
  # 很难从现象倒推回来（踩过）。
  if [ -z "$admin_id" ] || [ -z "$admin_secret" ] || [ -z "$check_id" ] || [ -z "$check_secret" ]; then
    echo "[错误] 开户目标上缺 realm 级凭据（pearnly-admin / pearnly-password-check），" >&2
    echo "       这样开出来的租户能登录、改不了密码。先在控制面把开户目标补齐。" >&2
    return 1
  fi

  dc exec -T "$(bench_of "$group")" bash -lc "
    set -e
    B='bench --site $site'
    \$B set-config pearnly_oidc_issuer '$issuer' >/dev/null
    \$B set-config pearnly_oidc_client_id '$cid' >/dev/null
    \$B set-config pearnly_oidc_client_secret '$secret' >/dev/null
    \$B set-config pearnly_oidc_redirect_uri '$redirect' >/dev/null
    \$B set-config pearnly_product '$product' >/dev/null
    \$B set-config pearnly_oidc_admin_client_id '$admin_id' >/dev/null
    \$B set-config pearnly_oidc_admin_client_secret '$admin_secret' >/dev/null
    \$B set-config pearnly_oidc_password_check_client_id '$check_id' >/dev/null
    \$B set-config pearnly_oidc_password_check_client_secret '$check_secret' >/dev/null
  "
  # ${cid} 要带花括号：紧跟中文时 shell 会把中文当成变量名的一部分（踩过）
  echo "已写身份配置 issuer=$issuer client=${cid}（含 realm 级改密凭据）"
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

act_set_roles() {  # <payload-json>
  # 把"勾了哪些功能"落成站点上的角色与权限，并把 System Manager 从租户管理员拿走。
  local payload="$1" group site admin out kwargs
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  admin=$(py -c "$get" "$payload" admin_user)
  # spec 以**字符串**传：bench execute 对 --kwargs 做的是 Python eval，
  # 传嵌套 dict 时它自己 eval 失败会报出无关的 NameError（踩过）
  kwargs=$(py -c "$WRAP" "$payload" "$admin")
  out=$(dc exec -T "$(bench_of "$group")" bench --site "$site" execute \
        pearnly_core.provisioning.ensure_feature_roles \
        --kwargs "$kwargs" 2>&1 | tail -3)
  echo "$out"
  printf '%s' "$out" | grep -q "roles" || return 1
}

act_set_workspace() {  # <payload-json>
  # 给租户建"只列他买了什么"的工作区。没有它，拿掉 System Manager 后
  # 租户打开 Desk 会是一片空白（实测）。
  local payload="$1" group site out kwargs
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  group=$(py -c "$get" "$payload" group)
  site=$(py -c "$get" "$payload" site)
  kwargs=$(py -c "$WRAP2" "$payload")
  out=$(dc exec -T "$(bench_of "$group")" bench --site "$site" execute \
        pearnly_core.provisioning.ensure_product_workspace \
        --kwargs "$kwargs" 2>&1 | tail -3)
  echo "$out"
  printf '%s' "$out" | grep -q "workspace" || return 1
}

act_mark_active() {  # <deployment>
  api_post pearnly_control.provisioning.api_mark_active \
    "$(py -c 'import json,sys; print(json.dumps({"deployment": sys.argv[1]}))' "$1")" \
    | py -c 'import sys,json; d=json.load(sys.stdin); print("部署状态 ->", (d.get("message") or {}).get("status") or d)'
}

act_drop_site() {  # <group> <site>
  # 拆掉一个租户站点：**不备份**（用户 2026-09-19 定："删除就是清空所有"）。
  #
  # 幂等：站点不在了就算做完 —— 重复点"删除租户"不该报错。
  # 但"删干净"要**复核**：drop-site 删库，站点目录（含 private/files）得自己删，
  # 两样都确认真的没了才算这一步完成（少一样就是"看起来删了"）。
  local group="$1" site="$2" left
  if ! site_exists "$group" "$site"; then
    echo "${site} 已经不在（跳过）"
    return 0
  fi

  dc exec -T "$(bench_of "$group")" bench drop-site "$site" \
    --db-root-username root --db-root-password "$DB_ROOT_PW" --force --no-backup 2>&1 | tail -3
  # 站点目录连同它的文件一起删
  dc exec -T "$(bench_of "$group")" bash -lc \
    "rm -rf /home/frappe/frappe-bench/sites/${site}" || return 1
  # ⚠️ `bench drop-site` 不是"删掉"，是**搬到 archived/sites** 当备份。
  # 用户要的是"不备份、清空"，所以那份归档也要一起删（不删就等于留了一份完整副本）。
  dc exec -T "$(bench_of "$group")" bash -lc \
    "rm -rf /home/frappe/frappe-bench/archived/sites/${site}" || return 1

  left=$(dc exec -T "$(bench_of "$group")" bash -lc \
    "test -d /home/frappe/frappe-bench/sites/${site} && echo dir" 2>/dev/null | tr -d '\r')
  [ -z "$left" ] || { echo "[错误] 站点目录还在：${site}" >&2; return 1; }
  left=$(dc exec -T "$(bench_of "$group")" bash -lc \
    "test -d /home/frappe/frappe-bench/archived/sites/${site} && echo archive" 2>/dev/null | tr -d '\r')
  [ -z "$left" ] || { echo "[错误] 归档副本还在：${site}" >&2; return 1; }
  left=$(dc exec -T "$(bench_of "$group")" bash -lc \
    "mysql -h mariadb -uroot -p'${DB_ROOT_PW}' -N -e \"show databases like '${site}'\"" 2>/dev/null | tr -d '\r')
  [ -z "$left" ] || { echo "[错误] 数据库还在：${left}" >&2; return 1; }
  echo "${site} 已删除（站点目录 + 数据库，未备份）"
}

act_purge_control() {  # <payload-json>
  # 外部清理：删身份服务里的账号与这个部署的 OIDC 客户端。
  # 只有控制面知道它们是谁（subject 记在发号单上），所以这一步是"让控制面去做"。
  local payload="$1" request out
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  request=$(py -c "$get" "$payload" request)
  out=$(api_post pearnly_control.teardown.api_purge_control \
    "$(py -c 'import json,sys; print(json.dumps({"request": sys.argv[1]}))' "$request")")
  printf '%s' "$out" | py -c '
import sys, json
d = json.load(sys.stdin)
m = d.get("message") or {}
if not m:
    print("清理失败:", json.dumps(d, ensure_ascii=False)[:200])
    raise SystemExit(1)
print("账号 删 %d 个；保留 %d 个（别的租户还在用）；OIDC 客户端 删 %d 个"
      % (len(m.get("removed_users") or []), len(m.get("kept_users") or []),
         len(m.get("removed_oidc_clients") or [])))
'
}

run_step() {  # <action> <payload-json>
  local action="$1" payload="$2"
  local get='import json,sys; print(json.loads(sys.argv[1]).get(sys.argv[2],""))'
  case "$action" in
    new_site)    act_new_site    "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" ;;
    install_app) act_install_app "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" "$(py -c "$get" "$payload" app)" ;;
    set_config)  act_set_config  "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" "$(py -c "$get" "$payload" key)" "$(py -c "$get" "$payload" value)" ;;
    set_oidc)    act_set_oidc    "$payload" ;;
    set_billing) act_set_billing "$payload" ;;
    set_ocr)     act_set_ocr     "$payload" ;;
    restart_web) act_restart_web "$(py -c "$get" "$payload" group)" ;;
    finalize_site) act_finalize_site "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" ;;
    set_line)      act_set_line "$payload" ;;
    bind_user)   act_bind_user   "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" \
                                 "$(py -c "$get" "$payload" email)" "$(py -c "$get" "$payload" full_name)" \
                                 "$(py -c "$get" "$payload" subject)" "$(py -c "$get" "$payload" deployment)" ;;
    set_roles)   act_set_roles "$payload" ;;
    set_workspace) act_set_workspace "$payload" ;;
    mark_active) act_mark_active "$(py -c "$get" "$payload" deployment)" ;;
    drop_site)     act_drop_site     "$(py -c "$get" "$payload" group)" "$(py -c "$get" "$payload" site)" ;;
    purge_control) act_purge_control "$payload" ;;
    *) echo "未知动作：$action" >&2; return 2 ;;
  esac
}

report() {  # report <step_key> <status> <output>
  local body
  body=$(py -c 'import json,sys; print(json.dumps({"step_key":sys.argv[1],"status":sys.argv[2],"output":sys.argv[3]}))' "$1" "$2" "${3:-}")
  api_post pearnly_control.provisioning.api_report "$body" >/dev/null
}

has_pending() {  # 有没有待办步骤（只问一句，不打印）
  local one
  one=$(api_get "pearnly_control.provisioning.api_next_steps?limit=1" \
        | py -c 'import sys,json; m=json.load(sys.stdin).get("message") or []; print("yes" if m else "")')
  [ -n "$one" ]
}

# 把所有待办步骤跑完。没有待办就返回 0；有一步失败就立刻返回 1（不往下跳）。
# `run` 与 `watch` 共用这一份，免得两条路径的执行逻辑各长一棵。
run_pending() {
  local guard=0 step key action payload out
  while :; do
    guard=$((guard + 1))
    [ "$guard" -gt 200 ] && { echo "步数异常，停止" >&2; return 1; }
    step=$(api_get "pearnly_control.provisioning.api_next_steps?limit=1" \
           | py -c 'import sys,json; m=json.load(sys.stdin).get("message") or []; print(json.dumps(m[0]) if m else "")')
    [ -z "$step" ] && { echo "[完成] 没有待办步骤了"; return 0; }
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
      return 1
    fi
  done
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
# 注意：f-string 的表达式里不能出现反斜杠（Python 3.12 之前），
# 所以这里用 .format 而不是 f-string —— 之前写成 f-string 直接语法错，整个 status 命令是坏的。
    print("   [{:7}] {}".format(s["status"], s["step_key"]))
for dep in m["deployments"]:
    print("  部署:", dep["site_name"], dep["status"], dep["url"] or "")
'
    ;;

  run)
    if [ "${1:-}" ]; then
      "$0" plan "$1" >/dev/null
    fi
    run_pending || exit 1
    ;;

  watch)
    # 一直盯着控制面的待办：谁在表单上点了「执行开通」，这里几秒内就开始建站。
    #
    # ⚠️ 干活那一步**另起一个进程**（`$0 run`），不是在本进程里直接调 run_pending。
    # 原因：bash 是边读边执行脚本的，跑着的 watcher 一直握着启动时那份内容 ——
    # 改了执行器代码不重启它就不生效，表现是"代码明明改了、行为还是旧的"
    # （这个坑我踩了两次）。每轮起新进程，改完立刻生效，不用记得重启。
    POLL="${PEARNLY_WATCH_POLL:-3}"
    echo "[盯梢] 每 ${POLL}s 看一次待办（每轮起新进程，改代码即生效）"
    while :; do
      if has_pending; then "$0" run || echo "[盯梢] 有步骤失败，停在失败处（修好后 $0 retry <单号>）" >&2; fi
      sleep "$POLL"
    done
    ;;

  retry)
    issue="${1:?给发号单号}"
    # --all：连 skipped 一起补跑。LINE 这种"先开通、后配渠道"的步骤要靠它 ——
    # 配好渠道之后跑一次，那一步才会真的把密钥写到站点上。
    include_skipped=0
    [ "${2:-}" = "--all" ] && include_skipped=1
    req=$(api_get "pearnly_control.provisioning.api_request_of_issue?issue=$issue" \
          | py -c 'import sys,json; print(json.load(sys.stdin).get("message") or "")')
    [ -n "$req" ] || { echo "该发号单还没有开通请求" >&2; exit 1; }
    py -c 'import json,sys; print(json.dumps({"request": sys.argv[1], "include_skipped": int(sys.argv[2])}))' \
      "$req" "$include_skipped" \
      | { body=$(cat); api_post pearnly_control.provisioning.api_retry_failed "$body" \
          | py -c '
import sys, json
m = (json.load(sys.stdin).get("message") or {})
print("重置为待执行:", m.get("reset") or "（没有失败步骤）")
'; }
    exec "$0" run
    ;;

  *) echo "未知命令：$cmd" >&2; exit 2 ;;
esac
