#!/usr/bin/env bash
# 把老产品的 MR.ERP DMS 连接（测试账号 dmstest）写进本站的 `Pearnly ERP Endpoint`。
#
# 前置：先跑过 `scripts/import-legacy-endpoints.py`，产出一份
#       `~/.config/pearnly/credentials/legacy-endpoints.json`。
#
# 用法: scripts/sample-seed-dms-endpoint.sh <组> <站点>
#   scripts/sample-seed-dms-endpoint.sh dms qd65956.localhost
#
# ⚠️ 老产品文档写明：dmstest **只允许一个活跃登录**。跑之前确认没别的会话占着。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CREDS="${PEARNLY_LEGACY_ENDPOINTS:-$HOME/.config/pearnly/credentials/legacy-endpoints.json}"
PICKER="$HERE/scripts/legacy-endpoint-to-json.py"
CHECK="$HERE/scripts/sample-seed-dms-endpoint.py"

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

group="${1:?用法: $0 <组> <站点>}"
site="${2:?用法: $0 <组> <站点>}"
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

if [ ! -f "$CREDS" ]; then
  echo "[失败] 没有 $CREDS" >&2
  echo "       先跑：<老产品>/venv/bin/python scripts/import-legacy-endpoints.py --sql <dump导出的sql> --env <web.env>" >&2
  exit 1
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "[1/3] 从导入结果里挑出 dmstest 那条连接"
python3 "$PICKER" --in "$CREDS" --out "$TMP/dms-endpoint.json" --require-username dmstest

echo "[2/3] 送进容器"
dc exec -T "$group-bench" bash -lc 'cat > /tmp/dms-endpoint.json && chmod 600 /tmp/dms-endpoint.json' \
  < "$TMP/dms-endpoint.json"
dc exec -T "$group-bench" bash -lc 'cat > /tmp/seed_dms_endpoint.py' < "$CHECK"

echo "[3/3] 写库并回读校验"
out=$(dc exec -T "$group-bench" bash -lc \
  "printf 'exec(open(\"/tmp/seed_dms_endpoint.py\").read())\n' | bench --site $site console" 2>&1)
clean=$(printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //')
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|Traceback|  )" || true

if printf '%s' "$clean" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] DMS 连接已写进 $site"
  exit 0
fi
echo "[失败] 写库没过，完整输出：" >&2
printf '%s\n' "$clean" | tail -30 >&2
exit 1
