#!/usr/bin/env bash
# DMS 建单验证（**真写**）：复核主档 → 建订车单 → 挂附件。只对测试账号。
# 用法: scripts/sample-verify-dms-booking-real.sh dms qd65956.localhost
#
# ⚠️ 会真的建出一张订车单（测试账号、测试客户）。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-dms-booking-real.py"
SAMPLE_IMG="$HERE/samples/ocr-id-card/images/id_card_clean_001.jpg"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/03-wekan.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> <站点>}"
site="${2:?用法: $0 <组> <站点>}"

dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

dc exec -T "$group-bench" bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_booking_real.py' < "$CHECK"
dc cp "$SAMPLE_IMG" "$group-bench:/tmp/dmscheck/idcard.jpg" >/dev/null

echo "真连 DMS 建单（起 chromium + 建单 + 挂附件，约 60–150 秒）…"
out=$(dc exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_booking_real.py $site" 2>&1 || true)

clean=$(printf '%s\n' "$out" | grep -vE "^\s*(- |taking|waiting|fonts)")
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  |⚠️)" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 建单验证通过（写的是测试数据）"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -50 >&2
exit 1
