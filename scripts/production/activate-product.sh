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
compose=(docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f /opt/pearnly/deploy/infrastructure/production/compose.yml --profile "$product")
# 先核对"服务器上这套部署脚本"和"这个镜像自带的副本"是同一份：不同源就停在这里，
# 不要出现"站点已经跑新代码、开户还在用旧脚本"这种当时不报错的错配。
python3 /opt/pearnly/deploy/scripts/production/check-deploy-image.py "$image" /opt/pearnly/deploy
# 按机器核数与内存算的容量参数（sizing.py，配置对照表的公式版）：compose 在 runtime.env 之前读它，runtime.env 写死的照旧以它为准；
# 算不出（低于最小支持的机器等）就留空，全按 runtime.env，与没有这一步时相同。
python3 /opt/pearnly/deploy/infrastructure/production/sizing.py --write /opt/pearnly/state/sizing.env >/dev/null \
  || { echo "sizing skipped; using runtime.env only" >&2; : > /opt/pearnly/state/sizing.env; }
docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
# 开户代理的维护容器（provision-agent.py `ensure_maintenance`，只跑 sleep、最多占 1 GiB）还是本产品的旧镜像：激活时收掉，
# 下一张开户工单按新镜像重建。放在容量等待之前，腾出的内存算进余量（2026-10-18 生产上 10-12 起的
# pearnly-maintenance-cowork 一直留着：改已有站点的工单不收，之后没再来工单；残留的还挡过一次 set_ocr 的容量准入）。
# 只收本产品组的、确认是维护容器的那一个：名字对、标签 pearnly.role=maintenance 且 pearnly.group=本产品（ensure_maintenance 建时打的）。
# 占用按开户代理现有机制判：开户代理整张工单持着 provision.lock，这里上面已 flock 同一把锁，此刻没有工单在跑；
# 另外容器里还有 site-step.py 进程（上一张工单的 SSH 断了、步骤还在跑完）也算在用，留着并告警，不中途杀掉。
maintenance="pearnly-maintenance-$product"
maintenance_labels=$(docker inspect --format '{{index .Config.Labels "pearnly.role"}}/{{index .Config.Labels "pearnly.group"}}' "$maintenance" 2>/dev/null) \
  || maintenance_labels=
if [[ "$maintenance_labels" == "maintenance/$product" ]]; then
  # 先存下再匹配：管道接 grep -q 在 pipefail 下会因 SIGPIPE 误判成「没在用」
  maintenance_processes=$(docker top "$maintenance" -o pid,args 2>/dev/null) || maintenance_processes=
  if [[ "$maintenance_processes" == *site-step.py* ]]; then
    printf '%s is still running a provisioning step; kept.\n' "$maintenance" >&2
  else
    docker rm -f "$maintenance" >/dev/null || printf '%s could not be removed; capacity check continues.\n' "$maintenance" >&2
  fi
elif [[ -n "$maintenance_labels" ]]; then
  printf '%s is not a provisioning maintenance container for %s; kept.\n' "$maintenance" "$product" >&2
fi
# 超配的内存上限要有 swap 兜着（capacity.py 核「上限之和 ≤ 物理内存 + swap」）；幂等，已有就不改。
bash /opt/pearnly/deploy/scripts/production/ensure-swap.sh >/dev/null
# 余量不够时最多等 10 分钟：上一个产品刚激活，迁移后的后台任务还在涨内存，等它回落再叠下一个，而不是马上叠上去或直接失败。
python3 /opt/pearnly/deploy/infrastructure/production/capacity.py --product "$product" --wait-seconds 600
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
if [[ "$product" == erp ]]; then
  "${compose[@]}" run --rm --no-deps erp-web env/bin/python /opt/pearnly/deploy-src/scripts/production/configure-erp-runtime.py
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
# 已开通租户站的宿主机 nginx 配置按 provision-agent 当前模板重写（幂等，只改内容不同的；nginx -t 不过就全部还原）。
# 模板改了（例如 2026-10-02 真实客户端 IP）时旧站点也跟上；失败只告警，不影响已切好的产品。
python3 /opt/pearnly/deploy/scripts/production/refresh-tenant-nginx.py \
  || printf 'Tenant nginx refresh failed; existing host configs were kept.\n' >&2
printf 'Product %s activated with verified image.\n' "$product"
