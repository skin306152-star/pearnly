# 样板虚拟数据种子：六个测试人设。
#
# 用法（在样板机上执行，站点名按需替换）：
#   docker --context colima-pearnly compose -f infrastructure/compose/01-bench.yml \
#     exec -T cowork-bench bench --site cowork-a.localhost console < scripts/sample-seed-personas.py
#
# 重要限制：**这是"形态"种子，不是真正的授权模型。**
# 真正的 Principal / Membership / Scope 与产品权限码要等 P2/P3 的新 App 落地。
# 现在只用 Frappe 原生 User 把六种人设摆出来，用来验证"能登/不能登"这条底线。
#
# 六种人设（对应计划里要求的覆盖）：
#   platform-admin  平台管理员
#   tenant-admin    租户管理员
#   staff           普通员工
#   viewer          只读
#   revoked         已撤权（停用）
#   （未邀请者不建号，用于反面验证）

import frappe

COMMON_PASSWORD = "SampleLocal123!"  # 仅本地样板

PERSONAS = [
    ("platform-admin@sample.local", "平台管理员", ["System Manager"], True),
    ("tenant-admin@sample.local", "租户管理员", ["System Manager"], True),
    ("staff@sample.local", "普通员工", ["Desk User"], True),
    ("viewer@sample.local", "只读", ["Desk User"], True),
    ("revoked@sample.local", "已撤权", ["Desk User"], False),
]

for email, name, roles, enabled in PERSONAS:
    if frappe.db.exists("User", email):
        print("skip", email)
        continue
    user = frappe.get_doc({
        "doctype": "User",
        "email": email,
        "first_name": name,
        "enabled": 1,
        "send_welcome_email": 0,
        "new_password": COMMON_PASSWORD,
    })
    for role in roles:
        user.append("roles", {"role": role})
    user.insert(ignore_permissions=True)
    if not enabled:
        frappe.db.set_value("User", email, "enabled", 0)
    print("created", email)

frappe.db.commit()
print("total users:", frappe.db.count("User"))
