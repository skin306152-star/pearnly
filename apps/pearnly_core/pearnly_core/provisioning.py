# -*- coding: utf-8 -*-
"""开通时要在**目标租户站点**上做的事。

为什么放在 App 里而不是写成脚本：`bench console` 会吞多行脚本（连报错都不给），
踩过好几次。这里每个函数都能用**一行** `bench execute` 调起来：

    bench --site <site> execute pearnly_core.provisioning.ensure_local_user \
        --kwargs '{"email": "...", "full_name": "...", "subject": "...", "deployment_id": "..."}'
"""

import frappe
from frappe import _

from pearnly_core import identity


# ── 语言 ────────────────────────────────────────────────────────────────────

# 租户默认语言（用户 2026-09-18 定："新租户默认泰语"）。
#
# 为什么放在这里而不是硬编码进各处：语言是"租户的开通参数"，将来某个租户要中文，
# 只需要在它的站点上写一条 `pearnly_default_language`，不用改代码。没写就是泰语。
#
# 覆盖到两处，缺一个都会"看着像泰语但其实是中文"：
#   1. `System Settings.language` —— 未登录访客与系统兜底走它；
#   2. 新建账号的 `User.language` —— 登录之后一切都看账号自己的语言。
# Frappe 未登录时的取值顺序是 `_lang` → cookie → Accept-Language → 用户 → 系统，
# 所以浏览器语言排在系统默认之前；这是 Frappe 定的，我们不去改它。
TENANT_DEFAULT_LANGUAGE = "th"


def tenant_default_language() -> str:
    """本站点的默认语言。站点配置 `pearnly_default_language` 优先，否则租户默认值。"""
    configured = str(frappe.conf.get("pearnly_default_language") or "").strip()
    return configured or TENANT_DEFAULT_LANGUAGE


def ensure_local_user(email: str, full_name: str, subject: str,
                      deployment_id: str | None = None,
                      role: str = "System Manager") -> dict:
    """建本地账号并绑定身份。幂等。

    三条合同约束：
    1. **不按邮箱合并到已有账号**（P1.04）：已存在就复用，不再新建；
    2. **不给本地密码**：业务用户只走统一身份登录，不允许本地密码旁路；
    3. 已经绑过的身份不重复绑，视为成功（重跑开通不会卡在这一步）。
    """
    email = (email or "").strip().lower()
    if not email:
        frappe.throw(_("邮箱不能为空"))

    created = "exists"
    if not frappe.db.exists("User", email):
        user = frappe.get_doc({
            "doctype": "User",
            "email": email,
            "first_name": (full_name or email.split("@")[0])[:140],
            "enabled": 1,
            "send_welcome_email": 0,
            "user_type": "System User",
            # 新账号默认跟着租户语言走（泰语）。
            # 只在**新建**时设：已有账号不覆盖，否则重跑开通会把用户自己
            # 在界面上改过的语言又顶回去。
            "language": tenant_default_language(),
        })
        user.insert(ignore_permissions=True)
        created = "created"
    else:
        user = frappe.get_doc("User", email)

    if role and role not in [r.role for r in user.roles]:
        user.add_roles(role)

    bound = "already"
    existing = frappe.db.exists(
        "Pearnly Identity", {"issuer": identity.issuer(), "subject": subject}
    )
    if not existing:
        identity.bind_identity(user=email, subject=subject, deployment_id=deployment_id)
        bound = "bound"

    frappe.db.commit()
    return {"user": email, "created": created, "bound": bound, "role": role}


def finalize_site() -> dict:
    """建站收尾：把 desk 首页从 setup-wizard 摘出来、标记设置已完成。

    新建的站点默认带着 `desktop:home_page = setup-wizard`，而"设置向导"页面在
    设置已完成时又会跳回 /desk —— 两头互踢会造成**整页无限重载（闪屏）**。
    这是踩过的坑，详见 docs/p4/03-desk-unstable.md；开通流程必须包含这一步，
    否则新开的租户一登录就落到向导页。

    真值说明（也是最容易改错的地方）：
      "设置已完成"的真正开关是 `Installed Application.is_setup_complete`
      （`frappe/__init__.py:is_setup_complete()` 读的就是它），
      System Settings 上那个同名值只是它的回写，单改那个没有任何效果。
    """
    from frappe.desk.doctype.desktop_icon.desktop_icon import clear_desktop_icons_cache

    frappe.db.sql(
        "update `tabInstalled Application` set is_setup_complete=1 "
        "where app_name in (%s, %s)",
        ("frappe", "erpnext"),
    )
    frappe.db.set_single_value("System Settings", "setup_complete", 1)
    frappe.db.set_default("desktop:home_page", "desktop")

    # 业务站点关掉"本地密码登录"这条旁路。
    # 为什么必须关：站点上的 Administrator 是有密码的，而业务账号在站点上
    # 根本没有密码 —— 不关就等于留了一条"用站点本地密码登录"的绕行路，
    # 与 P3 合同"业务用户无独立本地密码登录旁路"直接冲突。
    # 这个开关同时管住网页表单和 /api/method/login（frappe/auth.py:149）。
    # 控制面是有意用本地账号的（ADR-004），所以用 pearnly_product 区分。
    if str(frappe.conf.get("pearnly_product") or "").strip():
        frappe.db.set_single_value("System Settings", "disable_user_pass_login", 1)
        # 免密码的"邮箱链接登录"也不在统一登录口内，一并关掉
        frappe.db.set_single_value("System Settings", "login_with_email_link", 0)
        # 租户默认语言（见 TENANT_DEFAULT_LANGUAGE 的说明）。
        # 只给业务站点设：控制面是老板自己用的，保持原样。
        frappe.db.set_single_value("System Settings", "language", tenant_default_language())
    frappe.db.commit()

    frappe.clear_cache()
    clear_desktop_icons_cache()

    home_page = frappe.db.get_default("desktop:home_page")
    done = frappe.is_setup_complete()
    if home_page != "desktop" or not done:
        frappe.throw(
            f"收尾没成功：desktop:home_page={home_page!r} is_setup_complete={done}"
        )
    return {
        "home_page": home_page,
        "is_setup_complete": done,
        "language": frappe.db.get_single_value("System Settings", "language"),
    }


# ── 功能权限落地 ────────────────────────────────────────────────────────────

# 授予功能角色的 DocType 权限：业务功能要能读写，但**不给删除**——
# 业务单据删掉就没了，要给租户管理员留一条"只能作废不能抹掉"的习惯。
FEATURE_PERMS = {"read": 1, "write": 1, "create": 1, "print": 1, "email": 1, "report": 1}

# 产品管理员角色拿到的权限：能管本产品的员工账号，**但碰不到系统设置**。
ADMIN_DOCTYPES = ["User", "Role", "User Permission", "Contact"]
ADMIN_PERMS = {"read": 1, "write": 1, "create": 1, "email": 1, "report": 1}


def ensure_feature_roles(spec_json: str, admin_user: str = "") -> dict:
    """按"勾了哪些功能"在**本站点**建角色、给权限、发给租户管理员。

    这是"未勾选的功能不可访问"能真正成立的地方 —— 不做这一步，勾选只是记录，
    没有任何约束力。

    为什么要拿走 System Manager：那是 Frappe 的框架超管角色，能改系统设置、
    装应用、看所有配置。租户管理员要的是"管自己的业务"，不是"管这台机器"。
    拿走之后他只有：产品管理员角色 + 买了的功能角色 + 基础角色（Desk User）。

    `spec_json` 形状见控制面的 `pearnly_control.features.roles_payload`。

    为什么收 JSON **字符串**而不是 dict：调用方是 `bench execute`，它对
    `--kwargs` 做的是 Python `eval`（`frappe/commands/utils.py:286`），
    传嵌套 dict 时一旦里面出现 `true/false/null` 就炸；更糟的是它把真实异常
    吞掉后换成一句 `NameError: name 'pearnly_core' is not defined`（踩过）。
    传字符串绕开整套 eval 语义。
    """
    import json

    spec = json.loads(spec_json) if spec_json else {}
    admin_user = admin_user or spec.get("admin_user") or ""
    product = spec.get("product") or ""
    admin_role = spec.get("admin_role") or ""
    base_roles = list(spec.get("base_roles") or [])
    feature_roles = spec.get("roles") or []

    created_roles = []
    for role_name, doctypes, perms in (
        [(admin_role, ADMIN_DOCTYPES, ADMIN_PERMS)] if admin_role else []
    ) + [(r["role"], r["doctypes"], FEATURE_PERMS) for r in feature_roles]:
        if not role_name:
            continue
        _ensure_role(role_name)
        created_roles.append(role_name)
        for doctype in doctypes or []:
            if frappe.db.exists("DocType", doctype):
                _grant(doctype, role_name, perms)

    # 换掉租户管理员的角色组合
    user = frappe.get_doc("User", admin_user)
    want = set(base_roles) | ({admin_role} if admin_role else set())
    want |= {r["role"] for r in feature_roles}
    have = {r.role for r in user.roles}

    for role in want - have:
        if frappe.db.exists("Role", role):
            user.append("roles", {"role": role})
    # System Manager 必须拿走；其它多余角色也一并收回，避免"发出去忘了收"
    drop = {"System Manager"} | (have - want - {"All", "Guest"})
    removed = sorted(drop & have)
    user.roles = [r for r in user.roles if r.role not in drop]
    if removed or (want - have):
        user.save(ignore_permissions=True)
        frappe.db.commit()

    frappe.clear_cache(user=admin_user)
    return {
        "user": admin_user,
        "roles": sorted(want),
        "removed": removed,
        "created_roles": sorted(set(created_roles)),
    }


def _ensure_role(role_name: str) -> None:
    if frappe.db.exists("Role", role_name):
        return
    frappe.get_doc({
        "doctype": "Role",
        "role_name": role_name,
        "desk_access": 1,
    }).insert(ignore_permissions=True)
    frappe.db.commit()


def _grant(doctype: str, role: str, perms: dict) -> None:
    """给角色加一条 DocType 权限。已有就更新，不重复建（幂等）。"""
    from frappe.permissions import setup_custom_perms

    # 这一步会把标准权限复制成自定义权限；否则新增的角色权限会被标准权限盖掉
    setup_custom_perms(doctype)

    existing = frappe.db.get_value(
        "Custom DocPerm",
        {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
        "name",
    )
    if existing:
        doc = frappe.get_doc("Custom DocPerm", existing)
        for key, value in perms.items():
            doc.set(key, value)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Custom DocPerm",
            "__islocal": 1,
            "parent": doctype,
            "parenttype": "DocType",
            "parentfield": "permissions",
            "role": role,
            "permlevel": 0,
            **perms,
        }).save(ignore_permissions=True)
    frappe.db.commit()


def ensure_product_workspace(spec_json: str) -> dict:
    """给这个租户建一个只列**已购功能**的工作区。

    为什么必须有：租户管理员不再有 System Manager（框架超管），Frappe 自带那些
    工作区对他不可见，打开 Desk 会是一片空白（实测过）。产品自己的界面还没做，
    所以先给一个"只列他买了什么"的工作区 —— 既有落点，也一眼看得出买了哪些功能。

    工作区是**公开**的（public=1），但可见性靠 roles 限定在"本租户的角色"上，
    所以不会串到别人家。
    """
    import json

    spec = json.loads(spec_json) if spec_json else {}
    title = spec.get("title") or "工作区"
    icon = spec.get("icon") or "folder"
    entries = spec.get("entries") or []
    roles = list(spec.get("roles") or [])

    links = []
    for entry in entries:
        links.append({
            "type": "Card Break",
            "label": entry["label"],
            "icon": icon,
        })
        links.append({
            "type": "Link",
            "label": f"{entry['label']} 列表",
            "link_type": "DocType",
            "link_to": entry["doctype"],
            "dependencies": "",
            "is_query_report": 0,
        })

    if frappe.db.exists("Workspace", title):
        doc = frappe.get_doc("Workspace", title)
        doc.set("links", [])
        doc.set("roles", [])
    else:
        doc = frappe.get_doc({"doctype": "Workspace", "title": title, "public": 1})

    doc.public = 1
    doc.icon = icon
    # 模块必须是**该产品自己的模块**：Frappe 用
    # `workspace.module in user.allowed_modules` 判断可见性，租户用户被允许的
    # 只有他所用产品 App 的模块。写成核心层模块会被判无权限直接丢掉（踩过）。
    doc.module = spec.get("module") or "Pearnly Core"
    doc.label = title
    doc.sequence_id = 1.0
    for link in links:
        doc.append("links", link)
    for role in roles:
        if frappe.db.exists("Role", role):
            doc.append("roles", {"role": role})
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    frappe.clear_cache()

    # 桌面上给它一个图标，否则登录后只在侧栏看得到
    _ensure_workspace_icon(title, icon, spec.get("product") or "")
    _clear_desktop_caches()
    return {"workspace": doc.name, "title": title, "links": len(entries), "roles": roles}


def _clear_desktop_caches() -> None:
    """清掉**所有用户**的桌面图标与 boot 缓存。

    坑：Frappe 的 `clear_desktop_icons_cache()` 不带参数时只清**当前会话用户**
    （`user or frappe.session.user`）。开通是由 bench 以 Administrator 身份跑的，
    所以租户用户那份"什么都没有"的旧缓存会一直留着 —— 表现为数据都建好了，
    租户登录进去还是**一片空白**（实测踩到）。开通这种低频动作上，整体清掉最稳。
    """
    frappe.clear_cache()
    for key in ("desktop_icons", "bootinfo"):
        try:
            # 必须用 delete_key：这两个键是 **hash**，而 `cache.delete(key)` 对
            # hash 无效**且不报错**（返回 0，键原样留着）—— silent no-op，
            # 所以不能靠 try/except 兜底（踩过）。
            frappe.cache.delete_key(key)
        except Exception:
            frappe.log_error(title="清理桌面缓存失败", message=key)


def _ensure_workspace_icon(title: str, icon: str, product: str = "") -> None:
    # 桌面图标走 "Workspace Sidebar" 类型，而它的 link_to 是**动态链接**：
    # 必须指向一个真实存在的 Workspace Sidebar 记录（不是 Workspace！），
    # 否则报 `LinkValidationError: Could not find Link To: <标题>`（踩过）。
    _ensure_workspace_sidebar(title, icon)
    payload = {
        "icon_type": "Link",
        "link_type": "Workspace Sidebar",
        "link_to": title,
        "icon": icon,
        "app": "pearnly_core",
        "standard": 0,
        "hidden": 0,
    }
    # 桌面图标取图的顺序是：按标签找 App 自带 SVG → logo_url/icon_image → 画标签首字母。
    # 我们的标签是**租户名**（动态、且是中文），前两条都走不通，第三条对中文
    # 会画成一个空白方块（踩过，控制面那次同因）。所以显式给 logo_url。
    if product:
        payload["logo_url"] = f"/assets/pearnly_core/icons/desktop_icons/{product}.svg"
    name = frappe.db.exists("Desktop Icon", title)
    if name:
        doc = frappe.get_doc("Desktop Icon", name)
        for key, value in payload.items():
            doc.set(key, value)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({"doctype": "Desktop Icon", "label": title, **payload}).insert(
            ignore_permissions=True
        )
    frappe.db.commit()


def _ensure_workspace_sidebar(title: str, icon: str) -> None:
    """给这个工作区配一个同名侧边栏。

    侧边栏第一项决定点桌面图标后落到哪 —— 这里让它打开**工作区本身**，
    而不是直接落到某个列表。
    """
    items = [{
        "doctype": "Workspace Sidebar Item",
        "type": "Link",
        "label": title,
        "link_type": "Workspace",
        "link_to": title,
    }]
    if frappe.db.exists("Workspace Sidebar", title):
        doc = frappe.get_doc("Workspace Sidebar", title)
        doc.header_icon = icon
        doc.set("items", [])
        for item in items:
            doc.append("items", item)
        doc.save(ignore_permissions=True)
    else:
        frappe.get_doc({
            "doctype": "Workspace Sidebar",
            "title": title,
            "header_icon": icon,
            "app": "pearnly_core",
            "items": items,
        }).insert(ignore_permissions=True)
    frappe.db.commit()
