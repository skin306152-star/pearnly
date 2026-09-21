"""Verify ERP team management on the dedicated laboratory.

Covers the server side of "add a cashier on the ERP entry and let them in with the
same login": who may manage the team, which job roles may be handed out, that the
owner account cannot be edited through this door, and that the navigation only
projects pages the signed-in member may actually open.

Synthetic users only, lab volume only, no identity/LINE/ERP/billing service.
"""
import json
import os
from pathlib import Path

import frappe

SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
EMPLOYEE = "erp-pos-team-member@example.com"


def connect(site=SITE):
    if os.environ.get("PEARNLY_ERP_POS_LAB") != "1":
        raise SystemExit("Dedicated ERP/POS laboratory required")
    if site not in ALLOWED_SITES or not (SITE_ROOT / ".erp-pos-lab").is_file():
        raise SystemExit("Dedicated lab Site marker required")
    os.chdir(SITE_ROOT)
    frappe.init(site=site, sites_path=str(SITE_ROOT))
    frappe.connect()
    if not frappe.conf.get("pearnly_erp_pos_lab") or frappe.conf.get("db_host") != "db":
        raise SystemExit("Refusing a non-lab Site")
    frappe.flags.mute_emails = True


def as_user(user, func, *args, **kwargs):
    frappe.set_user(user)
    try:
        return True, func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        return False, type(exc).__name__


def main():
    connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.team import service
    from pearnly_erp_portal import purchase
    from pearnly_core.desk import navigation

    ensure_feature_roles(json.dumps({
        "product": "erp",
        "admin_role": "ERP Admin",
        "base_roles": ["Desk User"],
        "roles": [{"role": "ERP Purchase", "doctypes": ["Purchase Document", "Purchase Line"],
                   "feature": "purchase"}],
    }), admin_user=OWNER)

    results = {}
    results["assignable_roles"] = list(service.assignable_roles())

    # 老板新增一名采购员工 —— 与老板共用同一套站点账号体系。
    if frappe.db.exists("User", EMPLOYEE):
        frappe.delete_doc("User", EMPLOYEE, force=True, ignore_permissions=True)
        frappe.db.commit()
    ok, created = as_user(OWNER, service.add_member, email=EMPLOYEE,
                          full_name="Lab Member", roles=["ERP Purchase"])
    results["owner_add_member"] = ok
    results["created"] = created if ok else created

    ok, members = as_user(OWNER, service.list_members)
    results["member_count"] = len(members) if ok else members
    results["member_roles"] = next((m["roles"] for m in members if m["email"] == EMPLOYEE), None) if ok else None

    # 员工拿到岗位后能干活，但看不到老板的记录。
    ok, rows = as_user(EMPLOYEE, purchase.list_purchases, limit=50)
    results["employee_can_read_purchases"] = ok
    results["employee_rows"] = len(rows) if ok else rows

    # 不能发平台角色，也不能发本站点没有的角色。
    ok, err = as_user(OWNER, service.add_member, email="erp-pos-nope@example.com",
                      full_name="Nope", roles=["System Manager"])
    results["grant_system_manager_allowed"] = ok
    results["grant_system_manager_error"] = None if ok else err

    ok, err = as_user(OWNER, service.add_member, email="erp-pos-nope2@example.com",
                      full_name="Nope", roles=["ERP Sales"])
    results["grant_unpurchased_role_allowed"] = ok
    results["grant_unpurchased_role_error"] = None if ok else err

    # 空岗位与"改老板本人"都要被拒。
    ok, err = as_user(OWNER, service.set_member_roles, email=EMPLOYEE, roles=[])
    results["empty_roles_allowed"] = ok
    ok, err = as_user(OWNER, service.set_member_roles, email=OWNER, roles=["ERP Purchase"])
    results["edit_owner_allowed"] = ok
    results["edit_owner_error"] = None if ok else err

    # 员工不能管理团队。
    ok, err = as_user(EMPLOYEE, service.list_members)
    results["employee_manages_team"] = ok
    results["employee_team_error"] = None if ok else err

    # 导航投影：只有能打开页面的账号才看到那个入口。
    def pages(user):
        frappe.set_user(user)
        return sorted(navigation.visible_pages("erp"))

    results["owner_pages"] = pages(OWNER)
    results["employee_pages"] = pages(EMPLOYEE)
    frappe.set_user("Administrator")
    if not frappe.db.exists("User", "erp-pos-outsider@example.com"):
        frappe.get_doc({"doctype": "User", "email": "erp-pos-outsider@example.com",
                        "first_name": "outsider", "send_welcome_email": 0,
                        "roles": [{"role": "Desk User"}]}).insert(ignore_permissions=True)
        frappe.db.commit()
    results["outsider_pages"] = pages("erp-pos-outsider@example.com")

    # 停用员工：状态落库。
    #
    # 注意边界：停用后"不能再建会话"是框架登录闸的职责（Frappe 在认证时看 enabled），
    # 在同一个进程里 `set_user` 不会重新走登录检查 —— 用进程内断言冒充它只会骗自己。
    # 会话级拦截留到有 HTTP 服务的批次验证。
    frappe.set_user(OWNER)
    ok, payload = as_user(OWNER, service.set_member_active, email=EMPLOYEE, enabled=0)
    results["disable_member"] = payload if ok else payload
    results["member_enabled_after_disable"] = bool(
        frappe.db.get_value("User", EMPLOYEE, "enabled"))

    frappe.set_user("Administrator")

    expected = {
        "owner_add_member": True,
        "grant_system_manager_allowed": False,
        "grant_system_manager_error": "PermissionError",
        "grant_unpurchased_role_allowed": False,
        "grant_unpurchased_role_error": "PermissionError",
        "empty_roles_allowed": False,
        "edit_owner_allowed": False,
        "edit_owner_error": "PermissionError",
        "employee_manages_team": False,
        "employee_team_error": "PermissionError",
        "employee_can_read_purchases": True,
        "employee_rows": 0,
        "member_enabled_after_disable": False,
    }
    problems = {k: {"expected": v, "actual": results.get(k)} for k, v in expected.items()
                if results.get(k) != v}
    if "pearnly-purchase" not in (results.get("owner_pages") or []):
        problems["owner_sees_purchase_page"] = results.get("owner_pages")
    if "pearnly-purchase" not in (results.get("employee_pages") or []):
        problems["employee_sees_purchase_page"] = results.get("employee_pages")
    if "pearnly-purchase" in (results.get("outsider_pages") or []):
        problems["outsider_sees_purchase_page"] = results.get("outsider_pages")
    if results.get("member_roles") != ["ERP Purchase"]:
        problems["member_roles"] = results.get("member_roles")

    print(json.dumps({"site": SITE, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
