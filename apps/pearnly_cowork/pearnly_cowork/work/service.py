"""工作协调看板的服务端实现（照 Trello）。

一个团队（站点）一块板，挂在站点内部那一份归属（`scope.default_workspace`）上。
- 列表：增删（归档 / 管理员彻底删除）、改名、排序、复制、颜色、「完成列表」（拖进去就算完成）。
- 卡片：增改移、完成（重复到期的卡完成后滚到下一次到期）、复制、模板、镜像、归档；开始日、到期日与时间、
  提醒时间；负责人、标签、多个清单、自定义字段、封面；附件（文件与链接）、评论（@）、关注。
每个写入口先 `access.require()`，再核对对象属于本站归属；写入走 `documents.persist`，原生接口写不进来。
改完用原生 `publish_realtime` 通知正在看板上的人刷新（只带卡片号）。
"""
import json
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import add_days, convert_utc_to_system_timezone, getdate, strip_html, today

from pearnly_cowork.access import scope
from pearnly_cowork.work import access, contracts, notify
from pearnly_cowork.work.contracts import Invalid
from pearnly_cowork.work.documents import persist, remove

CARD, LABEL, ACTIVITY = access.CARD, access.LABEL, access.ACTIVITY
COLUMN, FIELD, MIRROR, RULE = access.COLUMN, access.FIELD, access.MIRROR, access.RULE
REALTIME_EVENT = "cowork_work_changed"
SUMMARY_FIELDS = ["name", "title", "board_list", "completed", "position", "start_date", "due_date", "due_time",
                  "reminder", "recurrence", "is_template", "description", "cover_color", "cover_file",
                  "modified", "creation"]
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _workspace():
    access.require()
    workspace = scope.default_workspace()
    _ensure_lists(workspace)
    return workspace


def _changed(card, action=None):
    frappe.publish_realtime(REALTIME_EVENT, {"card": card, "action": action}, after_commit=True)


def _log(card, workspace, action, detail=None, content=None):
    return persist(frappe.get_doc({
        "doctype": ACTIVITY, "workspace": workspace, "card": card, "actor": frappe.session.user,
        "action": action, "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
        "content": content,
    }))


def _lock(doctype):
    # 同类对象的并发新建 / 重排在 DocType 元数据行上排队（同 `scope.ensure_site_workspace`）。
    frappe.db.get_value("DocType", doctype, "name", for_update=True)


# ── 列表 ──────────────────────────────────────────────────────────────────────


def _ensure_lists(workspace):
    """看板还没有任何列表时建默认三列（待办 / 进行中 / 已完成，最后一列是完成列表）。

    正常由装好 / 升级后的 `ensure_on_migrate` 建好；这里是兜底。打开看板是 GET 请求，框架会回滚 GET 里的写入，
    所以兜底建完要当场提交，否则页面拿到的列表号根本不存在（测试机空白站点 2026-09-27 实测）。
    """
    if frappe.db.exists(COLUMN, {"workspace": workspace}):
        return
    _lock(COLUMN)
    if frappe.db.exists(COLUMN, {"workspace": workspace}):
        return
    for index, (key, done) in enumerate(contracts.DEFAULT_LISTS):
        persist(frappe.get_doc({"doctype": COLUMN, "workspace": workspace, "title": _(f"cowork.work.status.{key}"),
                                "position": contracts.STEP * (index + 1), "is_done_list": 1 if done else 0}))
    frappe.db.commit()


def ensure_on_migrate():
    """装好 / 升级后给站点那一份归属建好默认三列（名字用站点语言；只在 Cowork 业务站点做）。"""
    if frappe.conf.get("pearnly_product") != "cowork" or not frappe.db.table_exists(COLUMN):
        return
    _ensure_lists(scope.ensure_site_workspace())


def lists(workspace, archived=False):
    return frappe.get_all(COLUMN, filters={"workspace": workspace, "archived": 1 if archived else 0},
                          fields=["name", "title", "color", "is_done_list", "position", "archived_at"],
                          order_by="position asc, creation asc", limit_page_length=0)


def _list(name, workspace, *, archived=False, lock=False):
    row = frappe.db.get_value(COLUMN, {"name": name or "", "workspace": workspace},
                              ["name", "title", "archived", "is_done_list", "position"], as_dict=True,
                              for_update=lock)
    if not row or (archived is not None and bool(row.archived) != archived):
        raise Invalid("cowork.work.error.list")
    return row


def _list_tail(workspace):
    rows = frappe.get_all(COLUMN, filters={"workspace": workspace, "archived": 0}, fields=["position"],
                          order_by="position desc", limit_page_length=1)
    return rows[0].position if rows else None


def create_list(title):
    workspace = _workspace()
    _lock(COLUMN)
    if frappe.db.count(COLUMN, {"workspace": workspace, "archived": 0}) >= contracts.LISTS_MAX:
        raise Invalid("cowork.work.error.too_many")
    doc = persist(frappe.get_doc({"doctype": COLUMN, "workspace": workspace,
                                  "title": contracts.text(title, contracts.LIST_TITLE_MAX, required=True),
                                  "position": contracts.position_between(_list_tail(workspace), None)}))
    _changed(None, "list")
    return {"name": doc.name}


def update_list(name, values):
    """改名、颜色、是否完成列表。"""
    workspace = _workspace()
    if not isinstance(values, dict):
        raise Invalid("cowork.work.error.request")
    _list(name, workspace, lock=True)
    doc = frappe.get_doc(COLUMN, name)
    if "title" in values:
        doc.title = contracts.text(values["title"], contracts.LIST_TITLE_MAX, required=True)
    if "color" in values:
        doc.color = contracts.color(values["color"]) if values["color"] else ""
    if "is_done_list" in values:
        doc.is_done_list = 1 if values["is_done_list"] in (1, True, "1") else 0
    persist(doc)
    _changed(None, "list")
    return {"name": doc.name, "title": doc.title, "color": doc.color or "", "is_done_list": doc.is_done_list}


def move_list(name, before=None, after=None):
    """拖列表 / 列表菜单「移动列表」：落在 `before` 与 `after` 两个列表之间。"""
    workspace = _workspace()
    _lock(COLUMN)
    _list(name, workspace)

    def neighbour(other):
        if not other or other == name:
            return None
        return frappe.db.get_value(COLUMN, {"name": other, "workspace": workspace, "archived": 0}, "position")

    position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        rows = frappe.get_all(COLUMN, filters={"workspace": workspace, "archived": 0, "name": ["!=", name]},
                              pluck="name", order_by="position asc, creation asc", limit_page_length=0)
        for row, value in zip(rows, contracts.renumber(len(rows))):
            frappe.db.set_value(COLUMN, row, "position", value, update_modified=False)
        position = (contracts.position_between(neighbour(before), neighbour(after))
                    or contracts.position_between(_list_tail(workspace), None))
    frappe.db.set_value(COLUMN, name, "position", position, update_modified=False)
    _changed(None, "list")
    return {"name": name, "position": position}


def copy_list(name, title):
    """复制列表：新列表放在原列表后面，带上里面所有未归档的卡（清单、标签、成员、字段、描述、日期）。"""
    workspace = _workspace()
    _lock(COLUMN)
    source = _list(name, workspace)
    following = frappe.get_all(COLUMN, filters={"workspace": workspace, "archived": 0,
                                                "position": [">", source.position]},
                               fields=["position"], order_by="position asc", limit_page_length=1)
    position = (contracts.position_between(source.position, following[0].position if following else None)
                or contracts.position_between(_list_tail(workspace), None))
    target = persist(frappe.get_doc({"doctype": COLUMN, "workspace": workspace,
                                     "title": contracts.text(title, contracts.LIST_TITLE_MAX, required=True),
                                     "color": frappe.db.get_value(COLUMN, name, "color"), "position": position}))
    for card_name in frappe.get_all(CARD, filters={"workspace": workspace, "board_list": source.name, "archived": 0},
                                    pluck="name", order_by="position asc, creation asc", limit_page_length=0):
        _clone(frappe.get_doc(CARD, card_name), workspace, target.name, None,
               {"checklist", "labels", "members", "fields"})
    _changed(None, "list")
    return {"name": target.name}


def archive_list(name):
    workspace = _workspace()
    _list(name, workspace, lock=True)
    if frappe.db.count(COLUMN, {"workspace": workspace, "archived": 0}) <= 1:
        raise Invalid("cowork.work.error.last_list")
    doc = frappe.get_doc(COLUMN, name)
    doc.archived, doc.archived_at = 1, _now()
    persist(doc)
    _changed(None, "list")
    return {"name": name}


def restore_list(name):
    workspace = _workspace()
    _list(name, workspace, archived=True, lock=True)
    doc = frappe.get_doc(COLUMN, name)
    doc.archived, doc.archived_at = 0, None
    doc.position = contracts.position_between(_list_tail(workspace), None)
    persist(doc)
    _changed(None, "list")
    return {"name": name}


def delete_list(name):
    """彻底删除列表和里面的卡：只给管理员，且只删已归档的列表。"""
    workspace = _workspace()
    if not access.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    _list(name, workspace, archived=True, lock=True)
    for card_name in frappe.get_all(CARD, filters={"workspace": workspace, "board_list": name}, pluck="name",
                                    limit_page_length=0):
        _purge(frappe.get_doc(CARD, card_name))
    for doctype in (MIRROR, RULE):
        for row in frappe.get_all(doctype, filters={"board_list": name}, pluck="name", limit_page_length=0):
            remove(frappe.get_doc(doctype, row))
    remove(frappe.get_doc(COLUMN, name))
    _changed(None, "list")
    return {"name": name}


SORTS = {"created_desc": ("creation", True), "created_asc": ("creation", False),
         "due": ("due_date", False), "title": ("title", False)}


def sort_list(name, key):
    """列表菜单「排序依据」：按选的规则把这一列重新排好（写回位置，所有人看到的一样）。"""
    workspace = _workspace()
    _list(name, workspace)
    if key not in SORTS:
        raise Invalid("cowork.work.error.request")
    field, reverse = SORTS[key]
    _lock(CARD)
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "board_list": name, "archived": 0},
                          fields=["name", "title", "due_date", "creation"], limit_page_length=0)
    if field == "due_date":
        rows.sort(key=lambda row: (row.due_date is None, row.due_date or getdate("1900-01-01")))
    elif field == "title":
        rows.sort(key=lambda row: (row.title or "").casefold())
    else:
        rows.sort(key=lambda row: row.creation, reverse=reverse)
    for row, value in zip(rows, contracts.renumber(len(rows))):
        frappe.db.set_value(CARD, row.name, "position", value, update_modified=False)
    _changed(None, "sort")
    return {"count": len(rows)}


def move_all(name, target):
    """列表菜单「移动此列表中的所有卡片」：按原顺序接到目标列表列尾。"""
    workspace = _workspace()
    if name == target:
        raise Invalid("cowork.work.error.request")
    _list(name, workspace)
    target_row = _list(target, workspace)
    _lock(CARD)
    names = frappe.get_all(CARD, filters={"workspace": workspace, "board_list": name, "archived": 0},
                           pluck="name", order_by="position asc, creation asc", limit_page_length=0)
    for card_name in names:
        doc = frappe.get_doc(CARD, card_name)
        _place(doc, workspace, target_row)
        persist(doc)
    _changed(None, "move")
    return {"moved": len(names)}


def archive_all(name):
    """列表菜单「将此列表中的所有卡片归档」。"""
    workspace = _workspace()
    _list(name, workspace)
    names = frappe.get_all(CARD, filters={"workspace": workspace, "board_list": name, "archived": 0},
                           pluck="name", limit_page_length=0)
    for card_name in names:
        doc = _card(card_name, workspace, lock=True, archived=False)
        doc.archived, doc.archived_at = 1, _now()
        persist(doc)
        _log(doc.name, workspace, "archive")
    _changed(None, "archive")
    return {"archived": len(names)}


# ── 读 ────────────────────────────────────────────────────────────────────────


def members():
    """能被分派的人 = Cowork 团队管理里勾了「工作协调」的人和产品管理员（启用的账号）。"""
    users = sorted(notify.members())
    if not users:
        return []
    rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name", "user_image"],
                          order_by="full_name asc")
    return [{"user": row.name, "name": row.full_name or row.name, "image": row.user_image} for row in rows]


def labels(workspace=None):
    workspace = workspace or _workspace()
    return frappe.get_all(LABEL, filters={"workspace": workspace}, fields=["name", "title", "color"],
                          order_by="creation asc")


def fields(workspace=None):
    workspace = workspace or _workspace()
    rows = frappe.get_all(FIELD, filters={"workspace": workspace},
                          fields=["name", "title", "fieldtype", "options", "show_on_card"],
                          order_by="position asc, creation asc", limit_page_length=0)
    for row in rows:
        row.options = json.loads(row.options) if row.options else []
    return rows


def _children(doctype, parents, columns):
    if not parents:
        return {}
    grouped = {}
    for row in frappe.get_all(doctype, filters={"parenttype": CARD, "parent": ["in", parents]},
                              fields=["parent", *columns], order_by="idx asc", limit_page_length=0):
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
    checklist = _children("Cowork Work Checklist Item", names, ["title", "done"])
    watchers = _children("Cowork Work Card Watcher", names, ["user"])
    values = _children("Cowork Work Card Value", names, ["field", "value"])
    covers = dict(frappe.get_all("File", filters={"name": ["in", [row.cover_file for row in rows if row.cover_file] or [""]]},
                                 fields=["name", "file_url"], as_list=True))
    comments = _counts(ACTIVITY, "card", names, {"action": "comment"})
    files = _counts("File", "attached_to_name", names, {"attached_to_doctype": CARD})
    result = []
    for row in rows:
        items = [item for item in checklist.get(row.name, []) if item.title]
        result.append({
            "name": row.name, "title": row.title, "board_list": row.board_list, "completed": row.completed,
            "position": row.position, "start_date": row.start_date, "due_date": row.due_date,
            "due_time": str(row.due_time) if row.due_time is not None else None, "reminder": row.reminder,
            "recurrence": row.recurrence, "is_template": row.is_template, "modified": str(row.modified),
            "has_description": bool(strip_html(row.description or "").strip()),
            "assignees": [item.user for item in assignees.get(row.name, [])],
            "labels": [item.label for item in tags.get(row.name, [])],
            "watching": any(item.user == frappe.session.user for item in watchers.get(row.name, [])),
            "values": {item.field: item.value for item in values.get(row.name, [])},
            "cover_color": row.cover_color or "", "cover_url": covers.get(row.cover_file or "", ""),
            "checklist_done": sum(1 for item in items if item.done), "checklist_total": len(items),
            "comments": comments.get(row.name, 0), "attachments": files.get(row.name, 0),
        })
    return result


def board():
    workspace = _workspace()
    open_lists = lists(workspace)
    names = [row.name for row in open_lists]
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "archived": 0, "board_list": ["in", names or [""]]},
                          fields=SUMMARY_FIELDS, order_by="position asc, creation asc", limit_page_length=0)
    visible = {row.name for row in rows}
    mirrors = [row for row in frappe.get_all(MIRROR, filters={"workspace": workspace, "board_list": ["in", names or [""]]},
                                             fields=["name", "card", "board_list", "position"],
                                             order_by="position asc, creation asc", limit_page_length=0)
               if row.card in visible]
    return {"lists": open_lists, "cards": _summaries(rows), "mirrors": mirrors, "members": members(),
            "labels": labels(workspace), "fields": fields(workspace), "me": frappe.session.user,
            "admin": access.administrator(),
            "unread": frappe.db.count(access.NOTIFICATION, {"workspace": workspace, "user": frappe.session.user,
                                                            "is_read": 0})}


def summary(name):
    workspace = _workspace()
    rows = frappe.get_all(CARD, filters={"workspace": workspace, "name": name or ""}, fields=SUMMARY_FIELDS)
    if not rows:
        raise Invalid("cowork.work.error.not_found")
    return _summaries(rows)[0]


def _attachments(name):
    rows = frappe.get_all("File", filters={"attached_to_doctype": CARD, "attached_to_name": name},
                          fields=["name", "file_name", "file_url", "file_size", "is_private", "owner", "creation"],
                          order_by="creation desc")
    for row in rows:
        # 链接附件：原生 File 只存地址，没有文件内容。
        row.is_link = (row.file_url or "").startswith(("http://", "https://"))
    return rows


def _activity(name):
    rows = frappe.get_all(ACTIVITY, filters={"card": name}, order_by="creation desc", limit_page_length=200,
                          fields=["name", "actor", "action", "detail", "content", "creation"])
    for row in rows:
        row.detail = json.loads(row.detail) if row.detail else {}
    return rows


def _checklists(doc):
    groups = {}
    for row in doc.checklist:
        group = groups.setdefault(row.group_id or "c1", {"id": row.group_id or "c1", "title": row.checklist or "",
                                                         "items": []})
        if row.title:
            group["items"].append({"title": row.title, "done": row.done})
    return list(groups.values())


def card(name):
    workspace = _workspace()
    doc = _card(name, workspace, archived=None)
    return {
        "name": doc.name, "title": doc.title, "board_list": doc.board_list, "completed": doc.completed,
        "start_date": doc.start_date, "due_date": doc.due_date,
        "due_time": str(doc.due_time) if doc.due_time is not None else None,
        "reminder": doc.reminder or contracts.DEFAULT_REMINDER, "recurrence": doc.recurrence or "never",
        "is_template": doc.is_template, "description": doc.description or "", "archived": doc.archived,
        "modified": str(doc.modified), "owner": doc.owner, "creation": doc.creation,
        "assignees": [row.user for row in doc.assignees], "labels": [row.label for row in doc.labels],
        "watchers": [row.user for row in doc.watchers],
        "watching": any(row.user == frappe.session.user for row in doc.watchers),
        "cover_color": doc.cover_color or "", "cover_file": doc.cover_file or "",
        "checklists": _checklists(doc), "values": {row.field: row.value for row in doc.get("values") or []},
        "mirrors": frappe.get_all(MIRROR, filters={"card": doc.name}, fields=["name", "board_list"]),
        "attachments": _attachments(doc.name), "activity": _activity(doc.name),
    }


def archived():
    workspace = _workspace()
    titles = {row.name: row.title for row in frappe.get_all(COLUMN, filters={"workspace": workspace},
                                                             fields=["name", "title"], limit_page_length=0)}
    cards = frappe.get_all(CARD, filters={"workspace": workspace, "archived": 1},
                           fields=["name", "title", "board_list", "archived_at"], order_by="archived_at desc",
                           limit_page_length=0)
    archived_lists = lists(workspace, archived=True)
    # 库里存 UTC；页面按站点时区的裸时间显示（同 `p.datetime_text` 的口径）。
    for row in [*cards, *archived_lists]:
        if row.archived_at:
            row.archived_at = convert_utc_to_system_timezone(row.archived_at).replace(tzinfo=None)
    for row in cards:
        row.list_title = titles.get(row.board_list, "")
    return {"cards": cards, "lists": archived_lists}


# ── 卡片 ──────────────────────────────────────────────────────────────────────


def _card(name, workspace, *, lock=False, archived=False):
    row = frappe.db.get_value(CARD, {"name": name or "", "workspace": workspace},
                              ["name", "archived"], as_dict=True, for_update=lock)
    if not row or (archived is not None and bool(row.archived) != archived):
        raise Invalid("cowork.work.error.not_found")
    return frappe.get_doc(CARD, row.name)


def _column_tail(workspace, board_list, exclude=None):
    filters = {"workspace": workspace, "board_list": board_list, "archived": 0}
    if exclude:
        filters["name"] = ["!=", exclude]
    rows = frappe.get_all(CARD, filters=filters, fields=["position"], order_by="position desc", limit_page_length=1)
    return rows[0].position if rows else None


def _complete(doc, workspace, done):
    """标记完成 / 未完成。重复到期的卡"完成"时不算完成：到期日滚到下一次（照 Trello），返回 True。"""
    if done and (doc.recurrence or "never") != "never" and doc.due_date:
        due = contracts.next_due(getdate(doc.due_date), doc.recurrence)
        doc.due_date, doc.completed, doc.completed_at = str(due), 0, None
        _log(doc.name, workspace, "repeat", {"due": str(due)})
        return True
    if bool(doc.completed) == bool(done):
        return False
    doc.completed = 1 if done else 0
    doc.completed_at = _now() if done else None
    _log(doc.name, workspace, "complete" if done else "uncomplete")
    if done:
        notify.send(notify.audience(doc), doc, "complete")
    return False


def _place(doc, workspace, target, position=None):
    """换列表：记活动、提醒负责人和关注的人；进「完成列表」算完成、从完成列表拿出来算未完成。
    重复到期的卡拖进完成列表：到期日滚到下一次，卡留在原列表。"""
    if target.name != doc.board_list:
        source = frappe.db.get_value(COLUMN, doc.board_list, ["name", "title", "is_done_list"], as_dict=True)
        if target.is_done_list and not doc.completed and (doc.recurrence or "never") != "never" and doc.due_date:
            _complete(doc, workspace, True)
            return
        _log(doc.name, workspace, "move", {"from": source.title if source else "", "to": target.title})
        notify.send(notify.audience(doc), doc, "move", {"from": source.title if source else "", "to": target.title})
        doc.board_list = target.name
        if target.is_done_list:
            _complete(doc, workspace, True)
        elif source and source.is_done_list and doc.completed:
            _complete(doc, workspace, False)
        _apply_rules(doc, workspace, target.name)
        if position is None:
            position = contracts.position_between(_column_tail(workspace, target.name, doc.name), None)
    if position is not None:
        doc.position = position


def create(title, board_list, template=None):
    """列尾快速新建；带 `template` 就照那张模板卡建（清单、标签、成员、字段、描述、日期、封面颜色）。"""
    workspace = _workspace()
    target = _list(board_list, workspace)
    title = contracts.text(title, contracts.TITLE_MAX, required=True)
    _lock(CARD)
    if template:
        source = _card(template, workspace, archived=None)
        if not source.is_template:
            raise Invalid("cowork.work.error.not_found")
        doc = _clone(source, workspace, target.name, title, {"checklist", "labels", "members", "fields"},
                     action="create")
    else:
        doc = persist(frappe.get_doc({"doctype": CARD, "workspace": workspace, "title": title,
                                      "board_list": target.name, "reminder": contracts.DEFAULT_REMINDER,
                                      "recurrence": "never", "completed": 1 if target.is_done_list else 0,
                                      "completed_at": _now() if target.is_done_list else None,
                                      "position": contracts.position_between(_column_tail(workspace, target.name), None)}))
        _log(doc.name, workspace, "create", {"list": target.title})
        if _apply_rules(doc, workspace, target.name):
            persist(doc)
    _changed(doc.name, "create")
    return summary(doc.name)


def _clone(source, workspace, board_list, title, keep, action="copy"):
    doc = frappe.get_doc({"doctype": CARD, "workspace": workspace, "title": title or source.title,
                          "board_list": board_list, "description": source.description,
                          "start_date": source.start_date, "due_date": source.due_date, "due_time": source.due_time,
                          "reminder": source.reminder or contracts.DEFAULT_REMINDER,
                          "recurrence": source.recurrence or "never", "cover_color": source.cover_color,
                          "position": contracts.position_between(_column_tail(workspace, board_list), None)})
    if frappe.db.get_value(COLUMN, board_list, "is_done_list"):
        doc.completed, doc.completed_at = 1, _now()
    if "checklist" in keep:
        doc.set("checklist", [{"checklist": row.checklist, "group_id": row.group_id, "title": row.title,
                               "done": 0 if action == "create" else row.done} for row in source.checklist])
    if "labels" in keep:
        doc.set("labels", [{"label": row.label} for row in source.labels])
    if "members" in keep:
        allowed = _members_set()
        doc.set("assignees", [{"user": row.user} for row in source.assignees if row.user in allowed])
    if "fields" in keep:
        doc.set("values", [{"field": row.field, "value": row.value} for row in source.get("values") or []])
    persist(doc)
    _log(doc.name, workspace, action, {"from": source.title})
    if doc.assignees:
        notify.send([row.user for row in doc.assignees], doc, "assign")
    if _apply_rules(doc, workspace, board_list):
        persist(doc)
    return doc


def _members_set():
    return notify.members()


def update(name, modified, values):
    """保存详情弹窗 / 快捷菜单里改过的字段。`modified` 是打开时的版本，别人先保存过就拒绝（请重新打开）。"""
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
    for key in ("start_date", "due_date"):
        if key in values:
            try:
                value = str(getdate(values[key])) if values[key] else None
            except Exception:
                raise Invalid("cowork.work.error.date")
            if key == "due_date" and str(doc.due_date or "") != str(value or ""):
                _log(doc.name, workspace, "due", {"due": value})
            doc.set(key, value)
    if "due_time" in values:
        value = values["due_time"] or None
        if value:
            try:
                contracts.due_at("2000-01-01", value)
            except (ValueError, IndexError):
                raise Invalid("cowork.work.error.date")
        doc.due_time = value
    if "reminder" in values:
        doc.reminder = contracts.choice(values["reminder"], contracts.REMINDERS)
    if "recurrence" in values:
        doc.recurrence = contracts.choice(values["recurrence"], contracts.REPEATS)
    if doc.start_date and doc.due_date and getdate(doc.start_date) > getdate(doc.due_date):
        raise Invalid("cowork.work.error.start_after_due")
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
            notify.send(added, doc, "assign")
    if "labels" in values:
        chosen = contracts.distinct(values["labels"], contracts.LABELS_MAX)
        known = {row.name for row in labels(workspace)}
        if set(chosen) - known:
            raise Invalid("cowork.work.error.label")
        doc.set("labels", [{"label": label} for label in chosen])
    if "checklists" in values:
        rows = []
        for index, group in enumerate(contracts.checklists(values["checklists"]), start=1):
            rows += [{"checklist": group["title"], "group_id": f"c{index}", **item} for item in group["items"]]
            if not group["items"]:
                # 空清单也要留住（刚建好、还没加项）：记一行空标题的占位，读的时候不算进度。
                rows.append({"checklist": group["title"], "group_id": f"c{index}", "title": "", "done": 0})
        doc.set("checklist", rows)
    if "values" in values:
        doc.set("values", _field_values(workspace, values["values"]))
    if "cover_color" in values:
        doc.cover_color = contracts.color(values["cover_color"]) if values["cover_color"] else ""
        if doc.cover_color:
            doc.cover_file = ""
    if "cover_file" in values:
        doc.cover_file = _cover_file(doc.name, values["cover_file"]) if values["cover_file"] else ""
        if doc.cover_file:
            doc.cover_color = ""
    if "is_template" in values:
        doc.is_template = 1 if values["is_template"] in (1, True, "1") else 0
    if "completed" in values:
        _complete(doc, workspace, values["completed"] in (1, True, "1"))
    if "board_list" in values and values["board_list"] != doc.board_list:
        _place(doc, workspace, _list(values["board_list"], workspace))
    persist(doc)
    _changed(doc.name, "update")
    return card(doc.name)


def _field_values(workspace, raw):
    if not isinstance(raw, dict):
        raise Invalid("cowork.work.error.request")
    known = {row.name: row for row in fields(workspace)}
    rows = []
    for field, value in raw.items():
        if field not in known:
            raise Invalid("cowork.work.error.field_value")
        cleaned = contracts.field_value(known[field].fieldtype, known[field].options, value)
        if cleaned != "":
            rows.append({"field": field, "value": cleaned})
    return rows


def move(name, board_list, before=None, after=None):
    """拖到 `board_list`、落在 `before` 与 `after` 两张卡之间（卡片号，没有就是列头 / 列尾）。"""
    workspace = _workspace()
    target = _list(board_list, workspace)
    doc = _card(name, workspace, lock=True, archived=False)

    def neighbour(other):
        if not other or other == doc.name:
            return None
        return frappe.db.get_value(CARD, {"name": other, "workspace": workspace, "board_list": target.name,
                                          "archived": 0}, "position")

    position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        # 两卡之间挤不下：整列按当前顺序重排，再算一次。
        column = frappe.get_all(CARD, filters={"workspace": workspace, "board_list": target.name, "archived": 0,
                                               "name": ["!=", doc.name]},
                                pluck="name", order_by="position asc, creation asc", limit_page_length=0)
        for row, value in zip(column, contracts.renumber(len(column))):
            frappe.db.set_value(CARD, row, "position", value, update_modified=False)
        position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        position = contracts.position_between(_column_tail(workspace, target.name, doc.name), None)
    _place(doc, workspace, target, position)
    persist(doc)
    _changed(doc.name, "move")
    return summary(doc.name)


def toggle_done(name):
    """卡片上的完成圆圈（照 Trello）：只改完成与否，不挪列表；重复到期的卡完成后滚到下一次到期。"""
    workspace = _workspace()
    doc = _card(name, workspace, lock=True, archived=False)
    _complete(doc, workspace, not doc.completed)
    persist(doc)
    _changed(doc.name, "update")
    return summary(doc.name)


def copy(name, title, board_list, keep=None):
    """复制卡片：新标题、目标列表；可选带上清单 / 标签 / 成员 / 字段。放在目标列表列尾。"""
    workspace = _workspace()
    source = _card(name, workspace, archived=None)
    target = _list(board_list, workspace)
    title = contracts.text(title, contracts.TITLE_MAX, required=True)
    keep = set(contracts.distinct(keep, 4)) & {"checklist", "labels", "members", "fields"}
    _lock(CARD)
    doc = _clone(source, workspace, target.name, title, keep)
    _changed(doc.name, "create")
    return summary(doc.name)


def mirror(name, board_list):
    """卡片镜像：这张卡也显示在另一个列表里（同一张卡，改哪边都是改它）。"""
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    target = _list(board_list, workspace)
    if target.name == doc.board_list or frappe.db.exists(MIRROR, {"card": doc.name, "board_list": target.name}):
        raise Invalid("cowork.work.error.mirror")
    _lock(MIRROR)
    tail = frappe.get_all(MIRROR, filters={"board_list": target.name}, fields=["position"], order_by="position desc",
                          limit_page_length=1)
    tails = [value for value in (tail[0].position if tail else None, _column_tail(workspace, target.name))
             if value is not None]
    row = persist(frappe.get_doc({"doctype": MIRROR, "workspace": workspace, "card": doc.name,
                                  "board_list": target.name,
                                  "position": contracts.position_between(max(tails) if tails else None, None)}))
    _log(doc.name, workspace, "mirror", {"to": target.title})
    _changed(doc.name, "mirror")
    return {"name": row.name}


def move_mirror(name, board_list, before=None, after=None):
    workspace = _workspace()
    row = frappe.db.get_value(MIRROR, {"name": name or "", "workspace": workspace}, ["name", "card"], as_dict=True)
    if not row:
        raise Invalid("cowork.work.error.not_found")
    target = _list(board_list, workspace)
    if target.name == frappe.db.get_value(CARD, row.card, "board_list"):
        raise Invalid("cowork.work.error.mirror")

    def neighbour(other):
        if not other:
            return None
        return (frappe.db.get_value(CARD, {"name": other, "board_list": target.name}, "position")
                or frappe.db.get_value(MIRROR, {"name": other, "board_list": target.name}, "position"))

    doc = frappe.get_doc(MIRROR, row.name)
    doc.board_list = target.name
    doc.position = (contracts.position_between(neighbour(before), neighbour(after))
                    or contracts.position_between(_column_tail(workspace, target.name), None))
    persist(doc)
    _changed(row.card, "mirror")
    return {"name": doc.name}


def unmirror(name):
    workspace = _workspace()
    row = frappe.db.get_value(MIRROR, {"name": name or "", "workspace": workspace}, ["name", "card"], as_dict=True)
    if not row:
        raise Invalid("cowork.work.error.not_found")
    remove(frappe.get_doc(MIRROR, row.name))
    _changed(row.card, "mirror")
    return {"name": name}


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
    target = frappe.db.get_value(COLUMN, doc.board_list, ["name", "archived"], as_dict=True)
    if not target or target.archived:
        # 原列表已归档 / 删掉：放回第一个列表。
        first = lists(workspace)
        if not first:
            raise Invalid("cowork.work.error.list")
        doc.board_list = first[0].name
    doc.archived, doc.archived_at = 0, None
    doc.position = contracts.position_between(_column_tail(workspace, doc.board_list, doc.name), None)
    persist(doc)
    _log(doc.name, workspace, "restore")
    _changed(doc.name, "restore")
    return summary(doc.name)


def _purge(doc):
    for file in _attachments(doc.name):
        frappe.delete_doc("File", file.name, ignore_permissions=True, force=True)
    for doctype in (ACTIVITY, access.NOTIFICATION, MIRROR):
        for row in frappe.get_all(doctype, filters={"card": doc.name}, pluck="name", limit_page_length=0):
            remove(frappe.get_doc(doctype, row))
    remove(doc)


def delete(name):
    """彻底删除：只给管理员，且只删已归档的卡（先归档、再删，与 Trello 同）。"""
    workspace = _workspace()
    if not access.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    _purge(_card(name, workspace, lock=True, archived=True))
    _changed(name, "delete")
    return {"name": name}


def watch(name, on):
    """关注 / 取消关注（只改自己）。"""
    workspace = _workspace()
    doc = _card(name, workspace, lock=True, archived=False)
    others = [row.user for row in doc.watchers if row.user != frappe.session.user]
    doc.set("watchers", [{"user": user} for user in others + ([frappe.session.user] if on else [])])
    persist(doc)
    _changed(doc.name, "watch")
    return summary(doc.name)


def _cover_file(card_name, file):
    row = frappe.db.get_value("File", {"name": file, "attached_to_doctype": CARD, "attached_to_name": card_name},
                              ["name", "file_name", "file_url"], as_dict=True)
    if (not row or (row.file_url or "").startswith(("http://", "https://"))
            or not (row.file_name or "").lower().endswith(IMAGE_TYPES)):
        raise Invalid("cowork.work.error.cover")
    return row.name


# ── 评论与附件 ────────────────────────────────────────────────────────────────


def comment(name, content, mentions=None):
    """发评论；`mentions` 是评论里 @ 到的成员（页面在选人时收集），各发一条"@了你"，
    负责人和关注的人再各发一条"有新评论"（已经被 @ 的不重复）。"""
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    content = contracts.comment(content)
    mentioned = set(contracts.distinct(mentions, 50)) & _members_set()
    _log(doc.name, workspace, "comment", {"mentions": sorted(mentioned)} if mentioned else None, content=content)
    excerpt = content[:120]
    notify.send(mentioned, doc, "mention", {"text": excerpt})
    notify.send(notify.audience(doc) - mentioned, doc, "comment", {"text": excerpt})
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


def attach_link(name, url, text=None):
    """附件里加一个链接（照 Trello「搜索或粘贴链接」+「显示文本」）：原生 File 只存地址。"""
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    url = contracts.link(url)
    label = contracts.text(text, contracts.TITLE_MAX) or url[:contracts.TITLE_MAX]
    file = frappe.get_doc({"doctype": "File", "attached_to_doctype": CARD, "attached_to_name": doc.name,
                           "file_name": label, "file_url": url, "is_private": 0})
    file.insert(ignore_permissions=True)
    _log(doc.name, workspace, "attach", {"file": label})
    _changed(doc.name, "attach")
    return _attachments(doc.name)


def detach(name, file):
    workspace = _workspace()
    doc = _card(name, workspace, archived=False)
    row = frappe.db.get_value("File", {"name": file or "", "attached_to_doctype": CARD,
                                       "attached_to_name": doc.name}, ["name", "file_name"], as_dict=True)
    if not row:
        raise Invalid("cowork.work.error.not_found")
    if doc.cover_file == row.name:
        doc.cover_file = ""
        persist(doc)
    frappe.delete_doc("File", row.name, ignore_permissions=True)
    _log(doc.name, workspace, "detach", {"file": row.file_name})
    _changed(doc.name, "attach")
    return _attachments(doc.name)


# ── 标签与自定义字段（全板共用） ──────────────────────────────────────────────


def save_labels(rows):
    """整板标签一次保存：带 name 的是改，不带的是新建，不在列表里的删掉（并从卡片上摘掉）。"""
    workspace = _workspace()
    if not isinstance(rows, list):
        raise Invalid("cowork.work.error.request")
    if len(rows) > contracts.LABELS_MAX:
        raise Invalid("cowork.work.error.too_many")
    _lock(LABEL)
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


def save_fields(rows):
    """整板自定义字段一次保存（照标签）：名字、类型（文本 / 数字 / 日期 / 勾选 / 下拉）、下拉选项、是否显示在卡片正面。
    删掉的字段从所有卡片上摘掉它的值；已有字段不改类型（改类型就删掉重建）。"""
    workspace = _workspace()
    if not isinstance(rows, list):
        raise Invalid("cowork.work.error.request")
    if len(rows) > contracts.FIELDS_MAX:
        raise Invalid("cowork.work.error.too_many")
    _lock(FIELD)
    current = {row.name: row for row in fields(workspace)}
    kept = set()
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise Invalid("cowork.work.error.request")
        title = contracts.text(row.get("title"), contracts.LABEL_TITLE_MAX, required=True,
                               key="cowork.work.error.field_title")
        fieldtype = contracts.choice(row.get("fieldtype"), contracts.FIELD_TYPES)
        options = contracts.field_options(fieldtype, row.get("options"))
        values = {"title": title, "options": json.dumps(options, ensure_ascii=False) if options else None,
                  "show_on_card": 1 if row.get("show_on_card") in (1, True, "1") else 0,
                  "position": contracts.STEP * index}
        name = row.get("name")
        if name:
            if name not in current or current[name].fieldtype != fieldtype:
                raise Invalid("cowork.work.error.field_value")
            kept.add(name)
            doc = frappe.get_doc(FIELD, name)
            doc.update(values)
            persist(doc)
            if fieldtype == "select":
                # 下拉少了的选项：卡片上用着它的值一起清掉。
                frappe.db.delete("Cowork Work Card Value", {"parenttype": CARD, "field": name,
                                                            "value": ["not in", options]})
        else:
            persist(frappe.get_doc({"doctype": FIELD, "workspace": workspace, "fieldtype": fieldtype, **values}))
    for name in set(current) - kept:
        frappe.db.delete("Cowork Work Card Value", {"parenttype": CARD, "field": name})
        remove(frappe.get_doc(FIELD, name))
    _changed(None, "fields")
    return fields(workspace)


# ── 提醒 ──────────────────────────────────────────────────────────────────────


def notifications():
    return notify.mine(_workspace())


def read_notifications(names=None):
    workspace = _workspace()
    notify.mark_read(workspace, contracts.distinct(names, 200) if names else None)
    return notify.mine(workspace)


# ── 自动化（照 Trello「卡片被添加到列表中时」） ────────────────────────────────


def rules(workspace=None):
    workspace = workspace or _workspace()
    return frappe.get_all(RULE, filters={"workspace": workspace},
                          fields=["name", "board_list", "action", "user", "label", "days", "enabled"],
                          order_by="creation asc", limit_page_length=0)


def _apply_rules(doc, workspace, board_list):
    """卡片进入 `board_list`（新建、拖入、复制进来）时按规则自动做事；有改动返回 True（调用方再保存）。
    规则只改负责人 / 标签 / 到期日 / 完成 / 关注，不挪卡片，所以不会互相触发成死循环。"""
    changed = False
    for rule in frappe.get_all(RULE, filters={"workspace": workspace, "board_list": board_list, "enabled": 1},
                               fields=["action", "user", "label", "days"], order_by="creation asc",
                               limit_page_length=0):
        detail = {"action": rule.action}
        if rule.action == "assign":
            if not rule.user or rule.user not in _members_set() or rule.user in {row.user for row in doc.assignees}:
                continue
            doc.append("assignees", {"user": rule.user})
            notify.send([rule.user], doc, "assign")
            detail["user"] = rule.user
        elif rule.action == "watch":
            if not rule.user or rule.user in {row.user for row in doc.watchers}:
                continue
            doc.append("watchers", {"user": rule.user})
            detail["user"] = rule.user
        elif rule.action == "label":
            title = frappe.db.get_value(LABEL, {"name": rule.label or "", "workspace": workspace}, "title")
            if title is None or rule.label in {row.label for row in doc.labels}:
                continue
            doc.append("labels", {"label": rule.label})
            detail["label"] = title
        elif rule.action == "due_days":
            doc.due_date = str(getdate(add_days(today(), int(rule.days or 0))))
            detail["due"] = doc.due_date
        elif rule.action == "complete":
            if doc.completed:
                continue
            _complete(doc, workspace, True)
        else:
            continue
        _log(doc.name, workspace, "rule", detail)
        changed = True
    return changed


def save_rules(rows):
    """整板规则一次保存（照标签）：带 name 的是改，不带的是新建，不在列表里的删掉。"""
    workspace = _workspace()
    if not isinstance(rows, list):
        raise Invalid("cowork.work.error.request")
    if len(rows) > contracts.RULES_MAX:
        raise Invalid("cowork.work.error.too_many")
    _lock(RULE)
    current = {row.name: row for row in rules(workspace)}
    members = _members_set()
    known_labels = {row.name for row in labels(workspace)}
    kept = set()
    for row in rows:
        if not isinstance(row, dict):
            raise Invalid("cowork.work.error.request")
        _list(row.get("board_list"), workspace)
        action = contracts.choice(row.get("action"), contracts.RULE_ACTIONS)
        values = {"board_list": row["board_list"], "action": action, "user": None, "label": None, "days": 0,
                  "enabled": 0 if row.get("enabled") in (0, False, "0") else 1}
        if action in ("assign", "watch"):
            if row.get("user") not in members:
                raise Invalid("cowork.work.error.member")
            values["user"] = row["user"]
        elif action == "label":
            if row.get("label") not in known_labels:
                raise Invalid("cowork.work.error.label")
            values["label"] = row["label"]
        elif action == "due_days":
            try:
                days = int(row.get("days"))
            except (TypeError, ValueError):
                raise Invalid("cowork.work.error.rule_days")
            if not 0 <= days <= 365:
                raise Invalid("cowork.work.error.rule_days")
            values["days"] = days
        name = row.get("name")
        if name:
            if name not in current:
                raise Invalid("cowork.work.error.request")
            kept.add(name)
            doc = frappe.get_doc(RULE, name)
            doc.update(values)
            persist(doc)
        else:
            persist(frappe.get_doc({"doctype": RULE, "workspace": workspace, **values}))
    for name in set(current) - kept:
        remove(frappe.get_doc(RULE, name))
    _changed(None, "rules")
    return rules(workspace)


# ── 全文搜索与导出 ────────────────────────────────────────────────────────────


def search(keyword):
    """筛选区的关键词：卡片标题、描述、评论、清单项、自定义字段值里有就算（不区分大小写）。"""
    workspace = _workspace()
    keyword = contracts.text(keyword, 100)
    if len(keyword) < contracts.SEARCH_MIN:
        return []
    like = ["like", f"%{keyword}%"]
    names = set(frappe.get_all(CARD, filters={"workspace": workspace, "archived": 0},
                               or_filters={"title": like, "description": like}, pluck="name", limit_page_length=0))
    names |= set(frappe.get_all(ACTIVITY, filters={"workspace": workspace, "action": "comment", "content": like},
                                pluck="card", limit_page_length=0))
    for doctype, field in (("Cowork Work Checklist Item", "title"), ("Cowork Work Card Value", "value")):
        names |= set(frappe.get_all(doctype, filters={"parenttype": CARD, field: like}, pluck="parent",
                                    limit_page_length=0))
    if not names:
        return []
    return frappe.get_all(CARD, filters={"workspace": workspace, "archived": 0, "name": ["in", list(names)]},
                          pluck="name", limit_page_length=0)


def export(names=None):
    """表格视图「导出 Excel」：按页面上当前看得到的卡片（`names`，没给就是整板）出一张工作簿。"""
    from frappe.utils.xlsxutils import make_xlsx

    data = board()
    wanted = set(contracts.distinct(names, 5000)) if names else None
    list_order = {row.name: (index, row.title) for index, row in enumerate(data["lists"])}
    people = {row["user"]: row["name"] for row in data["members"]}
    tags = {row.name: row.title or _(f"cowork.work.color.{row.color}") for row in data["labels"]}
    custom = data["fields"]
    header = [_("cowork.work.field.title"), _("cowork.work.field.list"), _("cowork.work.field.completed"),
              _("cowork.work.field.start"), _("cowork.work.field.due"), _("cowork.work.field.dueTime"),
              _("cowork.work.field.members"), _("cowork.work.field.labels"), _("cowork.work.field.checklist"),
              *[field.title for field in custom]]
    rows = [header]
    cards = [card for card in data["cards"] if not card["is_template"] and (wanted is None or card["name"] in wanted)]
    cards.sort(key=lambda card: (list_order.get(card["board_list"], (999, ""))[0], card["position"]))
    for card in cards:
        rows.append([
            card["title"], list_order.get(card["board_list"], (0, ""))[1], _("Yes") if card["completed"] else "",
            str(card["start_date"] or ""), str(card["due_date"] or ""), (card["due_time"] or "")[:5],
            ", ".join(people.get(user, user) for user in card["assignees"]),
            ", ".join(tags.get(label, "") for label in card["labels"]),
            f"{card['checklist_done']}/{card['checklist_total']}" if card["checklist_total"] else "",
            *[_field_cell(field, card["values"].get(field.name)) for field in custom],
        ])
    return make_xlsx(rows, _("cowork.nav.work")[:31]).getvalue()


def _field_cell(field, value):
    if value in (None, ""):
        return ""
    if field.fieldtype == "checkbox":
        return _("Yes")
    return value
