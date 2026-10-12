"""Deployed invoice mapper keys only; this is not a verified Companion contract.

Preflight also carries account_root/account_dir/account_company/account_set_row.
Those routing keys are outside this legacy mapper set and require the future
transport's verified consumer contract; do not use this set as queue validation.
"""
from __future__ import annotations

# express payload v1 全集:build_express_payload / build_express_sales_payload 的
# 固定键 + 条件键(stock_acccod / vat_capitalized / prior_docnum)+ preflight 注入的
# opening_stock + finalize_payload 盖的 payload_version。
WRITE_PAYLOAD_KEYS = frozenset(
    {
        "payload_version",
        "direction",
        "doctype",
        "account_set",
        "docdate_be",
        "vat_period_be",
        "ref_no",
        "supplier",
        "customer",
        "vat_rate",
        "base_amount",
        "vat_amount",
        "total_amount",
        "lines",
        "items",
        "items_status",
        "items_account",
        "items_line_sum",
        "account_source",
        "account_review",
        "doctype_src",
        "doc_lane",
        "item_src",
        "source",
        "stock_acccod",
        "vat_capitalized",
        "prior_docnum",
        # 复核页「仍然推送」：服务端准备时加（不是映射器出的），小助手查重闸认它。
        "duplicate_confirmed",
        "opening_stock",
        # POS 导出的日汇总票（载荷 3）：票号起止、自编单号、付款方式转账凭证（`contracts/pos.py`）。
        "pos",
    }
)
