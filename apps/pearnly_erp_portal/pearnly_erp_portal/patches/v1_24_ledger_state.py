"""单据上的「账本状态 / 账本单号」改成通用列（评估 2026-11-02 §12 第 6 条，主会话审定 §12.1 #6）：采购、销售、出入库、盘点、
收银日汇总五种单原来的 Express 专用栏 ``express_*`` → 通用的 ``ledger_*``，另加 ``ledger_target`` 记这张单的账本是哪一个
（``express``；以后 MR.ERP 是 ``mrerp``）。一张单只会有一个账本，一列一义。

- 模型同步之后跑：同步已建好 ``ledger_*`` 列，旧列还在库里（同步不删列）。逐种单把旧列抄到新列，抄过的单记 ``ledger_target``
  = ``express``；旧列一项都没有值的单（从没排进过 Express）不动，账本留空。
- 幂等：只抄 ``ledger_target`` 为空且旧列有值的行 —— 抄过的、改列后由代码新排上的（排队时就记了账本）都跳过，重跑不会拿旧值
  盖掉新状态。原生报表、列表列设置、属性设置里存着的旧列名照 Frappe ``rename_field`` 的同一套改成新列名；个人视图设置
  （``__UserSettings``）自己一次改完（:func:`_user_settings`）。
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
    from frappe.model.utils.rename_field import update_property_setters, update_reports, update_users_report_view_settings

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
        renames = [*pairs, ("express_section", "ledger_section")]
        for old, new in renames:
            update_reports(doctype, old, new)
            update_users_report_view_settings(doctype, old, new)
            update_property_setters(doctype, old, new)
        _user_settings(doctype, renames)
    frappe.db.commit()


def _user_settings(doctype, renames):
    """个人列表 / 报表视图设置（``__UserSettings``）里存的列名换掉：筛选、报表视图的列、排序都在这份 JSON 里，按带引号 / 反引号的
    列名整词换。不用 Frappe 的 ``update_user_settings``：它只改筛选，且一次补丁里连着调时会把它刚清空的缓存同步回库，整份设置
    变成空（实验栈 11-02 实测）。先把这种单缓存里还没落库的设置落库（缓存里是空值的只清掉，不往库里写空），改完清掉缓存，
    下次打开从库里读。"""
    if "__UserSettings" not in frappe.db.get_tables(cached=False):  # `table_exists` 会自己加 tab 前缀，认不到这张表
        return
    for key, cached in (frappe.cache.hgetall("_user_settings") or {}).items():
        key = frappe.safe_decode(key)
        cached_doctype, _sep, user = key.partition("::")
        if cached_doctype != doctype:
            continue
        if cached:
            frappe.db.sql("""INSERT INTO `__UserSettings` (`user`, `doctype`, `data`) VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE `data` = %s""", (user, doctype, cached, cached))
        frappe.cache.hdel("_user_settings", key)
    rows = frappe.db.sql("SELECT `user`, `data` FROM `__UserSettings` WHERE `doctype` = %s AND `data` LIKE %s",
                         (doctype, f"%{renames[0][0].split('_', 1)[0]}_%"), as_dict=True)
    for row in rows:
        data = row.data or ""
        for old, new in renames:
            data = data.replace(f'"{old}"', f'"{new}"').replace(f"`{old}`", f"`{new}`")
        if data != (row.data or ""):
            frappe.db.sql("UPDATE `__UserSettings` SET `data` = %s WHERE `doctype` = %s AND `user` = %s",
                          (data, doctype, row.user))
            frappe.cache.hdel("_user_settings", f"{doctype}::{row.user}")


def rollback():
    """回退前跑（新镜像还在时，``bench --site <站点> execute pearnly_erp_portal.patches.v1_24_ledger_state.rollback``）：新列抄回
    旧列，清掉账本标记与这条补丁的执行记录，存着的视图改回旧列名。之后换回旧镜像，旧代码读写旧列；再升级时本补丁重跑、重新抄。"""
    from frappe.model.utils.rename_field import update_property_setters, update_reports, update_users_report_view_settings

    for doctype in DOCTYPES:
        if not frappe.db.table_exists(doctype) or not frappe.db.has_column(doctype, "ledger_target"):
            continue
        pairs = _pairs(doctype)
        if pairs:
            assign = ", ".join(f"`{old}` = `{new}`" for old, new in pairs)
            frappe.db.sql(f"UPDATE `tab{doctype}` SET {assign}, `ledger_target` = NULL")
            print(f"{doctype}: copied the ledger columns back to the express columns")
        renames = [(new, old) for old, new in [*pairs, ("express_section", "ledger_section")]]
        for new, old in renames:
            update_reports(doctype, new, old)
            update_users_report_view_settings(doctype, new, old)
            update_property_setters(doctype, new, old)
        _user_settings(doctype, renames)
    frappe.db.delete("Patch Log", {"patch": PATCH})
    frappe.db.commit()
