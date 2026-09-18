# -*- coding: utf-8 -*-
"""DMS 操作员花名册（服务层 + 接口）。

照老产品搬（`static/dms/dms-roster*.js` + `services/dms_roster/service.py`），
**不自己设计**。老产品的模型是：

    每个操作员 = 一个本地账号（member 用户） + 他自己的 DMS 账密 + 一行档案
                （显示名 / 角色 sales|admin / 启停 / 是否允许查询 / 提成归属）

两条语义照抄，别改：

1. **操作员账号只走 LINE，永远不用于网页登录** —— 老产品原话是"操作员 user 永不该
   有 dms 门 token（只走 LINE）"。这里同样：账号建出来**没有**统一身份绑定
   （`Pearnly Identity` 没有这行就登不进来），也**没有**本地密码。
2. **绑定码是每一行上的动作，不是单独一页** —— 老产品界面就是列表每行一个
   「发绑定码」按钮，点开弹窗（让对方在 LINE 加好友 → 把码发过来 → 可重新生成 /
   看剩余有效期）。所以这里有 `api_operator_bind_code(name)`。

只有**租户管理员**能管花名册（老产品里这一页 nav 仅 owner 可见，员工直调后端 403）。
"""

import secrets

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

OPERATOR = "DMS Operator"
BINDING = "Pearnly LINE Binding"

VALID_ROLES = ("sales", "admin")
VALID_STATUS = ("active", "inactive")

# 能管花名册的角色 = 租户管理员。开通时发的是 "DMS 管理员"（见控制面
# features.PRODUCT_ADMIN_ROLE）；System Manager 是框架超管，本机调试要用。
ADMIN_ROLES = ("DMS 管理员", "System Manager")

# 操作员账号的邮箱后缀。**不是真实邮箱**：这些账号不用来收发信、也不用来登录，
# 只是 Frappe 的 User 主键必须是邮箱，所以造一个。
ROLL_DOMAIN = "operators.local"


def _require_admin() -> str:
    """老产品这一页是 owner-only。权限边界在后端，不在"前端把菜单藏起来"。"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("未登录"), frappe.AuthenticationError)
    if not set(ADMIN_ROLES) & set(frappe.get_roles(user)):
        frappe.throw(_("只有管理员能管操作员花名册"), frappe.PermissionError)
    return user


def _clean(value) -> str:
    return str(value or "").strip()


def _line_status(user: str) -> dict:
    """该操作员绑没绑 LINE。真值在 `Pearnly LINE Binding`（一 user 一 LINE）。"""
    row = frappe.db.get_value(
        BINDING,
        {"user": user, "status": "active"},
        ["display_name", "bound_at"],
        as_dict=True,
    )
    if not row:
        return {"line_bound": False, "line_display_name": "", "line_bound_at": ""}
    return {
        "line_bound": True,
        "line_display_name": row.display_name or "",
        "line_bound_at": str(row.bound_at or ""),
    }


def _row(doc) -> dict:
    return {
        "name": doc.name,
        "display_name": doc.display_name,
        "user": doc.user,
        "role": doc.dms_role,
        "status": doc.status,
        "can_query_dms": bool(doc.can_query_dms),
        "dms_username": doc.dms_username or "",
        # 密码**不回显**：老产品的编辑框是"留空则不修改"，界面只需要知道有没有配过
        "has_dms_password": bool(
            get_decrypted_password(
                OPERATOR, doc.name, "dms_password", raise_exception=False
            )
        ),
        "advisor_id": doc.advisor_id or "",
        "advisor_name": doc.advisor_name or "",
        **_line_status(doc.user),
    }


def list_operators() -> dict:
    """花名册列表。老产品的列：操作员 / 角色 / 查询权限 / LINE 状态 / 状态 / 操作。"""
    _require_admin()
    docs = frappe.get_all(
        OPERATOR, fields=["name"], order_by="creation asc", limit_page_length=0
    )
    return {"items": [_row(frappe.get_doc(OPERATOR, d.name)) for d in docs]}


def create_operator(
    display_name: str,
    dms_username: str,
    dms_password: str,
    dms_role: str = "sales",
    can_query_dms: int | bool = 0,
    advisor_id: str | None = None,
    advisor_name: str | None = None,
) -> dict:
    """新增操作员：建一个**只走 LINE** 的本地账号 + 一行档案。

    ⚠️ 与老产品的一处已知差异（明写，不隐瞒）：老产品要求"先连接你自己的 MR.ERP DMS，
    再新增操作员"（拿老板那条连接当模板），**新平台还没有 DMS 连接（endpoint）的后端**，
    所以这条前置检查现在无处可依，暂时不做。等 DMS 连接做出来要补上，否则会开出
    "挂着一个不存在的 DMS 连接"的操作员。
    """
    _require_admin()
    display_name = _clean(display_name)
    dms_username = _clean(dms_username)
    dms_password = str(dms_password or "")

    if not display_name or not dms_username or not dms_password:
        frappe.throw(_("请填写显示名、DMS 用户名和密码"))
    if dms_role not in VALID_ROLES:
        frappe.throw(_("角色只能是 sales（销售）或 admin（管理员）"))

    # 账号名随机、密码随机且**不回显不外传**（操作员只走 LINE，不需要网页密码）。
    # 邮箱是造的：Frappe 的 User 主键就是邮箱，而这些账号不收发信。
    username = "dmsop-" + secrets.token_hex(4)
    user = frappe.get_doc({
        "doctype": "User",
        "email": f"{username}@{ROLL_DOMAIN}",
        "username": username,
        "first_name": display_name[:140],
        "enabled": 1,
        "send_welcome_email": 0,
    })
    user.flags.ignore_password_policy = True
    # 不设密码、不发欢迎信、不给任何角色 —— 这个账号的用途只有一个：当 LINE 绑定的落点
    user.insert(ignore_permissions=True)

    doc = frappe.get_doc({
        "doctype": OPERATOR,
        "user": user.name,
        "display_name": display_name,
        "dms_role": dms_role,
        "status": "active",
        "dms_username": dms_username,
        "dms_password": dms_password,
        "can_query_dms": 1 if can_query_dms else 0,
        "advisor_id": _clean(advisor_id),
        # 名字由服务端解（老产品：不信客户端传的）；新平台还没有顾问名册来源，
        # 所以这里只收调用方给的显示名，取不到就留空。
        "advisor_name": _clean(advisor_name),
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return _row(frappe.get_doc(OPERATOR, doc.name))


def update_operator(
    name: str,
    *,
    dms_username: str | None = None,
    dms_password: str | None = None,
    dms_role: str | None = None,
    can_query_dms=None,
    advisor_id: str | None = None,
    advisor_name: str | None = None,
    notes: str | None = None,
) -> dict:
    """编辑操作员。DMS 用户名与密码**各改各的**（留空 = 不修改），照老产品。"""
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)

    if dms_username is not None and _clean(dms_username):
        doc.dms_username = _clean(dms_username)
    if dms_password:
        doc.dms_password = str(dms_password)
    if dms_role:
        if dms_role not in VALID_ROLES:
            frappe.throw(_("角色只能是 sales（销售）或 admin（管理员）"))
        doc.dms_role = dms_role
    if can_query_dms is not None:
        doc.can_query_dms = 1 if can_query_dms else 0
    if advisor_id is not None:
        doc.advisor_id = _clean(advisor_id)
        doc.advisor_name = _clean(advisor_name)
    if notes is not None:
        doc.notes = notes

    doc.save(ignore_permissions=True)
    frappe.db.commit()
    return _row(frappe.get_doc(OPERATOR, name))


def set_status(name: str, status: str) -> dict:
    """启用 / 停用。

    老产品原话：停用「停用后其 DMS 推送停止并解除 LINE 绑定」、
    启用「启用后需重新发绑定码连接 LINE」。两句都照做。
    """
    _require_admin()
    if status not in VALID_STATUS:
        frappe.throw(_("状态只能是 active 或 inactive"))
    doc = frappe.get_doc(OPERATOR, name)
    doc.status = status
    doc.save(ignore_permissions=True)

    unbound = 0
    if status == "inactive":
        from pearnly_core import line_binding

        for row in frappe.get_all(
            BINDING, filters={"user": doc.user, "status": "active"}, pluck="line_user_id"
        ):
            if line_binding.unbind(row):
                unbound += 1
    frappe.db.commit()
    return {**_row(frappe.get_doc(OPERATOR, name)), "unbound_line": unbound}


def delete_operator(name: str) -> dict:
    """删除操作员：解绑 LINE、移除账号；**推送记录保留**（照老产品的确认文案）。"""
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)
    user = doc.user

    from pearnly_core import line_binding

    for row in frappe.get_all(
        BINDING, filters={"user": user, "status": "active"}, pluck="line_user_id"
    ):
        line_binding.unbind(row)

    frappe.delete_doc(OPERATOR, name, ignore_permissions=True, force=True)
    if frappe.db.exists("User", user):
        frappe.delete_doc("User", user, ignore_permissions=True, force=True)
    frappe.db.commit()
    return {"deleted": name, "user": user}


# ── 接口层 ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_operators():
    return list_operators()


@frappe.whitelist(methods=["POST"])
def api_create_operator(**kwargs):
    kwargs.pop("cmd", None)
    return create_operator(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_update_operator(name, **kwargs):
    kwargs.pop("cmd", None)
    return update_operator(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_operator_status(name, status):
    return set_status(name, status)


@frappe.whitelist(methods=["POST"])
def api_delete_operator(name):
    return delete_operator(name)


@frappe.whitelist(methods=["POST"])
def api_operator_bind_code(name, minutes=None):
    """给**某一个操作员**生成一次性绑定码（列表每行那个「发绑定码」按钮）。

    老产品的弹窗是两步话术：
      1. 让该操作员在 LINE 添加 Pearnly DMS 好友；
      2. 在 LINE 对话中发送此绑定码完成连接。
    所以这里只发码，码怎么用是界面的事（话术照抄，不自己改）。

    只给**操作员**发码 —— 不接受任意 user 参数。拿到别人的码就等于把别人的 LINE
    绑到自己账号上。
    """
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)
    if doc.status != "active":
        frappe.throw(_("停用的操作员不能发绑定码，先启用"))

    from pearnly_core import line_binding

    try:
        ttl = int(minutes) if minutes else line_binding.DEFAULT_TTL_MINUTES
    except (TypeError, ValueError):
        ttl = line_binding.DEFAULT_TTL_MINUTES
    ttl = max(1, min(ttl, 24 * 60))

    result = line_binding.generate_bind_code(doc.user, ttl)
    return {**result, "operator": doc.name, "display_name": doc.display_name}
