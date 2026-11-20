"""流程用例：ERP「从连接添加」的 MR.ERP 标签页（公司接入第 3 批，2026-11-15）。

备数：一台在用电脑报一份 Express 账套（让选择窗有两种来源、出标签）；一条 MR.ERP 连接，目录快照里三家：
A 有税号、两个年度账套（去年、今年）；B 没税号、一个年度；C 已建公司（按 `mrerp.catalog.identity` 认的同一家）。

选择窗的 MR.ERP / Express / 云端 Express 三个标签一律显示（11-20 用户定，没数据的标签是空状态）。
`MRERP_LEDGER_ENABLED` 打开时：点 MR.ERP，一家一行（A 的两个年度归一行、第二行写各年度），
C 的勾选框禁用、标「已添加」；勾 A 添加 → 建出一家接 MR.ERP 的公司，公司标识是 A 的税号，记下的账套是覆盖今天的那本；再开选择窗 A 也标「已添加」。
开关关着（现在线上）时：MR.ERP 标签照常显示，点进去是空状态，MR.ERP 目录里的三家一家也不出现——CI 跑的是这一面；打开的那一面在开关提交前用开关已开的源码跑。
"""
from __future__ import annotations

import json
import random

from desk_list import IDLE

# 三个标签一律显示、顺序同 CC 选择公司（11-20 用户定）；云端 Express 按站点语言（流程站点是中文）
TABS = ["MR.ERP", "Express", "云端 Express"]
TITLE = "ERP「从连接添加」MR.ERP 标签页：一家一行、已添加禁用、勾选建公司（公司接入第 3 批）"


def _tax(rng):
    """过泰国税号校验位的合成税号（校验位不对的会被当成没税号，按 MR.ERP 站点 + 名称认公司）。"""
    body = "01055" + "".join(str(rng.randint(0, 9)) for _ in range(7))
    return body + str((11 - sum(int(d) * (13 - i) for i, d in enumerate(body)) % 11) % 10)


def prepare(lab, manifest):
    rng = random.Random()
    tag = f"{rng.randint(0, 0xFFFFFF):06X}"
    tax_a, tax_c, tax_x = _tax(rng), _tax(rng), _tax(rng)
    made = lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
from frappe.utils import nowdate
from pearnly_core.cc_desktop import documents
from pearnly_core.mrerp import catalog as core_catalog
from pearnly_erp_portal import mrerp
from pearnly_erp_portal.mrerp import catalog
year = int(nowdate()[:4])
device = documents.new(documents.DEVICE, "PCD-LABM{tag}", {{"device_label": "LAB {tag}", "token_digest": "lab-m{tag}",
                                                          "companion_version": "1.12.21"}})
documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": "labm-{tag}", "label": "บริษัท เอ็กซ์ {tag} จำกัด",
              "company": "บริษัท เอ็กซ์ {tag} จำกัด", "tax_id": {tax_x!r}, "branch": "00000", "path": "C:/LAB/labm-{tag}", "root": "C:/LAB",
              "period_start": f"{{year}}-01-01", "period_end": f"{{year}}-12-31", "writable": 1}})
rows = [
    {{"comidyear": "{tag}25", "seldb": "db{tag}a1", "name": "A{tag}" + str(year - 1), "company": "A{tag}",
      "company_name": "1010-01-000006 บริษัท เอ็มอาร์ {tag} จำกัด", "tax_id": {tax_a!r},
      "period_start": f"{{year - 1}}-01-01", "period_end": f"{{year - 1}}-12-31"}},
    {{"comidyear": "{tag}26", "seldb": "db{tag}a2", "name": "A{tag}" + str(year), "company": "A{tag}",
      "company_name": "1010-01-000006 บริษัท เอ็มอาร์ {tag} จำกัด", "tax_id": {tax_a!r},
      "period_start": f"{{year}}-01-01", "period_end": f"{{year}}-12-31"}},
    {{"comidyear": "{tag}B", "seldb": "db{tag}b", "name": "B{tag}" + str(year), "company": "B{tag}",
      "company_name": "ร้าน บี {tag}", "period_start": f"{{year}}-01-01", "period_end": f"{{year}}-12-31"}},
    {{"comidyear": "{tag}C", "seldb": "db{tag}c", "name": "C{tag}" + str(year), "company": "C{tag}",
      "company_name": "บริษัท ซี {tag} จำกัด", "tax_id": {tax_c!r},
      "period_start": f"{{year}}-01-01", "period_end": f"{{year}}-12-31"}},
]
conn = frappe.get_doc({{"doctype": "ERP MRERP Connection", "system_url": "https://lab-{tag.lower()}.mrerp.test", "enabled": 1,
                       "catalog_json": json.dumps(core_catalog.account_sets(rows), ensure_ascii=False)}}).insert(ignore_permissions=True)
used = frappe.get_doc({{"doctype": "ERP Company", "company_name": "บริษัท ซี {tag} จำกัด", "tax_id": {tax_c!r}, "branch": "00000",
                       "is_active": 1, "express_enabled": 0, "mrerp_enabled": 1, "mrerp_connection": conn.name,
                       "mrerp_account_set": "{tag}C:db{tag}c", "mrerp_company": catalog.identity(conn.name, {{"tax_id": {tax_c!r}}})}}
                      ).insert(ignore_permissions=True)
frappe.db.commit()
from pearnly_erp_portal.express import companies
frappe.cache.delete_value(companies.PENDING_KEY)
print(json.dumps({{"device": device.name, "connection": conn.name, "used": used.name, "on": mrerp.enabled(), "year": year}}))
""")
    return {"tag": tag, "tax_a": tax_a, "tax_c": tax_c, "tax_x": tax_x, **made, "cases": []}


# 打开选择窗：新报来的账套会自动弹；没弹就点公司页「从连接添加」
_OPEN = ("let d=null; for(let k=0;k<12&&!(d=document.querySelector('.modal.show [data-fieldname=items]'));k++) await new Promise(r=>setTimeout(r,250)); "
         "if(!d){ const b=[...document.querySelectorAll('.page-head button')].find(e=>e.offsetParent&&e.innerText.trim()==='从连接添加'); "
         "if(!b) return {error:'no add-from-connections button'}; b.click(); "
         "for(let k=0;k<60&&!(d=document.querySelector('.modal.show [data-fieldname=items]'));k++) await new Promise(r=>setTimeout(r,250)); } "
         "await idle(); await new Promise(r=>setTimeout(r,600)); ")
_TABS = "const tabs=[...document.querySelectorAll('.modal.show .form-tabs .nav-link')].filter(e=>e.offsetParent).map(e=>e.innerText.trim()); "


def _rows(tag):
    return ("const list=document.querySelector('.modal.show [data-fieldname=items]'); "
            f"const rows=list?[...list.querySelectorAll('.checkbox')].filter(e=>e.innerText.includes({tag})):[]; "
            "const mine=rows.map(e=>e.innerText.trim().replace(/\\s+/g,' ')); "
            "const disabled=rows.filter(e=>e.querySelector('input').disabled).map(e=>e.innerText.trim().replace(/\\s+/g,' ')); ")


def _mrerp_tab():
    return ("const t=[...document.querySelectorAll('.modal.show .form-tabs .nav-link')].find(e=>e.offsetParent&&e.innerText.trim()==='MR.ERP'); "
            "if(t){ t.click(); await new Promise(r=>setTimeout(r,400)); await idle(); await new Promise(r=>setTimeout(r,500)); } ")


def steps(params, camera_code=None):
    tag = json.dumps(params["tag"])
    return [
        {"name": "打开选择窗看标签", "shot": True, "js": IDLE +
         "frappe.set_route('pearnly-company'); await idle(); " + _OPEN + _TABS + _mrerp_tab() + _rows(tag) +
         "return {tabs, mine, disabled};"},
        {"name": "勾 A 并添加", "js": IDLE + _rows(tag) +
         "const a=rows.find(e=>e.innerText.includes('เอ็มอาร์')); if(!a) return {skipped: true}; a.querySelector('input').click(); "
         "await new Promise(r=>setTimeout(r,300)); document.querySelector('.modal.show .btn-primary').click(); "
         "for(let k=0;k<40&&document.querySelector('.modal.show [data-fieldname=items]');k++) await new Promise(r=>setTimeout(r,250)); "
         "await idle(); await new Promise(r=>setTimeout(r,800)); "
         "const alerts=[...document.querySelectorAll('.alert-message, .desk-alert')].map(e=>e.innerText.trim()); "
         "return {closed: !document.querySelector('.modal.show [data-fieldname=items]'), alerts};"},
        {"name": "再开选择窗", "js": IDLE +
         "document.querySelector('.modal.show .btn-modal-close')?.click(); await new Promise(r=>setTimeout(r,400)); " + _OPEN
         + _mrerp_tab() + _rows(tag) + "document.querySelector('.modal.show .btn-modal-close')?.click(); return {mine, disabled};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    first, added, again = by.get("打开选择窗看标签") or {}, by.get("勾 A 并添加") or {}, by.get("再开选择窗") or {}
    mine = first.get("mine") or []
    if not params.get("on"):
        return [{"case": "开关关着：MR.ERP 标签照常显示（三个标签一律显示），点进去 MR.ERP 目录里的公司一家也不出现", "seen": first,
                 "ok": (first.get("tabs") or []) == TABS and [text for text in mine if "เอ็กซ์" not in text] == []}]
    a = [text for text in mine if "เอ็มอาร์" in text]
    c = [text for text in mine if "ซี" in text]
    year = params["year"]
    return [
        {"case": "开关打开：选择窗有 MR.ERP、Express、云端 Express 三个标签", "seen": first.get("tabs"),
         "ok": (first.get("tabs") or []) == TABS},
        {"case": "MR.ERP 一家一行（A 的两个年度归一行、第二行写各年度；B、C 各一行），名称去掉内部编号", "seen": mine,
         "ok": len(mine) == 3 and len(a) == 1 and str(year - 1) in a[0] and str(year) in a[0] and "1010-01" not in a[0]},
        {"case": "已建公司的 C：勾选框禁用、标「已添加」", "seen": first,
         "ok": len(c) == 1 and "已添加" in c[0] and first.get("disabled") == c},
        {"case": "勾 A 添加：对话框关闭、提示已新建", "seen": added,
         "ok": bool(added.get("closed")) and any("已新建" in text for text in added.get("alerts") or [])},
        {"case": "再开选择窗：A 也标「已添加」、禁用", "seen": again,
         "ok": len([t for t in again.get("disabled") or [] if "เอ็มอาร์" in t]) == 1},
    ]


def verify(lab, manifest, params, camera_code=None):
    if not params.get("on"):
        return []
    got = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Company", filters={{"tax_id": {params['tax_a']!r}}},
                      fields=["name", "company_name", "mrerp_enabled", "mrerp_connection", "mrerp_account_set", "mrerp_company",
                              "express_enabled"], limit_page_length=5)
print(json.dumps({{"rows": rows}}, default=str))
""")
    rows = got["rows"]
    tag = params["tag"]
    return [
        {"case": "建出一家接 MR.ERP 的公司：标识是 A 的税号、记的是覆盖今天的那本、名称去掉内部编号", "saved": rows,
         "ok": len(rows) == 1 and rows[0]["mrerp_enabled"] == 1 and rows[0]["express_enabled"] == 0
         and rows[0]["mrerp_connection"] == params["connection"] and rows[0]["mrerp_company"] == f"tax:{params['tax_a']}"
         and rows[0]["mrerp_account_set"] == f"{tag}26:db{tag}a2" and rows[0]["company_name"] == f"บริษัท เอ็มอาร์ {tag} จำกัด"},
    ]


def cleanup(lab, manifest, params):
    lab.python(manifest["site"], f"""
frappe.set_user("Administrator")
names = frappe.get_all("ERP Company", filters={{"tax_id": ["in", [{params['tax_a']!r}, {params['tax_c']!r}, {params['tax_x']!r}]]}}, pluck="name")
for doctype, field in (("ERP Warehouse", "company"), ("ERP POS Terminal", "company")):
    frappe.db.delete(doctype, {{field: ["in", names or [""]]}})
frappe.db.delete("ERP Company", {{"name": ["in", names or [""]]}})
frappe.db.delete("ERP MRERP Connection", {{"name": {params.get('connection', '')!r}}})
frappe.db.delete("Pearnly CC Book", {{"device": {params.get('device', '')!r}}})
frappe.db.delete("Pearnly CC Device", {{"name": {params.get('device', '')!r}}})
frappe.db.commit()
print("{{}}")
""")
