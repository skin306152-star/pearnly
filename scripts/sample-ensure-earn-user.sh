#!/usr/bin/env bash
# 控制面登录账号写死：earn / earn。
#
# 为什么可以这么简单：控制面**只在本机跑、只自己用、永远不上服务器**（ADR-004）。
# 给这种后台配一整套身份服务是纯负担。业务站点的统一身份不受影响。
#
# 用法: scripts/sample-ensure-earn-user.sh
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
SITE=control.localhost

dc() { docker --context "$CTX" compose -f "$COMPOSE" "$@"; }

# 用单行调用。`bench console` 会吞多行脚本——多行版曾经"看起来成功"其实没建号。
echo "[earn] 建号（已存在则重置密码）"
printf 'import frappe; print("RESULT=" + frappe.get_attr("pearnly_control.bootstrap.ensure_local_admin")())\n' \
  | dc exec -T control-bench bench --site "$SITE" console 2>&1 | grep -oE 'RESULT=[A-Z]+' | tail -1

echo "[earn] 验证本地登录（登录名 earn@pearnly.local，密码 earn）"
code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 \
  -H "Host: $SITE" -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'usr=earn@pearnly.local' --data-urlencode 'pwd=earn' \
  "http://127.0.0.1:8104/api/method/login")
echo "  登录接口 HTTP=$code （200 = 成功）"
