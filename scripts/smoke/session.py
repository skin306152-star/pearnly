"""在站点的网页容器里给一个账号建一次 Desk 会话，打印 sid（冒烟用；不碰 Keycloak 密码）。

    env/bin/python /tmp/smoke-session.py <站点> <账号>             # 打印 SID <sid>
    env/bin/python /tmp/smoke-session.py <站点> <账号> drop <sid>  # 冒烟收尾：删掉这次建的会话

只给测试租户用。
"""
import sys

import frappe

site, user = sys.argv[1], sys.argv[2]
drop = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "drop" else ""
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    if drop:
        from frappe.sessions import delete_session

        delete_session(drop, user=user, reason="smoke finished")
        frappe.db.commit()
        print("DROPPED")
    else:
        if not frappe.db.exists("User", user) or not frappe.db.get_value("User", user, "enabled"):
            raise SystemExit(f"user not found or disabled: {user}")
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        frappe.local.request_ip = "127.0.0.1"
        frappe.local.request = Request(EnvironBuilder(path="/", headers={"Host": site}).get_environ())
        frappe.local.cookie_manager = None
        frappe.local.session = frappe._dict()
        from frappe.sessions import Session

        session = Session(user, resume=False, full_name=user, user_type="System User")
        frappe.db.commit()
        print("SID", session.sid)
finally:
    frappe.destroy()
