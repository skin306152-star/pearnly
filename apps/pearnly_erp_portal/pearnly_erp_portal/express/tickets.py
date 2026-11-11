"""ERP 在共用层「同一张票只写一次」里的声明（hooks ``pearnly_ledger_tickets``，`pearnly_core.ledger_tickets`；复核 5 批之第 1 批）。

同一张票 = 同一公司、同一账本（``ledger_target``）里身份键相同（`ticket_identity`）的采购单：供应商税号与印的分店、规范化单号、
单据日期、含税合计；买方是本公司（ERP 公司的税号与分店）。采购单的身份随保存维护在 ``ticket_key`` / ``ticket_level`` 列
（`purchase_identity`）；代录里还没提交的收单记录（``I:``）按它提交时同一份票面现算。
只看我们自己的写入（单据上的账本状态）：Express 里会计手录的同号照写（「推送不查重不拦」）。
销售单上没有对方的票号与税号，这一批不判（主会话 11-09 定，记进第 4 批「异常与清单」）。
"""
import json

import frappe

from pearnly_erp_portal.express import contracts, push

EXACT = "exact"
SCAN = 20
ENTERED_STATES = (contracts.WRITTEN, contracts.MISMATCH, contracts.HANDLED)
QUEUED_STATES = (contracts.QUEUED,)
INTAKE, PURCHASE_PREFIX = "I", "P"


def _branch(kind, number) -> str:
    from pearnly_core.express_rules.targets.account_match import HEAD_OFFICE, printed_branch

    if str(kind or "") == "head_office":
        return HEAD_OFFICE
    return printed_branch(number) if str(number or "").strip() else ""


def _company_side(company) -> tuple[str, str]:
    from pearnly_core.express_rules.targets.account_match import printed_branch

    found = frappe.db.get_value("ERP Company", company, ["tax_id", "branch"], as_dict=True) or {}
    return str(found.get("tax_id") or ""), printed_branch(found.get("branch"))


def _face(company, number, day, total, supplier_tax, branch_type, branch_no, supplier_name):
    from pearnly_core.express_rules.contracts import ticket_identity

    tax, branch = _company_side(company)
    return ticket_identity.Face(direction=ticket_identity.PURCHASE, number=str(number or ""), date=day or "",
                                total="" if total is None else total, seller_tax=str(supplier_tax or ""),
                                seller_branch=_branch(branch_type, branch_no), buyer_tax=tax, buyer_branch=branch,
                                seller_name=str(supplier_name or ""), buyer_name=company)


def purchase_identity(doc) -> tuple:
    """采购单这张票的身份 ``(ticket_key, ticket_level)``（保存时写在单据上）；认不出（单号、日期、合计缺一项）回 (None, None)。"""
    from pearnly_core.express_rules.contracts import ticket_identity

    found = ticket_identity.identity(_face(doc.company, doc.doc_no, doc.doc_date, doc.grand_total, doc.supplier_tax_id,
                                           doc.get("supplier_branch_type"), doc.get("supplier_branch_no"),
                                           doc.get("supplier_name")))
    return (found.key, found.level) if found else (None, None)


def _intake_identity(name):
    from pearnly_core.express_rules.contracts import ticket_identity
    from pearnly_erp_portal.integrations import intake

    row = frappe.db.get_value(intake.DOCTYPE, name, ["name", "company", "direction", "status", "purchase", "result"],
                              as_dict=True)
    if not isinstance(row, dict) or row.get("direction") != "purchase":
        return None, None, None, None
    if row.get("purchase"):  # 已提交：按提交出的采购单判
        return _document_identity(row["purchase"])
    result = json.loads(row.get("result") or "{}") if isinstance(row.get("result"), str) else {}

    def total():
        return intake.totals(frappe.get_doc(intake.DOCTYPE, name), result).get("grand_total")

    found = ticket_identity.identity(_face(row.company, result.get("doc_no"), result.get("doc_date"), total(),
                                           result.get("supplier_tax_id"), result.get("supplier_branch_type"),
                                           result.get("supplier_branch_no"), result.get("supplier_name")))
    return row.company, (found.key if found else None), (found.level if found else None), None


def _document_identity(name):
    row = frappe.db.get_value(push.PURCHASE, name, ["company", "ticket_key", "ticket_level"], as_dict=True) or {}
    return row.get("company"), row.get("ticket_key"), row.get("ticket_level"), name


def twin(doc_id) -> dict:
    """``{"doc_id", "docnum", "state", "key"}``：同一张票已由另一张采购单写成（entered）或正在写（queued）；认出身份但没写过的
    只回 ``{"key"}``；认不出身份的、销售单回 {}。``doc_id`` 是代录与直写同一种写法（``I:<收单记录>``、``P:<采购单>``）。"""
    prefix, _sep, name = str(doc_id or "").partition(":")
    if prefix == INTAKE:
        company, key, level, own = _intake_identity(name)
    elif prefix == PURCHASE_PREFIX:
        company, key, level, own = _document_identity(name)
    else:
        return {}
    if not company or not key or level != EXACT:
        return {}
    lock = f"{company}:{key}"
    filters = {"company": company, "ticket_key": key,
               "ledger_state": ["in", list(ENTERED_STATES + QUEUED_STATES)]}
    if own:
        filters["name"] = ["!=", own]
    for other in frappe.get_all(push.PURCHASE, filters=filters, fields=["name", "ledger_state", "ledger_docnum", "ledger_book",
                                                                        "ledger_target"],
                                order_by="modified desc", limit_page_length=SCAN):
        state = "entered" if other.ledger_state in ENTERED_STATES else "queued"
        found = {"doc_id": f"{PURCHASE_PREFIX}:{other.name}", "docnum": str(other.ledger_docnum or "") if state == "entered" else "",
                 "state": state, "key": lock}
        if state == "entered":
            # 核账本要的（录过的票先核账本，A 线 11-11）：写进哪本账、哪份账套；ERP 代录只写采购单
            found.update(connection=company, account_set=str(other.ledger_book or ""), direction="purchase",
                         ledger="mrerp" if other.ledger_target == "mrerp" else "express")
        return found
    return {"key": lock}
