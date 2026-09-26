"""每小时一次：给快到期（今天 / 明天）和已过期、还没完成的卡发提醒（hooks `scheduler_events`）。

收件人是卡的负责人和关注的人；同一张卡、同一个到期日、同一种提醒，每人只发一次
（换了到期日会重新提醒）。日期按站点时区的"今天"算。
"""
from datetime import timedelta

import frappe
from frappe.utils import getdate, today

from pearnly_cowork.work import access, contracts, notify


def run():
    if frappe.conf.get("pearnly_product") != "cowork" or not frappe.db.table_exists(access.CARD):
        return
    now = getdate(today())
    rows = frappe.get_all(access.CARD, filters={"archived": 0, "status": ["!=", contracts.DONE],
                                                "due_date": ["<=", now + timedelta(days=1)]},
                          pluck="name", limit_page_length=0)
    for name in rows:
        doc = frappe.get_doc(access.CARD, name)
        due = getdate(doc.due_date)
        kind = "overdue" if due < now else "due_soon"
        notify.send(notify.audience(doc), doc, kind, {"due": str(due)}, actor="",
                    dedupe=lambda user, doc=doc, kind=kind, due=due: f"{kind}:{doc.name}:{due}:{user}")
