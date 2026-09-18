#!/usr/bin/env bash
# LINE 状态机端到端验证：合成事件驱动整条建档流（真 OCR + 真写 DMS，测试账号）。
# 用法: scripts/sample-verify-line-flow-real.sh dms qd65956.localhost
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-line-flow-real.py"
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

dc exec -T "$group-bench" bash -lc 'mkdir -p /tmp/dmscheck && cat > /tmp/dmscheck/verify_line_flow.py' < "$CHECK"
dc cp "$SAMPLE_IMG" "$group-bench:/tmp/dmscheck/idcard.jpg" >/dev/null

echo "跑 LINE 状态机端到端（真 OCR + 真写 DMS，约 40–120 秒）…"
out=$(dc exec -T -e PYTHONUNBUFFERED=1 "$group-bench" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python /tmp/dmscheck/verify_line_flow.py $site" 2>&1 || true)

clean=$(printf '%s\n' "$out" | grep -vE "^\s*- ")
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  )" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] LINE 状态机端到端通过"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -45 >&2
exit 1
