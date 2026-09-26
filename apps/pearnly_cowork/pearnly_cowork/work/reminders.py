"""每 5 分钟一次（hooks `scheduler_events`）：按卡片的「提醒时间」发到期提醒，到期后再发一次过期提醒。

收件人是卡的负责人和关注的人；同一张卡、同一个到期时刻、同一种提醒，每人只发一次
（改了到期日 / 时间 / 提醒会重新提醒）。已完成、已归档、在已归档列表里的卡不提醒。
时间都按站点时区的裸时间算（到期日没填时间 = 当天 23:59）。
"""
from datetime import timedelta

import frappe
from frappe.utils import getdate, now_datetime

from pearnly_cowork.work import access, contracts, notify

LOOKBACK_DAYS = 30  # 更早就过期的卡不再补发（上线 / 停机后不刷屏）。


def run():
    if frappe.conf.get("pearnly_product") != "cowork" or not frappe.db.table_exists(access.CARD):
        return
    now = now_datetime().replace(tzinfo=None)
    today = getdate(now)
    open_lists = frappe.get_all(access.COLUMN, filters={"archived": 0}, pluck="name", limit_page_length=0)
    rows = frappe.get_all(access.CARD, filters={
        "archived": 0, "completed": 0, "is_template": 0, "board_list": ["in", open_lists or [""]],
        "due_date": ["between", [today - timedelta(days=LOOKBACK_DAYS), today + timedelta(days=3)]]},
        pluck="name", limit_page_length=0)
    for name in rows:
        doc = frappe.get_doc(access.CARD, name)
        due = contracts.due_at(doc.due_date, doc.due_time)
        stamp = due.strftime("%Y-%m-%d %H:%M")
        if due <= now:
            kind = "overdue"
        else:
            remind = contracts.remind_at(doc.due_date, doc.due_time, doc.reminder or contracts.DEFAULT_REMINDER)
            if remind is None or remind > now:
                continue
            kind = "reminder"
        notify.send(notify.audience(doc), doc, kind, {"due": stamp}, actor="",
                    dedupe=lambda user, doc=doc, kind=kind, stamp=stamp: f"{kind}:{doc.name}:{stamp}:{user}")
