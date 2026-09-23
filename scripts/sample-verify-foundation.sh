#!/usr/bin/env bash
# 跑地基自检（外部连接 + 推送日志）。
#
# 检查内容在 `scripts/sample-verify-foundation.py` 里；本脚本只负责把它送进容器、
# 用 bench console 执行、并**按输出判定成败**（不看退出码 —— bench console 永远 0）。
#
# 用法: scripts/sample-verify-foundation.sh <组> <站点>
#   例: scripts/sample-verify-foundation.sh dms qd65956.localhost
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-foundation.py"
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

# 检查脚本本身没被挂进容器，直接拷到 /tmp
dc exec -T "$group-bench" bash -lc 'cat > /tmp/verify_foundation.py' < "$CHECK"

out=$(dc exec -T "$group-bench" bash -lc \
  "printf 'exec(open(\"/tmp/verify_foundation.py\").read())\n' | bench --site $site console" 2>&1)

# 只打检查结果，不把 bench 的交互提示（In [1]: / 退出询问）刷满屏。
# ⚠️ 必须先剥掉 console 的 `In [1]: ` 前缀：它会跟脚本的第一行输出挤在同一行上，
# 不剥的话那行既不像 PASS 也不像 SKIP，直接被过滤掉 —— 表现为"看不到失败原因"（踩过）。
clean=$(printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //')
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|SKIP|RESULT|Traceback|  )" || true

if printf '%s' "$clean" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 地基自检通过"
  exit 0
fi
if printf '%s' "$clean" | grep -q "^RESULT: SKIP"; then
  echo "[skip] 条件不足，未执行（见上面说明）"
  exit 0
fi
echo "[失败] 地基自检没过，完整输出：" >&2
printf '%s\n' "$clean" | tail -30 >&2
exit 1
