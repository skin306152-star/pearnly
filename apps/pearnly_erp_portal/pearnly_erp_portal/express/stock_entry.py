"""ERP 出入库单 / 盘点调整 → Express 库存单（载荷 6，CC 桌面版 `dbf_stock`；出入库第一期 B 段，10-25）。

- 出库单 → 领用 OU；入库单 → 调整 JU（正）；物料调整单（岳父 11-06，号 IA）→ 盘点调整 TK（一张调整单一张 TK，数量带正负，
  按「存货 × Express 仓」轧差，见 :func:`document`；盘点生成的调整单也是这样写，盘点本身不再写 TK）；
  历史盘点生成的出入库单（``ref_type = count``，AD 号）→ 整份盘点轧差一张 TK（:func:`count_document`，只给旧盘点）。
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
- 调拨（多仓闭环 P2）→ 原生仓间调拨 RL（`2026-10-25-express-native-rules.md` 4.6 / 4.6b）：行上带调出仓码 ``loccod``，单头另带
  调入仓码 ``to_loccod``；两个仓都要有 Express 仓库码，否则停下交人（同 G15）。RL 不带价、不过账，不问科目；只核这份账套有 RL
  单据簿（CC 桌面版报了 RL 才核，旧版没报的交给电脑，电脑没有回 ``JOURNAL_BOOK_NOT_FOUND``）。载荷盖调拨的版本，旧版诚实拒收。
- Express 里没有的商品（多仓闭环 P3x，G14）：行上没有 Express 编码时，这份账套有同名存货就交品名、电脑照名认；没有同名的按 ERP 商品
  问一次（``item``，主题 ``stock|<ERP 商品>``，选现有存货或新建，同采购 / 销售的选存货），选新建的行标 ``new_ok``、载荷盖 15，CC
  桌面版在 Express 建 0 余额存货再写（旧版诚实拒收）。减少的行（领用、盘亏、调出）在不许负库存的账套里，电脑建档前就停
  ``STOCK_INSUFFICIENT``，允许负库存的照建照写（主会话 11-01）。新存货挂哪个存货科目组同采购：这份账套选过就带 ``stock_acccod``；
  没选过电脑用账套里用得最多的那组，判不出是存货科目时回 ``STOCK_ACC_GROUP_UNFIT``，单据的 Express 弹窗问一次
  （`prepare.book_group_needed`，按账套记住）。写完 `push.link_products` 把电脑用的码挂回 ERP 商品，之后不再问。没读到存货表时
  不问：出入库照旧交品名，盘点停在 ``item_unmapped``。
"""
import frappe

from pearnly_core.express_rules.push.account_rules import wire_account_set
from pearnly_core.express_rules.push.choices import NEW, norm_name
from pearnly_core.express_rules.push.common import FACTOR_PAYLOAD_VERSION, REMARKS_PAYLOAD_VERSION

from pearnly_erp_portal.express import choices, contracts, conversation, facts, target

DOCTYPE = "ERP Stock Entry"
PAYLOAD_VERSION = 6
# 调拨 RL 与作废 RL：CC 桌面版认得 RL 起的版本（合入时取 main 上当时最大的载荷版本 + 1，见 companion payload_contract）
TRANSFER_PAYLOAD_VERSION = 14
# 行上带 ``new_ok``（Express 里没有、人选了新建的商品）：CC 桌面版认得这一标记、会在库存单里建存货起的版本（规则同上）
NEW_ITEM_PAYLOAD_VERSION = 15
# 载荷带 ``book``（出库按用途写 OU 以外的簿：ZZ 报损 / XS 样品 / PD 生产领料）：CC 桌面版认得它起的版本（companion payload_contract 22）
STOCK_BOOK_PAYLOAD_VERSION = 22
STOCK_BOOK = "stock_book"  # 要人选的那一项：这份账套没有用途对应的那本领用簿，选这份账套里领用类的哪一本（按公司 + 用途记住）
# 出库用途 → Express 领用类的簿（4-1-1 下的四本，11-18 实录：画面相同、科目随簿）；其他 / 不填是 OU
PURPOSE_BOOKS = {"internal_use": "OU", "damage": "ZZ", "sample": "XS", "production": "PD"}
# 入库一律写 4-3-2 生产完工收货 PI（用户 11-18 定 D2：Pearnly 的入库是内部入库，不是采购；采购自动入库照旧不另写），科目随 PI 簿的默认
RECEIPT_BOOK = "PI"
ISSUE, ADJUST, COUNT, TRANSFER = "OU", "JU", "TK", "RL"
COUNT_REF = "count"
COUNT_DOCTYPE = "ERP Stock Count"
# 采购自动入库：那张采购单已带库存行写进去；盘点生成的出入库单：按整份盘点轧差写一张 TK（`count_document`），不逐张写
SKIPPED_REFS = ("purchase", COUNT_REF)
ACCOUNT = "account"  # 要人选的那一项（与费用科目同一种，选项是这份账套的科目表）
ITEM = "item"  # Express 里没有同名存货的商品：选现有存货或新建（与采购 / 销售的选存货同一种，选项是这份账套的存货表）


def item_subject(product) -> str:
    """选存货按 ERP 商品记（库存单没有对方；写完回填编码后这件商品就不会再问）。"""
    return f"stock|{product}"


def account_subject(doctype) -> str:
    return f"stock:{doctype}"


def company_of(name, doctype=DOCTYPE):
    warehouse = frappe.db.get_value(doctype, name, "warehouse") if name else None
    return frappe.db.get_value("ERP Warehouse", warehouse, "company") if warehouse else None


def eligible(row) -> bool:
    """这张出入库单该不该写进 Express（还要公司接了 Express，由调用方判）。"""
    return row.get("status") == "submitted" and (row.get("ref_type") or "") not in SKIPPED_REFS


ADJUST_DIRECTION = "adjust"


def kind(row) -> str:
    """单别：人建的物料调整 → 增减调整 JU（Express 4-3-1，11-18 主会话定 D1）；盘点生成的调整单与历史盘点 → 盘点调整 TK（原生
    4-1-3-3「ปรับปรุงจากการตรวจนับ」）；出库 → OU 类（按用途选簿）；调拨 → RL；入库 → JU 类的 PI 簿（:data:`RECEIPT_BOOK`）。"""
    if (row.get("ref_type") or "") == COUNT_REF or (row.get("direction") == ADJUST_DIRECTION and row.get("stock_count")):
        return COUNT
    if row.get("direction") == ADJUST_DIRECTION:
        return ADJUST
    if row.get("direction") == "transfer":
        return TRANSFER
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


def line_units(doc) -> dict:
    """单据上登记 / 选中的台按明细行号（从 1 起，同 `serials.set_doc_serials`）分好；表单预检的单没有台。"""
    from pearnly_erp_portal.inventory import serials

    return serials.line_serials(doc) if getattr(doc, "serials", None) else {}


def remarks_of(units, product) -> dict:
    """这一行的台 → 载荷行上的按台号码 ``remarks``（CC 桌面版写进 Express 这一行的附加说明，载荷 17）；没有台不带这一键。
    进货 / 销售（`push._lines`）与出入库 / 调拨同一处拼。"""
    remarks = contracts.unit_remarks(units or [], (product or {}).get("serial_kind") or contracts.VEHICLE)
    return {"remarks": remarks} if remarks else {}


def _net(lines) -> list:
    """物料调整单交给 TK 的明细：按「存货 × Express 仓」轧差（库存单位），净 0 的不写——Express 不分仓位，同一仓里「先减后加」的仓位
    挪动写成毛额，在不许负库存的账套里减的会被拦（同 :func:`count_document`）。单价只带**人亲手填 / 改过**的（主会话 11-06 审定 #5）：
    这一格的增加行全是人填的、价一样才带，否则不带，Express 用自己的均价。"""
    net, order = {}, []
    for line in lines:
        key = (line["code"] or line["product"] or line["name"], line["loccod"])
        slot = net.get(key)
        if slot is None:
            slot = net[key] = {"code": line["code"], "name": line["name"], "unit": line["unit"], "loccod": line["loccod"],
                               "qty": contracts.d(0), "product": line["product"], "prices": set(), "default": False}
            order.append(key)
        qty = contracts.d(line["qty"])
        slot["qty"] += qty
        if qty > 0:
            if line.get("manual_cost") is not None:
                slot["prices"].add(line["manual_cost"])
            else:
                slot["default"] = True
    out = []
    for key in order:
        slot = net[key]
        if not slot["qty"]:
            continue
        item = {"code": slot["code"], "name": slot["name"], "qty": _plain(slot["qty"]), "unit": slot["unit"],
                "loccod": slot["loccod"], "product": slot["product"]}
        if slot["qty"] > 0 and not slot["default"] and len(slot["prices"]) == 1:
            item["unit_cost"] = _plain(next(iter(slot["prices"])).quantize(contracts.d("0.0001")))
        out.append(item)
    return out


def document(name, ref, base=False) -> dict:
    """这张出入库单交给电脑的样子（还没选账套）。``ref``：单据标识（`push.ref`）。行照行上的单位与数量交（另带 ERP 商品单位表的
    换算率 ``factor``），CC 桌面版按 Express 主档的包装单位照原生记法写（`stock_units`）；``base=True`` 换成库存单位（盘点轧差用）。"""
    doc = frappe.get_doc(DOCTYPE, name)
    doctype = kind(doc)
    products, warehouses, lines = {}, {}, []
    adjust = doc.direction == ADJUST_DIRECTION
    base = base or adjust  # 物料调整按库存单位轧差
    # 盘点不改台（10-26）；物料调整写 TK 也不带台号（TK 是轧差后的数）
    units = line_units(doc) if doctype != COUNT else {}
    for index, line in enumerate(doc.lines, start=1):
        product = products.get(line.product)
        if product is None:
            product = products[line.product] = frappe.db.get_value(
                "ERP Product", line.product, ["product_name", "express_code", "base_unit", "unit", "serial_kind"],
                as_dict=True) or {}
        warehouse = line.warehouse or doc.warehouse
        if warehouse and warehouse not in warehouses:
            warehouses[warehouse] = frappe.db.get_value("ERP Warehouse", warehouse, "express_code") or ""
        # 行上可以是包装单位（箱 = 12；验收 10-25：原来按 1 写进 Express）：照行上单位交，带上 ERP 的换算率，CC 按 Express 主档换
        stock_unit, factor = base_unit(line.product, product, line.unit)
        qty, unit, cost = contracts.d(line.qty), str(line.unit or "").strip() or stock_unit, contracts.d(line.unit_cost)
        if base:
            qty, unit, cost = qty * factor, stock_unit, cost / factor
        if doctype == COUNT and doc.direction == "out":
            qty = -qty  # 历史盘点生成的出库单（物料调整单的数量本来就带正负）
        # ``product``：ERP 商品（选存货、回填编码用），进载荷前去掉
        item = {"code": product.get("express_code") or "", "name": product.get("product_name") or "",
                "qty": _plain(qty), "unit": unit, "loccod": warehouses.get(warehouse, ""), "product": line.product}
        if factor != 1 and not base:
            item["factor"] = _plain(factor)
        if qty > 0 and cost > 0:
            item["unit_cost"] = _plain(cost.quantize(contracts.d("0.0001")))
        if not base:
            item.update(remarks_of(units.get(index), product))
        # 轧差时带的单价（每个库存单位）：盘点 TK 只带人亲手填 / 改过的（其余 CC 按批次取，原生不让填）；人建的调整写 JU，原生调增的
        # 单价格要人填（不填是 0），页面上这一行显示的单价就是人认的价，照带
        if adjust and qty > 0 and line.unit_cost not in (None, "") and (doctype == ADJUST or line.get("unit_cost_manual")):
            item["manual_cost"] = cost
        lines.append(item)
    if doc.warehouse and doc.warehouse not in warehouses:
        warehouses[doc.warehouse] = frappe.db.get_value("ERP Warehouse", doc.warehouse, "express_code") or ""
    if doctype == TRANSFER:
        for item in lines:
            item.pop("unit_cost", None)  # RL 不带价（原生 UNITPR / TRNVAL 全空）
        warehouses[doc.to_warehouse] = frappe.db.get_value("ERP Warehouse", doc.to_warehouse, "express_code") or ""
    if adjust:
        lines = _net(lines)
    body = {"ref": ref, "direction": "stock", "doctype": doctype, "date": str(doc.posting_date),
            "number": doc.doc_no or doc.name, "remark": str(doc.reason or "").strip(),
            "loccod": warehouses.get(doc.warehouse, ""), "lines": lines}
    if doctype == ISSUE:
        body["purpose"] = str(doc.get("purpose") or "")
    elif doctype == ADJUST and doc.direction == "in":
        body["receipt"] = True  # 入库：写 PI 簿
    if doctype == TRANSFER:
        body["to_loccod"] = warehouses.get(doc.to_warehouse, "")
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
            key = (line["code"] or line["product"] or line["name"], line["loccod"])
            slot = net.setdefault(key, {"code": line["code"], "name": line["name"], "unit": line["unit"],
                                        "loccod": line["loccod"], "qty": contracts.d(0), "product": line["product"]})
            slot["qty"] += contracts.d(line["qty"])
    lines, new_items = [], set()
    for slot in net.values():
        if not slot["qty"]:
            continue
        if not slot["code"]:
            new_items.add(slot["name"])
        lines.append({"code": slot["code"], "name": slot["name"], "qty": _plain(slot["qty"]), "unit": slot["unit"],
                      "loccod": slot["loccod"], "product": slot["product"]})
    warehouse = count.get("warehouse")
    loccod = (frappe.db.get_value("ERP Warehouse", warehouse, "express_code") or "") if warehouse else ""
    missing.update(contracts.unmapped({warehouse: loccod}))
    body = {"ref": ref, "direction": "stock", "doctype": COUNT,
            "date": str(entries[0].posting_date) if entries else frappe.utils.nowdate(), "number": name,
            "remark": str(count.get("title") or "").strip(), "loccod": loccod, "lines": lines}
    if missing:
        body["unmapped_warehouses"] = sorted(missing)
    if new_items:
        body["unmapped_items"] = sorted(new_items)  # 还没有 Express 编码的商品：没读到存货表时 :func:`prepare` 停下交人
    return body


def _item_choices(body, data, company):
    """行上没有 Express 编码的商品按这份账套的存货表定（改 ``body`` 的行）：同名存货交品名；选过的照选的带码或标 ``new_ok``；
    其余回要人选的 ``needed``。没读到存货表回 ``None``（定不了，调用方照旧）。"""
    if not isinstance(data, dict) or not isinstance(data.get("products"), list):
        return None
    products = [row for row in facts.reported(data).get("reported_products") or []
                if str(row.get("kind") or "stock") == "stock"]
    names = {norm_name(row.get("name")) for row in products if norm_name(row.get("name"))}
    codes = {str(row.get("code") or "").strip() for row in products} - {""}
    needed, asked = [], set()
    for line in body["lines"]:
        product = line.get("product")
        if str(line.get("code") or "").strip() or not product:
            continue
        subject = item_subject(product)
        value = choices.chosen(company, ITEM, subject)
        if value == NEW:
            line["new_ok"] = True
        elif value and value in codes:
            line["code"] = value
        elif norm_name(line.get("name")) in names and not value:
            continue  # Express 里有同名存货：电脑照名认得到，不用问
        elif subject not in asked:
            asked.add(subject)
            needed.append({"kind": ITEM, "subject": subject, "label": str(line.get("name") or "")[:140],
                           "current": value or ""})
    return needed


def _items(lines) -> list:
    """载荷的明细：去掉只在服务端用的 ERP 商品。"""
    return [{key: value for key, value in line.items() if key != "product"} for line in lines]


def _version(base, lines) -> int:
    if any(line.get("new_ok") for line in lines):
        base = max(base, NEW_ITEM_PAYLOAD_VERSION)
    return max(base, REMARKS_PAYLOAD_VERSION) if any(line.get("remarks") for line in lines) else base


def _with_group(payload, company, book) -> dict:
    """有要新建的存货时带上这份账套选过的存货科目组（同采购的 ``stock_acccod``）；没选过不带，电脑用账套里用得最多的那组。"""
    group = choices.chosen(company, choices.STOCK_GROUP, book["key"]) if any(
        line.get("new_ok") for line in payload["items"]) else ""
    return {**payload, "stock_acccod": group} if group else payload


def prepare(body, company_row, ensure_masters) -> dict:
    """``{"payload", "book"}`` 或 ``{"hold": 原因, ...}``（同 `prepare.prepare` 的形状，`push.send` 照样处理）。"""
    from pearnly_erp_portal.express import prepare as invoices

    picked = target.pick(company_row, body["date"], conversation.wanted(company_row))
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
    stock_books = (data.get("facts") or {}).get("stock_books") if isinstance(data, dict) else None
    items = _item_choices(body, data, company_row["name"])
    if items is None and body.get("unmapped_items"):
        # 盘点当场新建的商品、这份账套的存货表还没读到：定不了同名，停下交人（同盘点评估第 7 条）
        return {"hold": contracts.ITEM_UNMAPPED, "book": book}
    items = items or []
    if body["doctype"] == TRANSFER:
        result = _transfer_payload(body, book, data, items)
        return {**result, "payload": _with_group(result["payload"], company_row["name"], book)} if "payload" in result else result
    prefix, asked = _book(body, data, company_row["name"])
    if asked:
        return _hold(book, [asked, *items])
    subject = account_subject(prefix)
    account = choices.chosen(company_row["name"], ACCOUNT, subject)
    if isinstance(stock_books, dict) and stock_books and prefix not in stock_books:
        # 这份账套没有这个单别的单据簿（ISRUN）：选科目也写不进去，直接停下说明（1.12.3 起 CC 桌面版才区分得出）
        return {"hold": contracts.NO_STOCK_BOOK, "book": book}
    if not account and isinstance(stock_books, dict):
        postable = {str(row.get("code") or "").strip() for row in facts.reported(data).get("reported_accounts") or []}
        default = str(stock_books.get(prefix) or "").strip()
        if not default or (postable and default not in postable):
            return _hold(book, [{"kind": ACCOUNT, "subject": subject, "label": prefix, "options": [], "current": ""},
                                *items])
    if items:
        return _hold(book, items)
    path = book["path"]
    payload = {
        # 有包装单位的行（带 ERP 换算率）要 CC 桌面版 1.12.4 起才会照 Express 主档换算：盖 7，旧版诚实拒收（验收 10-25）
        "payload_version": _version(FACTOR_PAYLOAD_VERSION if any(line.get("factor") for line in body["lines"])
                                    else PAYLOAD_VERSION, body["lines"]),
        "direction": "stock", "doctype": body["doctype"],
        "docdate_be": be_date(body["date"]), "ref_no": str(body["number"])[:15], "remark": body["remark"][:50],
        "loccod": body["loccod"], "items": _items(body["lines"]),
        "account_root": book.get("root") or "", "account_dir": path, "account_company": book.get("company") or "",
        "account_set_row": 0, "account_set": wire_account_set(path, path),
        # 单据日期所在的会计期在 Express 里已关账：电脑不写、回 PERIOD_LOCKED（同采购 / 销售）
        "respect_period_lock": True,
    }
    if prefix != body["doctype"]:
        payload["book"] = prefix
        payload["payload_version"] = max(payload["payload_version"], STOCK_BOOK_PAYLOAD_VERSION)
    if account:
        payload["account"] = account
    return {"payload": _with_group(payload, company_row["name"], book), "book": book}


def book_subject(purpose) -> str:
    return f"stock_book:{purpose or 'other'}"


def _book(body, data, company) -> tuple:
    """这张单写进哪一本簿（ISRUN 前缀）→ ``(前缀, 要人选的那一项或 None)``。出库按用途选（D12），入库写 PI（D2），做法相同：用途对应的那本这份账套里有就用；
    没有就看这家公司为这个用途选过哪一本（记住的那本还在才算），都没有 → 问一次（选项是这份账套领用类的每一本）。CC 桌面版没报簿清单
    （旧版）时照用途的默认前缀交给电脑（旧版收到 22 诚实拒收）。其余单别就是单别本身。"""
    if body["doctype"] == ISSUE:
        purpose = str(body.get("purpose") or "")
        wanted, subject = PURPOSE_BOOKS.get(purpose, ISSUE), book_subject(purpose)
    elif body["doctype"] == ADJUST and body.get("receipt"):
        wanted, subject = RECEIPT_BOOK, book_subject("receipt")
    else:
        return body["doctype"], None
    listed = (data.get("facts") or {}).get("stock_book_list") if isinstance(data, dict) else None
    if not isinstance(listed, list):
        return wanted, None
    # 入库的候选不列盘点调整 TK（那是盘点流程的簿，11-18 网页实录）
    issue_books = [row for row in listed
                   if isinstance(row, dict) and row.get("doctype") == body["doctype"] and row.get("prefix")
                   and not (body["doctype"] == ADJUST and row["prefix"] == COUNT)]
    prefixes = {row["prefix"] for row in issue_books}
    picked = choices.chosen(company, STOCK_BOOK, subject)
    if wanted in prefixes:
        return wanted, None
    if picked in prefixes:
        return picked, None
    return wanted, {"kind": STOCK_BOOK, "subject": subject, "label": str(body.get("number") or ""), "current": "",
                    "options": [{"code": row["prefix"], "name": str(row.get("name") or "")} for row in issue_books]}


def _hold(book, needed) -> dict:
    kinds = ",".join(dict.fromkeys(item["kind"] for item in needed))
    return {"hold": contracts.CHOICE + ":" + kinds, "book": book, "needed": needed}


def _transfer_payload(body, book, data, items) -> dict:
    """调拨 RL 的载荷：不过账、不问科目。CC 桌面版报过核了哪些单别（``stock_books_checked``）且里面有 RL、这份账套却没有 RL 簿
    → 停下说明；旧版没报的照旧交给电脑核。"""
    facts_ = (data.get("facts") or {}) if isinstance(data, dict) else {}
    checked = facts_.get("stock_books_checked")
    if isinstance(checked, list) and TRANSFER in checked and TRANSFER not in (facts_.get("stock_books") or {}):
        return {"hold": contracts.NO_STOCK_BOOK, "book": book}
    if items:
        return _hold(book, items)
    path = book["path"]
    payload = {
        "payload_version": _version(TRANSFER_PAYLOAD_VERSION, body["lines"]), "direction": "stock", "doctype": TRANSFER,
        "docdate_be": be_date(body["date"]), "ref_no": str(body["number"])[:15], "remark": body["remark"][:50],
        "loccod": body["loccod"], "to_loccod": body.get("to_loccod") or "", "items": _items(body["lines"]),
        "account_root": book.get("root") or "", "account_dir": path, "account_company": book.get("company") or "",
        "account_set_row": 0, "account_set": wire_account_set(path, path), "respect_period_lock": True,
    }
    return {"payload": payload, "book": book}


VOID_PAYLOAD_VERSION = 8
VOID_REQUEST = "erp:void:stock:"  # 作废任务的请求号前缀（`push.job_changed` 据此把回执记到作废栏上）


def void_payload(name, company_row) -> dict:
    """作废一张已写进 Express 的出入库单（载荷 8，CC 桌面版 `dbf_void`，照 Express 原生作废原单）→ ``{"payload", "book"}`` 或
    ``{"hold": 原因}``。账套就是写入时那一份（单据记的 ``ledger_book``）；逐行带存货码（电脑写入时回报的实际编码）、仓库码、数量，
    电脑据此核原单还是写入时的样子，对不上转人工。"""
    from pearnly_core.cc_desktop import jobs

    doc = frappe.get_doc(DOCTYPE, name)
    book = next((item for item in target.candidates(company_row, str(doc.posting_date))
                 if item.get("key") == doc.ledger_book), None)
    if not doc.ledger_docnum or not book:
        return {"hold": contracts.NO_BOOK}
    written = jobs.find_by_name(doc.ledger_job) if doc.ledger_job else None
    codes = {int(row.get("seq") or 0): str(row.get("code") or "") for row in (written or {}).get("line_codes") or []
             if isinstance(row, dict)}
    body = document(name, "void")
    items = [{"seq": seq, "code": codes.get(seq) or line["code"], "loccod": line["loccod"], "qty": line["qty"],
              **({"factor": line["factor"]} if line.get("factor") else {})}
             for seq, line in enumerate(body["lines"], start=1)]
    path = book["path"]
    payload = {
        # 作废 RL 要认得 RL 的电脑（调拨的版本）；其余照旧 8，带按台号码的单要会删附加说明的电脑（17，RL 原生保留说明不必）
        "payload_version": TRANSFER_PAYLOAD_VERSION if body["doctype"] == TRANSFER else
        _version(VOID_PAYLOAD_VERSION, body["lines"]),
        "direction": "stock", "action": "void", "doctype": body["doctype"],
        "docnum": doc.ledger_docnum, "items": items,
        "account_root": book.get("root") or "", "account_dir": path, "account_company": book.get("company") or "",
        "account_set_row": 0, "account_set": wire_account_set(path, path), "respect_period_lock": True,
    }
    return {"payload": payload, "book": book}


def form_body(payload) -> dict:
    """出入库单表单此刻的内容（还没存）→ :func:`prepare` 要的单（提交前问要人定的项用，`api.preflight`）：单别、日期（科目），
    明细只带商品与它的 Express 编码（Express 里没有的商品选存货，P3x）。"""
    direction = str(payload.get("direction") or "")
    direction = direction if direction in ("out", "transfer", ADJUST_DIRECTION) else "in"
    lines = []
    for line in payload.get("lines") or []:
        product = str((line or {}).get("product") or "").strip() if isinstance(line, dict) else ""
        row = frappe.db.get_value("ERP Product", product, ["product_name", "express_code"], as_dict=True) if product else None
        if row:
            lines.append({"code": row.express_code or "", "name": row.product_name or "", "product": product})
    return {"ref": "preflight", "direction": "stock", "doctype": kind({"direction": direction, "stock_count": payload.get("stock_count")}),
            "date": str(payload.get("posting_date") or frappe.utils.nowdate()), "number": "-", "remark": "",
            "loccod": "", "lines": lines, "purpose": str(payload.get("purpose") or ""), "receipt": direction == "in"}


__all__ = ["ACCOUNT", "ADJUST", "COUNT", "COUNT_DOCTYPE", "DOCTYPE", "ISSUE", "ITEM", "NEW_ITEM_PAYLOAD_VERSION", "PURPOSE_BOOKS",
           "STOCK_BOOK", "STOCK_BOOK_PAYLOAD_VERSION", "TRANSFER", "book_subject",
           "TRANSFER_PAYLOAD_VERSION", "VOID_REQUEST", "account_subject", "company_of", "count_document", "document", "eligible",
           "form_body", "item_subject", "kind", "prepare", "void_payload"]
