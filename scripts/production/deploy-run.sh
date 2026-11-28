#!/usr/bin/env bash
# 生产统一部署的服务器端（在生产机上跑）：同步部署工具 → 核对 → ERP / Cowork / DMS 依次激活 → 站外 LINE 表面同步 → 基础层 → 回读。
#
# 由本机 `scripts/production/deploy.sh` 经一次 SSH 后台启动（nohup，日志不经 SSH 回传）；也可在服务器上手工跑：
#   nohup bash deploy-run.sh ghcr.io/skin306152-star/pearnly/frappe@sha256:<64位> <main 完整 SHA> </dev/null >/dev/null 2>&1 &
# 步骤、顺序与 10-16 之后的人工模板（deploy-template.sh）相同，不改激活脚本本身：
#   - 镜像 revision 标签必须等于要部署的 SHA；
#   - 激活前从镜像同步 /opt/pearnly/deploy，check-deploy-image.py 必须通过；本脚本也必须与镜像里那份一致；
#   - 激活前租户配置对比（check-tenant-config.py，按这一版产品地图的配置分类）：正式租户「应一致」的键与测试租户不同、
#     或「形状规则」不满足就停下，生产服务不动；没有绕过开关；
#   - 产品逐个激活、不并行；每个产品和基础层开始前等整机可用内存回到 8 GB 以上（每 10 秒看一次，最多 10 分钟）；
#   - 全程每 10 秒记一次内存到 mem-<短SHA>.log；
#   - 激活脚本自带的维护模式、迁移前备份、失败边界都照旧；任何一步失败立刻停下，不自动回滚。
# 输出：deploy-<短SHA>.log（总日志）、activate-<产品>-<短SHA>.log、deploy-<短SHA>.status（running <步骤> / ok / failed <步骤>）。
# 同一时间只允许一次部署（锁目录 deploy.lock，记 pid 与 SHA）。退出码：0 成功，1 失败，3 已有部署在跑。
set -u

image=${1:-}
sha=${2:-}
if [[ ! "$image" =~ ^ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}$ || ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  printf 'usage: deploy-run.sh ghcr.io/skin306152-star/pearnly/frappe@sha256:<digest> <40-char sha>\n' >&2
  exit 2
fi
STATE=${PEARNLY_STATE_DIR:-/opt/pearnly/state}
ROOT=${PEARNLY_DEPLOY_ROOT:-/opt/pearnly/deploy}
MEM_WAIT_SECONDS=${PEARNLY_DEPLOY_MEM_WAIT_SECONDS:-10}
short=${sha:0:8}
log=$STATE/deploy-$short.log
status=$STATE/deploy-$short.status
lock=$STATE/deploy.lock
self=$(cd "$(dirname "$0")" && pwd)/$(basename "$0")

if ! mkdir "$lock" 2>/dev/null; then
  holder=$(cat "$lock/pid" 2>/dev/null || true)
  if [[ -n "$holder" ]] && kill -0 "$holder" 2>/dev/null; then
    printf 'Another deployment is running: pid %s, %s\n' "$holder" "$(cat "$lock/sha" 2>/dev/null)" >&2
    exit 3
  fi
  # 上一次部署的进程已经不在（被杀或机器重启），锁是残留
  rm -rf "$lock" && mkdir "$lock" || exit 3
fi
printf '%s\n' "$$" > "$lock/pid"
printf '%s\n' "$sha" > "$lock/sha"

exec >>"$log" 2>&1
step=start
set_status() { printf '%s\n' "$1" > "$status"; }
log() { printf '== %s %s\n' "$(date -u +%FT%TZ)" "$*"; }
fail() { log "FAILED $step: $*"; set_status "failed $step"; exit 1; }

( while true; do
    printf '%s %s\n' "$(date '+%T')" "$(free -m | awk '/Mem:/{print "avail="$7" used="$3} /Swap:/{print "swap_used="$3}' | tr '\n' ' ')"
    sleep 10
  done ) >> "$STATE/mem-$short.log" 2>&1 &
sampler=$!
trap 'kill $sampler 2>/dev/null; rm -rf "$lock"' EXIT

set_status "running $step"
since=$(date +%s)
printf '%s\n' "$since" > "$STATE/deploy-$short.since"
log "start image=$image sha=$sha"
log "free before"; free -m
log "non-production containers"; docker ps --format '{{.Names}}' | grep -v '^pearnly-production-' || echo "(none)"

step=revision; set_status "running $step"
revision=$(docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}') \
  || fail "image is not on this host (stage it first)"
log "image revision $revision"
[[ "$revision" == "$sha" ]] || fail "image revision $revision is not $sha"

step=sync-tooling; set_status "running $step"
log "sync deploy tooling"
docker rm -f pearnly-deploy-sync >/dev/null 2>&1 || true
docker create --name pearnly-deploy-sync "$image" >/dev/null || fail "docker create"
docker cp pearnly-deploy-sync:/opt/pearnly/deploy-src/. "$ROOT/" || { docker rm -f pearnly-deploy-sync >/dev/null 2>&1; fail "docker cp"; }
docker rm pearnly-deploy-sync >/dev/null
python3 "$ROOT/scripts/production/check-deploy-image.py" "$image" "$ROOT" || fail "deploy tooling does not match this image"
cmp -s "$self" "$ROOT/scripts/production/deploy-run.sh" || fail "this runner is not the copy inside the image"

step=config; set_status "running $step"
log "tenant config: formal tenants against the test tenant (product map classes)"
python3 "$ROOT/scripts/production/check-tenant-config.py" --map "$ROOT/scripts/production/product-map.json" \
  || fail "tenant config check found differences that block the deployment (BLOCK lines above)"

wait_mem() {
  local i avail
  for i in $(seq 1 60); do
    avail=$(free -m | awk '/Mem:/{print $7}')
    if [[ "$avail" -ge 8192 ]]; then log "available ${avail} MB"; return 0; fi
    log "available ${avail} MB < 8192, waiting ($i)"
    sleep "$MEM_WAIT_SECONDS"
  done
  return 1
}

for product in erp cowork dms; do
  step=$product; set_status "running $step"
  wait_mem || fail "memory did not recover to 8 GB in 10 minutes"
  log "activate $product"
  bash "$ROOT/scripts/production/activate-product.sh" "$image" "$product" > "$STATE/activate-$product-$short.log" 2>&1
  rc=$?
  tail -2 "$STATE/activate-$product-$short.log"
  if [[ $rc -ne 0 ]]; then tail -25 "$STATE/activate-$product-$short.log"; fail "activate-product.sh $product exited $rc"; fi
done

# 站外 LINE 表面（11-26 ris 富菜单 9 月后从没更新的事故）：三个产品都换上新镜像后，每个产品网页容器里对这个产品的
# 全部站点逐站对照「LINE 上现挂的 / 这一版代码应有的」，有漂移才发布、发布后回读；没有渠道凭据的站点跳过并列出。
# 发布前打 PLAN 行（每套改了哪些文字字段、图变没变）与 BEFORE 行（原配置，可原样交回 restore 回退，旧菜单从不删）；
# 发布后给有效绑定的人当场重挂并回读（发布前挂着的一个不少、都挂到这一版）；任何一站出错或回读不一致 → 部署失败。
step=surfaces; set_status "running $step"
for product in erp cowork dms; do
  log "LINE surfaces $product: publish where this release differs, then read back"
  docker exec -w /home/frappe/frappe-bench/sites "pearnly-production-$product-web-1" \
    /home/frappe/frappe-bench/env/bin/python -m pearnly_core.line_surfaces sync --product "$product" \
    </dev/null > "$STATE/surfaces-$product-$short.log" 2>&1
  rc=$?
  grep -E '^(PLAN|BEFORE|SITE|SUMMARY) ' "$STATE/surfaces-$product-$short.log"
  if [[ $rc -ne 0 ]]; then
    tail -25 "$STATE/surfaces-$product-$short.log"
    fail "LINE surfaces $product exited $rc (SUMMARY above names the site; roll a published site back with: docker exec -w /home/frappe/frappe-bench/sites pearnly-production-$product-web-1 /home/frappe/frappe-bench/env/bin/python -m pearnly_core.line_surfaces restore --product $product --site <site> --surface <surface> --before '<config JSON of its BEFORE line>')"
  fi
done

step=foundation; set_status "running $step"
wait_mem || fail "memory did not recover to 8 GB in 10 minutes"
log "activate foundation"
bash "$ROOT/scripts/production/activate-foundation.sh" "$image" > "$STATE/activate-foundation-$short.log" 2>&1
rc=$?
tail -2 "$STATE/activate-foundation-$short.log"
if [[ $rc -ne 0 ]]; then tail -25 "$STATE/activate-foundation-$short.log"; fail "activate-foundation.sh exited $rc"; fi
log "free after"; free -m

step=readback; set_status "running $step"
log "readback"
bash "$ROOT/scripts/production/deploy-readback.sh" "$image" "$since" || fail "readback found problems (activation itself finished)"
set_status ok
log "DEPLOY DONE"
