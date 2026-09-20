"""Bounded retries for complete, database-only billing service transactions.

MariaDB 11.8 snapshot isolation can reject a locking read after another request
commits. Retry the whole uncommitted service call, never an inner grant/debit or
an external operation. Native commit callbacks guard against replay after commit.
"""
from functools import wraps
import random
import time

import frappe


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
            except frappe.QueryDeadlockError:
                if committed or attempt == 11:
                    raise
                frappe.db.rollback()
                time.sleep(random.uniform(.025, min(.5, .05 * (2 ** attempt))))
    return call
