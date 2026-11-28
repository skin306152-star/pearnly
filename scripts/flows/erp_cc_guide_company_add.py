"""流程用例：ERP 版 CC「添加公司」引导（直写）真页面：备一台电脑报了一份还没有公司的 Express 账套（同公司页「从连接添加」流程
`erp_company_connect` 的造法），菜单点「添加公司」→ 勾这一份 → 复核 → 确定 → 结果卡「成功 · 公司名 · 同步中」；库里真建了这家、
排上主档同步。同步失败那条路径另见 `erp_cc_guide_company_add_failed`（共用本文件的备数与清理）。"""
from __future__ import annotations

import json
import random

import erp_cc_guide_form_lib as lib

TITLE = "ERP 版 CC 添加公司引导（直写）：勾连接里一份账套 → 复核 → 确定 → 结果卡成功带公司名与「同步中」；库里真建了这家并排上同步"


def seed(lab, manifest, tag_prefix="ead"):
    """一台电脑报一份新税号账套；开对话。回参数里带 ``identity``（选项码）、``tax``、``name``、``device``。"""
    rng = random.Random()
    tax = "01055" + "".join(str(rng.randint(0, 9)) for _ in range(8))
    params = lib.prepare(lab, manifest, tag_prefix=tag_prefix, extra=f"""
from pearnly_core.cc_desktop import documents
from pearnly_erp_portal.express import companies as express_companies
from pearnly_erp_portal.masters import cc_service
tag = frappe.generate_hash(length=6).upper()
name = "บริษัท เพิ่ม " + tag + " จำกัด"
frappe.set_user("Administrator")
device = documents.new(documents.DEVICE, "PCD-ADD" + tag, {{"device_label": "LAB ADD " + tag, "token_digest": "lab-add-" + tag,
                                                          "companion_version": "1.12.21"}})
documents.new(documents.BOOK, frappe.generate_hash(length=20), {{"device": device.name, "book_key": "lab-add-" + tag, "label": name,
              "company": name, "tax_id": {tax!r}, "branch": "00000", "path": "C:/LAB/add-" + tag, "root": "C:/LAB",
              "period_start": "2026-01-01", "period_end": "2026-12-31", "writable": 1}})
frappe.db.commit()
frappe.cache.delete_value(express_companies.PENDING_KEY)
frappe.set_user({lib.USER!r})
rows = cc_service.pending(tag, None, 0, 5)["rows"]
extra = {{"device": device.name, "name": name, "identity": rows[0]["identity"] if rows else ""}}
""")
    return {**params, "tax": tax}


def prepare(lab, manifest):
    return seed(lab, manifest)


def steps(params, camera_code=None):
    return [*lib.steps_head(params, "company_add"), lib.answer({"companies": params["identity"]}), lib.confirm(shot="1-result")]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, done = by.get("点菜单") or {}, by.get("确定") or {}
    text = done.get("text") or ""
    return [
        {"case": "备好的账套在连接目录里（选项码）", "seen": params.get("identity"), "ok": bool(params.get("identity"))},
        {"case": "业务菜单有「添加公司」，点了出引导卡、先问勾哪几家", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "company_add" and opened.get("keys") == ["companies"]},
        {"case": "确定 → 结果卡「成功」、公司名、主档还在同步写「同步中」", "seen": done,
         "ok": done.get("status") == "done" and done.get("result") == "entered" and "成功" in text and params["name"] in text
         and "同步中" in text},
        lib.no_internal_case(done),
    ]


def company_row(lab, manifest, params) -> dict:
    return lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP Company", {{"tax_id": {params['tax']!r}}}, ["name", "company_name", "express_enabled", "express_sync_state"],
                          as_dict=True) or {{}}
print(json.dumps(row, default=str))
""")


def verify(lab, manifest, params, camera_code=None):
    runs = lib.run_row(lab, manifest, params, "company_add")
    row = company_row(lab, manifest, params)
    return [lib.done_case(runs),
            {"case": "库里真建了这家（接 Express）并排上主档同步", "seen": row,
             "ok": row.get("company_name") == params["name"] and str(row.get("express_enabled")) == "1"
             and row.get("express_sync_state") in ("queued", "reading", "syncing")}]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params, extra="""
frappe.set_user("Administrator")
names = frappe.get_all("ERP Company", filters={"tax_id": params.get("tax") or "-"}, pluck="name")
for doctype in ("ERP Warehouse", "ERP POS Terminal"):
    frappe.db.delete(doctype, {"company": ["in", names or [""]]})
frappe.db.delete("ERP Company", {"name": ["in", names or [""]]})
frappe.db.delete("Pearnly CC Book", {"device": params.get("device") or "-"})
frappe.db.delete("Pearnly CC Device", {"name": params.get("device") or "-"})
frappe.set_user(%r)
""" % lib.USER)
