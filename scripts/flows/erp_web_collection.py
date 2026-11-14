"""流程用例：ERP 网页「新建」收款（11-14 用户：网页端也可以新建收款，弹窗编辑相关字段，提交后就写进 Express）。

不出网、不写 Express：在新开户那套站点里用收款员账号（`collection_lib.seed`：本次专用客户与两张未收清发票、账套收款类型）。
页面（收款员；电脑与手机宽度各跑一遍）：「销售 → 收款」页头【新建】→ 原生弹窗：只有一家公司，「公司」不显示、已取好 → 客户框打客户码，从服务端搜索的
下拉里点中 → 表格列出这位客户能收的两张发票 → 两张都勾（带出未收余额 1000 / 500）、在格子里把第二张改成 300、现金 1300 → 提交。
先开一次弹窗、两张都勾、在格子里把第一张改成 1200（超收）提交：服务端说超收（不是「请至少选择一张发票」）、
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


def _fill(params, typed, cash):
    """同人的操作：选好客户后两张发票都勾上（勾上带出未收余额），再点格子改 ``typed``（行号 → 金额）那几行的「本次收」，
    现金填 ``cash``，提交。点勾选框、点格子都会让客户框失焦（11-14 真机：失焦又报一次 change、重拉发票，把勾选与填的金额冲成 0，
    提交报「请至少选择一张发票」），所以这里一律真点，不改行对象。"""
    edits = "".join(f"await editCell({index}, '{value}');" for index, value in sorted(typed.items()))
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
// 只认本输入框自己的下拉（aria-owns）：上一次被拒的弹窗关了还留在页面里，它的下拉里也有这位客户，`pick` 会先点中那一个
const pickOwn=async(input,text)=>{{input.focus();input.value=text;input.dispatchEvent(new Event('input',{{bubbles:true}}));
  input.dispatchEvent(new KeyboardEvent('keyup',{{bubbles:true,key:'a'}}));
  const li=await until(()=>{{const ul=document.getElementById(input.getAttribute('aria-owns')||'');
    return ul&&[...ul.querySelectorAll('[role=option], li')].find(l=>l.textContent.includes(text));}}, 10000);
  if(!li) return false; li.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true}})); li.click(); await sleep(400); return true;}};
const picked=await pickOwn(dialog.fields_dict.customer.$input[0], {json.dumps(params["customer"])});
await idle();
const rows=await until(()=>(dialog.fields_dict.invoices.df.data||[]).length===2&&dialog.fields_dict.invoices.df.data, 20000);
const listed=(rows||[]).map(r=>[r.docnum, r.available]);
const grid=dialog.fields_dict.invoices.grid;
const rowAt=i=>grid.grid_rows[i];
const amounts=()=>(grid.get_data()||[]).map(r=>[r.docnum, Number(r.amount||0), !!r.__checked]);
for (const i of [0, 1]) {{
  const check=await until(()=>rowAt(i)&&rowAt(i).row.find('.grid-row-check:visible')[0], 10000);
  if(check) check.click();
  await sleep(500); await idle();
}}
const filled=amounts();
const editCell=async(i, value)=>{{
  const cell=rowAt(i).row.find('.grid-static-col[data-fieldname=amount]')[0];
  if(cell) cell.click();
  const input=await until(()=>rowAt(i).row.find('input[data-fieldname=amount]:visible')[0], 5000);
  setInput(input, value);
  await sleep(400); await idle();
}};
{edits}
const edited=amounts();
const cash=dialog.fields_dict.methods_area.$wrapper.find('input[data-fieldname=cash]')[0];
setInput(cash, '{cash}'); await sleep(300);
const wide=document.documentElement.scrollWidth<=window.innerWidth+1;
dialog.get_primary_btn().click();
const closed=await until(()=>!dialog.display, 15000); await idle();
const said=all('.msgprint').map(e=>e.textContent.trim()).join(' | ').slice(0,200);
return {{company, company_shown, picked, listed, filled, edited, wide, closed: !!closed, said, msg: msg(), alerts: alerts()}};"""


def steps(params, camera_code=None):
    # shot：设了 PEARNLY_FLOWS_SHOTS 才截（人工看手机宽度下弹窗的发票表与列表的列）
    return [{"name": "超收被拒", "js": JS + _fill(params, {0: 1200}, 1700), "shot": True},
            {"name": "关掉被拒的弹窗", "js": JS + """
// 被拒时上面压着一层提示框（也是 Dialog）：一层层关，收款弹窗也关掉，下一步才是新开的弹窗
for (let i=0; i<4 && window.cur_dialog && cur_dialog.display; i++) { cur_dialog.hide(); await sleep(400); }
return {closed: !(window.cur_dialog&&cur_dialog.display)};"""},
            {"name": "新建并提交", "js": JS + _fill(params, {1: 300}, 1300)},
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
         "seen": {k: refused.get(k) for k in ("edited", "closed", "said")},
         "ok": refused.get("edited") == [[first, 1200, True], [second, 500, True]] and refused.get("closed") is False
         and "超过未收金额" in str(refused.get("said") or "") and "请至少选择一张发票" not in str(refused.get("said") or "")},
        {"case": "ticking both invoices fills what is still due on each (clicking the grid does not reload the invoices)",
         "seen": made.get("filled"), "ok": made.get("filled") == [[first, 1000, True], [second, 500, True]]},
        {"case": "typing an amount into a cell keeps it and the other row", "seen": made.get("edited"),
         "ok": made.get("edited") == [[first, 1000, True], [second, 300, True]]},
        {"case": "a valid submit closes the dialog", "seen": {k: made.get(k) for k in ("closed", "msg", "alerts")},
         "ok": bool(made.get("closed"))},
        {"case": "the dialog fits the screen width", "seen": made.get("wide"), "ok": made.get("wide") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    site = manifest["site"]
    rows = lab.python(site, f"""
rows = frappe.get_all("ERP Collection", filters={{"customer": {params["customer"]!r}}},
                      fields=["name", "status", "source", "consent_ref", "cash", "total", "job"], limit_page_length=10)
for row in rows:
    row["lines"] = [[line.docnum, "%.2f" % (line.amount or 0)] for line in frappe.get_all("ERP Collection Invoice",
                    filters={{"parent": row.name, "parenttype": "ERP Collection"}}, fields=["docnum", "amount", "idx"], order_by="idx asc")]
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
         and bool(made.get("job")) and made.get("cash") == "1300.00" and made.get("total") == "1300.00"},
        {"case": "the server got each invoice at the amount shown in the dialog (1000 ticked, 300 typed)", "seen": made.get("lines"),
         "ok": made.get("lines") == [[params["invoices"][0], "1000.00"], [params["invoices"][1], "300.00"]]},
        {"case": "the collections page withdraws it like LINE does", "seen": withdrawn,
         "ok": withdrawn.get("status") == "withdrawn" and withdrawn.get("job") == "cancelled"},
    ]
