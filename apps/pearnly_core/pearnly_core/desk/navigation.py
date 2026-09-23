"""Product navigation in native Workspace Sidebar, without granting new roles.

The menu of a product belongs to that product. Each product app declares its own
entries in its own `hooks.py` as `pearnly_navigation`, together with the routes it
is ready to offer and the permission each route needs; this module only assembles
what the site actually has installed and renders the native sidebar from it.

Consequences worth keeping: adding or moving a menu entry never edits a shared
file, and a site that does not install a product can never show its pages (the
hook is only read from installed apps).
"""
import json

import frappe
from frappe import _


def _declarations():
    """The navigation each installed product declares, keyed by product."""
    found = {}
    for entry in frappe.get_hooks("pearnly_navigation") or []:
        if not isinstance(entry, dict):
            continue
        product = str(entry.get("product") or "").strip()
        if product and product not in found:
            found[product] = entry
    return found


def products():
    return list(_declarations())


def pages(product):
    """One product's entries as `(label, route, icon, feature)` tuples."""
    return [tuple(item) for item in (_declarations().get(product) or {}).get("pages") or []]


def available_pages(product):
    """Only pages that are implemented are offered; the rest keep their ownership."""
    return set((_declarations().get(product) or {}).get("available") or [])


def navigation_groups(product):
    """Native sidebar grouping is generic; the product owns group contents."""
    return list((_declarations().get(product) or {}).get("groups") or [])


def link_targets(product) -> dict:
    """产品声明的"选择框里的『新建』去哪儿"：`{主档 doctype: 我们自己的路由}`。

    只留下当前用户**能打开**的那些页面（判据与侧栏同一套 `visible_pages`）：点「新建」
    落进"没有权限 / 没实现"的一屏，比留在原地更糟。声明在产品的 `hooks.py` 里
    （ADR-009：菜单与可售卖功能由产品自己声明），这里只组装 + 过滤。
    """
    declared = (_declarations().get(product) or {}).get("links") or {}
    if not isinstance(declared, dict):
        return {}
    offered = set(visible_pages(product))
    out = {}
    for doctype, route in declared.items():
        route = str(route or "").strip()
        if doctype and route in offered:
            out[str(doctype)] = route
    return out


def _can_open_page(product, route):
    """Mirror the declared page permission; purchased features alone are not a role."""
    rule = ((_declarations().get(product) or {}).get("permissions") or {}).get(route) or {}
    roles = rule.get("any_role")
    if roles:
        return bool(set(roles) & set(frappe.get_roles()))
    doctype = rule.get("doctype")
    return not doctype or frappe.has_permission(doctype, "read")


def visible_pages(product):
    from pearnly_core.shell import entitlements
    features = set(entitlements())
    # 与侧栏同一份口径（`_offered` 里已经含产品自己的页面过滤器），再加"这个账号能不能进"。
    offered = _offered(product, features)
    return [route for _, route, _, _feature in pages(product)
            if route in offered and _can_open_page(product, route)]


def _user_menu_specs(product) -> list:
    """产品声明的那几项左下角菜单，按声明顺序。

    一项可以写成路由字符串，也可以写成 `{"route": ..., "before_profile": True}`；
    后者排在**「个人信息」上面**（用户 2026-09-23："公司……做在个人信息上方"），
    不带这个记号的一律排在「个人信息」下面。位置是产品自己的声明，外壳只照着画。
    """
    declared = (_declarations().get(product) or {}).get("user_menu") or []
    specs = []
    for item in declared:
        spec = item if isinstance(item, dict) else {"route": item}
        route = str(spec.get("route") or "").strip()
        if route:
            specs.append({"route": route,
                          "before_profile": bool(spec.get("before_profile"))})
    return specs


def _user_menu_routes(product) -> set:
    """只在**左下角用户菜单**里出现的路由。

    写进 `user_menu` 的页面从侧栏移出去 —— 这个声明本身就是"这个入口不放侧栏、放
    左下角那颗按钮里"（用户 2026-09-23："不要主数据，把团队管理放在左下角里面"）。
    页面本身仍然可按路由打开，`boot.pearnly_pages`（也能打开）不受影响。
    """
    return {spec["route"] for spec in _user_menu_specs(product)}


def sidebar_pages(product):
    """侧栏这一份该出现哪些页面：能打开的页面里去掉走左下角菜单的那些。"""
    hidden = _user_menu_routes(product)
    return [route for route in visible_pages(product) if route not in hidden]


def user_menu(product):
    """产品要在**左下角那颗用户按钮**的菜单里出现哪几项（例如 ERP 的「团队管理」）。

    产品声明的是**路由**，标题与图标仍从 `pages` 取 —— 同一句话只有一处词，不复制。
    判据与侧栏完全一致（`visible_pages`：页面已实现 + 当前用户能进），所以"只有产品
    管理员能进"的页面只会出现在管理员的菜单里，别人连项都看不到。
    """
    entries = {route: (label, icon) for label, route, icon, _feature in pages(product)}
    offered = set(visible_pages(product))
    entries_out = []
    for spec in _user_menu_specs(product):
        route = spec["route"]
        if route in offered and route in entries:
            label, icon = entries[route]
            entries_out.append({"route": route, "label": label, "icon": icon or "",
                                "before_profile": spec["before_profile"]})
    return entries_out


def _personal_order(product, offered):
    raw = frappe.defaults.get_user_default("pearnly_sidebar_order_" + product)
    try:
        saved = json.loads(raw or "[]")
    except (ValueError, TypeError):
        saved = []
    if not isinstance(saved, list):
        saved = []
    order = []
    for route in saved + offered:
        if isinstance(route, str) and route in offered and route not in order:
            order.append(route)
    return order


@frappe.whitelist(methods=["POST"])
def save_sidebar_order(routes):
    product = frappe.conf.get("pearnly_product")
    if frappe.session.user == "Guest" or product not in _declarations():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    if isinstance(routes, str):
        try:
            routes = json.loads(routes)
        except ValueError:
            frappe.throw(_("Invalid value"))
    # 校验的是**侧栏那一份**：走左下角菜单的页面本来就不在侧栏里，不该出现在这里比对。
    allowed = sidebar_pages(product)
    if (not isinstance(routes, list) or not all(isinstance(r, str) for r in routes)
            or len(routes) != len(allowed) or set(routes) != set(allowed)):
        frappe.throw(_("Invalid value"))
    # User Default is per-site and per-session user. No shared sidebar or role writes.
    frappe.defaults.set_user_default("pearnly_sidebar_order_" + product, json.dumps(routes))
    # `routes` 是侧栏的新顺序，`pages` 是"这个用户能打开的全部页面"（含左下角那些）——
    # 页面路由的准入判据用后者，不能因为排序把它变小。
    return {"routes": routes, "pages": visible_pages(product)}


def ensure_navigation():
    from pearnly_core.shell import entitlements, PRODUCT_LABELS
    product = frappe.conf.get("pearnly_product")
    if product not in _declarations():
        return
    title = "Pearnly " + PRODUCT_LABELS[product]
    features = set(entitlements())
    items = _sidebar_items(product, features)
    doc = (frappe.get_doc("Workspace Sidebar", title) if frappe.db.exists("Workspace Sidebar", title)
           else frappe.new_doc("Workspace Sidebar"))
    doc.update({"title": title, "app": _declarations()[product].get("app") or "", "header_icon": "layers", "items": items})
    doc.save(ignore_permissions=True)
    frappe.clear_cache()


def _offered(product, features) -> dict:
    """这次真正放进侧栏的页面：**做好的**（available_pages）+ 买了的 + 有权限的。

    没做好的页面不进侧栏 —— "不具备的功能不放出来"这条只能在这里守，
    因为侧栏是用户唯一能点到它们的地方。
    """
    offered = {}
    for label, route, icon, feature in pages(product):
        if route not in available_pages(product):
            continue
        if feature and feature not in features:
            continue
        offered[route] = (label, route, icon, feature)
    return _product_page_filter(product, offered)


def _product_page_filter(product, offered) -> dict:
    """产品可以在自己的声明里挂一个**页面过滤器**，把"某些页在当前数据下不该出现"表达出来。

    为什么要有这一层：租户买了某个功能（`features`）不等于**每一家公司**都用它 ——
    例如 ERP 的收银台是**公司级**开关（一家公司不开收银，那三页与收银岗位都不该出现），
    而"哪家公司开了"是产品自己的事，共用层不该认识它。所以产品声明里给一个点号路径，
    这里只负责调它：签名 `(offered) -> offered`，键是路由、值是 `(label, route, icon, feature)`。

    过滤器取不到（没声明/导不进来）就**原样返回**：这只管显隐，真正的授权在服务端。
    """
    spec = (_declarations().get(product) or {}).get("page_filter")
    if not spec:
        return offered
    try:
        from frappe.utils import get_attr

        return get_attr(spec)(offered) or offered
    except Exception:
        frappe.log_error(title=f"{product} page_filter failed")
        return offered


def _sidebar_items(product, features) -> list:
    """侧栏条目：有分组定义就出「分组标题 + 缩进的子项」，否则平铺。"""
    hidden = _user_menu_routes(product)
    offered = {route: value for route, value in _offered(product, features).items()
               if route not in hidden}
    groups = navigation_groups(product)
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
    if product not in _declarations():
        return
    boot.pearnly_product = product
    # Existing sites already retain the company name as their provisioned workspace.
    boot.pearnly_company_name = str(frappe.conf.get("pearnly_company_name")
                                   or frappe.conf.get("pearnly_workspace") or "").strip()
    boot.pearnly_features = entitlements()
    boot.pearnly_pages = _personal_order(product, visible_pages(product))
    # 选择框里的「新建 / 高级搜索」该进我们哪一页（已按"能打开"过滤）。产品声明，前端只照着用。
    boot.pearnly_links = link_targets(product)
    # 左下角用户菜单里产品自己那几项（已按"能进"筛过），外壳只负责画。
    boot.pearnly_user_menu = user_menu(product)
    # 侧栏这一份去掉了走左下角的那些页面（`boot.pearnly_pages` 仍是"能打开的页面"，
    # 页面路由的准入判据用它，不能跟着少）。
    sidebar_routes = [route for route in boot.pearnly_pages
                      if route in sidebar_pages(product)]
    boot.pearnly_native_account = bool(frappe.conf.get("pearnly_native_account"))
    from pearnly_core.shell import PRODUCT_LABELS
    boot.pearnly_sidebar = "Pearnly " + PRODUCT_LABELS[product]

    # 分组标题也要在启动数据里：侧栏是**按 boot 里的条目**渲染的，
    # 只写进 Workspace Sidebar 文档不够（那个文档只在整页刷新时才重新读）。
    boot.pearnly_nav_groups = [
        {"label": label, "icon": icon,
         "routes": [route for route in routes if route in sidebar_routes]}
        for label, icon, routes in navigation_groups(product)
    ]

    # Per-user boot projection, not a shared Workspace Sidebar mutation. Never save
    # one user's smaller menu back to the site-wide sidebar document.
    sidebars = boot.get("workspace_sidebar_item") or {}
    sidebar = sidebars.get(boot.pearnly_sidebar.lower())
    if sidebar:
        items = {item.get("link_to"): item for item in sidebar.get("items", [])}
        # 按分组重排本人这一份投影：父项是 Section Break，子项带 child=1。
        # 侧栏随后用它自己的 find_nested_items 嵌套，我们不自建折叠控件。
        groups = navigation_groups(product)
        if not groups:
            sidebar["items"] = [{**items[route], "child": 0, "indent": 0}
                                for route in sidebar_routes if route in items]
            return
        routes = sidebar_routes
        grouped = {route for _label, _icon, group_routes in groups for route in group_routes}
        # 平铺项一律按"平铺"重写：`items` 来自站点里那份 Workspace Sidebar 文档，
        # 页面改过分组之后，旧文档里可能还留着 `child`/`indent`（它原先是挂在某个
        # 分组下的）。原生渲染认这两个字段：**一个带 `child` 却没有分组父项的条目**
        # 会在 `sidebar_item.make()` 里读 `parent.indent` 抛错，整条侧栏只剩第一项
        # （2026-09-23 把「团队管理」移出分组时实测踩到，界面看上去像"菜单全丢了"）。
        projected = [{**items[route], "child": 0, "indent": 0} for route in routes
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
