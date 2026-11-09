"""流程用例：Cowork 同一张票只写一次（复核 5 批之第 1 批，`pearnly_core.ledger_tickets`；11-08 生产 SincereIce 的形状，同 ERP 的
`cc_ticket_once.py`）——同一份 24 张票在代录里等着，其中 4 张已经从截图直写进了 Express（单号写法不同：``/`` 与 ``-``）。
代录开始前卡片标出这 4 张「已写入」和它们在 Express 里的单号；点「开始」只交另外 20 张；站点交单（服务端在按身份的锁里再判）
同样跳过 4 张。只看我们自己的推送记录。

数据（`cowork_operator_lib.py`，产品自己的函数）：一条合成的 Express 连接与账套、从目录建的公司（带税号），24 张识别单
（双方税号齐全，身份是精确的）；另 4 张同一张票的识别单交出去后记成写成（推送记录带单号，代替电脑回执）。每次 prepare 先清、
verify 末尾再清，站点回到流程前的样子。网页上没有执行电脑：「开始」只截下交给站点的 start。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "Cowork 同一张票只写一次：24 张里 4 张已从直写写进 Express，代录开始前卡片标出 4 张已写入、只交 20 张，站点交单同样跳过"
READY = "document.readyState === 'complete' && !!document.querySelector('[data-mode=\"operator\"]')"
USER = "admin@cowork.regression"
TOTAL, WRITTEN = 24, 4


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    out = lab.python(site, CLEANUP + BASE + f"""
waiting = [recognized("IV69-%02d" % i) for i in range(1, {TOTAL} + 1)]
twins = []
for i in range(1, {WRITTEN} + 1):
    name = recognized("IV69/%02d" % i)  # 同一张票、截图读出的写法
    record = prepared(name)
    frappe.db.set_value("Cowork Push Record", record, {{"status": "success", "external_docnum": "RR-ONCE-%02d" % i}},
                        update_modified=False)
    frappe.db.set_value("Cowork OCR History", name, "last_push_status", "success", update_modified=False)
    twins.append(name)
frappe.db.commit()
rows = frappe.get_all("Cowork OCR History", filters={{"name": ["in", waiting + twins]}}, fields=["ticket_level", "ticket_key"])
print(json.dumps({{"company": company["id"], "title": company["title"], "period": period, "waiting": waiting, "twins": twins,
                  "skipped": waiting[:{WRITTEN}], "levels": sorted({{r.ticket_level or "" for r in rows}}),
                  "keys": len({{r.ticket_key for r in rows}})}}))
""")
    tag = "flow" + uuid.uuid4().hex[:6]
    return {**out, "tag": tag, "line": f"{tag} 录这 24 张", "cases": []}


def steps(params, camera_code=None):
    title, line = params["title"], params["line"]
    return [
        {"name": "开始前卡片", "js": JS + "await toMode('operator'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(title)}); const sent=await say({json.dumps(line)}); "
         "const card=await until(()=>q('[data-slot=\"cc-op-before\"] .cc-op-before-body'),200); await sleep(800); "
         "for(let k=0;k<5;k++){const more=q('[data-slot=\"cc-op-before\"] .cc-show-more'); if(!more) break; more.click(); await sleep(600);} "
         "const written=qa('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-written\"]').map(e=>e.innerText.trim()); "
         "return {picked, sent, card, written};"},
        {"name": "开始", "js": JS + "let body=null; const orig=window.fetch; "
         "window.fetch=async(u,i={})=>{const m=(String(u).split('express_operator.api.')[1]||'').split('?')[0]; "
         "if(m==='start'){ body=JSON.parse(i.body||'{}'); return new Response(JSON.stringify({message:{batch:'flow',results:{}}}),"
         "{status:200,headers:{'Content-Type':'application/json'}}); } return orig(u,i);}; "
         "const b=q('[data-slot=\"cc-op-before\"] [data-slot=\"cc-op-start\"]'); if(!b){window.fetch=orig; return {error:'no start'};} b.click(); "
         "await until(()=>body,160); window.fetch=orig; return {ids: body?JSON.parse(body.doc_ids||'[]'):[]};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    card, started = by.get("开始前卡片") or {}, by.get("开始") or {}
    written, ids = card.get("written") or [], started.get("ids") or []
    ours = set(params["waiting"])
    return [
        {"case": "数据：28 张识别单身份都是精确的，写法不同的 4 对是同一张票", "seen": {"levels": params["levels"], "keys": params["keys"]},
         "ok": params["levels"] == ["exact"] and params["keys"] == len(params["waiting"])},
        {"case": "开始前卡片标出 4 张已写入，各带它在 Express 里的单号", "seen": {"written": written},
         "ok": len(written) == len(params["skipped"]) and all(f"RR-ONCE-{i:02d}" in " ".join(written) for i in range(1, len(params["skipped"]) + 1))},
        {"case": "点「开始」只交另外 20 张（已写入的 4 张不交）", "seen": {"ids": len([i for i in ids if i in ours]),
         "skipped_sent": [i for i in ids if i in params["skipped"]]},
         "ok": len([i for i in ids if i in ours]) == len(ours) - len(params["skipped"]) and not [i for i in ids if i in params["skipped"]]},
    ]


def verify(lab, manifest, params, camera_code=None):
    """站点交单（服务端在按身份的锁里再判，不信页面）：24 张一起交，4 张回「已写入」带单号、20 张交出；说了再写一次的照交。
    真正的推送准备换成替身（不建推送记录）。末尾清掉造的数据。"""
    out = lab.python(manifest["site"], CLEANUP + f"""
from unittest import mock
from pearnly_cowork.assistant import operator_source, tools
frappe.set_user({USER!r})
sent = []
with mock.patch.object(tools, "_push_one_unguarded", lambda name, *a, **k: sent.append(name) or {{"status": "sent"}}), \\
        mock.patch.object(operator_source, "_settle", lambda name, memory: None):
    results = operator_source.submit({params["company"]!r}, {params["waiting"]!r}, {{"id": "flow", "total": 24, "start_at": 0}}, "PC-FLOW")
    again = operator_source.submit({params["company"]!r}, {params["skipped"][:1]!r},
                                   {{"id": "flow", "total": 1, "start_at": 0, "again": {params["skipped"][:1]!r}}}, "PC-FLOW")
frappe.db.rollback()
out = {{"written": [r["doc_id"] for r in results if r["status"] == "written"],
        "docnums": [r["reason"] for r in results if r["status"] == "written"],
        "sent": len(sent) - len(again), "again": [r["status"] for r in again]}}
out["gone"] = cleanup()
print(json.dumps(out, default=str))
""")
    return [
        {"case": "站点交单同样跳过这 4 张（带 Express 单号）、交出 20 张", "saved": out,
         "ok": out["written"] == params["skipped"] and out["sent"] == len(params["waiting"]) - len(params["skipped"])
         and all(str(n).startswith("RR-ONCE-") for n in out["docnums"])},
        {"case": "人说了再写一次的那张照交", "saved": out["again"], "ok": out["again"] == ["sent"]},
    ]
