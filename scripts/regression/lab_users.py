"""回归实验室：按岗位建测试账号 <岗位>@<产品>.regression（界面中文、Desk User + 岗位角色），已有就对齐角色。"""
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
finally:
    frappe.destroy()
