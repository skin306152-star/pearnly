"""识别来的采购 / 销售单写账前的票面合计核对（2026-11-03「CC 通用疑难处理」）：判据在 `contracts.printed_hold`，这里只读单据。
Express 直写与代录交单（`push.send`）、代录预检（`push.prepare_only`）、MR.ERP 导入（`mrerp.push.prepare`）写账前都调 :func:`hold`。"""
import frappe

from pearnly_erp_portal.express import contracts

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"


def hold(doctype, name):
    """停下的原因码（票面合计没有 / 对不上），可以写回 ``None``。不是采购 / 销售单、没有识别标记的单一律 ``None``。"""
    if doctype not in (PURCHASE, SALES):
        return None
    fields = ["ocr_review", "printed_total", "grand_total"] + (["net_payable"] if doctype == PURCHASE else [])
    row = frappe.db.get_value(doctype, name, fields, as_dict=True) or {}
    return contracts.printed_hold(bool(row.get("ocr_review")), row.get("printed_total"), row.get("grand_total"),
                                  row.get("net_payable"))


# 停在这两种原因上的单，票面合计补对后当场重新准备、写账（不用作废重开）
RELEASE = (contracts.PRINTED_TOTAL_MISSING, contracts.PRINTED_TOTAL_MISMATCH)
LOCKED = (contracts.WRITTEN, contracts.MISMATCH)  # 已写进账本：票面合计不再改（改了也到不了账本）
EDIT_SUBJECT = "pearnly.document_edit"  # 改动依据的记录（Comment.subject，同 Cowork `assistant.document_checks`）


def set_total(doctype, name, value, *, basis="", by="user", release=True) -> dict:
    """补 / 改一张单的票面合计（调用方已核过这张单的开单权）：已提交的单也能改（只这一栏，2026-11-03 主会话定），
    原生版本记录留谁、何时、改前改后；``basis`` 依据（CC 改时必填）另记一条。停在票面合计上的单当场重新准备写账；``release``
    为假（CC 对话里改，B3：写入只走计划）只改数，单据的写账状态不动，由写入计划推。"""
    from pearnly_core.tax_documents import amounts

    if doctype not in (PURCHASE, SALES):
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    row = frappe.db.get_value(doctype, name, ["printed_total", "status", "ledger_state", "ledger_reason", "ledger_job"],
                              as_dict=True)
    if not row or row.status == "void":
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    if row.ledger_state in LOCKED:
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)  # 已写进账本：改不了
    total = amounts.money(value)
    if total is None or total <= 0:
        frappe.throw(frappe._("Invalid request"))
    old = float(row.printed_total or 0)
    frappe.db.set_value(doctype, name, "printed_total", float(total), update_modified=False)
    frappe.get_doc({"doctype": "Version", "ref_doctype": doctype, "docname": name,
                    "data": frappe.as_json({"changed": [["printed_total", old, float(total)]]})}).insert(
        ignore_permissions=True)
    if basis:
        frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "subject": EDIT_SUBJECT,
                        "reference_doctype": doctype, "reference_name": name,
                        "content": frappe.as_json({"by": by, "basis": str(basis)[:500],
                                                   "changes": {"printed_total": [old, float(total)]}})}).insert(
            ignore_permissions=True)
    released = release and row.ledger_state == contracts.HELD and row.ledger_reason in RELEASE and not row.ledger_job
    if released:
        from pearnly_erp_portal.express import push

        frappe.db.set_value(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_reason": "", "ledger_needed": None},
                            update_modified=False)
        frappe.db.commit()
        push.send(doctype, name)  # 账本是 MR.ERP 的单由它转给 MR.ERP 导入
    else:
        frappe.db.commit()
    state = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_reason", "ledger_docnum"], as_dict=True) or {}
    return {"printed_total": float(total), "released": released, "ledger_state": state.get("ledger_state") or "",
            "ledger_reason": state.get("ledger_reason") or "", "docnum": state.get("ledger_docnum") or ""}


def edits(doctype, name, limit=10) -> list:
    """这张单最近的改动依据（新 → 旧）：``[{at, user, by, basis, changes}]``。"""
    rows = frappe.get_all("Comment", filters={"reference_doctype": doctype, "reference_name": name, "subject": EDIT_SUBJECT},
                          fields=["owner", "creation", "content"], order_by="creation desc", limit_page_length=limit)
    out = []
    for row in rows:
        try:
            body = frappe.parse_json(row.content or "{}") or {}
        except ValueError:
            continue
        out.append({"at": str(row.creation), "user": row.owner, "by": body.get("by") or "", "basis": body.get("basis") or "",
                    "changes": body.get("changes") or {}})
    return out
