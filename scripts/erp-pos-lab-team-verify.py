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
import sys

import frappe

CONTROL_APP = "/home/frappe/frappe-bench/apps/pearnly_control"
SITE = os.environ.get("PEARNLY_ERP_POS_LAB_SITE", "erp-pos-a.localhost")
ALLOWED_SITES = {"erp-pos-a.localhost", "erp-pos-b.localhost"}
SITE_ROOT = Path("/home/frappe/frappe-bench/sites")

OWNER = "erp-pos-owner@example.com"
EMPLOYEE = "erp-pos-team-member@example.com"
CASHIER = "erp-pos-team-cashier@example.com"


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


def drop_user(email):
    """清掉上一次运行留下的探针账号，让脚本可重复执行。"""
    if frappe.db.exists("User", email):
        frappe.delete_doc("User", email, force=True, ignore_permissions=True)
        frappe.db.commit()


def main():
    connect()
    frappe.set_user("Administrator")
    from pearnly_core.provisioning import ensure_feature_roles
    from pearnly_erp_portal.team import service
    from pearnly_erp_portal import purchase
    from pearnly_core.desk import navigation

    # 用控制面自己发布的清单开通，而不是在本脚本里另抄一份角色表。
    if CONTROL_APP not in sys.path:
        sys.path.insert(0, CONTROL_APP)
    from pearnly_control import features as control_features

    spec = control_features.roles_payload("erp", control_features.codes_of("erp"))
    ensure_feature_roles(json.dumps(spec), admin_user=OWNER)

    results = {}
    results["control_spec_roles"] = sorted(r["role"] for r in spec["roles"])
    results["assignable_roles"] = list(service.assignable_roles())
    results["cashier_role_exists"] = bool(frappe.db.exists("Role", "ERP Cashier"))
    results["owner_roles"] = sorted(frappe.get_roles(OWNER))

    # 老板新增一名采购员工 —— 与老板共用同一套站点账号体系。
    drop_user(EMPLOYEE)
    ok, created = as_user(OWNER, service.add_member, email=EMPLOYEE,
                          full_name="Lab Member", roles=["ERP Purchase"])
    results["owner_add_member"] = ok
    results["created"] = created if ok else created

    ok, members = as_user(OWNER, service.list_members)
    results["member_count"] = len(members) if ok else members
    results["member_roles"] = next((m["roles"] for m in members if m["email"] == EMPLOYEE), None) if ok else None

    # 收银含在 ERP：老板能直接把「收银员」发给员工，不需要另买功能。
    drop_user(CASHIER)
    ok, cashier = as_user(OWNER, service.add_member, email=CASHIER,
                          full_name="Lab Cashier", roles=["ERP Cashier"])
    results["add_cashier_allowed"] = ok
    results["cashier_roles"] = cashier.get("roles") if ok else cashier

    # 员工拿到岗位后能干活，但看不到老板的记录。
    ok, rows = as_user(EMPLOYEE, purchase.list_purchases, limit=50)
    results["employee_can_read_purchases"] = ok
    results["employee_rows"] = len(rows) if ok else rows

    # 不能发平台角色，也不能发本站点没有的角色。
    for leftover in ("erp-pos-nope@example.com", "erp-pos-nope2@example.com"):
        drop_user(leftover)
    ok, err = as_user(OWNER, service.add_member, email="erp-pos-nope@example.com",
                      full_name="Nope", roles=["System Manager"])
    results["grant_system_manager_allowed"] = ok
    results["grant_system_manager_error"] = None if ok else err

    # 不存在的岗位（不是本站点建出来的角色）同样不能发；用真岗位名会随目录变化而失效，
    # 所以这里刻意用一个永远不会被建出来的名字。
    ok, err = as_user(OWNER, service.add_member, email="erp-pos-nope2@example.com",
                      full_name="Nope", roles=["ERP Not A Job"])
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
        "cashier_role_exists": True,
        "add_cashier_allowed": True,
        "cashier_roles": ["ERP Cashier"],
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
    assignable = set(results.get("assignable_roles") or [])
    if not {"ERP Purchase", "ERP Cashier"} <= assignable:
        problems["assignable_roles"] = results.get("assignable_roles")
    if "ERP Cashier" not in (results.get("owner_roles") or []):
        # 老板由开通自动带上每个产品功能角色 —— 这是"老板自己也能收银"的依据。
        problems["owner_lacks_cashier_role"] = results.get("owner_roles")

    print(json.dumps({"site": SITE, "results": results, "problems": problems},
                     ensure_ascii=False, indent=2, default=str))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
