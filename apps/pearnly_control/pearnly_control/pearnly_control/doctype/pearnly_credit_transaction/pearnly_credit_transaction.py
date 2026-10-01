# -*- coding: utf-8 -*-
"""余额流水（台账）。

**只由 `pearnly_control.billing` 写**，而且必须与钱包余额在同一个事务里 ——
改了余额不落台账，钱账就对不上（老产品的铁律 #26）。

`amount_thb` 的正负就是方向：`topup`/`adjustment` 是正（进钱），
`usage` 是负（扣钱）。别另立一列表达方向。
"""

from frappe.model.document import Document


class PearnlyCreditTransaction(Document):
    pass


def on_doctype_update():
    """每次同步本 DocType 都确认台账的查询索引在（见 ``payments.usage.ensure_indexes``）。"""
    from pearnly_control.payments.usage import ensure_indexes

    ensure_indexes()
