"""流程用例：首页「订阅与充值」与明细删除逐个实点（ERP、Cowork 同一份页面 `pearnly_core/public/desk/home.js`；清存量基线 11-08）。

租户管理员打开首页（记录明细）：
- 页头「订阅与充值」→ 二级页：显示的余额就是计费服务里的余额；
- 二级页「选择套餐」→ 原生弹窗选一个和当前不同的套餐 →「订阅」→ 计费服务里当前套餐是它、余额少了它的月费；
- 二级页「充值」→ 弹窗填金额、付款人、经原生上传框交一张转账凭证 →「提交」→ 充值记录里多一条待审核、金额是填的（余额不变：要等平台审核）；
- 回明细，类型切到「充值」，勾上刚提交的那条 → 页头变「删除」→ 确认 → 充值记录里没有它了。
实验室计费是本机控制面（`scripts/regression/lab_billing.py`）；cleanup 在控制面按正式审核函数补回扣掉的月费，余额回到跑之前。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "首页订阅与充值：余额、选择套餐、充值申请、明细删除"
CONTROL = "control.regression.localhost"
NOTE = "Regression flow refund of the subscription fee; no real transfer"


def _admin(manifest):
    return "admin@cowork.regression" if manifest["site"].startswith("cowork") else "admin@erp.regression"


def _home(lab, manifest):
    return lab.python(manifest["site"], f"""
frappe.set_user({_admin(manifest)!r})
from pearnly_core import billing_www
home = billing_www.home()
topups = billing_www.records("topup", limit=200)["rows"]
print(json.dumps({{"balance": home["balance_thb"], "plan": (home.get("subscription") or {{}}).get("plan_code") or "",
                  "plans": home["plans"], "topups": [[row["name"], row["status"], row["requested_thb"]] for row in topups],
                  "tenant": frappe.conf.get("pearnly_tenant") or ""}}, default=str))
""")


def prepare(lab, manifest):
    now = _home(lab, manifest)
    plan = next(item for item in now["plans"] if item["code"] != now["plan"])
    tag = uuid.uuid4().hex[:6]
    return {"balance": now["balance"], "plan": plan["code"], "fee": plan["fee"], "tenant": now["tenant"],
            "amount": 100 + int(tag[:2], 16) / 100, "payer": f"flow payer {tag}", "cases": []}


def cleanup(lab, manifest, params):
    # 把这次订阅扣掉的月费补回去（控制面正式审核函数，同 lab_billing.py 给测试余额）
    if not params.get("tenant"):
        return
    lab.python(CONTROL, f"""
frappe.set_user("Administrator")
from pearnly_control import billing, billing_api
gap = round({params['balance']} - float(billing.balance_of({params['tenant']!r})), 2)
if gap > 0:
    request = frappe.get_doc({{"doctype": billing.TOPUP, "tenant": {params['tenant']!r}, "status": "pending", "requested_thb": gap,
                              "note": {NOTE!r}, "payer_name": "Regression laboratory"}}).insert()
    billing_api.approve_local(request.name, "Administrator", actual_amount_thb=gap, note={NOTE!r})
frappe.db.commit()
print(json.dumps({{"gap": gap}}))
""")


SETUP = JS + ACTION + """
const t=await frappe.pearnly.texts('home.');
const dlg=async()=>await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000);
const billing=async()=>{if(frappe.get_route()[1]!=='billing'){frappe.set_route('pearnly-home','billing');}
  await until(()=>frappe.get_route()[1]==='billing'&&pageOf('pearnly-home')?.querySelector('[data-fieldname="balance"]'), 25000); await idle();};
"""


def steps(params, camera_code=None):
    return [
        {"name": "订阅与充值", "js": SETUP + """
await until(()=>pageOf('pearnly-home')&&document.querySelector('.result, .no-result'), 25000); await idle();
const via=await action(t('home.billing')); if(!via) return {error:'no billing button'};
const opened=await until(()=>frappe.get_route()[1]==='billing'&&pageOf('pearnly-home')?.querySelector('[data-fieldname="balance"]'), 25000); await idle();
if(!opened) return {error:'billing page did not open'};
const el=pageOf('pearnly-home').querySelector('[data-fieldname="balance"]');
return {via, balance: (el.querySelector('.control-value, input, textarea')?.value||el.querySelector('.control-value')?.innerText||el.innerText||'').trim()};"""},
        {"name": "选择套餐", "js": SETUP + f"""
await billing();
const via=await action(t('home.plans.title')); if(!via) return {{error:'no plans button'}};
const d=await dlg(); if(!d) return {{error:'no plans dialog'}};
await d.set_value('plan_code', {js_value(params['plan'])}); await sleep(300);
d.get_primary_btn().click(); const closed=await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();
return {{via, closed: !!closed, msg: msg()}};"""},
        {"name": "充值", "js": SETUP + f"""
await billing();
const via=await action(t('home.topup')); if(!via) return {{error:'no topup button'}};
const d=await dlg(); if(!d) return {{error:'no topup dialog'}};
await d.set_values({{amount_thb: {params['amount']}, payer_name: {js_value(params['payer'])}}}); await sleep(300);
// 转账凭证（服务端必收）：点「上传转账凭证」，在框架原生上传框里放一张 1×1 的 PNG、点上传
d.fields_dict.upload.$input.click();
const up=await until(()=>[...document.querySelectorAll('.modal.show')].find(m=>m.querySelector('input[type=file]')), 15000);
if(!up) return {{error:'no slip uploader'}};
const raw=atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==');
const bytes=new Uint8Array(raw.length); for(let i=0;i<raw.length;i++) bytes[i]=raw.charCodeAt(i);
const box=up.querySelector('input[type=file]'); const dt=new DataTransfer(); dt.items.add(new File([bytes],'slip.png',{{type:'image/png'}}));
box.files=dt.files; box.dispatchEvent(new Event('change',{{bubbles:true}})); await sleep(600);
const send=await until(()=>[...up.querySelectorAll('.modal-footer .btn-primary')].find(b=>b.offsetParent&&!b.disabled), 10000);
if(!send) return {{error:'no upload button'}}; send.click();
const attached=await until(()=>d.get_value('receipt'), 20000); await idle(); await sleep(400);
if(!attached) return {{error:'slip not attached'}};
d.get_primary_btn().click(); const closed=await until(()=>!d.$wrapper.is(':visible'), 20000); await idle();
return {{via, closed: !!closed, msg: msg()}};"""},
        {"name": "删除", "js": SETUP + f"""
frappe.set_route('pearnly-home'); await until(()=>!frappe.get_route()[1]&&pageOf('pearnly-home')?.querySelector('.result, .no-result')&&cur_page?.page?.page?.fields_dict?.tab, 25000); await idle();
const page=frappe.pages['pearnly-home'].page; await page.fields_dict.tab.set_value('topup'); await sleep(800); await idle();
// 金额列照页面同一个写法（`p.money`）
const want=frappe.pearnly.money({params['amount']});
const row=await until(()=>[...document.querySelectorAll('.result .list-row-container')].find(r=>!r.querySelector('.list-row-head')&&r.innerText.includes(want)&&r.querySelector('.list-row-checkbox')), 20000);
if(!row) return {{error:'submitted top-up not listed'}};
const name=row.querySelector('.list-row-checkbox').dataset.name||''; row.querySelector('.list-row-checkbox').click(); await sleep(400);
const via=await action(__('Delete')); if(!via) return {{error:'no Delete button after selecting'}};
const yes=await until(()=>all('.modal.show .btn-primary')[0], 8000); if(!yes) return {{error:'no confirm dialog'}}; yes.click();
await until(()=>!document.querySelector('.list-row-checkbox[data-name="'+name+'"]'), 20000); await idle();
return {{via, name}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    page, plans, topup, drop = (by.get(k, {}) for k in ("订阅与充值", "选择套餐", "充值", "删除"))
    digits = "".join(ch for ch in (page.get("balance") or "") if ch.isdigit() or ch == ".")
    shown = float(digits) if digits.replace(".", "", 1).isdigit() else None
    return [
        {"case": "the billing page shows the balance the billing service holds", "seen": page,
         "ok": bool(page.get("via")) and shown is not None and abs(shown - float(params["balance"])) < 0.005},
        {"case": "the plans and top-up dialogs close without a message", "seen": {"plans": plans, "topup": topup},
         "ok": all(bool(x.get("via")) and x.get("closed") is True and not x.get("msg") for x in (plans, topup))},
        # 明细「充值」里找得到刚提交的那条（金额是填的）才勾得上：提交落了申请
        {"case": "the submitted top-up is listed and selecting it turns the head button into Delete", "seen": drop,
         "ok": bool(drop.get("via")) and bool(drop.get("name"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    now = _home(lab, manifest)
    mine = [row for row in now["topups"] if abs(float(row[2]) - params["amount"]) < 0.005]
    return [
        {"case": "the chosen plan is active and its fee came off the balance", "saved": {"plan": now["plan"], "balance": now["balance"]},
         "ok": now["plan"] == params["plan"] and abs(float(now["balance"]) - (params["balance"] - params["fee"])) < 0.005},
        {"case": "the deleted top-up request is gone from the records", "saved": mine, "ok": not mine},
    ]
