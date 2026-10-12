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
from typing import Optional

import frappe
from frappe import _

from pearnly_core.endpoints import read_password
from pearnly_dms.operators import advisors, line_account

OPERATOR = "DMS Operator"
BINDING = "Pearnly LINE Binding"

VALID_ROLES = ("sales", "admin")
VALID_STATUS = ("active", "inactive")

# 能管花名册的角色 = 租户管理员。开通时发的是 "DMS Admin"（见控制面
# features.PRODUCT_ADMIN_ROLE，**必须一字不差**：写过中文名，改名后管理员就被挡在外面）；
# System Manager 是框架超管，本机调试要用。
ADMIN_ROLES = ("DMS Admin", "System Manager")

# 操作员账号的邮箱后缀。**不是真实邮箱**：这些账号不用来收发信、也不用来登录，
# 只是 Frappe 的 User 主键必须是邮箱，所以造一个。
ROLL_DOMAIN = "operators.local"


def _require_admin() -> str:
    """老产品这一页是 owner-only。权限边界在后端，不在"前端把菜单藏起来"。"""
    user = frappe.session.user
    if not user or user == "Guest":
        frappe.throw(_("Not signed in"), frappe.AuthenticationError)
    if not set(ADMIN_ROLES) & set(frappe.get_roles(user)):
        frappe.throw(_("Only an admin can manage the operator roster"), frappe.PermissionError)
    return user


def _clean(value) -> str:
    return str(value or "").strip()


def _system_usernames() -> set:
    """本站系统在用的 DMS 用户名：公司连接的账号（即读档账号）与它的管理员账号、已登记的系统账号。

    操作员用了其中任何一个，后台读取 / 改写与操作员本人会互相把对方挤下线。反方向（登记系统账号
    时不能用操作员账号）在 `connections.api._taken_names` 里拦。
    """
    names = set()
    for row in frappe.get_all("Pearnly ERP Endpoint",
                              filters={"adapter": "mrerp_dms", "owner_user": ["is", "not set"]},
                              fields=["username", "admin_username"]):
        names.update((row.get("username"), row.get("admin_username")))
    names.update(frappe.get_all("DMS System Account", pluck="username"))
    return {_clean(name) for name in names if _clean(name)}


def _taken_usernames(exclude: str = "") -> set:
    """这位操作员不能再用的 DMS 用户名（小写）：系统账号 + 其他操作员（**含停用的**）。

    两个操作员共用一个 DMS 账号会互相挤下线，一人改了密码另一人就一直登录失败（用户
    2026-10-13 定：不许共用，不分大小写比对）。账号要转给新员工，先删掉旧操作员。
    """
    names = set(_system_usernames())
    names.update(frappe.get_all(OPERATOR, filters={"name": ["!=", exclude]} if exclude else None,
                                pluck="dms_username"))
    return {_clean(name).lower() for name in names if _clean(name)}


def _verify_dms_login(username: str, password: str) -> None:
    """新增操作员、或改了他的 DMS 密码时，用这组账密**只试登录一次**（用户 2026-10-02）。

    登上立即登出，不读不写任何资料；登不上就按原因拒绝保存（账密错 / 连不上 DMS / 账号正在
    别处登录），沿用页面原生的错误提示。只在这两个时点试：DMS 同一账号会互相挤下线。
    """
    url = frappe.db.get_value("Pearnly ERP Endpoint",
                              {"adapter": "mrerp_dms", "owner_user": ["is", "not set"]}, "system_url")
    if not url:
        frappe.throw(_("roster.error.noConnection"))
    from pearnly_dms import i18n
    from pearnly_dms.adapter.account_probe import probe

    result = probe(url, username, password, "login")
    if not result.get("ok"):
        frappe.throw(i18n.t("dms.err." + str(result.get("error_code") or "ERR_UNEXPECTED")))


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
        # （用共用读取口：解不开密文只算"没配"，不会把框架技术细节弹到操作员页上）
        "has_dms_password": bool(read_password(OPERATOR, doc.name, "dms_password")),
        "advisor_id": doc.advisor_id or "",
        "advisor_name": doc.advisor_name or "",
        **_line_status(doc.user),
    }


# ── 给别的模块读的档案口子（差异卡要按账号查角色） ──────────────────────────
#
# 键名与老产品 `dms_roster.store` 对齐（`user_id` / `dms_role` / `line_user_id`），
# 这样搬过来的流程层可以直接吃，不必到处改键名。


def _profile_row(doc) -> dict:
    return {
        "user_id": doc.user,
        "display_name": doc.display_name or "",
        "dms_role": doc.dms_role or "",
        "status": doc.status or "active",
        "can_query_dms": bool(doc.can_query_dms),
    }


def get_profile(user: str) -> Optional[dict]:
    """某人的花名册档案；**没有档案回 None**（调用方据此判断"是不是自己人"）。"""
    if not user:
        return None
    name = frappe.db.get_value(OPERATOR, {"user": user}, "name")
    if not name:
        return None
    return _profile_row(frappe.get_doc(OPERATOR, name))


def list_operators(start=0, page_length=20) -> dict:
    """花名册列表。老产品的列：操作员 / 角色 / 查询权限 / LINE 状态 / 状态 / 操作。"""
    _require_admin()
    try:
        start, page_length = int(start), int(page_length)
    except (TypeError, ValueError):
        frappe.throw(_("Invalid value"))
    if start < 0 or not 1 <= page_length <= 2500:
        frappe.throw(_("Invalid value"))
    docs = frappe.get_all(OPERATOR, fields=["name"], order_by="creation asc, name asc",
                          limit_start=start, limit_page_length=page_length)
    return {"items": [_row(frappe.get_doc(OPERATOR, d.name)) for d in docs],
            "total": frappe.db.count(OPERATOR)}


def create_operator(
    display_name: str,
    dms_username: str,
    dms_password: str,
    dms_role: str = "sales",
    can_query_dms: int | bool = 0,
    advisor_id: str | None = None,
    advisor_name: str | None = None,
) -> dict:
    """建立仅用于 LINE 绑定的操作员身份，沿用已连接的本站 DMS。"""
    _require_admin()
    display_name = _clean(display_name)
    dms_username = _clean(dms_username)
    dms_password = str(dms_password or "")

    if not display_name or not dms_username or not dms_password:
        frappe.throw(_("Fill in the display name, DMS username and password"))
    if dms_role not in VALID_ROLES:
        frappe.throw(_("Role must be sales or admin"))

    if not frappe.db.exists("Pearnly ERP Endpoint", {"adapter": "mrerp_dms", "enabled": 1, "owner_user": ["is", "not set"]}):
        frappe.throw(_("roster.error.noConnection"))
    if dms_username.lower() in _taken_usernames():
        frappe.throw(_("This account is already used by an operator or another system account"))

    pinned = advisors.resolve(advisor_id)
    _verify_dms_login(dms_username, dms_password)

    # 内部身份不提供后台登录，不分配平台角色。
    # 邮箱是造的：Frappe 的 User 主键就是邮箱，而这些账号不收发信。
    username = "dmsop-" + secrets.token_hex(4)
    user = frappe.get_doc({
        "doctype": "User",
        "email": f"{username}@{ROLL_DOMAIN}",
        "username": username,
        "first_name": display_name[:140],
        "enabled": 1,
        "send_welcome_email": 0,
        "user_type": "Website User",
        "language": "th",
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
        "advisor_id": pinned["id"],
        "advisor_name": pinned["name"],
    })
    doc.insert(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return _row(frappe.get_doc(OPERATOR, doc.name))


def update_operator(
    name: str,
    *,
    display_name: str | None = None,
    dms_username: str | None = None,
    dms_password: str | None = None,
    dms_role: str | None = None,
    can_query_dms=None,
    advisor_id: str | None = None,
    advisor_name: str | None = None,
    notes: str | None = None,
) -> dict:
    """编辑操作员：只改密码（留空 = 不修改）。

    DMS 用户名**开户时定死**：可以拿它登录，但不能改（用户 2026-09-23："后台管理发号后，
    用户名和邮箱是绑死这个租户的，唯一关系，用户只可以自己改密码"）。参数留着是为了
    老客户端不炸：值一样（或者没带）照旧通过，**想改就明确拒掉**，不静默忽略。
    """
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)

    if dms_username is not None and _clean(dms_username) and _clean(dms_username) != (doc.dms_username or ""):
        # 拒掉"改用户名"这个动作，但**不写"用户名是开户时发的"那类说明**（用户 2026-09-23：
        # "这类文案不要随意添加，删掉"）。界面本来就不提供这个动作，这里是防直连客户端的兜底，
        # 所以用本文件既有的短拒法（同函数其它参数被拒时也是这句）。
        frappe.throw(_("Invalid value"))
    pinned = advisors.resolve(advisor_id) if advisor_id is not None else None
    if display_name is not None:
        if not _clean(display_name):
            frappe.throw(_("Fill in the display name, DMS username and password"))
        doc.display_name = _clean(display_name)
    if dms_password:
        # 改了账密才试；只改显示名 / 角色 / 提成归属不碰 DMS。最后才试，前面的校验先拒掉。
        verify_password = str(dms_password)
        doc.dms_password = verify_password
    else:
        verify_password = ""
    if dms_role:
        if dms_role not in VALID_ROLES:
            frappe.throw(_("Role must be sales or admin"))
        doc.dms_role = dms_role
    if can_query_dms is not None:
        doc.can_query_dms = 1 if can_query_dms else 0
    if pinned is not None:
        doc.advisor_id = pinned["id"]
        doc.advisor_name = pinned["name"]
    if notes is not None:
        doc.notes = notes

    if verify_password:
        _verify_dms_login(doc.dms_username or "", verify_password)
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
        frappe.throw(_("Status must be active or inactive"))
    doc = frappe.get_doc(OPERATOR, name)
    if (status == "active" and doc.status != "active"
            and _clean(doc.dms_username).lower() in _taken_usernames(exclude=doc.name)):
        # 停用期间这个 DMS 账号已被别人占用（或是规则之前就共用的老数据）：不重新启用。
        frappe.throw(_("This account is already used by an operator or another system account"))
    doc.status = status
    doc.save(ignore_permissions=True)

    unbound = 0
    if status == "inactive":
        frappe.db.delete("Pearnly LINE Bind Code", {"user": doc.user, "used": 0})
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
    frappe.db.delete("Pearnly LINE Bind Code", {"user": user, "used": 0})

    from pearnly_core import line_binding

    for row in frappe.get_all(
        BINDING, filters={"user": user, "status": "active"}, pluck="line_user_id"
    ):
        line_binding.unbind(row)

    frappe.delete_doc(OPERATOR, name, ignore_permissions=True, force=True)
    if frappe.db.exists("User", user):
        # Keep the audit identity and endpoint links; a removed operator cannot log in.
        frappe.db.set_value("User", user, "enabled", 0)
        frappe.clear_cache(user=user)
    frappe.db.commit()
    return {"deleted": name, "user": user}


# ── 接口层 ──────────────────────────────────────────────────────────────────


@frappe.whitelist()
def api_list_operators(start=0, page_length=20):
    return list_operators(start, page_length)


@frappe.whitelist(methods=["POST"])
def api_create_operator(**kwargs):
    kwargs.pop("cmd", None)
    if "can_query_dms" in kwargs:
        kwargs["can_query_dms"] = frappe.utils.cint(kwargs["can_query_dms"])
    return create_operator(**kwargs)


@frappe.whitelist(methods=["POST"])
def api_update_operator(name, **kwargs):
    kwargs.pop("cmd", None)
    if "can_query_dms" in kwargs:
        kwargs["can_query_dms"] = frappe.utils.cint(kwargs["can_query_dms"])
    return update_operator(name, **kwargs)


@frappe.whitelist(methods=["POST"])
def api_set_operator_status(name, status):
    return set_status(name, status)


@frappe.whitelist(methods=["POST"])
def api_delete_operator(name):
    return delete_operator(name)


@frappe.whitelist(methods=["POST"])
def api_operator_bind_code(name, minutes=None):
    """Issue an operator-scoped code only after verifying this site's assigned OA.

    Never accept an arbitrary user: the code grants that operator's LINE identity.
    """
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)
    if doc.status != "active":
        frappe.throw(_("A disabled operator cannot issue a bind code; enable them first"))

    from pearnly_core import line_binding

    try:
        ttl = int(minutes) if minutes else line_binding.DEFAULT_TTL_MINUTES
    except (TypeError, ValueError):
        ttl = line_binding.DEFAULT_TTL_MINUTES
    ttl = max(1, min(ttl, 24 * 60))

    account = line_account.public_account()
    result = line_binding.generate_bind_code(doc.user, ttl)
    return {**result, "operator": doc.name, "display_name": doc.display_name, "line": account}


@frappe.whitelist(methods=["GET"])
def api_advisors():
    """Commission options for the roster dialog, with the failure as a code.

    A failed read must not abort the dialog or surface a server toast: the operator's
    existing assignment stays selected and saving simply omits the field. The save path
    (`advisors.resolve`) still throws, so an unverified id can never be written.
    """
    _require_admin()
    items, error = advisors.read_options()
    return {"items": items, "error": error}


@frappe.whitelist(methods=["POST"])
def api_unbind_operator(name):
    """Disconnect this employee's LINE, retaining their roster and DMS credentials."""
    _require_admin()
    doc = frappe.get_doc(OPERATOR, name)
    frappe.db.delete("Pearnly LINE Bind Code", {"user": doc.user, "used": 0})
    # 和停用、删除走同一个共用解绑入口：它会通知 `pearnly_line_binding_changed`，
    # 被解绑的人当场清掉普通草稿（待核对的订车照旧保留）、手机菜单当场换回未绑定。
    from pearnly_core import line_binding

    for row in frappe.get_all(BINDING, filters={"user": doc.user, "status": "active"}, pluck="line_user_id"):
        line_binding.unbind(row)
    frappe.db.commit()
    return _row(doc)
