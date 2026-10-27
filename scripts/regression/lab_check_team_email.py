"""团队管理「添加用户」邮箱可留空（team-placeholder-email）的服务端实测（回归实验室，ERP / Cowork）：
留空 → `<登录名>.<本站前缀>@pearnly.com`；手填合法 → 用手填的；手填不合法 → 拒；生成的地址已被别的账号占了 → 拒、不合并。
每条一行 JSON；最后一行 SUMMARY。

    ../env/bin/python /workspace/scripts/regression/lab_check_team_email.py <站点> <产品>
"""
import json
import os
import sys
import uuid

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402

RESULTS = []


def case(name, expect, fn):
    detail = ""
    try:
        detail = fn()
        actual = "ok"
    except Exception as error:  # noqa: BLE001 - 每条都要记结果
        frappe.db.rollback()
        detail, actual = f"{type(error).__name__}: {str(error)[:140]}", "error"
    passed = actual == expect and (not isinstance(detail, dict) or detail.get("pass", True))
    RESULTS.append({"case": name, "expect": expect, "actual": actual, "pass": passed, "detail": detail})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, default=str), flush=True)


def main():
    site, product = sys.argv[1], sys.argv[2]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        if product == "erp":
            from pearnly_erp_portal.team import service as team
            roles = ["ERP Menu purchase", "ERP Menu profile"]
        else:
            from pearnly_cowork.team import service as team
            roles = ["Cowork Intake", "Cowork Profile"]
        admin = f"admin@{product}.regression"
        prefix = site.split(".")[0]

        def add(username, email):
            frappe.set_user(admin)
            kwargs = {"username": username, "email": email, "roles": roles, "password": "Lab-only-" + uuid.uuid4().hex[:6]}
            if product == "erp":
                kwargs["companies"] = []
            out = team.add_member(**kwargs)
            frappe.db.commit()
            return out

        tag = uuid.uuid4().hex[:5]

        def blank():
            name = f"Blank{tag}"
            add(name, "")
            expected = f"blank{tag}.{prefix}@pearnly.com"
            return {"expected": expected, "exists": bool(frappe.db.exists("User", expected)), "pass": bool(frappe.db.exists("User", expected))}
        case("邮箱留空 → <登录名>.<本站前缀>@pearnly.com（全小写）", "ok", blank)

        def typed():
            email = f"Typed{tag}@Example.test"
            add(f"typed{tag}", email)
            return {"stored": frappe.db.exists("User", email.lower()), "pass": bool(frappe.db.exists("User", email.lower()))}
        case("手填合法邮箱 → 按手填的建（转小写）", "ok", typed)
        case("手填不合法邮箱（abc）→ 拒", "error", lambda: add(f"bad{tag}", "abc"))
        case("手填不合法邮箱（a@b）→ 拒", "error", lambda: add(f"bad2{tag}", "a@b"))

        def taken():
            name = f"taken{tag}"
            frappe.set_user("Administrator")
            frappe.get_doc({"doctype": "User", "email": f"{name}.{prefix}@pearnly.com", "first_name": "someone else",
                            "send_welcome_email": 0, "user_type": "Website User"}).insert(ignore_permissions=True)
            frappe.db.commit()
            return add(name, "")
        case("生成的地址已被别的账号占了 → 拒（不合并）", "error", taken)
        print("SUMMARY", json.dumps({"pass": sum(1 for r in RESULTS if r["pass"]), "total": len(RESULTS)}))
    finally:
        frappe.destroy()


if __name__ == "__main__":
    main()
