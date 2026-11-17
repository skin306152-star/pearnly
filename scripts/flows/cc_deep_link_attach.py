"""流程用例：从链接打开一段对话（``/assistant?c=<会话>``），页面还在切过去时就加附件、发一句——附件和这句都得进链接里那段对话。

11-17 查到（流程 cowork.cc.operator.upload-on-send 本机全套首轮三次挂、补跑才过）：页面打开 ``?c=`` 时先给「新对话」的输入框，
要等侧栏第一页、没列到再查一次 ``get``，才切到那段对话（``frontend/assistant/src/lib/nav.ts`` 的 openConversation）。
这段时间输入框能用：加附件会先新建一段会话、文件传进新会话，切过去后附件从眼前消失。正常加载只有一两秒，
手机网络、LINE 内置浏览器、首次加载更长；冷站点上流程正好撞上。

这里不靠冷站点碰运气：页面脚本跑之前注入一段（``PRELOAD``），把开页后 15 秒内的侧栏列表（``assistant.api.listing``）与
取单段会话（``assistant.api.get``）各拖慢 5 秒。输入框一出现就点「+」加一张图（加不上——例如产品做成切过去之前不可用——
每 0.5 秒再试，最多 30 秒，同人会再点一次），等它传完、页面切过去，再发一句。

判据：链接那段对话在服务端的附件目录里有这张图；整格没有多出别的会话；切过去以后附件还在输入框里，
这句出现在对话里，地址仍指向链接那段对话。
"""
from __future__ import annotations

import json
import uuid

from cc_lib import JS
from cc_operator_upload_on_send import FILE, PICK, TILES
from cowork_operator_lib import BASE, CLEANUP

TITLE = "CC 从链接打开对话、切过去之前就加附件和发送：附件与消息都进链接那段对话，不另建会话"
DELAY_MS = 5000
WINDOW_MS = 15000
PRELOAD = (
    "(()=>{const f=window.fetch, t0=Date.now(); window.__slowed=[];"
    "window.fetch=function(u,...a){const s=String((u&&u.url)||u);"
    "if(Date.now()-t0<" + str(WINDOW_MS) + "&&/pearnly_core\\.assistant\\.api\\.(listing|get)(\\?|$)/.test(s)){"
    "window.__slowed.push(s.split('?')[0].split('.').pop());"
    "return new Promise(r=>setTimeout(r," + str(DELAY_MS) + ")).then(()=>f.call(this,u,...a));}"
    "return f.call(this,u,...a);};})()"
)
# 同人看到的：输入框出现就能动手（不等侧栏、不等对话切过去——要测的正是这段时间）
READY = "document.readyState === 'complete' && !!document.querySelector('textarea.aui-composer-input')"


def _admin(site):
    return "admin@cowork.regression" if site.startswith("cowork") else "admin@erp.regression"


def prepare(lab, manifest):
    site = manifest["site"]
    admin = _admin(site)
    lab.python(site, f'frappe.db.set_value("User", {admin!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "dla" + uuid.uuid4().hex[:6]
    if site.startswith("cowork"):
        head = CLEANUP + BASE + "project = company['id']\n"
    else:
        head = (f"frappe.set_user({admin!r})\nproject = {(manifest.get('company') or manifest['companies'][0])!r}\n"
                "period = frappe.utils.today()[:7]\n")
    out = lab.python(site, head + f"""
from pearnly_core.assistant import sessions
frappe.set_user({admin!r})
now = str(frappe.utils.now_datetime())
made = sessions.create(project=project, period=period)
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r})
frappe.db.commit()
print(json.dumps({{"session": made["id"], "now": now,
                  "row": frappe.db.get_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "name")}}))
""")
    return {**out, "tag": tag, "admin": admin, "cases": []}


def steps(params, camera_code=None):
    tag, session = json.dumps(params["tag"]), json.dumps(params["session"])
    head = JS + PICK + TILES
    return [
        {"name": "马上加附件", "shot": True, "file": "1-attached-while-opening", "js": head +
         "const at=Date.now(); let tries=0; "
         # 加不上（输入框还不可用）就过 0.5 秒再点一次，最多 30 秒
         f"while(tiles().count===0&&Date.now()-at<30000){{ tries++; await pick([[{tag}+'.png',{FILE},'image/png']]); "
         "if(tiles().count>0) break; await sleep(500); } "
         "const shown=tiles().count>0; const pickedAt=Date.now()-at; "
         "const done=await until(()=>tiles().count>0&&tiles().uploading===0,120); "
         # 等页面把链接那段对话切过来（拖慢的请求 5 秒后回），再看附件还在不在
         f"await sleep({DELAY_MS + 3000}); "
         "return {shown, tries, pickedAt, done, slowed: window.__slowed||[], tiles: tiles(), url: location.search};"},
        {"name": "发一句", "shot": True, "file": "2-sent", "js": head +
         f"const said=await say({tag}+' 看看这张'); if(!said) return {{error:'could not send', tiles: tiles()}}; "
         f"const bubble=await until(()=>qa('[data-role=\"user\"]').some(e=>e.innerText.includes({tag})),80); "
         "const cleared=await until(()=>tiles().count===0,40); "
         f"return {{said, bubble, cleared, url: location.search, here: new URLSearchParams(location.search).get('c')==={session}}};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    picked, sent = by.get("马上加附件") or {}, by.get("发一句") or {}
    return [
        {"case": "the page was really slowed while opening (listing / get held back)", "seen": picked.get("slowed"),
         "ok": bool(picked.get("slowed"))},
        {"case": "a file added while the linked conversation is opening uploads and is still in the composer once it is open",
         "seen": {k: picked.get(k) for k in ("shown", "tries", "pickedAt", "done", "tiles")},
         "ok": picked.get("shown") is True and picked.get("done") is True and (picked.get("tiles") or {}).get("count") == 1},
        {"case": "the message goes out in the linked conversation", "seen": sent,
         "ok": sent.get("said") is True and sent.get("bubble") is True and sent.get("here") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    site = manifest["site"]
    got = lab.python(site, f"""
import os
from pearnly_core.assistant import files, sessions
folder = files._folder({params['row']!r})
kept = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
made = frappe.get_all("Pearnly Assistant Session", filters={{"owner": {params['admin']!r}, "creation": [">=", {params['now']!r}]}},
                      fields=["session_id"], limit_page_length=50)
frappe.set_user({params['admin']!r})
for row in made:  # 收尾：这一格建的会话（含页面多建的）都删掉
    sessions.delete(row.session_id)
frappe.db.commit()
print(json.dumps({{"kept": kept, "made": [r.session_id for r in made]}}))
""" + (CLEANUP + "cleanup()\nfrappe.db.commit()\n" if site.startswith("cowork") else ""))
    tag = params["tag"]
    return [
        {"case": "the linked conversation holds the file on the server", "saved": got["kept"],
         "ok": any(name.endswith(f"--{tag}.png") for name in got["kept"])},
        {"case": "no other conversation was created while opening", "saved": got["made"],
         "ok": got["made"] == [params["session"]]},
    ]
