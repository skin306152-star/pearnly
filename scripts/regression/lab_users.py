"""回归实验室：按岗位建测试账号 <岗位>@<产品>.regression（界面中文、Desk User + 岗位角色），已有就对齐角色。
站点收尾与功能角色授权走正式开户同一套函数（`pearnly_core.provisioning.finalize_site` / `ensure_feature_roles`，
清单取控制面 `features.roles_payload`，功能全开），所以岗位看到的权限与线上租户一致；管理员账号由 `ensure_feature_roles`
换成「产品管理员 + 全部功能角色 + Desk User」、收走 System Manager。"""
import json
import sys

import frappe

site, product, spec = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    for job, roles in spec.items():
        email = f"{job}@{product}.regression"
        wanted = ["Desk User", *roles]
        for role in wanted:
            if not frappe.db.exists("Role", role):
                frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)
        if frappe.db.exists("User", email):
            doc = frappe.get_doc("User", email)
            doc.set("roles", [])
        else:
            doc = frappe.get_doc({"doctype": "User", "email": email, "first_name": job, "send_welcome_email": 0, "enabled": 1})
        doc.language = "zh"
        doc.user_type = "System User"
        doc.flags.no_welcome_mail = True
        for role in wanted:
            doc.append("roles", {"role": role})
        doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
        print("USER", email, ",".join(wanted), flush=True)
    frappe.db.commit()
    from pearnly_control import features as catalog
    from pearnly_core import provisioning

    provisioning.finalize_site()
    codes = [item["code"] for item in json.load(open(frappe.get_app_path(
        {"erp": "pearnly_erp_portal", "cowork": "pearnly_cowork", "dms": "pearnly_dms"}[product], "features.json")))["features"]]
    print("ROLES", provisioning.ensure_feature_roles(json.dumps(catalog.roles_payload(product, codes)), admin_user=f"admin@{product}.regression"),
          flush=True)
    # 开户只发「产品管理员 + 功能角色」；团队管理里逐人勾的菜单角色（Cowork 的对账 / 盘点 / 个人页等）由团队服务另发——
    # 这里照岗位表补回管理员的那几项，等同管理员在团队管理里给自己全勾上
    admin = frappe.get_doc("User", f"admin@{product}.regression")
    have = {row.role for row in admin.roles}
    for role in spec.get("admin", []):
        if role not in have and frappe.db.exists("Role", role):
            admin.append("roles", {"role": role})
    admin.save(ignore_permissions=True)
    print("ADMIN", sorted(row.role for row in admin.roles), flush=True)
    frappe.db.commit()
finally:
    frappe.destroy()
