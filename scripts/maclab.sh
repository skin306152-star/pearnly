#!/usr/bin/env bash
# 本机 Mac 实验栈（maclab）：ERP、Cowork 两套服务端栈在本机并行跑，局域网里的测试机用 CC 桌面版各连一套（写账链路验收）。
# 栈定义只用 infrastructure/production/compose.yml 一份，Mac 的差异全在 infrastructure/maclab/override.yml。说明见
# infrastructure/maclab/README.md。
#
#   scripts/maclab.sh up <erp|cowork>          起栈（第一次自动建站点与测试账号）→ 收尾（迁移、清缓存、运行配置）→ ping
#   scripts/maclab.sh down <erp|cowork>        停栈：容器删、卷保留
#   scripts/maclab.sh status                   两套栈的容器、端口、实时源码状态
#   scripts/maclab.sh init <erp|cowork>        （重新）建站点、计费、测试账号（幂等）
#   scripts/maclab.sh finish <erp|cowork>      只做收尾
#   scripts/maclab.sh live <erp|cowork>        把 MACLAB_SRC（默认本工作树）的产品代码挂到镜像上并重启本栈（同 testenv live）
#   scripts/maclab.sh live-off <erp|cowork>    摘掉实时源码，回到纯镜像
#   scripts/maclab.sh mint <erp|cowork> [电脑名]   签一台测试机的配对码（只打到标准输出）
#   scripts/maclab.sh probe <erp|cowork> [--leave] 从标准输入读配对码，按 CC 的写法经局域网地址发一次心跳
#   scripts/maclab.sh reset <erp|cowork> --yes 删本栈的全部卷（站点、库、队列）；要显式 --yes
#   scripts/maclab.sh logs <erp|cowork> [服务…] / ps <erp|cowork>
#
# 边界：只碰项目名 pearnly-maclab-<产品> 的容器、卷、网络；不用全局清理命令；不连远程机器；口令只在本机
# ~/.config/pearnly-maclab/<产品>.env（600）。
set -euo pipefail

repo=$(cd "$(dirname "$0")/.." && pwd)
config_dir=${MACLAB_CONFIG_DIR:-$HOME/.config/pearnly-maclab}
state_dir=${MACLAB_STATE_DIR:-$HOME/.pearnly-maclab}
src=${MACLAB_SRC:-$repo}
context=${MACLAB_DOCKER_CONTEXT:-colima-pearnly}
colima_profile=${MACLAB_COLIMA_PROFILE:-pearnly}
default_image=ghcr.io/skin306152-star/pearnly/frappe@sha256:054aa603db1722c28381e65cb0517175a0dfa3a868c9df5d1e276fe870f261ff
LIVE_APPS=(pearnly_core pearnly_cowork pearnly_dms pearnly_erp_portal pearnly_control)
MAX_LOAD=${MACLAB_MAX_LOAD:-20}
MIN_FREE_GB=${MACLAB_MIN_FREE_GB:-8}

die() { echo "maclab: $*" >&2; exit 2; }

resolve_app() {
  case "${1:-}" in erp|cowork) app=$1 ;; *) die "要指定 erp 或 cowork" ;; esac
  project=pearnly-maclab-$app
  case "$app" in
    erp) port=${MACLAB_ERP_PORT:-18991}; frappe_app=pearnly_erp_portal; user=${MACLAB_ERP_USER:-acc.erp@maclab.test} ;;
    cowork) port=${MACLAB_COWORK_PORT:-18992}; frappe_app=pearnly_cowork; user=${MACLAB_COWORK_USER:-acc.cowork@maclab.test} ;;
  esac
  site=$app.maclab.localhost
  env_file=$config_dir/$app.env
  live_dir=$state_dir/live/$app
  live_override=$state_dir/$app-live.yml
  bench=$project-bench-1
  billing=$project-billing-web-1
  export MACLAB_PROJECT=$project MACLAB_REPO=$repo MACLAB_PORT=$port
  [[ -n "${MACLAB_IMAGE:-}" ]] && export PEARNLY_IMAGE=$MACLAB_IMAGE
  return 0
}

check_context() {
  local now; now=$(docker context show 2>/dev/null || true)
  [[ "$now" == "$context" ]] || die "docker 当前 context 是 '$now'，要 '$context'（MACLAB_DOCKER_CONTEXT 可改）"
}

lan_ip() {
  [[ -n "${MACLAB_LAN_IP:-}" ]] && { echo "$MACLAB_LAN_IP"; return; }
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1
}

secret() { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 32; }

ensure_env() {
  # 口令只生成一次（数据库卷里记着它）；文件 600，只在本机。
  mkdir -p "$config_dir"; chmod 700 "$config_dir"
  [[ -s "$env_file" ]] && return 0
  umask 077
  cat > "$env_file" <<EOF
# 本机 Mac 实验栈 $project 的运行参数（scripts/maclab.sh 生成；口令只在本机）
PEARNLY_IMAGE=$default_image
PEARNLY_DEPLOYMENT=acceptance
DB_ROOT_PASSWORD=$(secret)
KEYCLOAK_DB_PASSWORD=$(secret)
KEYCLOAK_ADMIN_PASSWORD=$(secret)
AUTH_HOST=auth.maclab.localhost
PEARNLY_MARIADB_MEM=1g
PEARNLY_MARIADB_BUFFER_POOL=256M
PEARNLY_ERP_OCR_WORKERS=1
PEARNLY_ERP_OCR_MEM=768m
PEARNLY_COWORK_OCR_WORKERS=1
PEARNLY_COWORK_OCR_MEM=768m
PEARNLY_COWORK_LONG_WORKERS=1
PEARNLY_COWORK_LONG_MEM=768m
MACLAB_ADMIN_PASSWORD=$(secret)
MACLAB_USER_PASSWORD=$(secret)
EOF
  chmod 600 "$env_file"
  echo "· 运行参数已生成：$env_file"
}

env_value() { grep -E "^$1=" "$env_file" | head -1 | cut -d= -f2-; }

dc() {
  local files=(-f "$repo/infrastructure/production/compose.yml" -f "$repo/infrastructure/maclab/override.yml")
  [[ -f "$live_override" ]] && files+=(-f "$live_override")
  docker compose --project-name "$project" --project-directory "$repo/infrastructure/production" \
    --env-file "$env_file" "${files[@]}" --profile "$app" "$@"
}

preflight() {
  # 本机与别的窗口共用 colima：负载高或内存不够时不起（任务书口径：1 分钟负载 > 20 或可用 < 8G）。
  local load free
  load=$(sysctl -n vm.loadavg | awk '{print $2}')
  free=$(colima ssh -p "$colima_profile" -- free -g 2>/dev/null | awk '/^Mem:/{print $7}')
  echo "· 本机 1 分钟负载 $load；colima 可用内存 ${free:-?}G"
  awk -v l="$load" -v m="$MAX_LOAD" 'BEGIN{exit !(l > m)}' && die "负载 $load 高于 $MAX_LOAD，先不起"
  [[ -n "$free" ]] && (( free < MIN_FREE_GB )) && die "colima 可用内存 ${free}G 不足 ${MIN_FREE_GB}G，先不起"
  local holder
  holder=$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E "[:.]$port->" | grep -v "^$project-" || true)
  [[ -z "$holder" ]] || die "端口 $port 已被别的容器占用：$holder"
  if ! docker ps --format '{{.Names}}' | grep -q "^$project-" && lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
    die "端口 $port 已被本机别的进程监听"
  fi
  docker image inspect "$(env_value PEARNLY_IMAGE)" >/dev/null 2>&1 || die "本机没有镜像 $(env_value PEARNLY_IMAGE)（不自动拉）"
}

ensure_volumes() {
  # production/compose.yml 把站点卷声明为 external：只建本栈要用的两个（账本 + 本产品）。
  local v
  for v in sites-billing "sites-$app"; do docker volume create "${project}_$v" >/dev/null; done
}

wait_running() {
  local deadline=$((SECONDS + 300)) bad
  while (( SECONDS < deadline )); do
    bad=$(dc ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null | awk '$2 != "running" || ($3 != "" && $3 != "healthy")')
    [[ -z "$bad" ]] && return 0
    sleep 5
  done
  echo "超时：$bad" >&2; return 1
}

in_bench() { docker exec -i "$@"; }

app_services() { dc config --services | grep -E "^($app-|billing-web$)"; }

restart_app() {
  # 站点配置有缓存：改了计费 / 站点配置后重启本产品的容器与账本；前端最后单独重启（nginx 启动时解析上游地址，见 testenv run.sh）。
  dc restart $(app_services | grep -v -E "^($app-frontend|$app-bench)$") >/dev/null
  dc restart "$app-frontend" >/dev/null
}

finish() {
  in_bench -e TESTENV_GROUP="$app" -e MACLAB_SKIP_MIGRATE="${1:-0}" -w /home/frappe/frappe-bench/sites "$bench" \
    /home/frappe/frappe-bench/env/bin/python /workspace/maclab/finish.py
}

init() {
  local db_pw admin_pw out lab
  db_pw=$(env_value DB_ROOT_PASSWORD); admin_pw=$(env_value MACLAB_ADMIN_PASSWORD)
  echo "== 账本（billing.internal）"
  in_bench "$billing" /home/frappe/frappe-bench/env/bin/python /workspace/testenv/billing-init.py
  # 租户令牌只在这里从一个容器进程传给另一个：截下那一行，不回显
  out=$(MACLAB_DB_ROOT_PASSWORD=$db_pw in_bench -e MACLAB_DB_ROOT_PASSWORD "$billing" \
    /home/frappe/frappe-bench/env/bin/python /workspace/maclab/init.py billing "$app")
  printf '%s\n' "$out" | grep -v '^LAB_BILLING '
  lab=$(printf '%s\n' "$out" | sed -n 's/^LAB_BILLING //p' | tail -1)
  [[ -n "$lab" ]] || die "账本初始化失败"
  echo "== 站点 $site"
  TESTENV_DB_ROOT_PASSWORD=$db_pw TESTENV_ADMIN_PASSWORD=$admin_pw in_bench \
    -e TESTENV_SITE="$site" -e TESTENV_GROUP="$app" -e TESTENV_APP="$frappe_app" \
    -e TESTENV_DB_ROOT_PASSWORD -e TESTENV_ADMIN_PASSWORD "$bench" \
    /home/frappe/frappe-bench/env/bin/python /workspace/testenv/site-init.py | grep -v '^PASS bench' || true
  docker exec "$bench" test -f "/home/frappe/frappe-bench/sites/$site/site_config.json" || die "站点没建出来"
  echo "== 测试账号与功能"
  if [[ "$app" == erp ]]; then
    LAB_BILLING=$lab MACLAB_USER_PASSWORD=$(env_value MACLAB_USER_PASSWORD) in_bench \
      -e MACLAB_SITE="$site" -e MACLAB_PUBLIC="$site:$port" -e MACLAB_USER="$user" -e LAB_BILLING -e MACLAB_USER_PASSWORD \
      "$bench" /home/frappe/frappe-bench/env/bin/python /workspace/maclab/init.py erp
  else
    # Cowork：照测试机 lab-init 的 site 段（testenv/cowork-lab.py：计费、全部功能、管理员、工作空间、ERP 推送总闸），再补本栈差异
    LAB_BILLING=$lab LAB_ADMIN_PASSWORD=$(env_value MACLAB_USER_PASSWORD) in_bench \
      -e TESTENV_SITE="$site" -e LAB_BILLING -e LAB_ADMIN_EMAIL="$user" -e LAB_ADMIN_PASSWORD "$bench" \
      /home/frappe/frappe-bench/env/bin/python /workspace/testenv/cowork-lab.py site
    in_bench -e MACLAB_SITE="$site" -e MACLAB_PUBLIC="$site:$port" "$bench" \
      /home/frappe/frappe-bench/env/bin/python /workspace/maclab/init.py cowork
  fi
}

ping_lan() {
  # 网页进程没有健康检查：重启后要等 gunicorn 预加载完（Rosetta 下十几秒），最多等 120 秒
  local ip code deadline=$((SECONDS + 120)); ip=$(lan_ip)
  while :; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 -H "Host: $site:$port" "http://$ip:$port/api/method/ping" || true)
    [[ "$code" == 200 ]] || (( SECONDS >= deadline )) && break
    sleep 3
  done
  echo "· ping http://$ip:$port（Host: $site:$port）→ $code"
  [[ "$code" == 200 ]]
}

up() {
  check_context
  ensure_env
  preflight
  local started=$SECONDS
  ensure_volumes
  dc up -d --wait mariadb redis-cache redis-queue billing-web
  dc up -d
  if docker exec "$bench" test -f "/home/frappe/frappe-bench/sites/$site/site_config.json"; then
    finish 0
  else
    init
    finish 1
  fi
  restart_app
  wait_running
  echo "· 起栈用时 $((SECONDS - started)) 秒"
  ping_lan
  dc ps
}

live_sync() {
  # 同 testenv live：被测工作树（含未提交改动）的产品代码 + 镜像里有、仓库不跟踪的构建产物，原地同步到 $live_dir，挂进本产品
  # 全部容器（只读）。表结构 / 补丁 / hooks 变了才迁移，否则只清缓存。
  check_context
  local list hash stamp=$state_dir/live/$app.migrate staging image fresh=0
  list=$(cd "$src/apps" && git ls-files -co --exclude-standard -- "${LIVE_APPS[@]}" \
    | while IFS= read -r file; do [[ -f "$file" ]] && printf '%s\n' "$file"; done)
  [[ -n "$list" ]] || die "$src/apps 下没有产品代码"
  hash=$(cd "$src/apps" && printf '%s\n' "$list" | grep -E '/doctype/.*\.json$|/patches\.txt$|/patches/.*\.py$|/hooks\.py$|/fixtures/' \
    | LC_ALL=C sort | while IFS= read -r file; do printf '%s\n' "$file"; cat "$file"; done | shasum -a 256 | cut -c1-16)
  staging=$(mktemp -d "${TMPDIR:-/tmp}/maclab-live.XXXXXX")
  (cd "$src/apps" && printf '%s\n' "$list" | COPYFILE_DISABLE=1 tar -cf - -T -) | tar -xf - -C "$staging"
  image=$(env_value PEARNLY_IMAGE); [[ -n "${MACLAB_IMAGE:-}" ]] && image=$MACLAB_IMAGE
  docker run --rm --entrypoint sh "$image" -c 'cd /home/frappe/frappe-bench/apps && tar -cf - $(ls -d \
    pearnly_core/pearnly_core/public/pearnly-app pearnly_core/pearnly_core/public/assistant \
    pearnly_control/pearnly_control/public/icons 2>/dev/null)' | tar -xf - -C "$staging" -k 2>/dev/null || true
  mkdir -p "$live_dir"
  rsync -a --delete "$staging/" "$live_dir/"
  rm -rf "$staging"
  chmod -R a+rX "$live_dir"
  [[ -f "$live_override" ]] || fresh=1
  {
    echo "# 由 scripts/maclab.sh live 生成：把 $live_dir 下的产品代码挂到镜像的 App 目录上（只读）。"
    echo "services:"
    local service pkg
    for service in $(app_services); do
      echo "  $service:"
      echo "    volumes:"
      for pkg in "${LIVE_APPS[@]}"; do echo "      - $live_dir/$pkg/$pkg:/home/frappe/frappe-bench/apps/$pkg/$pkg:ro"; done
    done
  } > "$live_override.tmp"
  mv "$live_override.tmp" "$live_override"
  echo "· 源码：$(git -C "$src" rev-parse --short HEAD)$(git -C "$src" diff --quiet HEAD -- apps || echo ' + 未提交改动')（$src）"
  dc up -d --no-deps $(app_services) >/dev/null
  (( fresh )) || dc restart $(app_services | grep -v -E "^$app-frontend$") >/dev/null
  dc restart "$app-frontend" >/dev/null
  if [[ "$(cat "$stamp" 2>/dev/null)" == "$hash" ]]; then
    echo "· 表结构 / 补丁 / hooks 没变：只清缓存"; finish 1
  else
    echo "· 表结构 / 补丁 / hooks 有变化：迁移 + 清缓存"; finish 0 && echo "$hash" > "$stamp"
  fi
  wait_running
}

live_off() {
  check_context
  rm -f "$live_override" "$state_dir/live/$app.migrate"
  dc up -d --no-deps $(app_services) >/dev/null
  dc restart "$app-frontend" >/dev/null
  finish 0
  wait_running
}

mint() {
  check_context
  local label=${1:-maclab-$(date +%m%d%H%M)}
  # 只把这两行交给调用方（标准输出）；别的输出走标准错误
  in_bench -e MACLAB_SITE="$site" -e MACLAB_PUBLIC="$site:$port" -e MACLAB_USER="$user" -e MACLAB_LABEL="$label" \
    -e MACLAB_REPLACE="${MACLAB_REPLACE:-0}" "$bench" /home/frappe/frappe-bench/env/bin/python /workspace/maclab/mint.py "$app"
}

probe() {
  python3 "$repo/infrastructure/maclab/probe.py" --via "http://$(lan_ip):$port" "$@"
}

status() {
  check_context
  local a
  for a in erp cowork; do
    resolve_app "$a"
    echo "== $project（网页入口 0.0.0.0:$port → $site；实时源码：$([[ -f "$live_override" ]] && echo 开 || echo 关)）"
    docker ps -a --filter "label=com.docker.compose.project=$project" --format '   {{.Names}}  {{.Status}}  {{.Ports}}'
    docker volume ls --filter "name=^${project}_" --format '   卷 {{.Name}}'
  done
}

down() {
  check_context
  dc down --remove-orphans
  docker ps -a --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' | grep . && die "还有残留容器" || echo "· $project 已停，卷保留"
}

reset() {
  check_context
  [[ "${1:-}" == "--yes" ]] || die "reset 会删 $project 的全部卷（站点、数据库、队列），确认请加 --yes"
  dc down --remove-orphans || true
  local v
  for v in sites-billing "sites-$app" database queue; do docker volume rm "${project}_$v" >/dev/null 2>&1 && echo "· 删卷 ${project}_$v" || true; done
  rm -rf "$live_dir" "$live_override" "$state_dir/live/$app.migrate"
  echo "· $project 已重置（运行参数 $env_file 保留）"
}

cmd=${1:-}
case "$cmd" in
  status) status ;;
  up|down|init|finish|live|live-off|mint|probe|reset|logs|ps)
    resolve_app "${2:-}"; shift 2
    [[ "$cmd" == probe ]] || [[ -s "$env_file" ]] || [[ "$cmd" == up ]] || die "$env_file 不存在：先 up $app"
    case "$cmd" in
      up) up ;;
      down) down ;;
      init) check_context; init; finish 0; restart_app ;;
      finish) check_context; finish 0 ;;
      live) live_sync ;;
      live-off) live_off ;;
      mint) mint "$@" ;;
      probe) probe "$@" ;;
      reset) reset "$@" ;;
      logs) check_context; dc logs --tail "${MACLAB_LOG_LINES:-100}" "$@" ;;
      ps) check_context; dc ps -a ;;
    esac ;;
  *) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 2 ;;
esac
