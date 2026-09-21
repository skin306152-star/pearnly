"""Product navigation in native Workspace Sidebar, without granting new roles."""
import json

import frappe
from frappe import _

PAGES = {'dms': [('nav.home', 'pearnly-home', 'home', None),
         ('nav.connect', 'pearnly-connect', 'plug', None),
         ('nav.records', 'pearnly-records', 'list', 'push_record'),
         ('nav.roster', 'pearnly-roster', 'users', 'roster')],
 'cowork': [('nav.home', 'pearnly-home', 'home', None),
            ('nav.intake', 'pearnly-intake', 'scan', 'intake'),
            ('nav.history', 'pearnly-history', 'list', 'ocr_history'),
            ('nav.pushLogs', 'pearnly-push-logs', 'list', 'push_log'),
            ('nav.reconcile', 'pearnly-reconcile', 'check', 'recon'),
            ('nav.stocktake', 'pearnly-stocktake', 'stock', 'stocktake'),
            ('nav.clients', 'pearnly-clients', 'users', 'master_data'),
            ('nav.company', 'pearnly-company', 'organization', 'master_data'),
            ('nav.team', 'pearnly-team', 'users', 'master_data'),
            ('nav.integrations', 'pearnly-integrations', 'plug', 'master_data'),
            ('nav.guide', 'pearnly-guide', 'help', 'express_guide')],
 'erp': [('nav.home', 'pearnly-home', 'home', None),
         ('nav.products', 'pearnly-products', 'stock', 'products'),
         ('nav.inventory', 'pearnly-inventory', 'stock', 'warehouses'),
         ('nav.stockIn', 'pearnly-stock-in', 'arrow-down', 'warehouses'),
         ('nav.stockOut', 'pearnly-stock-out', 'arrow-up', 'warehouses'),
         ('nav.stockCard', 'pearnly-stock-card', 'list', 'stockcard'),
         ('nav.purchase', 'pearnly-purchase', 'cart', 'purchase'),
         ('nav.suppliers', 'pearnly-suppliers', 'users', 'purchase'),
         ('nav.purchaseSettings', 'pearnly-buy-settings', 'settings', 'purchase'),
         ('nav.sales', 'pearnly-sales', 'cart', 'sales'),
         ('nav.erpClients', 'pearnly-clients', 'users', 'clients'),
         ('nav.company', 'pearnly-company', 'organization', None),
         ('nav.team', 'pearnly-team', 'users', None),
         ('nav.integrations', 'pearnly-integrations', 'plug', None)]}


# Only working pages are offered. The catalog above preserves future feature ownership.
AVAILABLE_PAGES = {"pearnly-home", "pearnly-connect", "pearnly-roster", "pearnly-records", "pearnly-intake", "pearnly-purchase"}



def _can_open_page(route):
    """Mirror the existing endpoint permission; purchased features alone are not a role."""
    if route in {"pearnly-connect", "pearnly-roster", "pearnly-records"}:
        return bool({"DMS Admin", "System Manager"} & set(frappe.get_roles()))
    doctype = {"pearnly-intake": "Intake Record", "pearnly-purchase": "Purchase Document"}.get(route)
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
    items = [{"type": "Link", "label": label, "link_type": "Page", "link_to": route,
              "icon": icon} for label, route, icon, feature in PAGES[product]
             if route in AVAILABLE_PAGES and (not feature or feature in features)]
    doc = (frappe.get_doc("Workspace Sidebar", title) if frappe.db.exists("Workspace Sidebar", title)
           else frappe.new_doc("Workspace Sidebar"))
    doc.update({"title": title, "app": {"dms": "pearnly_dms", "cowork": "pearnly_cowork", "erp": "pearnly_erp_portal"}[product], "header_icon": "layers", "items": items})
    doc.save(ignore_permissions=True)
    frappe.clear_cache()


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

    # Per-user boot projection, not a shared Workspace Sidebar mutation. Never save
    # one user's smaller menu back to the site-wide sidebar document.
    sidebars = boot.get("workspace_sidebar_item") or {}
    sidebar = sidebars.get(boot.pearnly_sidebar.lower())
    if sidebar:
        items = {item.get("link_to"): item for item in sidebar.get("items", [])}
        sidebar["items"] = [items[route] for route in boot.pearnly_pages if route in items]
