#!/usr/bin/env bash
# 权威只读只登一次 —— 对**真** MR.ERP DMS 的只读验证（要真浏览器 + 真外网）。
#
# 用法: scripts/sample-verify-dms-authoritative-read-real.sh [容器] [站点]
#       （也可以用 PEARNLY_DMS_VERIFY_CONTAINER / PEARNLY_DMS_VERIFY_SITE 指定）
#
# ⚠️ 会真连 mrerp4sme.com：用测试销售号 + 测试管理员号各登一次（退出时登出）。
#    只做读操作：不建客户、不建单、不改任何数据。
# ⚠️ 换容器前确认两件事：① 它能连外网（演示站 compose 的网络是 internal，连不出去）；
#    ② 它有 playwright 浏览器（`/opt/ms-playwright`）——没有就起不来 chromium。
#
# 对照组跑法（证明这个验证真能抓到差别、不是永远通过）：
#    PEARNLY_DMS_VERIFY_WORKTREE=0 bash scripts/sample-verify-dms-authoritative-read-real.sh
#    不送工作树代码，直接用容器里挂的那份检出（本次改动之前的老行为）——应该看到
#    "A 只真登录 1 次" 失败（老行为是登 2 次：先销售、再管理员）。
set -euo pipefail

CONTAINER="${1:-${PEARNLY_DMS_VERIFY_CONTAINER:-pearnly-sample-erp-bench-1}}"
SITE="${2:-${PEARNLY_DMS_VERIFY_SITE:-qerp.localhost}}"
USE_WORKTREE="${PEARNLY_DMS_VERIFY_WORKTREE:-1}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
BOX_DIR=/tmp/pearnly-dms-authread

docker exec -i "$CONTAINER" bash -lc "mkdir -p $BOX_DIR"
docker exec -i "$CONTAINER" bash -lc "cat > $BOX_DIR/verify_authread_real.py" \
  < "$HERE/scripts/sample-verify-dms-authoritative-read-real.py"

PY_PATH=""
if [ "$USE_WORKTREE" = "1" ]; then
  # 容器里挂的是**别的检出**（样板站挂主仓）。把本次工作树的两个 App 送进去并置于
  # PYTHONPATH 最前，否则跑的是挂载那份，不是这次改的代码。
  docker cp "$HERE/apps/pearnly_dms/pearnly_dms" "$CONTAINER:$BOX_DIR/" >/dev/null
  docker cp "$HERE/apps/pearnly_core/pearnly_core" "$CONTAINER:$BOX_DIR/" >/dev/null
  PY_PATH="$BOX_DIR"
  echo "被测代码: 本次工作树（$HERE/apps/pearnly_dms）"
else
  echo "被测代码: 容器里挂载的那份检出（对照组）"
fi

echo "对真 DMS 跑权威只读验证（容器 ${CONTAINER} / 站点 ${SITE} / 起 chromium / 约 30–120 秒）…"
out=$(docker exec -i -e PYTHONPATH="$PY_PATH" -e PYTHONUNBUFFERED=1 "$CONTAINER" \
  bash -lc "cd /home/frappe/frappe-bench/sites && /home/frappe/frappe-bench/env/bin/python $BOX_DIR/verify_authread_real.py $SITE" 2>&1 || true)

# 滤掉 playwright 的进度噪音，剩下的全是断言和读数
clean=$(printf '%s\n' "$out" | grep -vE "^(\s*(-|taking|waiting|fonts)|\[pid=)" || true)
printf '%s\n' "$clean" | grep -E "^(PASS|FAIL|RESULT|合计|整块|\[场景|被测|  )" || true

if printf '%s' "$out" | grep -q "^RESULT: ALL PASS"; then
  echo "[done] 权威只读对真系统验证通过"
  exit 0
fi
echo "[失败] 完整输出：" >&2
printf '%s\n' "$clean" | tail -60 >&2
exit 1
