"""ERP 核账本的 MR.ERP 一侧（hooks ``pearnly_ledger_check["mrerp"]``，共用入口 `pearnly_core.ledger_tickets.check_present`）。

``connection`` 是 ERP 的 MR.ERP 连接（公司上的 ``mrerp_connection``）、``account_set_key`` 是 ``comidyear:seldb``（公司上的
``mrerp_account_set``）；调用方已核过本人能用这家公司。MR.ERP 账本开关（:data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED`）
关着时不去、回空（共用入口记 unknown）。凭据与这份账套读到过的导入菜单照导入任务的取法（`mrerp.connection.lease`、
`mrerp.choices`），提交后再去 MR.ERP；账号正被 CC 桌面版代录占着就不去。查法与 gone 的规矩见 `pearnly_core.mrerp.ledger_check`。
只读：不写账、不改单据、不清被踢标记。
"""
import frappe

from pearnly_core.mrerp import ledger_check as mrerp_ledger
from pearnly_core.mrerp.session import lock_key
from pearnly_core.session_lock import operator_holds
from pearnly_erp_portal.mrerp import choices
from pearnly_erp_portal.mrerp import connection as connections


def check(connection, account_set_key, items, *, ledger="mrerp", wait=60) -> dict:
    if ledger != "mrerp" or not connection or not connections.mrerp.enabled():
        return {}
    lease = connections.lease(connection)
    menu_options, _masters = choices.menu_options_of(connection)
    frappe.db.commit()  # 对外请求前结束本事务，不在等 MR.ERP 时占着读快照
    if not lease.get("username") or not lease.get("password"):
        return {}
    if operator_holds(lock_key(lease["system_url"], lease["username"])):
        return {}  # 代录批次正用着这个账号：不去抢（同导入）
    return mrerp_ledger.check(lease, str(account_set_key or ""), items, menu_options=menu_options, wait=wait)
