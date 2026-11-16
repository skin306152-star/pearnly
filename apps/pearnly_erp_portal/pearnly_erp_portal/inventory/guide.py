"""ERP 盘点的引导式（CC 对话里点「盘点」，总表 ERP-D-ST）：声明 `inventory.cc_guide.GUIDES` 点名的产品函数都在这里。

每个函数只转调盘点页同一组服务函数（`inventory.count`，网页、LINE 手机盘点页、CC 工具 ``erp_stocktake_*`` 同一组），不另写规则：

- 都在本对话公司下执行（`masters.company.acting`，同 CC 工具）：盘点单、仓、物料都只列这家公司的；
- 新建：仓（这家公司启用的仓；本人上次选的 / 唯一的仓预先定下，同网页建单的预填 `warehouses.prefill_for`）→ 名称 → `create_count`；
- 记数：盘点单（进行中）→ 物料（同录入框的服务端查询 `search_count_products`）+ 数量（0 有效）→ 记法（累加 / 重新盘点该物料）
  → 逐行 `add_entry`（仓、仓位由服务按这一项账面位置定，同 CC 工具）；
- 结束：`close_count`（只锁定结果、不动库存；期间库存变了照服务口径拒）；
- 按盘点结果调整物料：已结束的盘点 → `adjustment_draft`（只建草稿，人在调整单页提交才动库存；已有就回那一张）；
- 删除：进行中的盘点 → `delete_count`。
"""
from __future__ import annotations

import contextlib
import hashlib
import json

import frappe
from frappe import _

NEW, COUNT, NEW_PRODUCT, CLOSE, ADJUST, DELETE = "new", "count", "new_product", "close", "adjust", "delete"
ACTIONS = ((NEW, "stock.count.new"), (COUNT, "stock.workbench.record"), (NEW_PRODUCT, "stock.mobile.newProduct"),
           (CLOSE, "stock.count.close"), (ADJUST, "stock.count.adjust"), (DELETE, "Delete"))
# 当场新建物料那几格 → `create_product_here` 的参数（同物料页新建弹窗的 `standard_fields`；空的不传）
PRODUCT_FIELDS = ("unit", "unit_price", "default_cost", "category")
ADD, RECOUNT = "add", "recount"
MODES = ((ADD, "stock.workbench.record"), (RECOUNT, "stock.workbench.recount"))
# 各动作能选的盘点单状态（同盘点页：记数、结束、删除只对进行中的；调整只对已结束、差异还没调的）
STATUSES = {COUNT: "Active", NEW_PRODUCT: "Active", CLOSE: "Active", DELETE: "Active", ADJUST: "Closed"}
SCAN_PAGE, SCAN_PAGES = 100, 20
REVIEW_ROWS = 200
DRAFT_SEP = "@"


def _counts():
    from pearnly_erp_portal.inventory import count

    return count


@contextlib.contextmanager
def _acting(ctx):
    """在本对话公司下执行（不在本人可用公司里的由 `acting` 拒）；对话没挂公司 = 本人当前公司。"""
    from pearnly_erp_portal.masters import company as companies

    with companies.acting(str(ctx.get("company") or "")):
        yield


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if text in f"{row['name']} {row.get('note') or ''}".casefold()]
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _only(rows, codes):
    wanted = {str(code) for code in codes}
    return {"options": [row for row in rows if row["code"] in wanted], "more": False}


# ── 做什么、记法 ──────────────────────────────────────────────────────────────


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def mode_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in MODES]
    return _only(rows, codes) if codes is not None else {"options": rows, "more": False}


def mode_default(ctx):
    """记法：默认累加（同扫码录入）；进「已定」，可改成重新盘点该物料。"""
    return {"value": ADD, "value_label": _(MODES[0][1]), "source": "default"}


# ── 新建：仓 ───────────────────────────────────────────────────────────────────


def _warehouses(ctx):
    from pearnly_erp_portal.inventory import warehouses

    with _acting(ctx):
        names = warehouses.names_of(include_inactive=False)
        labels = _counts()._warehouse_labels(names)
    return [{"code": name, "name": labels.get(name) or name} for name in names]


def warehouse_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这家公司启用着的仓（同建盘点时选仓；一家公司的仓是有界小表）。"""
    rows = _warehouses(ctx)
    return _only(rows, codes) if codes is not None else _page(rows, txt, start, page_length)


def warehouse_default(ctx):
    """本人上次在这家公司建盘点选的仓 / 这家公司唯一的仓（同网页建单的预填，`warehouses.prefill`）；都没有降成问。"""
    from pearnly_erp_portal.inventory import warehouses

    with _acting(ctx):
        found = warehouses.prefill("count").get("warehouse") or ""
    row = next((row for row in _warehouses(ctx) if row["code"] == found), None)
    return {"value": row["code"], "value_label": row["name"], "source": "remembered"} if row else None


# ── 盘点单 ───────────────────────────────────────────────────────────────────


def _status(ctx):
    return STATUSES.get(_answer(ctx, "action"))


def count_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这家公司的盘点单（同盘点页列表 `list_counts`，按动作只列对应状态的）；``codes`` 给了逐个核（`get_count`：还在、状态还对）。"""
    counts, status = _counts(), _status(ctx)
    with _acting(ctx):
        if codes is not None:
            rows = []
            for code in codes:
                try:
                    found = counts.get_count(str(code))  # loop-query: 只核这次交来的那一张
                except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError):
                    frappe.clear_messages()
                    continue
                if found["name"] == str(code) and (not status or found["status"] == status):
                    rows.append({"code": found["name"], "name": found["title"] or found["name"]})
            return {"options": rows, "more": False}
        found = []
        for number in range(SCAN_PAGES):
            page = counts.list_counts(status=status, limit=SCAN_PAGE, start=number * SCAN_PAGE)  # loop-query: 列表按页取，最多 20 页
            found.extend(page)
            if len(page) < SCAN_PAGE:
                break
    rows = [{"code": row["name"], "name": row["title"] or row["name"],
             **({"note": row["warehouse_name"]} if row.get("warehouse_name") else {})} for row in found]
    return _page(rows, txt, start, page_length)


# ── 记数：物料 + 数量 ────────────────────────────────────────────────────────────


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这张盘点能记的物料（同录入框的服务端查询 `search_count_products`：本盘点的物料 + 本盘点仓在库的物料）；
    ``codes`` 给了逐个按物料号核（`lookup_count`）。"""
    counts = _counts()
    count = str(_answer(ctx, "count"))
    with _acting(ctx):
        if codes is not None:
            rows = []
            for code in codes:
                try:
                    found = counts.lookup_count(count=count, product=str(code))["product"]  # loop-query: 只核这次交来的几行
                except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError, KeyError):
                    frappe.clear_messages()
                    continue
                if found.get("name") == str(code):
                    rows.append({"code": found["name"], "name": found.get("product_name") or found["name"]})
            return {"options": rows, "more": False}
        size = max(1, min(int(page_length or 20), 50))
        found = counts.search_count_products(count=count, txt=txt, start=start, page_len=size + 1)
    return {"options": [{"code": row[0], "name": row[1] or row[0], **({"note": row[2]} if row[2] else {})}
                        for row in found[:size]], "more": len(found) > size}


def qty_check(ctx, value) -> str:
    """数量不能是负数（0 有效：盘到 0 也是一笔），同 `add_entry` 的口径。"""
    from decimal import Decimal, InvalidOperation

    try:
        return "" if Decimal(str(value)) >= 0 else "Enter a quantity"
    except (InvalidOperation, ValueError):
        return "Enter a quantity"


# ── 草稿、复核、确定 ────────────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """答完了：不建任何东西，只记下「动作@盘点单@答案摘要」给复核与确定用。"""
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((values["action"], str(values.get("count") or ""), digest))}


def _line(key, label_key, value, step=None):
    return {"key": key, "label_key": label_key, "value": value, **({"step": step} if step else {})}


def _rows(ctx, rows):
    counts = _counts()
    count = str(_answer(ctx, "count"))
    out = []
    for row in rows:
        try:
            found = counts.lookup_count(count=count, product=str(row["code"]))["product"]  # loop-query: 复核这次要记的几行
        except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError, KeyError):
            frappe.clear_messages()
            found = {}
        note = " · ".join(filter(None, (found.get("code"), found.get("barcode_shown"))))
        out.append({"code": row["code"], "name": found.get("product_name") or row["code"], "amount": row["amount"],
                    **({"note": note} if note else {})})
    return out


def review(ctx, draft) -> dict:
    """复核卡：做什么 + 对象；新建列仓与名称；记数列要记的各行；结束列已盘 / 未盘与差异表（只列有差异的）。"""
    counts = _counts()
    action = _answer(ctx, "action")
    out = {"version": draft, "ready": True, "problem": "", "problem_text": "",
           "lines": [_line("action", "guide.step.stocktake.action", _(dict(ACTIONS).get(action, "")), "action")],
           "tables": [], "total": ""}
    with _acting(ctx):
        if action == NEW:
            warehouse = _answer(ctx, "warehouse") or (warehouse_default(ctx) or {}).get("value") or ""
            label = next((row["name"] for row in _warehouses(ctx) if row["code"] == warehouse), warehouse)
            out["lines"] += [_line("warehouse", "guide.step.stocktake.warehouse", label, "warehouse"),
                             _line("title", "guide.step.stocktake.title", _answer(ctx, "title"), "title")]
            return out
        count = str(_answer(ctx, "count"))
        task = counts.get_count(count)
        out["lines"].append(_line("count", "guide.step.stocktake.count", task.get("title") or count, "count"))
        if action == COUNT:
            out["lines"].append(_line("mode", "guide.step.stocktake.mode", _(dict(MODES)[_answer(ctx, "mode", ADD)]), "mode"))
            out["tables"].append({"key": "rows", "label_key": "guide.step.stocktake.rows", "step": "rows",
                                  "rows": _rows(ctx, _answer(ctx, "rows", []))})
        if action == NEW_PRODUCT:
            labels = {"code": "product.field.codeValue", "product_name": "product.field.name", "unit": "product.field.unit",
                      "unit_price": "product.field.price", "default_cost": "product.field.cost",
                      "category": "product.field.category", "qty": "stock.workbench.entryQty"}
            out["lines"] += [_line(key, label, _answer(ctx, key), key) for key, label in labels.items()
                             if _answer(ctx, key) not in ("", None)]
        if action == CLOSE:
            total, counted = int(task.get("item_total") or 0), int(task.get("counted_total") or 0)
            out["lines"] += [_line("counted", "stock.count.countedRows", counted),
                             _line("uncounted", "stock.count.rowUncounted", max(total - counted, 0))]
            items = counts.review_count(count).get("items") or ()
            out["tables"].append({"key": "differences", "label_key": "stock.workbench.quantityDiff", "step": "count",
                                  "rows": [{"code": item["name"], "name": item.get("product_name") or item.get("code") or "",
                                            "note": item.get("code") or "", "amount": item["difference"]}
                                           for item in items if item.get("counted") and item.get("difference")][:REVIEW_ROWS]})
    return out


def _record(ctx, count, rows, mode):
    """记数：每行同扫码录入（仓、仓位由服务按账面位置定）；重新盘点先作废这件物料已记的数。全有或全无（出错整笔回滚）。"""
    counts = _counts()
    for row in rows:
        product = str(row["code"])
        if mode == RECOUNT:
            counts.recount_product(count=count, product=product)  # loop-query: 一行一笔，同页面逐条记
        # 同一运行同一行只记一笔（服务按 request_id 去重，同 CC 工具的 ``cc:`` 前缀写法）
        request = "cc:" + hashlib.sha256(f"{ctx.get('run')}:{count}:{product}".encode()).hexdigest()[:32]
        counts.add_entry(count=count, qty=row["amount"], product=product, warehouse=None, location=None, request_id=request)
    return len(rows)


def _new_product(ctx, count, values) -> dict:
    """扫到系统没有的码：当场建物料（`create_product_here`：盘点锁里按码再认一次，别人刚建过或这码其实已有就用那件、不建重复码；
    来由记这份盘点）再记这一笔数（同盘点页「新建物料」后接着记数）。"""
    counts = _counts()
    extra = {key: values[key] for key in PRODUCT_FIELDS if values.get(key) not in (None, "")}
    made = counts.create_product_here(count=count, code=values["code"], product_name=values["product_name"], **extra)
    request = "cc:" + hashlib.sha256(f"{ctx.get('run')}:{count}:{made['name']}".encode()).hexdigest()[:32]
    counts.add_entry(count=count, qty=values["qty"], product=made["name"], warehouse=None, location=None, request_id=request)
    return {"status": "entered", "docnum": count, "product": made["name"], "created": bool(made.get("created"))}


def confirm(ctx, values, draft, consent) -> dict:
    counts = _counts()
    action = values["action"]
    count = str(values.get("count") or "")
    try:
        with _acting(ctx):
            if action == NEW:
                made = counts.create_count(title=values["title"], warehouse=values["warehouse"])
                return {"status": "entered", "docnum": made["name"], "rows": made.get("rows")}
            if action == COUNT:
                return {"status": "entered", "docnum": count, "recorded": _record(ctx, count, values["rows"],
                                                                                  values.get("mode") or ADD)}
            if action == NEW_PRODUCT:
                return _new_product(ctx, count, values)
            if action == CLOSE:
                review_token = counts.review_count(count).get("review_token")
                done = counts.close_count(count, review_token=review_token)
                return {"status": "entered", "docnum": count, "count_status": done.get("status")}
            if action == ADJUST:
                made = counts.adjustment_draft(count)
                return {"status": "entered", "docnum": made.get("doc_no") or made["name"], "adjustment": made["name"]}
            counts.delete_count(count)
            return {"status": "entered", "deleted": [count]}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        step = "title" if action == NEW else "code" if action == NEW_PRODUCT else "count"
        return {"returned": {step: {"reason": "stocktake_refused", "text": str(error)[:300]}}}
