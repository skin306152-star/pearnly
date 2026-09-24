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

case "${1:-}" in
  volumes)
    for v in sites-billing sites-dms sites-erp sites-cowork; do
      docker volume create "${project}_${v}" >/dev/null
    done
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
    dc up -d --wait mariadb redis-cache redis-queue keycloak billing-web
    dc --profile dms --profile erp --profile cowork up -d
    wait_healthy
    dc ps
    ;;
  ps) dc ps -a ;;
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
    for group in "${@:-dms}"; do
      case "$group" in
        dms) app=pearnly_dms ;;
        erp) app=pearnly_erp_portal ;;
        cowork) app=pearnly_cowork ;;
        *) echo "unknown group $group"; exit 2 ;;
      esac
      site="${group}.${site_suffix}"
      echo "== $group -> $site"
      dc run --rm --no-deps -T "$group-web" \
        -e TESTENV_SITE="$site" -e TESTENV_GROUP="$group" -e TESTENV_APP="$app" \
        -e TESTENV_DB_ROOT_PASSWORD="$(grep -E '^DB_ROOT_PASSWORD=' "$env_file" | cut -d= -f2-)" \
        -e TESTENV_ADMIN_PASSWORD="${TESTENV_ADMIN_PASSWORD:-testenv-admin}" \
        /home/frappe/frappe-bench/env/bin/python /workspace/testenv/site-init.py
    done
    ;;
  sh)
    shift
    dc exec "$@" bash
    ;;
  *)
    echo "usage: run.sh {volumes|pull|config|up|ps|logs|down|reset|sites|sh}"; exit 2 ;;
esac
