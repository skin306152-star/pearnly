"""Install native menu roles and migrate legacy employee grants without widening them."""
import frappe

from pearnly_core.provisioning import _ensure_role, _grant
from pearnly_erp_portal.access import menu, rules


def sync_roles():
    read = {"read": 1, "select": 1}
    write = {**read, "write": 1, "create": 1, "report": 1, "export": 1}
    dependencies = ("ERP Product", "ERP Company", "ERP Warehouse", "ERP POS Terminal",
                    "ERP Stock Balance", "ERP Stock Movement")
    for key, (_feature, _legacy, _caps, writable, readable) in menu.CATALOG.items():
        role = menu.role(key)
        _ensure_role(role)
        lookup = ("ERP Company",) if key in ("home", "profile", "team") else dependencies
        for doctype in set(lookup + readable + writable):
            if frappe.db.exists("DocType", doctype):
                _grant(doctype, role, write if doctype in writable else read)
        if key in ("inventory", "stock-in", "stock-out", "stock-card", "stock-count", "warehouses"):
            _grant("ERP Product", role, {"read": 1}, permlevel=1)
            _grant("ERP Stock Count Item", role, {"read": 1}, permlevel=1)


def migrate_members():
    sync_roles()
    from pearnly_core.shell import entitlements

    features = set(entitlements())
    legacy = set(rules.FEATURE_ROLES.values())
    users = set(frappe.get_all("Has Role", filters={"role": ["in", list(legacy)], "parenttype": "User"}, pluck="parent"))
    for name in users:
        user = frappe.get_doc("User", name)
        held = {row.role for row in user.roles}
        if name == "Administrator" or held.intersection(rules.ALL_RECORDS_ROLES):
            continue
        wanted = {menu.role(key) for key, spec in menu.CATALOG.items() if spec[1] in held and (not spec[0] or spec[0] in features)}
        wanted.update((menu.role("home"), menu.role("profile")))
        for row in list(user.roles):
            if row.role in legacy:
                user.roles.remove(row)
        for role in wanted - held:
            user.append("roles", {"role": role})
        user.save(ignore_permissions=True)
        frappe.clear_cache(user=name)
