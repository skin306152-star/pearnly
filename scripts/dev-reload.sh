#!/usr/bin/env bash
# 代码改完 → 生效，一条命令。
#
# 这是"改完我要马上验证"的落点。做的事就三件，顺序不能换：
#   1. （可选）`bench migrate`：改了 DocType / field_order 这类**结构**才需要
#   2. `bench clear-cache`：hooks、DocType 元数据都缓存在 Redis 里，不清就是旧行为
#      （容器重建**不会**清 —— 这个坑踩过好几次，表现为"代码改了、行为没变"）
#   3. 重启该运行组的 web/worker/scheduler：gunicorn 是 `--preload`，
#      进程不重启就一直用启动时加载的那份代码
#
# 因为 app 源码是**挂载进去**的（infrastructure/compose/04-dev-mounts.yml），
# 所以这里**不需要重建镜像** —— 这才是快的原因。
#
# 前端不受这里影响：改前端只要 `cd frontend && npm run build`（约 2.5 秒），
# 构建产物本身就是挂载进去的，连重启都不用。
#
# 用法:
#   scripts/dev-reload.sh <组|all> [--sites a,b] [--no-cache] [--migrate]
#     组 = cowork | erp | dms | control | all
#     --sites      只处理这几个站点（迭代时通常只关心自己在看的那一个，能省一半时间）
#     --no-cache   跳过 clear-cache。**只在你确定没改 hooks.py / DocType** 时用，
#                  这时只要重启进程就够（约 1 秒）
#     --migrate    先跑 bench migrate（改了 DocType 结构才需要）
#   例: scripts/dev-reload.sh dms                              # 安全档，几秒
#       scripts/dev-reload.sh dms --sites qd65956.localhost    # 只弄一个站点
#       scripts/dev-reload.sh dms --sites qd65956.localhost --no-cache   # 最快
#       scripts/dev-reload.sh all --migrate                    # 改过 DocType
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

dc() { docker --context "$CTX" compose "${args[@]}" "$@"; }

group="${1:?用法: $0 <组|all> [--migrate]}"
shift || true
migrate=0
use_cache=1
only_sites=""
while [ $# -gt 0 ]; do
  case "$1" in
    --migrate)  migrate=1 ;;
    --no-cache) use_cache=0 ;;
    --sites)    shift; only_sites="${1:-}" ;;
    *) echo "不认识的参数：$1" >&2; exit 2 ;;
  esac
  shift
done

case "$group" in
  all)      groups=(cowork erp dms control) ;;
  cowork|erp|dms|control) groups=("$group") ;;
  *) echo "组只能是 cowork / erp / dms / control / all" >&2; exit 2 ;;
esac

start=$(date +%s)

# 这个项目里实际存在哪些服务：各组差一个（控制面**没有** worker）。
# 先取一次名单再按名单重启 —— 否则 `restart control-worker` 会失败，而失败会被
# `|| true` 吞掉，结果是"控制面的 scheduler 根本没重启"这种**静默半成功**。
have_services=$(dc config --services 2>/dev/null || true)
existing() {  # 传入候选名字，输出其中真实存在的那几个
  for name in "$@"; do
    printf '%s\n' "$have_services" | grep -qx "$name" && printf '%s ' "$name"
  done
}

for g in "${groups[@]}"; do
  echo "── $g"
  # 这个组里有哪些站点：直接列 sites 目录（比维护一份名单可靠）
  sites=$(dc exec -T "$g-bench" bash -lc \
    'ls /home/frappe/frappe-bench/sites | grep -vE "^(assets|apps.json|apps.txt|common_site_config.json)$"' \
    2>/dev/null || true)
  if [ -n "$only_sites" ]; then
    # 只留用户点名的那几个（按行匹配，避免子串误命中）
    filtered=""
    for want in ${only_sites//,/ }; do
      for have in $sites; do
        [ "$want" = "$have" ] && filtered="$filtered $have"
      done
    done
    sites="$filtered"
  fi
  if [ -z "$sites" ]; then
    echo "   站点目录读不到，跳过（容器在跑吗？）"
    continue
  fi

  for s in $sites; do
    if [ "$migrate" = "1" ]; then
      echo "   migrate $s"
      dc exec -T "$g-bench" bench --site "$s" migrate >/dev/null 2>&1 || {
        echo "   [错误] $s 迁移失败，停下来别往下跑" >&2
        dc exec -T "$g-bench" bench --site "$s" migrate 2>&1 | tail -5
        exit 1
      }
    fi
    if [ "$use_cache" = "1" ]; then
      # 缓存必须清：hooks / DocType 元数据都在 Redis 里，容器重建不会清
      # （表现是"代码改了、行为没变"）。没动 hooks/DocType 时可以用 --no-cache 跳过。
      dc exec -T "$g-bench" bench --site "$s" clear-cache >/dev/null 2>&1 || true
      # 再显式清一次 meta：DocType 的客户端脚本（表单里那个 `__js`）缓在 meta 上，
      # 只跑 clear-cache 有时不够 —— 实测改完 doctype 的 .js，表单还在跑旧脚本，
      # 表现是"新加的按钮不出现"，很难往缓存上想。
      dc exec -T "$g-bench" bench --site "$s" execute frappe.clear_cache >/dev/null 2>&1 || true
      echo "   clear-cache $s"
    fi
  done

  # socketio 不含我们的代码，不用重启
  targets=$(existing "$g-web" "$g-worker" "$g-scheduler")
  [ -n "$targets" ] || { echo "   [错误] $g 组一个服务都没找到" >&2; exit 1; }
  # 按名单逐个重启：`restart a b c` 是**顺序**执行的，一个卡住会拖住后面的
  for t in $targets; do
    dc restart "$t" >/dev/null 2>&1 || echo "   [警告] 重启 $t 失败" >&2
  done
  echo "   已重启 ${targets}"
done

echo "[done] 用时 $(( $(date +%s) - start )) 秒"
echo "       前端改动不在这个回路里：cd frontend && npm run build"
