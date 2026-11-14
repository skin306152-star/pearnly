"""流程用例：ERP 网页「新建」收款（11-14 用户：网页端也可以新建收款，弹窗编辑相关字段，提交后就写进 Express）。

不出网、不写 Express：在新开户那套站点里用收款员账号（`collection_lib.seed`：本次专用客户与两张未收清发票、账套收款类型）。
页面（收款员；电脑与手机宽度各跑一遍）：「销售 → 收款」页头【新建】→ 原生弹窗：只有一家公司，「公司」不显示、已取好 → 客户框打客户码，从服务端搜索的
下拉里点中 → 表格列出这位客户能收的两张发票 → 勾第一张（带出未收余额 1000）、现金 1000 → 提交。再开一次弹窗、超收（第一张填 1200）提交：
服务端照码拒、弹窗不关、什么都不留。库里：一笔来源「网页」、待写入（已交写账队列，同意记成 ``web:<弹窗号>``）、金额对；
「收款」页点开这一笔可【撤回】（这里按同一个接口撤），撤回后写账作业撤掉。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collection_lib import restore, seed  # noqa: E402
from erp_lib import JS  # noqa: E402

TITLE = "网页新建收款 → 提交即放行 → 超收被拒 → 收款页撤回"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    return {**seed(lab, manifest, tag), "tag": tag}


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)


def _fill(params, amount):
    """``amount`` 为 None：只勾第一张发票（真机 11-14：勾上应带出未收余额 1000）；给了数就直接填那个数。"""
    first = params["invoices"][0]
    # 同人的操作：勾第一张（带出未收余额），要别的数再改这一行的「本次收」
    pick_row = ("const grid=dialog.fields_dict.invoices.grid; const check=await until(()=>grid.wrapper.find('.grid-body .grid-row-check:visible')[0], 10000);"
                "if(check) check.click(); await sleep(600);"
                if amount is None else
                # 超收这一步要测的是服务端照码拒：直接改第一行的「本次收」（勾选带出余额由提交那一步验）
                f"const grid=dialog.fields_dict.invoices.grid; await until(()=>grid.grid_rows&&grid.grid_rows[0], 10000);"
                f"grid.grid_rows[0].doc.amount={amount}; grid.refresh(); await sleep(300);")
    cash = 1000 if amount is None else amount
    return f"""
frappe.set_route('pearnly-collections');
await until(()=>pageOf('pearnly-collections')&&all('.page-actions .primary-action, .page-actions .btn-primary').length, 25000);
await idle();
all('.page-actions .primary-action, .page-actions .btn-primary')[0].click();
const dialog=await until(()=>window.cur_dialog&&cur_dialog.display&&cur_dialog.fields_dict.customer&&cur_dialog, 20000);
if(!dialog) return {{error:'no dialog', msg: msg()}};
await until(()=>dialog.get_value('company'), 15000); await idle();
const company=dialog.get_value('company');
const company_shown=!dialog.fields_dict.company.df.hidden;
const picked=await pick(dialog.fields_dict.customer.$input[0], {json.dumps(params["customer"])});
await idle();
const rows=await until(()=>(dialog.fields_dict.invoices.df.data||[]).length===2&&dialog.fields_dict.invoices.df.data, 20000);
const listed=(rows||[]).map(r=>[r.docnum, r.available]);
const row=(rows||[]).find(r=>r.docnum==={json.dumps(first)});
{pick_row}
const filled=row?row.amount:null;
const cash=dialog.fields_dict.methods_area.$wrapper.find('input[data-fieldname=cash]')[0];
setInput(cash, '{cash}'); await sleep(300);
const wide=document.documentElement.scrollWidth<=window.innerWidth+1;
dialog.get_primary_btn().click();
const closed=await until(()=>!dialog.display, 15000); await idle();
const said=all('.msgprint').map(e=>e.textContent.trim()).join(' | ').slice(0,200);
return {{company, company_shown, picked, listed, filled, wide, closed: !!closed, said, msg: msg(), alerts: alerts()}};"""


def steps(params, camera_code=None):
    # shot：设了 PEARNLY_FLOWS_SHOTS 才截（人工看手机宽度下弹窗的发票表与列表的列）
    return [{"name": "超收被拒", "js": JS + _fill(params, 1200), "shot": True},
            {"name": "关掉被拒的弹窗", "js": JS + """
if(window.cur_dialog&&cur_dialog.display) cur_dialog.hide();
await sleep(500); return {closed: !(window.cur_dialog&&cur_dialog.display)};"""},
            {"name": "新建并提交", "js": JS + _fill(params, None)},
            {"name": "列表", "js": JS + """
await until(()=>!(window.cur_dialog&&cur_dialog.display), 10000); await sleep(800); await idle();
const rows=all('.list-row-container').length;
const cut=all('.list-row-container .list-row-col').filter(el=>el.scrollWidth>el.clientWidth+1).map(el=>el.textContent.trim()).slice(0,8);
return {rows, cut};""", "shot": True}]


def judge(params, results, camera_code=None):
    seen = {r.get("step"): r.get("value") or {} for r in results}
    refused, made = seen.get("超收被拒", {}), seen.get("新建并提交", {})
    first, second = params["invoices"]
    return [
        {"case": "the only company is taken without showing the field, and the customer is found on the server",
         "seen": {k: made.get(k) for k in ("company", "company_shown", "picked", "error")},
         "ok": made.get("company") == params["company"] and made.get("company_shown") is False and bool(made.get("picked"))},
        {"case": "the customer's two open invoices are listed with what can be collected",
         "seen": made.get("listed"), "ok": made.get("listed") == [[first, 1000], [second, 500]]},
        {"case": "over-collecting is refused by the server with that reason and the dialog stays open",
         "seen": {k: refused.get(k) for k in ("closed", "said")},
         "ok": refused.get("closed") is False and "超过未收金额" in str(refused.get("said") or "")},
        {"case": "ticking an invoice fills what is still due", "seen": made.get("filled"), "ok": made.get("filled") == 1000},
        {"case": "a valid submit closes the dialog", "seen": {k: made.get(k) for k in ("closed", "msg", "alerts")},
         "ok": bool(made.get("closed"))},
        {"case": "the dialog fits the screen width", "seen": made.get("wide"), "ok": made.get("wide") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    site = manifest["site"]
    rows = lab.python(site, f"""
rows = frappe.get_all("ERP Collection", filters={{"customer": {params["customer"]!r}}},
                      fields=["name", "status", "source", "consent_ref", "cash", "total", "job"], limit_page_length=10)
print(json.dumps([{{**row, "cash": "%.2f" % (row.cash or 0), "total": "%.2f" % (row.total or 0)}} for row in rows], default=str))
""")
    made = rows[0] if len(rows) == 1 else {}
    withdrawn = lab.python(site, f"""
from pearnly_erp_portal.collection import api
frappe.set_user("collector@erp.regression")
name = {made.get("name", "")!r}
out = api.api_collection_withdraw(name) if name else {{}}
job = frappe.db.get_value("Pearnly CC Job", {made.get("job", "")!r}, "status") if name else ""
frappe.db.commit()
print(json.dumps({{"status": out.get("status"), "job": job}}))
""")
    return [
        {"case": "exactly one collection was recorded (the refused one left nothing)", "seen": rows, "ok": len(rows) == 1},
        {"case": "it came from the web and was released at once with the dialog as consent", "seen": made,
         "ok": made.get("source") == "web" and made.get("status") == "queued" and str(made.get("consent_ref", "")).startswith("web:")
         and bool(made.get("job")) and made.get("cash") == "1000.00" and made.get("total") == "1000.00"},
        {"case": "the collections page withdraws it like LINE does", "seen": withdrawn,
         "ok": withdrawn.get("status") == "withdrawn" and withdrawn.get("job") == "cancelled"},
    ]
