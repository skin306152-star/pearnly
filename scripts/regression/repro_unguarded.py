"""复现「没有业务角色的账号也能调、且没有权限检查」的接口（回归基线 B 发现，10-27）。只在回归实验室站点跑：
以 Administrator 造一条外部连接（假地址、假登录名）和一条推送记录，再用只有 Desk User 的账号走框架正式入口
`frappe.handler.execute_cmd` 调这两个接口，打印拿到的东西（登录名打码）。跑完回滚，不留数据。

    ../env/bin/python /workspace/scripts/regression/repro_unguarded.py dms.regression.localhost none@dms.regression
"""
import json
import os
import sys

if os.environ.get("PEARNLY_REGRESSION_LAB") != "1":
    raise SystemExit("Requires the task-owned regression laboratory")

import frappe  # noqa: E402
from frappe import handler  # noqa: E402
from werkzeug.test import EnvironBuilder  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402


def call(site, user, cmd, **args):
    frappe.set_user(user)
    frappe.local.request = Request(EnvironBuilder(path=f"/api/method/{cmd}", method="POST", headers={"Host": site}).get_environ())
    frappe.local.request_ip = "127.0.0.1"
    frappe.local.form_dict = frappe._dict(cmd=cmd, **args)
    frappe.local.response = frappe._dict()
    try:
        data = handler.execute_cmd(cmd)
        return {"ok": True, "message": data if data is not None else frappe.local.response.get("message")}
    except Exception as error:  # noqa: BLE001
        return {"ok": False, "error": f"{type(error).__name__}: {str(error)[:120]}"}


def mask(value):
    text = str(value or "")
    return text[:2] + "***" if text else ""


def main():
    site, user = sys.argv[1], sys.argv[2]
    frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
    frappe.connect()
    try:
        frappe.set_user("Administrator")
        endpoint = frappe.get_doc({"doctype": "Pearnly ERP Endpoint", "label": "Regression DMS", "adapter": "mrerp_dms", "enabled": 1,
                                   "is_default": 1, "system_url": "https://dms.example.test", "username": "dms_login_demo",
                                   "password": "lab-only", "admin_username": "dms_admin_demo", "admin_password": "lab-only"})
        endpoint.insert(ignore_permissions=True)
        log = frappe.get_doc({"doctype": "Pearnly Push Log", "status": "failed", "trigger": "manual", "endpoint": endpoint.name,
                              "business_key": "ID-1-2345-67890-12-3", "http_status": 500,
                              "error_msg": "DMS rejected customer 0812345678", "request_body": "{\"phone\": \"0812345678\"}"})
        log.insert(ignore_permissions=True)
        roles = sorted(frappe.get_roles(user))
        print("账号", user, "角色", roles)
        a = call(site, user, "pearnly_dms.records.push_log.api_list")
        if a["ok"]:
            rows = a["message"] or []
            print("push_log.api_list：拿到", len(rows), "条；字段", sorted(rows[0]) if rows else [],
                  "；例：business_key", mask(rows[0].get("business_key")) if rows else "", "error_msg", mask(rows[0].get("error_msg")) if rows else "")
        else:
            print("push_log.api_list：", a["error"])
        b = call(site, user, "pearnly_core.endpoints.api_list_endpoints", owner_user="")
        if b["ok"]:
            items = (b["message"] or {}).get("items") or []
            print("endpoints.api_list_endpoints：拿到", len(items), "条；字段", sorted(items[0]) if items else [],
                  "；例：system_url", mask(items[0].get("system_url")) if items else "", "username", mask(items[0].get("username")) if items else "",
                  "admin_username", mask(items[0].get("admin_username")) if items else "")
        else:
            print("endpoints.api_list_endpoints：", b["error"])
    finally:
        frappe.db.rollback()
        frappe.destroy()


if __name__ == "__main__":
    main()
