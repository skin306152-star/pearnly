"""流程用例：ERP 公司页「从连接添加」（公司接入重做第 2 批，2026-11-14，用户定 A 方案）。

备一台「在用」的 CC 桌面版电脑（版本够、令牌在用）报了两份 Express 账套：一份还没有公司（新税号），一份的税号与一家手工建的、
没接账本的公司相同。管理员在公司列表点「从连接添加」，原生对话框里两份都列出来；照人的做法勾这两份、点「添加」。
断言：页面提示已新建；库里新税号那份建了一家接 Express 的公司（没有「接 Express」勾选，来源决定）并排上同步，手工那家被接上
而不是另建一家；两家都不再出现在待勾名单里。用完删掉这台电脑、两份账套和建出来的公司。

11-15（主会话，照 CC「选择公司」弹窗）：再报两份——一份的公司已停用、一份的公司已在用。选择窗把连接上报的全部列出：右上有刷新，
每行第二行是「税号 · 总部 · 账套文件夹」；已在用的那行勾选框禁用、标「已添加」；停用的那行可勾、标「已停用」，勾了就是重新启用那家
（不另建）。左上角公司名下拉里有同一项「从连接添加」。右上刷新请在线电脑立刻整份重报（`cc_service.rescan`）；实验栈的电脑
不在线，刷新直接显示「CC 桌面版未连接」（在线电脑的下发与回执由 tests/test_cc_desktop_channel.py 守）。
"""
from __future__ import annotations

import json
import random

from desk_list import IDLE

TITLE = "ERP 公司页「从连接添加」：勾账套建公司、手工同税号公司直接接上（公司接入重做第 2 批）"


def _tax(rng):
    return "01055" + "".join(str(rng.randint(0, 9)) for _ in range(8))


def prepare(lab, manifest):
    rng = random.Random()
    tag = f"{rng.randint(0, 0xFFFFFF):06X}"
    fresh, manual, by_cc, stopped, used = _tax(rng), _tax(rng), _tax(rng), _tax(rng), _tax(rng)
    other = f"{rng.randint(0, 0xFFFFFF):06X}"  # CC 那份用另一个标记：网页步骤只勾带 tag 的两份
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
# 机器通道的表只经本域写（`cc_desktop.documents`）：同电脑配对、心跳报名单时的写法
from pearnly_core.cc_desktop import documents
device = documents.new(documents.DEVICE, "PCD-LAB{tag}", {{"device_label": "LAB {tag}", "token_digest": "lab-{tag}",
                                                         "companion_version": "1.12.21"}})
for key, name, tax in (("lab-{tag}-a", "บริษัท ต่อใหม่ {tag} จำกัด", {fresh!r}), ("lab-{tag}-b", "ห้าง มือ {tag}", {manual!r}),
                       ("lab-{tag}-c", "บริษัท ซีซี {other} จำกัด", {by_cc!r}), ("lab-{tag}-d", "บริษัท หยุด {tag} จำกัด", {stopped!r}),
                       ("lab-{tag}-e", "บริษัท ใช้อยู่ {tag} จำกัด", {used!r})):
    documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": key, "label": name,
                  "company": name, "tax_id": tax, "branch": "00000", "path": "C:/LAB/" + key, "root": "C:/LAB",
                  "period_start": "2026-01-01", "period_end": "2026-12-31", "writable": 1}})

hand = frappe.get_doc({{"doctype": "ERP Company", "company_name": "ห้าง มือ {tag}", "tax_id": {manual!r}, "branch": "00000",
                       "is_active": 1, "express_enabled": 0}}).insert(ignore_permissions=True)
off = frappe.get_doc({{"doctype": "ERP Company", "company_name": "บริษัท หยุด {tag} จำกัด", "tax_id": {stopped!r}, "branch": "00000",
                      "is_active": 0, "express_enabled": 1}}).insert(ignore_permissions=True)
frappe.get_doc({{"doctype": "ERP Company", "company_name": "บริษัท ใช้อยู่ {tag} จำกัด", "tax_id": {used!r}, "branch": "00000",
                "is_active": 1, "express_enabled": 1}}).insert(ignore_permissions=True)
frappe.db.commit()
from pearnly_erp_portal.express import companies
frappe.cache.delete_value(companies.PENDING_KEY)
print(json.dumps({{"device": device.name, "hand": hand.name, "off": off.name}}))
""")
    return {"tag": tag, "other": other, "fresh": fresh, "manual": manual, "by_cc": by_cc, "stopped": stopped, "used": used,
            **made, "cases": []}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    return [
        {"name": "授权后自动弹出选择窗", "shot": True, "js": IDLE +
         # 新报来的账套本人还没看过：打开 ERP 就自动弹（用户定 A 方案），不用点按钮
         "frappe.set_route('pearnly-company'); await idle(); "
         "let d=null; for(let k=0;k<40&&!(d=document.querySelector('.modal.show [data-fieldname=items] .checkbox'));k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,600)); d=document.querySelector('.modal.show [data-fieldname=items]'); "
         f"const rows=d?[...d.querySelectorAll('.checkbox')].filter(e=>e.innerText.includes({tag})):[]; "
         "const mine=rows.map(e=>e.innerText.trim()); "
         "const disabled=rows.filter(e=>e.querySelector('input').disabled).map(e=>e.innerText.trim()); "
         "const head=[...document.querySelectorAll('.modal.show .modal-actions button')].filter(e=>e.offsetParent).length; "
         "return {auto: !!d, title: document.querySelector('.modal.show .modal-title')?.innerText.trim(), mine, disabled, head};"},
        {"name": "关掉再从按钮打开", "js": IDLE +
         "document.querySelector('.modal.show .btn-modal-close')?.click(); await new Promise(r=>setTimeout(r,600)); "
         "const b=[...document.querySelectorAll('.page-head button')].find(e=>e.offsetParent&&e.innerText.trim()==='从连接添加'); "
         "if(!b) return {error:'no add-from-connections button'}; b.click(); "
         "let d=null; for(let k=0;k<60&&!(d=document.querySelector('.modal.show [data-fieldname=items] .checkbox'));k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,600)); d=document.querySelector('.modal.show [data-fieldname=items]'); "
         f"const mine=d?[...d.querySelectorAll('.checkbox')].map(e=>e.innerText.trim()).filter(t=>t.includes({tag})):[]; "
         "return {opened: !!d, mine};"},
        {"name": "勾两份并添加", "js": IDLE +
         "const d=document.querySelector('.modal.show [data-fieldname=items]'); if(!d) return {error:'no dialog'}; "
         f"for (const box of d.querySelectorAll('.checkbox')) {{ if (box.innerText.includes({tag})) box.querySelector('input').click(); }} "
         "await new Promise(r=>setTimeout(r,300)); document.querySelector('.modal.show .btn-primary').click(); "
         "for(let k=0;k<40&&document.querySelector('.modal.show [data-fieldname=items]');k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,800)); "
         "const alerts=[...document.querySelectorAll('.alert-message, .desk-alert')].map(e=>e.innerText.trim()); "
         f"return {{closed: !document.querySelector('.modal.show [data-fieldname=items]'), alerts, listed: document.body.innerText.includes({tag})}};"},
        {"name": "左上角公司名下拉里有从连接添加", "js": IDLE +
         "const head=document.querySelector('.sidebar-header'); if(!head) return {error:'no sidebar header'}; head.click(); "
         "await new Promise(r=>setTimeout(r,600)); "
         # 原生 `frappe.ui.create_menu` 画的菜单：按文字找看得见的那一项，点它所在的菜单项
         "const leaf=[...document.querySelectorAll('body *')].find(e=>e.offsetParent&&!e.children.length&&e.textContent.trim()==='从连接添加'"
         "&&!e.closest('.page-head')); "
         "if(!leaf) return {found:false}; (leaf.closest('[class*=menu-item], li, a, button')||leaf).click(); "
         "let d=null; for(let k=0;k<40&&!(d=document.querySelector('.modal.show [data-fieldname=items]'));k++) await new Promise(r=>setTimeout(r,250)); "
         "const ok=!!d; document.querySelector('.modal.show .btn-modal-close')?.click(); await new Promise(r=>setTimeout(r,400)); "
         "return {found:true, opened: ok};"},
        {"name": "刷新：没有在线电脑时显示未连接", "js": IDLE +
         # 实验栈里那台电脑从没心跳过（不在线）：刷新不下发、不干等，直接显示「CC 桌面版未连接」
         "const b=[...document.querySelectorAll('.page-head button')].find(e=>e.offsetParent&&e.innerText.trim()==='从连接添加'); "
         "if(!b) return {error:'no add-from-connections button'}; b.click(); "
         "let d=null; for(let k=0;k<40&&!(d=document.querySelector('.modal.show [data-fieldname=items]'));k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); const refresh=document.querySelector('.modal.show .modal-actions button:not(.btn-modal-close):not(.btn-modal-minimize)'); "
         "if(!refresh) return {error:'no refresh button'}; const t0=Date.now(); refresh.click(); "
         "let text=''; for(let k=0;k<40;k++){ await new Promise(r=>setTimeout(r,250)); "
         "text=document.querySelector('.modal.show [data-fieldname=empty]')?.innerText.trim()||''; if(text) break; } "
         "const took=Date.now()-t0; document.querySelector('.modal.show .btn-modal-close')?.click(); await new Promise(r=>setTimeout(r,400)); "
         "return {text, took};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    auto, opened, done = by.get("授权后自动弹出选择窗") or {}, by.get("关掉再从按钮打开") or {}, by.get("勾两份并添加") or {}
    mine = auto.get("mine") or []
    used = [text for text in mine if "ใช้อยู่" in text]
    stopped = [text for text in mine if "หยุด" in text]
    menu = by.get("左上角公司名下拉里有从连接添加") or {}
    return [
        {"case": "打开 ERP 自动弹「选择要用的公司」，连接上报的四份都列出（新税号、手工同税号、已停用、已在用），右上有刷新与关闭",
         "seen": auto,
         "ok": bool(auto.get("auto")) and auto.get("title") == "选择要用的公司" and len(mine) == 4 and auto.get("head", 0) >= 2},
        {"case": "每行第二行是「税号 · 总部 · 账套文件夹」", "seen": mine,
         "ok": all(" · 总部 · LAB" in text for text in mine)},
        {"case": "已在用的那行勾选框禁用、标「已添加」；已停用的那行可勾、标「已停用」", "seen": auto,
         "ok": len(used) == 1 and "已添加" in used[0] and auto.get("disabled") == used
         and len(stopped) == 1 and "已停用" in stopped[0]},
        {"case": "关掉后公司页「从连接添加」照样打开同一个选择窗", "seen": opened,
         "ok": bool(opened.get("opened")) and len(opened.get("mine") or []) == 4},
        {"case": "左上角公司名下拉里的「从连接添加」打开同一个选择窗", "seen": menu,
         "ok": bool(menu.get("found")) and bool(menu.get("opened"))},
        {"case": "刷新时没有在线电脑：不干等，直接显示「CC 桌面版未连接」", "seen": by.get("刷新：没有在线电脑时显示未连接"),
         "ok": (by.get("刷新：没有在线电脑时显示未连接") or {}).get("text") == "CC 桌面版未连接"
         and (by.get("刷新：没有在线电脑时显示未连接") or {}).get("took", 99999) < 5000},
        {"case": "添加后对话框关闭、提示已新建、列表里看得到", "seen": done,
         "ok": bool(done.get("closed")) and any("已新建" in a for a in done.get("alerts") or []) and bool(done.get("listed"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Company", filters={{"tax_id": ["in", [{params['fresh']!r}, {params['manual']!r}]]}},
                      fields=["name", "tax_id", "express_enabled", "express_sync_state"], limit_page_length=10)
stopped = frappe.get_all("ERP Company", filters={{"tax_id": {params['stopped']!r}}}, fields=["name", "is_active"])
from pearnly_erp_portal.masters import cc_service, cc_tools
left = [i for i in cc_service.pending_identities() if {params['fresh']!r} in i or {params['manual']!r} in i]
# CC 对话里同一个动作（ADR-027）：管理员用 CC 工具列出待添加的公司、选中第三份添加
frappe.set_user("admin@erp.regression")
listed = json.loads(cc_tools.connection_companies({{"source": "express", "search": {params['other']!r}}}))
picked = [{{"connection": c["connection"], "account_set_key": c["account_set_key"]}} for c in listed["companies"]]
added = json.loads(cc_tools.add_from_connections({{"companies": picked}})) if picked else {{}}
frappe.db.commit()
frappe.set_user("Administrator")
by_cc = frappe.get_all("ERP Company", filters={{"tax_id": {params['by_cc']!r}}}, fields=["name", "express_enabled"])
print(json.dumps({{"rows": rows, "left": left, "listed": listed, "added": added, "by_cc": by_cc, "stopped": stopped}}, default=str))
""")
    by_tax = {}
    for row in got["rows"]:
        by_tax.setdefault(row["tax_id"], []).append(row)
    fresh, manual = by_tax.get(params["fresh"], []), by_tax.get(params["manual"], [])
    return [
        {"case": "新税号那份建了一家接 Express 的公司、已排同步", "saved": fresh,
         "ok": len(fresh) == 1 and fresh[0]["express_enabled"] == 1 and fresh[0]["express_sync_state"] in ("queued", "reading", "offline")},
        {"case": "手工同税号的那家被接上，没另建一家", "saved": manual,
         "ok": len(manual) == 1 and manual[0]["name"] == params["hand"] and manual[0]["express_enabled"] == 1},
        {"case": "两份都不在待勾名单里了", "saved": got["left"], "ok": got["left"] == []},
        {"case": "勾了已停用的那家：重新启用它，没另建一家", "saved": got["stopped"],
         "ok": [(row["name"], row["is_active"]) for row in got["stopped"]] == [(params["off"], 1)]},
        {"case": "CC 工具列出同一份目录里还没添加的那家（ADR-027：网页动作 CC 也能办）", "saved": got["listed"],
         "ok": [c["account_set_key"] for c in got["listed"].get("companies") or []] == [f"{params['by_cc']}:00000"]},
        {"case": "CC 工具添加后建出一家接 Express 的公司", "saved": {"added": got["added"], "rows": got["by_cc"]},
         "ok": len(got["by_cc"]) == 1 and got["by_cc"][0]["express_enabled"] == 1
         and [c.get("created") for c in got["added"].get("companies") or []] == [True]},
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
names = frappe.get_all("ERP Company", filters={{"tax_id": ["in", [{params['fresh']!r}, {params['manual']!r}, {params.get('by_cc', '')!r},
                                                         {params.get('stopped', '')!r}, {params.get('used', '')!r}]]}}, pluck="name")
for doctype, field in (("ERP Warehouse", "company"), ("ERP POS Terminal", "company")):
    frappe.db.delete(doctype, {{field: ["in", names or [""]]}})
frappe.db.delete("ERP Company", {{"name": ["in", names or [""]]}})
frappe.db.delete("Pearnly CC Book", {{"device": {params['device']!r}}})
frappe.db.delete("Pearnly CC Device", {{"name": {params['device']!r}}})
frappe.db.commit()
print("{{}}")
""")
