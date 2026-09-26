"""工作协调看板的服务端实现：读板、卡片增改移、归档、评论、附件、标签。

一个团队（站点）一块板，挂在站点内部那一份归属（`scope.default_workspace`）上；三列固定。
每个写入口先 `access.require()`，再核对卡片属于本站归属；写入走 `documents.persist`，
原生接口写不进来。改完用原生 `publish_realtime` 通知正在看板上的人刷新（只带卡片号）。
"""
import json
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import convert_utc_to_system_timezone, getdate, strip_html

from pearnly_cowork.access import scope
from pearnly_cowork.work import access, contracts
from pearnly_cowork.work.contracts import Invalid
from pearnly_cowork.work.documents import persist, remove

CARD, LABEL, ACTIVITY = access.CARD, access.LABEL, access.ACTIVITY
REALTIME_EVENT = "cowork_work_changed"
SUMMARY_FIELDS = ["name", "title", "status", "position", "due_date", "description", "modified", "creation"]


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _workspace():
    access.require()
    return scope.default_workspace()


def _card(name, workspace, *, lock=False, archived=False):
    row = frappe.db.get_value(CARD, {"name": name or "", "workspace": workspace},
                              ["name", "archived"], as_dict=True, for_update=lock)
    if not row or (archived is not None and bool(row.archived) != archived):
        raise Invalid("cowork.work.error.not_found")
    return frappe.get_doc(CARD, row.name)


def _changed(card, action=None):
    frappe.publish_realtime(REALTIME_EVENT, {"card": card, "action": action}, after_commit=True)


def _log(card, workspace, action, detail=None, content=None):
    return persist(frappe.get_doc({
        "doctype": ACTIVITY, "workspace": workspace, "card": card, "actor": frappe.session.user,
        "action": action, "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
        "content": content,
    }))


# ── 读 ────────────────────────────────────────────────────────────────────────


def members():
    """能被分派的人：启用的、勾了「工作协调」或是产品管理员的账号。"""
    users = frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", [access.ROLE, access.ADMIN_ROLE]]},
                           pluck="parent", distinct=True)
    users = [user for user in users if user not in ("Administrator", "Guest")]
    if not users:
        return []
    rows = frappe.get_all("User", filters={"name": ["in", users], "enabled": 1},
                          fields=["name", "full_name", "user_image"], order_by="full_name asc")
    return [{"user": row.name, "name": row.full_name or row.name, "image": row.user_image} for row in rows]


def labels(workspace=None):
    workspace = workspace or _workspace()
    return frappe.get_all(LABEL, filters={"workspace": workspace}, fields=["name", "title", "color"],
                          order_by="creation asc")


def _children(doctype, parents, fields):
    if not parents:
        return {}
    grouped = {}
    for row in frappe.get_all(doctype, filters={"parenttype": CARD, "parent": ["in", parents]},
                              fields=["parent", *fields], order_by="idx asc"):
        grouped.setdefault(row.parent, []).append(row)
    return grouped


def _counts(doctype, field, parents, extra=None):
    if not parents:
        return {}
    filters = {field: ["in", parents], **(extra or {})}
    return {row[field]: row["count"] for row in frappe.get_all(
        doctype, filters=filters, fields=[field, {"COUNT": "name", "as": "count"}], group_by=field)}


def _summaries(rows):
    names = [row.name for row in rows]
    assignees = _children("Cowork Work Card Assignee", names, ["user"])
    tags = _children("Cowork Work Card Label", names, ["label"])
    checklist = _children("Cowork Work Checklist Item", names, ["done"])
    comments = _counts(ACTIVITY, "card", names, {"action": "comment"})
    files = _counts("File", "attached_to_name", names, {"attached_to_doctype": CARD})
    result = []
    for row in rows:
        items = checklist.get(row.name, [])
        result.append({
            "name": row.name, "title": row.title, "status": row.status, "position": row.position,
            "due_date": row.due_date, "modified": str(row.modified),
            "has_description": bool(strip_html(row.description or "").strip()),
            "assignees": [item.user for item in assignees.get(row.name, [])],
            "labels": [item.label for item in tags.get(row.name, [])],
            "checklist_done": sum(1 for item in items if item.done), "checklist_total": len(items),
            "comments": comments.get(row.name, 0), "attachments": files.get(row.name, 0),
        })
    return result


def board():
    workspace = _workspace()
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "archived": 0}, fields=SUMMARY_FIELDS,
                          order_by="position asc, creation asc", limit_page_length=0)
    return {"cards": _summaries(rows), "members": members(), "labels": labels(workspace),
            "statuses": list(contracts.STATUSES), "me": frappe.session.user, "admin": access.administrator()}


def summary(name):
    workspace = _workspace()
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "name": name or ""}, fields=SUMMARY_FIELDS)
    if not rows:
        raise Invalid("cowork.work.error.not_found")
    return _summaries(rows)[0]


def _attachments(name):
    return frappe.get_all("File", filters={"attached_to_doctype": CARD, "attached_to_name": name},
                          fields=["name", "file_name", "file_url", "file_size", "owner", "creation"],
                          order_by="creation desc")


def _activity(name):
    rows = frappe.get_all(ACTIVITY, filters={"card": name}, order_by="creation desc", limit_page_length=200,
                          fields=["name", "actor", "action", "detail", "content", "creation"])
    for row in rows:
        row.detail = json.loads(row.detail) if row.detail else {}
    return rows


def card(name):
    workspace = _workspace()
    doc = _card(name, workspace, archived=None)
    return {
        "name": doc.name, "title": doc.title, "status": doc.status, "due_date": doc.due_date,
        "description": doc.description or "", "archived": doc.archived, "modified": str(doc.modified),
        "owner": doc.owner, "creation": doc.creation,
        "assignees": [row.user for row in doc.assignees], "labels": [row.label for row in doc.labels],
        "checklist": [{"title": row.title, "done": row.done} for row in doc.checklist],
        "attachments": _attachments(doc.name), "activity": _activity(doc.name),
    }


def archived():
    workspace = _workspace()
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "archived": 1},
                          fields=["name", "title", "status", "archived_at"], order_by="archived_at desc",
                          limit_page_length=0)
    # 库里存 UTC；页面按站点时区的裸时间显示（同 `p.datetime_text` 的口径）。
    for row in rows:
        if row.archived_at:
            row.archived_at = convert_utc_to_system_timezone(row.archived_at).replace(tzinfo=None)
    return rows


# ── 卡片 ──────────────────────────────────────────────────────────────────────


def _column_tail(workspace, status, exclude=None):
    filters = {"workspace": workspace, "status": status, "archived": 0}
    if exclude:
        filters["name"] = ["!=", exclude]
    rows = frappe.get_all(CARD, filters=filters, fields=["position"], order_by="position desc", limit_page_length=1)
    return rows[0].position if rows else None


def create(title, status="todo"):
    workspace = _workspace()
    title = contracts.text(title, contracts.TITLE_MAX, required=True)
    status = contracts.status(status)
    # 同一列并发新建在列尾排队（DocType 元数据行上的锁，同 `scope.ensure_site_workspace`）。
    frappe.db.get_value("DocType", CARD, "name", for_update=True)
    doc = frappe.get_doc({"doctype": CARD, "workspace": workspace, "title": title, "status": status,
                          "position": contracts.position_between(_column_tail(workspace, status), None),
                          "completed_at": _now() if status == contracts.DONE else None})
    persist(doc)
    _log(doc.name, workspace, "create", {"status": status})
    _changed(doc.name, "create")
    return summary(doc.name)


def _members_set():
    return {row["user"] for row in members()}


def update(name, modified, values):
    """保存详情弹窗里改过的字段。`modified` 是打开时的版本，别人先保存过就拒绝（请重新打开）。"""
    workspace = _workspace()
    if not isinstance(values, dict):
        raise Invalid("cowork.work.error.request")
    doc = _card(name, workspace, lock=True, archived=False)
    if str(doc.modified) != str(modified or ""):
        raise Invalid("cowork.work.error.conflict")
    if "title" in values:
        doc.title = contracts.text(values["title"], contracts.TITLE_MAX, required=True)
    if "description" in values:
        description = str(values["description"] or "")
        if len(description) > contracts.DESCRIPTION_MAX:
            raise Invalid("cowork.work.error.too_long")
        doc.description = description if strip_html(description).strip() else ""
    if "due_date" in values:
        due = values["due_date"] or None
        try:
            due = str(getdate(due)) if due else None
        except Exception:
            raise Invalid("cowork.work.error.date")
        if str(doc.due_date or "") != str(due or ""):
            _log(doc.name, workspace, "due", {"due": due})
        doc.due_date = due
    if "assignees" in values:
        before = {row.user for row in doc.assignees}
        # 新加的人必须是现在能用看板的成员；已经在卡上的人（后来被停用 / 去掉权限）可以留着。
        allowed = _members_set() | before
        users = contracts.distinct(values["assignees"], len(allowed) or 1)
        if set(users) - allowed:
            raise Invalid("cowork.work.error.member")
        added = [user for user in users if user not in before]
        doc.set("assignees", [{"user": user} for user in users])
        if added:
            _log(doc.name, workspace, "assign", {"users": added})
    if "labels" in values:
        chosen = contracts.distinct(values["labels"], contracts.LABELS_MAX)
        known = {row.name for row in labels(workspace)}
        if set(chosen) - known:
            raise Invalid("cowork.work.error.label")
        doc.set("labels", [{"label": label} for label in chosen])
    if "checklist" in values:
        doc.set("checklist", contracts.checklist(values["checklist"]))
    if "status" in values:
        # 弹窗里换列：放到目标列列尾（拖动才能插到中间）。
        status = contracts.status(values["status"])
        if status != doc.status:
            _log(doc.name, workspace, "move", {"from": doc.status, "to": status})
            doc.position = contracts.position_between(_column_tail(workspace, status, doc.name), None)
            doc.completed_at = _now() if status == contracts.DONE else None
            doc.status = status
    persist(doc)
    _changed(doc.name, "update")
    return card(doc.name)


def move(name, status, before=None, after=None):
    """拖到 `status` 列、落在 `before` 与 `after` 两张卡之间（卡片号，没有就是列头 / 列尾）。"""
    workspace = _workspace()
    status = contracts.status(status)
    doc = _card(name, workspace, lock=True, archived=False)

    def neighbour(other):
        if not other or other == doc.name:
            return None
        row = frappe.db.get_value(CARD, {"name": other, "workspace": workspace, "status": status, "archived": 0},
                                  "position")
        return row

    position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        # 两卡之间挤不下：整列按当前顺序重排，再算一次。
        column = frappe.get_all(CARD, filters={"workspace": workspace, "status": status, "archived": 0,
                                               "name": ["!=", doc.name]},
                                fields=["name"], order_by="position asc, creation asc", limit_page_length=0)
        for row, value in zip(column, contracts.renumber(len(column))):
            frappe.db.set_value(CARD, row.name, "position", value, update_modified=False)
        position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        position = contracts.position_between(_column_tail(workspace, status, doc.name), None)
    previous = doc.status
    doc.status = status
    doc.position = position
    if status != previous:
        doc.completed_at = _now() if status == contracts.DONE else None
        _log(doc.name, workspace, "move", {"from": previous, "to": status})
    persist(doc)
    _changed(doc.name, "move")
    return summary(doc.name)


def archive(name):
    workspace = _workspace()
    doc = _card(name, workspace, lock=True, archived=False)
    doc.archived, doc.archived_at = 1, _now()
    persist(doc)
    _log(doc.name, workspace, "archive")
    _changed(doc.name, "archive")
    return {"name": doc.name}


def restore(name):
    workspace = _workspace()
    doc = _card(name, workspace, lock=True, archived=True)
    doc.archived, doc.archived_at = 0, None
    doc.position = contracts.position_between(_column_tail(workspace, doc.status, doc.name), None)
    persist(doc)
    _log(doc.name, workspace, "restore")
    _changed(doc.name, "restore")
    return summary(doc.name)


def delete(name):
    """彻底删除：只给管理员，且只删已归档的卡（先归档、再删，与 Trello 同）。"""
    workspace = _workspace()
    if not access.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    doc = _card(name, workspace, lock=True, archived=True)
    for file in _attachments(doc.name):
        frappe.delete_doc("File", file.name, ignore_permissions=True, force=True)
    for activity in frappe.get_all(ACTIVITY, filters={"card": doc.name}, pluck="name", limit_page_length=0):
        remove(frappe.get_doc(ACTIVITY, activity))
    remove(doc)
    _changed(name, "delete")
    return {"name": name}


# ── 评论与附件 ────────────────────────────────────────────────────────────────


def comment(name, content):
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    _log(doc.name, workspace, "comment", content=contracts.comment(content))
    _changed(doc.name, "comment")
    return _activity(doc.name)


def delete_comment(activity):
    """只能删自己写的评论。"""
    workspace = _workspace()
    row = frappe.db.get_value(ACTIVITY, {"name": activity or "", "workspace": workspace, "action": "comment"},
                              ["name", "card", "actor"], as_dict=True)
    if not row:
        raise Invalid("cowork.work.error.not_found")
    if row.actor != frappe.session.user:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    _card(row.card, workspace, archived=False)
    remove(frappe.get_doc(ACTIVITY, row.name))
    _changed(row.card, "comment")
    return _activity(row.card)


def attach(name, upload):
    """`upload` 是请求里的一个文件（`werkzeug` FileStorage）。存成挂在卡片上的原生私有 File。"""
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    if upload is None or not upload.filename:
        raise Invalid("cowork.work.error.file")
    content = upload.stream.read(contracts.ATTACHMENT_MAX_BYTES + 1)
    if not content:
        raise Invalid("cowork.work.error.file")
    if len(content) > contracts.ATTACHMENT_MAX_BYTES:
        raise Invalid("cowork.work.error.file_size")
    file = frappe.get_doc({"doctype": "File", "attached_to_doctype": CARD, "attached_to_name": doc.name,
                           "file_name": upload.filename, "is_private": 1, "content": content})
    file.insert(ignore_permissions=True)
    _log(doc.name, workspace, "attach", {"file": file.file_name})
    _changed(doc.name, "attach")
    return _attachments(doc.name)


def detach(name, file):
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    row = frappe.db.get_value("File", {"name": file or "", "attached_to_doctype": CARD,
                                       "attached_to_name": doc.name}, ["name", "file_name"], as_dict=True)
    if not row:
        raise Invalid("cowork.work.error.not_found")
    frappe.delete_doc("File", row.name, ignore_permissions=True)
    _log(doc.name, workspace, "detach", {"file": row.file_name})
    _changed(doc.name, "attach")
    return _attachments(doc.name)


# ── 标签 ──────────────────────────────────────────────────────────────────────


def save_labels(rows):
    """整板标签一次保存：带 name 的是改，不带的是新建，不在列表里的删掉（并从卡片上摘掉）。"""
    workspace = _workspace()
    if not isinstance(rows, list):
        raise Invalid("cowork.work.error.request")
    if len(rows) > contracts.LABELS_MAX:
        raise Invalid("cowork.work.error.too_many")
    frappe.db.get_value("DocType", LABEL, "name", for_update=True)
    current = {row.name: row for row in labels(workspace)}
    kept = set()
    for row in rows:
        if not isinstance(row, dict):
            raise Invalid("cowork.work.error.request")
        title = contracts.text(row.get("title"), contracts.LABEL_TITLE_MAX)
        color = contracts.color(row.get("color"))
        name = row.get("name")
        if name:
            if name not in current:
                raise Invalid("cowork.work.error.label")
            kept.add(name)
            if (current[name].title or "", current[name].color) != (title, color):
                doc = frappe.get_doc(LABEL, name)
                doc.title, doc.color = title, color
                persist(doc)
        else:
            persist(frappe.get_doc({"doctype": LABEL, "workspace": workspace, "title": title, "color": color}))
    for name in set(current) - kept:
        frappe.db.delete("Cowork Work Card Label", {"parenttype": CARD, "label": name})
        remove(frappe.get_doc(LABEL, name))
    _changed(None, "labels")
    return labels(workspace)
