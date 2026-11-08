"""流程用例：Cowork 对账三类（收入 / 销项 / 银行）在页面上从头做一遍，每颗页头按钮点下去都核库里的结果
（清产品地图「没测试」存量基线，11-08）。

对账岗依次打开收入、销项、银行三页，每页：
- 列表「新建」→ 新建页「取消」→ 回到列表，库里这一类的任务不多；
- 「新建」→ 左右两个「选择文件」各给一份 xlsx（页面的选文件替身，内容 prepare 在站点里生成）、填任务名称 →「开始」→
  看板 → 自动进结果页：库里多一份这个名称的任务、两边对上的笔数对；
- 结果页改一格（收入、销项改买方名称，银行改对账单摘要）→ 出「保存」→ 点下去：库里这份任务存着改后的字；
- 收入、银行结果页「提问 (n)」→ 答第一个选项（是）：库里记下这一条对账预设（收入：本公司税号 · 没对上的收入科目 4201；
  银行：账户尾号 · 科目的多笔对一笔）；收入页再点「对账预设」，弹窗里列着这一条，把答案改成「否」→ 库里跟着变；
- 销项结果页「导出」：走 POST、拿到的复核表里有这张发票号；
- 进行中：prepare 给对账岗挂一份一直排队的对账（只有状态缓存，没有真的后台任务）：列表出「进行中 · 1」，点进去列着它，
  「返回」回列表；打开它的看板，标题是它的名称，「返回」回列表。cleanup 把它拿掉。

不会调付费识别：两边都是 xlsx，走表格直读（实验室也没配识别）；judge / verify 核识别任务数不变。
税号、账户尾号每次随机：答过一次的预设会让以后不再问。cleanup 删掉本次建的任务（页面同一个删除接口）、预设和排队的假对账。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from desk_list import ACTION, AWAIT_EXPORT, CAPTURE_EXPORTS, workbook_text  # noqa: E402
from erp_lib import JS, js_value  # noqa: E402

TITLE = "Cowork 对账三类从头做一遍：新建、取消、开始、保存、提问、预设、进行中、返回、导出"
USER = "recon@cowork.regression"
ROOTS = {"income": "pearnly-recon-income", "vat": "pearnly-recon-vat", "bank": "pearnly-recon-bank"}
DOCTYPES = {"income": "Cowork Income Reconciliation", "vat": "Cowork VAT Check", "bank": "Cowork Bank Reconciliation"}
KINDS = ("income", "vat", "bank")


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    # 13 位、首位不是 0 的随机税号；4 位随机账户尾号
    tax = "1" + f"{uuid.uuid4().int % 10 ** 12:012d}"
    tail = f"{uuid.uuid4().int % 9000 + 1000}"
    out = lab.python(manifest["site"], f"""
import base64, io, uuid
import openpyxl
from pearnly_cowork.recon_center import jobs, progress

def book(sheets):
    wb = openpyxl.Workbook(); wb.remove(wb.active)
    for name, rows in sheets:
        ws = wb.create_sheet(title=name)
        for row in rows:
            ws.append(list(row))
    out = io.BytesIO(); wb.save(out)
    return base64.b64encode(out.getvalue()).decode()

tag, tax, tail = {tag!r}, {tax!r}, {tail!r}
head = [["รายงานภาษีขาย"], ["ชื่อผู้ประกอบการ บริษัท โฟลว์ จำกัด"], [f"เลขประจำตัวผู้เสียภาษี {{tax}} สำนักงานใหญ่"], []]
table = ["วันที่", "เลขที่ใบกำกับ", "ชื่อผู้ซื้อ", "เลขประจำตัวผู้เสียภาษี", "สาขา", "มูลค่า", "ภาษี", "ยอดรวม"]
sale = ["01/06/2569", f"INV-{{tag}}", f"ACME {{tag}}", "1234567890123", "00000", 1000.0, 70.0, 1070.0]
files = {{
    # 收入：左总账（一笔销售 1000 对得上税报的未税额、一笔利息 4201 税报里没有 → 问一次），右税报（表头印本公司税号）
    "income": {{"left": ("GL income.xlsx", book([("GL", [["วันที่", "ใบสำคัญ", "คำอธิบาย", "เดบิต", "เครดิต", "รหัสบัญชี"],
                                                        ["01/06/2569", f"INV-{{tag}}", "sale", 0, 1000.0, "4110-01"],
                                                        ["15/06/2569", f"GL-9-{{tag}}", "interest", 0, 50.0, "4201"]])])),
               "right": (f"VAT income {{tag}}.xlsx", book([("VAT", head + [table, sale])]))}},
    # 销项：左税报、右发票（表格版发票清单，同一张发票）
    "vat": {{"left": ("VAT report.xlsx", book([("VAT", head + [table, sale])])),
            "right": (f"Invoices {{tag}}.xlsx", book([("INV", [table, sale])]))}},
    # 银行：左对账单（工作表名就是账户尾号），右总账；对账单一笔 300 到账 = 总账两笔 100 + 200（多笔对一笔 → 问一次）
    "bank": {{"left": (f"STM BBL {{tail}}.xlsx", book([(tail, [["วันที่", "รายการ", "ถอนเงิน", "ฝากเงิน", "ยอดคงเหลือ"],
                                                             ["01/06/2569", "ยกมา", "", "", 1000.0],
                                                             ["02/06/2569", f"SWD ACME {{tag}}", 400.0, "", 600.0],
                                                             ["05/06/2569", f"DEP SHOP {{tag}}", "", 300.0, 900.0],
                                                             ["06/06/2569", "ค่าบริการ", 90.0, "", 810.0]])])),
             "right": (f"GL bank {{tag}}.xlsx", book([("gl", [["วันที่", "เลขที่เอกสาร", "คำอธิบาย", "เดบิต", "เครดิต", "คงเหลือ", "รหัสบัญชี"],
                                                     ["01/06/2569", "GL-0", "brought forward", 990.0, "", 990.0, "1112-01"],
                                                     ["02/06/2569", "GL-1", "payment", "", 400.0, 590.0, "1112-01"],
                                                     ["04/06/2569", "GL-2", "shop a", 100.0, "", 690.0, "1112-01"],
                                                     ["05/06/2569", "GL-3", "shop b", 200.0, "", 890.0, "1112-01"]])]))}},
}}
frappe.set_user({USER!r})
# 一直排队的对账（每类一份）：只有状态缓存与「进行中」清单，没有后台任务——「进行中」「返回」两颗按钮要它
waiting = {{}}
for kind in ("income", "vat", "bank"):
    job = uuid.uuid4().hex
    title = f"flow waiting {{kind}} {{tag}}"
    jobs._state(job, user={USER!r}, kind=kind, state="queued", stage="queued", task=None, error=None, title=title, files=[])
    progress.active_add({USER!r}, job, kind, title)
    waiting[kind] = {{"job": job, "title": title}}
frappe.db.commit()
print(json.dumps({{"files": {{kind: {{side: list(item) for side, item in sides.items()}} for kind, sides in files.items()}},
                  "waiting": waiting,
                  "ocr": frappe.db.count("Pearnly OCR Work") if frappe.db.table_exists("Pearnly OCR Work") else 0}}))
""")
    return {"tag": tag, "tax": tax, "tail": tail, **out,
            "titles": {kind: f"flow {kind} {tag}" for kind in KINDS},
            "edits": {"income": [f"ACME {tag}", f"ACME EDIT {tag}"], "vat": [f"ACME {tag}", f"ACME EDIT {tag}"],
                      "bank": ["ค่าบริการ", f"FEE EDIT {tag}"]}}


def cleanup(lab, manifest, params):
    # 本次建的东西都拿掉：排队的假对账、三份任务（走页面同一个删除接口）、答出来的预设——别的对账流程按「库里这一类有几份 /
    # 有没有预设」断言（`cowork.recon.presets` 新开户要「暂无预设」，`cowork.recon.exports` 新开户要没有任务）
    lab.python(manifest["site"], f"""
from pearnly_core.execution import state as pearnly_state
from pearnly_cowork.recon_center import api, jobs, progress
frappe.set_user({USER!r})
for item in {json.dumps(params["waiting"])}.values():
    progress.active_remove({USER!r}, item["job"])
    pearnly_state.store().delete_value(jobs._key(item["job"]))
for kind, doctype in {DOCTYPES!r}.items():
    names = frappe.get_all(doctype, filters={{"title": {json.dumps(params["titles"])}[kind]}}, pluck="name")
    if names:
        api.delete(kind, names)
from pearnly_cowork.recon_center import presets
# 预设表只许走服务删（`preset_store` 拒绝直接删）：用预设弹窗「删除」同一个函数
for like in ("%{params['tax']}%", "%|{params['tail']}|%"):
    for row in frappe.get_all("Cowork Recon Preset", filters={{"scope": ["like", like]}}, fields=["recon_kind", "scope", "rule"]):
        presets.remove(row.recon_kind, row.scope, row.rule)
frappe.db.commit()
print(json.dumps({{}}))
""")


# 页面公用：对账术语（`p.texts`）、等页面路由、点一颗页头按钮（手机宽度在「…」里）
SETUP = JS + ACTION + "const t=await frappe.pearnly.texts('cowork.recon.');" \
    "const at=(root,sub)=>{const r=frappe.get_route();return r[0]===root&&(sub===undefined?r.length===1:r[1]===sub);};" \
    "const go=async(...route)=>{frappe.set_route(...route);await sleep(600);await idle();};"


def _list(root):
    return f"await go({js_value(root)}); await until(()=>at({js_value(root)})&&document.querySelector('.page-head .primary-action:not(.hide)'), 25000); await idle();"


def steps(params, camera_code=None):
    out = []
    for kind in KINDS:
        root = ROOTS[kind]
        r, title = js_value(root), js_value(params["titles"][kind])
        left, right = params["files"][kind]["left"], params["files"][kind]["right"]
        old, new = (js_value(v) for v in params["edits"][kind])
        waiting = params["waiting"][kind]
        out.append({"name": f"新建取消 {kind}", "js": SETUP + _list(root) + f"""
const via=await action(t('cowork.recon.new')); if(!via) return {{error:'no New button'}};
if(!await until(()=>at({r},'new'), 15000)) return {{error:'new page did not open', route: frappe.get_route()}}; await idle();
const back=await action(__('Cancel')); if(!back) return {{error:'no Cancel button'}};
const listed=await until(()=>at({r}), 15000); await idle();
return {{via, back, listed: !!listed}};"""})
        out.append({"name": f"开始 {kind}", "js": SETUP + _list(root) + f"""
const file=(name,b64)=>{{const s=atob(b64);const a=new Uint8Array(s.length);for(let i=0;i<s.length;i++)a[i]=s.charCodeAt(i);
  return new File([a],name,{{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}});}};
const queue=[[file({js_value(left[0])},{js_value(left[1])})],[file({js_value(right[0])},{js_value(right[1])})]];
const real=frappe.pearnly.pick_files; frappe.pearnly.pick_files=async()=>queue.shift()||[];
try {{
  if(!await action(t('cowork.recon.new'))) return {{error:'no New button'}};
  await until(()=>at({r},'new')&&document.querySelector('[data-fieldname="left_pick"] button'), 15000); await idle();
  for(const side of ['left','right']){{const b=all('[data-fieldname="'+side+'_pick"] button')[0]; if(!b) return {{error:'no pick button '+side}};
    b.click(); await until(()=>(document.querySelector('[data-fieldname="'+side+'_file"] input')?.value||document.querySelector('[data-fieldname="'+side+'_file"] .control-value')?.innerText||'').trim(), 8000);}}
  // 任务名称：新建页上看得见的那一格（列表页的筛选栏也有一个 title 字段，藏着；这一格画出来是多行框）
  const name=all('[data-fieldname="title"] input, [data-fieldname="title"] textarea', pageOf({r})).pop(); if(!name) return {{error:'no title input'}};
  setInput(name, {title}); await sleep(300);
  const via=await action(t('cowork.recon.start')); if(!via) return {{error:'no Start button'}};
  const board=await until(()=>at({r},'run'), 20000);
  const done=await until(()=>{{const x=frappe.get_route();return x[0]==={r}&&/^C(IR|VC|BR)-/.test(x[1]||'')&&pageOf({r})?.querySelector('.pearnly-recon-book');}}, 120000);
  await idle(); await sleep(800);
  // 任务号给后面几步用（每步是一段独立脚本，靠页面上的全局变量接力）
  window.__recon_task_{kind}=done?frappe.get_route()[1]:'';
  return {{via, board: !!board, task: window.__recon_task_{kind}, title: (frappe.pages[{r}]?.page?.title||'').trim(), msg: msg()}};
}} finally {{ frappe.pearnly.pick_files=real; }}"""})
        out.append({"name": f"保存 {kind}", "js": SETUP + f"""
const task=window.__recon_task_{kind}; if(!task) return {{error:'no result page'}};
if(!at({r},task)) {{ await go({r}, task); await until(()=>pageOf({r})?.querySelector('.pearnly-recon-book'), 25000); await idle(); }}
// 可改的格子在明细页：逐个页签找到写着原字的那一格
let cell=null; const tabs=all('.pearnly-recon-tabs .nav-link');
for(const tab of [null,...tabs]){{ if(tab){{tab.click(); await sleep(300);}}
  cell=all('.pearnly-recon-scroll .editable').find(e=>e.innerText.trim()==={old}); if(cell) break; }}
if(!cell) return {{error:'no editable cell with '+{old}}};
cell.focus(); cell.innerText={new}; cell.blur();
const shown=await until(()=>all('.page-head .primary-action, .page-head .btn-primary').find(b=>b.innerText.trim()===__('Save')), 15000); await idle();
if(!shown) return {{error:'no Save button after the edit'}};
const via=await action(__('Save')); if(!via) return {{error:'Save not clickable'}};
const gone=await until(()=>!all('.page-head .primary-action, .page-head .btn-primary').some(b=>b.innerText.trim()===__('Save')), 20000); await idle();
return {{via, saved: !!gone, msg: msg()}};"""})
        if kind in ("income", "bank"):
            out.append({"name": f"提问 {kind}", "js": SETUP + f"""
const task=window.__recon_task_{kind}; if(!task) return {{error:'no result page'}};
if(!at({r},task)) {{ await go({r}, task); await until(()=>pageOf({r})?.querySelector('.pearnly-recon-book'), 25000); await idle(); }}
let via='', label=''; for(let n=1;n<6&&!via;n++){{label=t('cowork.recon.questions', n); via=await action(label);}}
if(!via) return {{error:'no Questions button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog, 15000); if(!d) return {{error:'no question dialog'}};
const asked=(d.title||'').trim(); const choice=d.$body.find('.pearnly-recon-choices button')[0]; if(!choice) return {{error:'no choices'}};
const answer=choice.innerText.trim(); choice.click();
await until(()=>!d.$wrapper.is(':visible'), 30000); await idle(); await sleep(800);
const presets=await call('pearnly_cowork.recon_center.presets.listing', {{kind: {js_value(kind)}}}, 'GET');
return {{via, label, asked, answer, presets: (presets||[]).map(p=>[p.scope, p.rule, p.value])}};"""})
        if kind == "income":
            out.append({"name": "预设 income", "js": SETUP + _list(root) + f"""
const via=await action(t('cowork.recon.presets')); if(!via) return {{error:'no Presets button'}};
const d=await until(()=>window.cur_dialog&&cur_dialog.$wrapper.is(':visible')&&cur_dialog.$body.find('.pearnly-recon-preset, .text-muted').length&&cur_dialog, 15000);
if(!d) return {{error:'no presets dialog'}}; await idle();
const row=[...d.$body.find('.pearnly-recon-preset')].find(e=>e.innerText.includes({js_value(params['tax'])})&&e.innerText.includes('4201'));
if(!row) return {{error:'answered preset not listed', body: d.$body.text().slice(0,300)}};
const select=row.querySelector('select'); if(!select) return {{error:'no answer select'}};
select.value='no'; select.dispatchEvent(new Event('change',{{bubbles:true}})); await sleep(500); await idle();
const listed=row.innerText.replace(/\\s+/g,' ').trim(); d.hide();
return {{via, listed}};"""})
        if kind == "vat":
            out.append({"name": "导出 vat", "js": SETUP + CAPTURE_EXPORTS + f"""
const task=window.__recon_task_{kind}; if(!task) return {{error:'no result page'}};
await go({r}, task); await until(()=>pageOf({r})?.querySelector('.pearnly-recon-book')&&document.querySelector('.page-head .primary-action:not(.hide)'), 25000); await idle();
const via=await action(t('cowork.recon.export')); if(!via) return {{error:'no Export button'}};
""" + AWAIT_EXPORT + "return got?{via, ...got}:{error:'no export response'};"})
        out.append({"name": f"进行中 {kind}", "js": SETUP + _list(root) + f"""
const label=t('cowork.recon.runningCount', 1);
const shown=await until(()=>all('.page-head button, .page-head .dropdown-item').some(b=>b.innerText.trim()===label)||document.querySelector('.page-head .menu-btn-group > button'), 15000);
await sleep(1500); await idle();
// 电脑宽度是页头按钮「进行中（1）」；手机宽度框架另放的菜单项是加按钮时的原字「进行中」（框架不跟着改字、不跟着隐藏）
let used=label, via=await action(label); if(!via){{used=t('cowork.recon.running'); via=await action(used);}}
if(!via) return {{error:'no running button '+label}};
const opened=await until(()=>at({r},'running'), 15000); await idle();
const listed=await until(()=>all('.result .list-row-container').some(e=>e.innerText.includes({js_value(waiting['title'])})), 15000);
const back=await action(t('cowork.recon.live.back')); if(!back) return {{error:'no Back button on the running page'}};
const returned=await until(()=>at({r}), 15000); await idle();
return {{via, used, opened: !!opened, listed: !!listed, back, returned: !!returned}};"""})
        out.append({"name": f"看板返回 {kind}", "js": SETUP + f"""
await go({r}, 'run', {js_value(waiting['job'])});
const titled=await until(()=>(frappe.pages[{r}]?.page?.title||'').trim()==={js_value(waiting['title'])}, 20000); await idle();
const back=await action(t('cowork.recon.live.back')); if(!back) return {{error:'no Back button on the board'}};
const returned=await until(()=>at({r}), 15000); await idle();
return {{titled: !!titled, back, returned: !!returned}};"""})
    return out


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    checks = []
    for kind in KINDS:
        cancel, start, save = by.get(f"新建取消 {kind}", {}), by.get(f"开始 {kind}", {}), by.get(f"保存 {kind}", {})
        running, board = by.get(f"进行中 {kind}", {}), by.get(f"看板返回 {kind}", {})
        checks += [
            {"case": f"{kind}: New opens the new page and Cancel goes back to the list", "seen": cancel,
             "ok": bool(cancel.get("via")) and bool(cancel.get("back")) and cancel.get("listed") is True},
            {"case": f"{kind}: Start goes through the board to the result page", "seen": start,
             "ok": bool(start.get("via")) and start.get("board") is True and bool(start.get("task")) and not start.get("msg")
             and start.get("title") == params["titles"][kind]},
            {"case": f"{kind}: an edit shows Save and Save clears it", "seen": save,
             "ok": bool(save.get("via")) and save.get("saved") is True and not save.get("msg")},
            {"case": f"{kind}: the running button lists the waiting reconciliation and Back returns", "seen": running,
             "ok": (running.get("via") == "button" and "1" in (running.get("used") or "") or running.get("via") == "menu")
             and running.get("opened") is True and running.get("listed") is True
             and bool(running.get("back")) and running.get("returned") is True},
            {"case": f"{kind}: the board shows the waiting reconciliation and Back returns", "seen": board,
             "ok": board.get("titled") is True and bool(board.get("back")) and board.get("returned") is True},
        ]
    for kind, scope in (("income", f"income|{params['tax']}|4201"),):
        asked = by.get(f"提问 {kind}", {})
        checks.append({"case": f"{kind}: answering the question stores the preset", "seen": asked,
                       "ok": bool(asked.get("via")) and [scope, "excluded", "yes"] in (asked.get("presets") or [])})
    asked = by.get("提问 bank", {})
    checks.append({"case": "bank: answering the grouped question stores the preset", "seen": asked,
                   "ok": bool(asked.get("via")) and any(p[0].startswith("bank|") and f"|{params['tail']}|" in p[0]
                                                       and p[1:] == ["grouped", "yes"] for p in asked.get("presets") or [])})
    presets = by.get("预设 income", {})
    checks.append({"case": "income: the presets dialog lists the answered preset", "seen": presets,
                   "ok": bool(presets.get("via")) and params["tax"] in (presets.get("listed") or "")})
    seen = by.get("导出 vat", {})
    detail = {k: seen.get(k) for k in ("url", "method", "status", "error")}
    ok = False
    if seen.get("b64"):
        try:
            _sheets, text = workbook_text(seen["b64"])
            detail["has_invoice"] = f"INV-{params['tag']}" in text
            ok = seen.get("method") == "POST" and seen.get("status") == 200 and detail["has_invoice"]
        except Exception as error:  # noqa: BLE001 - 打不开就是不通过
            detail["unreadable"] = str(error)[:200]
    checks.append({"case": "vat: Export posts and the workbook lists the invoice", "seen": detail, "ok": ok})
    return checks


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
frappe.set_user({USER!r})
from pearnly_cowork.recon_center import sheets
titles = {json.dumps(params["titles"])}
out = {{"counts": {{}}, "tasks": {{}}}}
for kind, doctype in {DOCTYPES!r}.items():
    # 本次的：标题带本次标记（填的名称带它；没填名称时默认用文件名，右边文件名也带它）
    out["counts"][kind] = frappe.db.count(doctype, {{"title": ["like", "%{params['tag']}%"]}})
    names = frappe.get_all(doctype, filters={{"title": titles[kind]}}, pluck="name")
    row = {{"names": names}}
    if names:
        doc = frappe.get_doc(doctype, names[0])
        row["counts"] = {{key: int(doc.get(key) or 0) for key in ("matched_count", "diff_count", "unmatched_count")
                         if doc.meta.has_field(key)}}
        row["inputs"] = json.dumps(sheets.load(kind, names[0], "th").get("inputs"), ensure_ascii=False, default=str)
    out["tasks"][kind] = row
out["presets"] = frappe.get_all("Cowork Recon Preset", fields=["scope", "rule", "value"],
                               filters={{"scope": ["like", "%{params['tax']}%"]}}) + \\
    frappe.get_all("Cowork Recon Preset", fields=["scope", "rule", "value"], filters={{"scope": ["like", "%|{params['tail']}|%"]}})
out["ocr"] = frappe.db.count("Pearnly OCR Work") if frappe.db.table_exists("Pearnly OCR Work") else 0
print(json.dumps(out, default=str))
""")
    checks = []
    for kind in KINDS:
        task = got["tasks"][kind]
        _old, new = params["edits"][kind]
        checks += [
            {"case": f"{kind}: Cancel added nothing and Start added exactly the one task", "saved": {"this_run": got["counts"][kind],
             "names": task["names"]}, "ok": got["counts"][kind] == 1 and len(task["names"]) == 1},
            # 销项改了买方名称存的是「名称差异」：两边配上了（对上或有差异），没有落单的
            {"case": f"{kind}: the two sides paired up", "saved": task.get("counts"),
             "ok": bool(task.get("counts")) and task["counts"].get("unmatched_count", 0) == 0
             and task["counts"].get("matched_count", 0) + task["counts"].get("diff_count", 0) >= 1},
            {"case": f"{kind}: Save stored the edited cell", "saved": (task.get("inputs") or "")[:300], "ok": new in (task.get("inputs") or "")},
        ]
    presets = {(p["scope"], p["rule"]): p["value"] for p in got["presets"]}
    checks.append({"case": "income: the answered preset is stored and the dialog changed it to no", "saved": got["presets"],
                   "ok": presets.get((f"income|{params['tax']}|4201", "excluded")) == "no"})
    checks.append({"case": "bank: the grouped answer is stored for the account", "saved": got["presets"],
                   "ok": any(rule == "grouped" and value == "yes" and scope.startswith("bank|") for (scope, rule), value in presets.items())})
    checks.append({"case": "nothing went to recognition", "saved": got["ocr"], "ok": got["ocr"] == params["ocr"]})
    return checks
