"""Install native menu roles and migrate legacy employee grants without widening them."""
import frappe

from pearnly_core.provisioning import _ensure_role, _grant
from pearnly_erp_portal.access import menu, rules


def sync_roles():
    read = {"read": 1, "select": 1}
    write = {**read, "write": 1, "create": 1, "report": 1, "export": 1}
    dependencies = ("ERP Product", "ERP Company", "ERP Warehouse", "ERP POS Terminal")
    # Costs for employees are projected by live entitlement-aware product services.
    # A static field grant would survive a feature being turned off.
    frappe.db.delete("Custom DocPerm", {"parent": "ERP Product", "permlevel": 1,
        "role": ["in", ["ERP Stock", *menu.roles()]], "if_owner": 0})
    # Updating the catalog alone leaves old Custom DocPerm rows effective on existing Sites.
    for doctype, key in (("ERP Stock Balance", "inventory"), ("ERP Stock Movement", "stock-card")):
        stale_roles = [role for role in menu.roles() if role != menu.role(key)]
        frappe.db.delete("Custom DocPerm", {"parent": doctype, "role": ["in", stale_roles],
                                            "permlevel": 0, "if_owner": 0})
    for key, (_feature, _legacy, _caps, writable, readable) in menu.CATALOG.items():
        role = menu.role(key)
        _ensure_role(role)
        lookup = ("ERP Company",) if key in menu.NON_BUSINESS else dependencies
        for doctype in set(lookup + readable + writable):
            if frappe.db.exists("DocType", doctype):
                _grant(doctype, role, write if doctype in writable else read)
        if key in ("inventory", "stock-in", "stock-out", "stock-card", "stock-count", "warehouses"):
            _grant("ERP Stock Count Item", role, {"read": 1}, permlevel=1)
    # LINE 开关就是这个角色（团队管理逐人勾选），不带任何单据权限。
    _ensure_role(rules.LINE_ROLE)
    from pearnly_erp_portal.access.sharing import remove_legacy_shares

    remove_legacy_shares()
    frappe.clear_cache()


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
