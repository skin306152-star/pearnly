#!/usr/bin/env bash
# 配一个"本地样板"开户目标，供发号页选择。
#
# 生产目标等服务器就绪后再加一条，把地址换成服务器上的身份服务即可——
# **代码不用改**，发号页会自己列出来。
#
# 用法: PEARNLY_BIZ_ADMIN_SECRET=... scripts/sample-ensure-target.sh
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
SITE=control.localhost
SECRET="${PEARNLY_BIZ_ADMIN_SECRET:?需要环境变量 PEARNLY_BIZ_ADMIN_SECRET}"

printf 'import frappe; print("RESULT=" + frappe.get_attr("pearnly_control.bootstrap.ensure_target")(admin_client_secret="%s"))\n' "$SECRET" \
  | docker --context "$CTX" compose -f "$COMPOSE" exec -T control-bench \
  bench --site "$SITE" console 2>&1 | grep -oE 'RESULT=[A-Z]+' | tail -1
