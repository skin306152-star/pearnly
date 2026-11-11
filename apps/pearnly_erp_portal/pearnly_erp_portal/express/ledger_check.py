"""ERP 核账本（hooks ``pearnly_ledger_check["express"]``，共用入口 `pearnly_core.ledger_tickets.check_present`）：经 CC 桌面版
共用请求通道（`pearnly_core.cc_desktop.requests`）发一件 ``express_doc_check``（CC 1.12.20 起），只读单头、回每张在不在。

``connection`` 是 ERP 公司、``account_set_key`` 是写进去的那份账套的键（`cc_desktop.books`）。此刻没有电脑能做（发起人没有登录过
CC 的电脑、没有一台够得到这份账套、都不在线、CC 桌面版太旧）不建请求、回空（共用入口整批记 unknown）；等到上限还没读好也回空，
并删掉这件请求。只读：不写账、不改写账件。
"""
import time
from uuid import uuid4

import frappe

from pearnly_core import ledger_tickets
from pearnly_core.cc_desktop import books, requests

KIND = "express_doc_check"
POLL_SECONDS = 2


def check(connection, account_set_key, items, *, ledger="express", wait=60) -> dict:
    del connection  # 账套键在 CC 桌面版通道里全站唯一；公司由调用方核过
    if ledger != "express":
        return {}
    book = books.find(account_set_key)
    if not book or requests.waiting(KIND, book["key"], actor=frappe.session.user) is not None:
        return {}
    made = requests.create(KIND, "doc_check:" + uuid4().hex, ledger_tickets.request_params(book["path"], book.get("root"), items),
                           book_key=book["key"], actor=frappe.session.user)
    frappe.db.commit()  # 电脑在另一个请求里领：先提交，它才看得到
    deadline = time.monotonic() + max(0, int(wait or 0))
    found = {}
    while True:
        frappe.db.rollback()  # 结束这次的读快照：每次都读到电脑刚交上来的
        view = requests.status(made["request"]) or {}
        if view.get("status") == "done":
            content, _meta = requests.result(made["request"])
            found = ledger_tickets.parse_result(content) if content else {}
            break
        if view.get("status") not in ("requested", "leased") or time.monotonic() >= deadline:
            break
        time.sleep(POLL_SECONDS)
    requests.drop(made["request"])
    frappe.db.commit()
    return found
