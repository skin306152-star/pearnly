"""写账前的通用护栏（多产品共用，2026-11-03「CC 通用疑难处理」）：只守管钱的底线，不按情况加规则。

纯函数：只依赖标准库与 ``tax_documents``。Express / MR.ERP 直写、代录建任务之前都调同一个 :func:`check`；
返回的原因码不为空 → 这张单不写账，进「待处理」，交人（或 CC 按用户给的资料）补齐后再推。
缺页、模糊、传错文件……都只是触发这几条底线的原因之一，这里不判断原因，原因由 CC 看原件找。

底线（判据与容差复用 :func:`amounts.printed_issues`，不另写一套）：
- ``total_missing``：没有总额（或不是正数）；
- ``amounts_not_closed``：票面印了小计 / 税额，却与总额对不上（含折扣、含税价、预扣税实付额这几种合法对法）；
- ``tax_invoice_total_only``：模型判为税票（``title`` 是 ``tax_invoice`` / ``receipt_tax_invoice``）却只读到总额、没有小计和税额
  （简式税票、含税收据、没有税票抬头的发票 ``bill_invoice`` 只印总额是正常的，照账套规则反推；用户 11-03 定）；
- ``number_missing``：完整税票没有单号（收据、简式税票没印单号不算缺）；
- ``date_missing``：没有日期。

明细合计对不上不在这里拦：表头金额闭合时钱没有错，各写入方照现有口径只写表头（用户 11-03 定）。
"""
from pearnly_core.tax_documents import amounts, codes

TOTAL_MISSING = "total_missing"
AMOUNTS_NOT_CLOSED = "amounts_not_closed"
TAX_INVOICE_TOTAL_ONLY = "tax_invoice_total_only"
NUMBER_MISSING = "number_missing"
DATE_MISSING = "date_missing"
CODES = (TOTAL_MISSING, AMOUNTS_NOT_CLOSED, TAX_INVOICE_TOTAL_ONLY, NUMBER_MISSING, DATE_MISSING)
# 模型（Claude 识别的 ``doc_kind``）判为完整税票的两种；平台码 ``tax_invoice`` 还包括 bill_invoice、utility 等，不能用来判。
TAX_INVOICE_TITLES = ("tax_invoice", "receipt_tax_invoice")


def check(*, document_type=None, title=None, number=None, date=None, subtotal=None, discount=None, vat=None, total=None) -> list:
    """一张单能不能写账：返回原因码（空 = 可以写）。金额按票面印的（字符串或数字都行）。
    ``document_type``：平台票种码；``title``：模型判的单据种类（``doc_kind``），没有时「只读到总额」这一条不判。"""
    found = []
    printed = {"subtotal": subtotal, "discount": discount, "vat": vat, "total": total}
    issues = amounts.printed_issues(printed, [])
    gross = amounts.money(total)
    if "total_missing" in issues or gross is None or gross <= 0:
        found.append(TOTAL_MISSING)
    elif "printed_math" in issues:
        found.append(AMOUNTS_NOT_CLOSED)
    elif title in TAX_INVOICE_TITLES and amounts.money(subtotal) is None and amounts.money(vat) is None:
        found.append(TAX_INVOICE_TOTAL_ONLY)
    if document_type == codes.TAX_INVOICE and not str(number or "").strip():
        found.append(NUMBER_MISSING)
    if not str(date or "").strip():
        found.append(DATE_MISSING)
    return found
