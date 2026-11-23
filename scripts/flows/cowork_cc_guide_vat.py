"""流程用例：Cowork 版 CC「增值税申报表」（ภ.พ.30）引导真页面从菜单到结果（表单业务 vat，不写账、没有复核步骤）。

造数据同 `cowork_cc_guide_form_lib`，再给 T.8 本月造一份完成的回读（同 CC 桌面版交回的读账结果形状，`readback.contracts.parse`；
存进回读存储、记录置为已完成——实验栈没有真电脑来读）：本公司销项一笔 1000 / 70、进项一笔 400 / 28。

页面上（不调引导接口、不经模型）：
1. 业务菜单「增值税申报表」→ 月份填本月 → 回读那一步自动定（本月已读完）→ 第 3、10 栏不填（跳过）、零税额行照推荐 → 提交；
2. 确定卡（增值税没有复核步骤）点「确定」→ 结果卡：已就绪、合计 = 草稿税额、工作簿在对话里；
库里：引导运行 done、同意来自页面；草稿一份，出草稿用的是第一份回读；之后再造一份完成的回读，按页面同一个取卡接口读结果标
「草稿已过期」（月结台账同一判据；run.py 不支持步骤中间改站点数据，这一段在 verify 里做）。
"""
from __future__ import annotations

import json

import cowork_cc_guide_form_lib as lib

TITLE = "Cowork 版 CC 增值税引导：菜单 → 本月（回读已读完不问）→ 跳过第 3、10 栏 → 确定卡「确定」→ 结果卡已就绪带工作簿；之后又回读一份，取卡时结果标草稿已过期"

# 一份完成的回读（CC 桌面版交回的读账结果形状，同 tests/test_cowork_readback.py 的 raw）：本公司，本月销项、进项各一笔
READBACK = '''
def readback_raw(period, number):
    day = period + "-05"
    return {"version": 1, "period": period, "account_set": "FLOWT8", "read_at": period + "-20T05:00:00Z",
            "company": {"name": "Flow Ledger Co", "tax_id": %(tax)r, "branch": "00000"},
            "calendar": [], "accounts": [], "balances": [], "movements": [], "journal": [], "documents": [],
            "vat": [{"vatrec": "S", "rectyp": "3", "vatprd": period + "-01", "vatdat": day, "docdat": day, "docnum": "IV" + number,
                     "refnum": "", "descrp": "Flow buyer", "prenam": "", "taxid": "0105556012345", "branch": "00000",
                     "docstat": "N", "amt01": "1000.00", "vat01": "70.00", "extra": {}, "deleted": False},
                    {"vatrec": "P", "rectyp": "3", "vatprd": period + "-01", "vatdat": day, "docdat": day, "docnum": "RR" + number,
                     "refnum": "", "descrp": "Flow seller", "prenam": "", "taxid": "0107561000013", "branch": "00000",
                     "docstat": "N", "amt01": "400.00", "vat01": "28.00", "extra": {}, "deleted": False}],
            "diagnostics": {"missing_tables": [], "bad_rows": {}, "unbalanced": [], "orphan_lines": 0}}

def ready_readback(number):
    from uuid import uuid4
    from pearnly_cowork.readback import service as rb, storage as rbs
    name = uuid4().hex
    now = frappe.utils.now_datetime()
    row = frappe.new_doc(rb.READBACK)
    row.update({"readback_id": name, "lookup": rb._lookup(frappe.session.user, conn["name"], "flowt8", %(month)r),
                "actor": frappe.session.user, "connection": conn["name"], "account_set_key": "flowt8", "account_label": "T.8",
                "period": %(month)r, "status": "ready", "request": "", "requested_at": now, "read_at": now, "ready_at": now,
                "data_version": rb.DATA_VERSION, "params_json": "{}", "summary_json": "{}"})
    rb.write(row)
    rbs.put_data(row.name, {"readback": readback_raw(%(month)r, number), "history": {}})
    frappe.db.commit()
    return row.name
''' % {"tax": lib.ledger.MY_TAX, "month": lib.MONTH}


def prepare(lab, manifest):
    return lib.prepare(lab, manifest, READBACK + 'extra = {"readback": ready_readback("1")}', "vat")


def steps(params, camera_code=None):
    return [
        lib.open_conversation(params),
        lib.open_business("vat"),
        lib.answer({"period": lib.MONTH}),
        lib.confirm(),
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    keys = [page.get("key") for page in answered.get("pages") or ()]
    return [
        {"case": "业务菜单有增值税，点了出引导卡，第一题是月份", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "vat" and (opened.get("keys") or [None])[0] == "period"},
        {"case": "本月回读已读完：回读那一步不问；第 3、10 栏可跳过；答完到确定卡", "seen": answered,
         "ok": "readback" not in keys and answered.get("stage") == "ready"},
        {"case": "确定卡主按钮「确定」→ 结果卡已就绪、带工作簿", "seen": done,
         "ok": done.get("label") == "确定" and bool(done.get("enabled")) and done.get("status") == "done"
         and done.get("result") == "ready" and bool(done.get("files"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": "vat"}},
                      fields=["status", "consent"], limit_page_length=3)
drafts = frappe.get_all("Cowork Tax Draft", filters={{"connection": {params['connection']!r}, "period": {lib.MONTH!r}}},
                        fields=["name", "source_ref", "tax_total"], limit_page_length=5)
# 之后这本账套这个月又完成一份回读（电脑又读了一次）：按页面同一个取卡接口读结果（结果每次取卡时现算），应标草稿已过期
from pearnly_core.assistant.guide import run
frappe.set_user({lib.USER!r})
conn = {{"name": {params['connection']!r}}}
""" + READBACK + f"""
names = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": "vat"}}, pluck="name", limit_page_length=1)
first = (run.view_of(names[0]).get("result") or {{}}) if names else {{}}
ready_readback("2")
later = (run.view_of(names[0]).get("result") or {{}}) if names else {{}}
print(json.dumps({{"runs": runs, "drafts": drafts, "first": {{"status": first.get("status"), "marks": first.get("marks")}},
                  "later": {{"status": later.get("status"), "marks": later.get("marks"), "files": later.get("files")}}}}, default=str))
""")
    runs, drafts = out.get("runs") or [], out.get("drafts") or []
    return [
        {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
         "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")},
        {"case": "草稿一份，出草稿用的是第一份回读", "seen": {"drafts": drafts, "readback": params.get("readback")},
         "ok": len(drafts) == 1 and drafts[0].get("source_ref") == params.get("readback")},
        {"case": "结果现算：之后又完成一份回读，页面取卡时结果标草稿已过期（tax_draft_stale），工作簿仍在；之前没有这一标",
         "seen": {"first": out.get("first"), "later": out.get("later")},
         "ok": (out.get("later") or {}).get("status") == "ready" and {"code": "tax_draft_stale"} in ((out.get("later") or {}).get("marks") or [])
         and {"code": "tax_draft_stale"} not in ((out.get("first") or {}).get("marks") or []) and bool((out.get("later") or {}).get("files"))},
    ]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params, f"""
for name in frappe.get_all("Cowork Tax Draft", filters={{"connection": {params.get('connection') or 'none'!r}}}, pluck="name", limit_page_length=20):
    frappe.delete_doc("Cowork Tax Draft", name, ignore_permissions=True, force=True)
""")
