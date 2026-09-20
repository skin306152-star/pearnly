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
docker image inspect "$image" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
python3 /opt/pearnly/deploy/infrastructure/production/capacity.py --product "$product"
previous_container=$("${compose[@]}" ps --quiet "$product-web")
previous_image=$(docker inspect "$previous_container" --format '{{.Image}}')
# Close admission before draining. Use the still-running version to back up its
# own schema. An interrupted migration must never automatically reopen traffic
# or start an old worker against a partially upgraded database.
"${compose[@]}" exec -T "$product-web" bench --site all set-maintenance-mode on
trap 'printf "Activation failed; maintenance remains enabled. Inspect migration and backups before recovery.\n" >&2' ERR
"${compose[@]}" stop -t 30 "$product-ocr-dispatch" "$product-scheduler"
"${compose[@]}" stop -t 130 "$product-web" "$product-socketio"
"${compose[@]}" stop -t 660 "$product-worker" "$product-ocr-worker"
PEARNLY_IMAGE="$previous_image" "${compose[@]}" run --rm --no-deps "$product-web" bench --site all backup --with-files
"${compose[@]}" run --rm --no-deps "$product-web" env/bin/python -m pearnly_core.execution.configuration --redis-url redis://redis-queue:6379/6
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all migrate
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all enable-scheduler
"${compose[@]}" up -d --no-deps --force-recreate "$product-web" "$product-worker" "$product-ocr-worker" "$product-ocr-dispatch" "$product-scheduler" "$product-socketio" "$product-frontend"
"${compose[@]}" exec -T "$product-web" bench --site all set-maintenance-mode off
trap - ERR
printf 'Product %s activated with verified image.\n' "$product"
