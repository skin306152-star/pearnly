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

  # “设置已完成”的真正开关不是 System Settings，而是 Installed Application.is_setup_complete：
  #   frappe/__init__.py:is_setup_complete() 要求 Installed Application 里
  #   app_name ∈ (frappe, erpnext) 的行全部为 1；System Settings.setup_complete 只是这个
  #   判断结果的回写（setup_wizard.py:406），单独改它没有任何效果（我第一版就栽在这）。
  #   这里对应 setup_wizard.py:177 的 frappe.db.set_value("Installed Application",
  #   {"app_name": app_name}, "is_setup_complete", 1)。
  # 用 bench execute 而不是 bench console：console 会吞掉多行脚本（连报错都不给），踩过。
  bench "$site" execute frappe.db.sql --args \
    '["update `tabInstalled Application` set is_setup_complete=1 where app_name in (%s, %s)", ["frappe", "erpnext"]]' >/dev/null
  bench "$site" execute frappe.db.set_single_value \
    --kwargs '{"doctype":"System Settings","fieldname":"setup_complete","value":1}' >/dev/null
  bench "$site" execute frappe.db.commit >/dev/null

  # desk 首页：必须是真实存在的 desk 页面
  bench "$site" execute frappe.db.set_default \
    --kwargs '{"key":"desktop:home_page","val":"desktop"}' >/dev/null 2>&1
  bench "$site" execute frappe.db.commit >/dev/null 2>&1
  bench "$site" clear-cache >/dev/null 2>&1

  got=$(bench "$site" execute frappe.db.get_default --kwargs '{"key":"desktop:home_page"}' 2>&1 | tail -1)
  # 验证必须读 is_setup_complete()（实际被 boot/desk 使用的那个），不是 System Settings
  done_flag=$(bench "$site" execute frappe.is_setup_complete 2>&1 | grep -v '^[[:space:]]*$' | tail -1 | tr 'A-Z' 'a-z')
  echo "  desktop:home_page = $got / is_setup_complete = $done_flag"
  if [ "$got" != "desktop" ] || [ "$done_flag" != "true" ]; then
    echo "  [错误] desk 首页没设对，/desk 仍会闪屏" >&2
    exit 1
  fi
done

echo "[done] 收尾完成；闪屏回归验证：node scripts/sample-verify-desk-stable.mjs <base-url>"
