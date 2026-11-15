"""验收场景的站点侧脚本（在测试租户 / 实验栈的 ERP 网页容器里跑）：给接了 MR.ERP 测试账套的那家公司开一张采购 / 销售单 →
导入 MR.ERP → 按单号把 MR.ERP 里的单据页与日记账读回来，把单据、写账状态、读回的格子与分录交回。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_mrerp_post.py <站点> '<JSON 参数>'

参数：user（开单的员工）、company（ERP 公司名）、account_set（MR.ERP 账套键，如 6:1）、book_label（账套名里必须带的字，如 TEST2019）、
kind：purchase_goods（货品赊购）/ purchase_expense（费用）/ sales_credit（赊销）/ sales_cash（现销现金）、run（本次运行标记）、
resend（真：写成后再推一次、再跑一次导入——单号不许变、列表里不许多一张）、journal_menus（日记账菜单号，默认 248 250 251 249 247）。

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
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
KINDS = {  # 我方种类 → (方向, MR.ERP 单据类型, 列表模块)
    "purchase_goods": ("purchase", "purchase", "aptran"), "purchase_expense": ("purchase", "purchase_expense", "aptran"),
    "sales_credit": ("sales", "sales_credit", "artran"), "sales_cash": ("sales", "sales_cash", "arse")}
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
try:
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_core.mrerp import account
    from pearnly_erp_portal import mrerp
    from pearnly_erp_portal.express import push
    from pearnly_erp_portal.mrerp import catalog, choices, connection

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    kind = params["kind"]
    if kind not in KINDS:
        raise SystemExit(f"unknown kind {kind}")
    direction, doc_type, listing_module = KINDS[kind]
    row = choices.company_row(params["company"])
    if not row or not row.mrerp_enabled or row.express_enabled or row.mrerp_account_set != params["account_set"]:
        raise SystemExit("refuse: this company is not linked to the given MR.ERP account set")
    frappe.set_user("Administrator")
    sets = catalog.account_sets(row.mrerp_connection, page_length=200)["rows"]
    label = next((item.get("label") or "" for item in sets if item["key"] == params["account_set"]), "")
    if params["book_label"] not in label:
        raise SystemExit(f"refuse: account set {params['account_set']} is not the test book (label: {label})")
    menus, _defaults = choices.chosen(row)
    menu = menus.get(doc_type)
    if not menu:
        raise SystemExit(f"no import menu chosen for {doc_type} in this company (pick it once on the document first)")
    result.update(company=row.name, account_set=params["account_set"], menu=menu, doc_type=doc_type)

    frappe.set_user(params["user"])
    tag, today = params["run"], frappe.utils.today()
    tax = tax_id(tag + str({"purchase_goods": 1, "purchase_expense": 2, "sales_credit": 3, "sales_cash": 4}[kind]))
    party = f"บริษัท เอซีซี {tag} จำกัด"
    line = {"description": f"ACC {kind} {tag}", "qty": "2", "unit_price": "150", "vat_rate": 7, "vat_applicable": 1, "unit": "ชิ้น",
            "item_type": "service" if kind == "purchase_expense" else "goods"}
    payload = {"company": row.name, "doc_date": today, "has_vat": 1, "lines": [line]}
    if direction == "purchase":
        from pearnly_erp_portal.purchasing import entry, suppliers

        doctype = "Purchase Document"
        supplier = suppliers.create_supplier(supplier_name=party, tax_id=tax)["name"]
        payload.update(doc_kind="expense" if kind == "purchase_expense" else "purchase_invoice", doc_no=f"ACC-{tag}-{kind[-3:]}", is_paid=0,
                       price_mode="exclusive", supplier=supplier)
    else:
        from pearnly_erp_portal.masters import customers
        from pearnly_erp_portal.sales import entry

        doctype = "ERP Sales Document"
        customer = customers.create_customer(customer_name=party, tax_id=tax)["name"]
        paid = kind == "sales_cash"
        payload.update(doc_kind="receipt", vat_rate=7, source="manual", customer=customer, is_paid=1 if paid else 0,
                       **({"payment_method": "cash"} if paid else {}))

    def state():
        frappe.db.rollback()
        return frappe.db.get_value(doctype, name, FIELDS, as_dict=True)

    with patch.object(mrerp, "MRERP_LEDGER_ENABLED", True):
        name = entry.post(payload, request_id=f"acc-{secrets.token_hex(6)}")["name"]
        frappe.db.commit()
        frappe.set_user("Administrator")
        push.mrerp_run(doctype, name)
        frappe.db.commit()
        first = state()
        result.update(doc=name, state=first, party={"name": party, "tax_id": tax})
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

    docnum = (result.get("resend") or {}).get("state", first).get("ledger_docnum") if first.ledger_state == "written" else None
    if docnum:
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
        result["read"] = read
    result["ok"] = True
except SystemExit as stop:
    result["why"] = str(stop)
except Exception:
    frappe.db.rollback()
    result["why"] = frappe.get_traceback()[-1500:]
finally:
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
