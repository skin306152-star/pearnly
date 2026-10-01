#!/usr/bin/env bash
# Run after staging a verified image. Back up and migrate only this product.
set -euo pipefail
image=${1:?Pass verified image digest}
product=${2:?Pass product}
[[ "$image" =~ ^ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$product" =~ ^(dms|erp|cowork)$ && "$EUID" == 0 ]] || exit 2
exec 9>/opt/pearnly/state/provision.lock
flock 9
export PEARNLY_IMAGE="$image"
compose=(docker compose --env-file /opt/pearnly/secrets/runtime.env -f /opt/pearnly/deploy/infrastructure/production/compose.yml --profile "$product")
# 先核对"服务器上这套部署脚本"和"这个镜像自带的副本"是同一份：不同源就停在这里，
# 不要出现"站点已经跑新代码、开户还在用旧脚本"这种当时不报错的错配。
python3 /opt/pearnly/deploy/scripts/production/check-deploy-image.py "$image" /opt/pearnly/deploy
docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
python3 /opt/pearnly/deploy/infrastructure/production/capacity.py --product "$product"
previous_container=$("${compose[@]}" ps --quiet "$product-web")
previous_image=$(docker inspect "$previous_container" --format '{{.Image}}')
# Close admission before draining. Use the still-running version to back up its
# own schema. An interrupted migration must never automatically reopen traffic
# or start an old worker against a partially upgraded database.
"${compose[@]}" exec -T "$product-web" bench --site all set-maintenance-mode on
image_record_temp=
trap '[[ -z "$image_record_temp" ]] || rm -f "$image_record_temp"; printf "Activation failed; inspect maintenance status, migration and backups before recovery.\n" >&2' ERR
"${compose[@]}" stop -t 30 "$product-ocr-dispatch" "$product-scheduler"
"${compose[@]}" stop -t 130 "$product-web" "$product-socketio"
workers=("$product-worker" "$product-ocr-worker")
if [[ "$product" == dms ]]; then workers+=(dms-sync-worker); fi
if [[ "$product" == cowork ]]; then workers+=(cowork-long-worker); fi
"${compose[@]}" stop -t 660 "${workers[@]}"
PEARNLY_IMAGE="$previous_image" "${compose[@]}" run --rm --no-deps "$product-web" bench --site all backup --with-files
"${compose[@]}" run --rm --no-deps "$product-web" env/bin/python -m pearnly_core.execution.configuration --redis-url redis://redis-queue:6379/6
if [[ "$product" == dms ]]; then
  "${compose[@]}" run --rm --no-deps dms-web env/bin/python /opt/pearnly/deploy-src/scripts/production/configure-dms-runtime.py
fi
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all migrate
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all enable-scheduler
"${compose[@]}" up -d --no-deps --force-recreate "$product-web" "${workers[@]}" "$product-ocr-dispatch" "$product-scheduler" "$product-socketio" "$product-frontend"
# Pin only this product. Keep the legacy shared record unchanged for products
# that have not been activated separately; their next provisioning job must not
# silently upgrade them. Write the exact verified input, not an arbitrary first
# RepoDigest, and publish the record only after activation has fully succeeded.
image_record_temp=$(mktemp "/opt/pearnly/deploy/.active-image-$product.XXXXXX")
printf '["%s"]\n' "$image" > "$image_record_temp"
"${compose[@]}" exec -T "$product-web" bench --site all set-maintenance-mode off
mv -f "$image_record_temp" "/opt/pearnly/deploy/active-image-$product.json"
image_record_temp=
trap - ERR
# migrate 清空了缓存：替最近活跃的用户把启动信息、本产品表结构先算好，免得每人第一次打开都
# 多等几秒（pearnly_core/desk/warmup.py）。已经切回正常服务、版本记录也已写好，这一步失败
# 只告警，不算激活失败；它有总时长上限，是一次性的，不是定时任务。
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all execute pearnly_core.desk.warmup.run \
  || printf 'Desk warm-up failed; each user computes their own first boot as before.\n' >&2
# Cowork：对账结果页的存档按新代码补一遍（pearnly_cowork/recon_center/view_store.py backfill）。后台低权重进程、分离运行，
# 不让激活等它；没补到的那几套打开时先给旧存档、后台再重建一份，不会让人等现算。一次性，不是定时任务。
if [[ "$product" == cowork ]]; then
  "${compose[@]}" run -d --rm --no-deps cowork-worker bench --site all execute pearnly_cowork.recon_center.view_store.backfill \
    || printf 'Reconciliation view backfill did not start; stale views refresh in the background on open.\n' >&2
fi
printf 'Product %s activated with verified image.\n' "$product"
