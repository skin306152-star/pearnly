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
    offered = available_pages(product)
    return [route for _, route, _, feature in pages(product)
            if route in offered and (not feature or feature in features)
            and _can_open_page(product, route)]


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
    declaration = _declarations().get(product)
    if not declaration:
        return
    title = "Pearnly " + PRODUCT_LABELS[product]
    features = set(entitlements())
    offered = available_pages(product)
    items = [{"type": "Link", "label": label, "link_type": "Page", "link_to": route,
              "icon": icon} for label, route, icon, feature in pages(product)
             if route in offered and (not feature or feature in features)]
    doc = (frappe.get_doc("Workspace Sidebar", title) if frappe.db.exists("Workspace Sidebar", title)
           else frappe.new_doc("Workspace Sidebar"))
    doc.update({"title": title, "app": declaration.get("app") or "", "header_icon": "layers", "items": items})
    doc.save(ignore_permissions=True)
    frappe.clear_cache()


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
    boot.pearnly_native_account = bool(frappe.conf.get("pearnly_native_account"))
    from pearnly_core.shell import PRODUCT_LABELS
    boot.pearnly_sidebar = "Pearnly " + PRODUCT_LABELS[product]

    # Per-user boot projection, not a shared Workspace Sidebar mutation. Never save
    # one user's smaller menu back to the site-wide sidebar document.
    sidebars = boot.get("workspace_sidebar_item") or {}
    sidebar = sidebars.get(boot.pearnly_sidebar.lower())
    if sidebar:
        items = {item.get("link_to"): item for item in sidebar.get("items", [])}
        sidebar["items"] = [items[route] for route in boot.pearnly_pages if route in items]
