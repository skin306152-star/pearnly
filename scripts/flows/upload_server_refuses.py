"""流程用例：页面拒收之外，服务端自己仍守住上传的上限与类型，被拒的上传不落盘、不留东西（ADR-028 与 11-12 输入框拒收的接缝）。

11-12 起 CC 页面在选文件那一刻就拒收超限 / 空文件 / 不收的类型（`cc.send-unblocked`），但服务端那条路仍有人走：CC 桌面版文件夹、
LINE、直接调接口都不经页面。上传又改成边收边写盘（ADR-028：网页入口把 multipart 原始字节写进临时文件、各接口按块拷到磁盘）。
这里绕过页面，直接按 multipart 打接口（同 CC 桌面版、脚本的做法），逐项核对：

1. CC 附件接口（`pearnly_core.assistant.api.upload`）：
   - 请求体超过 Frappe 上限（文件比 40MB 大 2MB）：413，站点没收这份；
   - 文件比 40MB 大一点、请求体还在 Frappe 上限内：服务端自己的上限照样拒收，回「文件超过 40 MB」；
   - 空文件、不收的类型（.bmp）：服务端回各自的原因；
   - 之后这段对话的留存目录里一份也没有，也没有因此去引擎开远端会话（`remote_ids` 仍是空的）。
2. Cowork 录入接口（`pearnly_cowork.files.api.upload`，只在 Cowork 跑）：超限 413；不收的类型（.bmp）在文件已经按块落到临时文件之后
   才被原件检查拒收——响应发出后那份临时文件（`pearnly-upload-*`）必须已经删掉；没有建出原件记录。
3. 两边：本流程开始后建的 `pearnly-upload-*` 临时文件，响应发完后一个不剩（站点进程的临时目录）。

只调接口、不点界面：拒收提示长什么样由 `cc.send-unblocked` 在界面上点着测。跑完删掉这段会话。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS

TITLE = "服务端自己守住上传上限与类型：直接打接口超限 413、超 40MB 一点也拒收、空文件与不收的类型拒收；被拒的不落盘、不开远端会话、临时文件删掉"
READY = "document.readyState === 'complete' && !!window.csrf_token"

# 直接按 multipart 打一个接口（不经页面的选文件），回 HTTP 状态与站点给人看的那句。size：字节数；-1 = 比 40MB 大 2MB（请求体也超）、
# -2 = 比 40MB 大 256KB（请求体还在 Frappe 上限内）
POST = ("const MB=1024*1024, MAX=40*MB; "
        "const post=async(method,fields,name,size,type)=>{const n=size===-1?MAX+2*MB:size===-2?MAX+256*1024:size; "
        "const form=new FormData(); for(const [k,v] of Object.entries(fields)) form.append(k,v); "
        "form.append('file',new File([new Uint8Array(n)],name,{type})); "
        "const r=await fetch('/api/method/'+method,{method:'POST',headers:{'X-Frappe-CSRF-Token':window.csrf_token||''},body:form}); "
        "let said=''; try{const j=await r.json(); said=j._server_messages?JSON.parse(JSON.parse(j._server_messages)[0]).message:(j.exception||j.message||'');}catch(e){} "
        "return {status:r.status, said:String(said||'').replace(/<[^>]+>/g,'').trim(), bytes:n};};")


def prepare(lab, manifest):
    site = manifest["site"]
    product = "cowork" if "cowork" in site else "erp"
    lab.python(site, f'frappe.db.set_value("User", "admin@{product}.regression", "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    start = lab.python(site, 'print(json.dumps({"now": str(frappe.utils.now_datetime())}))')["now"]
    return {"tag": "srv" + uuid.uuid4().hex[:6], "product": product, "start": start}


def steps(params, camera_code=None):
    cc = "pearnly_core.assistant.api.upload"
    out = [
        {"name": "CC 附件直接打接口", "js": JS + POST +
         "const created=await call('pearnly_core.assistant.api.create',{},true)||{}; const sid=created.id||created.session_id||created.name||''; "
         f"await call('pearnly_core.assistant.api.rename',{{session_id:sid,title:{json.dumps(params['tag'])}}},true); "
         f"const over=await post({json.dumps(cc)},{{session_id:sid}},'big.pdf',-1,'application/pdf'); "
         f"const near=await post({json.dumps(cc)},{{session_id:sid}},'near.pdf',-2,'application/pdf'); "
         f"const empty=await post({json.dumps(cc)},{{session_id:sid}},'empty.pdf',0,'application/pdf'); "
         f"const bmp=await post({json.dumps(cc)},{{session_id:sid}},'photo.bmp',2*MB,'image/bmp'); "
         "const files=((await call('pearnly_core.assistant.api.session_files',{session_id:sid}))||{}); "
         "return {sid, over, near, empty, bmp, files};"},
    ]
    if params["product"] == "cowork":
        intake = "pearnly_cowork.files.api.upload"
        out.append({"name": "Cowork 录入直接打接口", "js": JS + POST +
                    f"const over=await post({json.dumps(intake)},{{request_key:{json.dumps(params['tag'] + '-over')}}},'big.pdf',-1,'application/pdf'); "
                    f"const bmp=await post({json.dumps(intake)},{{request_key:{json.dumps(params['tag'] + '-bmp')}}},'photo.bmp',2*MB,'image/bmp'); "
                    "return {over, bmp};"})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    cc = by.get("CC 附件直接打接口") or {}
    over, near, empty, bmp = (cc.get(k) or {} for k in ("over", "near", "empty", "bmp"))
    refused = lambda case: case.get("status") in (400, 403, 417) and bool(case.get("said"))  # noqa: E731 —— frappe.throw 的拒收
    cases = [
        {"case": "CC 附件：请求体超过 Frappe 上限直接 413", "seen": over, "ok": bool(cc.get("sid")) and over.get("status") == 413},
        {"case": "CC 附件：比 40MB 大一点（请求体没超）由服务端自己的上限拒收，回「文件超过 40 MB」", "seen": near,
         "ok": refused(near) and "40 MB" in near.get("said", "")},
        # 原因按请求语言回词条（实验账号是中文；同页面选文件时拒收的那两句，`upload.fileEmpty` / `upload.typeUnsupported`）
        {"case": "CC 附件：空文件、不收的类型由服务端拒收，按请求语言说原因（文件为空 / 不支持此文件类型）", "seen": {"empty": empty, "bmp": bmp},
         "ok": refused(empty) and refused(bmp) and empty.get("said") == "文件为空" and bmp.get("said") == "不支持此文件类型"},
    ]
    if params["product"] == "cowork":
        intake = by.get("Cowork 录入直接打接口") or {}
        cases += [
            {"case": "Cowork 录入：超限 413；不收的类型（文件已落到临时文件后）由原件检查拒收", "seen": intake,
             "ok": (intake.get("over") or {}).get("status") == 413 and refused(intake.get("bmp") or {})},
        ]
    return cases


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
import glob, os, tempfile, time
from datetime import datetime
from pearnly_core.assistant import files as cc_files
start = datetime.fromisoformat({params['start']!r}).timestamp() - 5
rows = frappe.db.sql("select name, remote_ids from `tabPearnly Assistant Session` where creation >= %s and title = %s",
                     ({params['start']!r}, {params['tag']!r}), as_dict=True)
kept = {{}}
for row in rows:
    folder = cc_files._folder(row.name)
    kept[row.name] = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
# 站点进程的临时目录（网页进程与这里同一个容器、同一个 TMPDIR）。本流程开始后、此刻以前建的 pearnly-upload-*：别的流程同时在传的
# 一份几秒内就随响应删掉，漏删的会一直在，所以隔 15 秒看两次，两次都在的才算留下
now = time.time()
seen = lambda: {{p for p in glob.glob(os.path.join(tempfile.gettempdir(), "pearnly-upload-*")) if start <= os.path.getmtime(p) <= now}}
first = seen()
time.sleep(15 if first else 0)
left = sorted(first & seen())
sources = frappe.db.count("Cowork Source File", {{"creation": [">=", {params['start']!r}], "filename": ["in", ["photo.bmp", "big.pdf"]]}}) \
    if frappe.db.exists("DocType", "Cowork Source File") else 0
for row in rows:
    frappe.delete_doc("Pearnly Assistant Session", row.name, ignore_permissions=True, force=True)
frappe.db.commit()
print(json.dumps({{"sessions": [[r.name, r.remote_ids or ""] for r in rows], "kept": kept, "left": left, "tmp": tempfile.gettempdir(),
                  "sources": sources}}))
""")
    sessions = out.get("sessions") or []
    return [
        {"case": "被拒的 CC 附件一份也没留存，没有因此去引擎开远端会话（remote_ids 仍是空的）", "seen": out,
         "ok": len(sessions) == 1 and all(not kept for kept in (out.get("kept") or {}).values())
         and all(remote in ("", "[]") for _name, remote in sessions)},
        {"case": "本流程开始后建的 pearnly-upload-* 临时文件在响应发完后一个不剩；Cowork 没有建出原件记录", "seen": out,
         "ok": out.get("left") == [] and not out.get("sources")},
    ]
