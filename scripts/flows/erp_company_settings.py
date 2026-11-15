"""流程用例：ERP 公司资料的「公司类型」「接 Express」（盘点菜单做完整 S0，用户 10-29 批复）。

每套数据打开数据清单里那家公司的资料页：
- 新开户（接了 Express、CC 报过账套）：「接 Express」勾着且只读，有「断开」「同步 Express」，没有「下载 CC 桌面版」；
- 老数据（没接 Express）：「接 Express」没勾、可改，没有断开 / 下载。
两套都把公司类型改成「汽车」保存：库里改成 vehicle，启动数据里当前公司的类型跟着变（页面按它出按台格子）；跑完还原成普通。
"""
from __future__ import annotations

import json

TITLE = "ERP 公司资料：公司类型与接 Express 的三种状态"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")
# 截住页面画表单用的 FieldGroup（资料页不是对话框），再重进一次页面让它用截到的那份画
CAPTURE = ("window.__fgs=window.__fgs||[];if(!frappe.ui.FieldGroup.prototype.__cap){const o=frappe.ui.FieldGroup.prototype.make;"
           "frappe.ui.FieldGroup.prototype.make=function(){window.__fgs.push(this);return o.apply(this,arguments)};frappe.ui.FieldGroup.prototype.__cap=1;}")


def prepare(lab, manifest):
    company = manifest.get("company") or manifest["companies"][0]
    # 页面上把公司类型改成按台：从普通开始，格结束时 run_flow 还原原值（`Lab.set_fields`，页面步骤超时也还原，别影响别的流程）
    lab.set_fields(manifest["site"], "ERP Company", company, {"company_type": "general"})
    return {"company": company, "express": manifest["dataset"] == "new", "cases": []}


def steps(params, camera_code=None):
    company = json.dumps(params["company"])
    return [
        {"name": "打开公司资料", "js": IDLE + CAPTURE +
         f"frappe.set_route('pearnly-company'); await idle(); await new Promise(r=>setTimeout(r,800)); frappe.set_route('pearnly-company', {company});"
         "for(let k=0;k<60&&!window.__fgs.find(f=>f.fields_dict&&f.fields_dict.company_type);k++) await new Promise(r=>setTimeout(r,250)); await idle();"
         "const f=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.company_type).pop(); if(!f) return {error:'no company form'};"
         "const has=n=>!!(f.fields_dict[n]&&f.fields_dict[n].$wrapper.is(':visible'));"
         # 公司接入重做第 2 批（11-14）：没有「接 Express」勾选了，来源决定——接着的公司出「断开」，不接的整节不出
         "return {company_type: f.get_value('company_type'), checkbox: !!f.fields_dict.express_enabled,"
         " disconnect: has('express_disconnect'), download: has('cc_download'),"
         " sync: [...document.querySelectorAll('.page-actions button, .page-actions .btn, .page-actions .dropdown-item, .page-head .dropdown-item')]"
         ".some(b=>(b.textContent||'').includes('Express'))};"},  # 手机宽度下页头按钮收进「…」菜单
        {"name": "改成汽车保存", "js": IDLE +
         "const f=window.__fgs.filter(f=>f.fields_dict&&f.fields_dict.company_type).pop(); if(!f) return {error:'no company form'};"
         "await f.set_value('company_type','vehicle'); await new Promise(r=>setTimeout(r,300));"
         "const b=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!b) return {error:'no save button'}; b.click(); for(let k=0;k<40;k++){await new Promise(r=>setTimeout(r,250)); await idle();"
         " const cur=(frappe.boot.pearnly_companies||[]).find(r=>r.is_current); if(cur&&cur.company_type==='vehicle') break;}"
         "const cur=(frappe.boot.pearnly_companies||[]).find(r=>r.is_current)||{};"
         "return {boot_type: cur.company_type||'', unit_tracking: !!(p.unit_tracking&&p.unit_tracking())};"},
    ]


def judge(params, results, camera_code=None):
    by = {r.get("step"): (r.get("value") or {}) for r in results}
    seen, saved = by.get("打开公司资料", {}), by.get("改成汽车保存", {})
    if params["express"]:
        express_ok = not seen.get("checkbox") and seen.get("disconnect") and not seen.get("download") and seen.get("sync")
    else:
        express_ok = not seen.get("checkbox") and not seen.get("disconnect") and not seen.get("download")
    return [
        {"case": "company type shown as general", "seen": seen, "ok": seen.get("company_type") == "general"},
        {"case": "express state", "seen": seen, "ok": bool(express_ok)},
        {"case": "boot follows the saved type", "seen": saved, "ok": saved.get("boot_type") == "vehicle" and saved.get("unit_tracking") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
row = frappe.db.get_value("ERP Company", {params['company']!r}, ["company_type", "express_enabled"], as_dict=True)
print(json.dumps(row))
""")
    return [{"case": "company type saved", "saved": got, "ok": got["company_type"] == "vehicle"
             and got["express_enabled"] == (1 if params["express"] else 0)}]
