"""ERP 采购 / 销售单写进 Express（CC 桌面版直连 ERP，ADR-025），并把结果（Express 单号、停下的原因）记回 ERP 单据。

所有入口（网页手录、网页拍照、LINE 拍照）都汇到 `sales.entry.post` / `purchasing.entry.post`，提交前调 :func:`queue`：这张单该写
（是真进货 / 真销售，本公司的账套有 CC 桌面版报过）就标「排队中」，事务提交后在后台准备（:func:`send`）：选账套、按共用写账规则
备好载荷（`prepare`），交进共用机器通道的写账队列（`pearnly_core.cc_desktop.jobs`，录入人 = 开这张单的人）。之后由电脑领、写、
回报；每次状态变了，共用通道在同一事务里交回 :func:`job_changed`，单据上记 Express 单号或原因（HTTP 成功、交进队列都不算写入）。

停下的单（要人选的、没有账套、没有电脑能写、主档还在读）记原因；会自己好的与没有账套的（`contracts.POLL_REASONS`）每分钟再准备一次
（:func:`poll`），要人选的在单据上选一次（`api.answer`）后再准备。ERP 自己的库存照旧按单过账，写进 Express 后由主档同步对齐。

写不写只有一条判据（主会话 11-01）：公司勾着「接 Express」、有「接上时刻」（:func:`linked_at`，第一次主档同步落库时记，断开清空），
且单据开始时刻（:func:`started`：盘点 = 开盘点，其余 = 建单，采购「修改」出的新单沿原单）不早于它。不看此刻 CC 桌面版报没报账套：
提交时正好没报的照排，停在「没有账套」每分钟再试。断开优先：断开时这家公司还没写进去的单撤掉、标「已断开」（:func:`detach`）。
"""
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_core.cc_desktop import books, devices, jobs
from pearnly_core.cc_desktop.wire import ack_rules
from pearnly_core.express_rules.contracts import pos as pos_contract

from pearnly_erp_portal.express import choices, contracts, conversation, pos_day, prepare, printed, stock_entry
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal import mrerp
from pearnly_erp_portal.mrerp import contracts as mrerp_contracts, push as mrerp_push

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"
STOCK = stock_entry.DOCTYPE  # 出入库单 / 盘点调整（载荷 6，出入库第一期 B 段）
POS_DAY = pos_day.DOCTYPE  # 收银日汇总（载荷 3 的 POS 日汇总现销，B3a）
COUNT = stock_entry.COUNT_DOCTYPE  # 已过账的盘点：整份轧差写一张盘点调整 TK（载荷 6，验收 10-25）
DIRECTIONS = {SALES: "sales", PURCHASE: "purchase", STOCK: "stock", POS_DAY: "sales", COUNT: "stock"}
READY = {COUNT: "Posted"}  # 单据「可以写了」的状态（其余是 submitted）


def ready(doctype) -> str:
    return READY.get(doctype, "submitted")
POLL_MAX = 100
COMPANY = "ERP Company"


def _company(name):
    return frappe.db.get_value(COMPANY, name, ["name", "company_name", "tax_id", "branch"], as_dict=True) if name else None


def connected(company) -> bool:
    """CC 桌面版报过这家公司（税号 + 分店）的账套（不看公司上的「接 Express」）。"""
    row = _company(company)
    return bool(row and contracts.tax13(row.tax_id) and books.for_company(row.tax_id, row.branch))


def linked(company) -> bool:
    """这家公司此刻接着 Express：公司勾了「接 Express」（用户 10-29）且已连上。同步、建仓、期初、页面开关按它；单据写不写按
    :func:`started_linked`。"""
    return bool(company and frappe.db.get_value("ERP Company", company, "express_enabled") and connected(company))


def enabled(company) -> bool:
    return bool(company and frappe.db.get_value(COMPANY, company, "express_enabled"))


def linked_at(company):
    """这家公司的「接上时刻」：勾着「接 Express」且第一次主档同步落库后才有（`masters.apply`），断开 / 取消勾选清空、重新接上重记。"""
    row = frappe.db.get_value(COMPANY, company, ["express_enabled", "express_linked_at"], as_dict=True) if company else None
    return frappe.utils.get_datetime(row.express_linked_at) if row and row.express_enabled and row.express_linked_at else None


AMEND_DEPTH = 50  # 采购「修改」链最多往回找几张（正常只有几张；防坏数据成环）


def started(doctype, name):
    """单据的开始时刻：建单（含草稿）的时刻，盘点是开盘点的时刻；采购「修改」出的新单沿 ``corrected_from`` 取最早那张的。"""
    row = frappe.db.get_value(doctype, name, ["creation", "corrected_from"] if doctype == PURCHASE else ["creation"],
                              as_dict=True) if name else None
    seen = {name}
    while row and doctype == PURCHASE and row.corrected_from and row.corrected_from not in seen and len(seen) < AMEND_DEPTH:
        seen.add(row.corrected_from)
        row = frappe.db.get_value(doctype, row.corrected_from, ["creation", "corrected_from"], as_dict=True) or row
    return frappe.utils.get_datetime(row.creation) if row and row.creation else None


def counts_from(company, at) -> bool:
    """``at`` 开始的单这家公司写不写进 Express：勾着且接上了、开始时刻不早于接上时刻。"""
    since = linked_at(company)
    return bool(since and at and frappe.utils.get_datetime(at) >= since)


def started_linked(doctype, name, company=None) -> bool:
    """这张单开始时这家公司已接上 Express（:func:`counts_from`）：各入口写不写、随单建的主档归不归这家公司都按它。"""
    company = company or company_of(doctype, name)
    return counts_from(company, started(doctype, name))


def owner(company, at):
    """随单 / 盘点里新建的客户、供应商、商品归哪家公司：单据开始时已接上的归这家（写进去后挂码），否则是各家共用的本地档。"""
    return company if company and counts_from(company, at or frappe.utils.now_datetime()) else None


def company_of(doctype, name):
    """单据属于哪家公司：采购 / 销售单记在单上；出入库单按单头仓库的公司。"""
    if doctype in (STOCK, COUNT):
        return stock_entry.company_of(name, doctype)
    return frappe.db.get_value(doctype, name, "company")


def _eligible(doctype, doc) -> bool:
    if doc.get("status") != ready(doctype):
        return False
    if doctype == STOCK:
        kind_ok = stock_entry.eligible(doc)
    elif doctype in (POS_DAY, COUNT):
        kind_ok = True  # 收银日汇总只收接上之后卖的小票（`pos_day`）
    else:
        kinds = contracts.SALES_KINDS if doctype == SALES else contracts.PURCHASE_KINDS
        kind_ok = (doc.get("doc_kind") or "") in kinds
    return kind_ok and started_linked(doctype, doc.name, doc.get("company"))


def queue(doctype, name):
    """提交事务里调（`post` 的 ``finalize`` 之后、提交之前）：该写（:func:`_eligible`）就标「排队中」并排提交后的准备；不该写
    什么都不做。提交时 CC 桌面版正好没报账套也排上，停在「没有账套」等它回来（原来那一刻判没接上，这张单就永远不写）。"""
    if doctype not in DIRECTIONS:
        return
    fields = ["name", "status", "ref_type", "direction", "ledger_state"] if doctype == STOCK else \
        ["name", "status", "ledger_state"] if doctype == COUNT else \
        ["name", "status", "company", "ledger_state"] if doctype == POS_DAY else \
        ["name", "status", "doc_kind", "company", "ledger_state"]
    doc = frappe.db.get_value(doctype, name, fields, as_dict=True)
    if doc and doctype in (STOCK, COUNT):
        doc.company = stock_entry.company_of(name, doctype)
    if doc and mrerp_push.handles(doc.company):
        # 这家公司的账本是 MR.ERP（`pearnly_erp_portal.mrerp`，功能开着才有）：交给 MR.ERP 导入直写，不排 Express
        mrerp_push.queue(doctype, doc)
        return
    if not doc or doc.ledger_state or not _eligible(doctype, doc):
        return
    frappe.db.set_value(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_reason": "",
                                        "ledger_target": contracts.LEDGER_EXPRESS}, update_modified=False)
    carried = conversation.carried()  # CC 引导确定时提交的：后台按那段对话写（本对话账套、写入计划）
    frappe.enqueue("pearnly_erp_portal.express.push.send", queue="short", enqueue_after_commit=True,
                   doctype=doctype, name=name, **({"session": carried} if carried else {}))


def ref(name, creation) -> str:
    """单据标识：单号 + 建单时刻。单号在一个站点里不重复，但站点从备份恢复、重建后会从头再编，带上建单时刻免得撞上以前写过的
    同号单（写账队列按它认是不是同一件）。"""
    stamp = frappe.utils.get_datetime(creation).strftime("%Y%m%d%H%M%S%f") if creation else ""
    return f"{name}@{stamp}" if stamp else str(name)


def _party(doctype, doc):
    if doctype == SALES:
        row = frappe.db.get_value("ERP Customer", doc.customer, ["express_code", "customer_name", "tax_id", "branch_no"],
                                  as_dict=True) if doc.customer else None
        return {"code": (row or {}).get("express_code") or "", "name": doc.buyer_name or (row or {}).get("customer_name"),
                "tax_id": (row or {}).get("tax_id") or "", "branch": (row or {}).get("branch_no") or ""}
    row = frappe.db.get_value("ERP Supplier", doc.supplier, ["express_code", "supplier_name", "tax_id", "branch_no"],
                              as_dict=True) if doc.supplier else None
    return {"code": (row or {}).get("express_code") or "", "name": doc.supplier_name or (row or {}).get("supplier_name"),
            "tax_id": doc.supplier_tax_id or (row or {}).get("tax_id") or "",
            "branch": doc.supplier_branch_no or (row or {}).get("branch_no") or ""}


def _lines(doctype, doc):
    products, warehouses, out = {}, {}, []
    # 采购单 0 税率的行标免税：混合税率（有的行 0）照旧；勾了税务发票（has_vat）而全是 0% 的整单免税也标（主会话 10-29：语料 158 本
    # 免税单原生一律价格类型 1 / 2 + 明细 VATCOD '0' + 零税额）。CC 桌面版写价含税的现购时不摊税给它、明细记 VATCOD '0'
    # （验收复核测试包 e2 10-26；同收银日汇总的免税行）。没勾税务发票的整张无税不标（供应商不开税票，原生 FLGVAT 0）
    exempt_rates = doctype == PURCHASE and (bool(getattr(doc, "has_vat", 0)) or any(_rate(line) > 0 for line in doc.lines))
    units = stock_entry.line_units(doc)
    for index, line in enumerate(doc.lines, start=1):
        product = products.get(line.product)
        if line.product and product is None:
            product = products[line.product] = frappe.db.get_value(
                "ERP Product", line.product, ["product_name", "express_code", "express_stock", "base_unit", "unit",
                                              "serial_kind"], as_dict=True) or {}
        product = product or {}
        if line.warehouse and line.warehouse not in warehouses:
            warehouses[line.warehouse] = frappe.db.get_value("ERP Warehouse", line.warehouse, "express_code") or ""
        code = product.get("express_code") or ""
        # 动不动库存按行定（11-06 按行选物料与库存，评估 4.4）：只记账的行即使挂的是 Express 存货也写成非库存行、不带存货码
        # （按名写非库存品；评估 12 节 #6 允许，不拦）
        goods = (line.get("item_type") or "goods") == "goods"
        if code and goods:
            stock = bool(product.get("express_stock"))
        elif code and product.get("express_stock"):
            stock, code = False, ""
        else:
            stock = goods and bool(line.product)
        # 包装单位（箱 = 12）：带上 ERP 商品单位表的换算率（CC 桌面版按 Express 主档的包装单位照原生记法写，认不出时按它换；验收 10-25）
        factor = stock_entry.base_unit(line.product, product, line.unit, strict=False)[1] if stock and line.product else None
        out.append({"name": line.description or product.get("product_name") or code, "code": code, "unit": line.unit,
                    "qty": line.qty, "amount": line.line_total, "stock": stock, "warehouse": line.warehouse or "",
                    "loccod": warehouses.get(line.warehouse, "") if line.warehouse else "",
                    **({"factor": factor} if factor and factor != 1 else {}),
                    **({"vat_exempt": True} if exempt_rates and not _rate(line) > 0 else {}),
                    **stock_entry.remarks_of(units.get(index), product)})
    return out



def _rate(line):
    return contracts.d(line.get("vat_rate") or 0)


def form_document(doctype, payload, company) -> dict:
    """表单此刻的内容（还没存）→ 交给写账规则的单（与 :func:`document` 同一个形状）：提交前问要人定的项用
    （`api.preflight`）。金额用表单同一套计算（`entry.preview`），对方档、存货码、仓库码照表单选的主档取。"""
    if doctype == SALES:
        from pearnly_erp_portal.sales import entry

        totals = entry.preview(payload)
        row = frappe.db.get_value("ERP Customer", payload.get("customer"), ["express_code", "customer_name", "tax_id",
                                  "branch_no"], as_dict=True) if payload.get("customer") else None
        party = {"code": (row or {}).get("express_code") or "",
                 "name": payload.get("buyer_name") or (row or {}).get("customer_name") or "-",
                 "tax_id": (row or {}).get("tax_id") or payload.get("buyer_tax_id") or "",
                 "branch": (row or {}).get("branch_no") or payload.get("buyer_branch_no") or ""}
        paid = str(payload.get("is_paid", 1)) not in ("0", "False", "false", "")
        number, currency = payload.get("system_no") or "-", "THB"
    else:
        from pearnly_erp_portal.purchasing import entry

        totals = entry.preview(payload)
        row = frappe.db.get_value("ERP Supplier", payload.get("supplier"), ["express_code", "supplier_name", "tax_id",
                                  "branch_no"], as_dict=True) if payload.get("supplier") else None
        party = {"code": (row or {}).get("express_code") or "",
                 "name": payload.get("supplier_name") or (row or {}).get("supplier_name") or "-",
                 "tax_id": payload.get("supplier_tax_id") or (row or {}).get("tax_id") or "",
                 "branch": payload.get("supplier_branch_no") or (row or {}).get("branch_no") or ""}
        paid = str(payload.get("is_paid") or 0) not in ("0", "False", "false", "")
        number, currency = payload.get("doc_no") or payload.get("system_no") or "-", payload.get("currency") or "THB"
    from pearnly_erp_portal.inventory import warehouses

    stocked = str(payload.get("update_stock", 1)) not in ("0", "False", "false", "")
    # 认不到物料的入库 / 出库行提交时随单建档（采购；销售收据同口径，11-06 按行选物料与库存）
    creates = doctype == PURCHASE or str(payload.get("doc_kind") or "receipt") == "receipt"
    lines = []
    for line, computed in zip(payload.get("lines") or [], totals.get("lines") or []):
        line = frappe._dict({**line, "line_total": computed.get("line_total")})
        if not line.get("warehouse") and payload.get("warehouse"):
            line.warehouse = payload.get("warehouse")  # 单头仓给行上没写的（同提交时 `positions.prepare`，多仓闭环 P1）
        goods = (line.get("item_type") or "goods") == "goods"
        if goods and not line.get("product"):
            line.product = _matched_product(line, company)  # 提交时同样按条码 / 编码 / 品名认（不建档）
        if goods and stocked and (creates or line.product) and not warehouses.receiving(
                line.get("warehouse"), company=company):
            # 提交时也会这样拒（`positions.prepare`）：先报表单的错，别让人答完 Express 的选择又被打回（10-24 验收）
            frappe.throw(_("Warehouse not found"))
        lines.append(line)
    out = _lines(doctype, frappe._dict({"lines": lines}))
    for line, item in zip(lines, out):
        if creates and (line.get("item_type") or "goods") == "goods" and not line.get("product"):
            # 认不到的入库 / 出库行提交时自动建档（采购，销售收据同口径）：按库存品准备，不误问费用科目（10-24 审查）
            item["stock"] = True
    company_row = frappe.db.get_value(COMPANY, company, ["tax_id", "branch"], as_dict=True) or {}
    return contracts.document(ref="preflight", direction=DIRECTIONS[doctype], doc_date=str(payload.get("doc_date") or
                              frappe.utils.nowdate()), number=number, company=company_row, party=party, paid=paid,
                              has_vat=str(payload.get("has_vat", 1)) not in ("0", "False", "false", ""),
                              total=totals.get("grand_total"), vat=totals.get("vat_amount"), lines=out,
                              currency=currency)


def _matched_product(line, company):
    """没选商品的明细行：照提交时的认法（`products.purchase.resolve` / `products.sales.resolve`）认一件，认不出或不止一件回 None。"""
    from pearnly_erp_portal.masters.selection import match_product
    from pearnly_erp_portal.products import service

    for field in ("barcode", "code"):
        found = service.find(field, line.get(field), company)
        if found:
            return found
    name, count = match_product(line.get("description") or "", company)
    return name if count == 1 else None


def document(doctype, name) -> dict:
    """这张 ERP 单交给写账规则的样子（`contracts.document`）；出入库单是库存单的样子（`stock_entry.document`）。"""
    if doctype == STOCK:
        return stock_entry.document(name, ref(name, frappe.db.get_value(STOCK, name, "creation")))
    if doctype == POS_DAY:
        return pos_day.document(name, ref(name, frappe.db.get_value(POS_DAY, name, "creation")))
    if doctype == COUNT:
        return stock_entry.count_document(name, ref(name, frappe.db.get_value(COUNT, name, "creation")))
    doc = frappe.get_doc(doctype, name)
    company = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    if doctype == SALES:
        number, paid, currency = doc.system_no, doc.payment_status == "paid", "THB"
    else:
        number, paid = doc.doc_no or doc.system_no, bool(doc.is_paid) or doc.payment_status == "paid"
        currency = doc.currency or "THB"
    lines = _lines(doctype, doc)
    body = contracts.document(ref=ref(doc.name, doc.creation), direction=DIRECTIONS[doctype],
                              doc_date=str(doc.doc_date), number=number,
                              company=company, party=_party(doctype, doc), paid=paid, has_vat=bool(doc.has_vat),
                              total=doc.grand_total, vat=doc.vat_amount, lines=lines, currency=currency)
    missing = contracts.unmapped({line["warehouse"]: line["loccod"] for line in lines if line["stock"] and line["warehouse"]})
    if missing:
        body["unmapped_warehouses"] = missing
    if any(item.get("vat_exempt") for item in body["items"]):
        fields = vat_fields(body, _exempt_base(doc))
        if fields:
            body["vat_fields"] = fields
    return body


def _exempt_base(doc):
    """采购单 0 税率各行扣了整单折扣后的计税基数之和（按这张单自己的计算版本重算，`amounts.for_document`；验收复核 e3：
    明细的 line_total 是整单折扣分摊前的，与扣了折扣的税前合计口径不同）。很早的单（计算版本 1）没有整单折扣先于 VAT，照行金额。"""
    if (doc.get("calculation_version") or 0) >= 2:
        from pearnly_erp_portal.purchasing import amounts

        rows = amounts.for_document(doc)["lines"]
        return sum((contracts.d(row["vat_base"]) for row in rows if not contracts.d(row["vat_rate"]) > 0), contracts.d(0))
    return sum((contracts.d(line.line_total) for line in doc.lines if not _rate(line) > 0), contracts.d(0))


def vat_fields(body, exempt) -> dict:
    """采购单有免税行（0 税率的行标了免税，见 :func:`_lines`）：进项税记录 AMT01 只记应税部分、免税部分记 AMTRAT0（单头同记），
    同原生（S 盘副本 HP67012204：AMT01 383.18 = 税前 410.18 − 免税 27；整单免税 RR680121-011：AMT01 0、AMTRAT0 全额）。
    ``exempt`` = 免税行扣了整单折扣后的税前额。算法在共用写账规则（`express_rules.contracts.pos.exempt_vat_fields`）。"""
    return pos_contract.exempt_vat_fields(contracts.d(body["subtotal"]), contracts.d(exempt))


def _set(doctype, name, values):
    """写单据上的账本栏（通用列 ``ledger_*``，现在账本只有 Express；不改修改时间）。改到状态或原因时，这张单进出 ERP 版 CC 的「待处理」就给能看这家公司的人推一次
    （`pending.notify`，提交后）；LINE 上有人在等这张的结果（卡片「保存」后），叫醒它回结果（提交后，用户 11-22）。"""
    from pearnly_erp_portal.express import pending

    before = pending.ledger_of(doctype, name) if {"ledger_state", "ledger_reason"} & set(values) else None
    frappe.db.set_value(doctype, name, values, update_modified=False)
    if before is not None:
        pending.notify(doctype, name, before)
    pending.line_changed(doctype, name, values)


def _prepare_body(doctype, company, body):
    """按写账规则准备一张（`send` 与代录预检 `prepare_only` 同一条路）：回 ``(结论, vat_fields, 没有 Express 码的仓)``。
    结论同 ``prepare.prepare``：``{"payload", "book"}`` / ``{"hold", "needed"}`` / ``{"failed"}``。"""
    from pearnly_erp_portal.express import masters

    vat_fields = body.pop("vat_fields", None)
    unmapped = body.pop("unmapped_warehouses", None)
    # 盘点当场新建、Express 中没有的商品（``unmapped_items``）：`stock_entry.prepare` 按存货表问一次选现有或新建（P3x）；
    # 存货表还没读到时停在 item_unmapped
    if doctype == POS_DAY and "hold" in body:
        return body, vat_fields, unmapped  # 还不知道这家公司是不是增值税登记户：在单据上问一次（`pos_day.document`）
    prepared = stock_entry.prepare if doctype in (STOCK, COUNT) else prepare.prepare
    return prepared(body, _company(company), masters.ensure), vat_fields, unmapped


def prepare_only(doctype, name) -> dict:
    """``send`` 的准备那一半：不写单据、不排写账件（代录预检用，主会话 10-27「预检必须走产品的准备、新商品 / 要人选的项预检里
    就报」）。回准备的结论；准备好了但单上的仓没有 Express 码时回 ``{"hold": WAREHOUSE_UNMAPPED}``（与 `send` 同一条规则）。"""
    stop = printed.hold(doctype, name)
    if stop:
        return {"hold": stop}
    result, _vat_fields, unmapped = _prepare_body(doctype, company_of(doctype, name), document(doctype, name))
    if unmapped and "failed" not in result and "hold" not in result:
        return {"hold": contracts.WAREHOUSE_UNMAPPED, "book": result.get("book") or {}}
    return result


OPERATOR_KEY = "erp:operator:"  # 代录交的写账件的请求号前缀（`send`）


def operator_owned(doctype, name) -> bool:
    """这张单归代录：最近一件写账是代录交的（请求号带 ``OPERATOR_KEY``）、还没录成也没撤掉。归代录的单直写不接。"""
    found = jobs.of_source(doctype, name)
    last = found[-1] if found else None
    return bool(last) and str(last.get("request_key") or "").startswith(OPERATOR_KEY) \
        and last.get("status") not in ("success", "cancelled")


def release_operator(doctype, name) -> bool:
    """代录再录一次前：这张单上一件是代录交的、转人工了（停在要人处理），撤掉它、单据回到排队，好让代录重新交（同「再写一次」
    撤旧件的做法，只是之后仍交代录）。不是代录的件、或件还在排队 / 在写，什么都不做。"""
    current = frappe.db.get_value(doctype, name, "ledger_job")
    job = jobs.find_by_name(current) if current else None
    if not job or job.get("status") != "manual" or not str(job.get("request_key") or "").startswith(OPERATOR_KEY):
        return False
    jobs.cancel(job["job"], "replaced")
    _set(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_job": None, "ledger_reason": "", "ledger_needed": None})
    return True


# 写入计划里这张单的编号（`cc_tools` 的 ``<类>:<单号>``，与写账前置判定的单号；出入库单两边前缀不同，两种都认）
PLAN_PREFIXES = {PURCHASE: ("P",), SALES: ("S",), STOCK: ("M", "T"), POS_DAY: ("D",), COUNT: ("C",)}


def intake_ids(doctype, name) -> list:
    """这张单是 CC 对话收单记录提交出来的：那条记录的编号（``I:<记录>``，引导「确定」时写入计划里记的是它）。"""
    link = {PURCHASE: "purchase", SALES: "sales_document"}.get(doctype)
    if not link:
        return []
    return [f"I:{record}" for record in frappe.get_all("ERP LINE Intake", filters={link: name, "source": "cc"}, pluck="name",
                                                       limit_page_length=5)]


def plan_refusal(doctype, name) -> dict | None:
    """CC 对话里直写（非代录）推之前问写入计划（`write_plan.permit`，A 线 B：写入只走计划，下沉到推送入口）：在本对话已开始的
    计划里回 None；不在回 ``{"status": "not_pushed", "reason", "note"}``，单据不动。不在 CC 对话里执行（网页、LINE、每分钟重试、
    定时、代录交单另走 `express_operator`）不经这里。"""
    if not conversation.session():
        return None
    from pearnly_core.assistant import write_plan

    ids = [f"{prefix}:{name}" for prefix in PLAN_PREFIXES.get(doctype, ())]
    ids += intake_ids(doctype, name)
    allowed, refused = write_plan.permit(ids)
    if allowed or not refused:
        return None
    reason = next(iter(refused.values()))
    return {"status": "not_pushed", "reason": reason, "note": write_plan.PERMIT_NOTES.get(reason, "")}


def send(doctype, name, operator=None, session="", poll=False):
    """准备并交进写账队列（后台任务；停下的单每分钟由 :func:`poll` 再叫）。已经交进去的不再交。

    ``operator``：代录 Express 的批次信息（共用层 ``pearnly_core.express_operator``，CC「代录」里交的）：同一份载荷加代录标记
    （载荷 16），由 CC 桌面版在 Express 界面里录、不直写；回执照直写同一条（`job_changed`）。回这次的结论（代录交单用）。
    代录按批次的任务号（``task``）取任务会话的本对话账套（`conversation.for_task`）。CC 对话里直写只推已开始计划里的单
    （:func:`plan_refusal`）。"""
    if doctype not in DIRECTIONS:
        return
    if session and not operator:
        # CC 引导确定时提交、排进后台的（`queue` 带来的对话）：照那段对话的本对话账套与写入计划写
        with conversation.for_session(session):
            return send(doctype, name)
    if not operator:
        refused = plan_refusal(doctype, name)
        if refused:
            return refused
        return _send(doctype, name, None, poll=poll)
    with conversation.for_task((operator or {}).get("task")):
        return _send(doctype, name, operator)


# 写回前再核的账本栏与单据状态（准备途中提交过、作废恰好在这个空档完成：状态变了也放弃这次排件，11-28 B ③）
LEDGER_SEEN = ("ledger_state", "ledger_job", "ledger_reason", "ledger_book", "ledger_needed", "status")


def _ledger_seen_of(row) -> tuple:
    return tuple(str(row.get(field) or "") for field in LEDGER_SEEN)


def _ledger_seen(doctype, name) -> tuple:
    """锁内（SELECT … FOR UPDATE）重读这张单此刻的账本栏。"""
    return _ledger_seen_of(frappe.db.get_value(doctype, name, list(LEDGER_SEEN), as_dict=True, for_update=True) or {})


def _polled(row) -> bool:
    """每分钟巡检（:func:`poll`）该交的单：排队中、还没有件，或停在会自己好的原因上（:data:`contracts.POLL_REASONS`）。"""
    return not row.ledger_job and (row.ledger_state == contracts.QUEUED or (row.ledger_reason or "") in contracts.POLL_REASONS)


def _send(doctype, name, operator, poll=False):
    if not operator and operator_owned(doctype, name):
        return {"status": "held", "reason": contracts.OPERATOR_OWNED}
    # 行锁（原生 SELECT … FOR UPDATE，锁到这次准备写回、提交为止）：同一张单的几次准备（提交后的后台一轮、每分钟 poll、答题后
    # `api.answer`、代录交单、收银日汇总合成后的同步交）一次只进一个，后来的在锁里读到已排的件就退出（11-16 验收：两次准备并行，
    # 晚落库的那次把答题后的账套与要问的项盖回去；按件数现算请求号时还会排出第二件、写两次账）
    row = frappe.db.get_value(doctype, name, ["name", "creation", "status", "owner", *LEDGER_SEEN, "ledger_target"],
                              as_dict=True, for_update=True)
    if row and row.ledger_target == mrerp_contracts.LEDGER_MRERP and mrerp.enabled():
        return mrerp_push.send(doctype, name)  # 账本是 MR.ERP 的单（「再写一次」、对话直写）：走 MR.ERP 导入直写
    if not row or row.status != ready(doctype) or row.ledger_job or row.ledger_state not in contracts.OPEN_STATES:
        return {"status": "already_queued" if row and row.ledger_job else "not_ready"}
    if poll and not _polled(row):
        # 巡检用普通读列出这张时它还该交，拿到锁时已被别的路径改了（转成要人选、被人改原因…）：按锁内的现值放弃，不准备、不排件
        # （11-28 B ③b：按旧值决定要不要写）。人工「再写一次」、答题后的 `resume` 不带 ``poll``，照原路径
        return {"status": "skipped", "reason": row.ledger_reason or ""}
    company = company_of(doctype, name)
    if not enabled(company):
        # 排上之后这家公司断开了（断开时撤单与这一件同时发生）：不写，照实标已断开
        _set(doctype, name, {"ledger_state": contracts.DETACHED, "ledger_reason": contracts.DISCONNECTED,
                             "ledger_needed": None})
        frappe.db.commit()
        return {"status": "held", "reason": contracts.DISCONNECTED}
    stop = printed.hold(doctype, name)
    if stop:
        _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_reason": stop, "ledger_needed": None})
        frappe.db.commit()
        return {"status": "held", "reason": stop}
    body = document(doctype, name)
    if doctype == POS_DAY and body.get("net_negative"):
        # 同一天的退货冲减后是负数（共用约定 `net_day`）：不硬写，停下交人
        _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_reason": pos_day.NET_NEGATIVE, "ledger_needed": None})
        frappe.db.commit()
        return {"status": "held", "reason": pos_day.NET_NEGATIVE}
    if (doctype in (STOCK, COUNT) and not body["lines"]) or (doctype == POS_DAY and body.get("empty")):
        # 整份盘点 / 物料调整单轧差后没有要调的（只有仓位之间挪动）、收银日一天全退（冲减后全部为 0）：Express 里什么都不用写
        _set(doctype, name, {"ledger_state": None, "ledger_reason": "", "ledger_needed": None})
        frappe.db.commit()
        return {"status": "nothing_to_write"}
    result, vat_fields, unmapped = _prepare_body(doctype, company, body)
    # 写回前再核：准备途中可能提交过（主档落地「不带着事务等」，`masters`），行锁随之放开；锁内重读，账本栏与开始时不一样
    # （这期间有人答了题、另一次准备已排件或停下）就放弃这次的结论，以后到的为准，不盖回旧的「停下、要问」、不再排一件
    if _ledger_seen(doctype, name) != _ledger_seen_of(row):
        frappe.db.rollback()
        now = frappe.db.get_value(doctype, name, ["ledger_state", "ledger_job", "ledger_reason"], as_dict=True) or {}
        if now.get("ledger_job"):
            return {"status": "already_queued"}
        return {"status": "held" if now.get("ledger_state") == contracts.HELD else "not_ready",
                "reason": str(now.get("ledger_reason") or "")[:300]}
    book = result.get("book") or {}
    if "failed" in result:
        _set(doctype, name, {"ledger_state": contracts.FAILED, "ledger_reason": result["failed"][:500],
                             "ledger_needed": None})
    elif "hold" in result:
        _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_reason": result["hold"][:500],
                             "ledger_book": book.get("key") or None,
                             "ledger_needed": json.dumps(result.get("needed") or [], ensure_ascii=False) or None})
        if result["hold"].startswith(contracts.CHOICE) and result["hold"] != (row.ledger_reason or ""):
            # 提交时还问不了（主档那时还没读到），读到后才发现要人选：同样主动弹出，只在要问的变了时推一次
            frappe.publish_realtime(NEEDED_EVENT, {"doctype": doctype, "name": name}, user=row.owner, after_commit=True)
    elif unmapped:
        # 单上的仓在 Express 里没有对应的仓库（主档同步已读过、仍没有码）：不写进 Express 主仓，停下交人（G15）；
        # 在 Express 建好同名仓库、主档同步认领后「再写一次」，或在 Express 里手工处理后标记已处理
        _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_reason": contracts.WAREHOUSE_UNMAPPED,
                             "ledger_book": book.get("key") or None, "ledger_needed": None})
    else:
        _set(doctype, name, {"ledger_book": book["key"], "ledger_needed": None, "ledger_sent_at": now_datetime()})
        payload = result["payload"]
        if operator:
            from pearnly_core.express_operator import contracts as operator_contracts

            # 定制菜单的子菜单多个候选（评估 2026-11-05 修法 D）：这家这份账套这种单记住的菜单文字，与按单据付款方式认的现金 / 转账
            payload = operator_contracts.for_operator(
                payload, operator, form_choice=choices.operator_form(company, book["key"], payload.get("doctype")),
                pay_route=operator_pay_route(doctype, name),
                # 中途配方外的业务选择框这家公司答过的（CC 桌面版 1.12.23 照点，旧版忽略）
                dialog_answers_by_digest=choices.operator_dialogs(company))
        if vat_fields:
            # 混有免税商品的现销日汇总 / 混合税率的采购：税记录的应税基数按规则交（载荷 9，旧版 CC 诚实拒收）
            payload = {**payload, "vat_fields": vat_fields,
                       "payload_version": max(int(payload.get("payload_version") or 1), pos_day.VAT_FIELDS_PAYLOAD_VERSION)}
        # 一张单同时最多一件没撤销的写账件（作废件另算）：清掉 `ledger_job` 重交的路径（再写一次、改选对方档、代录再录）
        # 都先撤旧件；锁里仍见没撤销的就不再排（接回它），不会因并发或漏撤排出第二件、写两次账
        earlier = jobs.of_source(doctype, name)
        live = [job for job in earlier if job["status"] != "cancelled"
                and not str(job.get("request_key") or "").startswith(stock_entry.VOID_REQUEST)]
        if live:
            _set(doctype, name, {"ledger_job": live[-1]["job"]})
            frappe.db.commit()
            return {"status": "already_queued"}
        # 撤掉的那几件（与作废件）占着原来的请求号：后面的按第几件编号。锁里、且已核过没有没撤销的件，这个数只在显式撤旧件
        # 再交时增加，不会因并发现算成下一件（编号算法与线上已排的件一致）
        key = (f"{OPERATOR_KEY}{operator['id']}:" if operator else f"erp:{DIRECTIONS[doctype]}:") + body['ref'] \
            + (f"#{len(earlier) + 1}" if earlier else "")
        job = jobs.enqueue(source_doctype=doctype, source_name=name, request_key=key,
                           actor=actor_of(doctype, row, book["key"]), book_key=book["key"], payload=payload, payload_version=int(payload.get("payload_version") or 1),
                           label=body["number"])
        _set(doctype, name, {"ledger_job": job["job"]})
    frappe.db.commit()
    if "failed" in result:
        return {"status": "failed", "reason": result["failed"][:300]}
    if "hold" in result:
        return {"status": "held", "reason": result["hold"][:300]}
    if unmapped:
        return {"status": "held", "reason": contracts.WAREHOUSE_UNMAPPED}
    return {"status": "sent"}


def operator_pay_route(doctype, name) -> str:
    """代录载荷 ``operator.pay_route``：已付清 / 已收清的采购、销售单认现金 / 转账，定不下来回空
    （`pearnly_core.express_operator.menu_choice.pay_route`）。采购的付款明细（不算冲销行）方式全一致就按明细；明细不一致或没有明细，
    按单头「付款方式」（``payment_method``）。没付清的单不给（赊购 / 赊销；采购的付款方式默认是现金，不算数）。"""
    from pearnly_core.express_operator.menu_choice import pay_route

    if doctype == PURCHASE:
        row = frappe.db.get_value(doctype, name, ["is_paid", "payment_status", "payment_method"], as_dict=True) or {}
        paid = bool(row.get("is_paid")) or row.get("payment_status") == "paid"
    elif doctype == SALES:
        row = frappe.db.get_value(doctype, name, ["payment_status", "payment_method"], as_dict=True) or {}
        paid = row.get("payment_status") == "paid"
    else:
        return ""
    if not paid:
        return ""
    methods = set()
    if doctype == PURCHASE:
        methods = {str(item.get("method") or "") for item in frappe.get_all(  # bounded: 一张单的付款明细
            PURCHASE_PAYMENT, filters={"parent": name, "parenttype": doctype}, fields=["method", "is_reversal"],
            limit_page_length=PAYMENT_ROWS_MAX) if not item.get("is_reversal") and item.get("method")}
    return pay_route(methods.pop() if len(methods) == 1 else row.get("payment_method"))


PURCHASE_PAYMENT = "ERP Purchase Payment"
PAYMENT_ROWS_MAX = 200  # 一张采购单的付款明细（正常几行）


def mrerp_run(doctype, name):
    """（后台任务）账本是 MR.ERP 的单导入一次（`mrerp.push.run`）：建单据仍用本模块的 :func:`document`（交给写账规则的同一形状）。"""
    return mrerp_push.run(doctype, name, document)


def detach(company) -> int:
    """断开 Express（公司页「断开」、取消勾选）：这家公司排队中、停下的单不再写 —— 还没被电脑领走的写账件撤掉，单据标「已断开」
    （原因 :data:`contracts.DISCONNECTED`），页面照常看得见；电脑正在写的撤不了，结果回来照记（写成了记写入，没写成标已断开，
    见 :func:`job_changed`）。已写进 Express 的单不动（之后在 ERP 作废照样排 Express 作废）。回标了几张。"""
    from pearnly_erp_portal.inventory import warehouses

    count = 0
    for doctype in DIRECTIONS:
        mine = ({"warehouse": ["in", warehouses.names_of(company) or [""]]} if doctype in (STOCK, COUNT)
                else {"company": company})
        filters = {"ledger_state": ["in", contracts.OPEN_STATES], **mine}
        skipped = []
        while True:
            names = frappe.get_all(doctype, filters={**filters, "name": ["not in", skipped or [""]]},  # bounded: 一批 POLL_MAX，改完不再命中
                                   pluck="name", order_by="modified asc", limit_page_length=POLL_MAX)
            for name in names:
                job_name = frappe.db.get_value(doctype, name, "ledger_job")
                job = jobs.find_by_name(job_name) if job_name else None
                if job and job["status"] in ("queued", "manual"):
                    jobs.cancel(job["job"], "disconnected")  # 回执记回单据（`job_changed`），随后改成已断开
                elif job and job["status"] not in ("cancelled", "done"):
                    skipped.append(name)  # 电脑正在写：等结果
                    continue
                _set(doctype, name, {"ledger_state": contracts.DETACHED, "ledger_reason": contracts.DISCONNECTED,
                                     "ledger_needed": None})
                count += 1
            if len(names) < POLL_MAX:
                break
    return count


VOIDED = "voided"


def void(doctype, name):
    """ERP 作废一张单时（作废事务里叫）：还没写进 Express 的，排队中 / 转人工的写账件撤掉、清账本栏（电脑正在写的撤不了，写完回执
    到了见 `job_changed`）——出入库单、采购单、销售单都这样（11-13：采购 / 销售放行排队后作废，原来照冻结载荷写进 Express）。
    已写进去的出入库单再排一件「照 Express 原生作废原单」的写账（出入库第一期 B 段 ①）；采购 / 销售没有原生作废载荷，不排。
    准备不了（账套不在了）记在作废栏上，人看到后处理。"""
    if doctype not in (STOCK, PURCHASE, SALES):
        return
    fields = ["name", "creation", "owner", "ledger_state", "ledger_docnum", "ledger_job"]
    # 加锁读（作废事务已锁着这张单）：普通读是事务开头的旧值，看不到另一路刚排上的写账件，撤不掉它，作废了的单照样写进 Express
    # （关快照隔离后，11-28 B ③）
    row = frappe.db.get_value(doctype, name, fields + (["ledger_void_job"] if doctype == STOCK else []), as_dict=True,
                              for_update=True)
    if not row or row.get("ledger_void_job"):
        return
    # 不变式（11-28 B ③）：作废的单没有没撤销的写账件。按来源单据在写账件表里加锁找出全部还没写的（排队中、转人工）一律撤掉，
    # 不只认单据上记的 ``ledger_job``：作废前用旧副本保存过的路径（「修改」作废原单、别的事务里调作废）会把它盖回空。
    # 「照 Express 原生作废原单」那一件不在此列。电脑正在写的撤不了，写完回执到了再按作废处理（`job_changed`）。
    unwritten = _cancel_unwritten(doctype, name)
    if row.ledger_state in contracts.OPEN_STATES:
        # 还没写进去：排队中的写账撤掉（电脑正在写的撤不了，写完回执到了再作废，见 `job_changed`；验收 10-25）
        job = jobs.find_by_name(row.ledger_job) if row.ledger_job else None
        if job and job["job"] not in unwritten and job["status"] in ("queued", "manual"):
            jobs.cancel(job["job"], "voided")  # 单据上记的那件照旧也撤（与按来源单据撤的取并集）
        if not job or job["job"] in unwritten or job["status"] in ("queued", "manual", "cancelled"):
            _set(doctype, name, {"ledger_state": None, "ledger_job": None, "ledger_reason": "", "ledger_needed": None})
        return
    if doctype != STOCK or row.ledger_state not in (contracts.WRITTEN, contracts.MISMATCH) or not row.ledger_docnum:
        return
    company = company_of(doctype, name)
    result = stock_entry.void_payload(name, _company(company)) if company else {"hold": contracts.NO_BOOK}
    if "hold" in result:
        _set(doctype, name, {"ledger_void_state": contracts.HELD, "ledger_void_reason": result["hold"][:500]})
        return
    payload = result["payload"]
    job = jobs.enqueue(source_doctype=doctype, source_name=name, request_key=stock_entry.VOID_REQUEST + ref(name, row.creation),
                       actor=frappe.session.user or row.owner, book_key=result["book"]["key"], payload=payload,
                       payload_version=int(payload["payload_version"]), label=row.ledger_docnum)
    _set(doctype, name, {"ledger_void_state": contracts.QUEUED, "ledger_void_job": job["job"], "ledger_void_reason": ""})


def _cancel_unwritten(doctype, name) -> set:
    """撤掉这张单全部还没写的写账件（排队中、转人工；不含原生作废那一件），回撤掉的件名。加锁读：别人刚排上的也看得到。"""
    rows = frappe.db.sql(f"""SELECT name, request_key FROM `tab{jobs.JOB}` WHERE source_doctype = %s AND source_name = %s
        AND status IN ('queued', 'manual') FOR UPDATE""", (doctype, name), as_dict=True)
    done = set()
    for row in rows:
        if str(row.request_key or "").startswith(stock_entry.VOID_REQUEST):
            continue
        jobs.cancel(row.name, "voided")
        done.add(row.name)
    return done


def _void_changed(view):
    doctype, name = view.get("source_doctype"), view.get("source_name")
    status = view.get("status")
    state = VOIDED if status == "success" else contracts.QUEUED if status in ("queued", "leased") else contracts.HELD
    _set(doctype, name, {"ledger_void_state": state, "ledger_void_job": view.get("job"),
                         "ledger_void_reason": "" if state != contracts.HELD else str(view.get("reason") or "")[:500]})


def _state(view):
    status = view.get("status")
    if status == "success":
        return contracts.MISMATCH if view.get("verify_state") == "mismatch" else contracts.WRITTEN
    if status in ("queued", "leased"):
        return contracts.QUEUED
    if status == "cancelled":
        return contracts.FAILED
    return contracts.HELD


def _follow_up(doctype, name, state, values) -> None:
    """CC 对话里推的这张到点没等到结论（`cc_tools._follow_up` 记下了）：结论到了在那段对话里补一句（11-08 生产第 9 条，
    ``push_followup``）。只是补一句：出错只记日志。"""
    from pearnly_core.assistant import push_followup

    entered = state in (contracts.WRITTEN, contracts.MISMATCH)
    try:
        from pearnly_erp_portal.express import pending

        push_followup.tell("erp", f"{doctype}:{name}", entered, "Express", docnum=values.get("ledger_docnum") or "",
                           reason="" if entered else values.get("ledger_reason") or "",
                           explain=lambda raw: pending.reason_label(state, raw))
    except Exception:
        frappe.log_error(title="Push follow-up failed")


def still_wanted(view) -> bool:
    """电脑领单前问一次（11-13 作废单兜底）：来源单据已在 ERP 作废的写账件不要了（作废时撤写账件那一步没接上的路径也挡住）；
    「照 Express 原生作废原单」那一件照写。别的（收款、找不到单据）一律要，由原来的回执处理。"""
    doctype, name = view.get("source_doctype"), view.get("source_name")
    if doctype not in DIRECTIONS or not name:
        return True
    if str(view.get("request_key") or "").startswith(stock_entry.VOID_REQUEST):
        return True
    # 加锁读：领单那笔事务前面已有普通读（排头），关快照隔离后普通读看不到读视图之后才提交的作废（11-28 B ③，最后一道闸）
    row = frappe.db.get_value(doctype, name, ["status", "ledger_job"], as_dict=True, for_update=True)
    if not row:
        return True
    # 单据上记的已不是这件（被换掉的旧件；迟到写成转人工后还排着的那件，`job_changed`）：不交电脑写，Express 里不会多一张
    # （11-28 ④）。还没记上件的照旧要（换件都先撤旧件、再交新件，见 `_send`）
    return row.status != "void" and (not row.ledger_job or row.ledger_job == view.get("job"))


def _awaited(row, view) -> bool:
    """现在那件写成了，单据还在等它的结果：排队中 / 写入中；这件自己转了人工（结果不明 ``LOST``、代录半截单号）之后补报写成
    （共用通道只收这几种迟到的成功，`jobs.acknowledge`；单据有件时别的路径不会把它改成停下——`_send` 见有件就退出、答题与再写一次
    都先撤件）；同一份回执重放。别的（失败、已断开、已手工处理、已清空、已记着另一张单号、已转人工的迟到写成）都是被改过了。"""
    if row.ledger_state == contracts.QUEUED:
        return True
    if row.ledger_state == contracts.HELD:
        return row.ledger_reason != contracts.MOVED_DURING_WRITE
    return row.ledger_state in (contracts.WRITTEN, contracts.MISMATCH) and (row.ledger_docnum or "") == (view.get("docnum") or "")


def _hold_written(doctype, name, row, view):
    """写成了、单据却已不在等这件（主会话 11-28 定，同 MR.ERP 记结论遇到意外状态的第三种，`mrerp.push._conclude`）：Express 里有了
    这张，不记成干净的「已写入」，也不盖现在那件——转人工、停在结果不明（`MOVED_DURING_WRITE`），这件的单号记在单据上、
    ``ledger_job`` 指着它，另记一条错误日志带原状态、原来那件与单号。单据作废了的照「写进去后作废」。

    之后不再自动写：单据记着件，`_send` / 巡检 / 代录再录都不再排件，「再写一次」只认转人工的件；还排着的别的件（现在那件
    没被领走的、收回的）领单前由 `still_wanted` 撤掉。若被领走的那件之后也写成，回执照这里再记一次：两张单号在写账件表里各在各件
    那一行（``docnum``），单据记最后写成的那件，错误日志两张都带。"""
    frappe.log_error(title="ERP Express write landed after the document moved on",
                     message=f"{doctype} {name}: {row.ledger_state} {row.ledger_reason} job {row.ledger_job} "
                             f"Express {row.ledger_docnum or '-'}; job {view.get('job')} wrote Express {view.get('docnum') or '-'}")
    _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_job": view.get("job"), "ledger_docnum": view.get("docnum") or "",
                         "ledger_reason": contracts.VOIDED_AFTER_WRITE if row.status == "void" else contracts.MOVED_DURING_WRITE,
                         "ledger_needed": None})


def job_changed(view):
    """共用写账队列里这张单有了新状态（同一事务里叫）：记回单据；第一次写进去后排一次主档同步（库存按 Express 对齐）。

    只认单据上现在那件（``ledger_job``；还没记上件的，如刚交进队列的那一次，也算）。加锁读单据：回执在电脑回报那笔事务里，
    关快照隔离后普通读可能是旧值（11-28 ④）。不是现在那件、或单据已因迟到写成转了人工的：没写成的不盖（现在那件为准），写成了的
    转人工（:func:`_hold_written`）；现在那件写成了、单据却已不在等它（:func:`_awaited`）同样转人工。作废照原规则。"""
    doctype, name = view.get("source_doctype"), view.get("source_name")
    if doctype not in DIRECTIONS or not name or not frappe.db.exists(doctype, name):
        return
    if str(view.get("request_key") or "").startswith(stock_entry.VOID_REQUEST):
        _void_changed(view)  # 作废那一件：记在作废栏上，写入时的单号与状态不动
        return
    row = frappe.db.get_value(doctype, name, ["status", "ledger_state", "ledger_job", "ledger_docnum", "ledger_reason"],
                              as_dict=True, for_update=True)
    if view.get("status") == "cancelled" and row.status == "void":
        # 单据作废后撤掉的那件（作废时撤、或领单前兜底 `still_wanted`）：清账本栏，不记成「失败」（11-13）；已写进 Express 的单
        # 撤的是多出来的件（作废时按来源单据全撤，11-28 B ③），写入状态与单号照旧留着（出入库单另排原生作废那一件）
        if row.ledger_state not in (contracts.WRITTEN, contracts.MISMATCH):
            _set(doctype, name, {"ledger_state": None, "ledger_job": None, "ledger_reason": "", "ledger_needed": None})
        return
    landed = view.get("status") == "success"
    held = row.ledger_state == contracts.HELD and row.ledger_reason == contracts.MOVED_DURING_WRITE
    if held or (row.ledger_job and row.ledger_job != view.get("job")):
        if landed:
            _hold_written(doctype, name, row, view)
        return
    if landed and row.status != "void" and not _awaited(row, view):
        _hold_written(doctype, name, row, view)
        return
    before = row
    before.company = company_of(doctype, name)
    state = _state(view)
    values = {"ledger_state": state, "ledger_job": view.get("job"), "ledger_docnum": view.get("docnum") or "",
              "ledger_reason": "" if state in (contracts.WRITTEN, contracts.QUEUED)
              else str(view.get("reason") or "")[:ack_rules.OPERATOR_REASON_MAX]}
    _set(doctype, name, values)
    if state != contracts.QUEUED:
        _follow_up(doctype, name, state, values)
    if state not in (contracts.WRITTEN, contracts.MISMATCH) and before.company and not enabled(before.company):
        # 断开时电脑正在写、结果没写成（或撤掉的那件）：不再写，标已断开（:func:`detach`）
        _set(doctype, name, {"ledger_state": contracts.DETACHED, "ledger_reason": contracts.DISCONNECTED,
                             "ledger_needed": None})
        return
    if doctype == STOCK and state in (contracts.WRITTEN, contracts.MISMATCH) and \
            frappe.db.get_value(doctype, name, "status") == "void":
        void(doctype, name)  # 电脑写的时候 ERP 这边已作废：写完立刻照 Express 原生作废原单
    if doctype in (PURCHASE, SALES) and state in (contracts.WRITTEN, contracts.MISMATCH) and \
            frappe.db.get_value(doctype, name, "status") == "void":
        # 电脑写的时候 ERP 这边已作废（撤不了）：Express 里有了这张，不记成干净的「已写入」，转人工、单号如实留着（11-13）
        _set(doctype, name, {"ledger_state": contracts.HELD, "ledger_reason": contracts.VOIDED_AFTER_WRITE})
        return
    if state in (contracts.WRITTEN, contracts.MISMATCH) and (before.ledger_state, before.ledger_docnum or "") != (
            state, values["ledger_docnum"]) and before.company:
        from pearnly_erp_portal.express import masters

        link_party(doctype, name, before.company, view.get("party_code"))
        link_products(doctype, name, before.company, view.get("line_codes"))
        masters.forget(view.get("book_key"))
        masters.queue(before.company)
    if view.get("status") == "manual" and _needs_choice(view.get("reason")):
        # 电脑写时才发现、要人在 ERP 里选一次的（疑似重复对方档、存货科目组不合格）：推给录入人，页面在线就直接弹出
        # 这张单的 Express 弹窗（用户 10-23：不让人去找右上角）
        owner = frappe.db.get_value(doctype, name, "owner")
        if owner:
            frappe.publish_realtime(NEEDED_EVENT, {"doctype": doctype, "name": name}, user=owner, after_commit=True)


NEEDED_EVENT = "pearnly_erp_express_needed"


def _needs_choice(reason):
    # 关账、库存不足也弹出（只有原因、能重试；10-24 验收：页面不自动弹）
    return ack_rules.error_code(reason) in prepare.PARTY_CODES | prepare.GROUP_CODES | prepare.SERVICE_VAT_CODES | {
        "PERIOD_LOCKED", "STOCK_INSUFFICIENT"}


PARTIES = {SALES: ("ERP Customer", "customer"), PURCHASE: ("ERP Supplier", "supplier")}


def link_party(doctype, name, company, code):
    """写进 Express 后，把单上还没挂码的对方档挂上电脑实际用的码（10-24 验收：手输新客户写进去后，同步回来又多一条）：
    这家公司已有挂这个码的档（选了现成档 / 同步先到）→ 单据改指那条，自动建的那条只这张单用过就停用；否则这条档只被这家
    公司的单用过 → 挂上这家公司和这个码，之后主档同步按码认它、不另建。别家公司也用过的本地档不动（各家共用）。"""
    party_doctype, field = PARTIES.get(doctype, (None, None))
    code = str(code or "").strip()
    current = frappe.db.get_value(doctype, name, field) if party_doctype and code and company else None
    if not current:
        return
    row = frappe.db.get_value(party_doctype, current, ["express_code", "express_company"], as_dict=True)
    if not row or row.express_code or (row.express_company or company) != company:
        return
    mapped = frappe.db.get_value(party_doctype, {"express_company": company, "express_code": code, "is_active": 1}, "name")
    if mapped and mapped != current:
        _set(doctype, name, {field: mapped})
        if not frappe.get_all(doctype, filters={field: current, "name": ["!=", name]}, limit=1):
            frappe.db.set_value(party_doctype, current, "is_active", 0, update_modified=False)
        return
    if frappe.get_all(doctype, filters={field: current, "company": ["!=", company]}, limit=1):
        return
    frappe.db.set_value(party_doctype, current, {"express_company": company, "express_code": code}, update_modified=False)


LINES = {SALES: "ERP Sales Document Line", PURCHASE: "Purchase Line"}
# 引用商品的表 → 哪一列说明「是这张单的」（None：只要引用就算别处用过）；结存跟着流水走，不另看
PRODUCT_USES = {"Purchase Line": "parent", "ERP Sales Document Line": "parent", "ERP Sale Line": "parent",
                "ERP Stock Entry Line": "parent", "ERP Stock Movement": "ref_id", "ERP Stock Count Entry": None,
                "ERP Stock Count Item": None, "ERP Stock Opening": None}


def link_products(doctype, name, company, codes):
    """写进 Express 后，把这张单随单自动建的本地商品挂上电脑实际用的存货码（同 :func:`link_party`；10-24：认不到的货物行
    提交时建本地档，电脑在 Express 里另建存货，几家公司的租户主档同步不认领本地档，会按新码再建一条同名档）。
    采购 / 销售只认领只被这张单用过的本地档；出入库、盘点、收银日汇总（P3x：库存单里新建的、收银卖的）只认领没在别家公司的仓里
    动过库存的本地档。这家公司已有挂这个码的档时不动（库存流水已记在本地档上，不改指）。逐行按序号对上（收银日汇总按品名）、
    品名也一致才认。"""
    lines = _product_lines(doctype, name) if company and isinstance(codes, list) else None
    if lines is None:
        return
    for row in codes:
        if not isinstance(row, dict):
            continue
        seq, code = row.get("seq"), str(row.get("code") or "").strip()
        label = str(row.get("name") or "").strip()
        if isinstance(lines, dict):
            line = lines.get(label)  # 收银日汇总：一行是几张小票合起来的，按品名认
        elif isinstance(seq, int):
            line = lines[seq - 1] if 1 <= seq <= len(lines) else None
        else:
            # 代录回执只报随单新建的存货 {name, code}、没有序号（`common.written_codes`）：按品名认，品名唯一的那一行才认
            same = [entry for entry in lines if entry[1] == label]
            line = same[0] if label and len(same) == 1 else None
        if not code or not line or not line[0]:
            continue
        product = frappe.db.get_value("ERP Product", line[0], ["product_name", "express_code", "express_company"],
                                      as_dict=True)
        # 接上时随单 / 盘点里建的档已归这家公司、还没有码（`count.create_product_here`）：同对方档（`link_party`）照挂
        if not product or product.express_code or (product.express_company or company) != company:
            continue
        if label not in {line[1], str(product.product_name or "").strip()}:
            continue
        if frappe.db.get_value("ERP Product", {"express_company": company, "express_code": code}, "name"):
            continue
        if _used_elsewhere(line[0], name) if doctype in LINES else _moved_elsewhere(line[0], company):
            continue
        frappe.db.set_value("ERP Product", line[0], {"express_company": company, "express_code": code},
                            update_modified=False)


def _product_lines(doctype, name):
    """这张单交给电脑的明细 → ``[(ERP 商品, 行上的品名)]``（按序号）；收银日汇总回 ``{品名: (ERP 商品, 品名)}``；别的单回 None。"""
    if doctype in LINES:
        rows = frappe.get_all(LINES[doctype], filters={"parent": name, "parenttype": doctype},  # bounded: 一张单的明细
                              fields=["product", "description", "item_type"], order_by="idx asc", limit_page_length=0)
        # 只记账的行在 Express 写成非库存品（11-06 按行选）：电脑回的码不挂到这一行的 ERP 物料上（它可能是库存物料）
        return [(row.product if (row.item_type or "goods") == "goods" else None, str(row.description or "").strip())
                for row in rows]
    if doctype == STOCK:
        return [(line["product"], line["name"]) for line in stock_entry.document(name, "link")["lines"]]
    if doctype == COUNT:
        return [(line["product"], line["name"]) for line in stock_entry.count_document(name, "link")["lines"]]
    if doctype == POS_DAY:
        rows = frappe.db.sql("""SELECT DISTINCT l.product, p.product_name FROM `tabERP Sale Line` l
            JOIN `tabERP Sale` s ON s.name = l.parent AND l.parenttype = 'ERP Sale'
            JOIN `tabERP Product` p ON p.name = l.product WHERE s.express_summary = %s""", (name,), as_dict=True)
        return {str(row.product_name or "").strip(): (row.product, str(row.product_name or "").strip()) for row in rows}
    return None


def _moved_elsewhere(product, company):
    """这件本地商品在别家公司的仓里动过库存（各家共用的本地档不挂某一家的码）。"""
    own = frappe.get_all("ERP Warehouse", filters={"company": company}, pluck="name", limit_page_length=0)  # bounded: 一家公司的仓
    return bool(frappe.get_all("ERP Stock Movement", filters={"product": product, "warehouse": ["not in", own or [""]]},
                               limit=1))


def _used_elsewhere(product, name):
    for table, own in PRODUCT_USES.items():
        filters = {"product": product}
        if own:
            filters[own] = ["!=", name]
        if frappe.get_all(table, filters=filters, limit=1):
            return True
    return False


def actor_of(doctype, row, book_key):
    """写账作业归谁：开单人本人（ERP 单据上记的也是他）。谁的电脑写在电脑来领单那一刻判（共用 ``cc_desktop.jobs``，用户 11-17
    定稿，两产品同一条）：本人的电脑在线用自己的；否则公司里在线、够得到这份账套、登录人对这家公司有权限的那台替他写（:func:`may_write`）；
    都不在线就排队，谁先上线谁写。11-16 在排队时就换成办公室同事的写法（``office_actor``）作废。"""
    del doctype, book_key
    return row.owner


def may_write(user, company) -> bool:
    """``user`` 此刻能不能用自己登录 CC 的电脑替别人写这家公司的单：签发有效（在职、能连 CC）、对这家公司有权限。"""
    return bool(company) and devices.issuer_valid(user) and company in companies.allowed_companies(user)


def writers_of(actor, company, book_key) -> dict:
    """``actor`` 交到这家公司 ``book_key`` 的单此刻有没有电脑写（本人的、加上能替他写的，`cc_desktop.jobs.writers`）。"""
    return jobs.writers(actor, book_key, eligible=lambda user: may_write(user, company))


def _queued_note(doctype, row):
    """排队中的单此刻为什么还没写（页面写原因，不改状态）：这件作业归的人一台登录过 CC 的电脑都没有 →「CC 桌面版未连接」；
    他的与能替他写的电脑都关着 → 离线；都够不到这份账套 → 没有电脑能写。"""
    # 这件钉在一台此刻不在线的电脑上（领走后失联、收回时钉回它）：别的电脑不写，就是在等那台——先说它（11-25 两台电脑实测：
    # 同公司另一台在线时 writers 报 ok，页面只写「录入中」）
    pinned = jobs.waiting_computer(row.ledger_job) if row.get("ledger_job") else None
    if pinned is not None:
        return contracts.waiting_computer(pinned)
    found = writers_of(actor_of(doctype, row, row.ledger_book), company_of(doctype, row.name), row.ledger_book) \
        if row.ledger_book else {"state": "ok"}
    return {"missing": contracts.NOT_CONNECTED, "offline": contracts.OFFLINE,
            "unreachable": contracts.NO_WRITER}.get(found["state"], "")


def requeue_unmapped(company) -> int:
    """这家公司停在「仓库在 Express 里没有对应的仓库」的单重新排队（下一分钟 :func:`poll` 再交）：主档同步认领了新仓库码、
    或在 ERP 给仓选了 Express 仓之后叫（验收复核 P0，多仓闭环 P1）。仍没有码的，再交时照样停下。回重排的张数。"""
    from pearnly_erp_portal.inventory import warehouses

    count = 0
    for doctype in DIRECTIONS:
        # 公司条件放进查询（验收复核 P1：先取最早的 100 张再逐张判公司，别家停着 100 张以上时这家一张都轮不到）；
        # 只重排还能写的单（作废的停单不改成排队中）；一批改完就不再命中，取到空为止
        mine = ({"warehouse": ["in", warehouses.names_of(company) or [""]]} if doctype in (STOCK, COUNT)
                else {"company": company})
        filters = {"ledger_state": contracts.HELD, "ledger_reason": contracts.WAREHOUSE_UNMAPPED,
                   "status": ready(doctype), **mine}
        while True:
            names = frappe.get_all(doctype, filters=filters, pluck="name",  # bounded: 一批 POLL_MAX，改完不再命中
                                   order_by="modified asc", limit_page_length=POLL_MAX)
            for name in names:
                _set(doctype, name, {"ledger_state": contracts.QUEUED, "ledger_reason": ""})
            count += len(names)
            if len(names) < POLL_MAX:
                break
    return count


def poll():
    """每分钟：停下的单里会自己好的再准备一次；排队中还没交进队列的交一次；排队中的记下此刻为什么还没写。没有要看的就只是
    一条带索引的查询。"""
    from pearnly_core.express_operator import tasks as hands_on_tasks
    from pearnly_erp_portal.express import operator

    # 交给代录、在任务里等电脑的单不按直写重试（归代录；原来会被这一轮当成直写写进去），由任务自己再交（11-10）
    hands_on = operator.waiting_targets()
    for doctype in DIRECTIONS:
        rows = frappe.get_all(doctype, filters={"ledger_state": ["in", contracts.OPEN_STATES], "status": ready(doctype)},
                              fields=["name", "owner", "ledger_state", "ledger_reason", "ledger_job", "ledger_book",
                                      "ledger_target"],
                              order_by="modified asc", limit_page_length=POLL_MAX)
        for row in rows:
            if row.ledger_target == mrerp_contracts.LEDGER_MRERP:
                continue  # MR.ERP 的单由 `mrerp.push.poll` 看
            if (doctype, row.name) in hands_on:
                continue
            if _polled(row):
                send(doctype, row.name, poll=True)  # 锁内按现值再判一次（:func:`_send`）
            elif row.ledger_job and row.ledger_state == contracts.QUEUED:
                note = _queued_note(doctype, row)
                if note != (row.ledger_reason or ""):
                    _set(doctype, row.name, {"ledger_reason": note})
        frappe.db.commit()
    hands_on_tasks.wake_waiting()  # 代录任务里等电脑的单：叫醒任务再按代录交一次（电脑没好就继续等）
    mrerp_push.poll()  # 功能关着时直接返回
