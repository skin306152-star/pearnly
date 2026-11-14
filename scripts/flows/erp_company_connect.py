"""流程用例：ERP 公司页「从连接添加」（公司接入重做第 2 批，2026-11-14，用户定 A 方案）。

备一台「在用」的 CC 桌面版电脑（版本够、令牌在用）报了两份 Express 账套：一份还没有公司（新税号），一份的税号与一家手工建的、
没接账本的公司相同。管理员在公司列表点「从连接添加」，原生对话框里两份都列出来；照人的做法勾这两份、点「添加」。
断言：页面提示已新建；库里新税号那份建了一家接 Express 的公司（没有「接 Express」勾选，来源决定）并排上同步，手工那家被接上
而不是另建一家；两家都不再出现在待勾名单里。用完删掉这台电脑、两份账套和建出来的公司。
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
    fresh, manual = _tax(rng), _tax(rng)
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
# 机器通道的表只经本域写（`cc_desktop.documents`）：同电脑配对、心跳报名单时的写法
from pearnly_core.cc_desktop import documents
device = documents.new(documents.DEVICE, "PCD-LAB{tag}", {{"device_label": "LAB {tag}", "token_digest": "lab-{tag}",
                                                         "companion_version": "1.12.21"}})
for key, name, tax in (("lab-{tag}-a", "บริษัท ต่อใหม่ {tag} จำกัด", {fresh!r}), ("lab-{tag}-b", "ห้าง มือ {tag}", {manual!r})):
    documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": key, "label": name,
                  "company": name, "tax_id": tax, "branch": "00000", "path": "C:/LAB/" + key, "root": "C:/LAB",
                  "period_start": "2026-01-01", "period_end": "2026-12-31", "writable": 1}})

hand = frappe.get_doc({{"doctype": "ERP Company", "company_name": "ห้าง มือ {tag}", "tax_id": {manual!r}, "branch": "00000",
                       "is_active": 1, "express_enabled": 0}}).insert(ignore_permissions=True)
frappe.db.commit()
from pearnly_erp_portal.express import companies
frappe.cache.delete_value(companies.PENDING_KEY)
print(json.dumps({{"device": device.name, "hand": hand.name}}))
""")
    return {"tag": tag, "fresh": fresh, "manual": manual, **made, "cases": []}


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    return [
        {"name": "授权后自动弹出选择窗", "js": IDLE +
         # 新报来的账套本人还没看过：打开 ERP 就自动弹（用户定 A 方案），不用点按钮
         "frappe.set_route('pearnly-company'); await idle(); "
         "let d=null; for(let k=0;k<40&&!(d=document.querySelector('.modal.show [data-fieldname=items] .checkbox'));k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,600)); d=document.querySelector('.modal.show [data-fieldname=items]'); "
         f"const mine=d?[...d.querySelectorAll('.checkbox')].map(e=>e.innerText.trim()).filter(t=>t.includes({tag})):[]; "
         "return {auto: !!d, title: document.querySelector('.modal.show .modal-title')?.innerText.trim(), mine};"},
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
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    auto, opened, done = by.get("授权后自动弹出选择窗") or {}, by.get("关掉再从按钮打开") or {}, by.get("勾两份并添加") or {}
    return [
        {"case": "打开 ERP 自动弹「选择要用的公司」，列出两份（新税号的与手工同税号的）", "seen": auto,
         "ok": bool(auto.get("auto")) and auto.get("title") == "选择要用的公司" and len(auto.get("mine") or []) == 2},
        {"case": "关掉后公司页「从连接添加」照样打开同一个选择窗", "seen": opened,
         "ok": bool(opened.get("opened")) and len(opened.get("mine") or []) == 2},
        {"case": "添加后对话框关闭、提示已新建、列表里看得到", "seen": done,
         "ok": bool(done.get("closed")) and any("已新建" in a for a in done.get("alerts") or []) and bool(done.get("listed"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Company", filters={{"tax_id": ["in", [{params['fresh']!r}, {params['manual']!r}]]}},
                      fields=["name", "tax_id", "express_enabled", "express_sync_state"], limit_page_length=10)
from pearnly_erp_portal.masters import cc_service
left = [i for i in cc_service.pending_identities() if {params['fresh']!r} in i or {params['manual']!r} in i]
print(json.dumps({{"rows": rows, "left": left}}, default=str))
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
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
names = frappe.get_all("ERP Company", filters={{"tax_id": ["in", [{params['fresh']!r}, {params['manual']!r}]]}}, pluck="name")
for doctype, field in (("ERP Warehouse", "company"), ("ERP POS Terminal", "company")):
    frappe.db.delete(doctype, {{field: ["in", names or [""]]}})
frappe.db.delete("ERP Company", {{"name": ["in", names or [""]]}})
frappe.db.delete("Pearnly CC Book", {{"device": {params['device']!r}}})
frappe.db.delete("Pearnly CC Device", {{"name": {params['device']!r}}})
frappe.db.commit()
print("{{}}")
""")
