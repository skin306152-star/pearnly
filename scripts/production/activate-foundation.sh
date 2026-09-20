#!/usr/bin/env bash
# Run as root on the VPS after the immutable image has passed CI and been staged.
# Does not create/migrate any product tenant or change the local control plane.
set -euo pipefail
image=${1:?Pass a verified GHCR image digest}
[[ "$image" =~ ^ghcr\.io/skin306152-star/pearnly/frappe@sha256:[0-9a-f]{64}$ ]] || exit 2
[[ "$EUID" == 0 ]] || exit 2
root=/opt/pearnly/deploy
compose="$root/infrastructure/production/compose.yml"
export PEARNLY_IMAGE="$image"
# 同 activate-product.sh：基础层的部署脚本也必须和这个镜像自带的副本同源。
python3 "$root/scripts/production/check-deploy-image.py" "$image" "$root"
docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
docker compose --env-file /opt/pearnly/secrets/runtime.env -f "$compose" config --quiet
docker volume create pearnly-production_sites-billing >/dev/null
docker compose --env-file /opt/pearnly/secrets/runtime.env -f "$compose" up -d mariadb redis-cache redis-queue keycloak
docker run --rm --user 0 --network pearnly-production_runtime \
  -v pearnly-production_sites-billing:/home/frappe/frappe-bench/sites \
  -v "$root/infrastructure/production/bootstrap-billing.py:/run/bootstrap-billing.py:ro" \
  -v /opt/pearnly/secrets/runtime.env:/run/secrets/runtime.env:ro \
  --entrypoint /home/frappe/frappe-bench/env/bin/python "$image" /run/bootstrap-billing.py
docker compose --env-file /opt/pearnly/secrets/runtime.env -f "$compose" run --rm --no-deps billing-web bench --site billing.internal migrate
docker compose --env-file /opt/pearnly/secrets/runtime.env -f "$compose" up -d --no-deps billing-web
docker image inspect "$image" --format '{{json .RepoDigests}}' > /opt/pearnly/deploy/active-image.json
printf 'Foundation activated. Existing product services were not changed.\n'
