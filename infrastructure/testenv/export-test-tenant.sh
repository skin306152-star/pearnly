#!/usr/bin/env bash
# 在**正式服务器上**运行：把指定测试租户的数据库、文件与站点配置导出到一个目录，
# 供测试环境导入。只读操作之外仅产生 bench 备份文件，不改任何业务数据。
#
# 用法（在正式机上）：bash export-test-tenant.sh <输出目录> <组:站点名> ...
#   bash export-test-tenant.sh /root/te-export dms:testdms.pearnly.com erp:testerp.pearnly.com
set -euo pipefail

out=${1:?output dir}; shift
[[ $# -gt 0 ]] || { echo 'Pass at least one group:site'; exit 2; }
mkdir -p "$out"

for pair in "$@"; do
  group=${pair%%:*}
  site=${pair#*:}
  case "$group" in
    dms) container=pearnly-production-dms-web-1 ;;
    erp) container=pearnly-production-erp-web-1 ;;
    cowork) container=pearnly-production-cowork-web-1 ;;
    billing) container=pearnly-production-billing-web-1 ;;
    *) echo "unknown group $group"; exit 2 ;;
  esac
  [[ "$site" =~ ^[a-z0-9._-]+$ ]] || { echo "refusing site name $site"; exit 2; }
  docker inspect "$container" >/dev/null

  echo "== $site ($group)"
  docker exec "$container" bench --site "$site" backup --with-files

  dir="/home/frappe/frappe-bench/sites/$site/private/backups"
  in_container() { docker exec "$container" bash -c "$1"; }
  db=$(in_container "ls -1t $dir/*database.sql.gz | head -1")
  # 注意：*-private-files.tar 也匹配 *-files.tar，必须把 private 排掉。
  files=$(in_container "ls -1t $dir/*-files.tar | grep -v private-files | head -1")
  private=$(in_container "ls -1t $dir/*-private-files.tar | head -1")
  for f in "$db" "$files" "$private"; do
    echo "   $f"
    docker cp "$container:$f" "$out/"
  done
  # 站点配置里有 encryption_key 与各渠道凭据：同站名恢复必须原样带过去，
  # 否则库里已加密的字段解不开。
  docker cp "$container:/home/frappe/frappe-bench/sites/$site/site_config.json" \
    "$out/$site-site_config.json"
done

echo "--- $out ---"
ls -la "$out"
