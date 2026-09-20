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
        "opening_stock",
    }
)
