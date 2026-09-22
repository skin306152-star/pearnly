"""取号：唯一的发号入口。

计数落在原生 `ERP Doc Sequence`（销售取号也用这张表）的"前缀 + 佛历日"这一行上，
取号时 `for update` 行锁排队 —— 并发两张单据不会拿到同一个号，也不需要在应用层
再加锁。调用方负责提交事务（与旧线上把发号放在建单同一个事务里一致）。
"""

import frappe

from pearnly_erp_portal.numbering import contracts

SEQUENCE = "ERP Doc Sequence"


def allocate(direction: str = None, *, prefix: str = None, on=None) -> str:
    """发下一个号：`{前缀}-{佛历日}-{四位流水}`。"""
    prefix = prefix or contracts.prefix_for(direction)
    if prefix not in contracts.PREFIXES:
        raise ValueError(f"unknown document prefix: {prefix!r}")
    day = frappe.utils.nowdate() if on in (None, "") else str(on)[:10]
    bucket = contracts.day_bucket(day)
    kind = direction or prefix.lower()
    key = f"{kind}-{prefix}-{bucket}"
    if not frappe.db.exists(SEQUENCE, key):
        frappe.get_doc({"doctype": SEQUENCE, "doc_kind": kind, "prefix": prefix, "bucket": bucket,
                        "next_number": 1}).insert(ignore_permissions=True)
    rows = frappe.db.sql(f"select next_number from `tab{SEQUENCE}` where name=%s for update",
                         (key,), as_dict=True)
    number = int(rows[0].next_number)
    frappe.db.set_value(SEQUENCE, key, "next_number", number + 1, update_modified=False)
    return contracts.format_number(prefix, day, number)
