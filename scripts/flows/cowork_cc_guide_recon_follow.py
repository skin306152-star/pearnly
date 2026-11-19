"""流程用例：Cowork 版 CC 对账引导（表单业务 recon）办完后，结果卡上的追问作答（引导卡页面接口约定 6 ``guide_follow``，N1b；
docs/work/2026-11-19-cc-guide-page-endpoints.md）。现在只有对账引导声明了 ``follow``（`assistant.guided.recon`）。

数据照 `cowork_recon_run` 收入对账那一套（表格直读，不调付费识别）：左总账一笔销售 1000 对上税报的未税额、一笔利息 4201 税报里
没有 → 结果里问一次「这类收入要不要排除」（预设规则 excluded，范围 本公司税号 · 4201）。税号每次随机：答过一次的预设以后不再问。

页面上（管理员，假智能体只占着对话框架、不说话）：两份 xlsx 用对话页同一个附件接口传进这段直写对话 → ``guide_start(recon)`` →
卡上答种类（收入）、来源（交文件）、总账、税报两侧文件 → ``guide_confirm`` → 等后台对账完、结果卡带追问：

1. ``guide_follow`` 答一道结果里没有的题：回 ``refused``（``not offered``），版本不变。
2. ``guide_follow`` 答结果里的第一题（第一个选项）：收下、版本 +1；结果重算后这一题不再出现；库里记下这条预设。

cleanup 删会话（连引导运行与对话里的结果工作簿）、本次的收入对账任务与答出来的预设。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "Cowork 版 CC 对账引导：卡上交两侧文件确定 → 结果卡带追问 → guide_follow 答错题被拒、答对后追问消失并记成预设"
USER = "admin@cowork.regression"
API = "pearnly_core.assistant.api."
DOCTYPE = "Cowork Income Reconciliation"


def prepare(lab, manifest):
    tag = "rf" + uuid.uuid4().hex[:6]
    tax = "1" + f"{uuid.uuid4().int % 10 ** 12:012d}"   # 13 位、首位不是 0 的编造税号
    out = lab.python(manifest["site"], f"""
import base64, io
import openpyxl
from pearnly_core.assistant import sessions

def book(sheets):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(list(row))
    out = io.BytesIO(); wb.save(out)
    return base64.b64encode(out.getvalue()).decode()

tag, tax = {tag!r}, {tax!r}
head = [["รายงานภาษีขาย"], ["ชื่อผู้ประกอบการ บริษัท โฟลว์ จำกัด"], [f"เลขประจำตัวผู้เสียภาษี {{tax}} สำนักงานใหญ่"], []]
table = ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี", "ยอดรวม"]
sale = ["01/06/2569", f"INV-{{tag}}", f"ACME {{tag}}", "1234567890123", "00000", 1000.0, 70.0, 1070.0]
# 同 `cowork_recon_run` 的收入对账：总账多一笔利息 4201 税报里没有 → 结果里问一次
ledger = (f"GL income {{tag}}.xlsx", book([("GL", [["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
                                                  ["01/06/2569", f"INV-{{tag}}", "sale", 0, 1000.0, "4110-01"],
                                                  ["15/06/2569", f"GL-9-{{tag}}", "interest", 0, 50.0, "4201"]])]))
sales_vat = (f"VAT income {{tag}}.xlsx", book([("VAT", head + [table, sale])]))
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
made = sessions.create(period=frappe.utils.today()[:7])
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", tag)
frappe.db.commit()
print(json.dumps({{"session": made["id"], "ledger": list(ledger), "sales_vat": list(sales_vat),
                  "ocr": frappe.db.count("Pearnly OCR Work") if frappe.db.table_exists("Pearnly OCR Work") else 0}}))
""")
    return {**out, "tag": tag, "tax": tax, "scope": f"income|{tax}|4201", "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
from pearnly_cowork.recon_center import api, presets
frappe.set_user({USER!r})
if frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params['session']!r}}}):
    sessions.delete({params['session']!r})
names = frappe.get_all({DOCTYPE!r}, filters={{"title": ["like", "%{params['tag']}%"]}}, pluck="name")
if names:
    api.delete("income", names)
# 预设表只许走服务删（`preset_store` 拒绝直接删）：用预设弹窗「删除」同一个函数
for row in frappe.get_all("Cowork Recon Preset", filters={{"scope": ["like", "%{params['tax']}%"]}}, fields=["recon_kind", "scope", "rule"]):
    presets.remove(row.recon_kind, row.scope, row.rule)
frappe.db.commit()
print("{{}}")
""")


def _head(params):
    """每步开头：等页面载入（有 CSRF 令牌）；``view(run)`` 读此刻的卡（同页面 ``guide_view``）；``ready(run)`` 等结果算完（最多约 3 分钟）。"""
    return (JS + "await until(()=>q('textarea.aui-composer-input'),120); "
            f"const SID={json.dumps(params['session'])}; "
            f"const view=async run=>await call('{API}guide_view',{{run_name:run}}); "
            "const ready=async run=>{ let v=null; for(let k=0;k<180;k++){ v=await view(run); const res=(v||{}).result||{}; "
            " if(res.status&&res.status!=='queued'&&!res.wait) return v; await sleep(1000); } return v; }; "
            "const asked=v=>((((v||{}).result||{}).questions)||[]).map(x=>({key:x.key, title:x.title, options:(x.options||[]).map(o=>o.code)})); ")


def steps(params, camera_code=None):
    head = _head(params)
    left, right = params["ledger"], params["sales_vat"]
    return [
        # 两份 xlsx 走对话页同一个附件接口进这段对话；开对账引导，卡上逐题答到可确定
        {"name": "开引导作答", "js": head +
         "const up=async(name,b64)=>{const s=atob(b64); const a=new Uint8Array(s.length); for(let i=0;i<s.length;i++) a[i]=s.charCodeAt(i); "
         " const fd=new FormData(); fd.append('session_id',SID); "
         " fd.append('file', new File([a], name, {type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})); "
         f" const r=await fetch('/api/method/{API}upload',{{method:'POST',headers:{{'X-Frappe-CSRF-Token':window.csrf_token||''}},body:fd}}); "
         " const j=await r.json().catch(()=>({})); return ((j||{}).message||{}).fileId||'';}; "
         f"const fl=await up({json.dumps(left[0])},{json.dumps(left[1])}); const fr=await up({json.dumps(right[0])},{json.dumps(right[1])}); "
         "if(!fl||!fr) return {error:'upload failed', fl, fr}; "
         "const want={kind:'income', source:'income:upload', ledger:[fl], sales_vat:[fr]}; "
         f"let v=await call('{API}guide_start',{{session_id:SID, business:'recon'}},true); "
         "if(!v||!v.run) return {error:'guide_start failed'}; window.__flowRun=v.run; const rounds=[]; "
         "for(let k=0;k<6&&v&&v.status==='asking'&&!((v.card||{}).ready);k++){ const keys=((v.card||{}).questions||[]).map(x=>x.key); "
         " const answers={}; for(const key of keys){ if(!(key in want)) return {error:'unexpected question '+key, keys, rounds}; answers[key]=want[key]; } "
         " rounds.push(keys); "
         f" const r=await call('{API}guide_answer',{{run_name:v.run, answers:JSON.stringify(answers), version:v.version}},true); "
         " if(!r) return {error:'answer failed', rounds}; if(r.outcome) return {error:'answer '+r.outcome, refused:r.refused, rounds}; v=r; } "
         "return {run:v.run, status:v.status, ready:!!((v.card||{}).ready), version:v.version, rounds, uploaded:[fl,fr]};"},
        # 确定：后台对账，结果卡等算完；完成的结果带要定一次的追问
        {"name": "确定等结果", "shot": True, "file": "1-result", "js": head +
         "const run=window.__flowRun; if(!run) return {error:'no guide run'}; const v0=await view(run); "
         f"const r=await call('{API}guide_confirm',{{run_name:run, version:(v0||{{}}).version}},true)||{{}}; "
         "if(r.outcome) return {error:'confirm '+r.outcome, status:r.status}; "
         "const v=await ready(run); const res=(v||{}).result||{}; "
         "return {status:(v||{}).status||'', version:(v||{}).version||0, result_status:res.status||'', "
         "reason:String(res.reason_text||'').slice(0,200), files:(res.files||[]).map(f=>f.name), questions:asked(v)};"},
        # 答一道结果里没有的题：不收
        {"name": "追问答错题", "js": head +
         "const run=window.__flowRun; if(!run) return {error:'no guide run'}; const v=await view(run); "
         f"const r=await call('{API}guide_follow',{{run_name:run, answers:JSON.stringify({{q99:'yes'}}), version:(v||{{}}).version}},true)||{{}}; "
         "return {before:(v||{}).version||0, version:r.version||0, outcome:r.outcome||'', refused:r.refused||{}, questions:asked(r)};"},
        # 答结果里的第一题（第一个选项）：收下、版本 +1，结果按预设重算后这一题不再出现
        {"name": "追问作答", "shot": True, "file": "2-followed", "js": head +
         "const run=window.__flowRun; if(!run) return {error:'no guide run'}; const v=await view(run); const first=asked(v)[0]; "
         "if(!first||!first.options.length) return {error:'no follow up question', questions:asked(v)}; "
         "const answers={[first.key]: first.options[0]}; "
         f"const r=await call('{API}guide_follow',{{run_name:run, answers:JSON.stringify(answers), version:(v||{{}}).version}},true)||{{}}; "
         "const after=await ready(run); "
         "return {before:(v||{}).version||0, version:r.version||0, outcome:r.outcome||'', answered:{...first, value:first.options[0]}, "
         "result_status:(((after||{}).result)||{}).status||'', questions:asked(after)};"},
        {"name": "删除会话", "js": head +
         f"await call('{API}delete',{{session_id:SID}},true); const left=(await listed('chat')).some(s=>s.id===SID); return {{left}};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done, wrong, followed = (by.get(k) or {} for k in ("开引导作答", "确定等结果", "追问答错题", "追问作答"))
    asked = [k for keys in opened.get("rounds") or [] for k in keys]
    wrong_code = (wrong.get("refused") or {}).get("questions")
    wrong_code = wrong_code.get("code") if isinstance(wrong_code, dict) else wrong_code
    answered = followed.get("answered") or {}
    return [
        {"case": "the recon guide asks the kind and both income files, then can be confirmed", "seen": opened,
         "ok": opened.get("status") == "asking" and opened.get("ready") is True
         and all(k in asked for k in ("kind", "ledger", "sales_vat"))},
        {"case": "after confirm the result card is ready with the workbook and a follow up question", "seen": done,
         "ok": done.get("status") == "done" and done.get("result_status") == "ready" and bool(done.get("files"))
         and bool(done.get("questions"))},
        {"case": "guide_follow with a question that is not on the result is refused and nothing moves", "seen": wrong,
         "ok": wrong.get("outcome") == "refused" and wrong_code == "not offered" and wrong.get("version") == wrong.get("before")},
        {"case": "guide_follow with the first question's first option is taken: version +1", "seen": followed,
         "ok": not followed.get("outcome") and followed.get("version") == (followed.get("before") or 0) + 1},
        {"case": "the result is recomputed and the answered question is gone", "seen": followed,
         "ok": followed.get("result_status") == "ready" and bool(answered.get("title"))
         and all(q.get("title") != answered.get("title") for q in followed.get("questions") or [])},
        {"case": "the conversation is deleted", "seen": by.get("删除会话"), "ok": (by.get("删除会话") or {}).get("left") is False},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
presets = frappe.get_all("Cowork Recon Preset", filters={{"scope": ["like", "%{params['tax']}%"]}}, fields=["scope", "rule", "value"])
tasks = frappe.db.count({DOCTYPE!r}, {{"title": ["like", "%{params['tag']}%"]}})
print(json.dumps({{"presets": presets, "tasks": tasks,
                  "ocr": frappe.db.count("Pearnly OCR Work") if frappe.db.table_exists("Pearnly OCR Work") else 0}}, default=str))
""")
    presets = got.get("presets") or []
    return [
        {"case": "the follow up answer is stored as the preset for this company tax id and account 4201", "saved": presets,
         "ok": any(p.get("scope") == params["scope"] and p.get("rule") == "excluded" and p.get("value") for p in presets)},
        {"case": "the guide started one income reconciliation for this run", "saved": got.get("tasks"), "ok": got.get("tasks") == 1},
        {"case": "nothing went to recognition", "saved": got.get("ocr"), "ok": got.get("ocr") == params["ocr"]},
    ]
