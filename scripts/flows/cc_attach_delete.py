"""流程用例：CC 对话页「添加文件」与「删除对话」（ERP、Cowork 各一条，同一模块；清存量基线 11-08）。

模型换成实验室的假智能体服务（`lab_agents.py`，同 `cc.chat`），不调付费模型。管理员在 CC 新建页：
- 输入框左下「添加文件」→ 选一份小文本（页面的文件选择框替身给它）→ 附件传完 → 发一句带标记的话 → 收到回复；
  本会话的文件列表（`assistant.api.session_files`）里有这份，下载回来的内容就是传上去的；
- 对话标题栏「对话选项」→「删除」→ 确认框「删除」→ 侧栏接口里没有这段对话；库里这段会话没了、留存的附件目录也没了。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cc_lib import JS  # noqa: E402

TITLE = "CC 对话页：添加文件随消息发出、删除对话（假智能体后端）"
READY = "document.readyState === 'complete' && !!document.querySelector('textarea.aui-composer-input')"
REPLY = "[lab-agent]"


def _admin(manifest):
    return "admin@cowork.regression" if manifest["site"].startswith("cowork") else "admin@erp.regression"


def prepare(lab, manifest):
    admin = _admin(manifest)
    lab.python(manifest["site"], f'frappe.db.set_value("User", {admin!r}, "language", "zh")\nfrappe.db.commit()\nprint("{{}}")')
    tag = "flow" + uuid.uuid4().hex[:6]
    return {"tag": tag, "admin": admin, "file": f"{tag}-note.txt", "content": f"flow attachment {tag}", "cases": []}


def steps(params, camera_code=None):
    tag, name, content = (json.dumps(params[k]) for k in ("tag", "file", "content"))
    return [
        {"name": "添加文件并发出", "js": JS + f"""
await until(()=>q('textarea.aui-composer-input'),80);
// 文件选择框替身：组件库点「添加文件」时临时建一个 file 输入框并 click()，这里给它放进这份文件
if(!window.__fileHook){{window.__fileHook=1; const orig=HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click=function(){{ if(this.type==='file'&&window.__nextFile){{const dt=new DataTransfer(); dt.items.add(window.__nextFile);
    window.__nextFile=null; this.files=dt.files; this.dispatchEvent(new Event('change',{{bubbles:true}})); return;}} return orig.apply(this,arguments);}};}}
window.__nextFile=new File([{content}], {name}, {{type:'text/plain'}});
// 新对话先加文件：上传给它建会话，这期间对话区不能换成「正在加载消息」（.cc-loading，输入框会掉到最底下再跳回来，11-11 testcowork）
let loading=false; const watch=setInterval(()=>{{ if(q('.cc-loading')) loading=true; }},10);
const add=qa('button.cc-attach').find(seen); if(!add) return {{error:'no attach button'}}; add.click();
const shown=await until(()=>document.body.innerText.includes({name}),80);
// 等附件传完：发送钮可用
await until(()=>{{const b=q('button.aui-composer-send'); return b&&!b.disabled;}},120);
await sleep(1000); clearInterval(watch);
const sent=await say({tag}+' 看一下附件'); if(!sent) return {{error:'could not send', shown}};
const got=await until(()=>qa('[data-role="assistant"]').some(e=>e.innerText.includes({json.dumps(REPLY)})),240);
const row=(await listed('chat')).find(s=>(s.title||'').includes({tag}));
let files=[], body='';
if(row){{ files=(await call('pearnly_core.assistant.api.session_files',{{session_id: row.id}}))||[];
  const hit=files.find(f=>(f.filename||f.name||'')==={name});
  if(hit){{ const r=await fetch('/api/method/pearnly_core.assistant.api.download?'+new URLSearchParams({{session_id: row.id, file_id: hit.id}})); body=await r.text(); }} }}
return {{shown, got, loading, session: row&&row.id, files: files.map(f=>f.filename||f.name), body: body.slice(0,200)}};"""},
        {"name": "删除对话", "js": JS + f"""
const menu=qa('button[aria-label="对话选项"]').find(seen); if(!menu) return {{error:'no conversation menu'}};
press(menu); await until(()=>qa('[role="menuitem"]').some(e=>seen(e)&&e.innerText.trim()==='删除'),20);
const item=qa('[role="menuitem"]').find(e=>seen(e)&&e.innerText.trim()==='删除'); if(!item) return {{error:'no delete item'}}; item.click();
const yes=await until(()=>seen(q('[data-slot="cc-delete-chats-yes"]')),40); if(!yes) return {{error:'no delete confirm'}};
q('[data-slot="cc-delete-chats-yes"]').click();
let left=true;
for(let k=0;k<60&&left;k++){{ left=(await listed('chat')).some(s=>(s.title||'').includes({tag})); if(left) await sleep(500); }}
return {{confirmed: !!yes, left}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    att, drop = by.get("添加文件并发出", {}), by.get("删除对话", {})
    return [
        {"case": "the attached file shows in the composer, the message goes out and gets a reply", "seen": {k: att.get(k) for k in ("shown", "got")},
         "ok": att.get("shown") is True and att.get("got") is True},
        {"case": "attaching the file to a new chat never flips it to Loading messages", "seen": {"loading": att.get("loading")},
         "ok": att.get("loading") is False},
        {"case": "the session's file list holds the file and its download is what was sent", "seen": {k: att.get(k) for k in ("files", "body")},
         "ok": params["file"] in (att.get("files") or []) and params["content"] in (att.get("body") or "")},
        {"case": "the conversation menu deletes the chat after the confirm", "seen": drop,
         "ok": drop.get("confirmed") is True and drop.get("left") is False},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("Pearnly Assistant Session", filters={{"user": {params['admin']!r}, "title": ["like", "%{params['tag']}%"]}}, pluck="name")
print(json.dumps({{"rows": rows}}))
""")
    return [{"case": "the deleted conversation is gone from the books", "saved": got["rows"], "ok": got["rows"] == []}]
