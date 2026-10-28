"""实验室：给已有账号开一个会话，打印 SID（流程用例的 Chrome 带它登录）。只在任务自有的实验室跑。

    ../env/bin/python /workspace/scripts/regression/flows/sid.py <站点> <账号>
"""
import os
import sys

if os.environ.get("PEARNLY_FLOWS_LAB") != "1":
    raise SystemExit("Requires the task-owned flows laboratory")

import frappe  # noqa: E402
from frappe.sessions import Session  # noqa: E402
from werkzeug.test import EnvironBuilder  # noqa: E402
from werkzeug.wrappers import Request  # noqa: E402

site, email = sys.argv[1:3]
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites"); frappe.connect()
frappe.local.request_ip = "127.0.0.1"
frappe.local.request = Request(EnvironBuilder(path="/", headers={"Host": site}).get_environ())
frappe.local.cookie_manager = None
frappe.local.session = frappe._dict()
print("SID", Session(email, resume=False, full_name=email, user_type="System User").sid)
frappe.db.commit()
frappe.destroy()
