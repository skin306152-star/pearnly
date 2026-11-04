#!/usr/bin/env bash
# 生产部署后的回读（在生产机上跑，只读）：deploy-run.sh 最后一步调用，也可单独重跑：
#   bash /opt/pearnly/deploy/scripts/production/deploy-readback.sh <镜像@sha256:…> <部署开始的 Unix 秒>
# 检查项同 production-deploy-steps 第 3 步：
#   - pearnly-production- 下的 Frappe 容器全在新镜像，三个产品的 active-image-<产品>.json 是新镜像；
#   - 识别进程带 FRAPPE_BACKGROUND_WORKERS_NOFORK=1；
#   - 三个产品网页容器里的全部站点 maintenance_mode=0、部署开始后 Error Log 为 0（按各站点自己的时区换算）；
#     进维护那一分钟定时任务被拒的「Scheduled jobs can't run in maintenance mode」单独列出、不算问题（10-25 人工回读同样判定）；
#     每个租户另列页面报错回传（Error Log「Browser error」）：部署后的条数（算在 Error Log 里、非 0 即问题）和部署前 24 小时的条数（对照）；
#   - mrerp / erpcowork / ris 的 /api/method/ping 200，fatcc.com 首页 200（它的 ping 本来就 403；刚换完基础层会 502 十几秒，每个域名最多重试 6 次）；
#   - 配了 LIFF 的每个站点（站点配置 pearnly_line_liff_id）：LIFF 落地地址是规定的（ERP / Cowork 站点根，DMS /liff/dms-booking），
#     ERP / Cowork 从 LINE 打开的入口不登录走一遍不出 404（check-liff-endpoints.py，只读、不要密钥；11-03 子路径落地 404 事故）；
#   - 产品地图里标了上线必验的入口：每个正式租户 × 每一行的探针走一遍（check-tenant-entries.py，只读、不登录；
#     导出 product-map.json 在镜像构建时生成、随部署工具同步过来；11-04 产品地图 E 块）；
#   - mariadb 内存只用 docker stats 看，不 exec 进数据库容器。
# 这一批特有的表、字段、补丁检查不在这里，按发布记录另做。退出码：0 全部通过，1 有问题（逐项打印）。
set -u

image=${1:?image}
since=${2:?since}
ROOT=${PEARNLY_DEPLOY_ROOT:-/opt/pearnly/deploy}
PING_WAIT_SECONDS=${PEARNLY_DEPLOY_PING_WAIT_SECONDS:-10}
problems=0
problem() { printf '  PROBLEM %s\n' "$*"; problems=$((problems + 1)); }

echo "-- containers on the new image"
for c in $(docker ps --format '{{.Names}}' | grep '^pearnly-production-'); do
  current=$(docker inspect -f '{{.Config.Image}}' "$c")
  case "$current" in
    *pearnly/frappe*) [[ "$current" == "$image" ]] || problem "$c runs $current" ;;
  esac
done
for product in erp cowork dms; do
  grep -qF "\"$image\"" "$ROOT/active-image-$product.json" 2>/dev/null || problem "active-image-$product.json is not the new image"
done

echo "-- ocr-worker NOFORK"
for c in $(docker ps --format '{{.Names}}' | grep '^pearnly-production-.*ocr-worker'); do
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c" | grep -qx 'FRAPPE_BACKGROUND_WORKERS_NOFORK=1' \
    && echo "  $c ok" || problem "$c has no FRAPPE_BACKGROUND_WORKERS_NOFORK=1"
done

echo "-- sites: maintenance and Error Log since $(date -u -r "$since" +%FT%TZ 2>/dev/null || date -u -d "@$since" +%FT%TZ)"
list_sites='import json, os
for s in sorted(os.listdir("sites")):
    path = os.path.join("sites", s, "site_config.json")
    if os.path.isfile(path):
        conf = json.load(open(path))
        print(s, conf.get("maintenance_mode", 0), str(conf.get("pearnly_line_liff_id") or "").strip() or "-")'
maintenance_reject="Scheduled jobs can''t run in maintenance mode"
liff_rows=""
for product in erp cowork dms; do
  web=pearnly-production-$product-web-1
  sites=$(docker exec -w /home/frappe/frappe-bench "$web" python3 -c "$list_sites" </dev/null) || { problem "$web: cannot list sites"; continue; }
  [[ -n "$sites" ]] || problem "$web: no sites"
  while read -r site maintenance liff; do
    [[ -n "$site" ]] || continue
    [[ -n "$liff" && "$liff" != - ]] && liff_rows+="$product $site $liff"$'\n'
    [[ "$maintenance" == 0 ]] || problem "$site maintenance_mode=$maintenance"
    zone=$(docker exec -w /home/frappe/frappe-bench "$web" bench --site "$site" mariadb -N -e \
      "select defvalue from tabDefaultValue where parent='__default' and defkey='time_zone'" </dev/null 2>/dev/null | tail -1)
    local_since=$(python3 -c 'import datetime, sys, zoneinfo
print(datetime.datetime.fromtimestamp(int(sys.argv[1]), zoneinfo.ZoneInfo(sys.argv[2] or "Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S"))' "$since" "${zone:-Asia/Bangkok}")
    counts=$(docker exec -w /home/frappe/frappe-bench "$web" bench --site "$site" mariadb -N -e \
      "select sum(error not like '%$maintenance_reject%'), sum(error like '%$maintenance_reject%'), sum(method like 'Browser error%'),
              (select count(*) from \`tabError Log\` where method like 'Browser error%' and creation >= '$local_since' - interval 1 day and creation < '$local_since')
       from \`tabError Log\` where creation >= '$local_since'" \
      </dev/null 2>/dev/null | tail -1)
    read -r errors rejected browser browser_before <<< "$counts"
    [[ "$errors" == NULL ]] && errors=0
    [[ "$rejected" == NULL ]] && rejected=0
    [[ "$browser" == NULL ]] && browser=0
    echo "  $site maintenance=$maintenance errors_since=${errors:-?} maintenance_rejected_jobs=${rejected:-?} browser_errors_since=${browser:-?} browser_errors_24h_before=${browser_before:-?} (${zone:-Asia/Bangkok} $local_since)"
    if [[ "$errors" != 0 ]]; then
      problem "$site Error Log since deploy: ${errors:-unreadable}"
      docker exec -w /home/frappe/frappe-bench "$web" bench --site "$site" mariadb -N -e \
        "select creation, left(error, 160) from \`tabError Log\` where creation >= '$local_since' and error not like '%$maintenance_reject%' order by creation desc limit 3" </dev/null 2>/dev/null | sed 's/^/    /'
    fi
  done <<< "$sites"
done

echo "-- domains"
for url in https://mrerp.pearnly.com/api/method/ping https://erpcowork.pearnly.com/api/method/ping \
           https://ris.pearnly.com/api/method/ping https://fatcc.com/; do
  code=000
  for attempt in 1 2 3 4 5 6; do
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$url")
    [[ "$code" == 200 ]] && break
    sleep "$PING_WAIT_SECONDS"
  done
  echo "  $url $code"
  [[ "$code" == 200 ]] || problem "$url answered $code"
done

echo "-- LINE: LIFF endpoints and entries"
if [[ -z "$liff_rows" ]]; then
  problem "no site has pearnly_line_liff_id"
else
  printf '%s' "$liff_rows" | python3 "$ROOT/scripts/production/check-liff-endpoints.py" --stdin \
    || problem "LIFF endpoint or LINE entry check failed (lines above)"
fi

echo "-- product map: must-verify entries on every formal tenant"
map_export="$ROOT/scripts/production/product-map.json"
if [[ ! -f "$map_export" ]]; then
  problem "no product map export $map_export (the image should carry it)"
else
  python3 "$ROOT/scripts/production/check-tenant-entries.py" --map "$map_export" \
    || problem "a must-verify entry failed on a formal tenant (lines above)"
fi

echo "-- mariadb memory (docker stats)"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' | grep -i mariadb | sed 's/^/  /'
echo "-- non-production containers"
others=$(docker ps --format '{{.Names}}' | grep -v '^pearnly-production-')
printf '%s\n' "${others:-(none)}" | sed 's/^/  /'

if [[ $problems -ne 0 ]]; then
  printf 'Readback found %s problem(s).\n' "$problems"
  exit 1
fi
# 回读通过的时间：deploy.sh 部署前那次只读 SSH 用它列出之后被手工重启 / 重建过的容器（workflow.md「手工动过生产」）
printf '%s\n' "$(date +%s)" > "${PEARNLY_STATE_DIR:-/opt/pearnly/state}/last-readback-ok" 2>/dev/null || true
printf 'Readback passed.\n'
