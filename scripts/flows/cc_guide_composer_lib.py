"""CC **直写**对话里「先选业务（采购 / 销售），再用输入框旁的 + 上传票据、只发文件不打字」真页面流程的共用片段
（11-28 testerp：这样交的票原来只进了会话附件——没有引导运行、没有识别、CC 说「文件在会话里没被识别」）。ERP 与 Cowork 同一套点法、
同一套判据（`erp_cc_guide_composer_lib.py`、`cowork_cc_guide_composer_lib.py` 各自造数据、核产品那一侧）：

1. 打开这段直写对话；
2. 业务菜单点这一项（`cc_lib.pickBusiness`），等 CC 那句「请上传…」出来；输入框「+」挂一张票（文件选择框替身，同 `cc_attach_delete`），
   不打字直接点发送；
3. 对话里出了这项业务的引导卡（服务端开的，不经模型），而且过了「交文件」那一步（卡上不再是交文件那一题：输入框交的票已交进去识别）。

库里（各产品 ``verify``）：这段对话只有一条这项业务的引导运行，交进去的就是刚上传的那一份（对话文件编号），识别批次已有；产品那一侧
建了识别记录。实验栈没有真识别，识别成败不在判据里（交没交进去才是这一格要守的）。

现有 `erp.cc.guide.purchase` 那格在 ``prepare`` 里服务端直接 ``run.start(doc_ids=…)``，跳过了交文件 → 识别，这条路从来没被测到；这一格补上。
"""
from __future__ import annotations

import json
import time

from cc_lib import JS

CARD = "qa('[data-slot=\"cc-guide-run\"]').pop()"
WORD = {"purchase": "采购", "sales": "销售"}
# 识别受理要的站点配置（同 `cc_send_while_waiting` / 线上 `site-step.py`）：没有预算 Redis，识别提交当场「推迟」、交文件那一题不收。
# 凭据是占位的：识别受理后一直排着（实验室没有识别进程），不会真识别、不花钱；收尾作废这一格交的识别、还原配置
OCR_CONFIG = {"pearnly_cowork_ocr_environment": {"OCR_LLM_BACKEND": "vertex"}, "pearnly_cowork_ocr_policy": {"mode": "economy"},
              "pearnly_ocr_credentials": "/nonexistent/flow-placeholder.json", "pearnly_ocr_budget_redis": "redis://redis:6379/6"}
# 1×1 的 PNG（同 `erp_cc_operator_intake` 造的票图）
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def configure(lab, manifest):
    """准备的最后一步：打开识别受理（`lab.site_config` 等网页进程都看到新值）。回 ``{config_before, start}`` 并进参数。"""
    start = lab.python(manifest["site"], 'print(json.dumps({"now": str(frappe.utils.now_datetime())}))')["now"]
    return {"config_before": lab.site_config(manifest["site"], OCR_CONFIG), "start": start}


def cleanup(lab, manifest, params):
    """收尾（成功失败都跑）：作废这一格交的识别（不占后面流程的排队名额），还原站点配置。"""
    if params.get("start"):
        lab.python(manifest["site"], f"""
frappe.db.sql("update `tabPearnly OCR Work` set status='cancelled' where status in ('queued', 'deferred') and creation >= %s", ({params['start']!r},))
frappe.db.commit()
print("{{}}")
""")
    if params.get("config_before") is not None:
        lab.site_config(manifest["site"], params["config_before"])


def steps(params, camera_code=None):
    business, name = json.dumps(params["business"]), json.dumps(params["file"])
    return [
        {"name": "打开直写对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"},
        {"name": "选业务再用输入框上传发出", "shot": True, "file": "1-sent", "js": JS + f"""
const card=()=>{CARD};
if(!await until(composerReady,200)) return {{error:'composer not ready'}};
if(!await until(()=>menuRow(),160)) return {{error:'no business menu'}};
const picked=await pickBusiness({business});
const prompt=await until(()=>q('[data-slot="cc-business-prompt"]'),80);
await sleep(1500);  // 选业务的那一下交给服务端（POST business）
// 文件选择框替身：组件库点「添加文件」时临时建一个 file 输入框并 click()，这里给它放进这张票
if(!window.__fileHook){{window.__fileHook=1; const orig=HTMLInputElement.prototype.click;
  HTMLInputElement.prototype.click=function(){{ if(this.type==='file'&&window.__nextFile){{const dt=new DataTransfer(); dt.items.add(window.__nextFile);
    window.__nextFile=null; this.files=dt.files; this.dispatchEvent(new Event('change',{{bubbles:true}})); return;}} return orig.apply(this,arguments);}};}}
const bytes=Uint8Array.from(atob({json.dumps(PIXEL)}),c=>c.charCodeAt(0));
window.__nextFile=new File([bytes], {name}, {{type:'image/png'}});
const add=qa('button.cc-attach').find(seen); if(!add) return {{picked, error:'no attach button'}}; add.click();
// 图片附件在输入框里画缩略图，文件名只在附件按钮的无障碍名称里（「附件：<文件名> · …」）
const shown=await until(()=>document.body.innerText.includes({name})||qa('[aria-label]').some(e=>(e.getAttribute('aria-label')||'').includes({name})),80);
const ready=await until(()=>{{const b=q('button.aui-composer-send'); return b&&!b.disabled;}},160);
const before=qa('[data-slot="cc-guide-run"]').length;
q('button.aui-composer-send')?.click();
// 引导卡：服务端在发出时开（事件推到页面）
const opened=await until(()=>card()&&card().dataset.business==={business},240);
await sleep(8000);  // 输入框交的票由后台交进卡里（实验室没有实时通道，下一步重新打开对话再看）
return {{picked, prompt, shown, ready, before, opened, cards: qa('[data-slot="cc-guide-run"]').length}};"""},
        {"name": "重新打开对话", "wait_ms": 6000, "js": JS +
         f"setTimeout(()=>location.assign('/assistant?c='+encodeURIComponent({json.dumps(params['session'])})),0); return true;"},
        {"name": "引导卡", "js": JS + f"""
const card=()=>{CARD};
// 过了交文件：识别中的卡画识别状态行（cc-guide-uploads，交文件那一题没有；识别中的卡不带 data-stage），题卡 / 复核带 data-stage
const stageOf=c=>c?.dataset.stage||(c?.querySelector('[data-slot="cc-guide-uploads"]')?'reading':'');
const moved=await until(()=>card()&&card().dataset.business==={business}&&['reading','asking','review'].includes(stageOf(card())),240);
await sleep(500);
const c=card();
return {{moved, cards: qa('[data-slot="cc-guide-run"]').length, business: c?.dataset.business||'', stage: stageOf(c),
  status: c?.dataset.status||'', run: c?.dataset.run||'', text: (c?.innerText||'').slice(0,300)}};"""},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    sent, card = by.get("选业务再用输入框上传发出") or {}, by.get("引导卡") or {}
    business = params["business"]
    return [
        {"case": f"业务菜单点了「{WORD[business]}」，CC 那句「请上传…」出来了；输入框挂上了票、能发",
         "seen": {k: sent.get(k) for k in ("picked", "prompt", "shown", "ready", "error")},
         "ok": bool((sent.get("picked") or {}).get("label")) and bool(sent.get("prompt")) and bool(sent.get("shown"))
         and bool(sent.get("ready"))},
        {"case": f"只发文件：对话里出了一张{WORD[business]}引导卡（服务端开的，不经模型），且已过交文件那一步（票交进去识别了）",
         "seen": {"opened": sent.get("opened"), **card},
         "ok": bool(sent.get("opened")) and bool(card.get("moved")) and card.get("cards") == 1 and card.get("business") == business
         and card.get("stage") in ("reading", "asking", "review") and card.get("status") in ("asking", "reviewing")},
    ]


def guide_rows(lab, manifest, params):
    """这段对话的引导运行与刚上传的那一份（对话文件编号）。"""
    return lab.python(manifest["site"], f"""
from pearnly_core.assistant import files, sessions
row = frappe.db.get_value("Pearnly Assistant Session", {{"session_id": {params['session']!r}}}, ["name", "business"], as_dict=True) or {{}}
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}}},
                      fields=["name", "business", "status", "documents"], order_by="creation asc", limit_page_length=20)
for run in runs:
    try:
        run["documents"] = json.loads(run.get("documents") or "{{}}")
    except ValueError:
        pass
folder = files._folder(row.get("name") or "x")
import os
uploaded = sorted(entry.partition("--")[0] for entry in (os.listdir(folder) if os.path.isdir(folder) else ())
                  if "--" in entry and entry.endswith({params['file']!r}))
print(json.dumps({{"business": row.get("business"), "runs": runs, "uploaded": uploaded}}, default=str))
""")


def guide_cases(got, params):
    runs = [run for run in got.get("runs") or [] if run.get("business") == params["business"]]
    documents = (runs[0].get("documents") if runs else None) or {}
    return [
        {"case": f"这段对话记着业务「{params['business']}」，只开了一条这项业务的引导运行", "seen": got,
         "ok": got.get("business") == params["business"] and len(runs) == 1},
        {"case": "交进引导的就是输入框刚上传的那一份（对话文件编号），识别批次已有", "seen": documents,
         "ok": bool(got.get("uploaded")) and documents.get("files") == got.get("uploaded")
         and bool(documents.get("batches"))},
    ]
