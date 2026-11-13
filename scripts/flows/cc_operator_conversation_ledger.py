"""流程用例：代录新建页先选账套再发第一句（测试机1 11-12：新建页没有账套行，第一句之前选不了，只能等 CC 问）。

同 `cc_conversation_ledger.py` 的造数据（Cowork：一条 Express 连接，同一税号两本同期账套 T.8、T.9，都没记住现用的；一张识别好的进项票，
付款与科目两本都记住了），只是票据日期放在任务那个月（2026-10-09）、票上记的去向是同期的 T.9（代录按这家这个月的待录清单建计划，
清单按公司的账套列）。

1. 切到代录，新建页（还没建会话）选这家（月份默认上个月 2026-10）：账套胶囊未定（两本同期），点开选 T.8，再说第一句 → 建出代录会话与
   任务，接口读回的本对话账套就是 T.8，不出提问卡。
2. 说一句建写入计划（``pearnly_write_plan``，代录按任务这个月的待录清单）→ ready、这张票、目标 T.8（票上原来是 T.9，改投），
   不出选账套的提问卡。
   只建计划不开始（网页上没有执行电脑，开始与交单由离线测试锁住）。
3. verify：库里这段代录会话的 ``project_ledger`` = T.8、挂着任务；计划记的账套是 T.8。cleanup 删会话（任务、计划随之删）与造的数据。
不连 Express、不调模型。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_lib import JS  # noqa: E402
from cc_conversation_ledger import (EXTRA_CLEANUP, LEDGER_IDS, RESULT, SEED, T8, USER, _mine, cleanup as _cleanup,  # noqa: E402
                                    new_page_ledger)
from cowork_operator_lib import CLEANUP  # noqa: E402

TITLE = "CC 代录新建页先选账套（Cowork）：选 T.8 再发第一句，代录会话的本对话账套就是 T.8，建计划写进 T.8、不问账套"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea[aria-label]')"
# 票据日期放进任务那个月（代录按这家这个月的待录清单建计划）
OCTOBER_SEED = SEED.replace('"date": "2026-06-09"', '"date": "2026-10-09"')
assert OCTOBER_SEED != SEED, "cc_conversation_ledger.SEED changed: move the document date into 2026-10 again"


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-OL-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, _mine(CLEANUP) + EXTRA_CLEANUP + OCTOBER_SEED + LEDGER_IDS + f"""
name = recognized({ref!r})
# 网页 / 识别时记成了同期的 T.9（代录按公司列待录清单，要有这家的去向）：代录会话的本对话账套是 T.8，计划照 T.8 写（改投）
frappe.db.set_value("Cowork OCR History", name, {{"push_connection": conn["name"], "push_account_set": "FLOWT9"}})
frappe.db.commit()
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "connection": conn["name"],
                  "t8": ids.get({T8!r}, "")}}))
""")
    tag = "ol" + uuid.uuid4().hex[:6]
    plan = {"name": "pearnly_write_plan", "input": {}}
    return {**out, "ref": ref, "tag": tag, "cases": [],
            "open_line": f"{tag} 这个月的票录进 Express",
            "plan_line": f"{tag} 写入 [lab-tool]{json.dumps(plan)}[/lab-tool]"}


def steps(params, camera_code=None):
    company = json.dumps(params["company"])
    return [
        {"name": "代录新建页选 T.8 后说一句", "js": JS + RESULT + "const switched=await toMode('operator'); "
         "await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); " + new_page_ledger("T.8") +
         f"const sent=await say({json.dumps(params['open_line'])}); "
         f"const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent]')&&e.innerText.includes({json.dumps(params['tag'])})),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         "let row=null; for(let k=0;k<40&&!row;k++){row=(await listed('operator')).find(s=>s.project==="+company+"&&s.task)||null; "
         "if(!row) await sleep(500);} "
         "const session=(row&&row.id)||''; "
         "const read=session?((await call('pearnly_core.assistant.api.ledgers',{session_id:session}))||{}):{}; "
         "return {switched, mode: mode(), picked, chip_found, chip_before, chip_options, chip_after, sent, replied, session, "
         "task: (row&&row.task)||'', period: (row&&row.period)||'', current: read.current||'', asked: asked()};"},
        {"name": "说一句建写入计划", "js": JS + RESULT + f"const turn=await toolTurn({json.dumps(params['plan_line'])},'pearnly_write_plan'); "
         "const r=turn.result||{}; "
         "return {sent: turn.sent, status: r.status||'', plan: r.plan||'', count: r.count, documents: r.documents||[], "
         "skipped: r.skipped||[], account_sets: r.account_sets||[], asked: asked(), raw: r.unparsed||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, made = (by.get(k) or {} for k in ("代录新建页选 T.8 后说一句", "说一句建写入计划"))
    return [
        {"case": "代录新建页（还没建会话）选了公司就有账套胶囊：两本同期时未定；点开列出 T.8、T.9，选 T.8 后显示 T.8；说第一句建出"
                 "代录会话与任务（月份 2026-10），服务端读回本对话账套就是 T.8，没出提问卡", "seen": opened,
         "ok": opened.get("mode") == "operator" and bool(opened.get("picked")) and opened.get("chip_found") is True
         and opened.get("chip_before") not in ("T.8", "T.9") and {"T.8", "T.9"} <= set(opened.get("chip_options") or [])
         and opened.get("chip_after") == "T.8" and bool(opened.get("replied")) and bool(opened.get("task"))
         and opened.get("period") == "2026-10" and opened.get("current") == params["t8"] and opened.get("asked") == 0},
        {"case": "说一句建写入计划：ready、写这张票、目标 T.8；不出选账套的提问卡", "seen": made,
         "ok": made.get("status") == "ready" and params["doc"] in (made.get("documents") or [])
         and [(item.get("account_set"), item.get("account_set_title")) for item in made.get("account_sets") or []]
         == [(T8, "T.8")] and made.get("asked") == 0},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
sessions = frappe.get_all("Pearnly Assistant Session", filters={{"user": {USER!r}, "project": {params['company']!r}}},
                          fields=["session_id", "project_ledger", "operator_task"], limit_page_length=5)
plans = frappe.get_all("Pearnly Assistant Write Plan", filters={{"session_id": ["in", [s.session_id for s in sessions] or ["-"]]}},
                       fields=["name", "status", "ledger", "mode"], limit_page_length=5)
print(json.dumps({{"sessions": sessions, "plans": plans}}, default=str))
""")
    sessions, plans = got["sessions"], got["plans"]
    return [
        {"case": "库里这段代录会话挂着任务、本对话账套是 T.8（建会话时就存下，不是之后问的）", "saved": sessions,
         "ok": len(sessions) == 1 and bool(sessions[0]["operator_task"]) and sessions[0]["project_ledger"] == params["t8"]},
        {"case": "写入计划是代录的、记的账套是 T.8、还没开始", "saved": plans,
         "ok": len(plans) == 1 and plans[0]["ledger"] == params["t8"] and plans[0]["mode"] == "operator"
         and plans[0]["status"] == "awaiting"},
    ]


def cleanup(lab, manifest, params):
    """成功、失败、超时都跑：删这家公司的对话（代录任务、计划随之删）、清掉造的连接、公司、单据与推送记录。"""
    _cleanup(lab, manifest, params)
