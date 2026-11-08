"""流程用例：Cowork 录入列表「新建录入」（仅识别）与仅识别那一批的「导出」（清存量基线 11-08）。

录入岗：
- 列表页头「新建录入」→ 原生弹窗点「仅识别」卡、选进项 →「继续」→ 进上传页（地址 `pearnly-intake/new`），页面拿到的这一批口径就是选的
  （目标 none、方向 purchase）；
- 不跑识别（付费）：prepare 按手工录入同一个函数（`history.storage.create_manual`）建一张票，再照新建录入时的做法把目标记成「仅识别」
  （`storage.set_push_targets`）；打开这张票，页头是「导出」→ 点下去走 POST、拿到的复核工作簿里有这张票的单号。
推送「提交」要 Express 连接加一台在线的 CC 桌面版；「新建文件夹」要 CC 桌面版选文件夹，这两颗不在这条里。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, workbook_text  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "Cowork 录入：新建录入选仅识别进上传页、仅识别那一批导出复核工作簿"
USER = "intake@cowork.regression"


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:8]
    fields = {"document_type": "receipt", "invoice_number": f"FLOWX-{tag}", "date": "2026-10-21", "direction": "purchase",
              "subtotal": "100", "vat": "7", "total_amount": "107", "currency": "THB",
              "seller_name": f"Flow seller {tag}", "seller_tax": "0105561234567", "seller_addr": "1 Flow Road",
              "seller_branch": "00000", "buyer_name": "Flow buyer"}
    made = lab.python(manifest["site"], f"""
from pearnly_cowork.history import storage
frappe.set_user({USER!r})
row = storage.create_manual({manifest['workspace']!r}, [{{"page_index": 1, "fields": {json.dumps(fields)}}}], {tag!r})
storage.set_push_targets([row["name"]], "", "", "none")
frappe.db.commit()
print(json.dumps({{"name": row["name"]}}))
""")
    return {"record": made["name"], "invoice": fields["invoice_number"], "cases": []}


def steps(params, camera_code=None):
    return [
        {"name": "新建录入", "js": JS + ACTION + """
const t=await frappe.pearnly.texts('intake.');
await until(()=>pageOf('pearnly-intake')&&frappe.get_route().length===1&&$.active===0, 25000); await idle();
const via=await action(t('intake.action.new')); if(!via) return {error:'no new button'};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.fields_dict.direction&&cur_dialog, 15000);
if(!d) return {error:'no new dialog'};
const card=await until(()=>d.$wrapper.find('.pearnly-choice-card[data-value="none"]:not(.is-blocked)')[0], 15000); if(!card) return {error:'no recognize-only card'};
card.click(); await sleep(400); await idle();
await d.set_value('direction', 'purchase'); await sleep(500); await idle();
const ready=await until(()=>!d.get_primary_btn().prop('disabled'), 10000); if(!ready) return {error:'Continue stays disabled'};
d.get_primary_btn().click();
const moved=await until(()=>frappe.get_route()[1]==='new', 15000); await idle();
return {via, moved: !!moved, route: frappe.get_route().join('/'), choice: frappe.pearnly.intake_choice?{target: frappe.pearnly.intake_choice.target, direction: frappe.pearnly.intake_choice.direction}:null};"""},
        {"name": "导出", "js": JS + ACTION + CAPTURE_EXPORTS + f"""
frappe.pearnly.intake_choice=null;
frappe.set_route('pearnly-intake', 'new', {js_value(params['record'])});
await until(()=>frappe.get_route()[2]==={js_value(params['record'])}&&all('.page-head .primary-action, .page-head .btn-primary')[0], 25000); await idle(); await sleep(800);
const via=await action(__('Export')); if(!via) return {{error:'no Export button', head: all('.page-head button').map(b=>b.innerText.trim())}};
""" + AWAIT_EXPORT + "return got?{via, ...got}:{error:'no export response', via};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    new, seen = by.get("新建录入", {}), by.get("导出", {})
    detail = {k: seen.get(k) for k in ("via", "url", "method", "status", "error")}
    ok = False
    if seen.get("b64"):
        try:
            _sheets, text = workbook_text(seen["b64"])
            detail["has_invoice"] = params["invoice"] in text
            ok = seen.get("method") == "POST" and seen.get("status") == 200 and detail["has_invoice"]
        except Exception as error:  # noqa: BLE001 - 打不开就是不通过
            detail["unreadable"] = str(error)[:200]
    return [
        {"case": "New intake with recognize-only and purchase goes to the upload page with that batch", "seen": new,
         "ok": bool(new.get("via")) and new.get("moved") is True and new.get("choice") == {"target": "none", "direction": "purchase"}},
        {"case": "the recognize-only ticket exports a review workbook holding its invoice number", "seen": detail, "ok": ok},
    ]


def verify(lab, manifest, params, camera_code=None):
    return []
