"""Install native menu roles and migrate legacy employee grants without widening them."""
import frappe

from pearnly_core.provisioning import _ensure_role, _grant
from pearnly_erp_portal.access import menu, rules


def sync_roles():
    read = {"read": 1, "select": 1}
    write = {**read, "write": 1, "create": 1, "report": 1, "export": 1}
    dependencies = ("ERP Product", "ERP Product Category", "ERP Company", "ERP Warehouse", "ERP POS Terminal")
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
        if key in ("inventory", "stock-in", "stock-out", "stock-transfer", "stock-adjust", "stock-card", "stock-count", "warehouses"):
            _grant("ERP Stock Count Item", role, {"read": 1}, permlevel=1)
    # 按台记录：能读商品或库存的角色新旧两套都给读与导出（行仍按公司与仓过滤，`inventory.access.record_query`）。
    # 后加的表开户时老岗位角色与管理员拿不到，按 features.json 同一口径在这里补，已有站点迁移即生效。
    holders = {(menu.role(key), legacy) for key, (_f, legacy, _c, _w, readable) in menu.CATALOG.items()
               if set(menu.SERIALS) <= set(readable)}
    for role in sorted({name for pair in holders for name in pair} | {rules.ADMIN_ROLE}):
        _ensure_role(role)
        for doctype in menu.SERIALS:
            if frappe.db.exists("DocType", doctype):
                _grant(doctype, role, {**read, "report": 1, "export": 1})
    # 物料分类（11-01）：已有站点的管理员与老岗位角色开户时没有这张表，这里按 features.json 同一口径补（新建 / 改名的能力
    # 控制器再判：盘点岗只能建）
    if frappe.db.exists("DocType", "ERP Product Category"):
        for role, perms in ((rules.ADMIN_ROLE, write), ("ERP Products", write), ("ERP Stock", write),
                            ("ERP Purchase", read), ("ERP Sales", read), ("ERP Cashier", read)):
            if frappe.db.exists("Role", role):
                _grant("ERP Product Category", role, perms)
    # LINE 开关就是这个角色（团队管理逐人勾选），不带任何单据权限。
    _ensure_role(rules.LINE_ROLE)
    # 收款（11-11）：收款员读自己的收款记录、能看收款的读本公司的（行由 `collection.access` 再按人与公司过滤）；写只走服务层
    _ensure_role(rules.COLLECTOR_ROLE)
    if frappe.db.exists("DocType", "ERP Collection"):
        for role in (rules.COLLECTOR_ROLE, rules.ADMIN_ROLE, "ERP Sales", menu.role("sales")):
            if frappe.db.exists("Role", role):
                _grant("ERP Collection", role, {**read, "report": 1, "export": 1})
    from pearnly_erp_portal.access.sharing import remove_legacy_shares

    remove_legacy_shares()
    frappe.clear_cache()


def migrate_members():
    sync_roles()
    from pearnly_core.shell import entitlements

    features = set(entitlements())
    # 收款员不是旧的粗岗位（没有对应的菜单角色），迁移不摘它
    legacy = set(rules.FEATURE_ROLES.values()) - {rules.COLLECTOR_ROLE}
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
