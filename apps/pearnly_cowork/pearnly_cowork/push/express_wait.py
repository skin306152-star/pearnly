"""推送记录此刻是不是在等 Express 关闭（给列表、详情、CC 对话显示现成那句 ``intake.error.ACCOUNT_BUSY_LOCKED``；只读，11-12）。

电脑回报「Express 开着这本账套」的记录：排队中（``pending``）、原因是 ``ack_rules.EXPRESS_OPEN``、``updated_at`` 是那次回报的时刻
（`push.native.service.ack`）。读的时候按上次回报到现在多久算（``ack_rules.waiting_for_express``），不抄到票的失败原因栏
（那一栏在十来处被当作「失败 / 停下」的标志）、不另存：被领走、写成、转人工后状态不再是排队中，电脑不再回报则到点过期。
"""
from datetime import datetime, timezone

import frappe

from pearnly_core.cc_desktop.wire import ack_rules

RECORD = "Cowork Push Record"
CODE = ack_rules.EXPRESS_OPEN


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def many(names, moment=None) -> dict:
    """``{推送记录名: 上次回报的时刻}``，只含此刻在等 Express 关闭的那几条（一次查完，不逐条查）。"""
    names = sorted({str(name) for name in names or () if name})
    if not names:
        return {}
    rows = frappe.get_all(RECORD, filters={"name": ["in", names], "status": "pending", "reason": CODE},
                          fields=["name", "status", "reason", "updated_at"], limit_page_length=len(names))
    moment = moment or _now()
    return {row["name"]: row["updated_at"] for row in rows
            if ack_rules.waiting_for_express(row["status"], row["reason"] or "", row["updated_at"], moment)}


def one(name, moment=None) -> bool:
    return bool(name) and name in many([name], moment)


def account(connection, account_key, moment=None) -> bool:
    """这条连接的这本账套此刻有没有任何一条在等 Express 关闭。"""
    if not connection or not account_key:
        return False
    rows = frappe.get_all(RECORD, filters={"connection": connection, "account_key": account_key, "status": "pending",
                                           "reason": CODE},
                          fields=["name", "status", "reason", "updated_at"], order_by="updated_at desc", limit_page_length=1)
    moment = moment or _now()
    return any(ack_rules.waiting_for_express(row["status"], row["reason"] or "", row["updated_at"], moment) for row in rows)
