"""流程用例：ERP 采购单页头动钱 / 改账的按钮逐个实点（清产品地图「没测试」存量基线，11-07）。

采购岗打开采购记录：
- 列表页头「新建」：点按钮（不是 `set_route`），进到新建页。
- 草稿「删除」：搜出 prepare 建的草稿、勾选它，页头变成「删除 (1)」，点下去、原生确认框点是 → 库里这张草稿没了。
- 已提交未付的单（一行服务、预扣税 3%）：页头「付款」开原生弹窗，金额改成 50 提交 → 库里付款记录多一行 50、付款状态部分付；
  「…」菜单「补开收据」「预扣税凭证」各点一次 → 库里这张单各多一份生成的 PDF 附件（文件在）；
  「修改」→ 进修改页直接提交 → 原单作废（付款冲回）、新单记着改自原单且已提交。
- 另一张已提交、入了库的单：「…」菜单「作废」开弹窗填原因确认 → 库里作废、原因是填的那句、这张单入的那 1 件冲回（账面回到建单前）。

新开户那家公司接了 Express：这条只看采购本身，跑的时候关掉「接 Express」（修改页提交不走写账预检），`cleanup` 还原。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, codes, company, js_value, stock_of, warehouses  # noqa: E402

TITLE = "ERP 采购单页头动作：新建、删草稿、付款、补开收据、预扣税凭证、修改、作废"
USER = "purchase@erp.regression"

# 打开一张单、等页头标题是它的系统号（页面画完）
OPEN = ("const open=async(name,no)=>{frappe.set_route('pearnly-purchase', name);"
        "const t=await until(()=>(frappe.pages['pearnly-purchase']?.page?.title||'').trim()===no&&$.active===0, 25000); await idle(); await sleep(500);"
        "window.__opened=t?'':JSON.stringify({route: frappe.get_route(), title: frappe.pages['pearnly-purchase']?.page?.title}); return !!t;};")
# 原生弹窗：等 cur_dialog 出来，填值，点主按钮，等它关上
DIALOG = ("const dialog=async(values)=>{const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000);"
          "if(!d) return null; await d.set_values(values); await sleep(300); const title=(d.title||'').trim(); d.get_primary_btn().click();"
          "await until(()=>!d.$wrapper.is(':visible'), 20000); await idle(); return {title, open: d.$wrapper.is(':visible'), msg: msg()};};")


def prepare(lab, manifest):
    co = company(manifest)
    wh = warehouses(manifest)[0]["name"]
    product = codes(manifest)["by_code"]["product"]
    tag = uuid.uuid4().hex[:6]
    before = stock_of(lab, manifest["site"], product, wh)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.purchasing import entry, service, suppliers
frappe.set_user({USER!r})
companies.set_active_company({co!r})
express = frappe.db.get_value("ERP Company", {co!r}, "express_enabled")
frappe.db.set_value("ERP Company", {co!r}, "express_enabled", 0, update_modified=False)
frappe.db.commit()
supplier = frappe.db.get_value("ERP Supplier", {{"supplier_name": "ผู้ขายโฟลว์ปุ่ม"}}) or \\
    suppliers.create_supplier(supplier_name="ผู้ขายโฟลว์ปุ่ม")["name"]
frappe.db.commit()
head = {{"company": {co!r}, "supplier": supplier, "doc_date": frappe.utils.nowdate(), "currency": "THB", "fx_rate": 1,
         "has_vat": 1, "price_mode": "exclusive", "doc_kind": "purchase_invoice", "warehouse": {wh!r}}}
# 未付、一行服务带预扣税 3%：付款、两份凭证、修改都在它上面
paying = entry.post(dict(head, doc_no="FLOW-PAY-{tag}", is_paid=0, update_stock=0,
                         lines=[{{"item_type": "service", "description": "flow service {tag}", "qty": 1, "unit": "งาน",
                                 "unit_price": 1000, "vat_rate": 7, "wht_rate": 3}}]), request_id="flow-pay-{tag}")
# 入了库的一张：作废要把它入的那 1 件冲回
voiding = entry.post(dict(head, doc_no="FLOW-VOID-{tag}", is_paid=1, payment_method="cash", update_stock=1,
                          lines=[{{"product": {product!r}, "item_type": "goods", "description": "flow goods {tag}", "qty": 1, "unit": "ชิ้น",
                                  "unit_price": 100, "vat_rate": 7, "warehouse": {wh!r}, "location": ""}}]), request_id="flow-void-{tag}")
draft = service.create_purchase(supplier_name="flow draft {tag}", doc_date=frappe.utils.nowdate(), company={co!r},
                                lines=[{{"item_type": "service", "description": "flow draft {tag}", "qty": 1, "unit_price": 10}}])
frappe.db.commit()
rows = {{key: frappe.db.get_value("Purchase Document", name, ["name", "system_no", "wht_amount", "net_payable", "status"], as_dict=True)
        for key, name in (("paying", paying["name"]), ("voiding", voiding["name"]), ("draft", draft["name"]))}}
print(json.dumps({{"express": int(express or 0), "rows": rows}}, default=str))
""")
    rows = out["rows"]
    return {"company": co, "warehouse": wh, "product": product, "before": before, "express": out["express"], "tag": tag,
            "paying": rows["paying"]["name"], "paying_no": rows["paying"]["system_no"], "wht": float(rows["paying"]["wht_amount"] or 0),
            "voiding": rows["voiding"]["name"], "voiding_no": rows["voiding"]["system_no"],
            "draft": rows["draft"]["name"], "draft_supplier": f"flow draft {tag}",
            "reason": f"flow void {tag}", "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.db.set_value("ERP Company", {params['company']!r}, "express_enabled", {params['express']}, update_modified=False)
frappe.db.commit()
print(json.dumps({{}}))
""")


def steps(params, camera_code=None):
    v = {k: js_value(params[k]) for k in ("paying", "paying_no", "voiding", "voiding_no", "draft", "draft_supplier", "reason")}
    return [
        {"name": "列表新建", "js": JS + ACTION + """
await until(()=>pageOf('pearnly-purchase')&&document.querySelector('.result'), 25000); await idle();
const via=await action(__('New')); if(!via) return {error:'no New button on the list'};
const moved=await until(()=>frappe.get_route()[1]==='new', 15000); await idle();
const route=frappe.get_route().join('/'); frappe.set_route('pearnly-purchase'); await sleep(800); await idle();
return {via, route, moved: !!moved};"""},
        {"name": "删除草稿", "js": JS + f"""
await until(()=>document.querySelector('.result'), 25000); await idle();
setInput(document.querySelector('.page-form [data-fieldname="q"] input'), {v['draft_supplier']}); await sleep(600); await idle();
const sel='.list-row-checkbox[data-name='+JSON.stringify({v['draft']})+']';
const box=await until(()=>document.querySelector(sel), 20000); if(!box) return {{error:'draft not in the list'}};
box.click(); await sleep(400);
const btn=await until(()=>all('.page-head .primary-action, .page-head .btn-primary').find(b=>b.innerText.trim().startsWith(__('Delete'))), 8000);
if(!btn) return {{error:'no Delete button after selecting the draft'}}; const label=btn.innerText.trim(); btn.click();
const yes=await until(()=>all('.modal.show .btn-primary')[0], 8000); if(!yes) return {{error:'no confirm dialog'}}; yes.click();
const gone=await until(()=>!document.querySelector(sel), 20000); await idle();
return {{label, gone: !!gone}};"""},
        {"name": "付款", "js": JS + ACTION + OPEN + DIALOG + f"""
if(!await open({v['paying']}, {v['paying_no']})) return {{error:'unpaid purchase did not open '+window.__opened}};
const via=await action(__('Payment')); if(!via) return {{error:'no Payment button'}};
const done=await dialog({{amount: 50, method: 'transfer'}}); if(!done) return {{error:'no payment dialog'}};
return {{via, ...done}};"""},
        {"name": "补开收据", "js": JS + ACTION + OPEN + f"""
if(!await open({v['paying']}, {v['paying_no']})) return {{error:'purchase did not open '+window.__opened}};
const via=await action(frappe.pearnly.purchase_ui.label('substitute_receipt')); if(!via) return {{error:'no Substitute Receipt item'}};
await sleep(800); await idle(); return {{via, msg: msg()}};"""},
        {"name": "预扣税凭证", "js": JS + ACTION + OPEN + f"""
if(!await open({v['paying']}, {v['paying_no']})) return {{error:'purchase did not open '+window.__opened}};
const via=await action(frappe.pearnly.purchase_ui.label('wht_cert')); if(!via) return {{error:'no Withholding Tax Certificate item'}};
await sleep(800); await idle(); return {{via, msg: msg()}};"""},
        {"name": "修改", "js": JS + ACTION + OPEN + f"""
if(!await open({v['paying']}, {v['paying_no']})) return {{error:'purchase did not open '+window.__opened}};
const via=await action(__('Amend')); if(!via) return {{error:'no Amend item'}};
const opened=await until(()=>frappe.get_route()[1]==='amend', 15000); if(!opened) return {{error:'amend page did not open '+window.__opened}};
const btn=await until(()=>all('.page-head .primary-action, .page-head .btn-primary')[0], 20000); await idle(); await sleep(800);
if(!btn) return {{error:'no submit button on the amend page'}}; btn.click(); await sleep(1200);
const yes=all('.modal.show .btn-primary')[0]; if(yes) yes.click();
const moved=await until(()=>frappe.get_route()[1]&&frappe.get_route()[1]!=='amend', 30000); await idle();
return {{via, route: frappe.get_route().join('/'), moved: !!moved, msg: msg()}};"""},
        {"name": "作废", "js": JS + ACTION + OPEN + DIALOG + f"""
if(!await open({v['voiding']}, {v['voiding_no']})) return {{error:'stocked purchase did not open '+window.__opened}};
const via=await action(__('Cancel')); if(!via) return {{error:'no Cancel item'}};
const done=await dialog({{reason: {v['reason']}}}); if(!done) return {{error:'no cancel dialog'}};
return {{via, ...done}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, drop, amend = by.get("列表新建", {}), by.get("删除草稿", {}), by.get("修改", {})
    return [
        {"case": "the list New button opens the new page", "seen": new, "ok": bool(new.get("via")) and new.get("route") == "pearnly-purchase/new"},
        {"case": "selecting the draft turns the head button into Delete and the row goes away", "seen": drop,
         "ok": (drop.get("label") or "").endswith("(1)") and drop.get("gone") is True},
        {"case": "payment, voucher and cancel dialogs closed without a message", "seen": {k: by.get(k) for k in ("付款", "作废")},
         "ok": all((by.get(k) or {}).get("open") is False and not (by.get(k) or {}).get("msg") for k in ("付款", "作废"))},
        {"case": "amend submits and leaves the amend page", "seen": amend, "ok": amend.get("moved") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
paying = frappe.get_doc("Purchase Document", {params['paying']!r})
voiding = frappe.db.get_value("Purchase Document", {params['voiding']!r}, ["status", "void_reason"], as_dict=True)
amended = frappe.get_all("Purchase Document", filters={{"corrected_from": paying.name}}, fields=["name", "status", "wht_amount"])
files = {{row.kind: bool(frappe.db.exists("File", row.file)) for row in paying.attachments if row.generated}}
pays = [{{"amount": float(row.amount), "method": row.method, "reversal": int(row.is_reversal or 0)}} for row in paying.payments]
print(json.dumps({{"draft": bool(frappe.db.exists("Purchase Document", {params['draft']!r})), "status": paying.status,
                  "pays": pays, "files": files, "amended": amended, "voiding": voiding}}, default=str))
""")
    after = stock_of(lab, manifest["site"], params["product"], params["warehouse"])
    pays = got["pays"]
    amended = got["amended"]
    return [
        {"case": "the deleted draft is gone", "saved": got["draft"], "ok": got["draft"] is False},
        {"case": "the payment dialog recorded 50 by transfer", "saved": pays,
         "ok": any(p["amount"] == 50 and p["method"] == "transfer" and not p["reversal"] for p in pays)},
        {"case": "substitute receipt and withholding certificate files were generated", "saved": got["files"],
         "ok": params["wht"] > 0 and got["files"].get("substitute_receipt") is True and got["files"].get("wht_cert") is True},
        {"case": "amend voided the original, reversed its payment and submitted the new one", "saved": {"status": got["status"], "amended": amended, "pays": pays},
         "ok": got["status"] == "void" and len(amended) == 1 and amended[0]["status"] == "submitted"
         and any(p["amount"] == -50 and p["reversal"] for p in pays)},
        {"case": "cancel voided the stocked purchase with the typed reason", "saved": got["voiding"],
         "ok": got["voiding"].get("status") == "void" and got["voiding"].get("void_reason") == params["reason"]},
        {"case": "cancel took back the received unit", "saved": {"before": params["before"], "after": after}, "ok": after == params["before"]},
    ]
