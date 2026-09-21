"""Product navigation in native Workspace Sidebar, without granting new roles.

图标名必须是**精灵里真有的**：非 `es-` 开头的走 `#icon-<name>`（lucide 精灵），
`es-` 开头的走 `#<name>`（espresso 精灵），都不存在时渲染出来是空的 ——
所以下面每个名字都在 `frappe/public/icons/lucide.svg` 里核对过。

分组用原生的 `Section Break`（父）+ `child=1`（子）：框架的
`Sidebar.find_nested_items()` 就是按这两者嵌套的，不需要自建折叠控件。
"""
import json

import frappe
from frappe import _

# 一条导航项：(文案键, 路由, 图标, 功能码或 None)。
Page = tuple

PAGES = {
    'dms': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.connect', 'pearnly-connect', 'plug', None),
            ('nav.records', 'pearnly-records', 'list', 'push_record'),
            ('nav.roster', 'pearnly-roster', 'users', 'roster')],
    'cowork': [('nav.home', 'pearnly-home', 'house', None),
               ('nav.work', 'pearnly-work', 'folder-kanban', 'boards'),
               ('nav.intake', 'pearnly-intake', 'scan', 'intake'),
               ('nav.history', 'pearnly-history', 'list', 'ocr_history'),
               ('nav.pushLogs', 'pearnly-push-logs', 'list', 'push_log'),
               ('nav.reconcile', 'pearnly-reconcile', 'check', 'recon'),
               ('nav.stocktake', 'pearnly-stocktake', 'boxes', 'stocktake'),
               ('nav.clients', 'pearnly-clients', 'users', 'master_data'),
               ('nav.company', 'pearnly-company', 'building-2', 'master_data'),
               ('nav.team', 'pearnly-team', 'users', 'master_data'),
               ('nav.integrations', 'pearnly-integrations', 'plug', 'master_data'),
               ('nav.guide', 'pearnly-guide', 'circle-help', 'express_guide')],
    'erp': [('nav.home', 'pearnly-home', 'house', None),
            ('nav.products', 'pearnly-products', 'package', 'products'),
            ('nav.inventory', 'pearnly-inventory', 'warehouse', 'warehouses'),
            ('nav.stockIn', 'pearnly-stock-in', 'download', 'warehouses'),
            ('nav.stockOut', 'pearnly-stock-out', 'upload', 'warehouses'),
            ('nav.stockCard', 'pearnly-stock-card', 'list-checks', 'stockcard'),
            ('nav.stockCount', 'pearnly-stock-count', 'boxes', 'stocktake'),
            ('nav.purchase', 'pearnly-purchase', 'shopping-cart', 'purchase'),
            ('nav.suppliers', 'pearnly-suppliers', 'store', 'purchase'),
            ('nav.purchaseSettings', 'pearnly-buy-settings', 'sun', 'purchase'),
            ('nav.sales', 'pearnly-sales', 'receipt-text', 'sales'),
            ('nav.erpClients', 'pearnly-clients', 'user', 'clients'),
            ('nav.companyMaster', 'pearnly-company-master', 'users', None),
            ('nav.company', 'pearnly-company', 'contact', None),
            ('nav.team', 'pearnly-team', 'users-round', None),
            ('nav.integrations', 'pearnly-integrations', 'plug', None)],
}

# ERP 侧栏按截图分组：父项是分组标题（原生 Section Break），子项是上面的页面。
# 没在分组里的（首页）留在最上面，不进任何组。
GROUPS = {
    'erp': [
        ('nav.group.goodsStock', 'boxes',
         ['pearnly-products', 'pearnly-inventory', 'pearnly-stock-in', 'pearnly-stock-out',
          'pearnly-stock-card', 'pearnly-stock-count']),
        ('nav.group.purchasing', 'shopping-cart',
         ['pearnly-purchase', 'pearnly-suppliers', 'pearnly-buy-settings']),
        ('nav.group.sales', 'receipt-text', ['pearnly-sales', 'pearnly-clients']),
        ('nav.group.master', 'building-2',
         ['pearnly-company-master', 'pearnly-company', 'pearnly-team',
          'pearnly-integrations']),
    ],
}


# Only working pages are offered. The catalog above preserves future feature ownership.
# 每做完一页就把它加进来 —— 侧栏是用户唯一能点到页面的地方，没加就是"放出来了但点不开"。
AVAILABLE_PAGES = {"pearnly-home", "pearnly-connect", "pearnly-roster", "pearnly-records",
                   "pearnly-intake", "pearnly-purchase", "pearnly-products",
                   "pearnly-inventory", "pearnly-stock-count"}

# 页面 → 判权用的 DocType。没有映射的页面由它自己的接口判权（页面加载后第一请求会拒）。
PAGE_DOCTYPE = {
    "pearnly-intake": "Intake Record",
    "pearnly-purchase": "Purchase Document",
    "pearnly-products": "ERP Product",
    # 在库一览属于库存岗位（旧线上商品岗位看不到），所以按结存表判权。
    "pearnly-inventory": "ERP Stock Balance",
    "pearnly-stock-count": "ERP Stock Count",
}



def _can_open_page(route):
    """Mirror the existing endpoint permission; purchased features alone are not a role."""
    if route in {"pearnly-connect", "pearnly-roster", "pearnly-records"}:
        return bool({"DMS Admin", "System Manager"} & set(frappe.get_roles()))
    doctype = PAGE_DOCTYPE.get(route)
    return not doctype or frappe.has_permission(doctype, "read")



def visible_pages(product):
    from pearnly_core.shell import entitlements
    features = set(entitlements())
    return [route for _, route, _, feature in PAGES.get(product, [])
            if route in AVAILABLE_PAGES and (not feature or feature in features) and _can_open_page(route)]


def _personal_order(product, pages):
    raw = frappe.defaults.get_user_default("pearnly_sidebar_order_" + product)
    try:
        saved = json.loads(raw or "[]")
    except (ValueError, TypeError):
        saved = []
    if not isinstance(saved, list):
        saved = []
    order = []
    for route in saved + pages:
        if isinstance(route, str) and route in pages and route not in order:
            order.append(route)
    return order


@frappe.whitelist(methods=["POST"])
def save_sidebar_order(routes):
    product = frappe.conf.get("pearnly_product")
    if frappe.session.user == "Guest" or product not in PAGES:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if isinstance(routes, str):
        try:
            routes = json.loads(routes)
        except ValueError:
            frappe.throw(_("Invalid value"))
    allowed = visible_pages(product)
    if (not isinstance(routes, list) or not all(isinstance(r, str) for r in routes)
            or len(routes) != len(allowed) or set(routes) != set(allowed)):
        frappe.throw(_("Invalid value"))
    # User Default is per-site and per-session user. No shared sidebar or role writes.
    frappe.defaults.set_user_default("pearnly_sidebar_order_" + product, json.dumps(routes))
    return {"routes": routes}


def ensure_navigation():
    from pearnly_core.shell import entitlements, PRODUCT_LABELS
    product = frappe.conf.get("pearnly_product")
    if product not in PAGES:
        return
    title = "Pearnly " + PRODUCT_LABELS[product]
    features = set(entitlements())
    items = _sidebar_items(product, features)
    doc = (frappe.get_doc("Workspace Sidebar", title) if frappe.db.exists("Workspace Sidebar", title)
           else frappe.new_doc("Workspace Sidebar"))
    doc.update({"title": title, "app": {"dms": "pearnly_dms", "cowork": "pearnly_cowork", "erp": "pearnly_erp_portal"}[product], "header_icon": "layers", "items": items})
    doc.save(ignore_permissions=True)
    frappe.clear_cache()


def _offered(product, features) -> dict:
    """这次真正放进侧栏的页面：**做好的**（AVAILABLE_PAGES）+ 买了的 + 有权限的。

    没做好的页面不进侧栏 —— "不具备的功能不放出来"这条只能在这里守，
    因为侧栏是用户唯一能点到它们的地方。
    """
    from pearnly_core.shell import entitlements  # noqa: F401 - 保持导入位置与语义
    offered = {}
    for label, route, icon, feature in PAGES[product]:
        if route not in AVAILABLE_PAGES:
            continue
        if feature and feature not in features:
            continue
        offered[route] = (label, route, icon, feature)
    return offered


def _sidebar_items(product, features) -> list:
    """侧栏条目：有分组定义就出「分组标题 + 缩进的子项」，否则平铺。"""
    offered = _offered(product, features)
    groups = GROUPS.get(product)
    if not groups:
        return [{"type": "Link", "label": label, "link_type": "Page", "link_to": route,
                 "icon": icon} for label, route, icon, _feature in offered.values()]

    grouped = {route for _label, _icon, routes in groups for route in routes}
    # 分组外的（首页）留在最上面，跟原生一样是平铺项。
    items = [{"type": "Link", "label": label, "link_type": "Page", "link_to": route,
              "icon": icon}
             for route, (label, route, icon, _feature) in offered.items()
             if route not in grouped]
    for label, icon, routes in groups:
        children = [(route, offered[route]) for route in routes if route in offered]
        if not children:
            # 整组一个页面都没有就不要出空标题 —— 空组比没有更让人以为"坏了"。
            continue
        items.append(_group_item(label, icon))
        for route, (child_label, _route, _child_icon, _feature) in children:
            items.append({"type": "Link", "label": child_label, "link_type": "Page",
                          "link_to": route, "icon": "", "child": 1, "indent": 1})
    return items


def _group_item(label: str, icon: str) -> dict:
    """分组标题：**一个总图标 + 标题 + 折叠箭头**，子项不再带图标。

    为什么父项写 `indent=1`（原生里这个字段本来是"缩进"）：
      · Section Break 只有在 `indent != 1` 时才渲染成那条细分隔线；`indent == 1` 会走
        「普通条目」分支，于是标题前面能有一个图标（`sidebar_item.html` 的分支判断）。
      · 同时 `sidebar_item.js` 给**没有图标的子项**补一个 `list` 图标的条件是
        `!(item.child && item.parent.indent)` —— 父项 indent=1 正好把补图标这条关掉，
        子项于是真的没有图标，跟参考图一致。
    折叠仍然由原生的 `collapsible` + 右侧 drop-icon 负责，不自建控件。
    """
    return {"type": "Section Break", "label": label, "icon": icon, "indent": 1,
            "collapsible": 1, "keep_closed": 0}


def extend_boot(boot):
    from pearnly_core.shell import entitlements
    product = frappe.conf.get("pearnly_product")
    if product not in PAGES:
        return
    boot.pearnly_product = product
    # Existing sites already retain the company name as their provisioned workspace.
    boot.pearnly_company_name = str(frappe.conf.get("pearnly_company_name")
                                   or frappe.conf.get("pearnly_workspace") or "").strip()
    boot.pearnly_features = entitlements()
    boot.pearnly_pages = _personal_order(product, visible_pages(product))
    boot.pearnly_native_account = bool(frappe.conf.get("pearnly_native_account"))
    from pearnly_core.shell import PRODUCT_LABELS
    boot.pearnly_sidebar = "Pearnly " + PRODUCT_LABELS[product]

    # 分组标题也要在启动数据里：侧栏是**按 boot 里的条目**渲染的，
    # 只写进 Workspace Sidebar 文档不够（那个文档只在整页刷新时才重新读）。
    boot.pearnly_nav_groups = [
        {"label": label, "icon": icon,
         "routes": [route for route in routes if route in boot.pearnly_pages]}
        for label, icon, routes in GROUPS.get(product, [])
    ]

    # Per-user boot projection, not a shared Workspace Sidebar mutation. Never save
    # one user's smaller menu back to the site-wide sidebar document.
    sidebars = boot.get("workspace_sidebar_item") or {}
    sidebar = sidebars.get(boot.pearnly_sidebar.lower())
    if sidebar:
        items = {item.get("link_to"): item for item in sidebar.get("items", [])}
        # 按分组重排本人这一份投影：父项是 Section Break，子项带 child=1。
        # 侧栏随后用它自己的 find_nested_items 嵌套，我们不自建折叠控件。
        groups = GROUPS.get(product)
        if not groups:
            sidebar["items"] = [items[route] for route in boot.pearnly_pages if route in items]
            return
        routes = list(boot.pearnly_pages)
        grouped = {route for _label, _icon, group_routes in groups for route in group_routes}
        projected = [items[route] for route in routes
                     if route in items and route not in grouped]
        for label, icon, group_routes in groups:
            members = [items[route] for route in group_routes
                       if route in items and route in routes]
            if not members:
                continue
            # 原生给条目做的是 `_(item.label)`（frappe/boot.py:get_sidebar_items）。
            # 这里的 Section Break 是**我们新造的**，不经过那一步，所以要自己翻 ——
            # 否则标题会原样显示 `nav.group.*` 这种键（实测过）。
            projected.append({**_group_item(frappe._(label), icon)})
            for member in members:
                projected.append({**member, "icon": "", "child": 1, "indent": 1})
        sidebar["items"] = projected
