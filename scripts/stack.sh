#!/usr/bin/env bash
# 本机样板栈的唯一入口。
#
# 为什么要有它：整套栈由 4 个 compose 文件组成，**必须一起带上**才算完整 ——
# 只带 `01-bench.yml` 会把站点组和数据库/身份服务拆成两半（文件名里的编号就是
# 加载顺序，`04-dev-mounts.yml` 是追加的覆盖层）。以前是各人凭记忆敲 `-f`，
# 迟早漏一个，然后出现"怎么数据库没了"这种排查半天的问题。
#
# 用法:
#   scripts/stack.sh up            # 起（或应用新配置）
#   scripts/stack.sh down          # 停（**不删卷**）
#   scripts/stack.sh ps            # 看状态
#   scripts/stack.sh restart <服务...>
#   scripts/stack.sh logs <服务>
#   scripts/stack.sh config        # 渲染出最终配置，排查挂载/环境变量用
#
# 需要更底层的操作时照旧直接调 docker compose，只要记得带上同一组文件：
#   docker --context colima-pearnly compose \
#     -f infrastructure/compose/00-base.yml -f infrastructure/compose/01-bench.yml \
#     -f infrastructure/compose/02-keycloak.yml \
#     -f infrastructure/compose/04-dev-mounts.yml <命令>
set -euo pipefail

CTX="${PEARNLY_CTX:-colima-pearnly}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
FILES=(
  "$HERE/infrastructure/compose/00-base.yml"
  "$HERE/infrastructure/compose/01-bench.yml"
  "$HERE/infrastructure/compose/02-keycloak.yml"
  "$HERE/infrastructure/compose/04-dev-mounts.yml"
)

args=()
for f in "${FILES[@]}"; do args+=(-f "$f"); done

cmd="${1:?用法: $0 up|down|ps|restart <服务...>|logs <服务>|config}"
shift || true

docker --context "$CTX" compose "${args[@]}" "$cmd" "$@"
