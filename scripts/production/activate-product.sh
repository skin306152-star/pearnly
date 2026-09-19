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
"${compose[@]}" stop -t 120 "$product-worker" "$product-scheduler"
trap '"${compose[@]}" start "$product-worker" "$product-scheduler"' EXIT
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all backup --with-files
"${compose[@]}" run --rm --no-deps "$product-web" bench --site all migrate
"${compose[@]}" up -d --no-deps --force-recreate "$product-web" "$product-worker" "$product-scheduler" "$product-socketio" "$product-frontend"
trap - EXIT
printf 'Product %s activated with verified image.\n' "$product"
