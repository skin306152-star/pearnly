"""流程用例：代录会话「选中就上传、发送后才识别」（11-14 用户：「选中就上传当初是我问为什么上传很慢，要转圈半天才能发送，
而不是上传就识别」「难道上传还未发送就开始识别了吗」）。

每一步都在页面上点（选文件走输入框的「添加文件」），ERP 与 Cowork、电脑与手机宽度各跑一遍：

1. 一次选两份：都立刻传上；输入框里的附件只有上传状态（传完没有「排队中 / 识别中」）；这时服务端任务里没有一份算上传
   （``task_uploads`` 是空的），识别队列里也没有新活（不识别、不扣费）。
2. 移除第一份：服务端那一行「待发送」与本站留的那份一起删掉。
3. 上传接口一时不通时选第三份：输入框留失败卡（卡上看得出是哪份），任务上不多记；点 × 移除。
4. 打一句发送：带着的第二份这时才进对话（上传卡片），后台交给产品识别——任务里这一份有了识别任务号（或产品不收的原因），
   识别队列里多出的活只有这一份。被移除的那份从头到尾没交识别。

模型调用换成实验室的假智能体服务（`lab_agents.py`），识别交给实验室里的产品原样排队（不等识别出结果）。verify 删掉会话连任务。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cowork_operator_lib import BASE, CLEANUP

TITLE = "CC 代录会话选中就上传、发送后才识别：没发出时不识别不扣费，移除的不识别，上传失败留失败卡，发出后才交识别"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea.aui-composer-input')"
OCR_WORK = "Pearnly OCR Work"

# 一次选几份文件（同点「+」）：[文件名, 内容]
PICK = ("const pick=async specs=>{const files=specs.map(([name,body,type])=>new File([body],name,{type})); "
        "const orig=HTMLInputElement.prototype.click; HTMLInputElement.prototype.click=function(){ if(this.type==='file'){ const dt=new DataTransfer(); "
        "for(const f of files) dt.items.add(f); this.files=dt.files; this.dispatchEvent(new Event('change',{bubbles:true})); return; } return orig.call(this); }; "
        "qa('button.cc-attach').find(seen)?.click(); HTMLInputElement.prototype.click=orig; await sleep(600); return files.length;};")
# 输入框此刻的附件：方块的读屏名字（含「上传中」）、方块上的文字、失败卡；任务里算上传的那几份
TILES = ("const tiles=()=>({count: qa('.aui-attachment-root').length, uploading: qa('.aui-attachment-tile-uploading').length, "
         "failed: qa('[data-slot=\"attachment\"][data-state=\"error\"]').length, "
         "labels: qa('.aui-attachment-tile').map(e=>e.getAttribute('aria-label')||''), "
         "text: qa('.aui-attachment-root').map(e=>e.innerText.trim()).join(' | ')});")
# 一张 1×1 的 PNG（识别交得出去；读出什么不重要，不等识别结果）
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
FILE = "Uint8Array.from(atob('" + PNG + "'),c=>c.charCodeAt(0))"


def _admin(site):
    return "admin@cowork.regression" if site.startswith("cowork") else "admin@erp.regression"


def prepare(lab, manifest):
    site = manifest["site"]
    admin = _admin(site)
    lab.python(site, f'frappe.db.set_value("User", {admin!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "uos" + uuid.uuid4().hex[:6]
    if site.startswith("cowork"):
        # Cowork：建一家接了 Express 连接、选好账套的公司（产品自己的函数，`cowork_operator_lib`），识别才交得出去
        head = CLEANUP + BASE + "project = company['id']\n"
    else:
        head = (f"frappe.set_user({admin!r})\nproject = {(manifest.get('company') or manifest['companies'][0])!r}\n"
                "period = frappe.utils.add_months(frappe.utils.today(), -1)[:7]\n")
    out = lab.python(site, head + f"""
from pearnly_core.assistant import sessions
frappe.set_user({admin!r})
made = sessions.create(project=project, period=period, mode="operator")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r})
frappe.db.commit()
print(json.dumps({{"session": made["id"], "task": made["task"], "project": project, "now": str(frappe.utils.now_datetime()),
                  "row": frappe.db.get_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "name"),
                  "work": frappe.db.count({OCR_WORK!r})}}))
""")
    return {**out, "tag": tag, "admin": admin, "cases": []}


def steps(params, camera_code=None):
    task, tag = json.dumps(params["task"]), json.dumps(params["tag"])
    head = JS + PICK + TILES + f"const listedUploads=async()=>((await call('pearnly_core.express_operator.api.task_uploads',{{name:{task}}}))||{{}}).uploads||[];"
    return [
        {"name": "选两份", "shot": True, "file": "1-two-uploaded", "js": head +
         "await until(()=>q('textarea.aui-composer-input'),120); "
         f"await pick([['{params['tag']}-a.png',{FILE},'image/png'],['{params['tag']}-b.png',{FILE},'image/png']]); "
         "const shown=await until(()=>tiles().count===2,80); "
         "const done=await until(()=>tiles().count===2&&tiles().uploading===0&&tiles().failed===0,240); "
         "const send=await until(()=>{const b=q('button.aui-composer-send'); return b&&!b.disabled;},80); "
         # 传完再等几秒：以前这时已开始轮询识别、方块上出现「排队中」
         "await sleep(4000); "
         "return {shown, done, send, tiles: tiles(), uploads: await listedUploads()};"},
        {"name": "移除第一份", "js": head +
         "const x=qa('.aui-attachment-tile-remove').find(seen); if(!x) return {error:'no remove button'}; x.click(); "
         "const removed=await until(()=>tiles().count===1,40); await sleep(1500); "
         "return {removed, tiles: tiles(), uploads: await listedUploads()};"},
        {"name": "传不上的一份", "shot": True, "file": "2-upload-failed", "js": head +
         "const real=window.fetch; window.fetch=(u,o)=>String(u&&u.url||u).includes('assistant.api.upload')?Promise.resolve(new Response('',{status:503})):real(u,o); "
         f"await pick([['{params['tag']}-c.png',{FILE},'image/png']]); "
         "const failed=await until(()=>tiles().failed===1,120); "
         "const card=q('[data-slot=\"attachment\"][data-state=\"error\"]')?.innerText.trim()||''; window.fetch=real; "
         "const x=q('[data-slot=\"attachment\"][data-state=\"error\"] .aui-attachment-tile-remove'); x?.click(); "
         "const removed=await until(()=>tiles().failed===0&&tiles().count===1,40); "
         "return {failed, card, removed, tiles: tiles(), uploads: await listedUploads()};"},
        {"name": "发出", "shot": True, "file": "3-sent", "js": head +
         f"const said=await say({tag}+' 看看这张'); if(!said) return {{error:'could not send'}}; "
         f"const bubble=await until(()=>qa('[data-role=\"user\"]').some(e=>e.innerText.includes({tag})),80); "
         "const cleared=await until(()=>tiles().count===0,40); "
         # 发出后才交识别（后台）：任务里这一份有了识别任务号，或产品不收的原因（失败）
         "let got=[]; "
         "for(let k=0;k<120;k++){ got=await listedUploads(); if(got.length&&got.every(r=>r.batch||r.state==='failed')) break; await sleep(500); } "
         "await openSteps('cc-op-upload'); const card=await until(()=>q('[data-slot=\"cc-op-upload\"]'),80); "
         "return {said, bubble, cleared, rows: got, card, cardText: q('[data-slot=\"cc-op-upload\"]')?.innerText.trim()||''};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    two, removed, failed, sent = (by.get(k) or {} for k in ("选两份", "移除第一份", "传不上的一份", "发出"))
    words = ("排队中", "识别中", "已识别")
    tile_words = " ".join([(two.get("tiles") or {}).get("text", "")] + list((two.get("tiles") or {}).get("labels") or []))
    rows = sent.get("rows") or []
    return [
        {"case": "two picked files upload at once and the send button is ready", "seen": {k: two.get(k) for k in ("shown", "done", "send")},
         "ok": two.get("shown") is True and two.get("done") is True and two.get("send") is True},
        {"case": "a composer file shows only its upload, never queued or recognizing", "seen": tile_words,
         "ok": not any(word in tile_words for word in words)},
        {"case": "before sending the task has no upload yet", "seen": two.get("uploads"), "ok": two.get("uploads") == []},
        {"case": "removing a file leaves one in the composer and none in the task", "seen": removed,
         "ok": removed.get("removed") is True and removed.get("uploads") == []},
        {"case": "an upload that cannot get through leaves a failed card that can be removed, nothing recorded", "seen": failed,
         "ok": failed.get("failed") is True and params["tag"] + "-c.png" in (failed.get("card") or "")
         and failed.get("removed") is True and failed.get("uploads") == []},
        {"case": "after sending only the sent file joins the conversation and is handed to recognition", "seen": {
            "rows": [{k: r.get(k) for k in ("filename", "batch", "state", "code")} for r in rows], "card": sent.get("cardText")},
         "ok": sent.get("bubble") is True and sent.get("cleared") is True and len(rows) == 1
         and rows[0].get("filename") == params["tag"] + "-b.png" and bool(rows[0].get("batch") or rows[0].get("state") == "failed")
         and sent.get("card") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    site = manifest["site"]
    got = lab.python(site, f"""
import os
from pearnly_core.assistant import files
task = frappe.get_doc("Pearnly Express Operator Task", {params['task']!r})
rows = [{{"filename": r.filename, "batch": r.batch or "", "code": r.start_code or "", "sent": bool(r.uploaded_at)}} for r in task.uploads]
batches = [r.batch for r in task.uploads if r.batch]
folder = files._folder({params['row']!r})
kept = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
work = frappe.get_all({OCR_WORK!r}, filters={{"creation": [">=", {params['now']!r}]}}, fields=["reference"], limit_page_length=50)
frappe.set_user({params['admin']!r})
from pearnly_core.assistant import sessions
sessions.delete({params['session']!r})
frappe.db.commit()
print(json.dumps({{"rows": rows, "kept": kept, "work": [w.reference for w in work], "batches": batches,
                  "left": frappe.db.exists("Pearnly Express Operator Task", {params['task']!r})}}))
""" + (CLEANUP + "cleanup()\nfrappe.db.commit()\n" if site.startswith("cowork") else ""))
    tag = params["tag"]
    rows, kept = got["rows"], got["kept"]
    return [
        {"case": "the task holds only the sent file, handed to recognition after sending", "saved": rows,
         "ok": len(rows) == 1 and rows[0]["filename"] == f"{tag}-b.png" and rows[0]["sent"] and bool(rows[0]["batch"] or rows[0]["code"])},
        {"case": "the removed file's kept copy is gone, the sent one stays until the chat is deleted", "saved": kept,
         "ok": not any(name.endswith(f"--{tag}-a.png") for name in kept) and any(name.endswith(f"--{tag}-b.png") for name in kept)},
        {"case": "the only new recognition work is the sent file's (the removed one was never recognized)",
         "saved": {"work": got["work"], "batches": got["batches"]},
         "ok": all(ref in got["batches"] for ref in got["work"]) and len(got["work"]) <= 1},
        {"case": "deleting the chat deletes its task", "saved": got["left"], "ok": not got["left"]},
    ]
