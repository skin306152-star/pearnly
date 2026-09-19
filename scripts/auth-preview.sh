#!/usr/bin/env bash
# Operates only the isolated local review stack; never deploys or creates a tenant.
set -euo pipefail
preview_root="$(cd "$(dirname "$0")/.." && pwd)"
preview_action="${1:-status}"
preview_compose=(docker --context colima-pearnly compose -p pearnly-auth-preview -f "$preview_root/infrastructure/compose/auth-preview.yml")
case "$preview_action" in
  status) "${preview_compose[@]}" ps ;;
  config) "${preview_compose[@]}" config --quiet ;;
  up)
    preview_image="$(docker --context colima-pearnly image inspect pearnly/frappe-sample:16.34.1 --format '{{.Id}}')"
    if [ "$preview_image" != sha256:90c1d545d215d6081965740013dd8af84337ca23c5fde5c36acdf162cd4655c3 ]; then
      echo 'Preview image differs from the verified baseline; recheck versions before starting.' >&2
      exit 1
    fi
    if ! docker --context colima-pearnly volume inspect pearnly-auth-preview_sites >/dev/null 2>&1; then
      echo 'Preview Site is not initialized. See docs/work/2026-09-20-auth-flow-fixes.md.' >&2
      exit 1
    fi
    "${preview_compose[@]}" up -d
    ;;
  restart-site) "${preview_compose[@]}" restart site ;;
  *) echo 'Usage: bash scripts/auth-preview.sh [status|config|up|restart-site]' >&2; exit 2 ;;
esac
