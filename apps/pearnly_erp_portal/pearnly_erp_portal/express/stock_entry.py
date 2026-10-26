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
from pearnly_core.express_rules.push.common import FACTOR_PAYLOAD_VERSION

from pearnly_erp_portal.express import choices, contracts, facts, target

DOCTYPE = "ERP Stock Entry"
PAYLOAD_VERSION = 6
ISSUE, ADJUST, COUNT = "OU", "JU", "TK"
COUNT_REF = "count"
COUNT_DOCTYPE = "ERP Stock Count"
# 采购自动入库：那张采购单已带库存行写进去；盘点生成的出入库单：按整份盘点轧差写一张 TK（`count_document`），不逐张写
SKIPPED_REFS = ("purchase", COUNT_REF)
ACCOUNT = "account"  # 要人选的那一项（与费用科目同一种，选项是这份账套的科目表）


def account_subject(doctype) -> str:
    return f"stock:{doctype}"


def company_of(name, doctype=DOCTYPE):
    warehouse = frappe.db.get_value(doctype, name, "warehouse") if name else None
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


def _plain(value) -> str:
    text = format(value.normalize(), "f")
    return "0" if text in ("-0", "") else text


def base_unit(product_name, product, unit, strict=True):
    """（库存单位, 行上单位到库存单位的换算率）。行上没写单位或就是库存单位 → 1；商品单位表里没有这个单位 → 报错（同 ERP 流水），
    ``strict=False`` 回 1（采购 / 销售行的单位可以是票面上自由写的，换不了就照原样交给 CC，与以前相同）。"""
    base = str(product.get("base_unit") or product.get("unit") or "").strip()
    unit = str(unit or "").strip()
    if not unit or unit == base:
        return base, contracts.d(1)
    factor = frappe.db.get_value("ERP Product Unit", {"parent": product_name, "parenttype": "ERP Product", "unit_name": unit},
                                 "factor_to_base")
    if (not factor or contracts.d(factor) <= 0) and not strict:
        return base, contracts.d(1)
    if not factor or contracts.d(factor) <= 0:
        frappe.throw(frappe._("Unit {0} is not defined on {1}").format(unit, product.get("product_name") or product_name))
    return base, contracts.d(factor)


def document(name, ref, base=False) -> dict:
    """这张出入库单交给电脑的样子（还没选账套）。``ref``：单据标识（`push.ref`）。行照行上的单位与数量交（另带 ERP 商品单位表的
    换算率 ``factor``），CC 桌面版按 Express 主档的包装单位照原生记法写（`stock_units`）；``base=True`` 换成库存单位（盘点轧差用）。"""
    doc = frappe.get_doc(DOCTYPE, name)
    doctype = kind(doc)
    products, warehouses, lines = {}, {}, []
    for line in doc.lines:
        product = products.get(line.product)
        if product is None:
            product = products[line.product] = frappe.db.get_value(
                "ERP Product", line.product, ["product_name", "express_code", "base_unit", "unit"], as_dict=True) or {}
        warehouse = line.warehouse or doc.warehouse
        if warehouse and warehouse not in warehouses:
            warehouses[warehouse] = frappe.db.get_value("ERP Warehouse", warehouse, "express_code") or ""
        # 行上可以是包装单位（箱 = 12；验收 10-25：原来按 1 写进 Express）：照行上单位交，带上 ERP 的换算率，CC 按 Express 主档换
        stock_unit, factor = base_unit(line.product, product, line.unit)
        qty, unit, cost = contracts.d(line.qty), str(line.unit or "").strip() or stock_unit, contracts.d(line.unit_cost)
        if base:
            qty, unit, cost = qty * factor, stock_unit, cost / factor
        if doctype == COUNT and doc.direction == "out":
            qty = -qty
        item = {"code": product.get("express_code") or "", "name": product.get("product_name") or "",
                "qty": _plain(qty), "unit": unit, "loccod": warehouses.get(warehouse, "")}
        if factor != 1 and not base:
            item["factor"] = _plain(factor)
        if qty > 0 and cost > 0:
            item["unit_cost"] = _plain(cost.quantize(contracts.d("0.0001")))
        lines.append(item)
    if doc.warehouse and doc.warehouse not in warehouses:
        warehouses[doc.warehouse] = frappe.db.get_value("ERP Warehouse", doc.warehouse, "express_code") or ""
    body = {"ref": ref, "direction": "stock", "doctype": doctype, "date": str(doc.posting_date),
            "number": doc.doc_no or doc.name, "remark": str(doc.reason or "").strip(),
            "loccod": warehouses.get(doc.warehouse, ""), "lines": lines}
    missing = contracts.unmapped(warehouses)
    if missing:
        body["unmapped_warehouses"] = missing  # Express 里没有对应仓库的仓：`push.send` 停下交人（G15）
    return body


def count_document(name, ref) -> dict:
    """一份已过账的盘点 → 一张多行盘点调整 TK（验收 10-25）：这份盘点生成的全部出入库单按「存货 × Express 仓」轧差（都换成库存单位），
    库位之间挪动净 0 的不写；Express 不分库位，原来逐张写 TK 会把挪动写成毛额（不允许负库存的账套里减的被拦、加的照写）。
    盘盈的行不带 ERP 的估值：接了 Express 的公司以 Express 成本为准，CC 桌面版按这本账套这件品的现有均价（没有就标准成本）取
    （主会话 10-25；Express 自己的盘点调整怎么取价列进服务器测试账套待测）。"""
    count = frappe.db.get_value(COUNT_DOCTYPE, name, ["title", "warehouse"], as_dict=True) or {}
    entries = frappe.get_all(DOCTYPE, filters={"ref_type": COUNT_REF, "ref_id": name, "status": "submitted"},  # bounded: 一份盘点的单
                             fields=["name", "posting_date"], order_by="creation asc", limit_page_length=0)
    net, missing = {}, set()
    for entry in entries:
        part = document(entry.name, ref, base=True)
        missing.update(part.get("unmapped_warehouses") or [])
        for line in part["lines"]:
            key = (line["code"] or line["name"], line["loccod"])
            slot = net.setdefault(key, {"code": line["code"], "name": line["name"], "unit": line["unit"],
                                        "loccod": line["loccod"], "qty": contracts.d(0)})
            slot["qty"] += contracts.d(line["qty"])
    lines = []
    for slot in net.values():
        if not slot["qty"]:
            continue
        lines.append({"code": slot["code"], "name": slot["name"], "qty": _plain(slot["qty"]), "unit": slot["unit"],
                      "loccod": slot["loccod"]})
    warehouse = count.get("warehouse")
    loccod = (frappe.db.get_value("ERP Warehouse", warehouse, "express_code") or "") if warehouse else ""
    missing.update(contracts.unmapped({warehouse: loccod}))
    body = {"ref": ref, "direction": "stock", "doctype": COUNT,
            "date": str(entries[0].posting_date) if entries else frappe.utils.nowdate(), "number": name,
            "remark": str(count.get("title") or "").strip(), "loccod": loccod, "lines": lines}
    if missing:
        body["unmapped_warehouses"] = sorted(missing)
    return body


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
    if isinstance(stock_books, dict) and stock_books and body["doctype"] not in stock_books:
        # 这份账套没有这个单别的单据簿（ISRUN）：选科目也写不进去，直接停下说明（1.12.3 起 CC 桌面版才区分得出）
        return {"hold": contracts.NO_STOCK_BOOK, "book": book}
    if not account and isinstance(stock_books, dict):
        postable = {str(row.get("code") or "").strip() for row in facts.reported(data).get("reported_accounts") or []}
        default = str(stock_books.get(body["doctype"]) or "").strip()
        if not default or (postable and default not in postable):
            return {"hold": contracts.CHOICE + ":" + ACCOUNT, "book": book,
                    "needed": [{"kind": ACCOUNT, "subject": subject, "label": body["doctype"], "options": [], "current": ""}]}
    path = book["path"]
    payload = {
        # 有包装单位的行（带 ERP 换算率）要 CC 桌面版 1.12.4 起才会照 Express 主档换算：盖 7，旧版诚实拒收（验收 10-25）
        "payload_version": FACTOR_PAYLOAD_VERSION if any(line.get("factor") for line in body["lines"]) else PAYLOAD_VERSION,
        "direction": "stock", "doctype": body["doctype"],
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


VOID_PAYLOAD_VERSION = 8
VOID_REQUEST = "erp:void:stock:"  # 作废任务的请求号前缀（`push.job_changed` 据此把回执记到作废栏上）


def void_payload(name, company_row) -> dict:
    """作废一张已写进 Express 的出入库单（载荷 8，CC 桌面版 `dbf_void`，照 Express 原生作废原单）→ ``{"payload", "book"}`` 或
    ``{"hold": 原因}``。账套就是写入时那一份（单据记的 ``express_book``）；逐行带存货码（电脑写入时回报的实际编码）、仓库码、数量，
    电脑据此核原单还是写入时的样子，对不上转人工。"""
    from pearnly_core.cc_desktop import jobs

    doc = frappe.get_doc(DOCTYPE, name)
    book = next((item for item in target.candidates(company_row, str(doc.posting_date))
                 if item.get("key") == doc.express_book), None)
    if not doc.express_docnum or not book:
        return {"hold": contracts.NO_BOOK}
    written = jobs.find_by_name(doc.express_job) if doc.express_job else None
    codes = {int(row.get("seq") or 0): str(row.get("code") or "") for row in (written or {}).get("line_codes") or []
             if isinstance(row, dict)}
    body = document(name, "void")
    items = [{"seq": seq, "code": codes.get(seq) or line["code"], "loccod": line["loccod"], "qty": line["qty"],
              **({"factor": line["factor"]} if line.get("factor") else {})}
             for seq, line in enumerate(body["lines"], start=1)]
    path = book["path"]
    payload = {
        "payload_version": VOID_PAYLOAD_VERSION, "direction": "stock", "action": "void", "doctype": body["doctype"],
        "docnum": doc.express_docnum, "items": items,
        "account_root": book.get("root") or "", "account_dir": path, "account_company": book.get("company") or "",
        "account_set_row": 0, "account_set": wire_account_set(path, path), "respect_period_lock": True,
    }
    return {"payload": payload, "book": book}


def form_body(payload) -> dict:
    """出入库单表单此刻的内容（还没存）→ :func:`prepare` 要的单（提交前问要人定的项用，`api.preflight`）。只看单别与日期：
    要问的只有科目，与明细无关。"""
    direction = "out" if str(payload.get("direction") or "") == "out" else "in"
    return {"ref": "preflight", "direction": "stock", "doctype": kind({"direction": direction}),
            "date": str(payload.get("posting_date") or frappe.utils.nowdate()), "number": "-", "remark": "",
            "loccod": "", "lines": []}


__all__ = ["ACCOUNT", "ADJUST", "COUNT", "COUNT_DOCTYPE", "DOCTYPE", "ISSUE", "VOID_REQUEST", "account_subject", "company_of",
           "count_document", "document", "eligible", "form_body", "kind", "prepare", "void_payload"]
