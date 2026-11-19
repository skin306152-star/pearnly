"""流程用例：ERP 版 CC 对话里的库存四单引导（出库、调拨、物料调整、入库；`inventory.cc_stock_guides` + `inventory.stock_guide`）。

真页面、假智能体（只用来开一段对话：说一句话，假后端回一句）；新开户那套数据（公司按正式方式接上 Express、7 个仓、几件物料）。
不连 Express、不发 LINE、不调模型。电脑与手机宽度各跑一遍。

页面上：先在输入框下面选公司（同真实用户），说一句开对话；业务菜单里有「出库 / 调拨 / 物料调整 / 入库」。四种各走一遍，
用页面同一组接口（`pearnly_core.assistant.api.guide_start / guide_answer / guide_confirm`）照此刻卡上的题作答：
- 入库：仓 01，本次物料 10 件进仓 01、单价 12，用途「其他」，带一张对话里上传的图片当附件；
- 出库：仓 01 出 1 件，用途「报损」；
- 调拨：仓 01 → 仓 02，2 件；
- 物料调整：仓 02 增加 1 件（单价 5）、仓 01 减少 1 件。
账套缺簿时那一题照预选答（同网页弹窗）。每种复核后点「确定」，结果一行。
库里：四张 ERP Stock Entry 都已提交（备注带本次标记），方向、行上的仓、数量、单价、用途与卡上答的一样；同意键 ``cc:guide:<运行>:<版本>``；
入库那张挂着一个附件。cleanup 作废这四张（冲回库存）、撤掉没写的写账任务。
"""
from __future__ import annotations

import json
import uuid

from cc_approval_lib import JS, set_language
from erp_lib import codes, company, warehouses

TITLE = "ERP 版 CC 库存四单引导：选公司 → 菜单有出库 / 调拨 / 物料调整 / 入库 → 四种各走一遍到确定（按行选仓、单价、用途、附件）→ 读回库存单"
USER = "admin@erp.regression"
BUSINESSES = {"stock_out": "出库", "stock_transfer": "调拨", "stock_adjust": "物料调整", "stock_in": "入库"}
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def prepare(lab, manifest):
    site = manifest["site"]
    set_language(lab, site, USER, "zh")
    tag = "gs" + uuid.uuid4().hex[:6]
    stores = [row["name"] for row in warehouses(manifest)][:2]
    product = next(iter(codes(manifest).values()))["product"]
    title = manifest.get("company_name") or lab.python(
        site, f"print(json.dumps({{'t': frappe.db.get_value('ERP Company', {company(manifest)!r}, 'company_name')}}))")["t"]
    return {"tag": tag, "title": title, "company": company(manifest), "wh1": stores[0], "wh2": stores[1], "product": product,
            "cases": []}


def _code(warehouse, product) -> str:
    return f"n:{warehouse}|{product}"


def plans(params) -> dict:
    """每种引导要答的值（键 = 题目 key；卡上此刻有哪题就答哪题）。"""
    wh1, wh2, product, tag = params["wh1"], params["wh2"], params["product"], params["tag"]
    return {
        "stock_in": {"warehouse": wh1, "purpose": "other", "rows": [{"code": _code(wh1, product), "amount": "10"}],
                     "prices": [{"code": _code(wh1, product), "amount": "12"}], "note": f"flow {tag} in"},
        "stock_out": {"warehouse": wh1, "purpose": "damage", "rows": [{"code": _code(wh1, product), "amount": "1"}],
                      "note": f"flow {tag} out"},
        "stock_transfer": {"warehouse": wh1, "to_warehouse": wh2, "rows": [{"code": _code(wh1, product), "amount": "2"}],
                           "note": f"flow {tag} transfer"},
        "stock_adjust": {"warehouse": wh2, "plus": [{"code": _code(wh2, product), "amount": "1"}],
                         "minus": [{"code": _code(wh1, product), "amount": "1"}],
                         "prices": [{"code": _code(wh2, product), "amount": "5"}], "note": f"flow {tag} adjust"},
    }


# 一种引导走到底（页面同一组接口）：照此刻卡上的题答计划里有的；账套那题照预选；复核后确定。``file`` 有值时一并交上
RUN = (
    "const sid=async tag=>((await listed('chat')).find(s=>(s.title||'').includes(tag))||{}).id||'';"
    "const walk=async(session,business,plan)=>{"
    " let v=await call('pearnly_core.assistant.api.guide_start',{session_id:session,business},true); const asked=[]; let refused=null;"
    " for(let round=0; round<8 && v&&v.status==='asking'; round++){"
    "  const given={}; for(const q of ((v.card||{}).questions||[])){ asked.push(q.key);"
    "   if(q.key in plan) given[q.key]=plan[q.key];"
    "   else if(q.key==='book'){ const a={}; for(const s of q.questions||[]) a[s.key]=s.preselected||((s.options||[])[0]||{}).code; given.book=a; } }"
    "  if(!Object.keys(given).length) break;"
    "  v=await call('pearnly_core.assistant.api.guide_answer',{run_name:v.run,answers:JSON.stringify(given),version:v.version},true);"
    "  if(v&&v.refused){ refused=v.refused; break; } }"
    " const review=v&&v.status==='reviewing'?v.review:null;"
    " const done=review?await call('pearnly_core.assistant.api.guide_confirm',{run_name:v.run,version:v.version},true):null;"
    " return {run:v&&v.run, status:(done||v||{}).status||'', asked:[...new Set(asked)], refused,"
    "  review_lines:(review&&review.lines||[]).map(l=>l.key), result:done&&done.result||null};};"
)


def steps(params, camera_code=None):
    # 每步自己的代码包进一个块：与公用片段 JS 里同名的 card / q / row… 各在各的作用域（同 erp_cc_guide_collection）
    return [{**step, "js": JS + "{" + step["js"][len(JS):] + "}"} if step["js"].startswith(JS) else step for step in _steps(params)]


def _steps(params):
    tag = json.dumps(params["tag"])
    plan = plans(params)
    one = lambda business, extra="": (  # noqa: E731
        JS + RUN + f"const session=await sid({tag}); if(!session) return {{error:'no session'}}; "
        f"const plan={json.dumps(plan[business])}; {extra} return await walk(session,{json.dumps(business)},plan);")
    upload = ("const bytes=Uint8Array.from(atob(" + json.dumps(PIXEL) + "),c=>c.charCodeAt(0)); const fd=new FormData(); "
              "fd.append('session_id',session); fd.append('file',new File([bytes],'slip.png',{type:'image/png'})); "
              "const up=await fetch('/api/method/pearnly_core.assistant.api.upload',{method:'POST',"
              "headers:{'X-Frappe-CSRF-Token':window.csrf_token||''},body:fd}).then(r=>r.json()).catch(()=>({})); "
              "const fileId=((up||{}).message||{}).fileId||''; if(fileId) plan.file=[fileId];")
    return [
        {"name": "选公司", "js": JS + f"const chosen=await pickCompany({json.dumps(params['title'])}); return {{chosen}};"},
        {"name": "开对话", "js": JS + f"const sent=await say({json.dumps(params['tag'] + ' 你好')}); await sleep(1500); return {{sent}};"},
        {"name": "业务菜单", "js": JS + "const row=()=>q('[data-slot=\"cc-business-menu\"]'); await until(()=>row(),120); "
         "const session=((await listed('chat')).find(s=>(s.title||'').includes(" + tag + "))||{}).id||''; "
         "const offered=await call('pearnly_core.assistant.api.businesses',{session_id:session}); "
         "return {chat:(offered||{}).chat||[], labels:(offered||{}).labels||{}, order:(offered||{}).order||{}};"},
        {"name": "入库", "js": one("stock_in", upload)},
        {"name": "出库", "js": one("stock_out")},
        {"name": "调拨", "js": one("stock_transfer")},
        {"name": "物料调整", "shot": True, "file": "1-adjust", "js": one("stock_adjust")},
        {"name": "删除会话", "js": JS + f"const mine=(await listed('chat')).filter(s=>(s.title||'').includes({tag})); "
         "for(const s of mine) await call('pearnly_core.assistant.api.delete',{session_id:s.id},true); return {ids: mine.map(s=>s.id)};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    menu = by.get("业务菜单") or {}
    out = [{"case": "选好公司（接了 Express）后业务菜单有出库、调拨、物料调整、入库，顺序 61–64", "seen": menu,
            "ok": all(name in (menu.get("chat") or []) for name in BUSINESSES)
            and [menu.get("order", {}).get(name) for name in BUSINESSES] == [61, 62, 63, 64]}]
    for business, label in BUSINESSES.items():
        found = by.get(label) or {}
        out.append({"case": f"{label}：卡上逐题答完进复核，确定后办完，结果带 ERP 单号", "seen": found,
                    "ok": found.get("status") == "done" and not found.get("refused") and "day" in (found.get("review_lines") or [])
                    and bool((found.get("result") or {}).get("doc_no"))})
    out.append({"case": "删掉这段会话", "seen": by.get("删除会话"), "ok": bool((by.get("删除会话") or {}).get("ids"))})
    return out


def _read(lab, manifest, params) -> dict:
    return lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Entry", filters={{"reason": ["like", "flow {params['tag']} %"]}},
                      fields=["name", "direction", "status", "warehouse", "to_warehouse", "purpose", "reason", "dedupe_key"],
                      order_by="creation asc", limit_page_length=10)
for row in rows:
    row["lines"] = frappe.get_all("ERP Stock Entry Line", filters={{"parent": row["name"]}}, fields=["product", "qty", "warehouse", "unit_cost"],
                                  order_by="idx asc", limit_page_length=20)
    row["files"] = len(frappe.get_all("File", filters={{"attached_to_doctype": "ERP Stock Entry", "attached_to_name": row["name"]}},
                                      pluck="name", limit_page_length=20))
print(json.dumps({{"rows": rows}}, default=str))
""")


def verify(lab, manifest, params, camera_code=None):
    found = _read(lab, manifest, params)
    rows = {row["direction"]: row for row in found.get("rows") or []}
    wh1, wh2, product = params["wh1"], params["wh2"], params["product"]

    def lines(direction):
        return [(line["product"], float(line["qty"]), line["warehouse"]) for line in (rows.get(direction) or {}).get("lines") or []]

    def cost(direction, index=0):
        items = (rows.get(direction) or {}).get("lines") or []
        return float(items[index]["unit_cost"] or 0) if len(items) > index else None

    entry_in, entry_out = rows.get("in") or {}, rows.get("out") or {}
    entry_transfer, entry_adjust = rows.get("transfer") or {}, rows.get("adjust") or {}
    return [
        {"case": "四张都已提交、同意键是页面上这一下（cc:guide:<运行>:<版本>）", "seen": found,
         "ok": len(rows) == 4 and all(row["status"] == "submitted" and str(row["dedupe_key"] or "").startswith("cc:guide:")
                                       for row in rows.values())},
        {"case": "入库：仓 01 进 10 件、单价 12、用途其他、挂一个附件", "seen": entry_in,
         "ok": lines("in") == [(product, 10.0, wh1)] and cost("in") == 12.0 and entry_in.get("purpose") == "other"
         and entry_in.get("files") == 1},
        {"case": "出库：仓 01 出 1 件、用途报损", "seen": entry_out,
         "ok": lines("out") == [(product, 1.0, wh1)] and entry_out.get("purpose") == "damage"},
        {"case": "调拨：仓 01 → 仓 02，2 件", "seen": entry_transfer,
         "ok": (entry_transfer.get("warehouse"), entry_transfer.get("to_warehouse")) == (wh1, wh2)
         and lines("transfer") == [(product, 2.0, wh1)]},
        {"case": "物料调整：按行选仓（仓 02 增加 1 件单价 5、仓 01 减少 1 件）", "seen": entry_adjust,
         "ok": lines("adjust") == [(product, 1.0, wh2), (product, -1.0, wh1)] and cost("adjust") == 5.0},
    ]


def cleanup(lab, manifest, params):
    """作废本次的四张（冲回库存，后建的先作废），撤掉还没写的写账任务。"""
    lab.python(manifest["site"], f"""
from pearnly_core.cc_desktop import jobs
from pearnly_erp_portal.inventory import documents
frappe.set_user("Administrator")
names = frappe.get_all("ERP Stock Entry", filters={{"reason": ["like", "flow {params['tag']} %"], "status": "submitted"}},
                       pluck="name", order_by="creation desc", limit_page_length=10)
for name in names:
    for job in frappe.get_all("Pearnly CC Job", filters={{"source_doctype": "ERP Stock Entry", "source_name": name,
                              "status": ["in", ["queued", "manual"]]}}, pluck="name", limit_page_length=20):
        jobs.cancel(job, "flow cleanup")
    try:
        documents.void_entry(name)
    except Exception:
        frappe.db.rollback()
frappe.db.commit()
print(json.dumps({{"voided": names}}))
""")
