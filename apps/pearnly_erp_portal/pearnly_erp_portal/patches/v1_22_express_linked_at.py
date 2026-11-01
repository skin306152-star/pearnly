"""接 Express 的「接上时刻」（主会话 11-01）：单据开始时刻不早于它才写进 Express，库存对齐只叠加它之后开始的单，收银日汇总只收
它之后卖的小票。之后由第一次主档同步落库时记（`express.masters.apply`），断开清空。

已经接上（勾着、同步落过库）的公司按第一次同步的证据回填：取「收银写 Express 起始日」（第一次落库那天，补丁 v1_14 给更早接上的
公司记成那次上线当天）的 00:00，与这家公司已排进 / 写进 Express 的单里最早的建单时刻，两者取早的 —— 已写过、正在排队的单都
不早于它，行为不变；从没写过的旧单维持不推。都查不到（勾着、落过库却没有起始日）就记补丁运行时刻。没勾的、没落过库的不填。
只填空值，重跑无副作用；打印回填了几家与每家的依据（发布回读用）。"""
import frappe
from frappe.utils import get_datetime, now_datetime

COMPANY = "ERP Company"
BY_COMPANY = ("ERP Sales Document", "Purchase Document", "ERP POS Day Summary")
BY_WAREHOUSE = ("ERP Stock Entry", "ERP Stock Count")


def _earliest_document(company):
    found = []
    for doctype in BY_COMPANY:
        if frappe.db.table_exists(doctype):
            found.append(frappe.db.sql(f"""SELECT MIN(creation) FROM `tab{doctype}` WHERE company = %s
                AND COALESCE(express_state, '') != ''""", (company,))[0][0])
    for doctype in BY_WAREHOUSE:
        if frappe.db.table_exists(doctype):
            found.append(frappe.db.sql(f"""SELECT MIN(d.creation) FROM `tab{doctype}` d
                JOIN `tabERP Warehouse` w ON w.name = d.warehouse WHERE w.company = %s
                AND COALESCE(d.express_state, '') != ''""", (company,))[0][0])
    found = [get_datetime(value) for value in found if value]
    return min(found) if found else None


def execute():
    if not frappe.db.has_column(COMPANY, "express_linked_at"):
        return
    rows = frappe.db.sql(f"""SELECT name, express_pos_from, express_book FROM `tab{COMPANY}`
        WHERE express_enabled = 1 AND express_linked_at IS NULL
        AND (express_pos_from IS NOT NULL OR COALESCE(express_book, '') != '')""", as_dict=True)
    for row in rows:
        evidence = {}
        if row.express_pos_from:
            evidence["pos_from"] = get_datetime(f"{row.express_pos_from} 00:00:00")
        earliest = _earliest_document(row.name)
        if earliest:
            evidence["first_document"] = earliest
        source, at = min(evidence.items(), key=lambda item: item[1]) if evidence else ("patch_time", now_datetime())
        frappe.db.set_value(COMPANY, row.name, "express_linked_at", at, update_modified=False)
        print(f"ERP Company {row.name}: express_linked_at {at} ({source})")
    frappe.db.commit()
    print(f"ERP Company express_linked_at: {len(rows)} filled")
