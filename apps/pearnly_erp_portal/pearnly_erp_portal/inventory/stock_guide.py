"""ERP 库存四单的引导式（CC 对话里点「更多 → 出库 / 调拨 / 物料调整 / 入库」）：声明 `inventory.cc_stock_guides.GUIDES`
点名的产品函数都在这里，四份声明共用（方向按 ``ctx["business"]`` 定）。评估：``docs/work/2026-11-19-cc-guided-stock-entries-评估.md``。

只转调网页库存单同一组服务，不另写业务规则：

- 都在本对话公司下执行（`masters.company.acting`，同盘点引导）：仓、物料、结存、按台的台都只列这家公司的；
- 日期：对话月份里的今天，否则那个月的月末（进「已定」，可改；改的日期要在对话月份里）；
- 仓库（默认仓）：这家公司启用的仓，本人上次在这种单上选的 / 唯一的仓作推荐排第一（`warehouses.prefill_for`），不预选；
- 物料按行选仓：选项的码是 ``<s|n>:<仓>|<物料>``（``s`` = 按台管理，`serials.tracked`），显示「物料 · 仓库」与结存；
  出库、调拨、调整减少只列有结存的（调拨只列调出仓的，服务端单头一出一进），入库、调整增加列这家公司在架的物料 × 启用的仓；
- 单价（入库、调整增加）：推荐值是这个仓现在的均价 / 物料参考成本（`documents.default_unit_cost`，同网页带出），只给看得到成本的人；
- 按台管理（11-19 主会话定进第一版）：出库、调拨、调整减少逐行选在库的台；入库「退库」收回已售 / 已出库的台；其余入库、调整增加
  逐台登记主号（子题，`questions`）；台数要等于数量，过账时 `serials.apply` 再全核一遍；
- 写账前置判定（网页提交前同一个 `express.api.preflight`）：这家公司账套里缺这种单要的簿 / 科目时问一次、按公司记住；不缺就进「已定」；
- 附件：对话里交的文件（只认本人本对话的，`assistant.files`）在确定时存成本人私有的登记 File，随建单一起挂上（`api_create_stock_entry`
  的 ``attachments``，同网页「新建时先选附件」）；
- 草稿只是字符串（「方向@答案摘要」）：确定之前不建 ``ERP Stock Entry``，改答案、取消、失效都不留东西；
- 确定 = `inventory.api.api_create_stock_entry(..., submit=1)`（与网页提交同一次调用：权限、附件先核后挂、建单并过账、排队写 Express）；
  幂等按「运行 + 版本」的 ``dedupe_key``：同一键已建过就回那一张；服务拒了退回对应那一步、整笔回滚；
- 结果：ERP 单号 + Express 写入状态与单号（同网页列表 ``ledger_state / ledger_docnum``）。

选项函数签名同引擎（`pearnly_core.assistant.guide.run`）：``options(ctx, txt, start, page_length, codes)``。
"""
from __future__ import annotations

import calendar
import contextlib
import datetime
import hashlib
import json

import frappe
from frappe import _

DOCTYPE = "ERP Stock Entry"
PRODUCT = "ERP Product"
BALANCE = "ERP Stock Balance"
WAREHOUSE = "ERP Warehouse"
DIRECTIONS = {"stock_out": "out", "stock_transfer": "transfer", "stock_adjust": "adjust", "stock_in": "in"}
# 预填仓的单据类型（`warehouses.KINDS`；建单时服务记的同一组，`documents.create_entry` → `warehouses.remember`）
PREFILL_KINDS = {"out": "stock_out", "transfer": "transfer", "adjust": "stock_adjust", "in": "stock_in"}
TRACKED, PLAIN = "s", "n"          # 选项码前缀：按台管理 / 不按台（声明里「按台那一题」按 ``s:`` 判出不出）
SEP = "|"
DRAFT_SEP = "@"
PAGE_MAX = 50
MATCH_MAX = 500                    # 搜索时先认出的物料最多几件（再按页切；一家公司在架的物料按这个封顶）
HELD_MAX = 2000                    # 有结存的行最多取几行（一家公司启用的仓 × 有货的物料）
UNIT_PAGES = 4                     # 选台的下拉最多翻几页（每页 50，`serials.search_units` 的上限）
QUESTIONS_MAX = 50                 # 一题最多几个子题（引擎 `steps.QUESTIONS_MAX`）
READY = {"ready": "1"}             # 写账前置判定没有要问的：这一步的值（进「已定」）

# 拒收 / 退回的码（术语表 ``guide.refused.<码>``，四语）
DAY_OUTSIDE = "stock_day_period"
UNITS_MISMATCH = "stock_units_mismatch"
FILES_LIMIT = "stock_files_limit"
NO_LINES = "stock_no_lines"
REFUSED = "stock_refused"

# 服务拒绝的原因（英文原句，`frappe.throw(_(…))`）→ 退回哪一步；没列的退回物料那一步
_STEP_OF_ERROR = (
    ("The destination warehouse must differ from the source warehouse", "to_warehouse"),
    ("Pick a destination warehouse", "to_warehouse"),
    ("The number of units does not match the quantity", "units"),
    ("Quantities of products tracked by unit must be whole numbers in the base unit", "units"),
    ("This unit number is already registered", "units"),
    ("This unit is not in stock in this warehouse", "units"),
    ("Unit not found", "units"),
    ("Pick a warehouse", "warehouse"),
    ("Warehouse not found", "warehouse"),
    ("File not found", "file"),
    ("Invalid file", "file"),
)


# ── 上下文 ───────────────────────────────────────────────────────────────────


def _direction(ctx) -> str:
    return DIRECTIONS[str(ctx.get("business") or "")]


@contextlib.contextmanager
def _acting(ctx):
    """在本对话公司下执行（不在本人可用公司里的由 `acting` 拒）；对话没挂公司 = 本人当前公司。"""
    from pearnly_erp_portal.masters import company as companies

    with companies.acting(str(ctx.get("company") or "")):
        yield


@contextlib.contextmanager
def _conversation(ctx):
    """读对话里的文件时带上这段对话（`assistant.files` 按它认本人本对话的文件），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def _answers(ctx) -> dict:
    return ctx.get("answers") or {}


def _company(ctx) -> str:
    from pearnly_erp_portal.masters import company as companies

    with _acting(ctx):
        return companies.active_company()


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if text in f"{row['name']} {row.get('note') or ''}".casefold()]
    start, size = max(0, int(start or 0)), max(1, min(int(page_length or 20), PAGE_MAX))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


def code_of(flag, warehouse, product) -> str:
    return f"{flag}:{warehouse}{SEP}{product}"


def parse(code) -> tuple:
    """选项码 → ``(按台标记, 仓, 物料)``；不是这种写法回 ``("", "", "")``。"""
    flag, _colon, rest = str(code or "").partition(":")
    warehouse, _sep, product = rest.partition(SEP)
    return (flag, warehouse, product) if flag in (TRACKED, PLAIN) and warehouse and product else ("", "", "")


# ── 日期（对话月份里的今天，否则月末；改的日期要在对话月份里）──────────────────────────────


def _month(ctx):
    period = str(ctx.get("period") or "")
    try:
        year, month = (int(part) for part in period.split("-")[:2])
        return year, month
    except ValueError:
        return None


def day_default(ctx):
    today = datetime.date.fromisoformat(frappe.utils.nowdate())
    month = _month(ctx)
    if month and (today.year, today.month) != month:
        day = datetime.date(month[0], month[1], calendar.monthrange(*month)[1])
    else:
        day = today
    value = day.isoformat()
    return {"value": value, "value_label": value, "source": "period" if day != today else "today"}


def day_check(ctx, value) -> str:
    month = _month(ctx)
    try:
        day = datetime.date.fromisoformat(str(value))
    except ValueError:
        return DAY_OUTSIDE
    return "" if not month or (day.year, day.month) == month else DAY_OUTSIDE


# ── 用途（只出库、入库有；同网页可不选）────────────────────────────────────────────────


def purpose_options(ctx, txt="", start=0, page_length=20, codes=None):
    from pearnly_erp_portal.inventory import documents

    rows = [{"code": value, "name": _(f"stock.purpose.{value}")} for value in documents.PURPOSES.get(_direction(ctx), ())]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


# ── 仓库 ─────────────────────────────────────────────────────────────────────


def _warehouses(ctx) -> list:
    """这家公司启用的仓：``[{code, name}]``（「编号 · 名称」，同选择框的写法 `warehouses.label`）。一家公司的仓是有界小表。"""
    from pearnly_erp_portal.inventory import warehouses

    with _acting(ctx):
        names = warehouses.names_of(include_inactive=False)
        rows = frappe.get_all(WAREHOUSE, filters={"name": ["in", list(names) or [""]]},  # bounded: 一家公司的仓
                              fields=["name", "warehouse_name", "warehouse_code", "express_code"],
                              limit_page_length=len(names) or 1)
    labels = {row.get("name"): warehouses.label(row) or row.get("name") for row in rows}
    return [{"code": name, "name": labels.get(name) or name} for name in names]


def warehouse_default(ctx):
    """推荐的仓：本人上次在这家公司这种单上选的 / 这家公司唯一的仓（同网页建单预填 `warehouses.prefill_for`）。只作推荐、排第一。"""
    from pearnly_erp_portal.inventory import warehouses

    company = _company(ctx)
    with _acting(ctx):
        found = warehouses.prefill_for(PREFILL_KINDS[_direction(ctx)], company).get("warehouse") or ""
    row = next((row for row in _warehouses(ctx) if row["code"] == found), None)
    return {"value": row["code"], "value_label": row["name"], "source": "remembered"} if row else None


def warehouse_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = _warehouses(ctx)
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def to_warehouse_options(ctx, txt="", start=0, page_length=20, codes=None):
    """调入仓：同公司启用的另一个仓（与调出仓相同的不列，服务端同样拒 ``SAME_WAREHOUSE``）。"""
    source = str(_answers(ctx).get("warehouse") or "")
    rows = [row for row in _warehouses(ctx) if row["code"] != source]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def _default_warehouse(ctx) -> str:
    return str(_answers(ctx).get("warehouse") or (warehouse_default(ctx) or {}).get("value") or "")


def _ordered_warehouses(ctx) -> list:
    """默认仓排第一，其余照这家公司的顺序。"""
    rows = _warehouses(ctx)
    first = _default_warehouse(ctx)
    return sorted(rows, key=lambda row: row["code"] != first)


# ── 物料（按行选仓）────────────────────────────────────────────────────────────


def _products(names=None, txt="") -> dict:
    """这家公司看得到的在架物料（原生列表权限按公司隔离，同网页明细里的物料下拉）：``{name: row}``。
    ``names`` 给了只取这几件；``txt`` 按名称、编码、条码等搜（同物料页的搜索字段）。"""
    from pearnly_erp_portal.products import service as products

    filters = [["is_active", "=", 1]]
    if names is not None:
        wanted = sorted({str(name) for name in names if name})
        if not wanted:
            return {}
        filters.append(["name", "in", wanted])
    text = str(txt or "").strip()
    or_filters = [[field, "like", f"%{text}%"] for field in products.SEARCH_FIELDS] if text else None
    rows = frappe.get_list(PRODUCT, filters=filters, or_filters=or_filters,
                           fields=["name", "product_name", "internal_code", "base_unit", "unit"],
                           order_by="product_name asc", limit_page_length=MATCH_MAX if names is None else len(filters[-1][2]))
    return {row["name"]: row for row in rows}


def _tracked(ctx, products) -> set:
    from pearnly_erp_portal.inventory import serials

    return serials.tracked(list(products), _company(ctx))


def _held(warehouses, products=None) -> dict:
    """这几个仓里有结存的 ``{(仓, 物料): 数量}``（同一仓的几行结存合起来）。"""
    filters = [["warehouse", "in", list(warehouses) or [""]], ["qty_on_hand", ">", 0]]
    if products is not None:
        filters.append(["product", "in", sorted(products) or [""]])
    rows = frappe.get_all(BALANCE, filters=filters, fields=["warehouse", "product", "qty_on_hand"],  # bounded: HELD_MAX
                          limit_page_length=HELD_MAX)
    out = {}
    for row in rows:
        key = (row["warehouse"], row["product"])
        out[key] = out.get(key, 0) + frappe.utils.flt(row["qty_on_hand"])
    return {key: qty for key, qty in out.items() if qty > 0}


def _qty_text(qty) -> str:
    qty = frappe.utils.flt(qty)
    return str(int(qty)) if qty == int(qty) else f"{qty:g}"


def _option(flag, warehouse, product, labels, qty=None) -> dict:
    unit = (product.get("base_unit") or product.get("unit") or "").strip()
    note = f"{_('stock.col.onHand')} {_qty_text(qty or 0)}" + (f" {unit}" if unit else "")
    return {"code": code_of(flag, warehouse, product["name"]),
            "name": f"{product.get('product_name') or product['name']} · {labels.get(warehouse) or warehouse}",
            "note": note}


def _checked_codes(ctx, codes, held) -> dict:
    """核答案：每个码的仓是这家公司启用的、物料看得到且在架、按台标记与此刻一致；``held`` 时还要此刻有结存。"""
    labels = {row["code"]: row["name"] for row in _warehouses(ctx)}
    if held and _direction(ctx) == "transfer":
        source = _default_warehouse(ctx)
        labels = {key: value for key, value in labels.items() if key == source}
    wanted = [parse(code) for code in codes]
    with _acting(ctx):
        products = _products(names=[product for _flag, _wh, product in wanted])
        tracked = _tracked(ctx, products)
        stock = _held([wh for _flag, wh, _p in wanted if wh in labels], products) if held else {}
    rows = []
    for flag, warehouse, product in wanted:
        if warehouse not in labels or product not in products or flag != (TRACKED if product in tracked else PLAIN):
            continue
        if held and (warehouse, product) not in stock:
            continue
        rows.append(_option(flag, warehouse, products[product], labels, stock.get((warehouse, product))))
    return {"options": rows, "more": False}


def held_options(ctx, txt="", start=0, page_length=20, codes=None):
    """出库、调拨、调整减少：有结存的「物料 · 仓库」（默认仓的排前面；调拨只列调出仓的）。"""
    if codes is not None:
        return _checked_codes(ctx, codes, held=True)
    order = _ordered_warehouses(ctx)
    if _direction(ctx) == "transfer":
        source = _default_warehouse(ctx)
        order = [row for row in order if row["code"] == source]
    labels = {row["code"]: row["name"] for row in order}
    rank = {row["code"]: index for index, row in enumerate(order)}
    with _acting(ctx):
        matched = _products(txt=txt) if str(txt or "").strip() else None
        stock = _held(list(labels), None if matched is None else list(matched))
        products = matched if matched is not None else _products(names=[product for _wh, product in stock])
        tracked = _tracked(ctx, products)
    keys = sorted((key for key in stock if key[1] in products),
                  key=lambda key: (rank[key[0]], str(products[key[1]].get("product_name") or ""), key[1]))
    begin, size = max(0, int(start or 0)), max(1, min(int(page_length or 20), PAGE_MAX))
    rows = [_option(TRACKED if product in tracked else PLAIN, warehouse, products[product], labels, stock[(warehouse, product)])
            for warehouse, product in keys[begin:begin + size]]
    return {"options": rows, "more": len(keys) > begin + size}


def inbound_options(ctx, txt="", start=0, page_length=20, codes=None):
    """入库、调整增加：这家公司在架的物料 × 启用的仓（默认仓的排前面），带此刻结存。"""
    if codes is not None:
        return _checked_codes(ctx, codes, held=False)
    order = _ordered_warehouses(ctx)
    labels = {row["code"]: row["name"] for row in order}
    with _acting(ctx):
        products = _products(txt=txt)
    pairs = [(row["code"], name) for row in order for name in products]
    begin, size = max(0, int(start or 0)), max(1, min(int(page_length or 20), PAGE_MAX))
    page = pairs[begin:begin + size]
    with _acting(ctx):
        tracked = _tracked(ctx, {product for _wh, product in page})
        stock = _held({wh for wh, _p in page}, {product for _wh, product in page}) if page else {}
    rows = [_option(TRACKED if product in tracked else PLAIN, warehouse, products[product], labels, stock.get((warehouse, product)))
            for warehouse, product in page]
    return {"options": rows, "more": len(pairs) > begin + size}


# ── 单价（入库、调整增加；推荐 = 这个仓现在的均价 / 参考成本，只给看得到成本的人）──────────────────


def _priced_step(ctx) -> str:
    return "plus" if _direction(ctx) == "adjust" else "rows"


def price_options(ctx, txt="", start=0, page_length=20, codes=None):
    """要填单价的行 = 上一步选的那几行（码不变）。"""
    chosen = [str(row.get("code")) for row in _answers(ctx).get(_priced_step(ctx)) or () if isinstance(row, dict)]
    found = _checked_codes(ctx, chosen, held=False)["options"]
    return _only(found, codes) if codes is not None else _page(found, txt, start, page_length)


def price_default(ctx):
    """推荐单价（`documents.default_unit_cost`，同网页选物料时带出）：看不到成本的人不给；一行都算不出就没有推荐。"""
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.inventory import documents

    if not guard.cost_visible():
        return None
    out = []
    with _acting(ctx):
        for row in _answers(ctx).get(_priced_step(ctx)) or ():
            _flag, warehouse, product = parse(row.get("code") if isinstance(row, dict) else "")
            if not product:
                continue
            cost = documents.default_unit_cost(product, warehouse)  # loop-query: 上一步选的几行，每行一个均价
            if cost is not None:
                out.append({"code": row["code"], "amount": str(cost)})
    return {"value": out, "source": "average"} if out else None


# ── 按台管理（子题：选在库的台 / 收回已售的台 / 登记新台）───────────────────────────────────


def _units_rows(ctx, step_key) -> tuple:
    """按台那一题对应哪一步的行与做法：``(行, 做法)``；做法 pick（选在库的）/ return（收回已售、已出库的）/ register（登记新台）。"""
    direction = _direction(ctx)
    if direction == "adjust":
        source = "plus" if step_key == "plus_units" else "minus"
        mode = "register" if source == "plus" else "pick"
    else:
        source = "rows"
        mode = "pick" if direction in ("out", "transfer") else (
            "return" if _answers(ctx).get("purpose") == "return" else "register")
    rows = [row for row in _answers(ctx).get(source) or () if isinstance(row, dict) and str(row.get("code")).startswith(TRACKED + ":")]
    return rows, mode


def _whole(amount) -> int:
    qty = frappe.utils.flt(amount)
    return int(qty) if qty > 0 and qty == int(qty) else 0


def _units_of(ctx, product, warehouse, mode) -> list:
    """一行能选的台（`serials.search_units`，同网页选台下拉；最多翻 :data:`UNIT_PAGES` 页）。"""
    from pearnly_erp_portal.inventory import serials

    filters = {"product": product, **({"warehouse": warehouse} if mode == "pick" else {}),
               "status": serials.RETURNABLE if mode == "return" else serials.IN_STOCK}
    found = []
    for page in range(UNIT_PAGES):
        rows = serials.search_units(txt="", start=page * PAGE_MAX, page_len=PAGE_MAX,  # loop-query: 选台下拉按页取，最多 4 页
                                    filters=json.dumps(filters))
        found.extend(rows)
        if len(rows) < PAGE_MAX:
            break
    return [{"code": row[0], "name": " · ".join(part for part in (row[1], row[2]) if part)} for row in found]


def _questions(ctx, step_key) -> list:
    rows, mode = _units_rows(ctx, step_key)
    labels = {row["code"]: row["name"] for row in _warehouses(ctx)}
    out = []
    with _acting(ctx):
        names = _products(names=[parse(row["code"])[2] for row in rows])
        kinds = {name: frappe.db.get_value(PRODUCT, name, "serial_kind") for name in names}  # loop-query: 这张单按台的几件物料
        for index, row in enumerate(rows, start=1):
            _flag, warehouse, product = parse(row["code"])
            item = names.get(product) or {"product_name": product}
            title = f"{item.get('product_name') or product} · {labels.get(warehouse) or warehouse} · {_qty_text(row['amount'])}"
            if mode == "register":
                label = _("serial.device.primary" if kinds.get(product) == "device" else "serial.vehicle.primary")
                for number in range(1, _whole(row["amount"]) + 1):
                    out.append({"key": f"r{index}_{number}", "input": "text", "title": f"{label} {number} · {title}"})
                continue
            out.append({"key": f"r{index}", "input": "multi", "title": title,
                        "options": _units_of(ctx, product, warehouse, mode)})  # loop-query: 这张单按台的几行，每行一次选台
    return out[:QUESTIONS_MAX]


def _units_options(ctx, step_key):
    return {"questions": _questions(ctx, step_key)}


def _units_check(ctx, step_key, value) -> str:
    """台数要等于这一行的数量（整数）、同一台不能出现两次；登记的号不能重复。服务端过账时 `serials.apply` 再核一遍。"""
    rows, mode = _units_rows(ctx, step_key)
    seen = []
    for index, row in enumerate(rows, start=1):
        wanted = _whole(row["amount"])
        if not wanted:
            return UNITS_MISMATCH
        if mode == "register":
            numbers = [str((value or {}).get(f"r{index}_{n}") or "").strip().upper() for n in range(1, wanted + 1)]
            if not all(numbers):
                return UNITS_MISMATCH
            seen.extend(numbers)
            continue
        chosen = list((value or {}).get(f"r{index}") or ())
        if len(chosen) != wanted:
            return UNITS_MISMATCH
        seen.extend(chosen)
    return "" if len(seen) == len(set(seen)) else UNITS_MISMATCH


def units_options(ctx, txt="", start=0, page_length=20, codes=None):
    """出库、调拨、入库的按台那一题（对应「物料」那一步里按台管理的行）。"""
    return _units_options(ctx, "units")


def units_check(ctx, value) -> str:
    return _units_check(ctx, "units", value)


def plus_units_options(ctx, txt="", start=0, page_length=20, codes=None):
    """物料调整「增加」里按台管理的行：逐台登记。"""
    return _units_options(ctx, "plus_units")


def plus_units_check(ctx, value) -> str:
    return _units_check(ctx, "plus_units", value)


def minus_units_options(ctx, txt="", start=0, page_length=20, codes=None):
    """物料调整「减少」里按台管理的行：选在库的台。"""
    return _units_options(ctx, "minus_units")


def minus_units_check(ctx, value) -> str:
    return _units_check(ctx, "minus_units", value)


def _serials(values, step_key, rows, mode) -> list:
    answer = values.get(step_key) or {}
    out = []
    for index, row in enumerate(rows, start=1):
        if mode == "register":
            out.append([{"serial_no": str(answer.get(f"r{index}_{n}") or "").strip()}
                        for n in range(1, _whole(row["amount"]) + 1)])
        else:
            out.append([{"serial": name} for name in answer.get(f"r{index}") or ()])
    return out


# ── 明细（交服务的形状：行上带仓，同 ERP 界面窗口按行选仓之后的载荷）─────────────────────────────


def _lines(ctx, values) -> list:
    """答案 → `create_entry` 的 ``lines``：``{product, qty, warehouse, unit_cost?, serials?}``（调整的减少行数量为负）。"""
    direction = _direction(ctx)
    prices = {str(row["code"]): str(row["amount"]) for row in values.get("prices") or ()}
    groups = [("plus", 1, "plus_units"), ("minus", -1, "minus_units")] if direction == "adjust" else [("rows", 1, "units")]
    lines = []
    for source, sign, units_key in groups:
        rows = list(values.get(source) or ())
        tracked = [row for row in rows if str(row["code"]).startswith(TRACKED + ":")]
        mode = "register" if (direction == "adjust" and sign > 0) else "pick" if direction in ("out", "transfer", "adjust") else (
            "return" if values.get("purpose") == "return" else "register")
        picked = dict(zip((row["code"] for row in tracked), _serials(values, units_key, tracked, mode)))
        for row in rows:
            _flag, warehouse, product = parse(row["code"])
            qty = frappe.utils.flt(row["amount"]) * sign
            line = {"product": product, "qty": _qty_text(qty), "warehouse": warehouse}
            if row["code"] in prices and (direction == "in" or sign > 0):
                line.update(unit_cost=prices[row["code"]], unit_cost_manual=1)
            if row["code"] in picked:
                line["serials"] = picked[row["code"]]
            lines.append(line)
    return lines


# ── 写账前置判定（网页提交前同一个 `express.api.preflight`）─────────────────────────────────


def _payload(ctx, values=None) -> dict:
    values = values if values is not None else _answers(ctx)
    lines = [{"product": parse(row.get("code"))[2]} for key in ("rows", "plus", "minus")
             for row in values.get(key) or () if isinstance(row, dict) and parse(row.get("code"))[2]]
    return {"direction": _direction(ctx), "posting_date": str(values.get("day") or frappe.utils.nowdate()),
            "company": _company(ctx), "lines": lines}


def _needed(ctx, answers=None) -> list:
    """要人定的项（``[{kind, subject, label, options, current}]``）；没接 Express / 不用人选 / 判不出都回空（照常提交，提交后自动接着走）。"""
    from pearnly_erp_portal.express import api as express_api, push

    payload = _payload(ctx)
    if not payload["lines"]:
        return []
    try:
        with _acting(ctx):
            found = express_api.preflight(push.STOCK, json.dumps(payload), answers=json.dumps(answers) if answers else None)
    except (frappe.ValidationError, frappe.PermissionError):
        if answers:
            raise
        frappe.clear_messages()
        return []  # 同网页：判定本身出错不拦提交（提交后照常准备，要选的再停在单上）
    return list((found or {}).get("needed") or ())


def book_default(ctx):
    """没有要人定的项：这一步进「已定」（值只是「已就绪」）。有要问的回 None（降成问）。"""
    return {"value": dict(READY), "value_label": "Express", "source": "ledger"} if not _needed(ctx) else None


def book_options(ctx, txt="", start=0, page_length=20, codes=None):
    """要人定的项，一项一个单选子题（同网页提交前弹窗的那几项；``current`` 是预选）。"""
    out = []
    for index, item in enumerate(_needed(ctx), start=1):
        sub = {"key": f"q{index}", "input": "choice", "title": str(item.get("label") or item.get("subject") or ""),
               "options": [{"code": str(row.get("code")), "name": str(row.get("name") or row.get("code"))}
                           for row in item.get("options") or () if row.get("code") not in (None, "")]}
        if item.get("current"):
            sub["preselected"] = str(item["current"])
        out.append(sub)
    return {"questions": out}


def book_apply(ctx, value) -> None:
    """答了：照网页弹窗选的那样记在这家公司名下（`express.api.preflight` 带 ``answers``：只认这一项能选的值、记住、提交）。"""
    if value == READY:
        return
    items = _needed(ctx)
    answers = [{"kind": item["kind"], "subject": item["subject"], "value": str(value.get(f"q{index}") or "")}
               for index, item in enumerate(items, start=1) if value.get(f"q{index}")]
    if answers:
        _needed(ctx, answers)


# ── 附件 ─────────────────────────────────────────────────────────────────────


def file_check(ctx, value) -> str:
    from pearnly_erp_portal.inventory import attachments

    return FILES_LIMIT if len(value or ()) > attachments.LIMIT else ""


def _original_id(ctx, file_id) -> str:
    """同网页上传附件的原件 id 写法（`recognition.originals`：站点 + 本人 + 方向 + 幂等键）；幂等键 = 这次引导 + 对话文件编号。"""
    seed = f"{frappe.local.site}|{frappe.session.user}|stock_{_direction(ctx)}|cc-{ctx.get('run')}-{file_id}"
    return "ERO-" + hashlib.sha256(seed.encode()).hexdigest()[:48]


def _stored_files(ctx, file_ids) -> list:
    """对话里交的文件 → 本人私有、没挂单的登记 File（原件层配了存原件层，没配存本机，同网页经服务器上传 `originals.upload`）。
    同一次引导同一份文件只存一次（重来认出上次那条）。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_core.originals import registered

    names = []
    with _conversation(ctx):
        for file_id in file_ids or ():
            original_id = _original_id(ctx, file_id)
            existing = frappe.db.get_value("File", {"file_name": original_id + ".bin", "owner": frappe.session.user},  # loop-query: 一张单最多 10 个附件
                                           "name")
            if existing:
                names.append(existing)
                continue
            filename, content = chat_files.attachment(file_id)  # loop-query: 同上
            stored = registered.put(original_id, filename, content)
            if isinstance(stored, dict):
                frappe.throw(_("Invalid file"))
            if stored is None:  # 这个站点没配原件层
                stored = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                                         "content": content}).insert(ignore_permissions=True)
            names.append(stored.name)
    return names


# ── 草稿、复核、确定、结果 ─────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """答完了：不建任何东西，只记下「方向@答案摘要」给复核与确定用（上一次的草稿号在 ``ctx["draft"]``，也只是字符串，不用作废）。
    调整一行都没有时退回「增加」那一步。"""
    if _direction(ctx) == "adjust" and not (values.get("plus") or values.get("minus")):
        return {"returned": {"plus": {"reason": NO_LINES, "text": _("Provide at least one line item")}}}
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": f"{_direction(ctx)}{DRAFT_SEP}{digest}"}


def _line(ctx, key, value, step=None):
    return {"key": key, "label_key": f"guide.step.{ctx.get('business')}.{key}", "value": value, **({"step": step} if step else {})}


def _table(ctx, key, rows, labels):
    found = {row["code"]: row for row in _checked_codes(ctx, [row["code"] for row in rows], held=False)["options"]}
    return {"key": key, "label_key": f"guide.step.{ctx.get('business')}.{key}", "step": key,
            "rows": [{"code": row["code"], "name": (found.get(row["code"]) or {}).get("name") or row["code"],
                      "amount": str(row["amount"]), **({"note": labels[row["code"]]} if labels.get(row["code"]) else {})}
                     for row in rows]}


def review(ctx, draft) -> dict:
    """复核卡：日期、仓、调入仓、用途、备注、附件份数；物料各行（物料 · 仓库、数量，按台的列出台），单价各行。每行指回那一步。"""
    answers = _answers(ctx)
    direction = _direction(ctx)
    warehouses = {row["code"]: row["name"] for row in _warehouses(ctx)}
    out = {"version": draft, "ready": True, "problem": "", "problem_text": "", "lines": [], "tables": [], "total": ""}
    out["lines"].append(_line(ctx, "day", answers.get("day") or (day_default(ctx) or {}).get("value") or "", "day"))
    warehouse = _default_warehouse(ctx)
    out["lines"].append(_line(ctx, "warehouse", warehouses.get(warehouse) or warehouse, "warehouse"))
    if direction == "transfer":
        target = str(answers.get("to_warehouse") or "")
        out["lines"].append(_line(ctx, "to_warehouse", warehouses.get(target) or target, "to_warehouse"))
    if answers.get("purpose"):
        out["lines"].append(_line(ctx, "purpose", _(f"stock.purpose.{answers['purpose']}"), "purpose"))
    if answers.get("note"):
        out["lines"].append(_line(ctx, "note", answers["note"], "note"))
    if answers.get("file"):
        out["lines"].append(_line(ctx, "file", str(len(answers["file"])), "file"))
    serial_notes = {}
    for line, row in zip(_lines(ctx, answers), [row for key in ("rows", "plus", "minus") for row in answers.get(key) or ()]):
        units = [unit.get("serial_no") or unit.get("serial") for unit in line.get("serials") or ()]
        if units:
            serial_notes[row["code"]] = ", ".join(str(unit) for unit in units if unit)
    with _acting(ctx):
        for key in ("rows", "plus", "minus", "prices"):
            if answers.get(key):
                notes = serial_notes if key != "prices" else {}
                out["tables"].append(_table(ctx, key, answers[key], notes))  # loop-query: 最多四张表（物料 / 增加 / 减少 / 单价），每张按码核一次
    return out


def note(ctx, draft) -> str:
    """确定卡问句下面那行事实：会动到哪家公司（单据写进这家公司的仓，过账后排队写 Express）。"""
    company = _company(ctx)
    return str(frappe.db.get_value("ERP Company", company, "company_name") or company) if company else ""


def _dedupe_key(consent) -> str:
    """幂等键：「运行 + 版本」（同意凭证 ``guide:<运行>:<版本>``，页面上点的这一下）。"""
    return "cc:" + str(consent or "")


def _step_of(ctx, error) -> str:
    text = str(error or "")
    step = next((step for message, step in _STEP_OF_ERROR if text.startswith((message, _(message)))), "")
    if step == "units":
        return _units_step_for_error(ctx)
    if step == "to_warehouse" and _direction(ctx) != "transfer":
        step = "warehouse"
    return step or ("plus" if _direction(ctx) == "adjust" else "rows")


def _units_step_for_error(ctx) -> str:
    """按台的错退回哪一道：调整里有减少的台先退减少（出台核仓最常拒），否则增加。"""
    if _direction(ctx) != "adjust":
        return "units"
    return "minus_units" if _answers(ctx).get("minus_units") else "plus_units"


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：同一键（运行 + 版本）已建过就回那一张；否则存附件、`api_create_stock_entry(submit=1)`（同网页提交）。
    服务拒了（仓、数量、按台、附件）整笔回滚、退回对应那一步，原因照服务原话。"""
    from pearnly_erp_portal.inventory import api

    direction = _direction(ctx)
    key = _dedupe_key(consent)
    with _acting(ctx):
        existing = frappe.db.get_value(DOCTYPE, {"dedupe_key": key}, ["name", "doc_no", "status"], as_dict=True)
        if existing:
            return {"status": "entered", "name": existing["name"], "docnum": existing["doc_no"] or existing["name"]}
        try:
            files = _stored_files(ctx, values.get("file") or ())
            made = api.api_create_stock_entry(
                direction=direction, posting_date=values["day"], warehouse=_default_warehouse(ctx),
                **({"to_warehouse": values.get("to_warehouse")} if direction == "transfer" else {}),
                **({"purpose": values["purpose"]} if values.get("purpose") else {}),
                reason=values.get("note") or None, lines=_lines(ctx, values), submit=1, dedupe_key=key, attachments=files)
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return {"returned": {_step_of(ctx, error): {"reason": REFUSED, "text": str(error)[:300]}}}
    return {"status": "entered", "name": made["name"], "docnum": made.get("doc_no") or made["name"]}


# Express 写入状态 → 结果卡的状态（同收款引导：已写 / 排队；停下要人选、失败、对不上的给原因）；没接写账的单（空）照已提交
RESULT = {"written": "entered", "detached": "entered", "": "entered", "queued": "queued"}


def result(ctx, draft) -> dict | None:
    """结果卡一行：ERP 单号 + Express 写入状态与单号（同网页列表 ``ledger_state / ledger_docnum``）；排队中页面过一会儿再取。"""
    done = ctx.get("done") or {}
    name = done.get("name") if isinstance(done, dict) else None
    if not name:
        return None
    row = frappe.db.get_value(DOCTYPE, name, ["doc_no", "status", "ledger_state", "ledger_docnum", "ledger_reason"], as_dict=True)
    if not row:
        return None
    state = row.get("ledger_state") or ""
    status = RESULT.get(state, "failed")
    out = {"status": status, "name": name, "docnum": " · ".join(part for part in (row.get("doc_no") or name, row.get("ledger_docnum"))
                                                                 if part),
           "doc_no": row.get("doc_no") or name, "ledger_state": state, "ledger_docnum": row.get("ledger_docnum") or ""}
    if status == "queued":
        out["wait"] = True
    if status == "failed" and row.get("ledger_reason"):
        out["reason_text"] = _(row["ledger_reason"])
    return out
