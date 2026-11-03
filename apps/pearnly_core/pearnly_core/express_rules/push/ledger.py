"""写账前的通用护栏接到 Express / MR.ERP 直写（2026-11-03）：从推送用的展平 history（``history.flatten_history_for_mrerp``）
取票面金额与必填项，交 ``tax_documents.ledger_guard.check``；原因码换成两边各自已有的拦截原因（文案、待处理归类都沿用）。纯函数。"""
from pearnly_core.tax_documents import ledger_guard

# Express 直写：沿用 mapper / confidence 已有的原因（已有文案 intake.error.*，待处理照「转人工」显示）
EXPRESS_REASON = {ledger_guard.TOTAL_MISSING: "amounts_not_consistent", ledger_guard.AMOUNTS_NOT_CLOSED: "amounts_not_consistent",
                  ledger_guard.TAX_INVOICE_TOTAL_ONLY: "amounts_not_consistent",
                  ledger_guard.NUMBER_MISSING: "low_confidence:invoice_no_missing", ledger_guard.DATE_MISSING: "bad_or_missing_date"}
# MR.ERP 直写：沿用预检已有的错误码；金额不闭合是新码（同属「用户数据错」，不自动重试，``mrerp.push.user_data``）
MRERP_REASON = {ledger_guard.TOTAL_MISSING: "ERR_NO_TOTAL_AMOUNT", ledger_guard.AMOUNTS_NOT_CLOSED: "ERR_AMOUNTS_NOT_CLOSED",
                ledger_guard.TAX_INVOICE_TOTAL_ONLY: "ERR_AMOUNTS_NOT_CLOSED", ledger_guard.NUMBER_MISSING: "ERR_NO_INVOICE_NO",
                ledger_guard.DATE_MISSING: "ERR_NO_INVOICE_DATE"}


def _pick(flat, *keys):
    fields = flat.get("fields") if isinstance(flat.get("fields"), dict) else {}
    for key in keys:
        for source in (flat, fields):
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def reasons(flat) -> list:
    """展平的 history → 护栏原因码（空 = 可以写账）。总额先看顶层、再看票面字段（同 ``common.amounts``）。
    ``doc_kind``（模型判的单据种类）票面字段里还没有带：在带上之前「完整税票只读到总额」这一条不生效。"""
    return ledger_guard.check(document_type=_pick(flat, "document_type"), title=_pick(flat, "doc_kind"), number=_pick(flat, "invoice_number", "invoice_no"),
                              date=_pick(flat, "invoice_date", "date"), subtotal=_pick(flat, "subtotal"),
                              discount=_pick(flat, "discount"), vat=_pick(flat, "vat"), total=_pick(flat, "total_amount", "total"))


def express_reason(flat):
    found = reasons(flat)
    return EXPRESS_REASON[found[0]] if found else None


def mrerp_reason(flat):
    found = reasons(flat)
    return MRERP_REASON[found[0]] if found else None
