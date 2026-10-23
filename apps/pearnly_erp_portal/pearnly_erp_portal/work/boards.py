"""任务管理的多块看板（用户 2026-09-29，照 Trello）：默认看板、看板列表、新建、设置、成员、归档 / 恢复。

- 看板直接属于本站点（租户 = 站点）：Cowork 那份站点内部归属（`workspace`）在 ERP 不设，看板就是最上面一层。
- 默认看板（`is_default`）：团队可见、没有显式成员，装好 / 升级时建好；不能归档。
  名字不存（空），按各人的界面语言显示「团队看板」，有人改名后才是存下的名字（录制实测：泰语用户看到中文名）。
- 新建看板的人是该看板管理员；新看板带默认三列和六个颜色标签。
- 权限判据在 `access`，矩阵在 `contracts.board_rights`；这里每个入口先核对权利再写。
"""
from datetime import datetime, timezone

import frappe
from frappe import _

from pearnly_erp_portal.work import access, contracts, notify
from pearnly_erp_portal.work.contracts import Invalid
from pearnly_erp_portal.work.documents import persist

BOARD, COLUMN, LABEL = access.BOARD, access.COLUMN, access.LABEL
DEFAULT_TITLE = "erp.work.board.default"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _lock(doctype):
    # 同类对象的并发新建在 DocType 元数据行上排队。
    frappe.db.get_value("DocType", doctype, "name", for_update=True)


def default_board():
    """本站点的默认看板；没有就建一块（团队可见、没有显式成员）。

    正常由 `ensure_on_migrate` 建好；这里是兜底。打开看板是 GET 请求，框架会回滚 GET 里的写入，
    所以兜底建完要当场提交（同默认列表）。
    """
    rows = frappe.get_all(BOARD, filters={"is_default": 1}, pluck="name",
                          order_by="creation asc", limit_page_length=1)
    if rows:
        return rows[0]
    _lock(BOARD)
    rows = frappe.get_all(BOARD, filters={"is_default": 1}, pluck="name",
                          order_by="creation asc", limit_page_length=1)
    if rows:
        return rows[0]
    doc = persist(frappe.get_doc({"doctype": BOARD, "title": "",
                                  "visibility": "team", "commenting": "members", "member_management": "members",
                                  "is_default": 1}))
    frappe.db.commit()
    return doc.name


def ensure_lists(board):
    """看板还没有任何列表时建默认三列（待办 / 进行中 / 已完成，最后一列是完成列表）和六个颜色标签。"""
    if frappe.db.exists(COLUMN, {"board": board}):
        return False
    _lock(COLUMN)
    if frappe.db.exists(COLUMN, {"board": board}):
        return False
    for index, (key, done) in enumerate(contracts.DEFAULT_LISTS):
        persist(frappe.get_doc({"doctype": COLUMN, "board": board,
                                "title": _(f"erp.work.status.{key}"), "position": contracts.STEP * (index + 1),
                                "is_done_list": 1 if done else 0}))
    seed_labels(board)
    return True


def seed_labels(board):
    """新看板带上默认的六个颜色标签（照 Trello）。只在一个标签都没有时建：用户自己删光了也不再补。"""
    if frappe.db.exists(LABEL, {"board": board}):
        return
    for color in contracts.DEFAULT_LABEL_COLORS:
        persist(frappe.get_doc({"doctype": LABEL, "board": board, "title": "",
                                "color": color}))


def ensure_on_migrate():
    """装好 / 升级后建好默认看板和默认三列（名字用站点语言；只在 ERP 业务站点做）。"""
    if frappe.conf.get("pearnly_product") != "erp" or not frappe.db.table_exists(BOARD):
        return
    ensure_lists(default_board())


def open_board(board=None, right="view"):
    """页面级接口的入口：没指定看板就是默认看板；核对当前用户在这块板上有 `right`。返回看板行。"""
    access.require()
    name = board or default_board()
    info = access.board_info(name)
    if not info:
        raise Invalid("erp.work.error.board")
    access.require_right(name, right)
    if ensure_lists(name):
        frappe.db.commit()
    return info


def _info(row, user=None):
    rights = access.rights(row.name, user)
    return {"name": row.name, "title": row.title or _(DEFAULT_TITLE), "visibility": row.visibility,
            "commenting": row.commenting,
            "member_management": row.member_management, "is_default": row.is_default, "archived": row.archived,
            "archived_at": row.get("archived_at"), "role": access.members_of(row.name).get(user or frappe.session.user),
            "rights": [right for right in contracts.RIGHTS if right in rights]}


def info(board):
    return _info(access.board_info(board))


def boards(archived=False):
    """能看的看板（切换看板用）：默认看板在最前，其余按名字。归档的只列自己能管的。"""
    access.require()
    names = access.visible_boards()
    rows = frappe.get_all(BOARD, filters={"archived": 1 if archived else 0,
                                          "name": ["in", names or [""]]},
                          fields=access.BOARD_FIELDS + ["archived_at"], limit_page_length=0)
    result = [_info(row) for row in rows]
    if archived:
        result = [row for row in result if "manage" in row["rights"]]
    result.sort(key=lambda row: (not row["is_default"], (row["title"] or "").casefold()))
    return result


def lists(board):
    """另一块看板的列表（移动 / 复制 / 镜像到别的看板时选列表用）。"""
    open_board(board, "view")
    return frappe.get_all(COLUMN, filters={"board": board, "archived": 0}, fields=["name", "title"],
                          order_by="position asc, creation asc", limit_page_length=0)


def _values(values, row=None):
    if not isinstance(values, dict):
        raise Invalid("erp.work.error.request")
    result = {}
    if "title" in values or row is None:
        result["title"] = contracts.text(values.get("title"), contracts.BOARD_TITLE_MAX, required=True,
                                         key="erp.work.error.board_title")
    for key, allowed in (("visibility", contracts.VISIBILITIES), ("commenting", contracts.COMMENTING),
                         ("member_management", contracts.MEMBER_MANAGEMENT)):
        if key in values:
            result[key] = contracts.choice(values[key], allowed)
    return result


def create(values):
    """新建看板：有任务管理权限的人都能建，建的人是该看板管理员。"""
    access.require()
    fields = {"visibility": "team", "commenting": "members", "member_management": "members", **_values(values)}
    _lock(BOARD)
    if frappe.db.count(BOARD, {"archived": 0}) >= contracts.BOARDS_MAX:
        raise Invalid("erp.work.error.too_many")
    doc = persist(frappe.get_doc({"doctype": BOARD, **fields, "is_default": 0,
                                  "members": [{"user": frappe.session.user, "role": "admin"}]}))
    access.forget()
    ensure_lists(doc.name)
    return info(doc.name)


def update(board, values):
    """看板设置：名字、可见性、谁能评论、谁能加减成员（看板管理员）。"""
    row = open_board(board, "manage")
    doc = frappe.get_doc(BOARD, row.name)
    doc.update(_values(values, row))
    persist(doc)
    access.forget()
    notify.changed(doc.name, None, "board")
    return info(doc.name)


def archive(board):
    row = open_board(board, "manage")
    if row.is_default:
        raise Invalid("erp.work.error.default_board")
    doc = frappe.get_doc(BOARD, row.name)
    doc.archived, doc.archived_at = 1, _now()
    persist(doc)
    access.forget()
    notify.changed(doc.name, None, "board")
    return {"name": doc.name}


def restore(board):
    access.require()
    info_row = access.board_info(board)
    if not info_row:
        raise Invalid("erp.work.error.board")
    access.require_right(board, "manage")
    doc = frappe.get_doc(BOARD, board)
    doc.archived, doc.archived_at = 0, None
    persist(doc)
    access.forget()
    notify.changed(doc.name, None, "board")
    return info(doc.name)


def people(users):
    users = sorted(set(users))
    if not users:
        return []
    rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name", "user_image"],
                          order_by="full_name asc")
    return [{"user": row.name, "name": row.full_name or row.name, "image": row.user_image} for row in rows]


def assignable(board):
    """能看这块板的人（能被 @、能关注）；`can_edit` = 能改这块板，只有这些人能被分派为负责人。"""
    editors = access.editors(board)
    rows = people(access.viewers(board))
    for row in rows:
        row["can_edit"] = row["user"] in editors
    return rows


def members(board):
    """成员名单（能看这块板的人都能看）+ 可以加进来的人（只给有加减成员权利的人）。"""
    row = open_board(board, "view")
    roles = access.members_of(row.name)
    rows = people(roles)
    for item in rows:
        item["role"] = roles[item["user"]]
    candidates = []
    if access.can(row.name, "members"):
        candidates = people(access.team_users() - set(roles))
    return {"members": rows, "candidates": candidates, "board": info(row.name)}


def save_members(board, rows):
    """成员名单一次保存：[{user, role}]。能改什么见 `contracts.member_changes`；新加的人必须有任务管理权限。"""
    row = open_board(board, "view")
    if not isinstance(rows, list):
        raise Invalid("erp.work.error.request")
    wanted = {}
    for item in rows:
        if not isinstance(item, dict):
            raise Invalid("erp.work.error.request")
        wanted[item.get("user")] = item.get("role")
    doc = frappe.get_doc(BOARD, row.name)
    current = {item.user: item.role for item in doc.members}
    me = frappe.session.user
    changes = contracts.member_changes(current, wanted, actor=me, manage=access.can(row.name, "manage"),
                                       can_members=access.can(row.name, "members"))
    if set(changes["add"]) - access.team_users():
        raise Invalid("erp.work.error.member")
    doc.set("members", [{"user": user, "role": role} for user, role in wanted.items()])
    persist(doc)
    access.forget()
    notify.changed(row.name, None, "members")
    if access.can(row.name, "view"):
        return members(row.name)
    return {"members": [], "candidates": [], "board": None}
