"""流程用例：LINE 卡片「编辑」打开另一家公司的单，不改网页的当前公司（线上 6，11-15）。

老数据那套有两家公司（A、B）。管理员当前公司是 A，在实验室里建一张 B 公司、已识别的 LINE 采购收单（不跑识别、不经 LINE），
另建一张 A 公司的。页面（管理员）：
1. 打开 `/desk/pearnly-purchase/line/<B 的那张>`：这一页按 B 取数（`p.page_company` 是 B，明细行的仓是 B 的仓、单头没有仓
   （11-19 起仓库按行），按当前公司搜仓只出 B 的仓），本人的当前公司（左上角、库里的用户默认值）仍是 A；
2. 提交：转去这张单的查看页，打得开（查看页仍按 B）；
3. 回采购列表：这一页的公司还原成 A；
4. 同页再开 A 的那张：不进入页面级公司；
5. 打开一张已不在的：弹原生提示、回列表，页面级公司是空的。
库里：两张都是各自公司的；本人的用户默认值从头到尾是 A。另以采购岗（只能用 A）与销售岗（没有采购岗）各开一张 B 公司的卡片：服务端拒。
"""
from __future__ import annotations

import base64
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from erp_lib import JS, void_documents  # noqa: E402

TITLE = "LINE 卡片编辑别家公司的单：只这一页按那家，网页当前公司不变"
PIXEL = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def _intake(company, user, tag, warehouse):
    return f"""
file = frappe.get_doc({{"doctype": "File", "file_name": "line-{tag}.png", "is_private": 1,
                        "content": base64.b64decode({base64.b64encode(PIXEL).decode()!r})}}).insert(ignore_permissions=True)
result = {{"doc_no": "LINE-{tag}", "doc_date": "2026-11-15", "doc_kind": "purchase_invoice", "has_vat": 1,
          "supplier_name": "flow other company {tag}",
          "lines": [{{"item_type": "service", "description": "ค่าบริการ {tag}", "qty": 1, "unit_price": 30, "vat_rate": 7}}]}}
doc = frappe.get_doc({{"doctype": intake.DOCTYPE, "status": intake.READY, "direction": "purchase", "sent_by": {user!r},
                       "line_user_id": "U-flow-{tag}", "language": "zh", "media_type": "image", "company": {company!r}, "party": "@doc",
                       "is_paid": 1, "payment_method": "cash", "source_file": file.file_url, "source_file_name": file.name,
                       "file_hash": hashlib.sha256(b"{tag}").hexdigest(), "received_at": frappe.utils.now(),
                       "line_choices": json.dumps({{"warehouse": {warehouse!r}}}),
                       "result": json.dumps(result, ensure_ascii=False)}}).insert(ignore_permissions=True)
"""


def prepare(lab, manifest):
    a, b = manifest["companies"][:2]
    tag = uuid.uuid4().hex[:6]
    wh = {co: next(w["name"] for w in manifest["warehouses"] if w.get("company") == co) for co in (a, b)}
    for co in (a, b):  # 这一格关掉「接 Express」，单子不排进账本；格结束时 run_flow 还原
        lab.set_fields(manifest["site"], "ERP Company", co, {"express_enabled": 0})
    out = lab.python(manifest["site"], f"""
import base64, hashlib
from pearnly_erp_portal.integrations import intake
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({a!r})
{_intake(b, "admin@erp.regression", tag + "b", wh[b])}
other = doc.name
{_intake(a, "admin@erp.regression", tag + "a", wh[a])}
mine = doc.name
frappe.set_user("purchase@erp.regression")
{_intake(b, "purchase@erp.regression", tag + "p", wh[b])}
foreign = doc.name
frappe.set_user("sales@erp.regression")
{_intake(b, "sales@erp.regression", tag + "s", wh[b])}
no_post = doc.name
frappe.db.commit()
print(json.dumps({{"other": other, "mine": mine, "foreign": foreign, "no_post": no_post}}))
""")
    return {**out, "a": a, "b": b, "wh_a": wh[a], "wh_b": wh[b], "tag": tag}


def cleanup(lab, manifest, params):
    made = lab.python(manifest["site"], f"""
names = [{params.get('other')!r}, {params.get('mine')!r}]
print(json.dumps({{"purchases": [p for p in (frappe.db.get_value("ERP LINE Intake", n, "purchase") for n in names if n) if p]}}))
""")
    void_documents(lab, manifest["site"], made["purchases"])
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
frappe.set_user("admin@erp.regression")
companies.set_active_company({params['a']!r})
frappe.db.commit()
print(json.dumps({{"ok": True}}))
""")


def steps(params, camera_code=None):
    state = """const state=()=>({page: frappe.pearnly.page_company.current(), boot: frappe.boot.pearnly_company,
  header: frappe.pearnly.header_title && frappe.pearnly.header_title(), route: frappe.get_route()});
const heads=()=>(window.__fgs||[]).filter(f=>f.fields_dict&&f.fields_dict.bulk_edit&&f.wrapper&&(f.wrapper.jquery?f.wrapper[0]:f.wrapper).isConnected);
"""
    capture = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
               "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};"
               "frappe.ui.FieldGroup.prototype.__cap=1;}")
    return [
        {"name": "打开别家公司的卡片", "js": JS + capture + state + f"""
frappe.set_route('pearnly-purchase', 'line', {json.dumps(params['other'])});
await until(()=>heads().length, 25000); await idle(); await sleep(800);
const head=heads().pop();
const searched=await frappe.call({{method: 'frappe.desk.search.search_link', args: {{doctype: 'ERP Warehouse', txt: '',
  filters: {{company: frappe.boot.pearnly_company}}, page_length: 50}}}});
const context=(await frappe.call({{method: 'pearnly_erp_portal.masters.api.api_company_context'}})).message;
const line=(window.__fgs||[]).filter(f=>f.fields_dict&&f.fields_dict.unit_price&&f.fields_dict.stock_in&&f.wrapper
  &&(f.wrapper.jquery?f.wrapper[0]:f.wrapper).isConnected)[0];
return {{...state(), warehouse: line&&line.get_value('warehouse'),
         head_place: head ? Object.keys(head.fields_dict).filter(k=>['warehouse','location'].includes(k)) : null,
         searched: (searched.message||searched.results||[]).map(r=>r.value||r.name),
         context_current: context&&context.current}};"""},
        {"name": "提交", "js": JS + state + """
const btn=all('.page-actions .primary-action, .page-actions .btn-primary')[0]; if(!btn) return {error:'no submit button'};
btn.click(); await sleep(1500);
const yes=all('.modal.show .btn-primary').find(b=>/是|确定|提交|Yes|Submit/.test(b.textContent)); if (yes) yes.click();
const moved=await until(()=>frappe.get_route()[1] && frappe.get_route()[1]!=='line', 25000); await idle(); await sleep(1500);
return {...state(), moved: !!moved, msg: msg(), title: (document.querySelector('.page-head .title-text')||{}).textContent||''};"""},
        {"name": "回列表", "js": JS + state + """
frappe.set_route('pearnly-purchase'); await sleep(1200); await idle();
return state();"""},
        {"name": "同页开本公司的卡片", "js": JS + capture + state + f"""
frappe.set_route('pearnly-purchase', 'line', {json.dumps(params['other'])}); await sleep(300);
frappe.set_route('pearnly-purchase', 'line', {json.dumps(params['mine'])});
await until(()=>heads().length && frappe.get_route()[2]==={json.dumps(params['mine'])}, 25000); await idle(); await sleep(800);
return state();"""},
        {"name": "打开已不在的", "js": JS + state + """
frappe.set_route('pearnly-purchase', 'line', 'NOPE-LINE-0001');
const said=await until(()=>msg(), 15000);
await until(()=>frappe.get_route().length===1, 15000); await sleep(400);
frappe.msg_dialog?.hide?.();
return {...state(), said: said||''};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    opened, sent, back, mine, gone = (by.get(k, {}) for k in ("打开别家公司的卡片", "提交", "回列表", "同页开本公司的卡片", "打开已不在的"))
    a, b = params["a"], params["b"]
    return [
        {"case": "the card page reads as B: page company, boot company, the line warehouse and a warehouse search",
         "seen": opened, "ok": opened.get("page") == b and opened.get("boot") == b and opened.get("warehouse") == params["wh_b"]
         and opened.get("head_place") == []
         and params["wh_b"] in (opened.get("searched") or []) and params["wh_a"] not in (opened.get("searched") or [])
         and opened.get("context_current") == b},
        {"case": "submitting opens the new document's view and the page still reads as B", "seen": sent,
         "ok": bool(sent.get("moved")) and sent.get("page") == b and "Not permitted" not in str(sent.get("msg"))
         and "没有权限" not in str(sent.get("msg"))},
        {"case": "back on the list the page company is restored to A", "seen": back,
         "ok": back.get("page") is None and back.get("boot") == a},
        {"case": "opening a card of A on the same page does not enter a page company", "seen": mine,
         "ok": mine.get("page") is None and mine.get("boot") == a},
        {"case": "a gone card says so, goes back to the list and leaves no page company", "seen": gone,
         "ok": bool(gone.get("said")) and gone.get("page") is None and gone.get("boot") == a},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_core.account import user_defaults
from pearnly_erp_portal.integrations import intake
row = frappe.db.get_value("ERP LINE Intake", {params['other']!r}, ["status", "purchase"], as_dict=True)
made_company = frappe.db.get_value("Purchase Document", row.purchase, "company") if row.purchase else None
refused = {{}}
for user, name in (("purchase@erp.regression", {params['foreign']!r}), ("sales@erp.regression", {params['no_post']!r})):
    frappe.set_user(user)
    try:
        intake.open_for_edit(name)
        refused[user] = False
    except frappe.PermissionError:
        refused[user] = True
    frappe.db.rollback()
print(json.dumps({{"status": row.status, "made_company": made_company, "refused": refused,
                  "admin_default": user_defaults.current("company", "admin@erp.regression")}}))
""")
    return [
        {"case": "the B card is recorded into B", "seen": got, "ok": got["status"] == "confirmed" and got["made_company"] == params["b"]},
        {"case": "the admin's own current company stayed A the whole time", "seen": got["admin_default"],
         "ok": got["admin_default"] == params["a"]},
        {"case": "someone who may not use B, and someone without the purchase post, are refused",
         "seen": got["refused"], "ok": all(got["refused"].values()) and len(got["refused"]) == 2},
    ]
