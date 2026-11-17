"""验收场景的站点侧脚本（在测试租户 / 实验栈的 ERP 网页容器里跑）：给接了 MR.ERP 测试账套的那家公司开一张采购 / 销售单 →
导入 MR.ERP → 按单号把 MR.ERP 里的单据页与日记账读回来，把单据、写账状态、读回的格子与分录交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_mrerp_post.py <站点> '<JSON 参数>'

参数：user（开单的员工）、company（ERP 公司名）、account_set（MR.ERP 账套键，如 6:1）、book_label（账套名里必须带的字，如 TEST2019）、
kind：purchase_goods（货品赊购）/ purchase_expense（费用）/ sales_credit（赊销）/ sales_cash（现销现金）/ purchase_cash（现购）/
sales_discount（整单折扣的赊销）/ purchase_novat（无税卖家的采购）/ purchase_nobook（单据日期不在任何账套期间里，应停下不导入）、
outside_date（purchase_nobook 用的日期，默认 2019-06-01）、run（本次运行标记）、
resend（真：写成后再推一次、再跑一次导入——单号不许变、列表里不许多一张）、journal_menus（日记账菜单号，默认 248 250 251 249 247）。

第 6 批另加的 kind（11-15 清单 T9–T12、T16、T18；采购的几种是货品赊购的形状，销售的几种是赊销的形状）：
- menu_choice（T9）：提交前去掉公司记的「采购」导入菜单 → 导入停在「要选一次」→ 以开单人在单据上选回原来那个（`express.api.answer`）
  → 再导入写成；收尾核公司上又记着原来那个（没记回就按原样写回）。
- retry（T10）：第一次导入时只在这个进程里让连接「没有登录名」（`connection.lease` 包一层，不改连接上存的凭据）→ 停在没登录 →
  以开单人点单据页「再写一次」（`express.api.retry`）→ 写成。
- void_importing（T12）：导入那几十秒里（真导入回来、记结论之前）以开单人把单据作废（`sales.entry.cancel`）→ 转人工「作废后写进了」、单号留着。
- switch_off（T16）：开着开关提交（排队）→ 关开关跑每分钟巡检与导入任务 → 仍排着、没单号、公司账本设置不变 → 开回再导入写成。
- tax_purchase / tax_sales（T18）：写成后在 MR.ERP 归期（进项 acvatdsel 210 / 销项 acvatsaledsel 214，只勾这一张，报税日期 = 今天的佛历），
  再取税报告（进项 226/43、销项 232/45，xlsx），交回列着这张税票号的行。**会改 MR.ERP 测试账套里这张单的归期**（TEST2019 专属测试，
  写账窗口 11-14 经主会话批过归期写入）。
- held_kicked（T11 的起点）：记这个 MR.ERP 账号「被踢」→ 提交 → 自动导入快速失败、不登录，单据停下；**不清标记**（由 CC 对话推送那一步清）。
  need_switch 真时：被测站点源码里写账开关关着就拒绝（对话在网页进程里跑，只在这个进程里打开不算数）。
- readback（T11 的终点）：不建单，等 doc / doctype 那张单落定后按单号读回（导入由网页进程里 CC 推送排的任务做）。
- clear_kick：清掉这个账号的「被踢」标记（T11 收尾）。
称谓分栏：purchase_goods、sales_credit 另读 MR.ERP 对方档页（`apmas` / `armas` 查看页）的称谓下拉与名称栏；sales_credit 的客户名改用
「ห้างหุ้นส่วนจำกัด …」（全称要落成 MR.ERP 称谓表里的「หจก.」，11-15 T3 缺陷 84facae90）。

做法照 11-15 写账窗口实测过的那一套（`~/pearnly-devtools/mrerp-write-grid-lab/`，这里收成一个进程）：
- `MRERP_LEDGER_ENABLED` 是产品代码里的常量、默认关：**只在这个进程里**打开（不改产品代码、不动别的进程），所以后台任务不会替我们导入，
  这里以后台身份同步跑一次 `express.push.mrerp_run`。
- 读回没有现成的产品函数：经全仓唯一的登录入口 `pearnly_core.mrerp.account.use`（人点的口径）自己取单据列表、单据查看页、日记账查看页。
  凭据用连接上存的那一份（`connection.lease`），只在进程里用，**不打印、不进结果**。
防护：只在测试站点跑；公司必须已经接着这份账套、账套名里带 book_label（只写测试账号的测试账套）；每张单的对方用新名字、新税号，
断言只按本次的单号读回——MR.ERP 里的单不删（产品没有删除函数），数据会累积。
登录会踢掉别处同一个 MR.ERP 账号的会话：运行器先占 `mrerp:<账号>` 的锁，跑前先问主会话。
"""
import base64
import html
import json
import re
import secrets
import sys
import time
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
KINDS = {  # 我方种类 → (方向, MR.ERP 单据类型, 列表模块)
    "purchase_goods": ("purchase", "purchase", "aptran"), "purchase_expense": ("purchase", "purchase_expense", "aptran"),
    "sales_credit": ("sales", "sales_credit", "artran"), "sales_cash": ("sales", "sales_cash", "arse"),
    # 第二批（11-15 清单 T7、T8、T15）：现购、整单折扣的赊销、无税卖家的采购、单据日期不在任何账套期间里
    "purchase_cash": ("purchase", "purchase", "aptran"), "sales_discount": ("sales", "sales_credit", "artran"),
    "purchase_novat": ("purchase", "purchase", "aptran"), "purchase_nobook": ("purchase", "purchase", "aptran"),
    # 第 6 批（T9–T12、T16、T18）：货品赊购 / 赊销的形状
    "menu_choice": ("purchase", "purchase", "aptran"), "retry": ("purchase", "purchase", "aptran"),
    "void_importing": ("sales", "sales_credit", "artran"), "switch_off": ("purchase", "purchase", "aptran"),
    "tax_purchase": ("purchase", "purchase", "aptran"), "tax_sales": ("sales", "sales_credit", "artran"),
    "held_kicked": ("purchase", "purchase", "aptran")}
PREFIXED = {"purchase_goods": ("บริษัท", "เอซีซี {tag} จำกัด"), "sales_credit": ("หจก.", "เอซีซี {tag}")}  # 称谓分栏读回：（称谓栏, 名称栏）
TAX = {"tax_purchase": ("acvatdsel", 210, "cbacvatd", 226, 43), "tax_sales": ("acvatsaledsel", 214, "cbacvatsaled", 232, 45)}
SERVICE_KINDS = ("readback", "clear_kick")
ORDER = list(KINDS)
FIELDS = ["status", "ledger_state", "ledger_target", "ledger_reason", "ledger_docnum", "ledger_book", "ledger_needed", "grand_total",
          "vat_amount", "subtotal"]
FORM_KEYS = ["txtdoctype", "txtdocnum", "txtdocdate", "txtapmas", "txtapmasdetail", "txtapmas_inv", "txtapmas_invdetail", "txtarmas",
             "txtarmasdetail", "txtvatnum", "txtbillno", "txtothvatrate", "txtsumamt", "txtvatamt", "txtnetamt", "txtsumnet", "txttotalamt",
             "txtcash", "txtbank1", "txtothrec1", "txtremark"]


def tax_id(seed: str) -> str:
    """一个校验位正确的 13 位税号（MOD-11），由本次运行标记定：每次是新的对方。"""
    digits = [int(ch) for ch in ("0105" + "".join(ch for ch in seed if ch.isdigit()).rjust(8, "7"))[:12]]
    check = (11 - sum(digit * weight for digit, weight in zip(digits, range(13, 1, -1))) % 11) % 10
    return "".join(map(str, digits)) + str(check)


def form_fields(page: str) -> dict:
    """MR.ERP 查看页 → {格子 id: 值}（勾选框记勾没勾；跳过公司设置与按钮）。"""
    out = {}
    for match in re.finditer(r"<input\b([^>]*)>", page, re.I):
        attrs = match.group(1)
        ident = re.search(r"\bid=[\"']([^\"']+)", attrs)
        if not ident or ident.group(1).endswith(("_fssm", "_pg2")):
            continue
        kind = (re.search(r"\btype=[\"']([^\"']+)", attrs) or [None, "text"])[1].lower()
        if kind in ("button", "submit"):
            continue
        if kind in ("checkbox", "radio"):
            out[ident.group(1)] = "checked" if re.search(r"\bchecked\b", attrs, re.I) else "unchecked"
            continue
        value = re.search(r"\bvalue=[\"']([^\"']*)", attrs)
        value = html.unescape(value.group(1)) if value else ""
        if value.strip():
            out[ident.group(1)] = value
    return out


def ids_of(listing: str, docnum: str) -> list:
    """列表页里这个单号对应的查看页 id（每出现一次算一张）。"""
    found, start = [], 0
    while True:
        index = listing.find(docnum, start)
        if index < 0:
            return found
        match = re.search(r"allform\.php\?id=(\d+)&(?:amp;)?status=vie", listing[index:])
        if match and match.group(1) not in found:
            found.append(match.group(1))
        start = index + len(docnum)


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
restore_menus = None
try:
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_core.mrerp import account
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.mrerp import catalog, choices, connection

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    kind = params["kind"]
    if kind not in KINDS and kind not in SERVICE_KINDS:
        raise SystemExit(f"unknown kind {kind}")
    if params.get("need_switch") and not mrerp.MRERP_LEDGER_ENABLED:
        # 对话推送在网页进程里跑：那里的开关是源码里的常量，只在本进程里打开不算数
        raise SystemExit("refuse-switch: MRERP_LEDGER_ENABLED is off in the tested source: the CC chat runs in the web process")
    row = choices.company_row(params["company"])
    if not row or not row.mrerp_enabled or row.express_enabled or row.mrerp_account_set != params["account_set"]:
        raise SystemExit("refuse: this company is not linked to the given MR.ERP account set")
    frappe.set_user("Administrator")
    sets = catalog.account_sets(row.mrerp_connection, page_length=200)["rows"]
    label = next((item.get("label") or "" for item in sets if item["key"] == params["account_set"]), "")
    if params["book_label"] not in label:
        raise SystemExit(f"refuse: account set {params['account_set']} is not the test book (label: {label})")
    result.update(company=row.name, account_set=params["account_set"])

    def kick_key():
        from pearnly_core.mrerp.session import lock_key

        lease = connection.lease(row.mrerp_connection)
        if not lease.get("username"):
            raise SystemExit("this connection has no stored login")
        return lock_key(lease["system_url"], lease["username"])

    if kind == "clear_kick":
        from pearnly_core.mrerp import kick_mark

        result.update(ok=True, cleared=bool(kick_mark.clear(kick_key())))
        raise SystemExit("")

    if kind == "readback":
        direction = "purchase" if params["doctype"] == "Purchase Document" else "sales"
        doc_type, listing_module = ("purchase", "aptran") if direction == "purchase" else ("sales_credit", "artran")
    else:
        direction, doc_type, listing_module = KINDS[kind]
    menus, _defaults = choices.chosen(row)
    menu = menus.get(doc_type)
    if not menu:
        raise SystemExit(f"no import menu chosen for {doc_type} in this company (pick it once on the document first)")
    result.update(menu=menu, doc_type=doc_type)

    tag, today = params.get("run") or "", frappe.utils.today()
    doctype = "Purchase Document" if direction == "purchase" else "ERP Sales Document"
    name = params.get("doc") or ""

    def state():
        frappe.db.rollback()
        return frappe.db.get_value(doctype, name, FIELDS, as_dict=True)

    if kind == "readback":
        deadline, last = time.monotonic() + int(params.get("wait", 420)), None
        while True:
            last = state()
            settled = last.ledger_state in ("written", "held", "failed", "mismatch") and last.ledger_reason != "mrerp.importing"
            if settled or time.monotonic() > deadline:
                break
            time.sleep(5)
        result.update(doc=name, state=last)
    else:
        frappe.set_user(params["user"])
        tax = "" if kind == "purchase_novat" else tax_id(tag + str(ORDER.index(kind) + 1))
        if kind == "purchase_nobook":
            today = params.get("outside_date") or "2019-06-01"
        party = f"ห้างหุ้นส่วนจำกัด เอซีซี {tag}" if kind == "sales_credit" else f"บริษัท เอซีซี {tag} จำกัด"
        line = {"description": f"ACC {kind} {tag}", "qty": "2", "unit_price": "150", "vat_rate": 7, "vat_applicable": 1, "unit": "ชิ้น",
                "item_type": "service" if kind == "purchase_expense" else "goods"}
        # 公司有几个启用仓时产品要求单上写明仓（inventory.warehouses.required，不替人猜）：带上参数给的仓，没给就取这家公司第一个启用仓
        # （11-17 maclab：主档整读落了 MR.ERP 的 3 个仓，开单报「Pick a warehouse」）
        if line["item_type"] == "goods":
            line["warehouse"] = params.get("warehouse") or frappe.db.get_value(
                "ERP Warehouse", {"company": row.name, "is_active": 1}, "name", order_by="name asc") or ""
        payload = {"company": row.name, "doc_date": today, "has_vat": 0 if kind == "purchase_novat" else 1, "lines": [line]}
        if kind == "purchase_novat":
            line.update(vat_rate=0, vat_applicable=0)
        if direction == "purchase":
            from pearnly_erp_portal.purchasing import entry, suppliers

            supplier = suppliers.create_supplier(supplier_name=party, tax_id=tax or None)["name"]
            payload.update(doc_kind="expense" if kind == "purchase_expense" else "purchase_invoice", doc_no=f"ACC-{tag}-{ORDER.index(kind)}",
                           is_paid=1 if kind == "purchase_cash" else 0, price_mode="exclusive", supplier=supplier)
        else:
            from pearnly_erp_portal.masters import customers
            from pearnly_erp_portal.sales import entry

            customer = customers.create_customer(customer_name=party, tax_id=tax)["name"]
            paid = kind == "sales_cash"
            payload.update(doc_kind="receipt", vat_rate=7, source="manual", customer=customer, is_paid=1 if paid else 0,
                           **({"payment_method": "cash"} if paid else {}), **({"header_discount": "50"} if kind == "sales_discount" else {}))
        result["party"] = {"name": party, "tax_id": tax}
        if kind in PREFIXED:
            result["party"].update(prefix=PREFIXED[kind][0], core=PREFIXED[kind][1].format(tag=tag))

        with patch.object(mrerp, "MRERP_LEDGER_ENABLED", True):
            from pearnly_erp_portal.mrerp import push as mrerp_push

            if kind == "menu_choice":
                # 第一次推、菜单没选过（T9）：去掉公司在这本账套下记的这类单据的菜单，收尾核它被选回来了
                restore_menus = frappe.db.get_value("ERP Company", row.name, "mrerp_menus") or "{}"
                raw = json.loads(restore_menus)
                (raw.get(params["account_set"]) or {}).pop(doc_type, None)
                frappe.db.set_value("ERP Company", row.name, "mrerp_menus", json.dumps(raw, ensure_ascii=False), update_modified=False)
                frappe.db.commit()
            if kind == "held_kicked":
                from pearnly_core.mrerp import kick_mark

                kick_mark.mark(kick_key())   # 同 MR.ERP 页面出「มีผู้ Login ซ้ำ」时共用层记的那个
                frappe.db.commit()
            frappe.set_user(params["user"])
            name = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")["name"]
            frappe.db.commit()
            frappe.set_user("Administrator")
            result["doc"], result["number"] = name, frappe.db.get_value(doctype, name, "system_no") or name
            if kind == "switch_off":
                watched = ["express_enabled", "mrerp_enabled", "mrerp_account_set"]
                before = frappe.db.get_value("ERP Company", row.name, watched, as_dict=True)
                with patch.object(mrerp, "MRERP_LEDGER_ENABLED", False):
                    mrerp_push.poll()
                    push.mrerp_run(doctype, name)
                    frappe.db.commit()
                    result["off"] = {"handles": mrerp_push.handles(row.name), "state": state(), "company_before": before,
                                     "company_after": frappe.db.get_value("ERP Company", row.name, watched, as_dict=True)}
            if kind == "retry":
                real_lease = connection.lease
                with patch.object(connection, "lease", lambda key: {**real_lease(key), "username": ""}):
                    push.mrerp_run(doctype, name)
                frappe.db.commit()
            elif kind == "void_importing":
                from pearnly_core.mrerp.push import execute as executing

                real_execute = executing.execute

                def importing_then_voided(*args, **kwargs):
                    outcome = real_execute(*args, **kwargs)
                    frappe.set_user(params["user"])
                    entry.cancel(name, reason="acceptance: void while importing")
                    frappe.db.commit()
                    frappe.set_user("Administrator")
                    result["import_outcome"] = {"status": outcome.get("status"), "docnum": outcome.get("docnum")}
                    return outcome

                with patch.object(executing, "execute", importing_then_voided):
                    push.mrerp_run(doctype, name)
                frappe.db.commit()
            else:
                push.mrerp_run(doctype, name)
                frappe.db.commit()
            first = state()
            result["first"] = first
            if kind == "menu_choice":
                from pearnly_erp_portal.express import api

                needed = json.loads(first.ledger_needed or "[]") if first.ledger_state == "held" else []
                item = next((one for one in needed if one.get("kind") == "mrerp_menu" and one.get("subject") == doc_type), None)
                result["choice"] = {"needed": needed, "offered": [str(option.get("code")) for option in (item or {}).get("options") or []]}
                if item and str(menu) in result["choice"]["offered"]:
                    frappe.set_user(params["user"])
                    shown = api.answer(doctype, name, [{"kind": "mrerp_menu", "subject": doc_type, "value": str(menu)}])
                    frappe.db.commit()
                    frappe.set_user("Administrator")
                    push.mrerp_run(doctype, name)
                    frappe.db.commit()
                    result["choice"]["answered"] = {key: shown.get(key) for key in ("state", "reason", "needed")}
                result["choice"]["menu_after"] = choices.chosen(choices.company_row(row.name))[0].get(doc_type)
            if kind == "retry":
                from pearnly_erp_portal.express import api

                frappe.set_user(params["user"])
                shown = api.document_express(doctype, name)
                result["retry"] = {"can_retry": bool(shown.get("can_retry"))}
                if shown.get("can_retry"):
                    clicked = api.retry(doctype, name)
                    frappe.db.commit()
                    frappe.set_user("Administrator")
                    push.mrerp_run(doctype, name)
                    frappe.db.commit()
                    result["retry"]["after_click"] = {key: clicked.get(key) for key in ("state", "reason")}
                frappe.set_user("Administrator")
            if kind == "switch_off":
                result["on_again"] = {"handles": mrerp_push.handles(row.name)}
            if params.get("resend") and first.ledger_state == "written":
                try:
                    again = push.send(doctype, name)
                    frappe.db.commit()
                    push.mrerp_run(doctype, name)
                    frappe.db.commit()
                    result["resend"] = {"send": again if isinstance(again, (dict, str)) else str(again), "state": state()}
                except Exception as error:  # noqa: BLE001 —— 被挡是允许的结果
                    frappe.db.rollback()
                    result["resend"] = {"refused": str(error)[:200], "state": state()}
        last = state()
        result["state"] = first if params.get("resend") else last
        if kind == "menu_choice" and str(result["choice"].get("menu_after") or "") == str(menu):
            restore_menus = None   # 已经按原来那个记回去了，不用再写

    final = (result.get("resend") or {}).get("state") or result["state"]
    docnum = final.get("ledger_docnum") if (final.get("ledger_state") == "written" or kind == "void_importing") else None
    if docnum and kind != "held_kicked":
        lease = connection.lease(row.mrerp_connection)
        if not lease.get("username") or not lease.get("password"):
            raise SystemExit("this connection has no stored login: cannot read the document back")
        year, book = params["account_set"].split(":")

        def listing(session, module, idmenu):
            session.request("GET", f"{module}/allview.php", params={"idmenu": idmenu})
            return session.request("POST", f"{module}/component/showdata.php",
                                   data={"searchdataval": docnum, "showdata_numrows": "30", "showdata_pages": "1"},
                                   headers={"X-Requested-With": "XMLHttpRequest", "Origin": session.base,
                                            "Referer": f"{session.base}/{module}/allview.php?idmenu={idmenu}"}).text

        def view(session, module, idmenu, ident):
            return session.request("GET", f"{module}/allform.php", params={"id": ident, "status": "vie"},
                                   headers={"Referer": f"{session.base}/{module}/allview.php?idmenu={idmenu}"}).text

        def party_page(session, module, code):
            """对方档（`apmas` / `armas`）按代码找到档号，读查看页称谓下拉选中的那项与名称栏（同写账窗口 `mr_lab.py prefix_of`）。"""
            from pearnly_core.mrerp.master_reader import MasterReader

            reader = MasterReader(lambda method, path, **kw: session.request(method, path, **kw), session.base)
            found = next((item[0] for item in (reader.listbox(module) or {}).get("inpdupdata") or [] if str(item[1]).strip() == code), None)
            if found is None:
                return {"code": code, "found": False}
            page = session.request("GET", f"{module}/allform.php", params={"id": found, "status": "vie"},
                                   headers={"Referer": f"{session.base}/{module}/allview.php"}).text
            chosen = re.search(r'<select[^>]*id="selprefix"[^>]*>(.*?)</select>', page, re.S)
            picked = [html.unescape(re.sub(r"<[^>]+>", "", text).strip()) for text in
                      re.findall(r"<option[^>]*selected[^>]*>(.*?)</option>", chosen.group(1), re.S)] if chosen else []
            core = re.search(r'id="txtname"[^>]*value="([^"]*)"', page)
            return {"code": code, "found": True, "prefix": picked[0] if picked else "",
                    "name": html.unescape(core.group(1)).strip() if core else ""}

        def vat_rows(session, module, idmenu, search):
            session.request("GET", f"{module}/{module}form.php", params={"idmenu": idmenu},
                            headers={"Referer": f"{session.base}/login/mainmenu.php"})
            page = session.request("POST", f"{module}/component/showdata.php",
                                   data={"searchdataval": search, "showdata_numrows": "100", "showdata_pages": "1"},
                                   headers={"X-Requested-With": "XMLHttpRequest", "Origin": session.base,
                                            "Referer": f"{session.base}/{module}/{module}form.php?idmenu={idmenu}"}).text
            out = []
            for block in re.findall(r"<p\b[^>]*>(.*?)</p>", page, re.S):
                ident = re.search(r'name="cb[a-z]+\[(\d+)\]"', block)
                cells = [" ".join(html.unescape(re.sub("<[^>]+>", " ", cell)).split())
                         for cell in re.findall(r"<span\b[^>]*>(.*?)</span>", block, re.S)]
                if ident:
                    out.append((ident.group(1), [cell for cell in cells if cell]))
            return out

        def tax_report(session, invoice_no):
            """归期（只勾这一张）→ 取税报告 xlsx，交回列着这张税票号的行（同写账窗口 `mr_lab.py vatpass / vatreport`）。"""
            import datetime as dt
            import io

            from openpyxl import load_workbook

            module, idmenu, field, report_menu, report_id = TAX[kind]
            day = dt.date.fromisoformat(str(frappe.utils.today()))
            stamp = f"{day.day:02d}/{day.month:02d}/{day.year + 543}"
            picked = [(ident, cells) for ident, cells in vat_rows(session, module, idmenu, invoice_no) if invoice_no in " ".join(cells)]
            out = {"invoice_no": invoice_no, "date": stamp, "pending": [cells for _ident, cells in picked]}
            if len(picked) != 1 or not invoice_no:
                return out
            data = [("txtvatdatepass", stamp)]
            for ident, _cells in picked:
                data += [(f"{field}[{ident}]", ident), (f"vatamtbal[{ident}]", "1")]
            passed = session.request("POST", f"{module}/{module}process.php", data=data,
                                     headers={"Referer": f"{session.base}/{module}/{module}form.php?idmenu={idmenu}"})
            out["passed"] = passed.status_code
            out["left_pending"] = len([1 for _ident, cells in vat_rows(session, module, idmenu, invoice_no) if invoice_no in " ".join(cells)])
            report = session.request("POST", f"component/php/showreportfile.php?idmenu={report_menu}&idreport={report_id}",
                                     data={"combrhval": "1", "txtvatdatepassbegin": stamp, "txtvatdatepassend": stamp},
                                     headers={"Referer": f"{session.base}/login/mainmenu.php"})
            try:
                sheet = load_workbook(io.BytesIO(report.content)).worksheets[0]
            except Exception as error:  # noqa: BLE001 —— 不是 xlsx：原样交回开头给人看
                out["report_error"] = f"{report.status_code} {str(error)[:120]} {(report.text or '')[:200]}"
                return out
            out["report"] = [[("" if value is None else str(value)) for value in values] for values in sheet.iter_rows(values_only=True)
                             if any(invoice_no == str(value or "").strip() for value in values)]
            return out

        with account.use(lease["system_url"], lease["username"], lease["password"], manual=True, lock_wait=60) as session:
            session.prepare(year, book)
            found = ids_of(listing(session, listing_module, menu), docnum)
            read = {"listed": len(found)}
            if found:
                fields = form_fields(view(session, listing_module, menu, found[0]))
                read["form"] = {key: fields[key] for key in FORM_KEYS if key in fields}
                for journal_menu in params.get("journal_menus") or [248, 250, 251, 249, 247]:
                    entries = ids_of(listing(session, "ac", journal_menu), docnum)
                    if not entries:
                        continue
                    page, lines, index = form_fields(view(session, "ac", journal_menu, entries[0])), [], 1
                    while page.get(f"txtcode{index}") or page.get(f"txtdebit{index}") or page.get(f"txtcredit{index}"):
                        lines.append({"account": page.get(f"txtcode{index}", ""), "debit": page.get(f"txtdebit{index}", ""),
                                      "credit": page.get(f"txtcredit{index}", "")})
                        index += 1
                    read.update(journal_menu=journal_menu, journals=len(entries), journal=lines)
                    break
                if kind in PREFIXED:
                    module = "apmas" if direction == "purchase" else "armas"
                    read["party"] = party_page(session, module, str(read["form"].get("txt" + module) or "").strip())
                if kind in TAX:
                    read["tax"] = tax_report(session, str(read["form"].get("txtvatnum") or "").strip())
        result["read"] = read
    result["ok"] = True
except SystemExit as stop:
    if str(stop):
        result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    if restore_menus is not None:   # 菜单没被选回原来那个（半路出错或没停下要选）：按原样写回，不留给别的场景
        try:
            frappe.db.set_value("ERP Company", params["company"], "mrerp_menus", restore_menus, update_modified=False)
            frappe.db.commit()
            result["menus_restored"] = True
        except Exception:  # noqa: BLE001
            result["menus_restored"] = False
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
