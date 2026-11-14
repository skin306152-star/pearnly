"""流程用例：接 MR.ERP 的公司，选择框里同样是本公司账本档排前、账本编码搜得到（公司接入第 3 批，2026-11-14；
与 Express 那条 `erp_link_ledger_first.py` 同构，判据一字不改）。

备数改成：建一家接 MR.ERP 的公司（``mrerp_enabled``、不接 Express）并设成 admin 的当前公司，账本档挂在它下面、来源 ``ledger`` =
MR.ERP（`mrerp.masters` 整读落库的样子）；收尾删掉这家公司与档、当前公司改回原来的。以下是 Express 那条的说明：

11-14 生产只读：testerp CO-00146 物料调整的物料框不打字只看到手工建的几条——原生链接搜索按最近修改排、一页 10 条，本公司
账本里的 1,079 个物料挤不进去，按 Express 编码也搜不到。这里在页面里用**真的** Link 控件（`frappe.ui.form.make_control`，
各页物料框同一个控件类、同一个 `set_custom_query`）取搜索参数、调原生 `search_link`：

备数：当前公司挂 3 个账本物料（带账本编码），另建 12 个更新的本地物料（照原来的排法它们会把账本物料挤出第一页）。
断言：不打字时第一页先列本公司账本物料；打账本编码搜得到那一个；本地物料照样搜得到；客户框同样按账本编码搜得到。
"""
from __future__ import annotations

import json
import random

from desk_list import IDLE

TITLE = "ERP 选择框（接 MR.ERP 的公司）：本公司账本档排前、账本编码可搜（公司接入第 3 批）"


def prepare(lab, manifest):
    tag = f"{random.Random().randint(0, 0xFFFFF):05X}"
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
from pearnly_erp_portal.inventory import warehouses
user = "admin@erp.regression"
before = frappe.defaults.get_user_default("company", user) or ""
co = frappe.get_doc({{"doctype": "ERP Company", "company_name": f"LAB MR.ERP {tag}", "tax_id": "", "branch": "", "is_active": 1,
                     "pos_enabled": 0, "express_enabled": 0, "company_type": "general", "mrerp_enabled": 1,
                     "mrerp_account_set": "6:1"}}).insert(ignore_permissions=True).name
warehouses.ensure_for_company(co, "Main")
frappe.defaults.set_user_default("company", co, user)
ledger = []
for i in range(3):
    doc = frappe.get_doc({{"doctype": "ERP Product", "product_name": f"LEDGER {tag} {{i}}", "base_unit": "ชิ้น", "is_active": 1,
                          "express_company": co, "express_code": f"LX{tag}{{i}}", "origin": "mrerp",
                          "ledger": "MR.ERP"}})
    doc.flags.express_sync = True
    ledger.append(doc.insert(ignore_permissions=True).name)
local = [frappe.get_doc({{"doctype": "ERP Product", "product_name": f"LOCAL {tag} {{i}}", "base_unit": "ชิ้น", "is_active": 1}})
         .insert(ignore_permissions=True).name for i in range(12)]
cust = frappe.get_doc({{"doctype": "ERP Customer", "customer_name": f"LEDGER CUST {tag}", "party_type": "person", "is_active": 1,
                       "express_company": co, "express_code": f"LC{tag}", "ledger": "MR.ERP"}})
cust.flags.express_sync = True
cust.insert(ignore_permissions=True)
frappe.db.commit()
print(json.dumps({{"company": co, "before": before, "ledger": ledger, "local": local, "customer": cust.name}}))
""")
    return {"tag": tag, **made, "cases": []}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    search = ("const ask=async (doctype, txt, filters)=>{const box=$('<div>').appendTo(document.body);"
              "const c=frappe.ui.form.make_control({df:{fieldtype:'Link', fieldname:'probe', options:doctype,"
              " get_query:()=>({filters: filters||{}})}, parent: box, render_input: true});"
              "const args=c.get_search_args(txt); const r=await frappe.call({method:'frappe.desk.search.search_link', args, type:'POST'});"
              "box.remove(); return {query: args.query||'', values: (r.message||[]).map(x=>x.value)};};")
    return [
        {"name": "选择框搜索", "js": IDLE + search +
         "frappe.set_route('pearnly-stock-adjust'); await idle();"
         "const empty=await ask('ERP Product', '', {is_active:1});"
         f"const code=await ask('ERP Product', 'LX'+{tag}+'1', {{is_active:1}});"
         f"const local=await ask('ERP Product', 'LOCAL '+{tag}+' 5', {{is_active:1}});"
         f"const cust=await ask('ERP Customer', 'LC'+{tag}, {{}});"
         "return {empty, code, local, cust};"},
    ]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "选择框搜索"), {})
    empty, code, local, cust = (seen.get(key) or {} for key in ("empty", "code", "local", "cust"))
    return [
        {"case": "物料框走 ERP 的选择框查询", "seen": empty.get("query"),
         "ok": empty.get("query") == "pearnly_erp_portal.masters.link_search.search"},
        {"case": "不打字时第一页先列本公司账本物料（比它们新的本地物料排后）", "seen": empty.get("values"),
         "ok": set(params["ledger"]) <= set((empty.get("values") or [])[:len(params["ledger"]) + 20])
         and all(name in params["ledger"] or name not in params["local"] for name in (empty.get("values") or [])[:3])},
        {"case": "打账本编码搜得到那一个", "seen": code.get("values"), "ok": (code.get("values") or [None])[0] == params["ledger"][1]},
        {"case": "本地物料照样搜得到", "seen": local.get("values"), "ok": params["local"][5] in (local.get("values") or [])},
        {"case": "客户框按账本编码搜得到", "seen": cust.get("values"), "ok": params["customer"] in (cust.get("values") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.db.delete("ERP Product", {{"name": ["in", {params['ledger'] + params['local']!r}]}})
frappe.db.delete("ERP Customer", {{"name": {params['customer']!r}}})
frappe.db.delete("ERP Warehouse", {{"company": {params['company']!r}}})
frappe.db.delete("ERP Company", {{"name": {params['company']!r}}})
frappe.defaults.set_user_default("company", {params['before']!r}, "admin@erp.regression")
frappe.db.commit()
print("{{}}")
""")
