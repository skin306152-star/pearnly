#!/usr/bin/env bash
# 站外 LINE 表面预览：用**将要上线的镜像**对三个产品的全部站点只读对照（11-28 主会话定：预览必须出自要上线的那份代码）。
#
#   bash /opt/pearnly/deploy/scripts/production/surfaces-plan.sh ghcr.io/skin306152-star/pearnly/frappe@sha256:<64位>
#
# deploy-run.sh 激活前的 plan 步骤调用它；deploy.sh --dry-run 经一次 SSH 也跑它（镜像要已放上生产机）。
# 每个产品起一个一次性容器（新镜像、--rm、--memory 2g），--volumes-from 该产品网页容器且**只读**（站点配置、密钥），
# 网络同网页容器（连得上数据库，只读站点配置与绑定）。不进数据库容器、不写库、不发布，只发 LINE 的 GET。
# 缓存隔开：一次性容器的 common_site_config.json 换成一份副本，所有 redis:// 地址指向连不上的 127.0.0.1:9——
# Frappe 的缓存连不上就只用进程内缓存，新代码的 hooks / 译文不会写进旧版本正在读的线上缓存。
# 输出：每个产品的 SITE / SUMMARY 行（SUMMARY.changes 逐套写改了哪些文字字段、图变没变）。
# 退出码：0 预览跑成（有没有漂移都算跑成）；1 某个产品没跑成（没有 SUMMARY、站点读不出、镜像不在本机）。
set -u

image=${1:-}
[[ "$image" =~ ^ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}$ ]] || {
  printf 'usage: surfaces-plan.sh ghcr.io/skin306152-star/pearnly/frappe@sha256:<digest>\n' >&2; exit 2; }
docker image inspect "$image" >/dev/null 2>&1 || { printf 'PLAN-FAILED image %s is not on this host (stage it first)\n' "$image"; exit 1; }
SITES=/home/frappe/frappe-bench/sites
status=0
# 测试环境实测用（生产不设）：PEARNLY_PLAN_PRODUCTS="dms"、PEARNLY_PLAN_WEB="pearnly-testenv-{p}-{p}-web-1"
products=${PEARNLY_PLAN_PRODUCTS:-erp cowork dms}
default_web='pearnly-production-{p}-web-1'
web_format=${PEARNLY_PLAN_WEB:-$default_web}

for product in $products; do
  web=${web_format//\{p\}/$product}
  name=pearnly-surfaces-plan-$product
  work=$(mktemp -d)
  echo "-- $product: preview with $image"
  if ! docker exec "$web" cat "$SITES/common_site_config.json" </dev/null > "$work/common.json"; then
    printf 'PLAN-FAILED %s: cannot read common_site_config.json from %s\n' "$product" "$web"; status=1; rm -rf "$work"; continue
  fi
  python3 - "$work/common.json" <<'PY'
import json, sys
path = sys.argv[1]
conf = json.load(open(path))
def isolate(value):
    if isinstance(value, str) and value.startswith(("redis://", "rediss://", "unix://")):
        return "redis://127.0.0.1:9"
    if isinstance(value, dict):
        return {k: isolate(v) for k, v in value.items()}
    return value
json.dump({k: isolate(v) for k, v in conf.items()}, open(path, "w"))
PY
  chown 1000:1000 "$work/common.json" && chmod 600 "$work/common.json"
  network=$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{println $k}}{{end}}' "$web" | head -1)
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$web" | grep -v '^$' > "$work/env"
  docker rm -f "$name" >/dev/null 2>&1 || true
  timeout 900 docker run --rm --name "$name" --memory 2g --network "$network" --volumes-from "$web:ro" \
    -v "$work/common.json:$SITES/common_site_config.json:ro" --env-file "$work/env" -w "$SITES" \
    --entrypoint /home/frappe/frappe-bench/env/bin/python "$image" -m pearnly_core.line_surfaces check --product "$product" \
    </dev/null > "$work/out" 2>&1
  rc=$?
  grep -E '^(SITE|SUMMARY) ' "$work/out"
  summary=$(sed -n 's/^SUMMARY //p' "$work/out" | tail -1)
  if [[ -z "$summary" || $rc -gt 1 ]] || python3 -c 'import json,sys; sys.exit(0 if json.loads(sys.argv[1])["failed"] else 1)' "$summary"; then
    tail -15 "$work/out" | sed 's/^/    /'
    printf 'PLAN-FAILED %s: preview exited %s\n' "$product" "$rc"; status=1
  fi
  docker rm -f "$name" >/dev/null 2>&1 || true
  rm -rf "$work"
done
exit $status
