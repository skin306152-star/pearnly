"""验收场景的站点侧脚本（只读，在测试租户的 ERP 网页容器里跑）：代录对话开之前，这家公司这个月「待录的单」有哪几张——
就是代录写入计划会列进去的那一份（产品的 `express.operator.sources`，按对话里登录的员工取）。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_operator_sources.py <站点> '<JSON 参数>'

参数：user（对话里登录的员工）、company（ERP Company）、period（YYYY-MM）。
交回 ``documents``（doc_id、种类、日期、对方、单号、合计）与 ``total``。11-16 晚 operator.talk 首跑：站点上有前几格残留的待写单，计划出了 4 张、
两张选择卡——场景据此在开对话前核「本月待录只有本格造的这一张」，不是就记前置条件不满足并列出是哪些。不建、不改任何东西。只在测试租户跑。
"""
import base64
import json
import sys

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.express import operator

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    frappe.set_user(params["user"])
    found = operator.sources(params["company"], params["period"], 0, operator.SOURCES_MAX)
    frappe.db.rollback()
    keep = ("doc_id", "kind", "date", "party", "ref", "total", "stopped", "other_month")
    result.update(ok=True, total=found.get("total"), documents=[{key: row.get(key) for key in keep if key in row}
                                                                for row in found.get("documents") or []])
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
