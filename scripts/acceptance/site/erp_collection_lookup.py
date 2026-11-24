"""验收场景的站点侧脚本（只读，在测试租户的 ERP 网页容器里跑）：按发票单号找挂着它的收款记录，等到其中有一笔来自指定入口并且
有了结果（写成 / 失败 / 撤回 / 被退回草稿），把每一笔此刻的样子、写账件读回、落点账套交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_collection_lookup.py <站点> '<JSON 参数>'

参数：invoice（发票单号）、source（只等这个入口的：cc / line / web）、wait（最多等几秒，默认 300；0 = 只看一眼）、exclude（不算的记录名：点写入前已在的）。
给 CC 对话收款的场景用：对话里点了「写入」之后，收款记录是 CC 建的，场景靠它拿记录号、单号去账本读回。只在测试租户跑。
"""
import base64
import json
import sys
import time

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]

site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.collection import service

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    frappe.set_user("Administrator")

    def found():
        frappe.db.rollback()
        names = sorted(set(frappe.get_all(service.INVOICE, filters={"docnum": params["invoice"], "parenttype": service.DOCTYPE},
                                          pluck="parent", limit_page_length=50)))
        out = []
        for name in names:
            doc = frappe.get_doc(service.DOCTYPE, name)
            view = service._view(doc)
            view["job"] = frappe.db.get_value("Pearnly CC Job", doc.job, ["status", "verify_state", "reason"], as_dict=True) if doc.job else None
            view["book_paths"] = [row.path for row in frappe.get_all(books.BOOK, filters={"book_key": doc.book_key}, fields=["path"])]
            view["consent_ref"] = doc.consent_ref or ""
            out.append(view)
        return out

    deadline = time.monotonic() + int(params.get("wait", 300))
    while True:
        records = found()
        skip = set(params.get("exclude") or ())   # 场景点写入前已在的记录（同号发票上前几格留下的）：不算，接着等本格这一笔
        mine = [item for item in records if item["source"] == params.get("source", "cc") and item["name"] not in skip]
        if any(item["status"] in ("written", "failed", "withdrawn") or item.get("returned") for item in mine) or time.monotonic() > deadline:
            break
        time.sleep(3)
    result.update(ok=True, records=records)
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
