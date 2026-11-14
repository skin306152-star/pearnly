"""流程用例：LINE 录收款（富菜单第 4 格「收款」，收款第 3 步 11-11）→ 确认卡【编辑】打开的「我的收款」弹窗改草稿 → 回 LINE 放行。

不经真 LINE、不出网、不写 Express：在新开户那套站点里以收款员发模拟 LINE 事件（`collection_lib.line_events`，真实分发与
ERP 处理器），依次走：收款 → 打客户码 → 勾两张发票 → BBL 转账 500 → 发凭证照片 → 再加一种方式 → 现金 1000 → 确认卡。
页面（收款员、手机宽度）：打开旧卡片【编辑】那个地址 `/desk/pearnly-my-collections/<记录>`（11-14 起转到「收款」页那一笔；新卡指
`/desk/pearnly-collections/<记录>`），弹窗里两张发票默认本次收 1000 / 500，
把第一张与现金都改成 900、销售员改成 S2 保存（默认是第一张发票的 S1、部门是单据簿默认 HQ）。库里：草稿版本 2、各张 900 / 500、现金 900。回 LINE：点旧卡片的【确认】（版本 1）不放行、重出
版本 2 的卡；没有收款员岗位的人伪造【确认】被拒；打「ok」放行（同意记成 LINE 事件号、交写账队列）；等写入回「已记录 · 待写入」，
点【撤回】撤回。富菜单：收款员那套是「收款」一格。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from collection_lib import COLLECTOR, SALES_LINE, line_events, postbacks, restore, seed  # noqa: E402
from erp_lib import JS  # noqa: E402

TITLE = "LINE 录收款 → 我的收款弹窗改草稿 → 回 LINE 放行与撤回"
EXPECTED = ["customer", "invoices", "invoices", "invoices", "method", "amount", "voucher", "more", "method", "amount", "card"]


def prepare(lab, manifest):
    tag = uuid.uuid4().hex[:6]
    seeded = seed(lab, manifest, tag)
    first, second = seeded["invoices"]
    events = [["postback", "a=col&s=start"], ["text", seeded["customer"]],
              ["postback", f"a=col&s=inv&v={first}"], ["postback", f"a=col&s=inv&v={second}"],
              ["postback", "a=col&s=invoices&v=done"], ["postback", "a=col&s=method&v=BB"], ["text", "500"],
              ["image", "flow-voucher"], ["postback", "a=col&s=more&v=method"], ["postback", "a=col&s=method&v=%40cash"],
              ["text", "1,000"]]
    ran = line_events(lab, manifest["site"], events)["events"]
    card = ran[-1]["session"]
    menu = lab.python(manifest["site"], f"""
from pearnly_erp_portal.integrations import line_menu
print(json.dumps({{"variant": line_menu.wanted_variant({COLLECTOR!r}), "sales": line_menu.wanted_variant("sales@erp.regression")}}))
""")
    return {**seeded, "tag": tag, "name": card.get("name") or "", "version": card.get("version"), "menu": menu,
            "steps": [row["step"] for row in ran], "errors": [row["error"] for row in ran if row["error"] or not row["handled"]],
            "card_buttons": postbacks(ran[-1]["messages"]), "cases": []}


def cleanup(lab, manifest, params):
    restore(lab, manifest, params)


def steps(params, camera_code=None):
    # 11-14 真机：旧卡【编辑】整页打开旧地址，外壳把它当成不是本产品的页、换回首页。流程按真的进法走：登记里的 start 是
    # LINE 卡片同一个入口 `line_login.enter?to=/desk/pearnly-my-collections/<记录>`（已登录直接整页跳过去），这里不再在页面里切路由
    return [{"name": "改草稿", "js": JS + f"""
const dialog=await until(()=>window.cur_dialog&&cur_dialog.display&&cur_dialog.fields_dict.invoice_0&&cur_dialog, 25000);
if(!dialog) return {{error:'no dialog', msg: msg()}};
await idle();
const before={{invoices:[dialog.get_value('invoice_0'), dialog.get_value('invoice_1')], cash: dialog.get_value('cash'),
              methods: Object.keys(dialog.fields_dict).filter(k=>k.startsWith('method_')).map(k=>dialog.fields_dict[k].df.label),
              salesman: dialog.get_value('salesman'), department: dialog.get_value('department')}};
await dialog.set_value('invoice_0', 900); await dialog.set_value('cash', 900); await dialog.set_value('salesman', 'S2'); await sleep(300);
dialog.get_primary_btn().click();
const closed=await until(()=>!dialog.display, 20000); await idle();
return {{before, closed: !!closed, msg: msg(), route: frappe.get_route(), path: location.pathname}};"""}]


def judge(params, results, camera_code=None):
    seen = next((r.get("value") or {} for r in results if r.get("step") == "改草稿"), {})
    before = seen.get("before") or {}
    return [
        {"case": "every LINE step was answered in order", "seen": {"steps": params["steps"], "errors": params["errors"]},
         "ok": params["steps"] == EXPECTED and not params["errors"]},
        {"case": "the card has confirm and withdraw for version 1",
         "seen": params["card_buttons"], "ok": any("s=ok" in d and "ver=1" in d and "k=" in d for d in params["card_buttons"])
         and any("s=wd" in d and "k=" in d for d in params["card_buttons"])},
        {"case": "the collector's menu has the collection tile and sales has none", "seen": params["menu"],
         "ok": params["menu"].get("variant") == "collection" and "collection" not in params["menu"].get("sales", "")},
        {"case": "the edit dialog shows the draft amounts", "seen": before,
         "ok": before.get("invoices") == [1000, 500] and before.get("cash") == 1000 and len(before.get("methods") or []) == 3
         and before.get("salesman") == "S1" and before.get("department") == params["department"]},
        {"case": "saved", "seen": {k: seen.get(k) for k in ("closed", "msg", "error")}, "ok": bool(seen.get("closed"))},
        {"case": "the old edit address opened the record on the collections page (not the home page)",
         "seen": {k: seen.get(k) for k in ("route", "path", "error")},
         "ok": (seen.get("route") or [None])[0] == "pearnly-collections" and seen.get("path", "").startswith("/desk/pearnly-collections/")},
    ]


def verify(lab, manifest, params, camera_code=None):
    site, name = manifest["site"], params["name"]
    saved = _record(lab, site, name)
    # 旧卡（网页改草稿之前出的那张）上的【确认】：带那张卡的一次性确认号（11-13 起【确认】【撤回】都要它）
    old_ok = next((data for data in params["card_buttons"] if "s=ok" in data), f"a=col&s=ok&n={name}&ver=1")
    ran = line_events(lab, site, [["postback", old_ok],
                                  ["postback", f"a=col&s=ok&n={name}&ver=2", SALES_LINE],
                                  ["text", " OK "]])["events"]
    released = _record(lab, site, name)
    pending = ran[-1]["messages"]
    withdraw = [data for data in postbacks(pending) if "s=wd" in data]
    after = line_events(lab, site, [["postback", withdraw[0]]])["events"] if withdraw else []
    withdrawn = _record(lab, site, name)
    return [
        {"case": "the dialog saved version 2 with the new amounts", "seen": saved,
         "ok": saved["version"] == 2 and saved["invoices"] == {params["invoices"][0]: "900.00", params["invoices"][1]: "500.00"}
         and saved["cash"] == "900.00" and saved["status"] == "draft" and saved["salesman"] == "S2"},
        {"case": "the old card's confirm shows the new card instead of releasing", "seen": postbacks(ran[0]["messages"]),
         "ok": any(f"n={name}" in d and "ver=2" in d for d in postbacks(ran[0]["messages"])) and ran[0]["step"] == "card"},
        {"case": "someone without the collector post cannot confirm it", "seen": ran[1]["messages"][:1],
         "ok": ran[1]["handled"] and not ran[1]["error"]},
        {"case": "typed ok released the shown version with the LINE event as consent", "seen": released,
         "ok": released["status"] == "queued" and released["consent"].startswith("line:flowevt") and bool(released["job"])},
        {"case": "the write payload names the edited salesman and the default department (not left to CC)",
         "seen": released["payload"], "ok": released["payload"].get("salesman") == "S2"
         and released["payload"].get("department") == params["department"] and bool(params["department"])},
        {"case": "the pending reply offers withdraw and withdrawing cancels the write", "seen": {"withdraw": withdraw, **withdrawn},
         "ok": bool(withdraw) and bool(after) and withdrawn["status"] == "withdrawn" and withdrawn["job_status"] == "cancelled"},
    ]


def _record(lab, site, name):
    return lab.python(site, f"""
doc = frappe.get_doc("ERP Collection", {name!r})
job = frappe.db.get_value("Pearnly CC Job", doc.job, ["status", "payload"], as_dict=True) if doc.job else None
payload = json.loads(job.payload or "{{}}") if job else {{}}
print(json.dumps({{"status": doc.status, "version": int(doc.draft_version or 0), "cash": "%.2f" % (doc.cash or 0),
                  "invoices": {{row.docnum: "%.2f" % row.amount for row in doc.invoices}}, "consent": doc.consent_ref or "",
                  "job": doc.job or "", "job_status": job.status if job else "", "salesman": doc.salesman or "",
                  "payload": {{key: payload.get(key) for key in ("salesman", "department", "direction", "payload_version")}}}}))
""")

