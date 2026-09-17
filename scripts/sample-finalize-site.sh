#!/usr/bin/env bash
# 建站收尾：把 desk 首页从 setup-wizard 摘出来，标记设置已完成。
#
# 背景（踩过的坑，症状是 /desk 整页闪屏、无限重载）：
#   Frappe 建站时 frappe/utils/install.py 会写默认值 desktop:home_page=setup-wizard，
#   本项目的站点从来不跑交互式设置向导，所以这个值一直留在库里。
#   desk 首页 = boot.home_page = desktop:home_page = setup-wizard 时：
#     1) /desk 无路由 -> pageview 渲染 setup-wizard 页面；
#     2) 该页面脚本第一句是“若 setup_complete 就 location.href = default_path || '/desk'”，
#        而 apps_data.default_path 为空 -> 跳回 /desk -> 再次渲染 setup-wizard -> 无限重载；
#     3) 且 desk.js 的 make_nav_bar() 在 home_page == 'setup-wizard' 时故意不建工具栏，
#        所以页面只剩骨架、没有菜单（看起来像“一直闪”）。
#   本 build 里真实存在的 desk 页面只有 desktop 和 setup-wizard，
#   所以正确值是 desktop（上游向导写的 workspace 在这里不存在，会落回 desktop）。
#
# 用法: scripts/sample-finalize-site.sh <组> <站点...>
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml

group=${1:?用法: $0 <组> <站点...>}
shift
if [ $# -eq 0 ]; then
  echo "至少给一个站点名" >&2
  exit 2
fi

bench() { docker --context "$CTX" compose -f "$COMPOSE" exec -T "$group-bench" bench --site "$1" "${@:2}"; }

for site in "$@"; do
  echo "[finalize] $site"

  # 做法只有一份实现：pearnly_core.provisioning.finalize_site()
  # （开通流程里的 finalize_site 步骤调的是同一个函数，不两处各写一套）
  out=$(bench "$site" execute pearnly_core.provisioning.finalize_site 2>&1 | tail -2)
  echo "  $out"
  printf '%s' "$out" | grep -q "home_page" || {
    echo "  [错误] 收尾没成功，/desk 仍会闪屏" >&2
    exit 1
  }
done

echo "[done] 收尾完成；闪屏回归验证：node scripts/sample-verify-desk-stable.mjs <base-url>"
