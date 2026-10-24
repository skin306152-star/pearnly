"""在站点的网页容器里给一个账号建一次 Desk 会话，打印 sid（冒烟用；不碰 Keycloak 密码）。

    env/bin/python /tmp/smoke-session.py <站点> <账号>             # 打印 SID <sid>
    env/bin/python /tmp/smoke-session.py <站点> <账号> company <公司>   # 先把这个账号的当前公司切到 <公司>（ERP 大账套跑时限），
                                                                     # 另打印 PREV <原公司>，收尾时还原
    env/bin/python /tmp/smoke-session.py <站点> <账号> drop <sid> [<原公司>]  # 冒烟收尾：删掉这次建的会话，还原当前公司

只给测试租户用。
"""
import base64
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

site, user = sys.argv[1], sys.argv[2]
drop = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "drop" else ""
restore = sys.argv[5] if drop and len(sys.argv) > 5 else None
company = sys.argv[4] if len(sys.argv) > 4 and sys.argv[3] == "company" else ""
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    if drop:
        from frappe.sessions import delete_session

        delete_session(drop, user=user, reason="smoke finished")
        if restore is not None:
            frappe.defaults.set_user_default("company", restore if restore != "-" else None, user)
        frappe.db.commit()
        print("DROPPED")
    else:
        if not frappe.db.exists("User", user) or not frappe.db.get_value("User", user, "enabled"):
            raise SystemExit(f"user not found or disabled: {user}")
        from werkzeug.test import EnvironBuilder
        from werkzeug.wrappers import Request

        if company:
            # 当前公司就是用户默认值里的 company（ERP 切公司的官方做法），冒烟收尾还原
            print("PREV", frappe.defaults.get_user_default("company", user) or "-")
            frappe.defaults.set_user_default("company", company, user)
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
