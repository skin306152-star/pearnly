"""流程用例：公司记住的分组在直写里真的落到单据上、推给 Express 的也是它（11-19 回滚审计 P2，现网管钱缺陷）。

缺陷：写账前置判定（`write_precheck`）先按这家公司记住的分组改单据（`operator_source._settle` 写方向 / 入账类型，没提交），
紧接着判卡片的 `precheck_source.document`「只是看」要回滚，把这笔改动冲掉：卡片按记住的分组判，单据还是原来的分组，直写推送照
单据推。代录交单前会重落一次分组，不受影响；直写不会。

做法（造数据同 `cc_conversation_ledger.py`：一条 Express 连接两本同期账套、从目录建公司、录入人一台登记过的电脑、一张识别好的
进项票，付款与科目两本都记住了）。另给这家公司记住：这个对方的票归「销售」（``group`` = ``sales``，资料区拖过一次的样子），而票自己
按识别是进项；销项要定的付款与科目也记住，判定没有要问的。对话里走直写真路：新建页选公司、选 T.8 说第一句 → 对话里判这张怎么录（``pearnly_precheck``，开始前卡片同一套；
`_settle` 在这里改单据，紧接着判卡片）。实验室没有电脑读销项主档，计划会停在「等电脑」，所以不走到真推送：推送用的方向按推送
定方向的同一句（`push.application.service._express_context` 里的 ``resolve_account_direction``）在库里现算。
断言：卡片说这张是销项；库里单据的主页面方向已是销项；推送用的方向也是销项（三者一致；修之前单据仍是进项、推送照进项推）。cleanup 删会话与造的数据。不连 Express、不调模型。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cc_conversation_ledger as base  # noqa: E402
from cc_lib import JS  # noqa: E402

TITLE = "公司记住的分组（销售）在直写里落到单据上、推给 Express 的方向也按它（P2）"
USER = base.USER
SELLER = "party|0107561000013|0"


def prepare(lab, manifest):
    site = manifest["site"]
    lab.python(site, f'frappe.db.set_value("User", {USER!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    ref = "FLOW-RG-" + uuid.uuid4().hex[:6].upper()
    out = lab.python(site, base._mine(base.CLEANUP) + base.EXTRA_CLEANUP + base.SEED + base.LEDGER_IDS + f"""
from pearnly_cowork.companies import rules
name = recognized({ref!r})
rules.save(company["id"], workspace=ws, kind=rules.GROUP, subject={SELLER!r}, value="sales", subject_label="Flow seller")
# 归到销售后对方是买方：销项要定的付款与科目也记住（判定没有要问的，计划才 ready）
for source in ("FLOWT8", "FLOWT9"):
    application.save_choices(name, conn["name"], source, json.dumps([
        {{"kind": "payment", "subject": "sales|{base.MY_TAX}", "value": "credit", "label": "Flow Ledger Co"}},
        {{"kind": "account", "subject": "sales|{base.MY_TAX}", "value": "S", "label": "Flow Ledger Co"}}]))
frappe.db.commit()
from pearnly_core.express_rules.contracts.history import primary_page_fields
before = primary_page_fields(json.loads(frappe.db.get_value("Cowork OCR History", name, "pages") or "[]"))
print(json.dumps({{"company": company["id"], "title": company["title"], "doc": name, "connection": conn["name"],
                  "t8": ids.get({base.T8!r}, ""), "before": {{k: before.get(k) for k in ("direction", "posting_kind")}}}}))
""")
    tag = "rg" + uuid.uuid4().hex[:6]
    check = {"name": "pearnly_precheck", "input": {"company": out["company"], "doc_ids": [out["doc"]], "mode": "direct"}}
    return {**out, "ref": ref, "tag": tag, "cases": [],
            "open_line": f"{tag} 这家这个月的票",
            "check_line": f"{tag} 看看这张怎么录 [lab-tool]{json.dumps(check)}[/lab-tool]"}


def steps(params, camera_code=None):
    sid = "new URLSearchParams(location.search).get('c')||''"
    return [
        {"name": "新建页选 T.8 后开对话", "js": JS + base.RESULT + "await toMode('chat'); await until(()=>q('[data-slot=\"cc-chin-company\"]')); "
         f"const picked=await pickCompany({json.dumps(params['title'])}); " + base.new_page_ledger("T.8") +
         f"const sent=await say({json.dumps(params['open_line'])}); "
         f"const replied=await until(()=>replies().some(e=>e.innerText.includes('[lab-agent]')&&e.innerText.includes({json.dumps(params['tag'])})),240); "
         "await until(()=>!q('[data-slot=\"cc-run-indicator\"]'),80); "
         f"await until(()=>!!({sid}),80); return {{picked, chip_after, sent, replied, session: {sid}}};"},
        # 对话里判这张怎么录（写账前置判定 `pearnly_precheck`，开始前卡片同一套）：这一步里按公司记住的分组改单据，再判卡片
        {"name": "判这张怎么录", "js": JS + base.RESULT + f"const turn=await toolTurn({json.dumps(params['check_line'])},'pearnly_precheck'); "
         "const r=turn.result||{}; "
         f"return {{sent: turn.sent, direction: (r.directions||{{}})[{json.dumps(params['doc'])}]||'', "
         "waiting: (r.waiting||[]).map(w=>w.doc_id), raw: r.unparsed||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    card = by.get("判这张怎么录") or {}
    return [
        {"case": "对话里判这张：卡片按公司记住的分组说它是销项", "seen": card,
         "ok": card.get("direction") == "sales"},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from pearnly_core.express_rules.contracts.history import primary_page_fields
from pearnly_cowork.history import storage
from pearnly_cowork.push.application import service
frappe.set_user({USER!r})
fields = primary_page_fields(json.loads(frappe.db.get_value("Cowork OCR History", {params['doc']!r}, "pages") or "[]"))
doc = storage.read({params['doc']!r})
# 推送定方向的同一句（`service._express_context`：单据投影 → 方向，本方税号与账套税号同推送时）
history_value = storage.project(doc)
own = frappe.db.get_value("Cowork Workspace", doc.workspace, "tax_id") or ""
pushed = service.resolve_account_direction(service.flatten_history_for_mrerp(history_value), history_value,
                                           own_tax_id=own, account_tax_id={base.MY_TAX!r})
print(json.dumps({{"direction": fields.get("direction"), "push_direction": pushed}}, default=str))
""")
    return [
        {"case": "库里：单据已按公司记住的分组改成销项（推送照单据推），不再是识别时的进项", "saved": {"before": params["before"], "now": got},
         "ok": params["before"].get("direction") == "purchase" and got.get("direction") == "sales"},
        {"case": "推送给 Express 用的方向（推送定方向的同一句 resolve_account_direction）是销项，与卡片说的一致", "saved": got,
         "ok": got.get("push_direction") == "sales"},
    ]


def cleanup(lab, manifest, params):
    base.cleanup(lab, manifest, params)
