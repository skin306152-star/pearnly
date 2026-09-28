"""看板提醒：谁在什么时候收到一条（被分派、被 @、关注 / 负责的卡有评论或换列、快到期 / 已过期）。

只在任务管理页面里提示（页面右上角的铃铛 + 在线时弹一条），不开全站原生通知（ADR-007 产品站关着）；
LINE 以后再接。收件人必须仍能看这张卡所在的看板；自己做的事不提醒自己。
看板有改动时的实时刷新（`changed`）也只推给能看那块板的人（私有看板不能靠广播泄露卡片号）。
"""
import json

import frappe

from pearnly_cowork.work import access
from pearnly_cowork.work.documents import persist

NOTIFICATION = access.NOTIFICATION
EVENT = "cowork_work_notify"
CHANGED = "cowork_work_changed"
KINDS = ("assign", "mention", "comment", "move", "due_soon", "overdue")


def audience(doc):
    """一张卡的"相关的人"：负责人 + 关注的人。"""
    return {row.user for row in doc.assignees} | {row.user for row in doc.get("watchers") or []}


def changed(board, card=None, action=None):
    """通知正在看这块板的人刷新（只带看板号与卡片号）。"""
    for user in sorted(access.viewers(board)):
        frappe.publish_realtime(CHANGED, {"board": board, "card": card, "action": action}, user=user,
                                after_commit=True)


def send(users, doc, kind, detail=None, *, actor=None, dedupe=None):
    """给 `users` 各发一条；`dedupe(user)` 给出去重键（到期提醒用），已发过的跳过。返回实际发出的人。"""
    actor = frappe.session.user if actor is None else actor
    targets = (set(users) & access.viewers(doc.board)) - {actor, "Administrator", "Guest"}
    sent = []
    for user in sorted(targets):
        key = dedupe(user) if dedupe else None
        if key and frappe.db.exists(NOTIFICATION, {"dedupe": key}):
            continue
        persist(frappe.get_doc({
            "doctype": NOTIFICATION, "workspace": doc.workspace, "user": user, "card": doc.name, "kind": kind,
            "actor": actor or None, "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
            "dedupe": key,
        }))
        frappe.publish_realtime(EVENT, {"card": doc.name, "title": doc.title, "kind": kind, "actor": actor or None,
                                        "actor_name": frappe.utils.get_fullname(actor) if actor else "",
                                        "detail": detail or {}}, user=user, after_commit=True)
        sent.append(user)
    return sent


def _visible_cards(workspace):
    # 只列还能看的看板上的卡（被移出私有看板后，那块板上的旧提醒不再出现）。
    boards = access.visible_boards()
    return frappe.get_all(access.CARD, filters={"workspace": workspace, "board": ["in", boards or [""]]},
                          pluck="name", limit_page_length=0)


def _filters(workspace):
    return {"workspace": workspace, "user": frappe.session.user, "card": ["in", _visible_cards(workspace) or [""]]}


def unread(workspace):
    return frappe.db.count(NOTIFICATION, {**_filters(workspace), "is_read": 0})


def mine(workspace, limit=50):
    filters = _filters(workspace)
    rows = frappe.get_all(NOTIFICATION, filters=filters,
                          fields=["name", "card", "kind", "actor", "detail", "is_read", "creation"],
                          order_by="creation desc", limit_page_length=limit)
    cards = {row.name: row for row in frappe.get_all(
        access.CARD, filters={"name": ["in", [row.card for row in rows] or [""]]}, fields=["name", "title", "board"])}
    for row in rows:
        row.detail = json.loads(row.detail) if row.detail else {}
        row.title = cards[row.card].title if row.card in cards else ""
        row.board = cards[row.card].board if row.card in cards else ""
        row.actor_name = frappe.utils.get_fullname(row.actor) if row.actor else ""
    unread = frappe.db.count(NOTIFICATION, {**filters, "is_read": 0})
    return {"rows": rows, "unread": unread}


def mark_read(workspace, names=None):
    filters = {"workspace": workspace, "user": frappe.session.user, "is_read": 0}
    if names:
        filters["name"] = ["in", list(names)]
    for name in frappe.get_all(NOTIFICATION, filters=filters, pluck="name", limit_page_length=0):
        doc = frappe.get_doc(NOTIFICATION, name)
        doc.is_read = 1
        persist(doc)
