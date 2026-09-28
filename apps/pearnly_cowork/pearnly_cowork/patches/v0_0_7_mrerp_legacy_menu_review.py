"""修复前推进 MR.ERP 的「成功」单改成需人工核对（2026-09-28，MR.ERP 推送假成功）。

修复前推送写死菜单 67 / 453 / 118 / 125，也不核对是否关联日记账、不回读：MR.ERP 回 "1" 就记成功。
在菜单没关联日记账的公司（例如 test01 · TEST2019），这些单进了 MR.ERP 却没有前缀、不入账、网页看不到。
Cowork 里没有办法事后分辨哪张落对了，所以修复前的每一张 MR.ERP 成功单都改成「待人工核对 + 结果不明」：

- 账本记录：status manual、result_unknown 1、原因 `ERR_MRERP_LEGACY_MENU_UNVERIFIED`；结果不明的记录占着这张票的
  活动键，同一张票不能再被推（换到正确菜单后重推不会触发「已存在」，会重复入账 —— 重推前必须由人确认）；
- 票据列表：这张票最近一次推送就是它时，状态跟着改成待人工核对并带原因；
- 回执、单号、尝试记录都原样保留；**MR.ERP 里的数据不动**，清不清理由用户定。

只改修复前已记成功的 MR.ERP 记录；Express 与其它状态不动。重复执行不重复改（已改过的不再是 success）。
逐条打印改了哪些（记录、票据、单号），作为清单。
"""
import json

import frappe

REASON = "ERR_MRERP_LEGACY_MENU_UNVERIFIED"
RECORD = "Cowork Push Record"
HISTORY = "Cowork OCR History"


def _is_mrerp(raw):
    try:
        return (json.loads(raw or "{}") or {}).get("adapter") == "mrerp"
    except ValueError:
        return False


def execute():
    if not frappe.db.table_exists(RECORD):
        return
    rows = frappe.db.sql("""SELECT name, history, document_key, external_docnum, invoice_no, target_json
        FROM `tabCowork Push Record` WHERE status='success' ORDER BY creation, name""", as_dict=True)
    for row in rows:
        if not _is_mrerp(row.target_json):
            continue
        # 同一张票已有另一条活动记录（正在排队 / 推送中）时活动键让给它：唯一约束不许两条，结果不明照记。
        taken = frappe.db.sql("SELECT name FROM `tabCowork Push Record` WHERE active_key=%s AND name<>%s",
                              (row.document_key, row.name))
        # 迁移不经过账本服务（没有登录用户与推送上下文）；只改这几列，与账本记「结果不明」时写的一致。
        frappe.db.set_value(RECORD, row.name, {
            "status": "manual", "result_unknown": 1, "reason": REASON,
            "active_key": None if taken else row.document_key}, update_modified=False)
        history = frappe.db.get_value(HISTORY, row.history, ["last_push_record"], as_dict=True) \
            if frappe.db.table_exists(HISTORY) else None
        if history and history.last_push_record == row.name:
            frappe.db.set_value(HISTORY, row.history, {"last_push_status": "manual", "last_push_reason": REASON},
                                update_modified=False)
        print(f"MR.ERP legacy push needs review: record={row.name} history={row.history} "
              f"invoice={row.invoice_no or ''} docnum={row.external_docnum or ''}")
