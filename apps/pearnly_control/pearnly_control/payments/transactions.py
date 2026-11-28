"""Bounded retries for complete, database-only billing service transactions.

MariaDB 11.8 snapshot isolation can reject a locking read after another request
commits. Retry the whole uncommitted service call, never an inner grant/debit or
an external operation. Native commit callbacks guard against replay after commit.
"""
from functools import wraps
import random
import time

import frappe

from pearnly_core.transaction import conflicts


def retry_service(function):
    @wraps(function)
    def call(*args, **kwargs):
        committed = False

        def mark_committed():
            nonlocal committed
            committed = True

        for attempt in range(12):
            frappe.db.after_commit.add(mark_committed)
            try:
                return function(*args, **kwargs)
            except Exception as error:
                # 关隔离后同一种冲突也会是保存前比对（B ①）；已提交过的不重来（会重放已落库的写）。扣费入口的那次「先结束
                # 读快照」的空提交放在本包装外面（`billing_api.service_charge`），否则它一提交就把这里标成「已提交」、之后撞
                # 1213 不再重试（11-28 B ③：漏收费的原有问题）。
                if not conflicts.is_conflict(error) or committed or attempt == 11:
                    raise
                frappe.db.rollback()
                time.sleep(random.uniform(.025, min(.5, .05 * (2 ** attempt))))
    return call
