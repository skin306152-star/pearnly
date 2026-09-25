"""ERP 识别计费：按套餐扣（用户 2026-09-26「要扣，按套餐逻辑扣费」）。

与 DMS、Cowork 同一个计费底座（`pearnly_core.billing_client` → 控制面 `billing.gate / charge`：
有套餐先扣本周期额度，超出按超额单价从余额扣；没套餐按页从余额扣）：

- 单位：物理页（一张图 1 页，PDF 按页数；一个文件拆出几张单据也只按页算一次），`kind="pdf"`；
- 档位：B（发票类，旧线上发票走 economy，与 DMS 证件的 A 档是两种业务）；
- 开工前查余额：不够就不调模型、告诉本人去充值；查不到（我们自己的故障）不说成余额不足 ——
  LINE 队列推迟重试，网页提示稍后再试；
- 识别成功（有结果、是单据）才报账；同一 `reference` 控制面只扣一次，重试不重复扣。
  报账暂时失败不把已完成的识别说成失败，记错误日志供对账。
"""
import frappe
from frappe import _

TIER = "B"
KIND = "pdf"


class InsufficientBalance(frappe.ValidationError):
    """余额不足：给本人看的话，LINE 与网页都原样转达。"""


def pages(images) -> int:
    from pearnly_erp_portal.recognition import inputs

    total = 0
    for content, mime in images:
        count = inputs.pdf_page_count(content) if mime == "application/pdf" else 1
        total += max(1, count)
    return total


def gate(units):
    from pearnly_core import billing_client
    from pearnly_core.execution.errors import Deferred

    status = billing_client.status(KIND, units, tier=TIER)
    if status.get("allowed"):
        return status
    if (status.get("error_code") or "lookup_error") == "lookup_error":
        raise Deferred("billing_unavailable", 30)
    frappe.throw(_("Your balance is not enough for recognition. Please top up and send it again."), InsufficientBalance)


def charge(units, reference, description):
    from pearnly_core import billing_client

    result = billing_client.charge("erp", KIND, units, tier=TIER, reference=reference, description=description)
    if not (isinstance(result, dict) and result.get("ok") is True):
        frappe.log_error(title="ERP OCR charge pending",
                         message=f"reference={reference} units={units} result={str(result)[:300]}")
    return result
