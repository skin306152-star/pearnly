#!/usr/bin/env bash
# 删掉跑测试留下的发号单：控制面记录 + 空部署 + 身份服务里的账号。
#
# 用法:
#   scripts/sample-clean-issues.sh ISS-2026-0070,ISS-2026-0072        # 先看会删什么（DRY）
#   scripts/sample-clean-issues.sh ISS-2026-0070 --yes                 # 真删
#
# 有 active 部署的单子会跳过 —— 那背后是已建好的站点，要单独处理。
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
GROUP=control
SITE=control.localhost

ISSUES_ARG="${1:?用法: $0 <单号,逗号分开 | --subjects=uuid,uuid> [--yes]}"
DRY=1
if [ "${2:-}" = "--yes" ]; then DRY=0; fi

# --subjects=... 走另一条路：单子已经删了，只剩身份服务里的孤儿账号
if [ "${ISSUES_ARG#--subjects=}" != "$ISSUES_ARG" ]; then
  SUBJECTS_ARG="${ISSUES_ARG#--subjects=}"
  ISSUES_ARG=""
else
  SUBJECTS_ARG=""
fi

FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)
args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done
dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

echo "[1/2] 送清理脚本进容器"
dc exec -T "$GROUP-bench" bash -lc 'cat > /tmp/clean_issues.py' < "$HERE/scripts/sample-clean-issues.py"

# 变量后面紧跟中文时要用 ${}：全角括号的字节会被 bash 当成变量名的一部分
echo "[2/2] 执行（DRY=${DRY}）"
out=$(dc exec -T -e "ISSUES=$ISSUES_ARG" -e "SUBJECTS=$SUBJECTS_ARG" -e "DRY=$DRY" -e "FORCE=${FORCE:-0}" "$GROUP-bench" bash -lc \
  "printf 'exec(open(\"/tmp/clean_issues.py\").read())\nmain()\n' | PYTHONUNBUFFERED=1 bench --site $SITE console 2>&1" || true)

printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //' | grep -E "^(  |DRY|已删|身份账号|FAIL)" || true

if printf '%s' "$out" | grep -q "^FAIL"; then
  echo "[失败] 见上面" >&2
  exit 1
fi
echo "[done]"
