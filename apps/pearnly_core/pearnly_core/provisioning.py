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

# 租户默认时区。
#
# 为什么必须显式设：Frappe 装出来的站点默认时区是 **Asia/Kolkata**（实测：租户站点
# 的 System Settings 就是它）。业务在泰国，差 1.5 小时会让所有"时间"看起来都不对 ——
# 列表里的创建时间、单据时间、以及**倒计时**（绑定码有效期）全都会偏。
# 绑定码那次就是被它咬到的：服务端给的是 IST 的字符串，浏览器按 +07 解析，
# 算出来"已经过期"。倒计时另外改成了用秒数传（见 `line_binding.generate_bind_code`），
# 但显示时间这件事只能靠时区本身对。
#
# 与语言同理，可以用站点配置 `pearnly_default_timezone` 覆盖。
TENANT_DEFAULT_TIMEZONE = "Asia/Bangkok"


def tenant_default_language() -> str:
    """本站点的默认语言。站点配置 `pearnly_default_language` 优先，否则租户默认值。"""
    configured = str(frappe.conf.get("pearnly_default_language") or "").strip()
    return configured or TENANT_DEFAULT_LANGUAGE


def tenant_default_timezone() -> str:
    """本站点的默认时区。站点配置 `pearnly_default_timezone` 优先。"""
    configured = str(frappe.conf.get("pearnly_default_timezone") or "").strip()
    return configured or TENANT_DEFAULT_TIMEZONE


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
        frappe.throw(_("Email is required"))

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
        # 站点侧的密码强度开关也关掉（Frappe 原生 `enable_password_policy`）。
        # 用户要求"密码不要卡得太严格，纯字母、纯数字都可以，包括首次使用"：
        # 业务密码本来存在身份服务里（那边没有策略），但只要有一步会落到站点的
        # 原生表单（例如租户自己加员工账号时用 Desk 的表单），这个开关就会把人
        # 挡在门外。控制面早就关了同一个开关（见 pearnly_control/bootstrap.py），
        # 两边行为保持一致。
        frappe.db.set_single_value("System Settings", "enable_password_policy", 0)
        # 租户默认语言（见 TENANT_DEFAULT_LANGUAGE 的说明）。
        # 只给业务站点设：控制面是老板自己用的，保持原样。
        frappe.db.set_single_value("System Settings", "language", tenant_default_language())
        # 租户默认时区（见 TENANT_DEFAULT_TIMEZONE 的说明）。
        # 不设就会留在 Frappe 的默认值 Asia/Kolkata 上，泰国业务差 1.5 小时。
        frappe.db.set_single_value("System Settings", "time_zone", tenant_default_timezone())
    frappe.db.commit()

    frappe.clear_cache()
    clear_desktop_icons_cache()

    home_page = frappe.db.get_default("desktop:home_page")
    done = frappe.is_setup_complete()
    if home_page != "desktop" or not done:
        frappe.throw(
            _("Finalize failed: desktop:home_page={0} is_setup_complete={1}").format(
                home_page, done
            )
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
    其中每个功能角色可以带 `perms_by_doctype`，给某张表单独换个权限档
    （DMS 的身份证录入表在网页端是**只读**的 —— 录入在 LINE 上做）。

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

    # 把"角色 → 表 → 权限"摊平。功能角色默认拿 FEATURE_PERMS，但每张表都可以被
    # `perms_by_doctype` 覆盖（例如 DMS 那张表在网页端只读，见控制面的 features.py）。
    grants: list[tuple[str, str, dict]] = []
    if admin_role:
        grants += [(admin_role, doctype, ADMIN_PERMS) for doctype in ADMIN_DOCTYPES]
    for row in feature_roles:
        overrides = row.get("perms_by_doctype") or {}
        for doctype in row.get("doctypes") or []:
            grants.append((row["role"], doctype, overrides.get(doctype) or FEATURE_PERMS))

    created_roles = []
    for role_name, doctype, perms in grants:
        if not role_name:
            continue
        _ensure_role(role_name)
        created_roles.append(role_name)
        if frappe.db.exists("DocType", doctype):
            _grant(doctype, role_name, perms)

    # 换掉租户管理员的角色组合
    user = frappe.get_doc("User", admin_user)

    # 保存用户之前先清掉悬空的默认工作区。不清的话这里会抛
    # `LinkValidationError: Could not find <默认工作区>`，而这一步跟工作区毫无关系 ——
    # 报错完全指不到真正的原因（`bench migrate` 会删掉生成的工作区）。
    from pearnly_core.workspace import clear_dangling_default_workspace

    if clear_dangling_default_workspace(admin_user):
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


# 授权时**整份**写这些字段。
#
# 为什么不能只写"给定的那几个键"：更新已有权限时只 set 给定的键，上一版给过的
# 权限会**残留**下来。实测过：把 DMS 那张表改成只读之后，`create` 还是 1，
# 网页上"新增"按钮照旧出现 —— 因为上一次授权把它设成了 1，这次没提它。
# 所以每次授权都把整组字段写满，让"这次给的"就是"最终生效的"。
PERM_FIELDS = (
    "read", "write", "create", "delete", "submit", "cancel", "amend",
    "report", "export", "import", "share", "print", "email",
)


def _grant(doctype: str, role: str, perms: dict) -> None:
    """给角色加一条 DocType 权限。已有就更新，不重复建（幂等）。"""
    from frappe.permissions import setup_custom_perms

    # 这一步会把标准权限复制成自定义权限；否则新增的角色权限会被标准权限盖掉
    setup_custom_perms(doctype)

    wanted = {key: (1 if perms.get(key) else 0) for key in PERM_FIELDS}
    existing = frappe.db.get_value(
        "Custom DocPerm",
        {"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
        "name",
    )
    if existing:
        doc = frappe.get_doc("Custom DocPerm", existing)
        for key, value in wanted.items():
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
            **wanted,
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
    title = spec.get("title") or _("Workspace")
    icon = spec.get("icon") or "folder"
    entries = spec.get("entries") or []
    roles = list(spec.get("roles") or [])

    # 入口行（卡片下面那一条）的文案 = 卡片名本身，**不拼"列表"后缀**。
    #
    # 拼后缀会写出第二份文案：这一段是**存进工作区内容的数据**，渲染时只做
    # `__(label)` 精确查表，拼出来的 "Push Records 列表" 在翻译表里查不到，
    # 于是中文永远跟着英文一起显示（2026-09-19 实测：卡片标题已经是泰文，
    # 下面那条还是 "Push Records 列表"）。
    # 不用后缀还有第二个好处：跟框架自带工作区一致 —— 那边的卡片标题是分组名，
    # 下面列的是条目自己的名字，没有后缀这种自造格式。
    links = []
    for entry in entries:
        links.append({
            "type": "Card Break",
            "label": entry["label"],
            "icon": icon,
        })
        links.append({
            "type": "Link",
            "label": entry["label"],
            "link_type": "DocType",
            "link_to": entry["doctype"],
            "dependencies": "",
            "is_query_report": 0,
        })

    # 工作台是按 `content` 里的"方块"画出来的，**光有 links 不够**。
    # content 为空时页面只剩一句"欢迎来到 XXX"，租户看到的是个空工作台
    # （2026-09-18 实测：默认落点改成工作台之后才暴露出来）。
    # 每个 Card Break（上面那组链接的标题）对应一张卡片；一行三张，跟框架自带工作区一样。
    blocks = [
        {
            "id": frappe.generate_hash(length=10),
            "type": "card",
            "data": {"card_name": entry["label"], "col": 4},
        }
        for entry in entries
    ]

    if frappe.db.exists("Workspace", title):
        doc = frappe.get_doc("Workspace", title)
        doc.set("links", [])
        doc.set("roles", [])
    else:
        doc = frappe.get_doc({"doctype": "Workspace", "title": title, "public": 1})

    doc.public = 1
    doc.icon = icon
    doc.content = frappe.as_json(blocks)
    # ⚠️ **module 必须留空**，两条理由（都是实测出来的，不是猜的）：
    #
    # 1. **填了会被 migrate 删掉**。`frappe/model/sync.py:remove_orphan_entities()`
    #    清的是"`public=1` 且 `module` 有值 且 `app` 有值、但在 App 目录里没有对应
    #    json 文件"的 Workspace；而 `Workspace.validate()` 会自动由 module 推出 app。
    #    我们的工作区是**每个租户按需生成**的，永远不可能在 App 目录里有文件 ——
    #    所以只要填了 module，跑一次 `bench migrate` 就没了。实测：跑完 migrate 后
    #    工作区消失，而 `User.default_workspace` 还指着它，于是保存任何用户都会报
    #    `LinkValidationError: Could not find พื้นที่ทำงานเริ่มต้น: <租户工作区>`。
    # 2. **留空反而更稳**。可见性在 `frappe/desk/desktop.py` 的 `Workspace.__init__`：
    #    `if self.doc.module and self.doc.module not in self.allowed_modules: PermissionError`。
    #    module 为空 → 这一条整个跳过 → 不会因为"模块不在该用户的允许模块里"被挡。
    #    （反过来说，填核心层模块会被判无权限丢掉 —— 这是更早踩过的坑。）
    #
    # 工作区名字因此不能靠 module 反查，改用站点配置里的 `pearnly_workspace`
    # （见 `pearnly_core/workspace.site_product_workspace`）。
    doc.module = ""
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

    # 桌面上给它一个图标。这一屏已经不再是租户的落点（见下），但图标留着：
    # 工作台侧边栏顶部的返回入口、以及将来真要回桌面时都还用得到。
    _ensure_workspace_icon(title, icon, spec.get("product") or "")
    _clear_desktop_caches()

    # 把"这家买到了哪些功能"落到站点配置上 —— 前端渲染菜单要用
    # （`pearnly_core.shell.describe` 读它）。放这一步是因为它本来就拿着这份清单：
    # 工作区里要列什么，就是买到的那些功能；同一件事不拆成两步。
    # 权威仍在控制面的发号单上，这里只是一份给站点自己看的投影。
    codes = [str(code) for code in (spec.get("features") or []) if code]
    if codes:
        # 注意导入方式：写成 `frappe.installer.update_site_config(...)` 会炸，
        # 因为 `frappe.installer` 不是自动挂上去的属性。而且炸出来的报错会被
        # `bench execute` 的 kwargs eval 吞掉，换成一句无关的
        # `NameError: name 'pearnly_core' is not defined` —— 极难定位（实测踩到）。
        from frappe.installer import update_site_config

        update_site_config("pearnly_features", codes)

    # 工作区名字记到站点配置里。为什么不能靠 module 反查：工作区的 module 必须为空
    # （否则 migrate 会删掉它，见上面的长注释），而 `site_product_workspace()`
    # 原来就是按 module 找的 —— 换成一个我们自己写、自己读的键，两边才一致。
    from frappe.installer import update_site_config

    update_site_config("pearnly_workspace", doc.name)

    # 让租户登录后**直接落到这个工作台**，不要先过一屏"桌面"再点一次。
    #
    # ⚠️ 顺序：必须在**两个站点配置都写完之后**再调 `apply_to_site_users()`。
    # 它内部靠 `site_product_workspace()` 找本站点的工作区，而工作区的 module
    # 是空的（见上），所以只能靠 `pearnly_workspace` 这个配置找 —— 先调它就会
    # 得到"本站点还没有产品工作区"，落点也就没设上（实测踩到）。
    from pearnly_core import workspace as workspace_mod

    landed = workspace_mod.apply_to_site_users()

    return {
        "workspace": doc.name,
        "title": title,
        "links": len(entries),
        "roles": roles,
        "features": codes,
        "landing": landed,
    }


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
