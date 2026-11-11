"""Cowork 核账本的 MR.ERP 一侧（hooks ``pearnly_ledger_check["mrerp"]``，共用入口 `pearnly_core.ledger_tickets.check_present`）。

``connection`` 是 Cowork 的 MR.ERP 连接、``account_set_key`` 是 ``comidyear:seldb``。按本人当前对这条连接的授权读凭据与这份账套读到过的
导入菜单（同 `connections.mrerp_menu_reader.run`），提交后再去 MR.ERP（对外请求期间不持行锁）；账号正被 CC 桌面版代录占着就不去，
回空（共用入口记 unknown）。查法与 gone 的规矩见 `pearnly_core.mrerp.ledger_check`。只读：不写账、不改推送记录、不清被踢标记。
"""
import frappe
from frappe.utils.password import get_decrypted_password

from pearnly_core.mrerp import ledger_check as mrerp_ledger
from pearnly_cowork.connections import access, model, state
from pearnly_cowork.connections import service as connections
from pearnly_cowork.push.mrerp import runner

_DEFAULT_URL = "https://www.mrerp4sme.com"


def check(connection, account_set_key, items, *, ledger="mrerp", wait=60) -> dict:
    if ledger != "mrerp" or not connection or connections.adapter_of(connection) != "mrerp":
        return {}
    try:
        doc = state.read(state.CONNECTION, connection, lock=False)
        access.require_connection(doc, actor=frappe.session.user, lock=False)
    except (frappe.PermissionError, frappe.ValidationError, frappe.DoesNotExistError):
        frappe.db.rollback()
        frappe.clear_messages()
        return {}
    if not doc.enabled:
        return {}
    config = model.parse(doc.config_json, {})
    lease = {"system_url": str(config.get("system_url") or _DEFAULT_URL).strip(),
             **{key: get_decrypted_password(state.CONNECTION, doc.name, "credential_" + key,
                                            raise_exception=False) or "" for key in ("username", "password")}}
    menu_options = doc.get("mrerp_menu_options")
    frappe.db.commit()  # 对外请求前结束本事务，不在等 MR.ERP 时占着读快照
    if runner.account_busy(lease["system_url"], lease["username"]):
        return {}  # 代录批次正用着这个账号：不去抢（同推送）
    return mrerp_ledger.check(lease, connections.mrerp_account_key(account_set_key), items,
                              menu_options=menu_options, wait=wait)
