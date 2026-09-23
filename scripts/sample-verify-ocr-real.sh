#!/usr/bin/env bash
# 跑 OCR **真样本**验证：真图片 → 真后端（Vertex）→ 跟老产品 ground truth 逐字段比。
#
# 跟 `sample-verify-ocr.sh` 的分工：
#   sample-verify-ocr.sh      → 桩服务器，验代码这一层（请求形状/升档/门槛），不碰真模型
#   sample-verify-ocr-real.sh → 真图片上真后端，验"模型认不认这张卡"
#
# 样本：老产品评测料 `tests/eval/vision_ablation_p3/id_card/`（合成泰国身份证，非真人）。
# 图被 gitignore，用固定种子现生成（本机已生成好；换机器要先跑生成器）：
#   cd <老产品> && node tests/eval/vision_ablation_p3/generate.cjs
#
# 用法: scripts/sample-verify-ocr-real.sh <组> <站点>
#   scripts/sample-verify-ocr-real.sh dms qd65956.localhost
#
# 环境变量：
#   PEARNLY_OCR_SAMPLES_DIR  覆盖样本目录（默认老产品评测料路径）
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
CHECK="$HERE/scripts/sample-verify-ocr-real.py"
LEGACY="${PEARNLY_LEGACY_REPO:-/Users/skin/Developer/Pearnly/pearnly-app}"
# 样本已复制进本仓库（`samples/ocr-id-card/`），**不再依赖老仓库**在不在本机。
# 想用别处的料（比如刚重新生成过）就设 PEARNLY_OCR_SAMPLES_DIR。
SAMPLES="${PEARNLY_OCR_SAMPLES_DIR:-$HERE/samples/ocr-id-card}"

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

if [ ! -d "$SAMPLES/images" ] || [ -z "$(ls -A "$SAMPLES/images" 2>/dev/null)" ]; then
  echo "[失败] 样本图不在：$SAMPLES/images" >&2
  echo "       先生成（约 5 秒）：" >&2
  echo "       cd $LEGACY && node tests/eval/vision_ablation_p3/generate.cjs" >&2
  exit 1
fi

echo "[1/3] 送样本进容器 $group-bench:/tmp/ocr-real"
dc exec -T "$group-bench" bash -lc 'rm -rf /tmp/ocr-real && mkdir -p /tmp/ocr-real/images /tmp/ocr-real/ground_truth'
dc cp "$SAMPLES/images/." "$group-bench:/tmp/ocr-real/images" >/dev/null
dc cp "$SAMPLES/ground_truth/." "$group-bench:/tmp/ocr-real/ground_truth" >/dev/null

echo "[2/3] 送检查脚本进容器"
dc exec -T "$group-bench" bash -lc 'cat > /tmp/verify_ocr_real.py' < "$CHECK"

echo "[3/3] 真调 Vertex（6 张图，可能因限流重试，最长约 2 分钟）"
out=$(dc exec -T "$group-bench" bash -lc \
  "printf 'exec(open(\"/tmp/verify_ocr_real.py\").read())\n' | bench --site $site console" 2>&1)

# 剥掉 console 的 `In [1]: ` 前缀，否则首行输出会被过滤掉（踩过）
clean=$(printf '%s\n' "$out" | sed -E 's/^In \[[0-9]+\]: //')
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|PART|SKIP|RESULT|Traceback|  )" || true

if printf '%s' "$clean" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 真样本识别全对"
  exit 0
fi
if printf '%s' "$clean" | grep -q "^RESULT: SKIP"; then
  echo "[未验成] 调不动模型（配额/网络），这次没验出识别率 —— 不是失败" >&2
  exit 2
fi
echo "[失败] 真样本识别没过，完整输出：" >&2
printf '%s\n' "$clean" | tail -40 >&2
exit 1
