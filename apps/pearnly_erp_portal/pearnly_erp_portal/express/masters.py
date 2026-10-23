"""Express 主档与库存回读进 ERP（ERP 当前台、Express 当账本，2026-10-22；10-23 起 CC 桌面版直连 ERP，ADR-025）。

经共用机器通道请办公室电脑读这家公司现用账套的整套主档与各仓结存（按需请求 ``express_masters``，账套的公共资料：够得到它的
电脑谁先来谁读），读好后在 ERP 里自动建档 / 更新：客户、供应商、商品（库存单位、售卖 / 进货单位、售价）、仓库（挂在这家公司下）；
整份另存一份（`facts`）给准备写账载荷用。主档以 Express 为准：

- 按「这家公司 + Express 编码」认档（``express_company`` + ``express_code``）：换了年度账套编码不变，不会重建一份；
  一个 ERP 租户挂几家公司，各自的档互不相认。``express_book`` 只记最近从哪份账套来。
- ERP 里没编码的同编码 / 同税号加分店 / 同名的档，第一次同步时认领过来，不另建一份。
- Express 里没有了的档（这家公司的）在 ERP 里停用；又出现了就启用。
- Express 里有的字段完全照 Express 写（空也写），在 ERP 里不能改（`locks`）；ERP 自有字段（图片、分类等）不动。
库存对齐成「Express 结存 + 还没写进 Express 的 ERP 单的变动」，差额记一笔调整流水（来源 ``express_sync``）。

触发：每小时一次（:func:`sync_all`）、ERP 单写进 Express 后一次（`push.job_changed`）、管理员在公司页手动一次（`api`）、准备写账时
这份账套还没读过一次（:func:`ensure`）。电脑读一本账套一般十几秒：后台的两种在任务里等它读完（:data:`JOB_WAIT`），手动的在请求
里最多等 :data:`api.WAIT` 秒；电脑离线 / 还没读完：这次什么都不改，下次再来。
"""
from decimal import Decimal
import gzip
import json
import time

import frappe
from frappe.utils import now_datetime, nowdate

from pearnly_core.cc_desktop import requests

from pearnly_erp_portal.express import contracts, facts, push, target
from pearnly_erp_portal.inventory import contracts as stock_contracts
from pearnly_erp_portal.inventory import stock

COMPANY = "ERP Company"
CUSTOMER = "ERP Customer"
SUPPLIER = "ERP Supplier"
PRODUCT = "ERP Product"
WAREHOUSE = "ERP Warehouse"
SYNC_REASON = "express_sync"
KIND = "express_masters"
TOL = Decimal("0.000001")
JOB_WAIT = 180      # 后台同步等电脑读完的上限（秒）
POLL_EVERY = 8


def linked_companies():
    """接了 Express 的公司：启用、CC 桌面版报过它的账套。"""
    rows = frappe.get_all(COMPANY, filters={"is_active": 1}, fields=["name", "tax_id", "branch"])
    return [row for row in rows if contracts.tax13(row.tax_id) and push.linked(row.name)]


def enqueue(company):
    frappe.enqueue("pearnly_erp_portal.express.masters.sync", queue="long", timeout=JOB_WAIT + 300,
                   enqueue_after_commit=True, job_id=f"erp-express-sync:{frappe.local.site}:{company}",
                   deduplicate=True, company=company, wait=JOB_WAIT)


def sync_all():
    for row in linked_companies():
        enqueue(row.name)


def sync(company, wait=0):
    """同步一家公司。电脑还在读时每隔几秒再问，最多等 ``wait`` 秒。
    回 ``{"state": ready | reading | offline | outdated | <原因码>, ...}``。"""
    deadline = time.monotonic() + max(0, int(wait or 0))
    while True:
        result = _sync_once(company)
        if result.get("state") != "reading" or time.monotonic() + POLL_EVERY > deadline:
            return result
        frappe.db.commit()  # 不带着事务等
        time.sleep(POLL_EVERY)


def _request_key(book):
    return "erp_masters:" + book["key"]


def _params(book):
    # 位置领单时换成领取那台电脑的写法（`request_contracts.localize`）
    return {"account_set": book["path"], "account_root": book.get("root") or ""}


def ensure(company, book):
    """准备写账时这份账套还没读过主档：请电脑读一次（已经在读、没有电脑能读都不建）；读好了就在这里落地（存账套资料，
    是这家公司现用的那份再落进 ERP 主档），下一次准备就有。同期几份账套时人选的那份不一定是主档同步读的那份
    （`target.current`），只靠同步落地会一直停在「正在读取」（10-23 验收第 3 轮）。"""
    made = requests.find(KIND, _request_key(book))
    if made is not None and made["status"] == "done":
        _land(company, book, made)
    elif made is None and requests.waiting(KIND, book["key"]) is None:
        requests.create(KIND, _request_key(book), _params(book), book_key=book["key"], actor=frappe.session.user)


def _land(company, book, made):
    """电脑读好的整套主档：存成这份账套的资料（`facts`），是这家公司现用的那份就落进 ERP 主档。回落了几条，或原因码。"""
    content, _meta = requests.result(made["request"])
    requests.drop(made["request"])
    try:
        data = json.loads(gzip.decompress(content or b""))
    except (OSError, ValueError):
        return {"state": "masters_invalid"}
    facts.save(book["key"], data)
    row = frappe.db.get_value(COMPANY, company, ["name", "tax_id", "branch"], as_dict=True)
    current = target.current(row, nowdate()) if row else None
    if not current or current["key"] != book["key"]:
        return {"state": "ready", "account_set": book["key"], "label": book["label"]}
    counts = apply(company, book["label"], data)
    return {"state": "ready", "account_set": book["key"], "label": book["label"], **counts}


def _sync_once(company):
    row = frappe.db.get_value(COMPANY, company, ["name", "tax_id", "branch"], as_dict=True)
    if not row or not contracts.tax13(row.tax_id):
        return {"state": "company_tax_missing"}
    book = target.current(row, nowdate())
    if book is None:
        return {"state": "account_set_missing"}
    made = requests.find(KIND, _request_key(book))
    if made is None:
        waiting = requests.waiting(KIND, book["key"])
        if waiting:  # 够得到这份账套的电脑都不在线，或 CC 桌面版太旧不认这种请求：不建，过一会儿再来
            return {"state": "outdated" if waiting.get("reason") == "update_needed" else "offline"}
        made = requests.create(KIND, _request_key(book), _params(book), book_key=book["key"],
                               actor=frappe.session.user)
    if made["status"] != "done":
        return {"state": "reading"}
    return _land(company, book, made)


def apply(company, book, data):
    """把读好的整套主档落进 ERP（幂等：再落一次不改任何东西）。回各类新建 / 更新 / 停用的条数。"""
    counts = {}
    counts["customers"] = _parties(CUSTOMER, company, book, data.get("customers"), _customer_fields)
    counts["suppliers"] = _parties(SUPPLIER, company, book, data.get("suppliers"), _supplier_fields)
    counts["products"] = _products(company, book, data)
    warehouses, counts["warehouses"] = _warehouses(company, book, data)
    counts["stock"] = _stock(company, warehouses, data.get("stock"))
    frappe.db.set_value(COMPANY, company, {"express_book": book[:140], "express_synced_at": now_datetime()},
                        update_modified=False)
    frappe.db.commit()
    return counts


# ── 写入 ─────────────────────────────────────────────────────────────────────


def _same(value):
    """比对用：空值一律当空串，数字按数值。"""
    if value in (None, "") or value is False:
        return ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return str(value)


def _unit_rows(doc):
    return [{"unit_name": row.unit_name, "factor": str(contracts.d(row.factor_to_base).normalize())}
            for row in doc.get("units") or []]


def _upsert(doctype, name, values):
    """有就只改不一样的字段，没有就建。回 1 表示改了 / 建了。单行出错只记日志，不挡整次同步。"""
    try:
        if name:
            doc = frappe.get_doc(doctype, name)
            diff = {key: value for key, value in values.items() if key != "units" and _same(doc.get(key)) != _same(value)}
            units = values.get("units")
            if units is not None and _unit_rows(doc) != units:
                doc.set("units", [{"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])} for row in units])
                diff["units"] = True
            if not diff:
                return 0
            doc.update({key: value for key, value in diff.items() if key != "units"})
            doc.flags.express_sync = True
            doc.save(ignore_permissions=True)
            return 1
        record = {key: value for key, value in values.items() if key != "units"}
        doc = frappe.get_doc({"doctype": doctype, **record})
        for row in values.get("units") or []:
            doc.append("units", {"unit_name": row["unit_name"], "factor_to_base": float(row["factor"])})
        doc.flags.express_sync = True
        doc.insert(ignore_permissions=True)
        return 1
    except frappe.ValidationError:
        frappe.clear_messages()
        frappe.log_error(title="ERP Express master sync row failed", message=frappe.get_traceback()[-1500:])
        return 0


def _claim(claimed, *finders):
    """这一行 Express 档落到 ERP 哪一条：按编码、税号 + 分店、名称依次认（认到了就不再往下找）；一条 ERP 档一次同步只给
    一行 Express 档，已被前面的行认走的跳过（否则两行认到同一条，后一行把前一行的编码改掉，前一行就丢了）。"""
    for find in finders:
        name = find()
        if name and name not in claimed:
            claimed.add(name)
            return name
    return None


def _retire(doctype, names):
    """这家公司在 Express 里已经没有了的档：停用（同步自己写，不受锁）。"""
    changed = 0
    for name in names:
        changed += _upsert(doctype, name, {"is_active": 0})
    return changed


def _mine(doctype, company, fields):
    """ERP 现有的档：这家公司同步来的按编码认；没归属却带编码的（旧数据）按编码也认；其余是可认领的。"""
    rows = frappe.get_all(doctype, fields=["name", "express_code", "express_company", *fields], limit_page_length=0)
    by_code = {}
    for row in sorted(rows, key=lambda item: item.express_company != company):
        if row.express_code and row.express_company in (company, None, ""):
            by_code.setdefault(row.express_code, row.name)
    ours = {row.express_code: row.name for row in rows if row.express_code and row.express_company == company}
    for code, name in ours.items():  # 1.11.0 之前的 CC 把编码里的不换行空格读成了普通空格：照普通空格也认回原来那条
        by_code.setdefault(_loose(code), name)
    free = [row for row in rows if not row.express_code]
    return by_code, ours, free


def _loose(code):
    return str(code or "").replace("\xa0", " ")


# ── 客户 / 供应商 ─────────────────────────────────────────────────────────────


def _customer_fields(item):
    # 与 ERP 客户保存时的归一同一口径（个人不存税号与分店，总店不存分店号），免得每次同步都当成有改动
    company = bool(item["tax_id"])
    return {"customer_name": item["name"], "tax_id": item["tax_id"] if company else None,
            "branch_no": item["branch"] if company and not item["head"] else None,
            "branch_type": ("hq" if item["head"] else "branch") if company else None,
            "party_type": "company" if company else "person",
            "address": item["address"] or None, "contact_phone": item["phone"] or None}


def _supplier_fields(item):
    return {"supplier_name": item["name"], "tax_id": item["tax_id"] or None,
            "branch_no": None if item["head"] else item["branch"],
            "branch_type": "head_office" if item["head"] else "branch",
            "address": item["address"] or None, "phone": item["phone"] or None}


def _parties(doctype, company, book, rows, fields_of):
    name_field = "customer_name" if doctype == CUSTOMER else "supplier_name"
    by_code, ours, free = _mine(doctype, company, ["tax_id", "branch_no", name_field])
    free_tax, free_name = {}, {}
    for row in free:
        if contracts.tax13(row.tax_id):
            free_tax.setdefault((row.tax_id, contracts.branch5(row.branch_no)), row.name)
        free_name.setdefault(str(row.get(name_field) or "").strip().casefold(), row.name)
    changed, claimed = 0, set()
    for raw in contracts.rows(rows):
        item = contracts.party(raw)
        values = {**fields_of(item), "express_code": item["code"], "express_company": company,
                  "express_book": book[:140], "is_active": 1}
        name = _claim(claimed, lambda: by_code.get(item["code"]), lambda: by_code.get(_loose(item["code"])),
                      lambda: free_tax.pop((item["tax_id"], item["branch"]), None) if item["tax_id"] else None,
                      lambda: free_name.pop(item["name"].casefold(), None))
        changed += _upsert(doctype, name, values)
    return changed + _retire(doctype, [name for name in ours.values() if name not in claimed])


# ── 商品 ─────────────────────────────────────────────────────────────────────


def _products(company, book, data):
    units = contracts.unit_names(data)
    by_code, ours, free = _mine(PRODUCT, company, ["code", "barcode", "product_name"])
    free_code = {row.code: row.name for row in free if row.code}
    free_name = {}
    for row in free:
        free_name.setdefault(str(row.product_name or "").strip().casefold(), row.name)
    taken = frappe.get_all(PRODUCT, fields=["name", "barcode"], filters={"barcode": ["is", "set"]}, limit_page_length=0)
    taken_barcodes = {row.barcode: row.name for row in taken if row.barcode}
    changed, claimed = 0, set()
    for raw in contracts.rows(data.get("products")):
        item = contracts.product(raw, units)
        name = _claim(claimed, lambda: by_code.get(item["code"]), lambda: by_code.get(_loose(item["code"])),
                      lambda: free_code.pop(item["code"], None),
                      lambda: free_name.pop(item["name"].casefold(), None))
        values = {"product_name": item["name"], "base_unit": item["base_unit"], "unit": item["unit"],
                  "units": [{"unit_name": row["unit_name"], "factor": str(contracts.d(row["factor"]).normalize())}
                            for row in item["units"]],
                  "express_code": item["code"], "express_company": company, "express_book": book[:140],
                  "express_stock": int(item["stock"]), "is_active": 1}
        if not name and not frappe.db.exists(PRODUCT, {"code": item["code"]}):
            # 问库而不是在内存里比：唯一键按库的排序规则比（泰文声调符号等会被当成一样），内存逐字比会漏
            values["code"] = item["code"]
        # 售价、条码完全照 Express（空就清空；条码被别的商品占着时不写，免得撞唯一键）
        values.update(unit_price=float(item["price"] or 0), price_set=1 if item["price"] else 0)
        if not item["barcode"]:
            values["barcode"] = None
        elif taken_barcodes.get(item["barcode"], name) == name:
            values["barcode"] = item["barcode"]
        changed += _upsert(PRODUCT, name, values)
    return changed + _retire(PRODUCT, [name for name in ours.values() if name not in claimed])


# ── 仓库与库存 ───────────────────────────────────────────────────────────────


def _warehouses(company, book, data):
    """这家公司的仓库按 Express 仓库码建 / 认领 / 改名 / 停用：回 (``{仓库码: ERP 仓库}``, 改动条数)。
    第一次同步时，这家公司唯一一个没编码的仓库认作 Express 的主仓（ERP 开户自带一个仓库）。"""
    existing = frappe.get_all(WAREHOUSE, filters={"company": company},
                              fields=["name", "warehouse_name", "express_code"], limit_page_length=0)
    by_code = {row.express_code: row.name for row in existing if row.express_code}
    free = [row for row in existing if not row.express_code]
    free_name = {str(row.warehouse_name or "").strip().casefold(): row.name for row in free}
    main = str((data.get("settings") or {}).get("mainloc") or "").strip()
    found, changed = {}, 0
    for raw in contracts.rows(data.get("warehouses")):
        code, label = str(raw["code"]).strip()[:4], str(raw.get("name") or raw["code"]).strip()[:140]
        name = by_code.get(code) or free_name.pop(label.casefold(), None)
        if not name and code == main and len(free) == 1 and free[0].name in free_name.values():
            name = free[0].name
            free_name = {key: value for key, value in free_name.items() if value != name}
        changed += _upsert(WAREHOUSE, name, {"warehouse_name": label, "express_code": code, "express_book": book[:140],
                                             "company": company, "is_active": 1})
        found[code] = name or frappe.db.get_value(WAREHOUSE, {"company": company, "express_code": code}, "name")
    changed += _retire(WAREHOUSE, [name for code, name in by_code.items() if code not in found])
    return {code: name for code, name in found.items() if name}, changed


def _pending(company, products, warehouses):
    """还没写进 Express 的 ERP 单已经过了的库存变动：``{(存货码, 仓库码): 带符号基本单位数量}``。"""
    refs = []
    for doctype, ref_type in (("ERP Sales Document", "ERP Sales Document"), ("Purchase Document", "purchase")):
        names = frappe.get_all(doctype, filters={"company": company, "status": "submitted",
                                                 "express_state": ["in", contracts.OPEN_STATES]},
                               pluck="name", limit_page_length=0)
        refs.extend((ref_type, name) for name in names)
    if not refs:
        return {}
    code_of = {name: code for code, name in products.items()}
    loc_of = {name: code for code, name in warehouses.items()}
    pending = {}
    for ref_type in {ref for ref, _ in refs}:
        ids = [name for ref, name in refs if ref == ref_type]
        for row in frappe.get_all(stock.MOVEMENT, filters={"ref_type": ref_type, "ref_id": ["in", ids]},
                                  fields=["product", "warehouse", "qty_base"], limit_page_length=0):
            key = (code_of.get(row.product), loc_of.get(row.warehouse))
            if key[0] and key[1]:
                pending[key] = pending.get(key, Decimal(0)) + contracts.d(row.qty_base)
    return pending


def _stock(company, warehouses, stock_rows):
    """ERP 结存对齐 Express：每个（Express 存货 × Express 仓库）差多少记一笔调整流水。回调整的笔数。
    只认这家公司同步来的库存品（编码在公司内唯一）。"""
    if not warehouses:
        return 0
    products = {row.express_code: row.name for row in frappe.get_all(
        PRODUCT, filters={"express_company": company, "express_code": ["is", "set"], "express_stock": 1},
        fields=["name", "express_code"], order_by="creation asc", limit_page_length=0)}
    targets = contracts.stock_targets(stock_rows, _pending(company, products, warehouses))
    current = {}
    for row in frappe.get_all(stock.BALANCE, filters={"warehouse": ["in", list(warehouses.values())]},
                              fields=["product", "warehouse", "qty_on_hand"], limit_page_length=0):
        key = (row.product, row.warehouse)
        current[key] = current.get(key, Decimal(0)) + contracts.d(row.qty_on_hand)
    wanted = {}
    for (code, loccod), qty in targets.items():
        if code in products and loccod in warehouses:
            wanted[(products[code], warehouses[loccod])] = qty
    linked = set(products.values())
    for key in current:
        if key[0] in linked and key not in wanted:
            wanted[key] = Decimal(0)  # Express 里这个仓已经没有了
    stamp = now_datetime().strftime("%Y%m%d%H%M%S")
    moved = 0
    for (product, warehouse), qty in wanted.items():
        delta = qty - current.get((product, warehouse), Decimal(0))
        if abs(delta) <= TOL:
            continue
        try:
            stock.apply_movement(product=product, warehouse=warehouse, movement_type=stock_contracts.ADJUST,
                                 qty=float(delta), posting_date=nowdate(), ref_type=SYNC_REASON, reason=SYNC_REASON,
                                 client_uuid=f"{SYNC_REASON}:{stamp}:{product}:{warehouse}"[:140],
                                 internal=True, allow_inactive=True, company=company)
            moved += 1
        except frappe.ValidationError:
            frappe.clear_messages()
            frappe.log_error(title="ERP Express stock sync failed", message=frappe.get_traceback()[-1500:])
    return moved
