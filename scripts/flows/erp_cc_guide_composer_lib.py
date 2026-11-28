"""ERP 版 CC 直写「先选业务再用输入框上传」（`cc_guide_composer_lib`）的造数据与核库：对话建在数据集第一家公司（本地 Express）、本月、直写；
核这段对话的引导运行与 ERP 对话收单记录（`integrations.intake.receive_chat`：本人、对话来的、方向 = 菜单上点的业务）。"""
from __future__ import annotations

import uuid

import cc_guide_composer_lib as lib
from erp_lib import company

USER = "admin@erp.regression"
steps, judge = lib.steps, lib.judge


def prepare(lab, manifest, business):
    tag = "gcp" + uuid.uuid4().hex[:6]
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.db.set_value("User", {USER!r}, "language", "zh")
frappe.set_user({USER!r})
made = sessions.create(project={company(manifest)!r}, period=frappe.utils.today()[:7], mode="chat")
frappe.db.set_value("Pearnly Assistant Session", {{"session_id": made["id"]}}, "title", {tag!r})
frappe.db.commit()
print(json.dumps({{"session": made["id"]}}))
""")
    params = {**out, "tag": tag, "business": business, "company": company(manifest), "file": f"{tag}.png", "cases": []}
    try:
        return {**params, **lib.configure(lab, manifest)}
    except Exception:
        cleanup(lab, manifest, params)
        raise


def verify(lab, manifest, params, camera_code=None):
    got = lib.guide_rows(lab, manifest, params)
    intake = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP LINE Intake", filters={{"session_id": {params['session']!r}}},
                      fields=["name", "direction", "source", "company", "owner", "status", "request_key"], limit_page_length=20)
print(json.dumps({{"rows": rows}}, default=str))
""")
    rows = intake.get("rows") or []
    return lib.guide_cases(got, params) + [
        {"case": "ERP 那一侧建了对话收单记录：这家公司、本人、来源 CC、方向是菜单上点的业务，请求键是那份文件（不会重复识别、重复扣费）",
         "seen": intake,
         "ok": len(rows) == 1 and rows[0].get("company") == params["company"] and rows[0].get("owner") == USER
         and rows[0].get("direction") == params["business"] and str(rows[0].get("source") or "") == "cc"
         and str(rows[0].get("request_key") or "") in (got.get("uploaded") or [])},
    ]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params)
    lab.python(manifest["site"], f"""
from pearnly_core.assistant import sessions
frappe.set_user({USER!r})
names = frappe.get_all("ERP LINE Intake", filters={{"session_id": {params.get('session', '')!r}}}, pluck="name", limit_page_length=20)
if {params.get('session', '')!r} and frappe.db.exists("Pearnly Assistant Session", {{"session_id": {params.get('session', '')!r}}}):
    sessions.delete({params.get('session', '')!r})
frappe.set_user("Administrator")
for name in names:
    frappe.delete_doc("ERP LINE Intake", name, ignore_permissions=True, force=True)
frappe.db.commit()
print("{{}}")
""")
