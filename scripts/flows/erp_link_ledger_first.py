"""流程用例：物料 / 客户选择框本公司账本档排前、账本编码搜得到（公司接入重做第 2 批，2026-11-14）。

11-14 生产只读：testerp CO-00146 物料调整的物料框不打字只看到手工建的几条——原生链接搜索按最近修改排、一页 10 条，本公司
账本里的 1,079 个物料挤不进去，按 Express 编码也搜不到。这里在页面里用**真的** Link 控件（`frappe.ui.form.make_control`，
各页物料框同一个控件类、同一个 `set_custom_query`）取搜索参数、调原生 `search_link`：

备数：当前公司挂 3 个账本物料（带账本编码），另建 12 个更新的本地物料（照原来的排法它们会把账本物料挤出第一页）。
断言：本次备的档里账本物料排最前（11-15 加：本公司随单新建、没有账本编码的 12 条名字更靠前，也排在后面）；打账本编码搜得到那一个；本地物料照样搜得到；客户框同样按账本编码搜得到。
"""
from __future__ import annotations

import json
import random

from desk_list import IDLE

TITLE = "ERP 选择框：本公司账本档排前、账本编码可搜（公司接入重做第 2 批）"


def prepare(lab, manifest):
    tag = f"{random.Random().randint(0, 0xFFFFF):05X}"
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
from pearnly_erp_portal.masters import company as companies
co = frappe.db.get_value("User", "admin@erp.regression", "name") and (frappe.defaults.get_user_default("company", "admin@erp.regression")
     or companies.allowed_companies("admin@erp.regression")[0])
ledger = []
for i in range(3):
    doc = frappe.get_doc({{"doctype": "ERP Product", "product_name": f"LEDGER {tag} {{i}}", "base_unit": "ชิ้น", "is_active": 1,
                          "express_company": co, "express_code": f"LX{tag}{{i}}", "origin": "express"}})
    doc.flags.express_sync = True
    ledger.append(doc.insert(ignore_permissions=True).name)
local = [frappe.get_doc({{"doctype": "ERP Product", "product_name": f"LOCAL {tag} {{i}}", "base_unit": "ชิ้น", "is_active": 1}})
         .insert(ignore_permissions=True).name for i in range(12)]
# 本公司随单 / 盘点新建、还没写进账本的（归这家公司、没有账本编码；名字排在账本物料前面）
fresh = [frappe.get_doc({{"doctype": "ERP Product", "product_name": f"AAA {tag} {{i}}", "base_unit": "ชิ้น", "is_active": 1,
                         "express_company": co}}).insert(ignore_permissions=True).name for i in range(12)]
cust = frappe.get_doc({{"doctype": "ERP Customer", "customer_name": f"LEDGER CUST {tag}", "party_type": "person", "is_active": 1,
                       "express_company": co, "express_code": f"LC{tag}"}})
cust.flags.express_sync = True
cust.insert(ignore_permissions=True)
frappe.db.commit()
print(json.dumps({{"company": co, "ledger": ledger, "local": local, "fresh": fresh, "customer": cust.name}}))
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
         f"const tagged=await ask('ERP Product', {tag}, {{is_active:1}});"
         f"const code=await ask('ERP Product', 'LX'+{tag}+'1', {{is_active:1}});"
         f"const local=await ask('ERP Product', 'LOCAL '+{tag}+' 5', {{is_active:1}});"
         f"const cust=await ask('ERP Customer', 'LC'+{tag}, {{}});"
         "return {empty, tagged, code, local, cust};"},
    ]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "选择框搜索"), {})
    empty, tagged, code, local, cust = (seen.get(key) or {} for key in ("empty", "tagged", "code", "local", "cust"))
    first = (tagged.get("values") or [])[:len(params["ledger"])]
    return [
        {"case": "物料框走 ERP 的选择框查询", "seen": empty.get("query"),
         "ok": empty.get("query") == "pearnly_erp_portal.masters.link_search.search"},
        # 不打字的第一页由站点里已有多少账本档决定（数据多的实验室里备的 3 条本来就在后面），只看本地档不插到账本档前面
        {"case": "不打字时第一页没有本地物料排在前面", "seen": empty.get("values"),
         "ok": all(name not in params["local"] for name in (empty.get("values") or [])[:3])},
        # 11-15：本公司随单 / 盘点新建、没有账本编码的档（名字更靠前）也排在账本物料之后
        {"case": "本次备的档里账本物料排最前（先于本公司无编码档与本地档）", "seen": tagged.get("values"),
         "ok": sorted(first) == sorted(params["ledger"])},
        {"case": "打账本编码搜得到那一个", "seen": code.get("values"), "ok": (code.get("values") or [None])[0] == params["ledger"][1]},
        {"case": "本地物料照样搜得到", "seen": local.get("values"), "ok": params["local"][5] in (local.get("values") or [])},
        {"case": "客户框按账本编码搜得到", "seen": cust.get("values"), "ok": params["customer"] in (cust.get("values") or [])},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.db.delete("ERP Product", {{"name": ["in", {params['ledger'] + params['local'] + params.get('fresh', [])!r}]}})
frappe.db.delete("ERP Customer", {{"name": {params['customer']!r}}})
frappe.db.commit()
print("{{}}")
""")
