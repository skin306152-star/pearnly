#!/usr/bin/env bash
# 测试机（WSL 内）执行入口。Mac 侧 scripts/testenv.sh 只负责同步与转发到这里，
# 避免多层引号把命令打散。
set -euo pipefail

root=/opt/pearnly-testenv
env_file=$root/env
compose="$root/deploy/infrastructure/production/compose.yml"
override="$root/deploy/infrastructure/testenv/override.yml"
project=pearnly-testenv
site_suffix=${TESTENV_SITE_SUFFIX:-test.localhost}

[[ -s "$env_file" ]] || { echo "Missing $env_file"; exit 2; }
dc() { docker compose --project-name "$project" --env-file "$env_file" -f "$compose" -f "$override" "$@"; }

ensure_volumes() {
  # 正式 compose 把这些卷声明为 external，和正式机一样先显式建卷再起服务。
  for v in sites-billing sites-dms sites-erp sites-cowork; do
    docker volume create "${project}_${v}" >/dev/null
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
      dms) app=pearnly_dms ;;
      erp) app=pearnly_erp_portal ;;
      cowork) app=pearnly_cowork ;;
      *) echo "unknown group $group"; exit 2 ;;
    esac
    site="${group}.${site_suffix}"
    echo "== $group -> $site"
    dc run --rm --no-deps -T \
      -e TESTENV_SITE="$site" -e TESTENV_GROUP="$group" -e TESTENV_APP="$app" \
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
    ensure_volumes
    dc up -d --wait mariadb redis-cache redis-queue keycloak billing-web
    dc --profile dms --profile erp --profile cowork up -d
    # 产品容器需要站点存在才能常驻；先建/迁移站点，再等健康。
    # shellcheck disable=SC2086
    init_sites ${TESTENV_GROUPS:-dms erp cowork}
    wait_healthy
    dc ps
    ;;
  ps) dc ps -a ;;
  check)
    # 每个产品组有独立的 sites 卷，必须分别在各自的容器里读。
    for g in dms erp cowork; do
      echo "== $g"
      dc run --rm --no-deps -T "$g-web" \
        /home/frappe/frappe-bench/env/bin/python /workspace/testenv/check.py
    done
    dc ps -a
    ;;
  smoke)
    # 在测试机内部按 Host 头访问前端入口，证明应用真的在应答（不经过 Mac 侧）。
    for entry in "dms.${site_suffix}:8183" "erp.${site_suffix}:8182" \
                 "cowork.${site_suffix}:8181" "billing.internal:8184"; do
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
  logs) shift; dc logs --tail "${TESTENV_LOG_LINES:-100}" "$@" ;;
  down) dc down ;;
  reset)
    dc down --remove-orphans || true
    for v in sites-billing sites-dms sites-erp sites-cowork; do
      docker volume rm "${project}_${v}" 2>/dev/null || true
    done
    docker volume rm "${project}_database" "${project}_queue" 2>/dev/null || true
    echo 'reset done: test-env volumes removed'
    ;;
  sites)
    shift
    init_sites "${@:-dms}"
    ;;
  sh)
    shift
    dc exec "$@" bash
    ;;
  *)
    echo "usage: run.sh {volumes|pull|config|up|ps|logs|down|reset|sites|sh}"; exit 2 ;;
esac
