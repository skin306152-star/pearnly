#!/usr/bin/env bash
# Run as root on the VPS after the immutable image has passed CI and been staged.
# Does not create/migrate any product tenant or change the local control plane.
set -euo pipefail
image=${1:?Pass a verified GHCR image digest}
[[ "$image" =~ ^ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$EUID" == 0 ]] || exit 2
root=/opt/pearnly/deploy
mkdir -p /opt/pearnly/state
exec 9>/opt/pearnly/state/provision.lock
flock -x 9
compose="$root/infrastructure/production/compose.yml"
export PEARNLY_IMAGE="$image"
# 同 activate-product.sh：基础层的部署脚本也必须和这个镜像自带的副本同源。
python3 "$root/scripts/production/check-deploy-image.py" "$image" "$root"
# 按机器算的容量参数（见 activate-product.sh）：MariaDB 的内存、缓冲池、连接数没在 runtime.env 写死时跟着机器走。
python3 "$root/infrastructure/production/sizing.py" --write /opt/pearnly/state/sizing.env >/dev/null \
  || { echo "sizing skipped; using runtime.env only" >&2; : > /opt/pearnly/state/sizing.env; }
test -s /opt/pearnly/secrets/member-identity.json
docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" config --quiet
# Freeze private RPC writes and preserve a database/files backup using the
# running version before applying native schema migrations. Failure leaves the
# endpoint stopped; do not automatically run old code against an upgraded DB.
billing_container=$(docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" ps -aq billing-web)
if [[ -n "$billing_container" ]]; then
  old_image=$(docker inspect "$billing_container" --format '{{.Config.Image}}')
  docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" stop -t 130 billing-web
  PEARNLY_IMAGE="$old_image" docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" \
    run --rm --no-deps billing-web bench --site billing.internal backup --with-files
fi
docker volume create pearnly-production_sites-billing >/dev/null
# Keycloak 的登录主题从部署拷贝挂载（compose）。目录空着 Keycloak 不报错、悄悄换成默认登录页（2026-10-11：pearnly-ai
# 一直是空目录，Keycloak 重建后 CC 登录跳到默认页、第一页的密码也带不过去），所以缺了就不激活。
themes="$root/infrastructure/keycloak/themes"
for theme in pearnly pearnly-ai; do
  test -s "$themes/$theme/login/theme.properties" || { echo "login theme $theme is missing under $themes" >&2; exit 1; }
done
themes_digest=$(python3 - "$themes" <<'PY'
import hashlib, sys
from pathlib import Path
root = Path(sys.argv[1])
digest = hashlib.sha256()
for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("._") and p.name != ".DS_Store"):
    digest.update(path.relative_to(root).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
print(digest.hexdigest())
PY
)
keycloak_before=$(docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" ps -q keycloak)
docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" up -d mariadb redis-cache redis-queue keycloak
keycloak_after=$(docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" ps -q keycloak)
themes_record=/opt/pearnly/state/keycloak-themes.sha256
themes_before=$(cat "$themes_record" 2>/dev/null || true)
if [[ -n "$themes_before" && "$themes_before" != "$themes_digest" && -n "$keycloak_before" && "$keycloak_before" == "$keycloak_after" ]]; then
  # 主题文件变了、容器没重建：Keycloak 缓存着已加载的主题和压缩过的资源，清掉压缩缓存再重启才生效（停登约 40 秒）。
  # 第一次（还没有记录）只记下、不重启。
  docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" exec -T keycloak rm -rf /opt/keycloak/data/tmp/kc-gzip-cache
  docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" restart keycloak
fi
printf '%s\n' "$themes_digest" > "$themes_record"
docker run --rm --user 0 --network pearnly-production_runtime \
  -v pearnly-production_sites-billing:/home/frappe/frappe-bench/sites \
  -v "$root/infrastructure/production/bootstrap-billing.py:/run/bootstrap-billing.py:ro" \
  -v /opt/pearnly/secrets/runtime.env:/run/secrets/runtime.env:ro \
  -v /opt/pearnly/secrets/member-identity.json:/run/secrets/member-identity.json:ro \
  --entrypoint /home/frappe/frappe-bench/env/bin/python "$image" /run/bootstrap-billing.py
# billing 没有后台进程，迁移默认排的「网站搜索索引」没人处理、永远滞留在共用队列（2026-10-03 清掉 13 个）；billing 也用不到网站搜索。
docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" run --rm --no-deps billing-web bench --site billing.internal migrate --skip-search-index
docker compose --env-file /opt/pearnly/state/sizing.env --env-file /opt/pearnly/secrets/runtime.env -f "$compose" up -d --no-deps billing-web
docker image inspect "$image" --format '{{json .RepoDigests}}' > /opt/pearnly/deploy/active-image.json
printf 'Foundation activated. Existing product services were not changed.\n'
