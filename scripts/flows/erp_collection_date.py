"""流程用例：今天不在这家任何一本账套的期间里时收款问收款日期（11-15 用户：「这个时间是人工可以改的，我只要改到账本期间，就可以
进行写帐，为何我们不可以？」——同 Express 里把工作日期改进期间再录）。

不出网、不写 Express：新开户那套站点（`collection_lib.seed`：本次专用客户与两张未收清发票、账套收款类型），把这本账套在电脑登记里的
期间临时改成 2024 年度（本年 2024-01-01～2024-12-31、次年止 2025-12-31，cleanup 还原），今天就不在任何一本的期间里。
- LINE（收款员）：收款 → 问收款日期（佛历期间、快捷回复「31/12/2567 …」）→ 点它 → 打客户码 → 勾第一张 → 现金 1000 → 确认卡
  写出收款日期；库里草稿日期 2024-12-31、记为人选的；打 ok 放行，写账载荷的日期还是 2024-12-31（不改回放行当天），再撤回。
- 网页（收款员）：「收款」页【新建】→ 收款日期被清空、原生日期框限定到 2024-01-01～2025-12-31、下面写出会计期间 → 填 2024-12-31 →
  选客户 → 列出两张发票。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collection_lib import line_events, postbacks, restore, seed  # noqa: E402
from erp_lib import JS  # noqa: E402

TITLE = "收款日期：今天不在账套期间里 → LINE 问日期写到载荷 / 网页日期框限定期间"
OLD = {"period_start": "2024-01-01", "period_end": "2024-12-31", "next_end": "2025-12-31"}


def _periods(lab, site, key, values=None):
    """这本账套在电脑登记里的期间：给了 ``values`` 就改成它（``{行名: 期间}`` 按行还原，否则各台电脑报的那几行一起改），回改之前的。"""
    return lab.python(site, f"""
rows = frappe.get_all("Pearnly CC Book", filters={{"book_key": {key!r}}}, fields=["name", "period_start", "period_end", "next_end"])
before = {{row.name: {{k: str(row[k]) if row[k] else None for k in ("period_start", "period_end", "next_end")}} for row in rows}}
values = json.loads({json.dumps(values)!r})
if values is not None:
    for name in before:
        frappe.db.set_value("Pearnly CC Book", name, values.get(name, values), update_modified=False)
frappe.db.commit()
print(json.dumps(before))
""")


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    seeded = seed(lab, manifest, tag)
    site = manifest["site"]
    periods = _periods(lab, site, seeded["book"], OLD)
    first = seeded["invoices"][0]
    asked = line_events(lab, site, [["postback", "a=col&s=start"]])["events"]
    buttons = [item["action"].get("data") or "" for message in asked[-1]["messages"]
               for item in (message.get("quickReply") or {}).get("items") or ()]
    day = next((data for data in buttons if "s=day" in data), "")
    ran = line_events(lab, site, [["postback", day or "a=col&s=day&v=2024-12-31"], ["text", seeded["customer"]],
                                  ["postback", f"a=col&s=inv&v={first}"], ["postback", "a=col&s=invoices&v=done"],
                                  ["postback", "a=col&s=method&v=%40cash"], ["text", "1000"]])["events"]
    text = "\n".join(message.get("text", "") for message in asked[-1]["messages"] if message.get("type") == "text")
    return {**seeded, "tag": tag, "periods": periods, "asked_step": asked[-1]["step"], "asked_text": text, "day_button": day,
            "steps": [row["step"] for row in ran], "errors": [row["error"] for row in ran if row["error"] or not row["handled"]],
            "name": ran[-1]["session"].get("name") or "", "card": json.dumps(ran[-1]["messages"], ensure_ascii=False)}


def cleanup(lab, manifest, params):
    if params.get("periods"):
        _periods(lab, manifest["site"], params["book"], params["periods"])
    restore(lab, manifest, params)


def steps(params, camera_code=None):
    return [{"name": "网页新建问日期", "js": JS + f"""
frappe.set_route('pearnly-collections');
await until(()=>pageOf('pearnly-collections')&&all('.page-actions .primary-action, .page-actions .btn-primary').length, 25000);
await idle();
all('.page-actions .primary-action, .page-actions .btn-primary')[0].click();
const dialog=await until(()=>window.cur_dialog&&cur_dialog.display&&cur_dialog.fields_dict.doc_date&&cur_dialog, 20000);
if(!dialog) return {{error:'no dialog', msg: msg()}};
await until(()=>dialog.get_value('company'), 15000);
await until(()=>dialog.fields_dict.doc_date.df.min_date, 15000); await sleep(600); await idle();
const control=dialog.fields_dict.doc_date;
const cleared=dialog.get_value('doc_date')||'';
const range=[control.df.min_date&&frappe.datetime.obj_to_str(control.df.min_date), control.df.max_date&&frappe.datetime.obj_to_str(control.df.max_date)];
const said=control.df.description||'';
await dialog.set_value('doc_date', '2024-12-31'); await sleep(800); await idle();
const input=dialog.fields_dict.customer.$input[0];
input.focus(); input.value={json.dumps(params["customer"])}; input.dispatchEvent(new Event('input',{{bubbles:true}}));
input.dispatchEvent(new KeyboardEvent('keyup',{{bubbles:true,key:'a'}}));
const li=await until(()=>{{const ul=document.getElementById(input.getAttribute('aria-owns')||'');
  return ul&&[...ul.querySelectorAll('[role=option], li')].find(l=>l.textContent.includes({json.dumps(params["customer"])}));}}, 10000);
if(li){{ li.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true}})); li.click(); }}
const rows=await until(()=>(dialog.fields_dict.invoices.df.data||[]).length===2&&dialog.fields_dict.invoices.df.data, 20000);
const listed=(rows||[]).map(r=>r.docnum);
dialog.hide();
return {{cleared, range, said, listed, msg: msg()}};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "网页新建问日期"), {})
    return [
        {"case": "LINE asks for the collection date with the Buddhist-era period and a last-day button",
         "seen": {k: params.get(k) for k in ("asked_step", "asked_text", "day_button")},
         "ok": params["asked_step"] == "day" and "收款日期" in params["asked_text"] and "01/01/2567" in params["asked_text"]
         and "31/12/2568" in params["asked_text"] and "v=2024-12-31" in params["day_button"]},
        {"case": "after the date every LINE step goes on to the card, which shows the date",
         "seen": {"steps": params["steps"], "errors": params["errors"]},
         "ok": params["steps"] == ["customer", "invoices", "invoices", "method", "amount", "card"] and not params["errors"]
         and "31/12/2567" in params["card"]},
        {"case": "the web dialog clears the date, limits it to the periods and names them",
         "seen": {k: seen.get(k) for k in ("cleared", "range", "said", "error")},
         "ok": seen.get("cleared") == "" and seen.get("range") == ["2024-01-01", "2025-12-31"] and "2024-01-01" in str(seen.get("said"))},
        {"case": "with a date inside the period the web dialog lists the customer's invoices",
         "seen": seen.get("listed"), "ok": seen.get("listed") == params["invoices"]},
    ]


def verify(lab, manifest, params, camera_code=None):
    site, name = manifest["site"], params["name"]
    drafted = _record(lab, site, name)
    ran = line_events(lab, site, [["text", "ok"]])["events"]
    released = _record(lab, site, name)
    withdraw = [data for data in postbacks(ran[-1]["messages"]) if "s=wd" in data]
    if withdraw:
        line_events(lab, site, [["postback", withdraw[0]]])
    return [
        {"case": "the draft keeps the chosen date and the old book", "seen": drafted,
         "ok": drafted["doc_date"] == "2024-12-31" and drafted["date_chosen"] == 1 and drafted["book_key"] == params["book"]},
        {"case": "releasing keeps the chosen date in the write payload (not the release day)", "seen": released,
         "ok": released["status"] == "queued" and released["doc_date"] == "2024-12-31"
         and released["payload_date"] == "2024-12-31"},
    ]


def _record(lab, site, name):
    return lab.python(site, f"""
doc = frappe.get_doc("ERP Collection", {name!r})
job = frappe.db.get_value("Pearnly CC Job", doc.job, ["payload"], as_dict=True) if doc.job else None
payload = json.loads(job.payload or "{{}}") if job else {{}}
print(json.dumps({{"status": doc.status, "doc_date": str(doc.doc_date or ""), "date_chosen": int(doc.date_chosen or 0),
                  "book_key": doc.book_key, "payload_date": str(payload.get("doc_date") or "")}}))
""")
