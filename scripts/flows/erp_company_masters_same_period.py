"""流程用例：同一家公司同期两份 Express 账套（C 盘副本 + 服务器年度账套）时，主档同步不默认读第一份（11-16 按类缺陷 8①）。

备一台 CC 桌面版电脑报同一税号、同一会计期间的两份账套（C 盘副本、S 盘年度文件夹），和一家接 Express 的公司；后台照自动同步
那样发起一次：应停下、状态「失败」、原因「选择账套」，不发读取请求。管理员打开这家公司的资料页：Express 一节出「账套」下拉，
两份都列出（账套名 · 位置，分得清副本）；选 S 盘那份。断言：选完下拉消失，记进写单同一份按公司 × 会计年度的记忆
（`express.choices` 的 account_set），公司排回同步（不再停在「选择账套」），再按日期选账套写单也直接用这份、不再问。
用完删掉这台电脑、两份账套、公司和记下的选择。
"""
from __future__ import annotations

import json
import random

from desk_list import IDLE

TITLE = "ERP 公司资料：同期两份 Express 账套没选过时主档同步停下、公司页选一次（按类缺陷 8①）"
# 截住页面画表单用的 FieldGroup（资料页不是对话框）：同 erp_company_settings
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")
FORM = "const form=()=>window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.express_book).pop();"


def _tax(rng):
    return "01055" + "".join(str(rng.randint(0, 9)) for _ in range(8))


def prepare(lab, manifest):
    rng = random.Random()
    tag = f"{rng.randint(0, 0xFFFFFF):06X}"
    tax = _tax(rng)
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
from pearnly_core.cc_desktop import documents
device = documents.new(documents.DEVICE, "PCD-SAME{tag}", {{"device_label": "SAME {tag}", "token_digest": "same-{tag}",
                                                          "companion_version": "1.12.21"}})
for key, path in (("same-{tag}-copy", "C:/Copy/{tag}"), ("same-{tag}-year", "S:/ACC/{tag}/2026")):
    documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": key,
                  "label": "บริษัท สองชุด {tag} จำกัด", "company": "บริษัท สองชุด {tag} จำกัด", "tax_id": {tax!r},
                  "branch": "00000", "path": path, "root": path.split("/")[0], "period_start": "2026-01-01",
                  "period_end": "2026-12-31", "writable": 1}})
company = frappe.get_doc({{"doctype": "ERP Company", "company_name": "บริษัท สองชุด {tag} จำกัด", "tax_id": {tax!r},
                          "branch": "00000", "is_active": 1, "express_enabled": 1}}).insert(ignore_permissions=True)
frappe.db.commit()
# 照自动同步那样发起一次（每分钟推进 / 公司页「同步 Express」同一个入口 `_request`）：没选过 → 停下，不发读取请求
from pearnly_erp_portal.express import masters
masters._record(company.name, masters._request(company.name, fresh=True))
frappe.db.commit()
row = frappe.db.get_value("ERP Company", company.name, ["express_sync_state", "express_sync_note"], as_dict=True)
sent = frappe.db.count("Pearnly CC Request", {{"book_key": ["in", ["same-{tag}-copy", "same-{tag}-year"]]}})
print(json.dumps({{"company": company.name, "device": device.name, "state": row.express_sync_state,
                   "note": row.express_sync_note, "requests": sent}}, default=str))
""")
    return {"tag": tag, "tax": tax, **made, "cases": []}


def steps(params, camera_code=None):
    company = json.dumps(params["company"])
    tag = json.dumps(params["tag"])
    return [
        {"name": "打开公司资料", "shot": True, "js": IDLE + CAPTURE + FORM +
         f"frappe.set_route('pearnly-company'); await idle(); await new Promise(r=>setTimeout(r,800)); frappe.set_route('pearnly-company', {company});"
         "for(let k=0;k<60&&!form();k++) await new Promise(r=>setTimeout(r,250)); await idle(); await new Promise(r=>setTimeout(r,600));"
         "const f=form(); if(!f) return {error:'no express section'};"
         "const c=f.fields_dict.express_book_choice; const note=f.fields_dict.express_sync_note;"
         "return {choice: !!(c&&c.$wrapper.is(':visible')), label: c?c.df.label:'',"
         " options: c?[...c.$wrapper.find('option')].map(o=>({value:o.value, text:o.textContent.trim()})).filter(o=>o.value):[],"
         " note: note?String(note.get_value()||''):''};"},
        {"name": "选服务器那份", "shot": True, "js": IDLE + FORM +
         "const f=form(); const c=f&&f.fields_dict.express_book_choice; if(!c) return {error:'no choice field'};"
         f"await c.set_value('same-'+{tag}+'-year'); "
         "for(let k=0;k<40;k++){await new Promise(r=>setTimeout(r,250)); const g=form(); if(g&&g!==f&&!g.fields_dict.express_book_choice) break;}"
         "await idle(); await new Promise(r=>setTimeout(r,600)); const g=form();"
         "return {gone: !!g && g!==f && !g.fields_dict.express_book_choice};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    seen, picked = by.get("打开公司资料", {}), by.get("选服务器那份", {})
    tag = params["tag"]
    values = sorted(o.get("value") for o in seen.get("options") or [])
    texts = [o.get("text") or "" for o in seen.get("options") or []]
    return [
        {"case": "自动同步停下、原因「选择账套」、没发读取请求",
         "seen": {key: params.get(key) for key in ("state", "note", "requests")},
         "ok": params.get("state") == "failed" and params.get("note") == "express.account_set_several" and params.get("requests") == 0},
        {"case": "公司页出「账套」下拉、两份都列出且带位置分得清", "seen": seen,
         "ok": bool(seen.get("choice")) and values == sorted([f"same-{tag}-copy", f"same-{tag}-year"])
         and any("C:/Copy" in t for t in texts) and any("S:/ACC" in t for t in texts) and seen.get("note") == "选择账套"},
        {"case": "选完下拉消失", "seen": picked, "ok": bool(picked.get("gone"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
from frappe.utils import nowdate
from pearnly_erp_portal.express import choices, target
row = frappe.db.get_value("ERP Company", {params['company']!r}, ["name", "tax_id", "branch", "express_sync_state",
                          "express_sync_note"], as_dict=True)
remembered = frappe.get_all(choices.CHOICE, filters={{"company": row.name, "kind": choices.ACCOUNT_SET}},
                            fields=["subject", "value"], limit_page_length=10)
picked = target.pick(row, nowdate())
print(json.dumps({{"state": row.express_sync_state, "note": row.express_sync_note, "remembered": remembered,
                   "asked": target.masters_choice(row, nowdate()), "pick": (picked["book"] or {{}}).get("key"),
                   "reason": picked["reason"]}}, default=str))
""")
    year = f"same-{params['tag']}-year"
    return [
        {"case": "记进写单同一份按公司 × 会计年度的记忆", "saved": got["remembered"],
         "ok": [(r["subject"], r["value"]) for r in got["remembered"]] == [("year:2026-01-01", year)]},
        {"case": "排回同步，不再停在「选择账套」", "saved": got,
         "ok": got["asked"] is None and got["note"] != "express.account_set_several"
         and got["state"] in ("queued", "reading", "offline", "outdated")},
        {"case": "写单按日期选账套直接用这份、不再问", "saved": got,
         "ok": got["pick"] == year and got["reason"] in ("", "express.no_writer")},
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
company = {params.get('company', '')!r} or "-"
frappe.db.delete("ERP Express Choice", {{"company": company}})
for doctype in ("ERP Warehouse", "ERP POS Terminal"):
    frappe.db.delete(doctype, {{"company": company}})
frappe.db.delete("ERP Company", {{"name": company}})
frappe.db.delete("Pearnly CC Request", {{"book_key": ["like", "same-{params['tag']}-%"]}})
frappe.db.delete("Pearnly CC Book", {{"device": {params.get('device', '')!r} or "-"}})
frappe.db.delete("Pearnly CC Device", {{"name": {params.get('device', '')!r} or "-"}})
frappe.db.commit()
print("{{}}")
""")
