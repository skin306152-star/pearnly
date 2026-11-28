"""MR.ERP 导入菜单与主档默认值，按 ERP 公司选一次、记住复用（评估 §14.6；规则是共用层 `pearnly_core.mrerp.menus` / `masters`，
与 Cowork 同一份）。

- 读到的选项记在连接上（``menu_options`` / ``master_options``，按账套键分）：管理员申请读一次（后台任务，网络那一半是共用层
  `pearnly_core.mrerp.menu_read`），每次导入读到的也存回来（`push.run`）。
- 选过的值记在公司上（``mrerp_menus`` / ``mrerp_defaults``，同样按账套键分）。一家公司可挂多本年度账套（公司接入第 3 批，
  `mrerp.catalog.pick` 按单据日期选），每本各自读、各自记——年度库是各自的一份 MR.ERP 数据库，菜单号与主档代码按库算。
  只收最近一次读到、当前可用的值；导入时 MR.ERP 那边按读到的再核对，失效就停下重问（同 Cowork）。
- ``book``：这张单选中的那本（`catalog.pick` 回的 ``{"connection", "key", ...}``）；不给时是公司上记的那一本
  （``mrerp_connection`` / ``mrerp_account_set``，旧调用方与没走选账套的入口）。
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.mrerp import masters as master_rules
from pearnly_core.mrerp import menus as menu_rules

from pearnly_erp_portal.mrerp import connection as connections

COMPANY = "ERP Company"
MENU, DEFAULT = "mrerp_menu", "mrerp_default"
JOB_TIMEOUT_SECONDS = 240


def company_row(company):
    return frappe.db.get_value(COMPANY, company, ["name", "company_name", "tax_id", "branch", "express_enabled",
                                                 "mrerp_enabled", "mrerp_connection", "mrerp_account_set",
                                                 "mrerp_linked_at", "mrerp_menus", "mrerp_defaults"],
                               as_dict=True) if company else None


def _options(connection, lock=False):
    row = frappe.db.get_value(connections.CONNECTION, connection, ["menu_options", "master_options"], as_dict=True,
                              for_update=lock) if connection else None
    return (row.menu_options, row.master_options) if row else (None, None)


def menu_options_of(connection) -> tuple:
    """（这条连接各账套读到过的导入页选项, 主档可用值）原始记录（核账本按方向找已关联的菜单用，`mrerp.ledger_check`）。"""
    return _options(connection)


def place(row, book=None) -> tuple:
    """（连接, 账套键）：给了选中的那本用那本，没给用公司上记的那一本。"""
    if book:
        return book.get("connection") or "", str(book.get("key") or "")
    return (row.mrerp_connection or "", row.mrerp_account_set or "") if row else ("", "")


def views(row, book=None) -> tuple:
    """（菜单视图, 主档视图）：共用层 `menus.view` / `masters.view` 的形状。"""
    connection, account = place(row, book)
    menu_options, master_options = _options(connection)
    return (menu_rules.view(row.mrerp_menus, menu_options, account),
            master_rules.view(row.mrerp_defaults, master_options, account))


def chosen(row, book=None) -> tuple:
    """准备导入时冻结进载荷的：（{单据类型: 菜单号}, {主档字段: 代码}）。"""
    account = place(row, book)[1]
    return menu_rules.chosen_map(row.mrerp_menus, account), master_rules.chosen_map(row.mrerp_defaults, account)


def request_read(company, *, manual):
    """申请去 MR.ERP 读这家公司账套的导入菜单与主档（只限管理员；同一连接同一账套只排一件）。

    ``manual``：人点的（`api.read_choices`，11-11）—— 读取时先清被踢标记、这次照常登录；由调用入口显式给。"""
    connections.require_admin()
    row = company_row(company)
    if not row or not row.mrerp_enabled or not row.mrerp_connection or not row.mrerp_account_set:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frappe.enqueue("pearnly_erp_portal.mrerp.choices.run_read", queue="short",
                   job_id=f"erp-mrerp-menus::{row.mrerp_connection}::{row.mrerp_account_set}", deduplicate=True,
                   enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, connection=row.mrerp_connection,
                   account_set=row.mrerp_account_set, manual=bool(manual))
    return {"company": company, "requested_at": str(now_datetime())}


def read_topic(connection, account_set) -> str:
    """这份账套导入页菜单与主档读取那件的主题（LINE 等「正在读取 MR.ERP」时叫醒，`pearnly_core.line_ui.receipt`）。"""
    return f"erp:mrerp-masters:{connection}:{account_set}"


def run_read(connection, account_set, manual=False):
    """（后台任务）读导入页菜单与主档（不持行锁）→ 存回连接。永不抛到队列。"""
    if not connections.mrerp.enabled():
        return
    from pearnly_core.mrerp import menu_read  # 连网的共用层只在读取任务里载入（同 `push.run`）

    lease = connections.lease(connection)
    frappe.db.commit()
    if not lease:
        return
    # 后台任务自己不算人点的：只有排任务的入口说了是才算（旧任务没带这个参数照不算）
    pages, fields, error, master_error = menu_read.read(lease, account_set, manual=bool(manual))
    observe(connection, account_set, pages, fields, error=error, master_error=master_error)
    frappe.db.commit()
    from pearnly_core.line_ui import receipt as line_receipt

    line_receipt.wake_topic(read_topic(connection, account_set))  # LINE 上在等收款方式（用户 11-22）


def observe(connection, account_set, pages, fields, error=None, master_error=None):
    """存下这次读到的导入页选项与主档可用值（读取任务与导入共用）；调用方提交。

    两栏是按账套键分的整份记录、整份写回：先加锁读（④ A2 #17，11-28）。同一条连接上两本账套同时导入 / 读取时，普通读拿到的是
    本事务读视图里的旧整份，后写的那路会把先写的那本刚存的选项盖掉；加锁读到最新的整份，只并本账套那一格（共用层 `observe`
    只改 ``account_set`` 那一项）。"""
    if not account_set or not connection:
        return
    if not (pages or error) and not (fields is not None or error or master_error):
        return  # 这次什么都没读到：不写，也不锁连接行
    menu_options, master_options = _options(connection, lock=True)
    at = now_datetime().isoformat()
    values = {}
    if pages or error:
        values["menu_options"] = json.dumps(menu_rules.observe(menu_options, account_set, pages, at=at, error=error),
                                            ensure_ascii=False)
    if fields is not None or error or master_error:
        values["master_options"] = json.dumps(master_rules.observe(master_options, account_set, fields, at=at,
                                                                   error=error or master_error), ensure_ascii=False)
    if values:
        frappe.db.set_value(connections.CONNECTION, connection, values, update_modified=False)


def choose(company, kind, subject, value, book=None):
    """记下这家公司一项选择（``kind``：``mrerp_menu`` / ``mrerp_default``），记在 ``book`` 那本（没给是公司上记的那本）名下。
    只收最近一次读到的可用值；不对回 None。权限由调用方核（单据上的开单权，同 `express.api.answer`）。"""
    connections.require_enabled()
    row = company_row(company)
    if not row or not row.mrerp_enabled:
        return None
    connection, account = place(row, book)
    menu_options, master_options = _options(connection)
    actor, at = frappe.session.user, now_datetime().isoformat()
    if kind == MENU:
        picked = menu_rules.choose(row.mrerp_menus, account, subject, value, menu_options, actor=actor, at=at)
        field = "mrerp_menus"
    elif kind == DEFAULT:
        picked = master_rules.choose(row.mrerp_defaults, account, subject, value, master_options, actor=actor, at=at)
        field = "mrerp_defaults"
    else:
        return None
    if picked is None:
        return None
    frappe.db.set_value(COMPANY, company, field, json.dumps(picked, ensure_ascii=False), update_modified=False)
    return picked


__all__ = ["DEFAULT", "MENU", "chosen", "choose", "company_row", "menu_options_of", "observe", "place", "request_read",
           "run_read", "views"]
