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
