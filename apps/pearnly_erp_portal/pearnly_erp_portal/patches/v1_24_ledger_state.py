"""单据上的「账本状态 / 账本单号」改成通用列（评估 2026-11-02 §12 第 6 条，主会话审定 §12.1 #6）：采购、销售、出入库、盘点、
收银日汇总五种单原来的 Express 专用栏 ``express_*`` → 通用的 ``ledger_*``，另加 ``ledger_target`` 记这张单的账本是哪一个
（``express``；以后 MR.ERP 是 ``mrerp``）。一张单只会有一个账本，一列一义。

- 模型同步之后跑：同步已建好 ``ledger_*`` 列，旧列还在库里（同步不删列）。逐种单把旧列抄到新列，抄过的单记 ``ledger_target``
  = ``express``；旧列一项都没有值的单（从没排进过 Express）不动，账本留空。
- 幂等：只抄 ``ledger_target`` 为空且旧列有值的行 —— 抄过的、改列后由代码新排上的（排队时就记了账本）都跳过，重跑不会拿旧值
  盖掉新状态。原生列表 / 报表视图里存着的旧列名（个人列表设置、报表、属性设置）照 Frappe ``rename_field`` 的同一套改成新列名。
- 旧列这一版**保留不删**（不在 DocType 里、代码不再读写），作回退用：回退前先跑 :func:`rollback` 把新列抄回旧列、清掉账本标记
  和这条补丁的执行记录，再换回旧镜像；以后再升级时这条补丁重跑、重新抄。确认不回退后由后续补丁删列。
- 打印每种单抄了几行（发布回读用）。
"""
import frappe

DOCTYPES = ("Purchase Document", "ERP Sales Document", "ERP Stock Entry", "ERP Stock Count", "ERP POS Day Summary")
COLUMNS = ("state", "docnum", "reason", "sent_at", "job", "book", "needed")
VOID_COLUMNS = ("void_state", "void_reason", "void_job")  # 出入库单在 Express 作废原单（只有出入库单有）
DATETIME_COLUMNS = ("sent_at",)
TARGET = "express"
PATCH = "pearnly_erp_portal.patches.v1_24_ledger_state"


def _pairs(doctype):
    """``[(旧列, 新列)]``：两列库里都在的。"""
    names = COLUMNS + (VOID_COLUMNS if doctype == "ERP Stock Entry" else ())
    return [(f"express_{name}", f"ledger_{name}") for name in names
            if frappe.db.has_column(doctype, f"express_{name}") and frappe.db.has_column(doctype, f"ledger_{name}")]


def _filled(column) -> str:
    name = column.split("_", 1)[1]
    return f"`{column}` IS NOT NULL" if name in DATETIME_COLUMNS else f"COALESCE(`{column}`, '') != ''"


def execute():
    from frappe.model.utils.rename_field import (update_property_setters, update_reports,
                                                 update_user_settings, update_users_report_view_settings)

    for doctype in DOCTYPES:
        if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, "ledger_target"):
            continue
        pairs = _pairs(doctype)
        if pairs:
            assign = ", ".join(f"`{new}` = `{old}`" for old, new in pairs)
            filled = " OR ".join(_filled(old) for old, _new in pairs)
            frappe.db.sql(f"""UPDATE `tab{doctype}` SET {assign}, `ledger_target` = %s
                WHERE COALESCE(`ledger_target`, '') = '' AND ({filled})""", (TARGET,))
            copied = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{doctype}` WHERE `ledger_target` = %s", (TARGET,))[0][0]
            print(f"{doctype}: ledger columns hold {copied} express rows")
        for old, new in [*pairs, ("express_section", "ledger_section")]:
            update_reports(doctype, old, new)
            update_users_report_view_settings(doctype, old, new)
            update_property_setters(doctype, old, new)
            update_user_settings(doctype, old, new)
    frappe.db.commit()


def rollback():
    """回退前跑（新镜像还在时，``bench --site <站点> execute pearnly_erp_portal.patches.v1_24_ledger_state.rollback``）：新列抄回
    旧列，清掉账本标记与这条补丁的执行记录，存着的视图改回旧列名。之后换回旧镜像，旧代码读写旧列；再升级时本补丁重跑、重新抄。"""
    from frappe.model.utils.rename_field import (update_property_setters, update_reports,
                                                 update_user_settings, update_users_report_view_settings)

    for doctype in DOCTYPES:
        if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, "ledger_target"):
            continue
        pairs = _pairs(doctype)
        if pairs:
            assign = ", ".join(f"`{old}` = `{new}`" for old, new in pairs)
            frappe.db.sql(f"UPDATE `tab{doctype}` SET {assign}, `ledger_target` = NULL")
            print(f"{doctype}: copied the ledger columns back to the express columns")
        for old, new in [*pairs, ("express_section", "ledger_section")]:
            update_reports(doctype, new, old)
            update_users_report_view_settings(doctype, new, old)
            update_property_setters(doctype, new, old)
            update_user_settings(doctype, new, old)
    frappe.db.delete("Patch Log", {"patch": PATCH})
    frappe.db.commit()
