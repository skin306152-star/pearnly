"""员工账号与岗位的服务端实现。

所有判断在服务端完成：谁可以管团队、能发哪些角色、能改谁。界面只负责展示。

为什么写用户时显式 `ignore_permissions`：核心给 `User` 注册的
`pearnly_core.desk.permissions.user_permission` 规定——产品站点上非超管只能访问
**自己**那条用户记录（个人设置自助，花名册由各自产品管）。该钩子
在文档级检查里对任何"别人的 User"返回 False，于是租户管理员连新增员工都会被框架挡下。
产品自己的判据因此放在本模块：只有带 `team.manage` 的账号（ERP Admin）能进来，员工
只能拿到本站点已开通的功能角色，老板本人与平台账号不可改。这与开通侧
`ensure_local_user` 的做法一致（那里同样用 `ignore_permissions` 建账号）。
"""

import re

import frappe
from frappe import _

from pearnly_erp_portal.access import guard, rules, menu

NOT_PERMITTED = "Not permitted"
ALREADY_EXISTS = "Already exists"
ROLE_REQUIRED = "Choose at least one job role"
EMAIL_REQUIRED = "Enter a valid email"
# 用户名就是"这个人"：请填写用户名（原来那句是"姓名"必填，一回事）。
USERNAME_REQUIRED = "Enter the user name"
# 用户名只认英文和数字 —— 这条是新增的一句，四语在术语表里。
USERNAME_CHARSET = "Username can only have letters and numbers"
# 重名用框架自己那句（`User.validate_username` 同款，四语都有）。
USERNAME_TAKEN = "Username {0} already exists"

# 用户名：**英文和数字**（用户 2026-09-24："添加用户必须是英文和数字，不区分大小写，长度不限"）。
# 长度不设上限（框架 `User.username` 是 Data，库列的宽度自己管着）；大小写不区分交给库的
# 排序规则 + 下面那次显式查询。
USERNAME_RE = re.compile(r"^[A-Za-z0-9]+$")

# 公司隔离用的是**框架自己的 User Permission**（官方那套），公司表是我们自己的。
COMPANY = "ERP Company"

# 岗位的中文/泰文/日文说法：术语表是唯一来源，键写在 ERP 的术语表里。
ROLE_KEYS = {
    "ERP Products": "team.role.products",
    "ERP Stock": "team.role.stock",
    "ERP Purchase": "team.role.purchase",
    "ERP Sales": "team.role.sales",
    "ERP Cashier": "team.role.cashier",
}


def role_catalog() -> list[dict]:
    """**能发给员工的岗位**（= 本租户买了、站点上真有的功能角色）+ 每个的中文说法。

    这不是一张配置表：名单来自 `rules.FEATURE_ROLES`，只保留本站点实际存在的角色。
    没开通的功能没有角色，也就不会出现在勾选框里。
    """
    from pearnly_core.desk.navigation import _declarations

    spec = _declarations().get("erp") or {}
    labels = {route: label for label, route, _icon, _feature in spec.get("pages", [])}
    groups = {route: label for label, _icon, routes in spec.get("groups", []) for route in routes}
    labels["user"] = spec.get("profile_label", "My Profile")
    groups.update({route: label for label, routes in spec.get("permission_groups", []) for route in routes})
    allowed = set(assignable_roles())
    catalog = [{"name": menu.role(key), "label": _(labels.get(menu.route(key), key)),
                "group": _(groups.get(menu.route(key), "User Settings"))}
               for key in menu.CATALOG if menu.role(key) in allowed]
    # LINE 和个人信息、团队管理放在同一组（用户 2026-09-25："line 的勾选应该在用户菜单那里"）。
    if rules.LINE_ROLE in allowed:
        catalog.append({"name": rules.LINE_ROLE, "label": "LINE",
                        "group": _(groups.get("user", "User Settings"))})
    return catalog


def _clean_username(username, *, exclude=None) -> str:
    """用户名：英文 + 数字、不区分大小写、长度不限；只挡空和重名。

    重名这一句用**框架自己那句**（`Username {0} already exists`），四语都有；
    比较用 `lower()`，所以 `Somchai` 与 `somchai` 算同一个人（用户口径"不区分大小写"）。
    """
    value = str(username or "").strip()
    if not value:
        frappe.throw(_(USERNAME_REQUIRED))
    if not USERNAME_RE.fullmatch(value):
        frappe.throw(_(USERNAME_CHARSET))
    taken = frappe.db.sql(
        "select name from `tabUser` where lower(username) = lower(%s) and name != %s limit 1",
        (value, exclude or ""), pluck=True)
    if taken:
        frappe.throw(_(USERNAME_TAKEN).format(value))
    return value


def username_of(user) -> str:
    """一个人显示成什么：**用户名**（没设才回落账号本身）。

    用户 2026-09-24："全局可以涉及到姓名的地方，全部改为用户名。" 显示层统一走这里，
    不要在页面上各写一遍 `full_name` / 邮箱。
    """
    if not user:
        return ""
    return frappe.db.get_value("User", user, "username") or str(user)


def _clean_password(password) -> str:
    """密码：**不做任何限制**（用户 2026-09-24："密码可以自由填写，不限制长度，也不限制其它东西"）。

    只挡空字符串 —— 空密码等于账号建出来谁都进不去（不是"限制"，是这条数据没有意义）。
    框架自己的强度闸也在站点设置里关掉了（`enable_password_policy = 0`，见 patch `v1_9_username_login`）。
    """
    value = str(password or "")
    if not value:
        frappe.throw(_("Invalid value"))
    return value


def _identity_configured() -> bool:
    return bool(frappe.conf.get("pearnly_oidc_issuer"))


def _local_password_allowed() -> bool:
    """这个站点要不要写本地密码。

    接了统一身份的业务站点**不写**：业务用户只从身份服务登录，本地密码等于旁路
    （本仓合同，见 `pearnly_core.provisioning.ensure_local_user` 的第 2 条）。
    仅没有身份服务、也没有云端账户标记的实验室 / 离线演示站点写本地密码。
    """
    return not _identity_configured() and not frappe.conf.get("pearnly_native_account")


def _identity_subject(email: str, username: str, secret: str) -> str:
    """通过私有受限服务建号，返回 subject；绝不接管邮箱相同的现有身份。

    静默降级的后果最坏：账号在本地建好了、员工却永远登不上，而且没人知道为什么。
    """
    from pearnly_core.account import member_client

    return member_client.create(email, username, secret)


def _bound_subject(user: str) -> str:
    return frappe.db.get_value("Pearnly Identity", {
        "user": user, "issuer": str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/"),
        "status": "active"}, "subject")


def _bind_member(user: str, subject: str):
    """Only reached after team.manage and an authenticated private create result.

    The generic identity.bind_identity RPC intentionally requires System Manager.
    ERP Admin does not gain that role or permission to bind arbitrary identities.
    """
    issuer = str(frappe.conf.get("pearnly_oidc_issuer") or "").rstrip("/")
    if frappe.db.exists("Pearnly Identity", {"issuer": issuer, "subject": subject}):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    frappe.get_doc({"doctype": "Pearnly Identity", "issuer": issuer, "subject": subject,
        "user": user, "status": "active", "invited_by": frappe.session.user}).insert(ignore_permissions=True)


def present_roles() -> list[str]:
    """本站点上真实存在的 ERP 功能角色（= 这个租户买了的岗位）。"""
    return frappe.get_all(
        "Role", filters={"name": ["in", list(rules.FEATURE_ROLES.values())]}, pluck="name"
    )


def assignable_roles() -> tuple[str, ...]:
    """能发给员工的岗位。**这家公司不做收银时，"收银"这个岗位不发**（用户 2026-09-24：
    "不勾选就不显示 pos、记录、交接班，以及团队管理里的收银"）——
    判据与侧栏那三页同一个（`companies.pos_enabled_any`），发岗位与看菜单口径一致。
    """
    from pearnly_core.shell import entitlements
    from pearnly_erp_portal.masters import company as companies

    features = set(entitlements())
    pos = companies.pos_enabled_any()
    return tuple(menu.role(key) for key, spec in menu.CATALOG.items()
                 if (not spec[0] or spec[0] in features)
                 and (spec[0] != "cashier" or pos)
                 and frappe.db.exists("Role", menu.role(key))) + (
        (rules.LINE_ROLE,) if frappe.db.exists("Role", rules.LINE_ROLE) else ())


def _clean_roles(roles, *, existing=()) -> list[str]:
    if isinstance(roles, str):
        roles = frappe.parse_json(roles)
    if not isinstance(roles, (list, tuple)):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    allowed = set(assignable_roles())
    retained = set(existing) - allowed
    if not roles and not retained:
        # 旧线上同样的口径：员工必须至少有一个功能，不能发一个空岗位。
        frappe.throw(_(ROLE_REQUIRED))
    cleaned: list[str] = sorted(retained)
    for role in roles:
        role = str(role).strip()
        # 平台角色、管理员角色，以及本站点没有的角色，一律拒绝。
        if role not in allowed and role not in retained:
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        if role not in cleaned:
            cleaned.append(role)
    return cleaned


def _member_roles():
    """Membership survives entitlement/POS changes; the assignable catalog does not."""
    return set(rules.FEATURE_ROLES.values()) | set(menu.roles()) | {rules.LINE_ROLE}


def _tenant_admin():
    return frappe.session.user == "Administrator" or bool(
        set(rules.ALL_RECORDS_ROLES) & guard.current_roles())


def _within_management_scope(user):
    from pearnly_erp_portal.masters import company

    held = {row.role for row in user.roles}
    if user.name in ("Administrator", "Guest") or not held.intersection(_member_roles()):
        return False
    if held.intersection(rules.ALL_RECORDS_ROLES):
        return False
    if _tenant_admin():
        return True
    # An unrestricted target remains unrestricted when new companies are added.
    # A delegated manager may only manage explicitly bounded employee accounts.
    target = set(member_companies(user.name))
    return bool(target and target.issubset(set(company.allowed_companies())))


def _employee(email: str):
    """取一个**可以管理**的员工；老板本人与平台账号不在此列。"""
    if not email or not frappe.db.exists("User", email):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    user = frappe.get_doc("User", email)
    if not _within_management_scope(user):
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    return user


def list_members() -> list[dict]:
    guard.require("team.manage")
    roles = _member_roles()
    emails = sorted(set(frappe.get_all(
        "Has Role", filters={"role": ["in", list(roles | {rules.ADMIN_ROLE})], "parenttype": "User"}, pluck="parent"
    )))
    bindings = _line_bindings(emails)
    members = []
    for email in emails:
        if email == "Administrator":
            continue
        user = frappe.get_doc("User", email)
        admin_row = rules.ADMIN_ROLE in {row.role for row in user.roles}
        if not _within_management_scope(user) and not (admin_row and _tenant_admin()):
            continue
        members.append({
            # `name` 是给列表框架用的行标识，**必须有**：框架在 `BaseList.prepare_data()`
            # 末尾有一句 `this.data = this.data.uniqBy(d => d.name)`，少了这个字段，
            # 整页的人会被当成同一行合并成一行（实测：8 个人只画出 1 行、计数 1 / 8）。
            # 值就是邮箱 —— 与框架里 `User.name` 同一个值，勾选与打开行也按它找行。
            "name": email,
            "email": email,
            # 列表上显示的就是**用户名**（没有才回落邮箱）—— 不再有"姓名"这一栏。
            "username": user.username or "",
            "display": username_of(email),
            "enabled": bool(user.enabled),
            "roles": sorted(r.role for r in user.roles if r.role in roles),
            # 一租户多公司：这个账号被指到哪几家（官方的 User Permission，没指就是全部）。
            "companies": member_companies(email),
            "last_login": user.last_login,
            "is_admin": bool(set(rules.ALL_RECORDS_ROLES) & {r.role for r in user.roles}),
            # LINE：开没开（租户管理员本人默认开），以及绑到了哪个 LINE。绑定码只有本人看得到
            # （`integrations/binding.py`），名单里只给状态。
            "line": admin_row or rules.LINE_ROLE in {r.role for r in user.roles},
            "line_name": (bindings.get(email) or {}).get("display_name") or "",
            "line_bound_at": (bindings.get(email) or {}).get("bound_at"),
        })
    return members


def _line_bindings(emails) -> dict:
    rows = frappe.get_all("Pearnly LINE Binding", filters={"user": ["in", list(emails) or [""]],
                          "status": "active"}, fields=["user", "display_name", "bound_at"])
    return {row.user: row for row in rows}


def _unbind_line(user: str) -> None:
    """取消 LINE = 立即解绑（用户 2026-09-25）：这个 LINE 再发单据会被拒。"""
    from pearnly_core import line_binding

    for line_user_id in frappe.get_all("Pearnly LINE Binding", filters={"user": user, "status": "active"},
                                       pluck="line_user_id"):
        line_binding.unbind(line_user_id)


def add_member(username: str = "", email: str = "", roles=None, password=None, companies=None) -> dict:
    """新增员工：**用户名** + 邮箱 + 初始密码 + 勾选的岗位。

    用户 2026-09-24 的口径："添加用户必须是英文和数字，不区分大小写，长度不限；密码可以自由
    填写，不限制长度，也不限制其它东西；**这个用户名是可以登录的，和邮箱一样**；邮箱和用户名
    是唯一绑定关系。"

    所以这里不再有"姓名"这一栏：**用户名就是这个人**（`User.username`，唯一；同时它也写进
    `first_name`，框架自己那些显示名字的地方（顶栏、邮件、"经手人"）跟着就是用户名）。
    邮箱仍是 `User.name`（账号主键），两边都是一对一：用户名不重、邮箱不重。

    登录那半边靠框架：`User.find_by_credentials` 在 `System Settings.allow_login_using_user_name`
    打开后同时按 `name`（邮箱）与 `username` 找账号 —— 这一位由 patch `v1_9_username_login` 打开。

    账号本身是**框架的 `User`**（复用 Frappe/ERPNext 的用户与权限体系），我们只做三件事：
    建账号、发"本站点确实存在"的功能角色、给一个初始密码让员工能马上登录。
    平台角色（System Manager）与产品管理员角色（ERP Admin）永不外发（`_clean_roles`）。
    """
    guard.require("team.manage")
    email = (email or "").strip().lower()
    if "@" not in email:
        frappe.throw(_(EMAIL_REQUIRED))
    if frappe.db.exists("User", email):
        frappe.throw(_(ALREADY_EXISTS))
    username = _clean_username(username)
    wanted = _clean_roles(roles)
    wanted_companies = _clean_companies(companies)
    secret = _clean_password(password)
    if frappe.conf.get("pearnly_native_account") and not _identity_configured():
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    # 统一身份里的号先建好：建不出来就整件事失败，不留"本地有号、人却登不上"的坑。
    subject = _identity_subject(email, username, secret) if _identity_configured() else None
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        # 用户名既是登录名，也是这个人在全站显示的名字（不再有"姓名"这一栏）。
        "username": username,
        "first_name": username,
        "send_welcome_email": 0,
        "user_type": "System User",
        "roles": [{"role": rules.BASE_ROLE}, *({"role": role} for role in wanted)],
    })
    # 初始密码由老板当面给员工，不走"邀请邮件"那条路（本租户不接邮件通道）。
    if _local_password_allowed():
        user.new_password = secret
    user.insert(ignore_permissions=True)
    if subject:
        _bind_member(user.name, subject)
    _write_companies(user, wanted_companies)
    frappe.db.commit()
    return {"email": user.name, "username": user.username, "roles": sorted(wanted)}


def set_member_password(email: str, password=None) -> dict:
    """重置员工密码（员工忘了密码时老板可以重置；旧密码不需要知道）。

    接了身份服务的站点改的是**身份服务里**那个密码；没有身份服务的站点改本地密码。
    """
    guard.require("team.manage")
    user = _employee(email)
    secret = _clean_password(password)
    if _identity_configured():
        from pearnly_core.account import member_client

        if not _bound_subject(user.name):
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
        member_client.reset_password(user.name, secret)
    elif not _local_password_allowed():
        frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    if _local_password_allowed():
        user.new_password = secret
        user.save(ignore_permissions=True)
    frappe.db.commit()
    return {"email": user.name}


def set_member_roles(email: str, roles=None, companies=None) -> dict:
    """改岗位：只动 ERP 功能角色，其它角色（如 Desk User）保持不动。

    这里**只有岗位**可改：用户名（发号时填的那个名字，就是登录用的身份）和邮箱是
    开户时定死的，租户里谁都不改（用户 2026-09-23："后台管理发号后，用户名和邮箱是
    绑死这个租户的，唯一关系，用户只可以自己改密码，员工也是一样"）。
    """
    guard.require("team.manage")
    user = _employee(email)
    held_roles = {row.role for row in user.roles}
    # Closed features cannot be newly granted, but editing an existing employee
    # must not silently erase their inactive grants.
    wanted = _clean_roles(roles, existing=held_roles.intersection(menu.roles()))
    wanted_companies = _clean_companies(companies) if companies is not None else None
    known = set(rules.FEATURE_ROLES.values()) | set(menu.roles()) | {rules.LINE_ROLE}
    # 只动子表、最后自己 save（`ignore_permissions`）：框架的 `add_roles/remove_roles`
    # 会各自 `save()` 一次、且不带 `ignore_permissions`，在租户站点上会被产品自己的
    # User 权限钩子挡下（非超管只能改自己那条）。直接给 `user.roles` 赋一串 dict 也不行，
    # Frappe 16 落库时拿到 dict 会炸。功能角色全摘掉再按勾选补回来，Desk User 不动。
    for row in list(user.roles):
        if row.role in known:
            user.roles.remove(row)
    # `held` 必须在**摘掉之后**再看：先记再摘的话，"已有岗位"里含着刚被摘掉的那些，
    # 于是"原样保存同一批岗位"会把岗位整个点没（2026-09-23 实测：编辑一个采购账号、
    # 什么都不改直接保存 → 采购岗位消失，这个账号也从员工名单里消失）。
    held = {row.role for row in user.roles}
    for role in wanted:
        if role not in held:
            user.append("roles", {"role": role})
    user.save(ignore_permissions=True)
    if rules.LINE_ROLE in held_roles and rules.LINE_ROLE not in wanted:
        _unbind_line(user.name)
    if wanted_companies is not None:
        _write_companies(user, wanted_companies)
    frappe.db.commit()
    # 角色刚变，清掉该账号的会话缓存；否则它当前会话里仍按旧岗位判权限。
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "roles": sorted(wanted)}


# ── 公司分配（官方 User Permission）────────────────────────────────────────────

def member_companies(email: str) -> list:
    """这个账号被指到哪几家公司：读官方的 `User Permission`（`allow = ERP Company`）。"""
    return sorted(frappe.get_all("User Permission", filters={"user": email, "allow": COMPANY},
                                 pluck="for_value"))


def set_member_companies(email: str, companies=None) -> dict:
    """把账号指到指定的几家公司；**空 = 不限制（看全部）**，跟官方一样。

    用的是框架自己的 `User Permission`：列表、报表、单据都会按它过滤，不用我们另写一套
    隔离。切换公司那颗按钮切的是**用户默认值**，两家都指上的人才能切。
    """
    guard.require("team.manage")
    user = _employee(email)
    wanted = _clean_companies(companies)
    _write_companies(user, wanted)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "companies": sorted(wanted)}


def _clean_companies(companies):
    if not isinstance(companies or [], (list, tuple)):
        frappe.throw(_("Invalid value"))
    wanted = sorted({str(name).strip() for name in (companies or []) if str(name).strip()})
    for name in wanted:
        if not frappe.db.exists(COMPANY, {"name": name, "is_active": 1}):
            frappe.throw(_("Not permitted"), frappe.PermissionError)
    if not _tenant_admin():
        from pearnly_erp_portal.masters import company

        if not wanted or not set(wanted).issubset(set(company.allowed_companies())):
            frappe.throw(_(NOT_PERMITTED), frappe.PermissionError)
    return wanted


def _write_companies(user, wanted):
    for row in frappe.get_all("User Permission", filters={"user": user.name, "allow": COMPANY},
                              pluck="name"):
        frappe.delete_doc("User Permission", row, ignore_permissions=True, force=True)
    for name in wanted:
        frappe.get_doc({"doctype": "User Permission", "user": user.name, "allow": COMPANY,
                        "for_value": name, "apply_to_all_doctypes": 0}).insert(
            ignore_permissions=True)
    # 用户默认值里那家可能已经被摘掉：跟着改，免得留在"看不见的公司"上。
    from frappe.defaults import get_user_default, set_user_default
    current = get_user_default("company", user.name)
    if current and current not in wanted and wanted:
        set_user_default("company", wanted[0], user.name)


def set_member_active(email: str, enabled) -> dict:
    """停用/启用员工。停用后旧会话再次提交同样会被服务端拒绝。"""
    guard.require("team.manage")
    user = _employee(email)
    user.enabled = 1 if enabled in (True, 1, "1", "true", "True") else 0
    user.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache(user=user.name)
    return {"email": user.name, "enabled": bool(user.enabled)}
