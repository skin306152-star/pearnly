"""取号：唯一的发号入口。

机制照 MR.ERP 的 `tbl_autono`（用户给的源码 `mrerp-source`）：**每种单据一个计数器**，
按"类型 + 年"记账，号码 = 类型码 + 年 + `-` + 六位零填充流水。这里把计数落在原生
`ERP Doc Sequence`（销售取号也用这张表）的"方向-前缀-桶"这一行上，取号时 `for update`
行锁排队 —— 并发两张单据不会拿到同一个号。调用方负责提交事务（与旧线上把发号放在建单
同一个事务里一致）。

比 MR.ERP 多一道保险：他们那边号码由表单带着回传、只校验"不小于计数器"，这里计数器是
唯一发号口，另外允许调用方传 `exists` 回调，发出的号若已被历史数据占用就往后跳。

额外提供一个**只读预览**：表单上要显示"当前要发的号"（MR.ERP 的表单也是把号填进
只读输入框），预览只读计数器、不占号。
"""

import frappe

from pearnly_erp_portal.numbering import contracts

SEQUENCE = "ERP Doc Sequence"


def _sequence(direction: str, prefix: str, on) -> tuple[str, str]:
    """(序号表里的行名, 桶)。没有就建一行。"""
    bucket = contracts.year_bucket(on)
    kind = direction or prefix.lower()
    key = f"{kind}-{prefix}-{bucket}"
    if not frappe.db.exists(SEQUENCE, key):
        frappe.get_doc({"doctype": SEQUENCE, "doc_kind": kind, "prefix": prefix, "bucket": bucket,
                        "next_number": 1}).insert(ignore_permissions=True, ignore_if_duplicate=True)
    return key, bucket


def _prefix(direction: str = None, prefix: str = None) -> str:
    prefix = prefix or contracts.prefix_for(direction)
    if prefix not in contracts.PREFIXES:
        raise ValueError(f"unknown document prefix: {prefix!r}")
    return prefix


def preview(direction: str = None, *, prefix: str = None, on=None) -> str:
    """**不占号**的预览：表单上显示"当前要发的那一个号"。"""
    prefix = _prefix(direction, prefix)
    day = frappe.utils.nowdate() if on in (None, "") else str(on)[:10]
    key = f"{direction or prefix.lower()}-{prefix}-{contracts.year_bucket(day)}"
    rows = frappe.db.sql(f"select next_number from `tab{SEQUENCE}` where name=%s",
                         (key,), as_dict=True)
    return contracts.format_number(prefix, day, int(rows[0].next_number) if rows else 1)


def allocate(direction: str = None, *, prefix: str = None, on=None, exists=None) -> str:
    """发下一个号：`{前缀}{佛历年}-{六位流水}`。

    `exists` 给"带着号码的单据表"用（MR.ERP 就是在建单时查库、撞号就继续加）：
    传一个 `value -> bool` 的回调，发出的号若已存在就往后找。
    """
    prefix = _prefix(direction, prefix)
    day = frappe.utils.nowdate() if on in (None, "") else str(on)[:10]
    key, _bucket = _sequence(direction, prefix, day)
    rows = frappe.db.sql(f"select next_number from `tab{SEQUENCE}` where name=%s for update",
                         (key,), as_dict=True)
    number = int(rows[0].next_number)
    # 撞号就继续加（MR.ERP 的 do…while）：历史单号里可能有同形状的值。
    while exists and exists(contracts.format_number(prefix, day, number)):
        number += 1
    frappe.db.set_value(SEQUENCE, key, "next_number", number + 1, update_modified=False)
    return contracts.format_number(prefix, day, number)
