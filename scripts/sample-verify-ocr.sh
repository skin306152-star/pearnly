#!/usr/bin/env bash
# 跑 OCR 自检（请求形状 + 两档升级 + 归一化门槛）。
#
# 检查内容在 `scripts/sample-verify-ocr.py`；本脚本只负责送进容器、执行、按输出判定。
#
# ⚠️ 这个脚本验的是**代码这一层**（请求长什么样、两档怎么升、门槛对不对）。
# 它**不验**"模型认不认一张真身份证"—— 那需要真凭据 + 真样本，
# 本机两样都没有（AI Studio key 额度耗尽、生产 Vertex 项目无权访问）。
#
# 用法: scripts/sample-verify-ocr.sh <组> <站点>
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-ocr.py"
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

dc exec -T "$group-bench" bash -lc 'cat > /tmp/verify_ocr.py' < "$CHECK"

out=$(dc exec -T "$group-bench" bash -lc \
  "printf 'exec(open(\"/tmp/verify_ocr.py\").read())\n' | bench --site $site console" 2>&1)

# 剥掉 console 的 `In [1]: ` 前缀，否则首行输出会被过滤掉（踩过）
clean=$(printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //')
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|SKIP|RESULT|Traceback|  )" || true

if printf '%s' "$clean" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] OCR 自检通过（代码这一层）"
  exit 0
fi
echo "[失败] OCR 自检没过，完整输出：" >&2
printf '%s\n' "$clean" | tail -30 >&2
exit 1
