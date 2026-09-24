#!/usr/bin/env bash
# 样板环境初始化：初始化一个运行组的卷、写全局配置、建站点。
#
# 用法: scripts/sample-init-group.sh <组名> <站点...>
#   组名 ∈ cowork | erp | dms | control
#
# 幂等：卷已初始化则跳过拷贝；站点已存在则跳过创建。可重复执行。
#
# 关键点（踩过的坑）：新建的卷里 common_site_config.json 是空的，realtime
# 服务会去连容器自己的 127.0.0.1:6379 而反复重启。redis_socketio 必须写进
# 全局配置，这条不做就会以为"样板跑不起来"。
set -euo pipefail

CTX=colima-pearnly
IMAGE=pearnly/frappe-sample:16.34.1
COMPOSE=infrastructure/compose/01-bench.yml
DB_ROOT_PW=sample-root-local-only
ADMIN_PW=admin-local-only

group=${1:?用法: $0 <组名> <站点...>}
shift
if [ $# -eq 0 ]; then
  echo "至少给一个站点名" >&2
  exit 2
fi

d() { docker --context "$CTX" "$@"; }
dc() { d compose -f "$COMPOSE" "$@"; }

vol="pearnly-sample_sites-$group"
bench="$group-bench"

if ! d run --rm --user root -v "$vol:/dest" --entrypoint bash "$IMAGE" \
      -c '[ -f /dest/common_site_config.json ]'; then
  echo "[init] 初始化卷 $vol"
  d run --rm --user root -v "$vol:/dest" --entrypoint bash "$IMAGE" \
    -c 'cp -a /home/frappe/frappe-bench/sites/. /dest/ && chown -R frappe:frappe /dest'
fi

echo "[up] 管理容器 $bench"
dc up -d "$bench" >/dev/null

dc exec -T "$bench" bench set-config -g db_host mariadb >/dev/null
dc exec -T "$bench" bench set-config -g redis_cache redis://redis-cache:6379 >/dev/null
dc exec -T "$bench" bench set-config -g redis_queue redis://redis-queue:6379 >/dev/null
dc exec -T "$bench" bench set-config -g redis_socketio redis://redis-queue:6379 >/dev/null
if [ "$group" = dms ]; then
  dc exec -T "$bench" env/bin/python - < scripts/production/configure-dms-runtime.py
fi

for site in "$@"; do
  if dc exec -T "$bench" bench --site "$site" list-apps >/dev/null 2>&1; then
    echo "[skip] $site 已存在"
    continue
  fi
  echo "[new-site] $site"
  dc exec -T "$bench" bench new-site "$site" \
    --db-host mariadb --db-port 3306 \
    --db-root-username root --db-root-password "$DB_ROOT_PW" \
    --mariadb-user-host-login-scope=% \
    --admin-password "$ADMIN_PW" >/dev/null 2>&1
  echo "[new-site] $site 完成"

  # 收尾：Frappe 建站默认把 desk 首页设成 setup-wizard，不摘掉 /desk 会无限重载
  scripts/sample-finalize-site.sh "$group" "$site"
done

echo "[done] 组 $group 就绪"
