"""ERP 接 MR.ERP 的对外入口（薄入口：解析参数、调本域服务；权限与 :data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 都在服务里判）。

都只限 ERP 管理员；功能关着时一律 403。没有任何入口收或回 MR.ERP 密码：登录只在 CC 桌面版里授权（共用通道
`pearnly_core.cc_desktop.api.mrerp_login_ticket` → `machine.mrerp_login`，经本产品的钩子写进连接）。
"""
import json

import frappe

from pearnly_erp_portal.mrerp import catalog, choices, connection


@frappe.whitelist(methods=["POST"])
def configure(system_url=None, connection_name=None, adapter=None, request_key=None, label=None, config=None,
              name=None, expected_revision=None):
    """建 / 改连接网址 → 连接（不含密码）。两种调法同一个入口：
    - 旧：``system_url`` / ``connection_name``；
    - CC 设置 → 连接的 MR.ERP 一行（`frontend/assistant/src/components/mrerp-connect.tsx`，同 Cowork 连接接口）：
      ``adapter="mrerp"``、``config={"system_url": …}``、``name``、``request_key``（没给 ``name`` 时同一请求号重发回同一条）。
    回 CC 那一行的形状（name / adapter / main / config / credential）并带旧视图的键（connection / system_url / enabled）。"""
    if adapter not in (None, "", "mrerp"):
        frappe.throw(frappe._("Invalid request"))
    if isinstance(config, str):
        try:
            config = json.loads(config or "{}")
        except ValueError:
            frappe.throw(frappe._("Invalid request"))
    url = system_url if system_url is not None else (config or {}).get("system_url") if isinstance(config, dict) else None
    saved = connection.configure_once(url, connection_name or name, request_key)
    return {**connection.record(saved, connection.main_name()), **connection.view(saved)}


@frappe.whitelist()
def records(start=0, limit=50):
    """CC 设置 → 连接的 MR.ERP 一行读的连接列表（只限管理员，服务端分页；主连接标 ``main``）。"""
    return connection.records(start, limit)


@frappe.whitelist(methods=["POST"])
def request_refresh(name, request_key=None):
    """CC 设置里存下网址或授权后读一次这条连接的账套目录（同 :func:`refresh_catalog`；同一连接只排一件）。人点的入口：
    采集时先清被踢标记、这次照常登录（11-11）。"""
    return catalog.refresh(name, manual=True)


@frappe.whitelist()
def connection_view(connection_name):
    connection.require_admin()
    return connection.view(connection_name)


@frappe.whitelist(methods=["POST"])
def refresh_catalog(connection_name):
    return catalog.refresh(connection_name, manual=True)  # 人点的入口（11-11）


@frappe.whitelist()
def account_sets(connection_name, start=0, page_length=20, search=None):
    return catalog.account_sets(connection_name, start, page_length, search)


@frappe.whitelist(methods=["POST"])
def choose_ledger(company, connection_name, account_set):
    """这家公司的账本改成这份 MR.ERP 账套。"""
    return catalog.choose(company, connection_name, account_set)


@frappe.whitelist(methods=["POST"])
def detach(company):
    catalog.detach(company)
    return {"company": company}


@frappe.whitelist(methods=["POST"])
def read_choices(company):
    """去 MR.ERP 读这家公司账套的导入菜单与主档（后台）。人点的入口：读取时先清被踢标记、这次照常登录（11-11）。"""
    return choices.request_read(company, manual=True)


@frappe.whitelist()
def company_choices(company):
    """这家公司的导入菜单与主档默认值视图（共用层 `menus.view` / `masters.view` 的形状）。"""
    connection.require_admin()
    row = choices.company_row(company)
    if not row or not row.mrerp_enabled:
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    menus, defaults = choices.views(row)
    return {"menus": menus, "defaults": defaults}


@frappe.whitelist(methods=["POST"])
def choose(company, kind, subject, value):
    """管理员在公司上预先选一项（不必等单据停下）。"""
    connection.require_admin()
    if choices.choose(company, kind, subject, value) is None:
        frappe.throw(frappe._("Invalid request"))
    return company_choices(company)
