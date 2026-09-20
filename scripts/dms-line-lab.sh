#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
compose=(docker --context colima-pearnly compose -p pearnly-dms-line-lab -f "$root/infrastructure/compose/dms-line-lab.yml")
case "${1:-status}" in
  init)
    "${compose[@]}" run --rm --no-deps cert-init
    "${compose[@]}" up -d --wait db redis
    "${compose[@]}" run --rm --no-deps site env/bin/python /workspace/scripts/dms-line-lab-init.py
    ;;
  up) "${compose[@]}" up -d simulator site worker ocr-worker ocr-dispatch scheduler socketio ;;
  status) "${compose[@]}" ps ;;
  test)
    "${compose[@]}" run --rm --no-deps site env/bin/python /workspace/scripts/test-dms-line-runtime.py
    ;;
  *) echo 'Usage: dms-line-lab.sh init|up|status|test' >&2; exit 2 ;;
esac
