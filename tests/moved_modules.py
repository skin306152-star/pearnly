"""搬进共用层的 Cowork 纯函数（ADR-025，2026-10-23；MR.ERP 规则 2026-11-02）：旧模块名 → 新模块名。

只给测试用：有的测试按 ``"pearnly_cowork." + 名字`` 批量登记替身模块，搬走的那些要按新位置登记，被测代码才找得到。
"""
_PUSH = ("account_rules", "bank_evidence", "choices", "common", "confidence", "doc_sanity", "field_clean",
         "field_corrections", "field_rules", "history", "mapper", "merchant", "payment", "posting_kind",
         "posting_profile", "preflight", "prepare", "primitives", "prior_doc", "referenced", "sales_mapper",
         "stock_acc_group", "wire_location", "written_check")
MOVED = {
    **{f"pearnly_cowork.push.express.{m}": f"pearnly_core.express_rules.push.{m}" for m in _PUSH},
    **{f"pearnly_cowork.contracts.{m}": f"pearnly_core.express_rules.contracts.{m}"
       for m in ("history", "line_items", "pos", "push")},
    **{f"pearnly_cowork.targets.{m}": f"pearnly_core.express_rules.targets.{m}"
       for m in ("identity", "account_match", "stock_groups")},
    "pearnly_cowork.push.receipts": "pearnly_core.express_rules.receipts",
    **{f"pearnly_cowork.companion.{m}": f"pearnly_core.cc_desktop.wire.{m}"
       for m in ("queue_contracts", "request_contracts", "route_contracts")},
    # MR.ERP 规则（评估 2026-11-02 §13 第 5 步）：`push.mrerp.runner` 留在 Cowork，其余搬进 pearnly_core.mrerp。
    **{f"pearnly_cowork.connections.mrerp_{m}": f"pearnly_core.mrerp.{m}"
       for m in ("session", "catalog", "master_reader", "needs")},
    **{f"pearnly_cowork.targets.mrerp_{m}": f"pearnly_core.mrerp.{m}" for m in ("menus", "masters")},
    **{f"pearnly_cowork.push.mrerp.{m}": f"pearnly_core.mrerp.push.{m}"
       for m in ("account_valve", "autocreate", "defaults", "models", "modules", "preview_parse", "readback",
                 "report_parse", "report_parser", "routing", "transport", "xlsx")},
}


def moved(name: str) -> str:
    return MOVED.get(name, name)
