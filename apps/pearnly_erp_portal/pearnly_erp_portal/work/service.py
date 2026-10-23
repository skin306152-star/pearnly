"""任务管理看板的服务端实现（照 Trello）。

多块看板（用户 2026-09-29）：看板本身、成员与设置在 `boards`；这里是看板里的列表与卡片。
- 列表：增删（归档 / 看板管理员彻底删除）、改名、排序、复制、颜色、「完成列表」（拖进去就算完成）。
- 卡片：增改移（可移到别的看板）、完成（重复到期的卡完成后滚到下一次到期）、复制、模板、镜像、归档；开始日、到期日与时间、
  提醒时间；负责人、标签、多个清单、自定义字段、封面；附件（文件与链接）、评论（@）、关注。
每个入口先找到对象所在的看板，再按 `access` 的权利核对（看 / 改 / 评论 / 管理）；写入走 `documents.persist`，
原生接口写不进来。改完用原生 `publish_realtime` 通知能看这块板的人刷新（只带看板号与卡片号）。
"""
import json
from datetime import datetime, timezone

import frappe
from frappe import _
from frappe.utils import add_days, convert_utc_to_system_timezone, getdate, strip_html, today

from pearnly_erp_portal.work import access, boards, contracts, notify
from pearnly_erp_portal.work.contracts import Invalid
from pearnly_erp_portal.work.documents import persist, remove

CARD, LABEL, ACTIVITY = access.CARD, access.LABEL, access.ACTIVITY
COLUMN, FIELD, MIRROR, RULE = access.COLUMN, access.FIELD, access.MIRROR, access.RULE
SUMMARY_FIELDS = ["name", "title", "board", "board_list", "completed", "position", "start_date", "due_date",
                  "due_time", "reminder", "recurrence", "is_template", "description", "cover_color", "cover_file",
                  "modified", "creation"]
LIST_FIELDS = ["name", "title", "board", "archived", "is_done_list", "position"]
IMAGE_TYPES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _changed(board, card=None, action=None):
    notify.changed(board, card, action)


def _log(card, action, detail=None, content=None):
    return persist(frappe.get_doc({
        "doctype": ACTIVITY, "card": card, "actor": frappe.session.user,
        "action": action, "detail": json.dumps(detail, ensure_ascii=False) if detail else None,
        "content": content,
    }))


def _lock(doctype):
    # 同类对象的并发新建 / 重排在 DocType 元数据行上排队。
    frappe.db.get_value("DocType", doctype, "name", for_update=True)


def ensure_on_migrate():
    """装好 / 升级后建好默认看板和默认三列（hooks `after_install` / `after_migrate` 登记的入口）。"""
    boards.ensure_on_migrate()


# ── 取对象并核对权利 ──────────────────────────────────────────────────────────


def _list_for(name, right, *, archived=False, lock=False):
    """按列表号取列表（带所在看板），核对当前用户在那块板上有 `right`。"""
    access.require()
    row = frappe.db.get_value(COLUMN, {"name": name or ""}, LIST_FIELDS, as_dict=True, for_update=lock)
    if not row:
        raise Invalid("erp.work.error.list")
    access.require_right(row.board, right)
    if archived is not None and bool(row.archived) != archived:
        raise Invalid("erp.work.error.list")
    return row


def _list(name, board, *, archived=False, lock=False):
    """同一块看板里的列表（调用方已经核对过这块板的权利）。"""
    row = frappe.db.get_value(COLUMN, {"name": name or "", "board": board}, LIST_FIELDS, as_dict=True,
                              for_update=lock)
    if not row or (archived is not None and bool(row.archived) != archived):
        raise Invalid("erp.work.error.list")
    return row


def _card(name, right, *, lock=False, archived=False):
    """按卡片号取卡片，核对当前用户在卡片所在看板上有 `right`。"""
    access.require()
    row = frappe.db.get_value(CARD, {"name": name or ""}, ["name", "archived", "board"], as_dict=True,
                              for_update=lock)
    if not row:
        raise Invalid("erp.work.error.not_found")
    access.require_right(row.board, right)
    if archived is not None and bool(row.archived) != archived:
        raise Invalid("erp.work.error.not_found")
    return frappe.get_doc(CARD, row.name)


# ── 列表 ──────────────────────────────────────────────────────────────────────


def lists(board, archived=False):
    return frappe.get_all(COLUMN, filters={"board": board, "archived": 1 if archived else 0},
                          fields=["name", "title", "color", "is_done_list", "position", "archived_at"],
                          order_by="position asc, creation asc", limit_page_length=0)


def _list_tail(board):
    rows = frappe.get_all(COLUMN, filters={"board": board, "archived": 0}, fields=["position"],
                          order_by="position desc", limit_page_length=1)
    return rows[0].position if rows else None


def create_list(title, board=None):
    row = boards.open_board(board, "edit")
    _lock(COLUMN)
    if frappe.db.count(COLUMN, {"board": row.name, "archived": 0}) >= contracts.LISTS_MAX:
        raise Invalid("erp.work.error.too_many")
    doc = persist(frappe.get_doc({"doctype": COLUMN, "board": row.name,
                                  "title": contracts.text(title, contracts.LIST_TITLE_MAX, required=True),
                                  "position": contracts.position_between(_list_tail(row.name), None)}))
    _changed(row.name, None, "list")
    return {"name": doc.name}


def update_list(name, values):
    """改名、颜色、是否完成列表。"""
    if not isinstance(values, dict):
        raise Invalid("erp.work.error.request")
    row = _list_for(name, "edit", lock=True)
    doc = frappe.get_doc(COLUMN, row.name)
    if "title" in values:
        doc.title = contracts.text(values["title"], contracts.LIST_TITLE_MAX, required=True)
    if "color" in values:
        doc.color = contracts.color(values["color"]) if values["color"] else ""
    if "is_done_list" in values:
        doc.is_done_list = 1 if values["is_done_list"] in (1, True, "1") else 0
    persist(doc)
    _changed(row.board, None, "list")
    return {"name": doc.name, "title": doc.title, "color": doc.color or "", "is_done_list": doc.is_done_list}


def move_list(name, before=None, after=None):
    """拖列表 / 列表菜单「移动列表」：落在同一块板上 `before` 与 `after` 两个列表之间。"""
    row = _list_for(name, "edit")
    board = row.board
    _lock(COLUMN)

    def neighbour(other):
        if not other or other == name:
            return None
        return frappe.db.get_value(COLUMN, {"name": other, "board": board, "archived": 0}, "position")

    position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        rows = frappe.get_all(COLUMN, filters={"board": board, "archived": 0, "name": ["!=", name]},
                              pluck="name", order_by="position asc, creation asc", limit_page_length=0)
        for other, value in zip(rows, contracts.renumber(len(rows))):
            frappe.db.set_value(COLUMN, other, "position", value, update_modified=False)
        position = (contracts.position_between(neighbour(before), neighbour(after))
                    or contracts.position_between(_list_tail(board), None))
    frappe.db.set_value(COLUMN, name, "position", position, update_modified=False)
    _changed(board, None, "list")
    return {"name": name, "position": position}


def copy_list(name, title):
    """复制列表：新列表放在原列表后面，带上里面所有未归档的卡（清单、标签、成员、字段、描述、日期）。"""
    source = _list_for(name, "edit")
    board = source.board
    _lock(COLUMN)
    following = frappe.get_all(COLUMN, filters={"board": board, "archived": 0, "position": [">", source.position]},
                               fields=["position"], order_by="position asc", limit_page_length=1)
    position = (contracts.position_between(source.position, following[0].position if following else None)
                or contracts.position_between(_list_tail(board), None))
    target = persist(frappe.get_doc({"doctype": COLUMN, "board": board,
                                     "title": contracts.text(title, contracts.LIST_TITLE_MAX, required=True),
                                     "color": frappe.db.get_value(COLUMN, name, "color"), "position": position}))
    for card_name in frappe.get_all(CARD, filters={"board_list": source.name, "archived": 0},
                                    pluck="name", order_by="position asc, creation asc", limit_page_length=0):
        _clone(frappe.get_doc(CARD, card_name), target, None, {"checklist", "labels", "members", "fields"})
    _changed(board, None, "list")
    return {"name": target.name}


def archive_list(name):
    row = _list_for(name, "edit", lock=True)
    if frappe.db.count(COLUMN, {"board": row.board, "archived": 0}) <= 1:
        raise Invalid("erp.work.error.last_list")
    doc = frappe.get_doc(COLUMN, name)
    doc.archived, doc.archived_at = 1, _now()
    persist(doc)
    _changed(row.board, None, "list")
    return {"name": name}


def restore_list(name):
    row = _list_for(name, "edit", archived=True, lock=True)
    doc = frappe.get_doc(COLUMN, name)
    doc.archived, doc.archived_at = 0, None
    doc.position = contracts.position_between(_list_tail(row.board), None)
    persist(doc)
    _changed(row.board, None, "list")
    return {"name": name}


def delete_list(name):
    """彻底删除列表和里面的卡：只给能管这块板的人（看板管理员 / ERP 管理员），且只删已归档的列表。"""
    row = _list_for(name, "manage", archived=None, lock=True)
    if not row.archived:
        raise Invalid("erp.work.error.list")
    for card_name in frappe.get_all(CARD, filters={"board_list": name}, pluck="name", limit_page_length=0):
        _purge(frappe.get_doc(CARD, card_name))
    for doctype in (MIRROR, RULE):
        for other in frappe.get_all(doctype, filters={"board_list": name}, pluck="name", limit_page_length=0):
            remove(frappe.get_doc(doctype, other))
    remove(frappe.get_doc(COLUMN, name))
    _changed(row.board, None, "list")
    return {"name": name}


SORTS = {"created_desc": ("creation", True), "created_asc": ("creation", False),
         "due": ("due_date", False), "title": ("title", False)}


def sort_list(name, key):
    """列表菜单「排序依据」：按选的规则把这一列重新排好（写回位置，所有人看到的一样）。"""
    row = _list_for(name, "edit")
    if key not in SORTS:
        raise Invalid("erp.work.error.request")
    field, reverse = SORTS[key]
    _lock(CARD)
    rows = frappe.get_all(CARD, filters={"board_list": name, "archived": 0},
                          fields=["name", "title", "due_date", "creation"], limit_page_length=0)
    if field == "due_date":
        rows.sort(key=lambda item: (item.due_date is None, item.due_date or getdate("1900-01-01")))
    elif field == "title":
        rows.sort(key=lambda item: (item.title or "").casefold())
    else:
        rows.sort(key=lambda item: item.creation, reverse=reverse)
    for item, value in zip(rows, contracts.renumber(len(rows))):
        frappe.db.set_value(CARD, item.name, "position", value, update_modified=False)
    _changed(row.board, None, "sort")
    return {"count": len(rows)}


def move_all(name, target):
    """列表菜单「移动此列表中的所有卡片」：按原顺序接到同一块板上目标列表的列尾。"""
    if name == target:
        raise Invalid("erp.work.error.request")
    row = _list_for(name, "edit")
    target_row = _list(target, row.board)
    _lock(CARD)
    names = frappe.get_all(CARD, filters={"board_list": name, "archived": 0},
                           pluck="name", order_by="position asc, creation asc", limit_page_length=0)
    for card_name in names:
        doc = frappe.get_doc(CARD, card_name)
        _place(doc, target_row)
        persist(doc)
    _changed(row.board, None, "move")
    return {"moved": len(names)}


def archive_all(name):
    """列表菜单「将此列表中的所有卡片归档」。"""
    row = _list_for(name, "edit")
    names = frappe.get_all(CARD, filters={"board_list": name, "archived": 0}, pluck="name", limit_page_length=0)
    for card_name in names:
        doc = _card(card_name, "edit", lock=True, archived=False)
        doc.archived, doc.archived_at = 1, _now()
        persist(doc)
        _log(doc.name, "archive")
    _changed(row.board, None, "archive")
    return {"archived": len(names)}


# ── 读 ────────────────────────────────────────────────────────────────────────


def members(board):
    """能被分派、被 @ 的人 = 能看这块板的人（启用的账号）。"""
    return boards.assignable(board)


def labels(board):
    return frappe.get_all(LABEL, filters={"board": board}, fields=["name", "title", "color"],
                          order_by="creation asc")


def fields(board):
    rows = frappe.get_all(FIELD, filters={"board": board},
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
    assignees = _children("ERP Work Card Assignee", names, ["user"])
    tags = _children("ERP Work Card Label", names, ["label"])
    checklist = _children("ERP Work Checklist Item", names, ["title", "done"])
    watchers = _children("ERP Work Card Watcher", names, ["user"])
    values = _children("ERP Work Card Value", names, ["field", "value"])
    covers = dict(frappe.get_all("File", filters={"name": ["in", [row.cover_file for row in rows if row.cover_file] or [""]]},
                                 fields=["name", "file_url"], as_list=True))
    comments = _counts(ACTIVITY, "card", names, {"action": "comment"})
    files = _counts("File", "attached_to_name", names, {"attached_to_doctype": CARD})
    result = []
    for row in rows:
        items = [item for item in checklist.get(row.name, []) if item.title]
        result.append({
            "name": row.name, "title": row.title, "board": row.board, "board_list": row.board_list,
            "completed": row.completed,
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


def board(board=None, fallback=False):
    """整块看板的数据。`fallback`：页面记着的看板已经没了 / 看不了 / 归档了时退回默认看板（不报错）。"""
    if board and fallback:
        access.require()
        info = access.board_info(board)
        if not info or info.archived or not access.can(board, "view"):
            board = None
    row = boards.open_board(board, "view")
    open_lists = lists(row.name)
    names = [item.name for item in open_lists]
    rows = frappe.get_all(CARD, filters={"board": row.name, "archived": 0, "board_list": ["in", names or [""]]},
                          fields=SUMMARY_FIELDS, order_by="position asc, creation asc", limit_page_length=0)
    visible = {item.name for item in rows}
    mirrors = frappe.get_all(MIRROR, filters={"board": row.name, "board_list": ["in", names or [""]]},
                             fields=["name", "card", "board_list", "position"],
                             order_by="position asc, creation asc", limit_page_length=0)
    # 别的看板上的卡镜像到这里：只给同时能看原卡所在看板的人看。
    foreign = [item for item in frappe.get_all(
        CARD, filters={"name": ["in", [item.card for item in mirrors if item.card not in visible] or [""]],
                       "board": ["!=", row.name], "archived": 0}, fields=SUMMARY_FIELDS, limit_page_length=0)
               if access.can(item.board, "view")]
    shown = visible | {item.name for item in foreign}
    return {"board": boards.info(row.name), "boards": boards.boards(),
            "lists": open_lists, "cards": _summaries(rows), "foreign": _summaries(foreign),
            "mirrors": [item for item in mirrors if item.card in shown], "members": members(row.name),
            "labels": labels(row.name), "fields": fields(row.name), "me": frappe.session.user,
            "admin": access.administrator(), "unread": notify.unread()}


board_data = board  # `export` 的参数也叫 board


def summary(name):
    doc = _card(name, "view", archived=None)
    return _summaries(frappe.get_all(CARD, filters={"name": doc.name}, fields=SUMMARY_FIELDS))[0]


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
    doc = _card(name, "view", archived=None)
    return {
        "name": doc.name, "title": doc.title, "board": doc.board, "board_list": doc.board_list,
        "completed": doc.completed,
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
        "mirrors": [row for row in frappe.get_all(MIRROR, filters={"card": doc.name},
                                                  fields=["name", "board", "board_list"])
                    if access.can(row.board, "view")],
        "attachments": _attachments(doc.name), "activity": _activity(doc.name),
        "rights": boards.info(doc.board)["rights"],
    }


def archived(board=None):
    row = boards.open_board(board, "view")
    titles = {item.name: item.title for item in frappe.get_all(COLUMN, filters={"board": row.name},
                                                                fields=["name", "title"], limit_page_length=0)}
    cards = frappe.get_all(CARD, filters={"board": row.name, "archived": 1},
                           fields=["name", "title", "board_list", "archived_at"], order_by="archived_at desc",
                           limit_page_length=0)
    archived_lists = lists(row.name, archived=True)
    closed = boards.boards(archived=True)
    # 库里存 UTC；页面按站点时区的裸时间显示（同 `p.datetime_text` 的口径）。
    for item in [*cards, *archived_lists, *closed]:
        if item["archived_at"]:
            item["archived_at"] = convert_utc_to_system_timezone(item["archived_at"]).replace(tzinfo=None)
    for item in cards:
        item.list_title = titles.get(item.board_list, "")
    return {"cards": cards, "lists": archived_lists, "boards": closed}


# ── 卡片 ──────────────────────────────────────────────────────────────────────


def _column_tail(board_list, exclude=None):
    filters = {"board_list": board_list, "archived": 0}
    if exclude:
        filters["name"] = ["!=", exclude]
    rows = frappe.get_all(CARD, filters=filters, fields=["position"], order_by="position desc", limit_page_length=1)
    return rows[0].position if rows else None


def _complete(doc, done):
    """标记完成 / 未完成。重复到期的卡"完成"时不算完成：到期日滚到下一次（照 Trello），返回 True。"""
    if done and (doc.recurrence or "never") != "never" and doc.due_date:
        due = contracts.next_due(getdate(doc.due_date), doc.recurrence)
        doc.due_date, doc.completed, doc.completed_at = str(due), 0, None
        _log(doc.name, "repeat", {"due": str(due)})
        return True
    if bool(doc.completed) == bool(done):
        return False
    doc.completed = 1 if done else 0
    doc.completed_at = _now() if done else None
    _log(doc.name, "complete" if done else "uncomplete")
    if done:
        notify.send(notify.audience(doc), doc, "complete")
    return False


def _rehome(doc, board):
    """卡片换到另一块看板：标签、字段值只留目标看板有的；负责人只留能改目标看板的人，关注人只留能看的人。"""
    doc.board = board
    known_labels = {row.name for row in labels(board)}
    known_fields = {row.name for row in fields(board)}
    viewers, editors = access.viewers(board), access.editors(board)
    doc.set("labels", [{"label": row.label} for row in doc.labels if row.label in known_labels])
    doc.set("values", [{"field": row.field, "value": row.value} for row in doc.get("values") or []
                       if row.field in known_fields])
    doc.set("assignees", [{"user": row.user} for row in doc.assignees if row.user in editors])
    doc.set("watchers", [{"user": row.user} for row in doc.watchers if row.user in viewers])


def _place(doc, target, position=None):
    """换列表（可跨看板）：记活动、提醒负责人和关注的人；进「完成列表」算完成、从完成列表拿出来算未完成。
    重复到期的卡拖进完成列表：到期日滚到下一次，卡留在原列表。"""
    if target.name != doc.board_list:
        source = frappe.db.get_value(COLUMN, doc.board_list, ["name", "title", "is_done_list"], as_dict=True)
        if (target.board == doc.board and target.is_done_list and not doc.completed
                and (doc.recurrence or "never") != "never" and doc.due_date):
            _complete(doc, True)
            return
        _log(doc.name, "move", {"from": source.title if source else "", "to": target.title})
        if target.board != doc.board:
            _rehome(doc, target.board)
        notify.send(notify.audience(doc), doc, "move", {"from": source.title if source else "", "to": target.title})
        doc.board_list = target.name
        if target.is_done_list:
            _complete(doc, True)
        elif source and source.is_done_list and doc.completed:
            _complete(doc, False)
        _apply_rules(doc, target.name)
        if position is None:
            position = contracts.position_between(_column_tail(target.name, doc.name), None)
    if position is not None:
        doc.position = position


def create(title, board_list, template=None):
    """列尾快速新建；带 `template` 就照同一块板上那张模板卡建（清单、标签、成员、字段、描述、日期、封面颜色）。"""
    target = _list_for(board_list, "edit")
    title = contracts.text(title, contracts.TITLE_MAX, required=True)
    _lock(CARD)
    if template:
        source = _card(template, "view", archived=None)
        if not source.is_template or source.board != target.board:
            raise Invalid("erp.work.error.not_found")
        doc = _clone(source, target, title, {"checklist", "labels", "members", "fields"}, action="create")
    else:
        doc = persist(frappe.get_doc({"doctype": CARD, "board": target.board,
                                      "title": title, "board_list": target.name,
                                      "reminder": contracts.DEFAULT_REMINDER,
                                      "recurrence": "never", "completed": 1 if target.is_done_list else 0,
                                      "completed_at": _now() if target.is_done_list else None,
                                      "position": contracts.position_between(_column_tail(target.name), None)}))
        _log(doc.name, "create", {"list": target.title})
        if _apply_rules(doc, target.name):
            persist(doc)
    _changed(target.board, doc.name, "create")
    return summary(doc.name)


def _clone(source, target, title, keep, action="copy"):
    """照 `source` 在列表 `target`（可以在别的看板）建一张新卡。"""
    doc = frappe.get_doc({"doctype": CARD, "board": target.board,
                          "title": title or source.title,
                          "board_list": target.name, "description": source.description,
                          "start_date": source.start_date, "due_date": source.due_date, "due_time": source.due_time,
                          "reminder": source.reminder or contracts.DEFAULT_REMINDER,
                          "recurrence": source.recurrence or "never", "cover_color": source.cover_color,
                          "position": contracts.position_between(_column_tail(target.name), None)})
    if target.is_done_list:
        doc.completed, doc.completed_at = 1, _now()
    if "checklist" in keep:
        doc.set("checklist", [{"checklist": row.checklist, "group_id": row.group_id, "title": row.title,
                               "done": 0 if action == "create" else row.done} for row in source.checklist])
    if "labels" in keep:
        known = {row.name for row in labels(target.board)}
        doc.set("labels", [{"label": row.label} for row in source.labels if row.label in known])
    if "members" in keep:
        allowed = access.editors(target.board)
        doc.set("assignees", [{"user": row.user} for row in source.assignees if row.user in allowed])
    if "fields" in keep:
        known = {row.name for row in fields(target.board)}
        doc.set("values", [{"field": row.field, "value": row.value} for row in source.get("values") or []
                           if row.field in known])
    persist(doc)
    _log(doc.name, action, {"from": source.title})
    if doc.assignees:
        notify.send([row.user for row in doc.assignees], doc, "assign")
    if _apply_rules(doc, target.name):
        persist(doc)
    return doc


def update(name, modified, values):
    """保存详情弹窗 / 快捷菜单里改过的字段。`modified` 是打开时的版本，别人先保存过就拒绝（请重新打开）。"""
    if not isinstance(values, dict):
        raise Invalid("erp.work.error.request")
    doc = _card(name, "edit", lock=True, archived=False)
    if str(doc.modified) != str(modified or ""):
        raise Invalid("erp.work.error.conflict")
    if "title" in values:
        doc.title = contracts.text(values["title"], contracts.TITLE_MAX, required=True)
    if "description" in values:
        description = str(values["description"] or "")
        if len(description) > contracts.DESCRIPTION_MAX:
            raise Invalid("erp.work.error.too_long")
        doc.description = description if strip_html(description).strip() else ""
    for key in ("start_date", "due_date"):
        if key in values:
            try:
                value = str(getdate(values[key])) if values[key] else None
            except Exception:
                raise Invalid("erp.work.error.date")
            if key == "due_date" and str(doc.due_date or "") != str(value or ""):
                _log(doc.name, "due", {"due": value})
            doc.set(key, value)
    if "due_time" in values:
        value = values["due_time"] or None
        if value:
            try:
                contracts.due_at("2000-01-01", value)
            except (ValueError, IndexError):
                raise Invalid("erp.work.error.date")
        doc.due_time = value
    if "reminder" in values:
        doc.reminder = contracts.choice(values["reminder"], contracts.REMINDERS)
    if "recurrence" in values:
        doc.recurrence = contracts.choice(values["recurrence"], contracts.REPEATS)
    if doc.start_date and doc.due_date and getdate(doc.start_date) > getdate(doc.due_date):
        raise Invalid("erp.work.error.start_after_due")
    if "assignees" in values:
        before = {row.user for row in doc.assignees}
        # 新加的人必须是现在能改这块板的人；已经在卡上的人（后来被停用 / 去掉权限）可以留着。
        allowed = access.editors(doc.board) | before
        users = contracts.distinct(values["assignees"], len(allowed) or 1)
        if set(users) - allowed:
            raise Invalid("erp.work.error.member")
        added = [user for user in users if user not in before]
        doc.set("assignees", [{"user": user} for user in users])
        if added:
            _log(doc.name, "assign", {"users": added})
            notify.send(added, doc, "assign")
    if "labels" in values:
        chosen = contracts.distinct(values["labels"], contracts.LABELS_MAX)
        known = {row.name for row in labels(doc.board)}
        if set(chosen) - known:
            raise Invalid("erp.work.error.label")
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
        doc.set("values", _field_values(doc.board, values["values"]))
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
        _complete(doc, values["completed"] in (1, True, "1"))
    source_board = doc.board
    if "board_list" in values and values["board_list"] != doc.board_list:
        _place(doc, _list_for(values["board_list"], "edit"))
    persist(doc)
    _changed(source_board, doc.name, "update")
    if doc.board != source_board:
        _changed(doc.board, doc.name, "update")
    return card(doc.name)


def _field_values(board, raw):
    if not isinstance(raw, dict):
        raise Invalid("erp.work.error.request")
    known = {row.name: row for row in fields(board)}
    rows = []
    for field, value in raw.items():
        if field not in known:
            raise Invalid("erp.work.error.field_value")
        cleaned = contracts.field_value(known[field].fieldtype, known[field].options, value)
        if cleaned != "":
            rows.append({"field": field, "value": cleaned})
    return rows


def move(name, board_list, before=None, after=None):
    """拖到 `board_list`（可以在别的看板，两边都要能改）、落在 `before` 与 `after` 两张卡之间。"""
    target = _list_for(board_list, "edit")
    doc = _card(name, "edit", lock=True, archived=False)
    source_board = doc.board

    def neighbour(other):
        if not other or other == doc.name:
            return None
        return frappe.db.get_value(CARD, {"name": other, "board_list": target.name, "archived": 0}, "position")

    position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        # 两卡之间挤不下：整列按当前顺序重排，再算一次。
        column = frappe.get_all(CARD, filters={"board_list": target.name, "archived": 0, "name": ["!=", doc.name]},
                                pluck="name", order_by="position asc, creation asc", limit_page_length=0)
        for row, value in zip(column, contracts.renumber(len(column))):
            frappe.db.set_value(CARD, row, "position", value, update_modified=False)
        position = contracts.position_between(neighbour(before), neighbour(after))
    if position is None:
        position = contracts.position_between(_column_tail(target.name, doc.name), None)
    _place(doc, target, position)
    persist(doc)
    _changed(source_board, doc.name, "move")
    if target.board != source_board:
        _changed(target.board, doc.name, "move")
    return summary(doc.name)


def toggle_done(name):
    """卡片上的完成圆圈（照 Trello）：只改完成与否，不挪列表；重复到期的卡完成后滚到下一次到期。"""
    doc = _card(name, "edit", lock=True, archived=False)
    _complete(doc, not doc.completed)
    persist(doc)
    _changed(doc.board, doc.name, "update")
    return summary(doc.name)


def copy(name, title, board_list, keep=None):
    """复制卡片：新标题、目标列表（可以在别的看板：源卡能看、目标能改）；可选带上清单 / 标签 / 成员 / 字段。"""
    source = _card(name, "view", archived=None)
    target = _list_for(board_list, "edit")
    title = contracts.text(title, contracts.TITLE_MAX, required=True)
    keep = set(contracts.distinct(keep, 4)) & {"checklist", "labels", "members", "fields"}
    _lock(CARD)
    doc = _clone(source, target, title, keep)
    _changed(target.board, doc.name, "create")
    return summary(doc.name)


def mirror(name, board_list):
    """卡片镜像：这张卡也显示在另一个列表里（同一张卡，改哪边都是改它）。目标可以在别的看板：
    源卡能看、目标能改；看不到源卡看板的人在目标看板上也看不到这条镜像。"""
    doc = _card(name, "view", archived=False)
    target = _list_for(board_list, "edit")
    if target.name == doc.board_list or frappe.db.exists(MIRROR, {"card": doc.name, "board_list": target.name}):
        raise Invalid("erp.work.error.mirror")
    _lock(MIRROR)
    tail = frappe.get_all(MIRROR, filters={"board_list": target.name}, fields=["position"], order_by="position desc",
                          limit_page_length=1)
    tails = [value for value in (tail[0].position if tail else None, _column_tail(target.name))
             if value is not None]
    row = persist(frappe.get_doc({"doctype": MIRROR, "board": target.board,
                                  "card": doc.name, "board_list": target.name,
                                  "position": contracts.position_between(max(tails) if tails else None, None)}))
    if access.can(doc.board, "edit"):
        _log(doc.name, "mirror", {"to": target.title})
    _changed(target.board, doc.name, "mirror")
    return {"name": row.name}


def _mirror(name):
    access.require()
    row = frappe.db.get_value(MIRROR, {"name": name or ""}, ["name", "card", "board"], as_dict=True)
    if not row:
        raise Invalid("erp.work.error.not_found")
    access.require_right(row.board, "edit")
    return row


def move_mirror(name, board_list, before=None, after=None):
    """拖镜像：只在镜像所在的看板里挪。"""
    row = _mirror(name)
    target = _list(board_list, row.board)
    if target.name == frappe.db.get_value(CARD, row.card, "board_list"):
        raise Invalid("erp.work.error.mirror")

    def neighbour(other):
        if not other:
            return None
        return (frappe.db.get_value(CARD, {"name": other, "board_list": target.name}, "position")
                or frappe.db.get_value(MIRROR, {"name": other, "board_list": target.name}, "position"))

    doc = frappe.get_doc(MIRROR, row.name)
    doc.board_list = target.name
    doc.position = (contracts.position_between(neighbour(before), neighbour(after))
                    or contracts.position_between(_column_tail(target.name), None))
    persist(doc)
    _changed(row.board, row.card, "mirror")
    return {"name": doc.name}


def unmirror(name):
    row = _mirror(name)
    remove(frappe.get_doc(MIRROR, row.name))
    _changed(row.board, row.card, "mirror")
    return {"name": name}


def archive(name):
    doc = _card(name, "edit", lock=True, archived=False)
    doc.archived, doc.archived_at = 1, _now()
    persist(doc)
    _log(doc.name, "archive")
    _changed(doc.board, doc.name, "archive")
    return {"name": doc.name}


def restore(name):
    doc = _card(name, "edit", lock=True, archived=True)
    target = frappe.db.get_value(COLUMN, doc.board_list, ["name", "archived"], as_dict=True)
    if not target or target.archived:
        # 原列表已归档 / 删掉：放回这块板的第一个列表。
        first = lists(doc.board)
        if not first:
            raise Invalid("erp.work.error.list")
        doc.board_list = first[0].name
    doc.archived, doc.archived_at = 0, None
    doc.position = contracts.position_between(_column_tail(doc.board_list, doc.name), None)
    persist(doc)
    _log(doc.name, "restore")
    _changed(doc.board, doc.name, "restore")
    return summary(doc.name)


def _purge(doc):
    for file in _attachments(doc.name):
        frappe.delete_doc("File", file.name, ignore_permissions=True, force=True)
    for doctype in (ACTIVITY, access.NOTIFICATION, MIRROR):
        for row in frappe.get_all(doctype, filters={"card": doc.name}, pluck="name", limit_page_length=0):
            remove(frappe.get_doc(doctype, row))
    remove(doc)


def delete(name):
    """彻底删除：只给能管这块板的人（看板管理员 / ERP 管理员），且只删已归档的卡（先归档、再删，与 Trello 同）。"""
    doc = _card(name, "manage", lock=True, archived=True)
    board = doc.board
    _purge(doc)
    _changed(board, name, "delete")
    return {"name": name}


def watch(name, on):
    """关注 / 取消关注（只改自己；能看就能关注，观察者也可以）。"""
    doc = _card(name, "view", lock=True, archived=False)
    others = [row.user for row in doc.watchers if row.user != frappe.session.user]
    doc.set("watchers", [{"user": user} for user in others + ([frappe.session.user] if on else [])])
    persist(doc)
    _changed(doc.board, doc.name, "watch")
    return summary(doc.name)


def _cover_file(card_name, file):
    row = frappe.db.get_value("File", {"name": file, "attached_to_doctype": CARD, "attached_to_name": card_name},
                              ["name", "file_name", "file_url"], as_dict=True)
    if (not row or (row.file_url or "").startswith(("http://", "https://"))
            or not (row.file_name or "").lower().endswith(IMAGE_TYPES)):
        raise Invalid("erp.work.error.cover")
    return row.name


# ── 评论与附件 ────────────────────────────────────────────────────────────────


def comment(name, content, mentions=None):
    """发评论（按看板的「谁能评论」设置）；`mentions` 是评论里 @ 到的人（页面在选人时收集），
    各发一条"@了你"，负责人和关注的人再各发一条"有新评论"（已经被 @ 的不重复）。"""
    doc = _card(name, "comment", archived=False)
    content = contracts.comment(content)
    mentioned = set(contracts.distinct(mentions, 50)) & access.viewers(doc.board)
    _log(doc.name, "comment", {"mentions": sorted(mentioned)} if mentioned else None, content=content)
    excerpt = content[:120]
    notify.send(mentioned, doc, "mention", {"text": excerpt})
    notify.send(notify.audience(doc) - mentioned, doc, "comment", {"text": excerpt})
    _changed(doc.board, doc.name, "comment")
    return _activity(doc.name)


def delete_comment(activity):
    """删自己写的评论；看板管理员（和 ERP 管理员）也能删别人的（照 Trello，管不当评论）。"""
    access.require()
    row = frappe.db.get_value(ACTIVITY, {"name": activity or "",
                                         "action": "comment"}, ["name", "card", "actor"], as_dict=True)
    if not row:
        raise Invalid("erp.work.error.not_found")
    doc = _card(row.card, "view", archived=False)
    if row.actor != frappe.session.user and not access.can(doc.board, "manage"):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    remove(frappe.get_doc(ACTIVITY, row.name))
    _changed(doc.board, row.card, "comment")
    return _activity(row.card)


def attach(name, upload):
    """`upload` 是请求里的一个文件（`werkzeug` FileStorage）。存成挂在卡片上的原生私有 File。"""
    doc = _card(name, "edit", archived=False)
    if upload is None or not upload.filename:
        raise Invalid("erp.work.error.file")
    content = upload.stream.read(contracts.ATTACHMENT_MAX_BYTES + 1)
    if not content:
        raise Invalid("erp.work.error.file")
    if len(content) > contracts.ATTACHMENT_MAX_BYTES:
        raise Invalid("erp.work.error.file_size")
    file = frappe.get_doc({"doctype": "File", "attached_to_doctype": CARD, "attached_to_name": doc.name,
                           "file_name": upload.filename, "is_private": 1, "content": content})
    file.insert(ignore_permissions=True)
    _log(doc.name, "attach", {"file": file.file_name})
    _changed(doc.board, doc.name, "attach")
    return _attachments(doc.name)


def attach_link(name, url, text=None):
    """附件里加一个链接（照 Trello「搜索或粘贴链接」+「显示文本」）：原生 File 只存地址。"""
    doc = _card(name, "edit", archived=False)
    url = contracts.link(url)
    label = contracts.text(text, contracts.TITLE_MAX) or url[:contracts.TITLE_MAX]
    file = frappe.get_doc({"doctype": "File", "attached_to_doctype": CARD, "attached_to_name": doc.name,
                           "file_name": label, "file_url": url, "is_private": 0})
    file.insert(ignore_permissions=True)
    _log(doc.name, "attach", {"file": label})
    _changed(doc.board, doc.name, "attach")
    return _attachments(doc.name)


def detach(name, file):
    doc = _card(name, "edit", archived=False)
    row = frappe.db.get_value("File", {"name": file or "", "attached_to_doctype": CARD,
                                       "attached_to_name": doc.name}, ["name", "file_name"], as_dict=True)
    if not row:
        raise Invalid("erp.work.error.not_found")
    if doc.cover_file == row.name:
        doc.cover_file = ""
        persist(doc)
    frappe.delete_doc("File", row.name, ignore_permissions=True)
    _log(doc.name, "detach", {"file": row.file_name})
    _changed(doc.board, doc.name, "attach")
    return _attachments(doc.name)


# ── 标签与自定义字段（每块看板一套，只有看板管理员能改） ──────────────────────


def save_labels(rows, board=None):
    """整板标签一次保存：带 name 的是改，不带的是新建，不在列表里的删掉（并从卡片上摘掉）。"""
    row = boards.open_board(board, "manage")
    if not isinstance(rows, list):
        raise Invalid("erp.work.error.request")
    if len(rows) > contracts.LABELS_MAX:
        raise Invalid("erp.work.error.too_many")
    _lock(LABEL)
    current = {item.name: item for item in labels(row.name)}
    kept = set()
    for item in rows:
        if not isinstance(item, dict):
            raise Invalid("erp.work.error.request")
        title = contracts.text(item.get("title"), contracts.LABEL_TITLE_MAX)
        color = contracts.color(item.get("color"))
        name = item.get("name")
        if name:
            if name not in current:
                raise Invalid("erp.work.error.label")
            kept.add(name)
            if (current[name].title or "", current[name].color) != (title, color):
                doc = frappe.get_doc(LABEL, name)
                doc.title, doc.color = title, color
                persist(doc)
        else:
            persist(frappe.get_doc({"doctype": LABEL, "board": row.name, "title": title,
                                    "color": color}))
    for name in set(current) - kept:
        frappe.db.delete("ERP Work Card Label", {"parenttype": CARD, "label": name})
        remove(frappe.get_doc(LABEL, name))
    _changed(row.name, None, "labels")
    return labels(row.name)


def save_fields(rows, board=None):
    """整板自定义字段一次保存（照标签）：名字、类型（文本 / 数字 / 日期 / 勾选 / 下拉）、下拉选项、是否显示在卡片正面。
    删掉的字段从所有卡片上摘掉它的值；已有字段不改类型（改类型就删掉重建）。"""
    row = boards.open_board(board, "manage")
    if not isinstance(rows, list):
        raise Invalid("erp.work.error.request")
    if len(rows) > contracts.FIELDS_MAX:
        raise Invalid("erp.work.error.too_many")
    _lock(FIELD)
    current = {item.name: item for item in fields(row.name)}
    kept = set()
    for index, item in enumerate(rows, start=1):
        if not isinstance(item, dict):
            raise Invalid("erp.work.error.request")
        title = contracts.text(item.get("title"), contracts.LABEL_TITLE_MAX, required=True,
                               key="erp.work.error.field_title")
        fieldtype = contracts.choice(item.get("fieldtype"), contracts.FIELD_TYPES)
        options = contracts.field_options(fieldtype, item.get("options"))
        values = {"title": title, "options": json.dumps(options, ensure_ascii=False) if options else None,
                  "show_on_card": 1 if item.get("show_on_card") in (1, True, "1") else 0,
                  "position": contracts.STEP * index}
        name = item.get("name")
        if name:
            if name not in current or current[name].fieldtype != fieldtype:
                raise Invalid("erp.work.error.field_value")
            kept.add(name)
            doc = frappe.get_doc(FIELD, name)
            doc.update(values)
            persist(doc)
            if fieldtype == "select":
                # 下拉少了的选项：卡片上用着它的值一起清掉。
                frappe.db.delete("ERP Work Card Value", {"parenttype": CARD, "field": name,
                                                            "value": ["not in", options]})
        else:
            persist(frappe.get_doc({"doctype": FIELD, "board": row.name,
                                    "fieldtype": fieldtype, **values}))
    for name in set(current) - kept:
        frappe.db.delete("ERP Work Card Value", {"parenttype": CARD, "field": name})
        remove(frappe.get_doc(FIELD, name))
    _changed(row.name, None, "fields")
    return fields(row.name)


# ── 提醒 ──────────────────────────────────────────────────────────────────────


def notifications():
    access.require()
    return notify.mine()


def read_notifications(names=None):
    access.require()
    notify.mark_read(contracts.distinct(names, 200) if names else None)
    return notify.mine()


# ── 自动化（照 Trello「卡片被添加到列表中时」；只给看板管理员） ────────────────


def rules(board=None):
    row = boards.open_board(board, "manage")
    return _rules(row.name)


def _rules(board):
    return frappe.get_all(RULE, filters={"board": board},
                          fields=["name", "board_list", "action", "user", "label", "days", "enabled"],
                          order_by="creation asc", limit_page_length=0)


def _apply_rules(doc, board_list):
    """卡片进入 `board_list`（新建、拖入、复制进来）时按规则自动做事；有改动返回 True（调用方再保存）。
    规则按所在看板生效，不看操作的人能不能改规则。规则只改负责人 / 标签 / 到期日 / 完成 / 关注，不挪卡片，
    所以不会互相触发成死循环。"""
    changed = False
    for rule in frappe.get_all(RULE, filters={"board_list": board_list, "enabled": 1},
                               fields=["action", "user", "label", "days"], order_by="creation asc",
                               limit_page_length=0):
        detail = {"action": rule.action}
        if rule.action == "assign":
            if (not rule.user or rule.user not in access.editors(doc.board)
                    or rule.user in {row.user for row in doc.assignees}):
                continue
            doc.append("assignees", {"user": rule.user})
            notify.send([rule.user], doc, "assign")
            detail["user"] = rule.user
        elif rule.action == "watch":
            if (not rule.user or rule.user not in access.viewers(doc.board)
                    or rule.user in {row.user for row in doc.watchers}):
                continue
            doc.append("watchers", {"user": rule.user})
            detail["user"] = rule.user
        elif rule.action == "label":
            title = frappe.db.get_value(LABEL, {"name": rule.label or "", "board": doc.board}, "title")
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
            _complete(doc, True)
        else:
            continue
        _log(doc.name, "rule", detail)
        changed = True
    return changed


def save_rules(rows, board=None):
    """整板规则一次保存（照标签）：带 name 的是改，不带的是新建，不在列表里的删掉。"""
    row = boards.open_board(board, "manage")
    if not isinstance(rows, list):
        raise Invalid("erp.work.error.request")
    if len(rows) > contracts.RULES_MAX:
        raise Invalid("erp.work.error.too_many")
    _lock(RULE)
    current = {item.name: item for item in _rules(row.name)}
    people, editors = access.viewers(row.name), access.editors(row.name)
    known_labels = {item.name for item in labels(row.name)}
    kept = set()
    for item in rows:
        if not isinstance(item, dict):
            raise Invalid("erp.work.error.request")
        _list(item.get("board_list"), row.name)
        action = contracts.choice(item.get("action"), contracts.RULE_ACTIONS)
        values = {"board_list": item["board_list"], "action": action, "user": None, "label": None, "days": 0,
                  "enabled": 0 if item.get("enabled") in (0, False, "0") else 1}
        if action in ("assign", "watch"):
            if item.get("user") not in (editors if action == "assign" else people):
                raise Invalid("erp.work.error.member")
            values["user"] = item["user"]
        elif action == "label":
            if item.get("label") not in known_labels:
                raise Invalid("erp.work.error.label")
            values["label"] = item["label"]
        elif action == "due_days":
            try:
                days = int(item.get("days"))
            except (TypeError, ValueError):
                raise Invalid("erp.work.error.rule_days")
            if not 0 <= days <= 365:
                raise Invalid("erp.work.error.rule_days")
            values["days"] = days
        name = item.get("name")
        if name:
            if name not in current:
                raise Invalid("erp.work.error.request")
            kept.add(name)
            doc = frappe.get_doc(RULE, name)
            doc.update(values)
            persist(doc)
        else:
            persist(frappe.get_doc({"doctype": RULE, "board": row.name, **values}))
    for name in set(current) - kept:
        remove(frappe.get_doc(RULE, name))
    _changed(row.name, None, "rules")
    return _rules(row.name)


# ── 全文搜索与导出 ────────────────────────────────────────────────────────────


def search(keyword, board=None):
    """筛选区的关键词：本看板卡片的标题、描述、评论、清单项、自定义字段值里有就算（不区分大小写）。"""
    row = boards.open_board(board, "view")
    keyword = contracts.text(keyword, 100)
    if len(keyword) < contracts.SEARCH_MIN:
        return []
    like = ["like", f"%{keyword}%"]
    filters = {"board": row.name, "archived": 0}
    names = set(frappe.get_all(CARD, filters=filters, or_filters={"title": like, "description": like},
                               pluck="name", limit_page_length=0))
    names |= set(frappe.get_all(ACTIVITY, filters={"action": "comment", "content": like},
                                pluck="card", limit_page_length=0))
    for doctype, field in (("ERP Work Checklist Item", "title"), ("ERP Work Card Value", "value")):
        names |= set(frappe.get_all(doctype, filters={"parenttype": CARD, field: like}, pluck="parent",
                                    limit_page_length=0))
    if not names:
        return []
    return frappe.get_all(CARD, filters={**filters, "name": ["in", list(names)]}, pluck="name", limit_page_length=0)


def export_size(names=None, board=None):
    """导出的卡片数（与 ``export`` 同一份取数、同一道看板权限）：``pearnly_core.exports`` 据此决定当场给还是排后台。"""
    data = board_data(board)
    wanted = set(contracts.distinct(names, 5000)) if names else None
    return sum(1 for card in data["cards"] if not card["is_template"] and (wanted is None or card["name"] in wanted))


def export(names=None, board=None):
    """表格视图「导出 Excel」：按页面上当前看得到的卡片（`names`，没给就是整板）出一张工作簿。"""
    from frappe.utils.xlsxutils import make_xlsx

    data = board_data(board)
    wanted = set(contracts.distinct(names, 5000)) if names else None
    list_order = {row.name: (index, row.title) for index, row in enumerate(data["lists"])}
    people = {row["user"]: row["name"] for row in data["members"]}
    tags = {row.name: row.title or _(f"erp.work.color.{row.color}") for row in data["labels"]}
    custom = data["fields"]
    header = [_("erp.work.field.title"), _("erp.work.field.list"), _("erp.work.field.completed"),
              _("erp.work.field.start"), _("erp.work.field.due"), _("erp.work.field.dueTime"),
              _("erp.work.field.members"), _("erp.work.field.labels"), _("erp.work.field.checklist"),
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
    return make_xlsx(rows, (data["board"]["title"] or _("erp.nav.work"))[:31]).getvalue()


def _field_cell(field, value):
    if value in (None, ""):
        return ""
    if field.fieldtype == "checkbox":
        return _("Yes")
    return value
