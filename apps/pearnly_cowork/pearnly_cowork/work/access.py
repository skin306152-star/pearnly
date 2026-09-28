"""任务管理的权限判据：专用接口与原生资源共用同一份。

能进任务管理 = 站点是 Cowork、账号启用、勾了「任务管理」（`Cowork Work`）或是产品管理员（`Cowork Admin`）。
不单独售卖（用户 2026-09-27），所以不看开通功能。
每块看板再按成员与可见范围算权利（用户 2026-09-29，矩阵在 `contracts.board_rights`）：团队可见的板有任务管理权限的人
都能看能改，私有板只有看板成员，Cowork 管理员看得到、管得了所有看板。卡片不单独设权限。
原生资源只给读（且只读得到能看的看板），写一律走本域的专用接口（见 `documents`）。
"""
import frappe
from frappe import _
from pearnly_cowork.access import scope
from pearnly_cowork.work import contracts

ROLE = "Cowork Work"
ADMIN_ROLE = "Cowork Admin"
BOARD = "Cowork Work Board"
BOARD_MEMBER = "Cowork Work Board Member"
CARD = "Cowork Work Card"
LABEL = "Cowork Work Label"
ACTIVITY = "Cowork Work Activity"
NOTIFICATION = "Cowork Work Notification"
COLUMN = "Cowork Work Column"
FIELD = "Cowork Work Field"
MIRROR = "Cowork Work Mirror"
RULE = "Cowork Work Rule"
TYPES = (BOARD, CARD, LABEL, ACTIVITY, NOTIFICATION, COLUMN, FIELD, MIRROR, RULE)
BOARD_FIELDS = ["name", "workspace", "title", "visibility", "commenting", "member_management", "is_default",
                "archived"]


def allowed(user=None):
    user = user or frappe.session.user
    return bool(user and user != "Guest" and frappe.conf.get("pearnly_product") == "cowork"
                and frappe.db.get_value("User", user, "enabled")
                and {ROLE, ADMIN_ROLE}.intersection(frappe.get_roles(user)))


def require():
    if not allowed():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def administrator(user=None):
    return ADMIN_ROLE in frappe.get_roles(user or frappe.session.user)


def _users_with(roles):
    users = frappe.get_all("Has Role", filters={"parenttype": "User", "role": ["in", list(roles)]},
                           pluck="parent", distinct=True)
    users = [user for user in users if user not in ("Administrator", "Guest")]
    if not users:
        return set()
    return set(frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name"))


def team_users():
    """有任务管理权限的人（启用的账号）。"""
    return _users_with((ROLE, ADMIN_ROLE))


# ── 看板权利 ──────────────────────────────────────────────────────────────────
# 一次请求里同一块板、同一个人只算一次；看板设置 / 成员改了由 `boards` 调 `forget()`。


def _cache():
    cache = getattr(frappe.local, "cowork_work_rights", None)
    if cache is None:
        cache = frappe.local.cowork_work_rights = {}
    return cache


def forget():
    frappe.local.cowork_work_rights = {}


def board_info(board):
    if not board:
        return None
    key = ("board", board)
    cache = _cache()
    if key not in cache:
        cache[key] = frappe.db.get_value(BOARD, board, BOARD_FIELDS, as_dict=True)
    return cache[key]


def members_of(board):
    """{用户: 角色}"""
    key = ("members", board)
    cache = _cache()
    if key not in cache:
        cache[key] = {row.user: row.role for row in frappe.get_all(
            BOARD_MEMBER, filters={"parenttype": BOARD, "parent": board}, fields=["user", "role"],
            order_by="idx asc", limit_page_length=0)}
    return cache[key]


def rights(board, user=None):
    user = user or frappe.session.user
    key = ("rights", board, user)
    cache = _cache()
    if key not in cache:
        info = board_info(board)
        if not info or not allowed(user) or not scope.contains(info.workspace, user=user):
            cache[key] = frozenset()
        else:
            cache[key] = contracts.board_rights(
                role=members_of(board).get(user), visibility=info.visibility, team_user=True,
                cowork_admin=administrator(user), commenting=info.commenting,
                member_management=info.member_management)
            if info.archived:
                # 归档的看板只读（照 Trello 关闭的看板），能管的人可以恢复。
                cache[key] = cache[key] & {"view", "manage"}
    return cache[key]


def can(board, right, user=None):
    return right in rights(board, user)


def require_right(board, right):
    require()
    if not can(board, right):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def viewers(board):
    """能看这块板的人：收提醒、被分派、被 @、收实时刷新都只限这些人。"""
    info = board_info(board)
    if not info:
        return set()
    team = team_users()
    if info.visibility == "team":
        return team
    return (set(members_of(board)) & team) | (_users_with((ADMIN_ROLE,)) & team)


def _visible_boards_sql(user):
    """当前用户能看的看板（子查询）；Cowork 管理员是全部。"""
    if administrator(user):
        return f"SELECT name FROM `tab{BOARD}`"
    return (f"SELECT b.name FROM `tab{BOARD}` b WHERE b.visibility='team' OR EXISTS ("
            f"SELECT 1 FROM `tab{BOARD_MEMBER}` m WHERE m.parent=b.name AND m.parenttype='{BOARD}' "
            f"AND m.user={frappe.db.escape(user)})")


def visible_boards(user=None):
    user = user or frappe.session.user
    if not allowed(user):
        return []
    return frappe.db.sql_list(_visible_boards_sql(user))


# ── 原生资源（只读）─────────────────────────────────────────────────────────


def _board_of(doc):
    if doc.doctype == BOARD:
        return doc.name
    if doc.doctype in (ACTIVITY, NOTIFICATION):
        return frappe.db.get_value(CARD, doc.card, "board")
    return doc.get("board")


def has_permission(doc, ptype=None, user=None, **kwargs):
    # 返回 False 会让框架退回 DocShare；这里直接抛，撤掉的权限不能靠旧分享找回来。
    # 自动化规则只给能管这块板的人看（普通成员、观察者读不到）。
    right = "manage" if doc.doctype == RULE else "view"
    if (not allowed(user) or ptype not in scope.READ_TYPES
            or not scope.contains(doc.workspace, user=user) or not can(_board_of(doc), right, user)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def notification_permission(doc, ptype=None, user=None, **kwargs):
    # 提醒只给收件人本人看。
    has_permission(doc, ptype, user)
    if doc.user != (user or frappe.session.user):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return True


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        frappe.throw(_("Assign Cowork access through company and client permissions."))


def _query(doctype, user=None):
    scope._reject_share_fallback(doctype, user)
    user = user or frappe.session.user
    if not allowed(user):
        return "1=0"
    table = f"`tab{doctype}`"
    clause = f"{table}.workspace IN (SELECT name FROM `tabCowork Workspace` WHERE enabled=1)"
    boards = _visible_boards_sql(user)
    if doctype == BOARD:
        return f"{clause} AND {table}.name IN ({boards})"
    if doctype in (ACTIVITY, NOTIFICATION):
        return f"{clause} AND {table}.card IN (SELECT c.name FROM `tab{CARD}` c WHERE c.board IN ({boards}))"
    return f"{clause} AND {table}.board IN ({boards})"


def board_query(user=None):
    return _query(BOARD, user)


def card_query(user=None):
    return _query(CARD, user)


def label_query(user=None):
    return _query(LABEL, user)


def activity_query(user=None):
    return _query(ACTIVITY, user)


def column_query(user=None):
    return _query(COLUMN, user)


def field_query(user=None):
    return _query(FIELD, user)


def mirror_query(user=None):
    return _query(MIRROR, user)


def rule_query(user=None):
    user = user or frappe.session.user
    clause = _query(RULE, user)
    if clause == "1=0" or administrator(user):
        return clause
    # 规则只给看板管理员看。
    return (f"{clause} AND `tab{RULE}`.board IN (SELECT m.parent FROM `tab{BOARD_MEMBER}` m "
            f"WHERE m.parenttype='{BOARD}' AND m.role='admin' AND m.user={frappe.db.escape(user)})")


def notification_query(user=None):
    clause = _query(NOTIFICATION, user)
    if clause == "1=0":
        return clause
    return f"{clause} AND `tab{NOTIFICATION}`.user={frappe.db.escape(user or frappe.session.user)}"
