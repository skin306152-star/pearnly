"""ERP 出入库单 / 盘点调整 → Express 库存单（载荷 6，CC 桌面版 `dbf_stock`；出入库第一期 B 段，10-25）。

- 出库单 → 领用 OU；入库单 → 调整 JU（正）；盘点生成的出入库单（``ref_type = count``）→ 盘点调整 TK（出库那张数量记负）。
- 采购确认后自动生成的入库单（``ref_type = purchase``）不写：那张采购单已带库存行写进 Express，再写一张就重复入账。
- 账套照采购 / 销售同一条规则选（`target.pick`：税号 + 分店 + 日期，同期两份按选过的，没选过停下让人选一次）；账套资料还没读到时
  请电脑读（`masters.ensure`），读好了再准备。这类单没有对方、没有税，不走发票那套写账规则（`express_rules`），载荷在这里直接备：
  逐行存货码（ERP 商品记的 Express 编码，没有就交品名，电脑认不出整单停下）、单位、数量、仓库码（行上的仓，没有是单头的仓）、
  入库的单位成本。
- 科目：这家公司为这个单别选过科目就用选的（`choices`，与费用科目、存货科目组同一套记法，按公司记住）；没选过用账套里这个单别的
  默认科目（ISRUN，CC 桌面版读主档时报来的 ``stock_books``）。默认科目没有、或不在这份账套可记账的科目里（10-25 离线回归：6 本
  ASIAFC / TUCL 账套如此）→ 停下让人在提交时的弹窗里选一次（``account``，主题 ``stock:<单别>``）。旧版 CC 桌面版不报 ``stock_books``，
  不核，照旧交给电脑用默认科目。
- 公司：出入库单没有公司栏，按单头仓库的公司（仓库属于公司）。
"""
import frappe

from pearnly_core.express_rules.push.account_rules import wire_account_set

from pearnly_erp_portal.express import choices, contracts, facts, target

DOCTYPE = "ERP Stock Entry"
PAYLOAD_VERSION = 6
ISSUE, ADJUST, COUNT = "OU", "JU", "TK"
COUNT_REF = "count"
SKIPPED_REFS = ("purchase",)
ACCOUNT = "account"  # 要人选的那一项（与费用科目同一种，选项是这份账套的科目表）


def account_subject(doctype) -> str:
    return f"stock:{doctype}"


def company_of(name):
    warehouse = frappe.db.get_value(DOCTYPE, name, "warehouse") if name else None
    return frappe.db.get_value("ERP Warehouse", warehouse, "company") if warehouse else None


def eligible(row) -> bool:
    """这张出入库单该不该写进 Express（还要公司接了 Express，由调用方判）。"""
    return row.get("status") == "submitted" and (row.get("ref_type") or "") not in SKIPPED_REFS


def kind(row) -> str:
    if (row.get("ref_type") or "") == COUNT_REF:
        return COUNT
    return ISSUE if row.get("direction") == "out" else ADJUST


def be_date(day) -> str:
    """单据日期 → 佛历 YYMMDD（Express 的单号与载荷都用它）。"""
    day = frappe.utils.getdate(day)
    return f"{(day.year + 543) % 100:02d}{day.month:02d}{day.day:02d}"


def document(name, ref) -> dict:
    """这张出入库单交给电脑的样子（还没选账套）。``ref``：单据标识（`push.ref`）。"""
    doc = frappe.get_doc(DOCTYPE, name)
    doctype = kind(doc)
    products, warehouses, lines = {}, {}, []
    for line in doc.lines:
        product = products.get(line.product)
        if product is None:
            product = products[line.product] = frappe.db.get_value(
                "ERP Product", line.product, ["product_name", "express_code"], as_dict=True) or {}
        warehouse = line.warehouse or doc.warehouse
        if warehouse and warehouse not in warehouses:
            warehouses[warehouse] = frappe.db.get_value("ERP Warehouse", warehouse, "express_code") or ""
        qty = contracts.d(line.qty)
        if doctype == COUNT and doc.direction == "out":
            qty = -qty
        item = {"code": product.get("express_code") or "", "name": product.get("product_name") or "",
                "qty": str(qty), "unit": line.unit or "", "loccod": warehouses.get(warehouse, "")}
        if qty > 0 and contracts.d(line.unit_cost) > 0:
            item["unit_cost"] = str(contracts.d(line.unit_cost))
        lines.append(item)
    return {"ref": ref, "direction": "stock", "doctype": doctype, "date": str(doc.posting_date),
            "number": doc.doc_no or doc.name, "remark": str(doc.reason or "").strip(),
            "loccod": warehouses.get(doc.warehouse, ""), "lines": lines}


def prepare(body, company_row, ensure_masters) -> dict:
    """``{"payload", "book"}`` 或 ``{"hold": 原因, ...}``（同 `prepare.prepare` 的形状，`push.send` 照样处理）。"""
    from pearnly_erp_portal.express import prepare as invoices

    picked = target.pick(company_row, body["date"])
    company_name = company_row.get("company_name") or company_row["name"]
    if picked["reason"] == contracts.SEVERAL_BOOKS:
        return {"hold": contracts.CHOICE + ":" + choices.ACCOUNT_SET, "needed": invoices._needed_book(picked, company_name)}
    if picked["reason"]:
        return {"hold": picked["reason"], "book": picked.get("book")}
    book = picked["book"]
    data = facts.load(book["key"])
    if data is None:
        ensure_masters(company_row["name"], book)
        data = facts.load(book["key"])
        if data is None:
            return {"hold": contracts.READING, "book": book}
    subject = account_subject(body["doctype"])
    account = choices.chosen(company_row["name"], ACCOUNT, subject)
    stock_books = (data.get("facts") or {}).get("stock_books") if isinstance(data, dict) else None
    if not account and isinstance(stock_books, dict):
        postable = {str(row.get("code") or "").strip() for row in facts.reported(data).get("reported_accounts") or []}
        default = str(stock_books.get(body["doctype"]) or "").strip()
        if not default or (postable and default not in postable):
            return {"hold": contracts.CHOICE + ":" + ACCOUNT, "book": book,
                    "needed": [{"kind": ACCOUNT, "subject": subject, "label": body["doctype"], "options": [], "current": ""}]}
    path = book["path"]
    payload = {
        "payload_version": PAYLOAD_VERSION, "direction": "stock", "doctype": body["doctype"],
        "docdate_be": be_date(body["date"]), "ref_no": str(body["number"])[:15], "remark": body["remark"][:50],
        "loccod": body["loccod"], "items": body["lines"],
        "account_root": book.get("root") or "", "account_dir": path, "account_company": book.get("company") or "",
        "account_set_row": 0, "account_set": wire_account_set(path, path),
        # 单据日期所在的会计期在 Express 里已关账：电脑不写、回 PERIOD_LOCKED（同采购 / 销售）
        "respect_period_lock": True,
    }
    if account:
        payload["account"] = account
    return {"payload": payload, "book": book}


def form_body(payload) -> dict:
    """出入库单表单此刻的内容（还没存）→ :func:`prepare` 要的单（提交前问要人定的项用，`api.preflight`）。只看单别与日期：
    要问的只有科目，与明细无关。"""
    direction = "out" if str(payload.get("direction") or "") == "out" else "in"
    return {"ref": "preflight", "direction": "stock", "doctype": kind({"direction": direction}),
            "date": str(payload.get("posting_date") or frappe.utils.nowdate()), "number": "-", "remark": "",
            "loccod": "", "lines": []}


__all__ = ["ACCOUNT", "ADJUST", "COUNT", "DOCTYPE", "ISSUE", "account_subject", "company_of", "document", "eligible",
           "form_body", "kind", "prepare"]
