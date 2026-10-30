"""流程用例：LINE 手机盘点页 `/stocktake`（起因：10-29 空仓盘点保存 404——GET 查码登记的盘点行被回滚）。

库存岗在 LINE 外打开时回落到本账号自己的 LINE 绑定换票据：实验室给库存岗记一条假的绑定（假 LINE 用户号，不连 LINE，只在实验室站点）。
新开户在没有期初的仓开盘点（空仓，正是事故场景），老数据在有存量的仓开盘点。逐个码输入查询：普通商品要出数量框、品名对，
填数保存后提示保存、抬头「已盘点」加一（新开户第一件 0→1）、库里记一条（商品、数量对）；按台商品在按台类型的公司里不能按数量记，
在普通公司照样按数量记（S0）；认不到的码要提示。
再对第一件多记一笔、点撤销：抬头回落、那一笔在库里作废、原来那笔不动。查码接口只收 POST：GET 调回 403（10-29 热修）。
手机宽度另用假摄像头点「扫码」读条码，同样要出数量框并能保存。全程页面不能有报错。
（work-followups 的手机盘点用例 10-29 并入：本账号 LINE 绑定回落、零库存仓、保存后计数加一与「实盘数量」大字、撤销回落、
GET 调查码 403、落库来源 LINE。）
"""
from __future__ import annotations

import json
import uuid

TITLE = "ERP 手机盘点：LINE 里打开、查码、记数、抬头计数、落库（空仓与有存量）"
LINE_USER = "U-flows-lab-stock"  # 实验室假 LINE 用户号
READY = "!!(document.readyState === 'complete' && document.getElementById('app') && document.getElementById('app').children.length)"


def cases(lab, manifest):
    """要试的码：(认法, 码, 应认出的商品, 品名, 预期)。"""
    if manifest["dataset"] == "new":
        picks = [(key, item["scan"], item["product"]) for key, item in manifest["products"].items()]
    else:
        first, second = manifest["products"][0], manifest["products"][1]
        picks = [("by_code", first["code"], first["name"]), ("by_barcode", second["barcode"], second["name"])]
    company = manifest.get("company") or manifest["companies"][0]
    # 按台盘只在「按台类型」的公司（汽车 / 家电）：普通公司的按台商品也按数量盘、照常落库（盘点菜单 S0，用户 10-29）。
    # 预期用服务端同一个判断（count.per_unit），不写死
    names = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
rows = frappe.get_all("ERP Product", filters={{"name": ["in", {[p for _, _, p in picks]!r}]}}, fields=["name", "product_name", "track_serial"])
print(json.dumps({{r.name: [r.product_name, int(count.per_unit(r, {company!r}))] for r in rows}}))
""")
    out = [{"key": key, "scan": scan, "product": product, "name": names[product][0],
            "expect": "unit" if names[product][1] else "entry"} for key, scan, product in picks]
    out.append({"key": "unknown", "scan": "NO-SUCH-CODE-" + uuid.uuid4().hex[:4], "product": None, "name": "", "expect": "not_found"})
    return out


def prepare(lab, manifest):
    """给库存岗记假 LINE 绑定，在新开户的空仓 / 老数据有存量的仓各开一份新盘点。"""
    company = manifest.get("company") or manifest["companies"][0]
    warehouse = next(w["name"] for w in manifest["warehouses"] if w.get("company", company) == company)
    out = lab.python(manifest["site"], f"""
from pearnly_erp_portal.inventory import count
from pearnly_erp_portal.masters import company as companies
user = "stock@erp.regression"
if not frappe.db.exists("Pearnly LINE Binding", {{"user": user, "status": "active"}}):
    frappe.get_doc({{"doctype": "Pearnly LINE Binding", "user": user, "line_user_id": {LINE_USER!r}, "status": "active",
                    "display_name": "flows lab", "bound_at": frappe.utils.now()}}).insert(ignore_permissions=True)
frappe.set_user(user)
companies.set_active_company({company!r})
doc = count.create_count(title="phone " + {uuid.uuid4().hex[:6]!r}, warehouse={warehouse!r})
stock = float(frappe.db.sql("select coalesce(sum(qty_on_hand), 0) from `tabERP Stock Balance` where warehouse=%s", {warehouse!r})[0][0])
frappe.db.commit()
print(json.dumps({{"count": doc["name"], "rows": doc["rows"], "stock": stock}}))
""")
    return {"count": out["count"], "snapshot_rows": out["rows"], "warehouse_stock": out["stock"], "company": company,
            "warehouse": warehouse, "cases": cases(lab, manifest)}


# 按条件等（页面自己的 fetch，没有 jQuery 在途数）：等到条件成立，最多 20 秒
WAIT = "const until=async(f)=>{for(let k=0;k<80;k++){try{if(f())return true;}catch(e){} await new Promise(r=>setTimeout(r,250));} return false;};"
STATE = ("const st=()=>({entry: !!document.getElementById('qty'), "
         "item: (document.querySelector('#app .card:nth-of-type(3) b')||{}).textContent||'', "
         "head: ((document.querySelector('#app .card .muted')||{}).textContent||'').trim(), "
         "toast: (document.getElementById('toast').textContent||'').trim()});")


def steps(params, camera_code=None):
    out = [{"name": "打开这份盘点", "js": WAIT + STATE + (
        "await until(()=>document.querySelector('[data-task], #code, #app .error'));"
        f"const b=document.querySelector('[data-task=\"{params['count']}\"]'); if(b){{b.click();}} "
        "else if(!document.getElementById('code')) return {error:'count not listed', html:document.getElementById('app').innerText.slice(0,300)};"
        "await until(()=>document.getElementById('code')); return st();")}]
    for i, case in enumerate(params["cases"]):
        if camera_code and case["scan"] != camera_code:
            continue
        if camera_code:
            find = "document.getElementById('scan').click(); await until(()=>document.getElementById('qty'));"
        else:
            find = (f"document.getElementById('code').value={json.dumps(case['scan'])}; document.getElementById('find').click(); "
                    "await until(()=>document.getElementById('qty')||document.getElementById('toast').textContent.trim()||"
                    "document.getElementById('error').textContent.trim()); await new Promise(r=>setTimeout(r,500));")
        # 上一件的录入卡先从页面上拿掉（只动显示，不动数据）：认不到 / 按台的码不会重画录入卡，留着会被当成这一次的结果
        out.append({"name": f"查找 {case['key']}", "js": WAIT + STATE + "document.getElementById('toast').textContent=''; "
                    "document.getElementById('qty')?.closest('.card')?.remove(); document.querySelectorAll('#app [data-undo]').forEach(b=>b.closest('.card')?.remove()); "
                    + find + "return st();"})
        if case["expect"] == "unit":
            # 按台商品：现状是手机页出了数量框、保存时服务端拒（「逐台扫码盘点」）；以后改成不出框也算对
            out.append({"name": f"保存 {case['key']}", "js": WAIT + STATE + (
                "if(!document.getElementById('qty')) return Object.assign(st(), {form: false}); "
                "document.getElementById('toast').textContent=''; document.getElementById('qty').value='3'; "
                "document.getElementById('save').click(); await until(()=>document.getElementById('toast').textContent.trim()); "
                "return Object.assign(st(), {form: true, bad: document.getElementById('toast').style.background.includes('127')});")})
        if case["expect"] == "entry":
            out.append({"name": f"保存 {case['key']}", "js": WAIT + STATE + (
                "if(!document.getElementById('qty')) return {error:'no entry to save'}; const before=st().head; "
                f"document.getElementById('toast').textContent=''; document.getElementById('qty').value='{i + 2}'; "
                "document.getElementById('save').click(); await until(()=>document.getElementById('toast').textContent.trim()); "
                "await until(()=>st().head!==before); await new Promise(r=>setTimeout(r,400)); "
                "const big=[...document.querySelectorAll('#app .big')].map(e=>e.textContent.trim()); "
                "return Object.assign(st(), {before: before, counted_big: big[1]||''});")})
    first = next((c for c in params["cases"] if c["expect"] == "entry"), None)
    if first and not camera_code:
        # 第一件再记一笔 99 再撤销：抬头先加后回，库里那一笔作废
        out.append({"name": "再记一笔再撤销", "js": WAIT + STATE + (
            f"document.getElementById('code').value={json.dumps(first['scan'])}; document.getElementById('find').click(); "
            "await until(()=>document.getElementById('qty')); await new Promise(r=>setTimeout(r,400)); const h0=st().head; "
            "const undos=()=>document.querySelectorAll('[data-undo]').length; const n0=undos(); "
            "document.getElementById('toast').textContent=''; document.getElementById('qty').value='99'; document.getElementById('save').click(); "
            "await until(()=>undos()>n0); await new Promise(r=>setTimeout(r,400)); const h1=st().head; "
            "const undo=[...document.querySelectorAll('[data-undo]')].find(b=>(b.closest('tr')?.cells[0]?.textContent||'').trim()==='99'); if(!undo) return {error:'no undo button for 99'}; "
            "document.getElementById('toast').textContent=''; undo.click(); await until(()=>undos()===n0&&st().toast); await new Promise(r=>setTimeout(r,400)); "
            "return {before: h0, saved: h1, undone: st().head, toast: st().toast, entries_before: n0, entries_after: undos()};")})
    out.append({"name": "GET 调查码", "js": (
        "const r=await fetch('/api/method/pearnly_erp_portal.inventory.line_mobile.api_line_stocktake_lookup?code=x', {credentials:'same-origin'}); "
        "return {status: r.status};")})
    return out


def counted(head):
    """抬头里的「已盘点 x / y」取 x（文案跟页面语言走，数字格式一样）。"""
    import re
    found = re.search(r"(\d+)\s*/\s*(\d+)", head or "")
    return int(found.group(1)) if found else None


def judge(params, results, camera_code=None):
    by = {r.get("step"): r.get("value") or {} for r in results}
    checks = [{"case": "打开这份盘点", "seen": by.get("打开这份盘点"), "ok": bool((by.get("打开这份盘点") or {}).get("head"))}]
    first_saved = True
    for case in params["cases"]:
        if camera_code and case["scan"] != camera_code:
            continue
        seen = by.get(f"查找 {case['key']}") or {}
        if case["expect"] == "entry":
            ok = bool(seen.get("entry")) and seen.get("item") == case["name"]
        elif case["expect"] == "unit":
            refused = by.get(f"保存 {case['key']}") or {}
            ok = (not seen.get("entry")) or (refused.get("form") and refused.get("bad") and bool(refused.get("toast")))
            seen = {"lookup": seen, "save": refused}
        else:
            ok = not seen.get("entry") and bool(seen.get("toast") or seen.get("error"))
        checks.append({"case": case["key"], "scan": case["scan"], "expect": case["expect"], "seen": seen, "ok": ok})
        if case["expect"] == "entry":
            saved = by.get(f"保存 {case['key']}") or {}
            # 抬头「已盘点 x / y」保存后马上变（10-29 抬头不刷新的修复）
            # 卡片上「实盘数量」大字随保存刷新成刚记的数（work-followups 断言 3）
            ok = bool(saved.get("toast")) and not saved.get("error") and counted(saved.get("head")) is not None \
                and saved.get("counted_big") == str(params["cases"].index(case) + 2) \
                and counted(saved.get("head")) == (counted(saved.get("before")) or 0) + 1
            if first_saved and params.get("snapshot_rows") == 0 and not camera_code:
                ok = ok and counted(saved.get("before")) == 0 and counted(saved.get("head")) == 1  # 空仓第一件 0→1
            first_saved = False
            checks.append({"case": f"保存 {case['key']}", "seen": saved, "ok": ok})
    if not camera_code:
        undo = by.get("再记一笔再撤销") or {}
        # 同一件商品再记一笔不增加「已盘点件数」，所以看的是撤销后抬头与记之前一致、且出了撤销提示
        checks.append({"case": "再记一笔再撤销", "seen": undo, "ok": bool(undo.get("toast")) and undo.get("undone") == undo.get("before")
                       and not undo.get("error")})
    get = by.get("GET 调查码") or {}
    checks.append({"case": "查码接口不收 GET", "seen": get, "ok": get.get("status") == 403})
    return checks


def verify(lab, manifest, params, camera_code=None):
    rows = lab.python(manifest["site"], f"""
rows = frappe.get_all("ERP Stock Count Entry", filters={{"count": {params['count']!r}}}, fields=["product", "qty", "voided", "source"],
                      order_by="creation asc", limit_page_length=50)
print(json.dumps([dict(r, qty=float(r.qty), voided=int(r.voided)) for r in rows]))
""")
    voided = [r for r in rows if r["voided"]]
    rows = [r for r in rows if not r["voided"]]
    checks = []
    for i, case in enumerate(params["cases"]):
        if case["expect"] != "entry" or (camera_code and case["scan"] != camera_code):
            continue
        hit = [r for r in rows if r["product"] == case["product"]]
        checks.append({"case": case["key"], "saved": hit, "ok": len(hit) == 1 and hit[0]["qty"] == float(i + 2)})
    expected = {c["product"] for c in params["cases"] if c["expect"] == "entry"}
    others = [r for r in rows if r["product"] not in expected]
    checks.append({"case": "no stray entries", "saved": others, "ok": not others})
    # 手机页记的每一笔都标来源 LINE（work-followups 断言 6）
    checks.append({"case": "来源都是 LINE", "saved": sorted({r["source"] for r in rows + voided}), "ok": all(r["source"] == "LINE" for r in rows + voided)})
    first = next((c for c in params["cases"] if c["expect"] == "entry"), None)
    if first and not camera_code:
        checks.append({"case": "撤销的那一笔作废", "saved": voided,
                       "ok": len(voided) == 1 and voided[0]["product"] == first["product"] and voided[0]["qty"] == 99.0})
    else:
        checks.append({"case": "没有作废记录", "saved": voided, "ok": not voided})
    return checks
