# -*- coding: utf-8 -*-
"""控制面的桌面入口：在原生桌面（那些图标）里加一个「租户开通」。

用的是什么原生机制（都从源码核实过，不是猜的）：

1. 桌面那张卡片里的每个图标 = 一条 `Desktop Icon` 单据。
   `frappe/desk/page/desktop/desktop.js:get_route()` 决定点击后去哪：
   - `link_type == "External"` → 拼成 `origin + link`，而且因为拼出来以 http
     开头，源码会把图标标成 `target="_blank"`，**强制新开标签页**；
   - `link_type == "Workspace Sidebar"` → 按 `label.toLowerCase()` 找侧边栏，
     取其中第一个 Link 项生成**内部路由**（同标签跳转）。
   我们要的是内部页面，所以走后者，并配一个 `Workspace Sidebar`。

2. 可见性（很重要，踩点）：
   `desktop_icon.py:get_desktop_icons()` 的查询条件是
       standard == 1  或  (standard == 0 且 owner ∈ {"Administrator", 当前用户})
   也就是说**非 standard 的图标默认只有创建者自己能看见**。这里用站点管理员身份
   建（bench 执行的 session user 就是 Administrator），owner 即 Administrator，
   所以所有 Desk 用户都能看见。
   不设 `standard=1` 是因为 standard 图标会被 migrate 的
   "Removing orphan Desktop Icons" 当成孤儿（它不在任何 app 的 fixtures 里）而删掉。

3. 桌面图标的样子**不是**由 `icon` 字段决定的。桌面模板
   （`frappe/public/js/frappe/ui/desktop_icon.html`）按这个顺序取图：
     a. `frappe.utils.get_desktop_icon(label, style)` —— 找
        `assets/<app>/icons/desktop_icons/<style>/<frappe.scrub(label)>.svg`，
        app 取图标自己的 `app` 字段。（原生那些图标走的就是这条）
     b. 没有就退回 `logo_url` / `icon_image`
     c. 再没有就画**标签首字母**（从 alphabets.svg 取字形）+ 底色方块
   本 App 自带一个 SVG：`public/icons/desktop_icons/subtle/tenant_provisioning.svg`
   （尺寸与配色照抄原生：54×54，底 #7B808A/19%，字 #6B6F78）。
   历史：标签原来是中文（"租户开通"），而 `frappe.scrub()` 对中文原样保留、
   字母表里又没有汉字，所以那时 (c) 走不通、必须自带 SVG（踩过）。现在标签是英文，
   (c) 也能画出首字母了；SVG 留着不影响（本地样例站点没挂 assets，就走的首字母）。

幂等：每次 migrate 后重跑，已存在则更新，不会重复建。
"""

import frappe

# 侧边栏的名字。**这个值是存进库的记录名**，渲染时框架不翻它
# （`frappe/public/js/frappe/ui/sidebar/sidebar_header.js` 直接把 title 打进 DOM），
# 所以按 Frappe 的惯例用英文 —— 与它下面的 "Pearnly Control" 一致。
# 菜单项不一样：`sidebar_item.js:393` 会 `__(item.label)`，所以项写英文 msgid 就能跟着语言走。
# 用户 2026-09-19 定的规矩是"不许写死文案"，这里的取舍是"名字用英文、项走翻译包"。
SIDEBAR_TITLE = "Tenant Provisioning"
ICON = "building-2"

# 改名前的旧名字。库里已经有这条记录的话要清掉，否则侧边栏会多出一个旧的（中文）条目。
LEGACY_TITLES = ("租户开通",)

# 侧边栏里的项。**第一项必须是 Link**，因为它决定点桌面图标后落到哪。
SIDEBAR_ITEMS = [
    {"label": "Issue", "link_type": "DocType", "link_to": "Pearnly Issue"},
    {"label": "Tenant", "link_type": "DocType", "link_to": "Pearnly Tenant"},
    {"label": "Feature Catalog", "link_type": "DocType", "link_to": "Pearnly Feature"},
    {"label": "Provisioning Target", "link_type": "DocType", "link_to": "Pearnly Target"},
    {"label": "Deployment", "link_type": "DocType", "link_to": "Pearnly Deployment"},
    {"label": "LINE Channel", "link_type": "DocType", "link_to": "Pearnly LINE Channel"},
    {"label": "Provisioning Progress", "link_type": "DocType", "link_to": "Pearnly Provisioning Request"},
    # 计费底座：余额与台账在控制面，三套产品共用一份（表归属见 P0.05）
    {"label": "Top-up Review", "link_type": "DocType", "link_to": "Pearnly Topup Request"},
]


def ensure_shortcuts() -> dict:
    """建/更新「租户开通」的侧边栏与桌面图标。幂等。"""
    sidebar = _ensure_sidebar()
    icon = _ensure_desktop_icon()
    removed = _drop_legacy()
    frappe.clear_cache()
    return {"sidebar": sidebar, "desktop_icon": icon, "removed": removed}


def _drop_legacy() -> list:
    """清掉改名前的旧侧边栏与旧桌面图标。

    不清的后果：侧边栏下拉里会同时出现中英两条（旧的那条指向同样的页面，
    看着像重复菜单）。这两条记录都是本模块生成的，不是用户数据，所以直接删。
    """
    removed = []
    for old in LEGACY_TITLES:
        if old == SIDEBAR_TITLE:
            continue
        for doctype in ("Workspace Sidebar", "Desktop Icon"):
            if frappe.db.exists(doctype, old):
                frappe.delete_doc(doctype, old, ignore_permissions=True, force=True)
                removed.append("%s:%s" % (doctype, old))
    if removed:
        frappe.db.commit()
    return removed


def _ensure_sidebar() -> str:
    items = [
        {"doctype": "Workspace Sidebar Item", "type": "Link", **item}
        for item in SIDEBAR_ITEMS
    ]
    if frappe.db.exists("Workspace Sidebar", SIDEBAR_TITLE):
        doc = frappe.get_doc("Workspace Sidebar", SIDEBAR_TITLE)
        doc.header_icon = ICON
        doc.app = "pearnly_control"
        doc.set("items", [])
        for item in items:
            doc.append("items", item)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({
            "doctype": "Workspace Sidebar",
            "title": SIDEBAR_TITLE,
            "header_icon": ICON,
            "app": "pearnly_control",
            "items": items,
        })
        doc.insert(ignore_permissions=True)
    frappe.db.commit()
    return doc.name


def _ensure_desktop_icon() -> str:
    payload = {
        "icon_type": "Link",
        "link_type": "Workspace Sidebar",
        "link_to": SIDEBAR_TITLE,   # 与 label 一致，get_route 是按 label 找侧边栏的
        "icon": ICON,
        # bg_color 只在"没找到图标、退回画首字母"时才用得上。中文标签走不通那条路，
        # 图标由图标的 SVG 提供，所以这里跟原生一样留空（默认灰）。
        "bg_color": None,
        "app": "pearnly_control",
        # 明确非 standard：避免被 migrate 当孤儿图标删掉
        "standard": 0,
        "hidden": 0,
    }
    name = frappe.db.exists("Desktop Icon", SIDEBAR_TITLE)
    if name:
        doc = frappe.get_doc("Desktop Icon", name)
        for key, value in payload.items():
            doc.set(key, value)
        doc.save(ignore_permissions=True)
    else:
        doc = frappe.get_doc({"doctype": "Desktop Icon", "label": SIDEBAR_TITLE, **payload})
        doc.insert(ignore_permissions=True)
    frappe.db.commit()

    # 桌面图标缓存是按用户存的，改完要清掉，否则界面还是旧的
    from frappe.desk.doctype.desktop_icon.desktop_icon import clear_desktop_icons_cache

    clear_desktop_icons_cache()
    return doc.name
