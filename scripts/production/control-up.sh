#!/usr/bin/env bash
set -euo pipefail
export PEARNLY_CONTROL_SOURCE="$(cd "$(dirname "$0")/../.." && pwd)"
sample="${PEARNLY_LOCAL_STACK_ROOT:-/Users/skin/pearnly}/infrastructure/compose"
docker --context colima-pearnly compose \
  -f "$sample/00-base.yml" -f "$sample/01-bench.yml" -f "$sample/02-keycloak.yml" \
  -f "$sample/03-wekan.yml" -f "$sample/04-dev-mounts.yml" \
  -f "$PEARNLY_CONTROL_SOURCE/infrastructure/production/local-control.override.yml" \
  up -d --no-deps control-bench control-web control-scheduler control-worker
