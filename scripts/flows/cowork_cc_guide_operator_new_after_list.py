"""流程用例：Cowork 版 CC 代录引导，资料清单开着时又进来一张新票、页面不刷新就点确定（11-25 回归，main 4eb0b2499）。

真实场景：代录对话里资料清单已经列过（这次代录的列出快照记下了 A），之后又有一张票进了这家这个月的待录清单（LINE / 上传 / 复核页改了日期），
用户没刷新，直接在复核卡上点确定。确定 → 交单（`express_operator.api.waiting`）第一次把这张新票记进快照；这一笔没提交时，接着的
Cowork 判定开头结束读快照（`precheck_source.document` → `transaction.snapshot.require_clean`）拒绝，结果卡「未完成」。
修法：`waiting` 记完快照（与读不到现状时的错误日志）先提交、再进判定（见 `api.waiting` 那段说明）。

造数据同 `cowork_cc_guide_operator_lib`（代录对话、一张本月识别完成的采购税票 A、``run.start(doc_ids=[A])``），另造一张同业务的票 B，
单据日期在别的月份（不在这次代录的待录清单里）。页面上：
1. 打开代录对话；
2. 资料清单：照清单卡同一个接口（``express_operator.api.documents``，POST，带任务）列这家本月——有 A、没有 B；
3. 题卡逐题点 → 复核卡（只有 A）；
4. 进来一张新票：照复核页同一条路（``history.api.detail`` → ``history.api.save``）把 B 的单据日期改进本月——B 进了待录清单，页面不刷新；
5. 点确定 → 结果卡交进代录（A）。
库里：引导运行 done；A 交进代录任务一次、B 不交（不是这次引导的票）；快照里 A、B 都在（B 是确定时第一次记进来的）；
这段时间站点 Error Log 没有「Snapshot reset with pending writes」。
"""
from __future__ import annotations

import json
import uuid

import cc_conversation_ledger as ledger
import cowork_cc_guide_docs_lib as docs
import cowork_cc_guide_operator_lib as lib
from cc_lib import JS

TITLE = "Cowork 版 CC 代录引导：资料清单开着时又进来一张新票、不刷新就点确定 → 结果卡交进代录；新票确定时记进快照、不再报未提交的写"
BUSINESS = "purchase"
USER = ledger.USER
HELP = "const card=()=>" + docs.CARD + "; "
POST = ("const post=async(m,body)=>{const r=await fetch('/api/method/'+m,{method:'POST',headers:{'X-Frappe-CSRF-Token':window.csrf_token||'',"
        "'Content-Type':'application/json'},body:JSON.stringify(body)}); const j=await r.json().catch(()=>({})); "
        "return {status:r.status, message:j.message, exc:j.exc_type||''};}; ")


def prepare(lab, manifest):
    """同 `cowork_cc_guide_operator_lib.prepare`，同一次造数据里另造 B（造数据开头会先清掉上次的，不能分两次）：同一家、同业务，
    单据日期在别的月份（`guided_doc` 默认那天），不在这次代录的待录清单里。另记开跑时刻，verify 只看之后的日志。"""
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "gn" + uuid.uuid4().hex[:6]
    number = "FLOW-GN-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, docs._seed_code() + f"""
from pearnly_core.assistant import sessions
from pearnly_core.assistant.guide import run
at = str(frappe.utils.now_datetime())
name = guided_doc({BUSINESS!r}, {number!r}, {ledger.CHAT_MONTH + "-01"!r})
late = guided_doc({BUSINESS!r}, {number + "-B"!r})
made = sessions.create(project=company["id"], period={ledger.CHAT_MONTH!r}, mode="operator", ledger=ids.get({ledger.T8!r}, ""))
sid = made["id"]
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": sid}}, "title", {tag!r})
frappe.db.commit()
try:
    started = run.start(sid, {BUSINESS!r}, doc_ids=[name], at="asking")
    frappe.db.commit()
    started = {{"run": started["run"], "status": started["status"], "stage": started.get("stage") or ""}}
except Exception as error:
    frappe.db.rollback()
    started = {{"run": "", "error": repr(error)[:300]}}
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "late": late, "connection": conn["name"],
                  "t8": ids.get({ledger.T8!r}, ""), "t9": ids.get({ledger.T9!r}, ""), "session": sid,
                  "task": made.get("task") or "", "started": started, "at": at}}, default=str))
""")
    return {**out, "business": BUSINESS, "number": number, "tag": tag, "month_day": ledger.CHAT_MONTH + "-01",
            "run": (out.get("started") or {}).get("run") or "", "cases": []}


def steps(params, camera_code=None):
    opened, asked, review, done = lib.steps(params, camera_code)
    period = ledger.CHAT_MONTH
    listing = {"name": "资料清单", "js": JS + POST +
               f"const r=await post('pearnly_core.express_operator.api.documents',{{company:{json.dumps(params['company'])}, "
               f"period:{json.dumps(period)}, offset:0, limit:50, task:{json.dumps(params['task'])}}}); "
               "return {status:r.status, exc:r.exc, docs:(r.message?.documents||[]).map(d=>d.doc_id)};"}
    arrive = {"name": "进来一张新票", "js": JS + HELP + POST +
              f"const name={json.dumps(params['late'])}; "
              "const got=await fetch('/api/method/pearnly_cowork.history.api.detail?name='+encodeURIComponent(name)).then(r=>r.json()); "
              "const d=got.message||{}; const pages=typeof d.pages==='string'?JSON.parse(d.pages):(d.pages||[]); "
              f"for(const p of pages){{(p.fields=p.fields||{{}}).date={json.dumps(params['month_day'])};}} "
              "const r=await post('pearnly_cowork.history.api.save',{name, pages:JSON.stringify(pages), expected_version:d.record_version}); "
              f"const after=await post('pearnly_core.express_operator.api.documents',{{company:{json.dumps(params['company'])}, "
              f"period:{json.dumps(period)}, offset:0, limit:50}}); "  # 不带任务：只看待录清单里有没有它，不记快照
              # 页面没刷新：复核卡还在、确定照样能点
              "const b=card()?.querySelector('[data-slot=\"cc-guide-confirm\"]'); "
              "return {saved:r.status, exc:r.exc, waiting:(after.message?.documents||[]).map(x=>x.doc_id), confirm:!!b&&!b.disabled};"}
    return [opened, listing, asked, review, arrive, done]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    listing, arrive = by.get("资料清单") or {}, by.get("进来一张新票") or {}
    doc, late = params["doc"], params["late"]
    base = lib.judge(params, results, camera_code)
    return [
        {"case": "先开资料清单：这家本月列出 A、没有 B（B 的单据日期在别的月份）", "seen": listing,
         "ok": listing.get("status") == 200 and doc in (listing.get("docs") or []) and late not in (listing.get("docs") or [])},
        *base,
        {"case": "清单开着时进来一张新票：复核页同一条路改日期后 B 进了本月待录清单，页面没刷新、确定照样能点", "seen": arrive,
         "ok": arrive.get("saved") == 200 and late in (arrive.get("waiting") or []) and bool(arrive.get("confirm"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
listed = json.loads(frappe.db.get_value("Pearnly Express Operator Task", {params.get('task', '')!r}, "listed") or "{{}}")
errors = frappe.get_all("Error Log", filters={{"method": "Snapshot reset with pending writes", "creation": [">=", {params['at']!r}]}},
                        pluck="name", limit_page_length=20)
print(json.dumps({{"listed": sorted(listed), "pending_writes": errors}}, default=str))
""")
    base = lib.verify(lab, manifest, params, camera_code)
    listed = got.get("listed") or []
    return [
        *base,
        {"case": "B 不是这次引导的票、不交进代录任务（只交 A）", "seen": base[-1]["seen"],
         "ok": all(str(item).split(":")[-1] != params["late"] for item in base[-1]["seen"] or [])},
        {"case": "快照里 A、B 都在（B 是点确定时第一次记进来的，已落库）", "seen": listed,
         "ok": params["doc"] in listed and params["late"] in listed},
        {"case": "这段时间站点没有「Snapshot reset with pending writes」", "seen": got.get("pending_writes"),
         "ok": not got.get("pending_writes")},
    ]


cleanup = lib.cleanup
