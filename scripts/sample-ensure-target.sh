#!/usr/bin/env bash
# 配一个"本地样板"开户目标，供发号页选择。
#
# 生产目标等服务器就绪后再加一条，把地址换成服务器上的身份服务即可——
# **代码不用改**，发号页会自己列出来。
#
# 两个密钥都是 **realm 级**（同 realm 下所有租户共用一套）：
#   PEARNLY_BIZ_ADMIN_SECRET            —— `pearnly-admin`，建站/改密用管理员令牌
#   PEARNLY_BIZ_PASSWORD_CHECK_SECRET   —— `pearnly-password-check`，校验"当前密码"
# 两个都要给：只给管理密钥的话，开出来的租户能登录、改不了密码（踩过）。
#
# 用法: PEARNLY_BIZ_ADMIN_SECRET=... PEARNLY_BIZ_PASSWORD_CHECK_SECRET=... \
#       scripts/sample-ensure-target.sh
set -euo pipefail

CTX=colima-pearnly
COMPOSE=infrastructure/compose/01-bench.yml
SITE=control.localhost
SECRET="${PEARNLY_BIZ_ADMIN_SECRET:?需要环境变量 PEARNLY_BIZ_ADMIN_SECRET}"
CHECK_SECRET="${PEARNLY_BIZ_PASSWORD_CHECK_SECRET:?需要环境变量 PEARNLY_BIZ_PASSWORD_CHECK_SECRET}"

# Cowork 自己的账号池（可选；`scripts/sample-cowork-realm.py` 建池并把两个密钥写到 ~/.config/pearnly/cowork-realm.env）。
COWORK_SECRET="${PEARNLY_COWORK_ADMIN_SECRET:-}"
COWORK_CHECK_SECRET="${PEARNLY_COWORK_PASSWORD_CHECK_SECRET:-}"

printf 'import frappe; print("RESULT=" + frappe.get_attr("pearnly_control.bootstrap.ensure_target")(admin_client_secret="%s", password_check_client_secret="%s", cowork_admin_client_secret="%s" or None, cowork_password_check_client_secret="%s" or None))\n' "$SECRET" "$CHECK_SECRET" "$COWORK_SECRET" "$COWORK_CHECK_SECRET" \
  | docker --context "$CTX" compose -f "$COMPOSE" exec -T control-bench \
  bench --site "$SITE" console 2>&1 | grep -oE 'RESULT=[A-Z]+' | tail -1
