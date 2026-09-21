#!/usr/bin/env bash
# 看 / 清 DMS 会话锁。开发期排障用。
#
# 用法:
#   scripts/dms-lock.sh status            # 只看（默认）
#   scripts/dms-lock.sh clear             # 清掉（进程被 OOM 杀掉留下的死锁）
#   scripts/dms-lock.sh status qd65956.localhost
#
# ⚠️ `clear` 会把活会话的锁也清掉。清之前确认没有别的会话在跑 DMS ——
#    否则两个会话同时登同一个账号，会互相踢下线，而且表现为"莫名其妙失败"。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/dms-lock.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

action="${1:-status}"
site="${2:-qd65956.localhost}"

docker --context "$CTX" compose "${args[@]}" exec -T dms-bench \
  bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/dms_lock.py' < "$CHECK"

docker --context "$CTX" compose "${args[@]}" exec -T dms-bench bash -lc \
  "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/dms_lock.py $action $site"
