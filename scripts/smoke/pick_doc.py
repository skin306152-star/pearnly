"""在站点的网页容器里只读挑一张「不是这个账号建的」最新单据，打印 NAME <单号>（冒烟 not_permitted 探针用：受限账号打开别人的单）。

    env/bin/python /tmp/smoke-pick.py <站点> <单据类型> <账号>

只读，不写库；只给测试租户用。
"""
import base64
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]  # run.py 传参用 base64

site, doctype, user = sys.argv[1], sys.argv[2], sys.argv[3]
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
try:
    rows = frappe.get_all(doctype, filters={"owner": ["!=", user]}, pluck="name", order_by="creation desc", limit_page_length=1)
    print("NAME", rows[0] if rows else "")
finally:
    frappe.destroy()
