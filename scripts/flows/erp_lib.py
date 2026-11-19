"""ERP 流程用例共用的几样东西（不是流程，不登记）：从数据清单取公司 / 仓 / 商品，页面步骤里的等待与操作片段。

数据一律取 `scripts/regression/datasets.py` 的清单：新开户 `products` 是 {认法: {product, scan}}，老数据是商品列表（编码、条码）。
页面片段拼进步骤 JS 用：`IDLE` 等请求都回来，`JS` 是取元素、填值、点下拉项这些小函数。
"""
from __future__ import annotations

import json

# 按条件等，不按固定时长：请求都回来（jQuery 在途数连续两次为 0），最多 20 秒（同 erp_stocktake_scan.py）
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
JS = IDLE + """
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const vis=el=>!!el&&el.getClientRects().length>0&&getComputedStyle(el).visibility!=='hidden';
const all=(sel,root=document)=>[...root.querySelectorAll(sel)].filter(vis);
const until=async(fn,ms=15000)=>{const end=Date.now()+ms;let v;while(Date.now()<end){try{v=await fn();}catch(e){v=null;}if(v)return v;await sleep(200);}return v;};
const setInput=(el,value)=>{if(!el)return false;el.focus();el.value=value;el.dispatchEvent(new Event('input',{bubbles:true}));
  el.dispatchEvent(new Event('change',{bubbles:true}));return true;};
// 只发 input 不发 change：change 会让 Link 控件先按单据名校验，码不是单据名就清空、按空串搜，下拉只剩最近改过的 10 件（10-31）
const pick=async(input,text,match)=>{input.focus();input.value=text;input.dispatchEvent(new Event('input',{bubbles:true}));
  input.dispatchEvent(new KeyboardEvent('keyup',{bubbles:true,key:'a'}));
  const own=()=>{const id=input.getAttribute('aria-owns');const ul=id&&document.getElementById(id);return ul?[...ul.querySelectorAll('[role=option], li')]:[];};
  // 有商品名时按名称**整段相等**认（原生下拉名称在 <strong>，组合框是「名称 · 码 · 单号」的第一段）：只看「包含」会把「… รหัส」认成「… รหัสภายใน」，
  // 搜索结果回来前下拉里还是旧列表，先命中的就是错的那件（10-31）
  const head=l=>{const s=l.querySelector('strong');return (s?s.textContent:l.textContent.split(' · ')[0]).trim();};
  const li=await until(()=>[...own(),...document.querySelectorAll('.awesomplete li, ul[role=listbox] li')].find(l=>match?head(l)===match:l.textContent.includes(text)),10000);
  if(!li)return false;li.dispatchEvent(new MouseEvent('mousedown',{bubbles:true}));li.click();await sleep(400);return true;};
const call=(method,args,type)=>frappe.call({method,args,type:type||'POST'}).then(r=>r.message);
const pageOf=route=>all('.page-container').find(el=>el.getAttribute('data-page-route')===route);
const alerts=()=>all('.alert-message, .desk-alert .alert-message').map(e=>e.textContent.trim()).join(' | ').slice(0,300);
const msg=()=>all('.modal.show .msgprint, .modal.show .modal-body').map(e=>e.textContent.trim()).join(' | ').slice(0,300);
"""


# 入库 / 出库 / 物料调整弹窗（11-19 起仓库按行，同 Express 原生 OU / JU / PI）：单头不放仓，明细每一行是一个小表单（官方 FieldGroup，
# 有 product / qty / warehouse 三格）。`LINES` 记下页面建的 FieldGroup，`entryLines(d)` 取弹窗 d 里的明细行（按顺序）。
LINES = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const f=frappe.ui.FieldGroup.prototype.make;"
         "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return f.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}"
         "const entryLines=d=>window.__fgs.filter(f=>f!==d&&f.fields_dict&&f.fields_dict.product&&f.fields_dict.qty&&f.fields_dict.warehouse"
         "&&$.contains(d.$wrapper[0],f.fields_dict.product.$wrapper[0]));")


def company(manifest) -> str:
    return manifest.get("company") or manifest["companies"][0]


def warehouses(manifest) -> list[dict]:
    co = company(manifest)
    return [w for w in manifest["warehouses"] if w.get("company", co) == co]


def codes(manifest) -> dict:
    """{认法: {product, scan}}：新开户照清单（去掉按台那件）；老数据第一件按编码、第二件按条码。"""
    if manifest["dataset"] == "new":
        return {key: item for key, item in manifest["products"].items() if key != "by_unit"}
    first, second = manifest["products"][0], manifest["products"][1]
    return {"by_code": {"product": first["name"], "scan": first["code"]},
            "by_barcode": {"product": second["name"], "scan": second["barcode"]}}


def as_user(lab, site, user, body):
    """以某岗位账号在站点里跑一段（库存岗、收银岗等），结尾自己打印 JSON。"""
    return lab.python(site, f"frappe.set_user({user!r})\n" + body)


def stock_of(lab, site, product, warehouse) -> float:
    return lab.python(site, f"""
qty = frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where product=%s and warehouse=%s", ({product!r}, {warehouse!r}))[0][0]
print(json.dumps({{"qty": float(qty)}}))
""")["qty"]


def ledger_case(lab, site, doctype, names, queued) -> dict:
    """单据上的账本栏是通用列（评估 2026-11-02 §12 第 6 条，主会话要求流程里断言）：DocType 里没有旧的 ``express_*`` 栏；
    每张单 ``ledger_state`` 有值就记了账本 ``ledger_target = express``，没值就没有账本；``queued`` 给了真 / 假时，排没排进账本
    也要对上（接了 Express 的公司、单据接上后开始 → 排进；没接、关了「接 Express」→ 不排）。"""
    got = lab.python(site, f"""
rows = frappe.get_all({doctype!r}, filters={{"name": ["in", {list(names)!r}]}}, fields=["name", "ledger_state", "ledger_target"],
                      limit_page_length={len(names) or 1})
old = [f.fieldname for f in frappe.get_meta({doctype!r}).fields if f.fieldname.startswith("express_")
       and f.fieldname not in ("express_summary",)]
print(json.dumps({{"rows": rows, "old": old}}, default=str))
""")
    rows = got["rows"]
    ok = bool(rows) and len(rows) == len(set(names)) and not got["old"] \
        and all(bool(row.get("ledger_state")) == (row.get("ledger_target") == "express") for row in rows)
    if queued is not None:
        ok = ok and all(bool(row.get("ledger_state")) == bool(queued) for row in rows)
    return {"case": f"{doctype} 账本栏是通用列（排进 {queued}）", "saved": got, "ok": ok}


def js_value(value) -> str:
    return json.dumps(value, ensure_ascii=False)


def void_documents(lab, site, names, doctype="Purchase Document", reason="flow cleanup") -> dict:
    """流程造的采购 / 销售单在 cleanup 里作废（冲回库存与付款），连同页面上「修改」出来的新单（`corrected_from` 一路跟下去），
    没写的写账任务撤掉：不留成后面流程「这个月等着录」的单（11-10 全套：erp.document.express 每遍在上个月留两张，积到 51 张，
    越过 cc.operator 开始前卡片一页 50 张，那格跟着失败）。草稿、已作废的跳过。"""
    names = [name for name in names if name]
    if not names:
        return {}
    module = {"Purchase Document": "pearnly_erp_portal.purchasing.entry", "ERP Sales Document": "pearnly_erp_portal.sales.entry"}[doctype]
    return lab.python(site, f"""
import importlib
from pearnly_core.cc_desktop import jobs
entry = importlib.import_module({module!r})
frappe.set_user("Administrator")
todo, seen, done = list({names!r}), set(), []
while todo:
    name = todo.pop(0)
    if name in seen or not frappe.db.exists({doctype!r}, name):
        continue
    seen.add(name)
    if frappe.get_meta({doctype!r}).has_field("corrected_from"):
        todo += frappe.get_all({doctype!r}, filters={{"corrected_from": name}}, pluck="name", limit_page_length=20)
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_doctype": {doctype!r}, "source_name": name,
                              "status": ["in", ["queued", "manual"]]}}, pluck="name", limit_page_length=20):
        jobs.cancel(job, "flow cleanup")
    if frappe.db.get_value({doctype!r}, name, "status") == "submitted":
        entry.cancel(name, {reason!r})
        done.append(name)
frappe.db.commit()
print(json.dumps({{"voided": done}}))
""")


def ensure_documents(lab, manifest) -> dict:
    """这家公司七类单据（入库、出库、入了库的采购单、销售单、已过账盘点、收银小票、已交班的班次）各保证有一张：已有就不动，
    没有就按正式函数建（请求号按公司固定，可重复跑）。打印、导出这类「拿库里已有单据」的流程先调它，不靠别的流程先建单。"""
    co = company(manifest)
    product = codes(manifest)["by_code"]["product"]
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.cashier import sales as pos, shifts, terminals
from pearnly_erp_portal.inventory import count, documents
from pearnly_erp_portal.masters import company as companies, customers
from pearnly_erp_portal.purchasing import entry as purchase, suppliers
from pearnly_erp_portal.sales import entry as sales
co, product = {co!r}, {product!r}
frappe.set_user("admin@erp.regression")
companies.set_active_company(co)
stores = frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}}, pluck="name", order_by="creation asc")
till = terminals.ensure_default(company=co)
if not frappe.db.get_value("ERP POS Terminal", till, "warehouse"):  # 接 Express 的公司默认那台不带仓：同 erp_pos_shift 挂第一个仓
    first = frappe.get_all("ERP Warehouse", filters={{"company": co, "is_active": 1}}, pluck="name", order_by="express_code asc, creation asc")[0]
    terminals.update_terminal(till, warehouse=first)
wh = frappe.db.get_value("ERP POS Terminal", till, "warehouse")
shelf = (frappe.get_all("ERP Warehouse Location", filters={{"parent": wh, "parenttype": "ERP Warehouse"}}, pluck="location_name",
                        order_by="idx asc", limit=1) or [""])[0]
tills = frappe.get_all("ERP POS Terminal", filters={{"company": co}}, pluck="name")
made = []
def has(doctype, filters):
    return bool(frappe.get_all(doctype, filters=filters, pluck="name", limit_page_length=1))
def entry(direction, qty):  # 每行一个仓（11-19 起库存只按物料 + 仓库，行上不放仓位）
    documents.create_entry(direction=direction, posting_date=frappe.utils.nowdate(), warehouse=wh, reason="flow seed", submit=True,
                           lines=[{{"product": product, "qty": qty, "unit_cost": 10, "warehouse": wh}}])
    made.append("stock " + direction)
def held():  # 这个仓有货（出库、收银从这里出，不扣成负数）
    return float(frappe.db.get_value("ERP Stock Balance", {{"product": product, "warehouse": wh}}, "qty_on_hand") or 0) >= 2
if not has("ERP Stock Entry", {{"direction": "in", "status": "submitted", "warehouse": ["in", stores]}}) or not held():
    entry("in", 10)
if not has("ERP Stock Entry", {{"direction": "out", "status": "submitted", "warehouse": ["in", stores]}}):
    entry("out", 1)
line = {{"product": product, "item_type": "goods", "description": "flow seed", "qty": 1, "unit": "ชิ้น", "unit_price": 100, "vat_rate": 7,
         "warehouse": wh}}
if not has("Purchase Document", {{"company": co, "status": "submitted", "stock_posted_at": ["is", "set"]}}):
    supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายโฟลว์"}}) or suppliers.create_supplier(supplier_name="ผู้ขายโฟลว์")["name"]
    purchase.post({{"company": co, "supplier": supplier, "doc_date": "2026-10-01", "currency": "THB", "fx_rate": 1, "has_vat": 1,
                   "price_mode": "exclusive", "doc_kind": "purchase_invoice", "update_stock": 1, "doc_no": "FLOW-SEED-IN", "warehouse": wh,
                   "lines": [line]}}, request_id="flow-seed-purchase-" + co)
    made.append("purchase")
if not has("ERP Sales Document", {{"company": co, "status": "submitted", "doc_kind": "receipt", "source": ["!=", "pos"]}}):
    customer = frappe.db.get_value("ERP Customer", {{"customer_name": "ลูกค้าโฟลว์"}}) or customers.create_customer(customer_name="ลูกค้าโฟลว์")["name"]
    sales.post({{"company": co, "customer": customer, "doc_date": "2026-10-05", "has_vat": 1, "vat_rate": 7, "source": "manual",
                "doc_kind": "receipt", "warehouse": wh, "paid_amount": 107, "payment_method": "cash", "payment_date": "2026-10-05",
                "lines": [dict(line, vat_applicable=1)]}}, request_id="flow-seed-sale-" + co)
    made.append("sale")
frappe.db.commit()
if not has("ERP Stock Count", {{"warehouse": ["in", stores], "status": "Posted"}}):
    frappe.set_user("stock@erp.regression")
    companies.set_active_company(co)
    posted = count.create_count(title="flow seed " + frappe.generate_hash(length=6), warehouse=wh)["name"]
    count.add_entry(count=posted, product=product, qty=1, warehouse=wh, location=shelf or None, request_id="flow-seed-count-" + posted)
    count.post_count(posted, review_token=count.review_count(posted)["review_token"])  # 结束 + 生成物料调整单 + 提交（11-06 起结束不入账）
    frappe.db.commit()
    made.append("count")
    frappe.set_user("admin@erp.regression")
    companies.set_active_company(co)
if not (has("ERP Sale", {{"terminal": ["in", tills]}}) and has("ERP Shift", {{"terminal": ["in", tills], "status": "closed"}})):
    for shift in frappe.get_all("ERP Shift", filters={{"status": "open", "terminal": till}}, pluck="name"):
        shifts.close_shift(shift, counted={{"cash": shifts.summary(shift, own_only=False)["expected_cash"]}})
    shift = shifts.open_shift(terminal=till, opening_float=500)["name"]
    price = float(frappe.db.get_value("ERP Product", product, "unit_price") or 100)
    pos.create_sale(lines=[{{"product": product, "qty": 1, "unit_price": price}}], payments=[{{"method": "cash", "amount": price}}],
                    shift=shift, terminal=till, client_uuid="flow-seed-pos-" + shift)
    shifts.close_shift(shift, counted={{"cash": shifts.summary(shift, own_only=False)["expected_cash"]}})
    frappe.db.commit()
    made.append("pos")
frappe.db.commit()
print(json.dumps({{"company": co, "made": made}}))
""")
