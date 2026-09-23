#!/usr/bin/env bash
# ERP 前端快速迭代：两条 lane 的唯一入口。
#
# 为什么要有它：同一个 Site 上跑两条 lane ——
#   预览 lane（8136）= 本机原生 arm64 镜像 + **挂载当前工作树源码**，改完几十毫秒级生效；
#   验收 lane（8126）= 正式 Dockerfile 构建的 linux/amd64 不可变镜像，用来做"和上线一样"的检查。
# 两条 lane 共用同一个 Site、同一个库、同一套认证，所以**数据、样式、原生组件完全一致**，
# 差的只是"源码是挂进去的还是打进镜像的"。
#
# 用法:
#   scripts/stocktake-preview.sh up                  # 起预览 lane（不动验收 lane）
#   scripts/stocktake-preview.sh reload              # 改完 Python/hooks：清缓存 + 重启预览 web（秒级）
#   scripts/stocktake-preview.sh assets              # 只改了静态文件：清站内资源缓存（约 2 秒）
#   scripts/stocktake-preview.sh status              # 两条 lane 的状态、端口、镜像 digest
#   scripts/stocktake-preview.sh logs [服务]          # 看日志（默认预览 web）
#   scripts/stocktake-preview.sh faithful            # 慢路：按当前 HEAD 重建正式镜像并升级验收 lane
#   scripts/stocktake-preview.sh down                # 只停预览 lane（验收 lane 不动）
#
# 前提：
#   · 验收项目已经初始化过（/tmp 或 ~/.config/pearnly 下的 env 文件里有镜像与口令，600 权限）；
#   · 预览镜像 ${PEARNLY_PREVIEW_IMAGE} 与验收镜像**同一份 frappe.Dockerfile**，只是 arm64 原生；
#     版本必须一致，否则"本地看得见"就没意义 —— status 会把两边的镜像 digest 打出来。
#
# 副作用：up/down/reload/assets 都只动本任务的预览与验收服务；faithful 会重建镜像并升级验收 lane
# （本机内部动作，不推远端、不碰生产）。env 文件里是本地口令，**不进仓库**。
set -euo pipefail

CTX="${PEARNLY_PREVIEW_CTX:-colima-pearnly-stocktake}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${PEARNLY_PREVIEW_ENV_FILE:-$HOME/.config/pearnly/stocktake-acceptance.env}"
[ -f "$ENV_FILE" ] || ENV_FILE=/tmp/pearnly-stocktake-acceptance.env

FILES=(
  "$HERE/infrastructure/production/compose.yml"
  "$HERE/infrastructure/compose/stocktake-acceptance.yml"
  "$HERE/infrastructure/compose/stocktake-preview.yml"
)
args=(); for f in "${FILES[@]}"; do args+=(-f "$f"); done
dc() { docker --context "$CTX" compose --env-file "$ENV_FILE" "${args[@]}" "$@"; }

PREVIEW_URL="http://stocktake.localhost:8136/desk/pearnly-products"
ACCEPT_URL="http://stocktake.localhost:8126/desk/pearnly-products"
SITE=stocktake.localhost

image_id() { docker --context "$CTX" image inspect "$1" --format '{{.Id}}' 2>/dev/null || echo '(缺镜像)'; }

cmd="${1:-status}"; shift || true
case "$cmd" in
  up)
    dc up -d erp-web-preview erp-frontend-preview
    echo "预览 lane: $PREVIEW_URL"
    ;;
  reload)
    dc exec -T erp-web-preview bench --site "$SITE" clear-website-cache >/dev/null
    dc exec -T erp-web-preview bench --site "$SITE" clear-cache >/dev/null
    dc restart erp-web-preview >/dev/null
    echo "预览 lane 已重载（Python/hooks 改动生效）：$PREVIEW_URL"
    ;;
  assets)
    # 只清站内资源缓存：静态文件（JS/CSS）改动本来就已经在挂载目录里，
    # 清缓存是为了让 `?v=` 版本号与页面缓存跟着更新。
    dc exec -T erp-web-preview bench --site "$SITE" clear-website-cache >/dev/null
    echo "静态资源缓存已清（页面上按 ⌘⇧R 硬刷新）：$PREVIEW_URL"
    ;;
  status)
    echo "context: $CTX    env: $ENV_FILE"
    dc ps erp-web erp-frontend erp-web-preview erp-frontend-preview erp-socketio 2>/dev/null \
      || dc ps
    echo
    echo "预览镜像   ${PEARNLY_PREVIEW_IMAGE:-$(grep '^PEARNLY_PREVIEW_IMAGE=' "$ENV_FILE" | cut -d= -f2-)} -> $(image_id "$(grep '^PEARNLY_PREVIEW_IMAGE=' "$ENV_FILE" | cut -d= -f2-)")"
    echo "验收镜像   -> $(image_id "$(grep '^PEARNLY_IMAGE=' "$ENV_FILE" | cut -d= -f2-)")"
    echo "预览 lane  $PREVIEW_URL"
    echo "验收 lane  $ACCEPT_URL"
    ;;
  logs)
    dc logs -f --tail 60 "${1:-erp-web-preview}"
    ;;
  faithful)
    # 慢路（约 5 分钟）：只有"要出正式镜像/改依赖/做上线前检查"时才走。
    sha="$(git -C "$HERE" rev-parse --short=7 HEAD)"
    tag="pearnly/stocktake-acceptance:$sha"
    echo "按 HEAD $sha 构建 linux/amd64 镜像…"
    ( cd "$HERE" && docker --context colima-pearnly build --platform linux/amd64 \
        --label "org.opencontainers.image.revision=$sha" \
        -f infrastructure/images/frappe.Dockerfile -t "$tag" . )
    docker --context colima-pearnly image save "$tag" | docker --context "$CTX" image load >/dev/null
    digest="$(image_id "$tag")"
    python3 - "$ENV_FILE" "$digest" <<'PY'
import re, sys, pathlib
path, digest = sys.argv[1], sys.argv[2]
file = pathlib.Path(path)
text, count = re.subn(r'^PEARNLY_IMAGE=.*$', 'PEARNLY_IMAGE=' + digest, file.read_text(), flags=re.M)
assert count == 1, 'env 里应当恰好有一行 PEARNLY_IMAGE'
file.write_text(text); file.chmod(0o600)
print('env 已指向', digest)
PY
    dc up -d erp-web erp-worker erp-ocr-worker erp-ocr-dispatch erp-scheduler erp-socketio erp-frontend
    dc exec -T erp-frontend true >/dev/null 2>&1 || true
    dc run --rm acceptance-init bench --site "$SITE" clear-cache
    # 变量名后面紧跟中文括号时必须写成 ${digest}：bash 会把全角括号并进变量名，
    # 在 set -u 下直接报 unbound variable（实测踩过）。
    echo "验收 lane 已升级到 ${digest}（${ACCEPT_URL}）"
    ;;
  down)
    dc stop erp-frontend-preview erp-web-preview
    echo "预览 lane 已停（验收 lane 不受影响）"
    ;;
  *)
    sed -n '2,30p' "$0"; exit 2
    ;;
esac
