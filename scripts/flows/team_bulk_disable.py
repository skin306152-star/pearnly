"""流程用例：团队页批量停用（11-01 真机：testcowork 勾一位员工点「停用 1」，按钮一直灰、没有成功提示，列表 20 多秒不变，手动刷新才显示已停用）。

ERP、Cowork 各跑一遍（同一个模块，按站点认产品）。prepare 按团队服务建一位员工；页面上勾选这一行 → 右上角批量按钮 → 点下去：
原生成功提示出来、按钮回到「新建」且可点、这一行**不刷新页面**就显示已停用（停用标红那一格）。库里：这位员工 `enabled = 0`。
"""
from __future__ import annotations

import json
import uuid

TITLE = "团队页批量停用：成功提示、按钮恢复、列表当场显示已停用"
IDLE = ("const idle=async()=>{let q=0;for(let k=0;k<80&&q<2;k++){await new Promise(r=>setTimeout(r,250));q=($.active===0)?q+1:0;}};")


def _app(manifest):
    return "pearnly_cowork" if manifest["site"].startswith("cowork") else "pearnly_erp_portal"


def prepare(lab, manifest):
    app = _app(manifest)
    tag = uuid.uuid4().hex[:6]
    admin = "admin@cowork.regression" if app == "pearnly_cowork" else "admin@erp.regression"
    out = lab.python(manifest["site"], f"""
from {app}.team import service
frappe.set_user({admin!r})
roles = [row["name"] for row in service.role_catalog() if not str(row["name"]).endswith("LINE")]
made = service.add_member(username={("flowbulk" + tag)!r}, first_name="Bulk", last_name={tag!r},
                          password="Flow-Bulk-{tag}-9a", roles=json.dumps(roles[:1]))
frappe.db.commit()
print(json.dumps({{"email": made.get("email") or frappe.db.get_value("User", {{"username": {("flowbulk" + tag)!r}}})}}))
""")
    return {**out, "app": app, "cases": []}


def steps(params, camera_code=None):
    email = json.dumps(params["email"])
    return [
        {"name": "批量停用", "js": IDLE + "await idle();"
         "window.__alerts=[]; const show=frappe.show_alert;"
         "frappe.show_alert=function(m){window.__alerts.push(typeof m==='string'?m:(m&&m.message)||'');return show.apply(this,arguments)};"
         f"const box=document.querySelector('.list-row-checkbox[data-name='+JSON.stringify({email})+']');"
         "if(!box) return {error:'member row not in list'}; box.click(); await new Promise(r=>setTimeout(r,300));"
         "const btn=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         "if(!btn) return {error:'no bulk button'}; const label=btn.innerText.trim(); const started=Date.now(); btn.click();"
         "for(let k=0;k<120&&!window.__alerts.length;k++) await new Promise(r=>setTimeout(r,250)); const alert_ms=Date.now()-started;"
         "await idle();"
         "const after=[...document.querySelectorAll('.page-head .primary-action, .page-head .btn-primary')].find(e=>e.offsetParent);"
         f"const again=document.querySelector('.list-row-checkbox[data-name='+JSON.stringify({email})+']');"
         "const row=again&&again.closest('.list-row-container, .list-row');"
         "return {label, alerts: window.__alerts, alert_ms, after_label: after?after.innerText.trim():'',"
         "after_disabled: after?after.disabled:null, still_checked: !!(again&&again.checked),"
         "row_inactive: !!(row&&row.querySelector('.pearnly-payment-due'))};"},
    ]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "批量停用"), {})
    return [
        {"case": "the bulk action shows the native success alert", "seen": {k: seen.get(k) for k in ("label", "alerts", "alert_ms")},
         "ok": bool(seen.get("alerts")) and (seen.get("alert_ms") or 0) < 15000},
        {"case": "the head button is back to new and clickable, the row is unchecked",
         "seen": {k: seen.get(k) for k in ("after_label", "after_disabled", "still_checked")},
         "ok": bool(seen.get("after_label")) and seen.get("after_label") != seen.get("label")
         and seen.get("after_disabled") is False and seen.get("still_checked") is False},
        {"case": "the list shows the member as inactive without reloading the page", "seen": {"row_inactive": seen.get("row_inactive")},
         "ok": seen.get("row_inactive") is True},
    ]


def verify(lab, manifest, params, camera_code=None):
    got = lab.python(manifest["site"], f"""
print(json.dumps({{"enabled": frappe.db.get_value("User", {params['email']!r}, "enabled")}}))
""")
    return [{"case": "the member is disabled in the database", "saved": got, "ok": got.get("enabled") == 0}]
