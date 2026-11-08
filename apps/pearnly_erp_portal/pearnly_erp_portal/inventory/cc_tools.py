"""ERP 版 CC 的盘点工具（盘点 2026-11-08 P1-1）与收发存报表导出（``erp_stock_card_export``，同网页导出）：在对话里列盘点、看差异、新建、按码记实盘数、结束、按结果生成物料调整单草稿。

- 形状照 Cowork 的 ``cowork_stocktake_*``（列表、结果、记数、结束），ERP 自己在 ``hooks.py`` 的 ``pearnly_assistant_tools`` 声明（ADR-009）；
  两份盘点代码不在这里合并，工具只调 ERP 盘点页同一组服务函数（``inventory.count``），权限、仓库可见、锁与审阅令牌都由它们核。
- 公司按对话挂的那家：工具执行期间 ERP 的「当前公司」就是它（共用层 ``pearnly_assistant_tool_context`` → ``masters.company.acting``），
  盘点服务照页面口径按当前公司取仓；模型给了别的公司（``masters.cc_tools.resolve`` 认得出、本人可用）就在那家下面执行。
- 不加「允许」卡片（主会话 11-08 第 4 条，同 Cowork 盘点工具）；结束只锁定结果、不动库存（ERP 口径，岳父 11-06），调整单只生成草稿，
  人在调整单页提交才入账。
- 业务拒绝回 ``not_saved`` 与原因（已翻译），不抛给模型（同 Cowork ``_call``）。
"""
import json
import uuid

import frappe

LIST_MAX = 50
ROWS_SHOWN = 40  # 同 Cowork STOCK_ROWS_SHOWN：结果默认只列有差异或还没盘的前 40 项


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _call(fn, *args, **kwargs) -> dict:
    """调盘点页同一个服务函数；业务拒绝回 not_saved 与原因，不抛给模型。"""
    try:
        result = fn(*args, **kwargs)
        frappe.db.commit()
        return result if isinstance(result, dict) else {"result": result}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        return {"status": "not_saved", "reason": str(error)[:300]}


def _company(tool_input) -> tuple:
    """(公司编号或 None, 认不出的原因)：给了 company 按 ``masters.cc_tools.resolve`` 认；没给用对话挂的那家；都没有 None（本人当前公司）。"""
    from pearnly_erp_portal.masters import cc_tools

    wanted, reason = cc_tools.resolve(tool_input.get("company"), cc_tools.filed())
    return (wanted or None), reason


def _in(company):
    """在 ``company`` 下面执行（``masters.company.acting``，不在本人可用公司里的拒）；None = 照当前（对话挂的那家）。"""
    from pearnly_erp_portal.masters import company as companies

    return companies.acting(company)


def _counts():
    from pearnly_erp_portal.inventory import count

    return count


def stocktake_list(tool_input) -> str:
    company, reason = _company(tool_input)
    if reason:
        return _json({"status": "not_saved", "reason": reason})
    counts = _counts()
    limit = max(1, min(int(tool_input.get("limit") or 20), LIST_MAX))
    offset = max(0, int(tool_input.get("offset") or 0))
    status = tool_input.get("status") or None
    with _in(company):
        rows = _call(counts.list_counts, status=status, limit=limit, start=offset)
        if rows.get("status") == "not_saved":
            return _json(rows)
        total = _call(counts.count_total, status=status)
    found = rows.get("result") or []
    keep = ("name", "title", "status", "warehouse_name", "item_total", "counted_total", "difference_total", "creation",
            "closed_at")
    return _json({"total": total.get("result"), "counts": [{k: row.get(k) for k in keep} for row in found],
                  "next_offset": offset + len(found) if len(found) == limit else None})


def stocktake_report(tool_input) -> str:
    """盘点结果：各项账面数、实盘数、差异；默认只列有差异或还没盘的（同 Cowork ``cowork_stocktake_report``）。"""
    count = str(tool_input.get("count") or "")
    data = _call(_counts().review_count, count)
    if data.get("status") == "not_saved":
        return _json(data)
    items = data.get("items") or []
    if not tool_input.get("all_items"):
        items = [r for r in items if not r.get("counted") or r.get("difference") or r.get("location_difference")]
    keep = ("code", "product_name", "unit", "warehouse_name", "location", "book_qty", "counted_qty", "difference",
            "actual_positions")
    rows = [{k: r.get(k) for k in keep if r.get(k) not in (None, "")} | {"counted": bool(r.get("counted"))}
            for r in items[:ROWS_SHOWN]]
    head = data.get("task") or {}
    return _json({"count": count, "title": head.get("title"), "status": head.get("status"),
                  "warehouse": head.get("warehouse_name"), "items_total": head.get("item_total"),
                  "counted_total": head.get("counted_total"), "difference_total": head.get("difference_total"),
                  "adjustment": head.get("adjustment_no") or head.get("adjustment"),
                  "stock_changed": data.get("conflicts") or [], "listed": len(items), "items": rows})


def stocktake_create(tool_input) -> str:
    """同盘点页「新建」（盘点在库）：按这个仓此刻的结存抄一份快照。"""
    company, reason = _company(tool_input)
    if reason:
        return _json({"status": "not_saved", "reason": reason})
    with _in(company):
        return _json(_call(_counts().create_count, title=str(tool_input.get("title") or ""),
                           warehouse=str(tool_input.get("warehouse") or "") or None))


def stocktake_count(tool_input) -> str:
    """按物料代码或条码记一笔实盘数（同扫码录入：同一物料多笔累加）；recount 先把这件物料已记的数作废再记。"""
    counts = _counts()
    count = str(tool_input.get("count") or "")
    code = str(tool_input.get("code") or "").strip()
    found = _call(counts.lookup_count, count=count, code=code)
    if found.get("status") == "not_saved":
        return _json(found)
    if found.get("not_product_code"):
        return _json({"status": "not_saved", "reason": found["not_product_code"]})
    if found.get("not_found"):
        return _json({"status": "not_saved", "reason": "not_found: no product has this code; add the product on the "
                                                       "stock count page or the products page first"})
    if found.get("candidates"):
        return _json({"status": "not_saved", "reason": "several products have this code: ask the user which one",
                      "candidates": found["candidates"]})
    product = (found.get("product") or {}).get("name")
    if tool_input.get("recount"):
        cleared = _call(counts.recount_product, count=count, product=product)
        if cleared.get("status") == "not_saved" or tool_input.get("qty") is None:
            return _json(cleared)
    if tool_input.get("qty") is None:
        return _json({"status": "not_saved", "reason": "qty is required."})
    return _json(_call(counts.add_entry, count=count, qty=tool_input.get("qty"), product=product,
                       warehouse=str(tool_input.get("warehouse") or "") or None,
                       location=str(tool_input.get("location") or "") or None,
                       request_id=f"cc:{uuid.uuid4().hex}"))  # 来源照页面录入（Desk）：记数来源只有 Desk / LINE / Import


def stocktake_close(tool_input) -> str:
    """结束盘点：照盘点页先取一次审阅令牌再结束（期间有人改了就照页面口径拒），只锁定结果、不动库存。"""
    counts = _counts()
    count = str(tool_input.get("count") or "")
    review = _call(counts.review_count, count)
    if review.get("status") == "not_saved":
        return _json(review)
    return _json(_call(counts.close_count, count, review_token=review.get("review_token")))


def stocktake_adjust(tool_input) -> str:
    """按盘点结果生成物料调整单草稿（同盘点页「按盘点结果调整物料」；已有就回那一张）。不提交、不动库存。"""
    out = _call(_counts().adjustment_draft, str(tool_input.get("count") or ""))
    if out.get("status") == "not_saved":
        return _json(out)
    return _json({**out, "state": "draft"})


FILE_NOTE = ("Added to this conversation: the user opens it in the chat. It was not saved to any folder on the user's "
             "computer.")  # 同 Cowork ``FILES_ADDED_NOTE``（10-13 测试机：CC 说放进了「结果文件夹」，用户去电脑上找）


def stock_card_export(tool_input) -> str:
    """收发存报表（照 MR.ERP 三种：合并全部仓 / 汇总 / 分仓；Excel / PDF），调网页导出同一个 ``card_export.build``（权限、成本可见、
    范围都由它核），文件放进对话。工具跑在对话的后台任务里（30 分钟时限，不受网页请求 120 秒限制），大的也当场生成，同 Cowork
    的导出工具；不排网页的后台导出（那条做完只在网页上提示下载，对话里拿不到）。"""
    from pearnly_core.assistant import files as chat_files
    from pearnly_erp_portal.inventory import card_export

    company, reason = _company(tool_input)
    if reason:
        return _json({"status": "not_exported", "reason": reason})
    args = {key: tool_input.get(key) for key in card_export.ARGS if tool_input.get(key) not in (None, "")}
    with _in(company):
        try:
            built = card_export.build(**args)
            path = chat_files.add_to_conversation(built["filename"], built["content"],
                                                  card_export.content_type(args.get("format")))
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            frappe.clear_messages()
            return _json({"status": "not_exported", "reason": str(error)[:300]})
    return _json({"status": "exported", "file": path, "files_added_to_chat": True, "files_note": FILE_NOTE})


COMPANY_PROP = {"type": "string", "description": "Only when the stock count is for another company than the one this "
                                                 "conversation is filed under: that company's id, after checking with "
                                                 "the user."}

TOOLS = [
    {"name": "erp_stocktake_list", "label": "Listed stock counts",
     "description": "The company's stock counts (stocktakes), newest first: name, title, status (Active = counting, Closed = "
                    "finished with differences not adjusted yet, Posted = finished and adjusted), warehouse and totals. "
                    f"Up to {LIST_MAX} per call; pass next_offset as offset for the next page.",
     "input_schema": {"type": "object", "properties": {
         "status": {"type": "string", "enum": ["Active", "Closed", "Posted"]},
         "offset": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": LIST_MAX},
         "company": COMPANY_PROP}},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_list"},
    {"name": "erp_stocktake_report", "label": "Viewed a stock count",
     "description": "Book quantity, counted quantity and difference per item of a stock count; by default only items with "
                    f"a difference, found elsewhere or not counted yet (up to {ROWS_SHOWN}), all_items for everything. "
                    "stock_changed lists items whose stock moved during counting (they must be counted again before "
                    "finishing); adjustment is its product adjustment once made.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}, "all_items": {"type": "boolean"}},
                      "required": ["count"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_report"},
    {"name": "erp_stocktake_create", "label": "Created a stock count",
     "description": "Start a stock count of one warehouse, like New on the stock count page: the warehouse's stock now "
                    "becomes the book quantities. warehouse is needed when the company has more than one; ask the user "
                    "which one.",
     "input_schema": {"type": "object", "properties": {"title": {"type": "string"}, "warehouse": {"type": "string"},
                                                       "company": COMPANY_PROP}, "required": ["title"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_create"},
    {"name": "erp_stocktake_count", "label": "Recorded a count",
     "description": "Record a counted quantity for a product by code or barcode, like scanning on the page: entries for "
                    "the same product add up. Warehouse and location default to the product's book position; give them "
                    "when it was found elsewhere. recount true voids that product's earlier counts first and then "
                    "records qty.",
     "input_schema": {"type": "object", "properties": {
         "count": {"type": "string"}, "code": {"type": "string"}, "qty": {"type": "number", "minimum": 0},
         "warehouse": {"type": "string"}, "location": {"type": "string"}, "recount": {"type": "boolean"}},
         "required": ["count", "code"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_count"},
    {"name": "erp_stocktake_close", "label": "Finished a stock count",
     "description": "Finish a stock count: the counts are locked and the differences worked out; stock does not change "
                    "yet. Only when the user says counting is done. Then make the adjustment with erp_stocktake_adjust "
                    "if the user wants the stock corrected.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_close"},
    {"name": "erp_stocktake_adjust", "label": "Created an adjustment from a stock count",
     "description": "Make the product adjustment for a finished stock count's differences, like Adjust products from "
                    "count results on the page. It is a draft: stock changes only when the user submits it on its page "
                    "(give the user its number). If the count already has one, that one is returned.",
     "input_schema": {"type": "object", "properties": {"count": {"type": "string"}}, "required": ["count"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stocktake_adjust"},
    {"name": "erp_stock_card_export", "label": "Exported the stock receipt-issue report",
     "description": "The stock receipt-issue report as a file in this conversation, like Export on the stock page (the "
                    "MR.ERP layouts): report all = every warehouse together, summary = one line per product, "
                    "by_warehouse = each warehouse separately; format xlsx or pdf. Dates are YYYY-MM-DD. Product and "
                    "warehouse ranges are codes, both ends included; leave them out for everything (warehouse range only "
                    "for by_warehouse). Ask the user which report, format and dates when they did not say.",
     "input_schema": {"type": "object", "properties": {
         "report": {"type": "string", "enum": ["all", "summary", "by_warehouse"]},
         "format": {"type": "string", "enum": ["xlsx", "pdf"]},
         "date_from": {"type": "string"}, "date_to": {"type": "string"},
         "product_from": {"type": "string"}, "product_to": {"type": "string"},
         "warehouse_from": {"type": "string"}, "warehouse_to": {"type": "string"}, "company": COMPANY_PROP},
         "required": ["report", "format", "date_from", "date_to"]},
     "handler": "pearnly_erp_portal.inventory.cc_tools.stock_card_export"},
]

STEP_NAMES = {
    "erp_stocktake_list": "Listing stock counts",
    "erp_stocktake_report": "Viewing a stock count",
    "erp_stocktake_create": "Creating a stock count",
    "erp_stocktake_count": "Recording a count",
    "erp_stocktake_close": "Finishing a stock count",
    "erp_stocktake_adjust": "Creating an adjustment from a stock count",
    "erp_stock_card_export": "Exporting the stock receipt-issue report",
}
