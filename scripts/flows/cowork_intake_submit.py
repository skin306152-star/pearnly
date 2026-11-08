"""流程用例：Cowork 录入编辑页「提交」把票推向 Express（清存量基线 11-08）。

造数据（产品自己的函数，`cowork_operator_lib.py` 那一套，换成本流程自己的连接名、账套键与公司税号，免得与代录流程并行时互删）：
一条 Express 连接、一份账套、从目录建的公司；一张识别好的进项票（目标 Express、这份账套），付款方式与费用科目记在账套记忆里（同复核页选过一次），
推送准备不停下等人选。打开这张票的编辑页，页头「提交」→ 推送接口：库里这张票有一条推送记录（已准备）；实验室没有能写这份账套的 CC 桌面版，
确认那一步交不出去，原因（没有能写这份账套的电脑）记在票上；退回原因只记在票上、不弹窗（用户 2026-09-26），
列表里这一行是红色「失败」、悬浮是这句原因。跑完清掉造的数据。不连 Express、不发 LINE、不调模型。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cowork_operator_lib import BASE, CLEANUP, LABEL, TAX  # noqa: E402
from desk_list import ACTION  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "Cowork 录入编辑页「提交」：推向 Express，库里有推送记录、票上录入中"
MY_LABEL = "Flow intake submit Express"
MY_TAX = "0994000777020"


def _mine(snippet: str) -> str:
    """代录流程的造数据片段换成本流程自己的连接名、账套键、公司税号。"""
    return (snippet.replace(LABEL, MY_LABEL).replace("FLOWOP", "FLOWIS").replace("flowop", "flowis")
            .replace(TAX, MY_TAX))


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6].upper()
    out = lab.python(manifest["site"], _mine(CLEANUP + BASE) + f"""
print(json.dumps({{"record": recognized("FLOW-IS-{tag}"), "connection": conn["name"], "invoice": "FLOW-IS-{tag}"}}))
""")
    return {**out, "cases": []}


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], _mine(CLEANUP) + "\nprint(json.dumps(cleanup()))")


def steps(params, camera_code=None):
    record = js_value(params["record"])
    return [{"name": "提交", "js": JS + ACTION + f"""
frappe.pearnly.intake_choice=null;
frappe.set_route('pearnly-intake', 'new', {record});
await until(()=>frappe.get_route()[2]==={record}&&all('.page-head .primary-action, .page-head .btn-primary')[0], 25000); await idle(); await sleep(800);
const via=await action(__('Submit')); if(!via) return {{error:'no Submit button', head: all('.page-head button').map(b=>b.innerText.trim())}};
await sleep(800); await until(()=>!document.querySelector('.freeze-message-container'), 30000); await idle(); await sleep(600);
const detail=await call('pearnly_cowork.history.api.detail', {{name: {record}}}, 'GET');
// 退回的原因只记在票上、不弹窗（用户 2026-09-26）：回列表看这张票那一行的状态与悬浮原因
frappe.set_route('pearnly-intake'); await until(()=>frappe.get_route().length===1&&document.querySelector('.result .list-row-container'), 20000); await idle(); await sleep(600);
const row=[...document.querySelectorAll('.result .list-row-container')].find(r=>r.innerText.includes({js_value(params['invoice'])}));
const pill=row&&row.querySelector('.indicator-pill');
return {{via, push: detail&&{{status: detail.last_push_status, reason: detail.last_push_reason}}, listed: !!row,
  status: pill?pill.innerText.trim():'', // 悬浮原因：框架把 title 接管成原生悬浮提示，挪到 data-original-title
  titles: row?[...row.querySelectorAll('[title], [data-original-title]')].flatMap(e=>[e.getAttribute('title'), e.getAttribute('data-original-title')]).filter(Boolean):[],
  reason: detail&&frappe.pearnly.intake_push_reason?frappe.pearnly.intake_push_reason(detail.last_push_reason):'', failed: __('connect.records.failed')}};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") for r in results if r.get("step") == "提交"), None) or {}
    push = seen.get("push") or {}
    return [{"case": "Submit pushes the ticket; with no CC Desktop able to write this account set the list shows it failed and why", "seen": seen,
             "ok": bool(seen.get("via")) and bool(push.get("reason")) and seen.get("listed") is True
             and seen.get("status") == seen.get("failed") and bool(seen.get("reason")) and seen.get("reason") in (seen.get("titles") or [])}]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("Cowork Push Record", filters={{"history": {params['record']!r}}}, fields=["name", "status", "connection"])
doc = frappe.db.get_value("Cowork OCR History", {params['record']!r}, ["last_push_status", "last_push_reason"], as_dict=True) or {{}}
print(json.dumps({{"rows": rows, "doc": doc}}, default=str))
""")
    rows, doc = got["rows"], got["doc"]
    return [
        {"case": "one push record for this ticket on this connection", "saved": rows,
         "ok": len(rows) == 1 and rows[0]["connection"] == params["connection"]},
        # 实验室没有 CC 桌面版：推送记录准备好了、等不到能写的电脑，原因记在票上（有电脑时的写入由代录 / 推送实验脚本守）
        {"case": "the record is prepared and the ticket keeps the reason", "saved": {"rows": rows, "doc": doc},
         "ok": bool(rows) and rows[0]["status"] == "prepared" and bool(doc.get("last_push_reason"))},
    ]
