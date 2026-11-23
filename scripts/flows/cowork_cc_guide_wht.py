"""流程用例：Cowork 版 CC「预扣税申报表」（ภ.ง.ด.3 / 53）引导真页面从菜单到结果（表单业务 wht，不写账、有复核步骤）。

造数据同 `cowork_cc_guide_form_lib`（本对话公司这个月没有费用票：草稿是空的也照出）。页面上（不经模型）：业务菜单「预扣税申报表」→
月份填本月 → 提交 → 复核卡 → 点确定那一下 → 结果卡：已就绪、工作簿在对话里。库里：引导运行 done、同意来自页面；草稿的付款公司就是
本对话公司；草稿删了以后按页面同一个取卡接口读结果是未完成、不再给工作簿（结果每次取卡时现算）。
"""
from __future__ import annotations

import cowork_cc_guide_form_lib as lib

TITLE = "Cowork 版 CC 预扣税引导：菜单 → 本月 → 复核卡 → 确定 → 结果卡已就绪带工作簿；草稿删了取卡时结果未完成"


def prepare(lab, manifest):
    # 预扣税按本对话公司的 13 位税号出草稿：造的公司补上税号（同目录里这本账套的税号；`lab.set_fields` 记原值、格结束时还原）
    params = lib.prepare(lab, manifest, "", "wht")
    lab.set_fields(manifest["site"], "Cowork Company", params["company"], {"tax_id": lib.ledger.MY_TAX})
    return {**params, "tax_id": lib.ledger.MY_TAX}


def steps(params, camera_code=None):
    return [lib.open_conversation(params), lib.open_business("wht"), lib.answer({"period": lib.MONTH}), lib.confirm()]


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    opened, answered, done = (by.get(k) or {} for k in ("点菜单", "作答", "确定"))
    return [
        {"case": "业务菜单有预扣税，点了出引导卡，只问月份", "seen": opened,
         "ok": bool(opened.get("clicked")) and opened.get("business") == "wht" and opened.get("keys") == ["period"]},
        {"case": "月份提交后到复核卡", "seen": answered, "ok": answered.get("status") == "reviewing"},
        {"case": "复核卡上确定 → 结果卡已就绪、带工作簿", "seen": done,
         "ok": bool(done.get("enabled")) and done.get("status") == "done" and done.get("result") == "ready" and bool(done.get("files"))},
    ]


def verify(lab, manifest, params, camera_code=None):
    out = lab.python(manifest["site"], f"""
from pearnly_core.assistant.guide import run
frappe.set_user({lib.USER!r})
runs = frappe.get_all("Pearnly Assistant Guide Run", filters={{"session_id": {params['session']!r}, "business": "wht"}},
                      fields=["name", "status", "consent"], limit_page_length=3)
drafts = frappe.get_all("Cowork Tax Draft", filters={{"kind": "withholding", "company_tax_id": {params.get('tax_id') or ''!r},
                        "period": {lib.MONTH!r}}}, pluck="name", limit_page_length=5)
gone = {{}}
if runs and drafts:
    for name in drafts:
        frappe.delete_doc("Cowork Tax Draft", name, ignore_permissions=True, force=True)
    frappe.db.commit()
    found = run.view_of(runs[0].name).get("result") or {{}}
    gone = {{"status": found.get("status"), "files": found.get("files")}}
print(json.dumps({{"runs": runs, "drafts": drafts, "gone": gone}}, default=str))
""")
    runs, drafts = out.get("runs") or [], out.get("drafts") or []
    return [
        {"case": "引导运行办完（done），同意是页面上这一下", "seen": runs,
         "ok": len(runs) == 1 and runs[0].get("status") == "done" and str(runs[0].get("consent") or "").startswith("guide:")},
        {"case": "草稿一份，付款公司就是本对话公司", "seen": drafts, "ok": len(drafts) == 1},
        {"case": "结果现算：草稿删了以后取卡，结果是未完成、不再给工作簿", "seen": out.get("gone"),
         "ok": (out.get("gone") or {}).get("status") == "failed" and not (out.get("gone") or {}).get("files")},
    ]


def cleanup(lab, manifest, params):
    lib.cleanup(lab, manifest, params, f"""
for name in frappe.get_all("Cowork Tax Draft", filters={{"company_tax_id": {params.get('tax_id') or 'none'!r}}}, pluck="name", limit_page_length=20):
    frappe.delete_doc("Cowork Tax Draft", name, ignore_permissions=True, force=True)
""")
