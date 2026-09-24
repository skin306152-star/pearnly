#!/usr/bin/env bash
# 测试机（WSL 内）执行入口。Mac 侧 scripts/testenv.sh 只负责同步与转发到这里，
# 避免多层引号把命令打散。
#
# **一个 app 一套栈**：`TESTENV_APP=dms` 时项目名是 `pearnly-testenv-dms`，它有自己的
# MariaDB、Redis、站点卷、网络与容器；ERP / Cowork 同理。这样测一个产品不会碰到另一个
# （改坏 DMS 不影响 ERP），也不会因为共用一套库互相拖累。
#
# 起栈会自动做三件以前要人工补的事（见 finish-start.py）：升级站点库、清 Frappe 缓存、
# 写 OCR 容量与队列配置。缺前两件会表现为"新功能看不见 / 表不存在"。
set -euo pipefail

root=/opt/pearnly-testenv
env_file=$root/env
compose="$root/deploy/infrastructure/production/compose.yml"
override="$root/deploy/infrastructure/testenv/override.yml"
site_suffix=${TESTENV_SITE_SUFFIX:-test.localhost}

# 一个 app 一套栈：项目名带 app 名，卷、网络、库、Redis 全部独立。
# 目的：测一个产品不会碰到另一个（改坏 DMS 不影响 ERP，反之亦然）。
app=${TESTENV_APP:-dms}
case "$app" in dms|erp|cowork) ;; *) echo "unknown app $app (expected dms|erp|cowork)" >&2; exit 2 ;; esac
project=${TESTENV_PROJECT:-pearnly-testenv-$app}
export TESTENV_PROJECT="$project"

[[ -s "$env_file" ]] || { echo "Missing $env_file"; exit 2; }
dc() { docker compose --project-name "$project" --env-file "$env_file" -f "$compose" -f "$override" --profile "$app" "$@"; }

ensure_volumes() {
  # 正式 compose 把这些卷声明为 external；只建本栈要用的那两个（站点 + 账本）。
  for v in sites-billing "sites-$app"; do
    docker volume create "${project}_${v}" >/dev/null
  done
}

finish_start() {
  # 换镜像之后有两件事必须自动做，否则用户看到的是"新功能不见了"或"表不存在"：
  #   ① 把站点库升级到当前镜像的 DocType（新表、新字段）
  #   ② 清掉 Frappe 的 hooks/资源缓存（缓存里存着旧的资源版本号，页面会一直加载旧 JS）
  # 顺带写入运行配置：OCR 容量存储与专用队列，缺了会让 LINE 识别在入队处直接失败。
  # 详见 docs/development/test-environment.md 的"开始测试前自动完成的事"。
  dc run --rm --no-deps -T -e TESTENV_GROUP="$app" "$app-web" \
    /home/frappe/frappe-bench/env/bin/python /workspace/testenv/finish-start.py
}

stop_other_stacks() {
  # 端口是固定分配的（前端 8183/8182/8181、账本 8184），所以同一时间只能跑一套。
  # 起本栈前把别的测试栈停掉：容器删掉、**卷保留**，数据不丢，下次 up 就回来。
  local name
  for name in $(docker ps --format '{{.Label "com.docker.compose.project"}}' | sort -u); do
    [[ -z "$name" || "$name" == "$project" ]] && continue
    case "$name" in
      pearnly-testenv|pearnly-testenv-*)
        echo "· 先停另一套测试栈 $name（卷保留，数据不丢）"
        docker ps -aq --filter "label=com.docker.compose.project=$name" | xargs -r docker rm -f >/dev/null
        docker network rm "${name}_runtime" >/dev/null 2>&1 || true
        ;;
    esac
  done
}

wait_healthy() {
  local deadline=$((SECONDS + 300))
  while (( SECONDS < deadline )); do
    local bad
    bad=$(dc ps -a --format '{{.Service}} {{.State}} {{.Health}}' 2>/dev/null \
      | awk '$2 != "running" || ($3 != "" && $3 != "healthy")')
    [[ -z "$bad" ]] && { echo 'all services running and healthy'; return 0; }
    sleep 5
  done
  echo 'timed out waiting for services'; dc ps; return 1
}

init_sites() {
  for group in "$@"; do
    case "$group" in
      # 用局部变量：`app` 是本脚本的顶层「产品名」，这里再赋值会把它覆盖成 Frappe 应用名，
      # 后面的 finish_start / dc（--profile）就全找错对象。
      dms) local frappe_app=pearnly_dms ;;
      erp) local frappe_app=pearnly_erp_portal ;;
      cowork) local frappe_app=pearnly_cowork ;;
      *) echo "unknown group $group"; exit 2 ;;
    esac
    site="${group}.${site_suffix}"
    echo "== $group -> $site"
    dc run --rm --no-deps -T \
      -e TESTENV_SITE="$site" -e TESTENV_GROUP="$group" -e TESTENV_APP="$frappe_app" \
      -e TESTENV_DB_ROOT_PASSWORD="$(grep -E '^DB_ROOT_PASSWORD=' "$env_file" | cut -d= -f2-)" \
      -e TESTENV_ADMIN_PASSWORD="${TESTENV_ADMIN_PASSWORD:-testenv-admin}" \
      "$group-web" \
      /home/frappe/frappe-bench/env/bin/python /workspace/testenv/site-init.py
  done
}

case "${1:-}" in
  volumes)
    ensure_volumes
    docker volume ls --filter "name=${project}_" --format '{{.Name}}'
    ;;
  pull)
    image=$(grep -E '^PEARNLY_IMAGE=' "$env_file" | cut -d= -f2-)
    [[ "$image" == *@sha256:* ]] || { echo "PEARNLY_IMAGE must be a digest, got: $image"; exit 2; }
    docker pull "$image"
    docker image inspect "$image" --format 'revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
    ;;
  config)
    dc config --quiet && echo 'compose config OK'
    ;;
  base)
    # 基础层只用公共镜像，用来单独验证卷、网络与健康检查。
    dc up -d --wait mariadb redis-cache redis-queue
    dc ps
    ;;
  up)
    stop_other_stacks
    ensure_volumes
    # keycloak 在 keycloak profile 里，本命令不带它（测试租户的身份用线上 Keycloak）。
    dc up -d --wait mariadb redis-cache redis-queue billing-web
    dc up -d
    # 产品容器需要站点存在才能常驻；先建/迁移站点，再等健康。
    init_sites "$app"
    finish_start
    wait_healthy
    dc ps
    ;;
  ps) dc ps -a ;;
  # 导入/换镜像之后单独再跑一次收尾（升级站点库 + 清缓存）。见 finish-start.py。
  finish) finish_start ;;
  check)
    # 本栈的站点在自己的容器里读（每个 app 一套卷，不能跨栈看）。
    echo "== $app"
    dc run --rm --no-deps -T "$app-web" \
      /home/frappe/frappe-bench/env/bin/python /workspace/testenv/check.py
    dc ps -a
    ;;
  smoke)
    # 在测试机内部按 Host 头访问前端入口，证明应用真的在应答（不经过 Mac 侧）。
    case "$app" in
      dms) entries=("dms.${site_suffix}:8183" "testdms.pearnly.com:8183") ;;
      erp) entries=("erp.${site_suffix}:8182" "testerp.pearnly.com:8182") ;;
      cowork) entries=("cowork.${site_suffix}:8181") ;;
    esac
    for entry in "${entries[@]}" "billing.internal:8184"; do
      host_name=${entry%%:*}; port=${entry##*:}
      echo "== $host_name:$port"
      for path in / /login /api/method/ping; do
        code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 30 \
          -H "Host: $host_name" "http://127.0.0.1:$port$path" || true)
        echo "   $path -> ${code:-no-response}"
        [[ "$code" == 2* || "$code" == 3* ]] || curl -s --max-time 30 -H "Host: $host_name" \
          "http://127.0.0.1:$port$path" | tail -c 120 | tr -d '\n' | sed 's/^/      /'
      done
    done
    ;;
  tunnel)
    shift
    # 传参给设备侧脚本：tunnel.sh <站点> <端口>
    bash "$(dirname "$0")/tunnel.sh" "$@"
    ;;
  logs) shift; dc logs --tail "${TESTENV_LOG_LINES:-100}" "$@" ;;
  down) dc down ;;
  reset)
    dc down --remove-orphans || true
    for v in sites-billing "sites-$app"; do
      docker volume rm "${project}_${v}" 2>/dev/null || true
    done
    docker volume rm "${project}_database" "${project}_queue" 2>/dev/null || true
    echo "reset done: $project volumes removed"
    ;;
  sites)
    init_sites "$app"
    ;;
  import)
    group=${2:?group}
    site=${3:?site}
    [[ "$group" == "$app" ]] || { echo "this stack serves $app, not $group" >&2; exit 2; }
    dc run --rm --no-deps -T \
      -e TESTENV_SITE="$site" -e TESTENV_GROUP="$group" \
      -e TESTENV_IMPORT_DIR="/workspace/import/$site" \
      -e TESTENV_DB_ROOT_PASSWORD="$(grep -E '^DB_ROOT_PASSWORD=' "$env_file" | cut -d= -f2-)" \
      "$group-web" \
      /home/frappe/frappe-bench/env/bin/python /workspace/testenv/import-test-tenant.py
    ;;
  migrate)
    group=${2:?group}
    site=${3:?site}
    [[ "$group" == "$app" ]] || { echo "this stack serves $app, not $group" >&2; exit 2; }
    dc run --rm --no-deps -T "$group-web" bench --site "$site" migrate
    ;;
  sh)
    shift
    dc exec "$@" bash
    ;;
  *)
    echo "usage: TESTENV_APP=<dms|erp|cowork> run.sh {volumes|pull|config|up|ps|logs|down|reset|sites|sh}"; exit 2 ;;
esac
