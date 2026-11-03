"""MR.ERP 导入菜单与主档的服务端读取：登录、进选定的账套、打开三个导入页存下菜单选项，再读主档的可用值。

为什么要读（2026-09-28，MR.ERP 推送假成功）：导入菜单号是各公司自己的，哪几个在当前年度关联了日记账
只在导入页上看得到（规则见 `pearnly_core.mrerp.menus`）。录入提交前某类单据有多个已关联菜单、又没选过时，
要把选项给人选一次；推送时每次也会读导入页核对，读到的同样存回来（`push/mrerp/runner.py`）。
主档（部门 / 员工 / 科目等默认值，规则见 `pearnly_core.mrerp.masters`）同一次登录里读，给同一个选择弹窗用。

只读：不上传、不导入，MR.ERP 里什么都不改。与推送、目录采集共用同一把账号锁（老 PHP 单账号单会话，
并发登录会互踢）；对外请求期间不持任何行锁（读凭据一笔提交、存结果一笔提交）。
"""
from __future__ import annotations

import logging

import frappe
from frappe.utils.password import get_decrypted_password
from . import access, model, service, state
# 读取本身（纯网络）在共用层（2026-11-03 搬去，ERP 接 MR.ERP 同用一份）；本模块对外名 `read` 照旧。
from pearnly_core.mrerp.menu_read import read  # noqa: F401

logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 240
_DEFAULT_URL = "https://www.mrerp4sme.com"


def dispatch(request):
    """`service.request_mrerp_menu` 的申请落定后排一个读取任务；同一连接同一账套只排一个，提交后可见。"""
    frappe.enqueue("pearnly_cowork.connections.mrerp_menu_reader.run", queue="short",
                   job_id=f"cowork-mrerp-menus::{request['connection']}::{request['account_set_key']}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS,
                   connection=request["connection"], account_set_key=request["account_set_key"],
                   actor=request["actor"])


def run(connection, account_set_key, actor):
    """后台任务入口：按申请人当前的授权读凭据 → 不持锁读 MR.ERP → 存结果。永不抛到队列。"""
    frappe.set_user(actor)
    try:
        doc = state.read(state.CONNECTION, connection)
        access.require_connection(doc, actor=actor)
        if doc.adapter != "mrerp" or not doc.enabled:
            frappe.db.rollback()
            return
        config = model.parse(doc.config_json, {})
        lease = {"system_url": str(config.get("system_url") or _DEFAULT_URL).strip(),
                 **{key: get_decrypted_password(state.CONNECTION, doc.name, "credential_" + key,
                                                raise_exception=False) or "" for key in ("username", "password")}}
    except frappe.PermissionError:
        frappe.db.rollback()
        return
    frappe.db.commit()
    pages, fields, error, master_error = read(lease, account_set_key)
    service.observe_mrerp_menus(connection, account_set_key, pages, error=error)
    service.observe_mrerp_masters(connection, account_set_key, fields, error=error or master_error)
    frappe.db.commit()
