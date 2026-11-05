"""流程用例：Cowork 的写账前置判定（开始前确认卡片的取数与作答，W2）——一张进项税票这家供应商的费用科目记过、现 / 赊没选过，
以登录的会计员身份调共用接口 `pearnly_core.write_precheck.api.check`：卡片按业务分组，现 / 赊是预选、科目是已定；答错的值整批不记；
`answer` 改成现购后记进账套记忆（同复核页那条路），重判时现 / 赊变已定、这张单照写、不再出卡片。

执行到的服务端：`write_precheck.api`（check / answer）→ 产品钩子 `hooks.pearnly_write_precheck` → Cowork
`assistant.precheck_source`（check、document、remember）→ 推送服务的取数与判定（`push.application.service._choices`、`save_choices`）。
造数据见 `cowork_operator_lib.py`（产品自己的函数 + 合成账套目录，不连 Express）。页面还没有这张卡片（W4 接），这里在 CC 页里
以会计员的会话直接调接口（check / search / answer）；不推送、不写 Express。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP, SUBJECT

TITLE = "Cowork 写账前置判定：卡片按组列出预选与已定，答错整批不记，作答记进账套记忆后重判变已定（共用接口 check / answer）"
READY = "document.readyState === 'complete' && !!window.csrf_token"


def prepare(lab, manifest):
    site = manifest["site"]
    ref = "FLOW-PRE-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, CLEANUP + BASE + f"""
name = recognized({ref!r})
# 只留费用科目的记忆：现 / 赊这家没选过（卡片里要人点的那一项）
frappe.db.delete("Cowork Express Choice", {{"connection": conn["name"], "kind": "payment"}})
frappe.db.commit()
print(json.dumps({{"company": company["id"], "doc": name, "connection": conn["name"]}}, default=str))
""")
    return {**out, "ref": ref, "subject": SUBJECT, "cases": []}


def steps(params, camera_code=None):
    args = json.dumps({"company": params["company"], "doc_ids": json.dumps([params["doc"]])})
    find = ("const items=c=>(c&&c.groups||[]).flatMap(g=>g.items.map(i=>({group:g.group,kind:i.kind,state:i.state,value:i.value,"
            "source:i.source,key:i.key,docs:i.docs})));")
    return [
        {"name": "取卡片", "js": JS + find + f"const c=await call('pearnly_core.write_precheck.api.check',{args},true); "
         "return {needs: c&&c.needs_card, items: items(c), ready: c&&c.ready, title: c&&c.company_title, "
         "targets: (c&&c.targets||[]).map(t=>[t.account_set,t.count])};"},
        {"name": "答错整批不记", "js": JS + find + f"const a={args}; const c=await call('pearnly_core.write_precheck.api.check',a,true); "
         "const pay=items(c).find(i=>i.kind==='payment'); if(!pay) return {error:'no payment item'}; "
         "a.answers=JSON.stringify([{key:pay.key,value:'later'}]); "
         "const r=await call('pearnly_core.write_precheck.api.answer',a,true); "
         "return {errors: r&&r.errors, still: items(r).find(i=>i.kind==='payment')};"},
        {"name": "搜存货", "js": JS + f"const r=await call('pearnly_core.write_precheck.api.search',"
         f"{{company:{json.dumps(params['company'])},doc_id:{json.dumps(params['doc'])},search:'products',page_length:5}}); "
         "return {codes: (r&&r.options||[]).map(o=>o.code), more: r&&r.more};"},
        {"name": "作答", "js": JS + find + f"const a={args}; const c=await call('pearnly_core.write_precheck.api.check',a,true); "
         "const pay=items(c).find(i=>i.kind==='payment'); if(!pay) return {error:'no payment item'}; "
         "a.answers=JSON.stringify([{key:pay.key,value:'cash'}]); "
         "const r=await call('pearnly_core.write_precheck.api.answer',a,true); "
         "return {errors: r&&r.errors||null, writable: r&&r.writable, held: r&&r.held, needs: r&&r.needs_card, items: items(r)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, wrong, done, found = (by.get(k) or {} for k in ("取卡片", "答错整批不记", "作答", "搜存货"))
    first_items = {i.get("kind"): i for i in first.get("items") or []}
    done_items = {i.get("kind"): i for i in done.get("items") or []}
    pay, account = first_items.get("payment") or {}, first_items.get("account") or {}
    return [
        {"case": "卡片要出：现 / 赊是预选（付款组、按票种预选赊购），费用科目是记住的已定（服务组），写进这份账套 1 张", "seen": first,
         "ok": first.get("needs") is True and pay.get("group") == "payment" and pay.get("state") == "proposed"
         and pay.get("value") == "credit" and account.get("state") == "decided" and account.get("value") == "5300"
         and [t[1] for t in first.get("targets") or []] == [1] and pay.get("docs") == [params["doc"]]},
        {"case": "不在可选范围里的值整批不记，回原因码，这一项仍是预选", "seen": wrong,
         "ok": [e.get("code") for e in wrong.get("errors") or []] == ["precheck.value_not_offered"]
         and (wrong.get("still") or {}).get("state") == "proposed"},
        {"case": "大列表按这张单的账套服务端分页：存货第一页最前面是「在 Express 新建」（合成账套没有存货主档）", "seen": found,
         "ok": found.get("codes") == ["__new__"] and found.get("more") is False},
        {"case": "改成现购后记下、重判变已定；这张照写，不再出卡片", "seen": done,
         "ok": done.get("errors") is None and done.get("writable") == [params["doc"]] and done.get("held") == []
         and done.get("needs") is False and (done_items.get("payment") or {}).get("state") == "decided"
         and (done_items.get("payment") or {}).get("value") == "cash"},
    ]


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], CLEANUP + f"""
frappe.set_user("admin@cowork.regression")
saved = frappe.get_all("Cowork Express Choice", filters={{"connection": {params['connection']!r}, "kind": "payment"}},
                       fields=["subject", "value"], limit_page_length=10)
doc = frappe.db.get_value("Cowork OCR History", {params['doc']!r}, ["last_push_status", "last_push_record"], as_dict=True)
records = frappe.db.count("Cowork Push Record", {{"history": {params['doc']!r}}})
gone = cleanup()
print(json.dumps({{"saved": saved, "doc": dict(doc or {{}}), "records": records, "gone": gone, "left": leftovers()}}, default=str))
""")
    saved = [(row["subject"], row["value"]) for row in rows["saved"]]
    return [
        {"case": "现 / 赊按供应商记进账套记忆（同复核页那条路），只记一条", "saved": rows["saved"],
         "ok": saved == [(params["subject"], "cash")]},
        {"case": "取卡片与作答都不推送：没有推送记录、单据推送状态不变", "saved": {"doc": rows["doc"], "records": rows["records"]},
         "ok": rows["records"] == 0 and not rows["doc"].get("last_push_status") and not rows["doc"].get("last_push_record")},
        {"case": "造的连接、公司、单据都清掉", "saved": {"gone": rows["gone"], "left": rows["left"]},
         "ok": rows["gone"]["connections"] == 1 and not any(rows["left"].values())},
    ]
