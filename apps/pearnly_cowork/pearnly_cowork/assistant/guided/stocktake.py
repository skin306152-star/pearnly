"""Cowork 盘点的引导式（CC 对话里点「盘点」，总表 CW-D-ST；评估 docs/work/2026-11-16-cc-guided-stocktake-评估.md）。

``GUIDES`` 是纯字面量（闸门按源码读）。业务 id、菜单字、图标、步骤 key 与 ERP 的盘点引导（`pearnly_erp_portal.inventory.cc_guide`）相同。
第一题「做什么」，各分支用 ``when`` 分开；确定时只转调盘点页 / CC 工具同一组函数，不另查库、不另写规则：

- 模板：`assistant.tools.stocktake_template`（模板文件放进对话）；
- 新建：交一份 Excel → 名称（可空，空 = 文件名）→ `assistant.tools.stocktake_create`（同一段对话同一份表只建一张）；
- 记数：盘点单（进行中）→ 物料（本盘点的行，服务端搜索）+ 数量（0 有效）一行一行加 → 记法（累加 / 重新盘点该物料）→
  `stocktake.desk_api` 的 lookup / recount / add_entry（同扫码录入；仓、仓位默认这一项的账面位置）；
- 结束：盘点单（进行中）→ `desk_api.api_stock_count_close`，差异工作簿放进对话（`assistant.tools.stocktake_export`）；
- 导出：盘点单 → 差异工作簿放进对话；
- 删除：盘点单（多选）→ `stocktake.upload.delete`（真删，同盘点页）。

盘点不按公司隔开（用户 11-16：Cowork 工作空间对象不分公司），选项列本工作空间全部盘点单。不写账本。
结束、删除画成允许卡（只有「拒绝 / 允许」，``consent`` 按「做什么」的答案区分，约定 11-16），其余是「确定」。
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from uuid import NAMESPACE_URL, uuid5

import frappe
from frappe import _

TEMPLATE, NEW, COUNT, CLOSE, EXPORT, DELETE = "template", "new", "count", "close", "export", "delete"
# 做什么 → 选项名（盘点页 / 盘点工作台已有词条）
ACTIONS = ((TEMPLATE, "cowork.stock.count.template"), (NEW, "cowork.stock.count.new"),
           (COUNT, "cowork.stock.workbench.record"), (CLOSE, "cowork.stock.count.close"),
           (EXPORT, "cowork.stock.action.export"), (DELETE, "Delete"))
ADD, RECOUNT = "add", "recount"
MODES = ((ADD, "cowork.stock.workbench.record"), (RECOUNT, "cowork.stock.workbench.recount"))
ACTIVE = "active"
STATUS_LABELS = {"active": "cowork.stock.count.statusActive", "closed": "cowork.stock.count.statusClosed"}
# 各动作能选的盘点单状态（记数、结束只对进行中的；导出、删除不限，同盘点页）
STATUSES = {COUNT: (ACTIVE,), CLOSE: (ACTIVE,)}
SCAN_PAGE, SCAN_PAGES = 100, 20          # 盘点单选项按页扫列表（`upload.records` 每页最多 100），最多 2000 张
REVIEW_ROWS = 200                         # 结束前的差异表最多列多少行（只列有差异的）
DRAFT_SEP = "@"


@contextlib.contextmanager
def _conversation(ctx):
    """调 CC 工具 / 对话文件时带上这段对话（附件、同一份表只建一张都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def allowed(user) -> bool:
    """菜单上出不出「盘点」：有盘点功能的人（`stocktake.access.allowed`，盘点页同一口径）。"""
    from pearnly_cowork.stocktake import access

    return bool(user) and user != "Guest" and bool(access.allowed(user))


def _answer(ctx, key, default=""):
    return (ctx.get("answers") or {}).get(key) or default


def _page(rows, txt="", start=0, page_length=20):
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if text in f"{row['name']} {row.get('note') or ''}".casefold()]
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


# ── 做什么 ───────────────────────────────────────────────────────────────────


def action_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in ACTIONS]
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [row for row in rows if row["code"] in wanted], "more": False}
    return _page(rows, txt, start, page_length)


# ── 盘点单 ───────────────────────────────────────────────────────────────────


def _tasks():
    """本工作空间的盘点单（新的在前），同盘点页列表（`upload.records`，按页取，最多 :data:`SCAN_PAGES` 页）。"""
    from pearnly_cowork.stocktake import upload

    out = []
    for number in range(SCAN_PAGES):
        found = upload.records(number * SCAN_PAGE, SCAN_PAGE)  # loop-query: 列表接口按页取（每页有上限），最多 20 页
        rows = found.get("tasks") or []
        out.extend(rows)
        if len(rows) < SCAN_PAGE or len(out) >= int(found.get("total") or 0):
            break
    return out


def _row(task):
    return {"code": task["id"], "name": task["name"] or task["id"], "note": _(STATUS_LABELS.get(task["status"], "Closed"))}


def _statuses(ctx):
    return STATUSES.get(_answer(ctx, "action"), ())


def count_options(ctx, txt="", start=0, page_length=20, codes=None):
    """盘点单：记数、结束只列进行中的；导出、删除列全部。``codes`` 给了按原样核（`upload.summaries`，还在、状态还对）。"""
    from pearnly_cowork.stocktake import upload

    statuses = _statuses(ctx)
    if codes is not None:
        found = upload.summaries([str(code) for code in codes])
        rows = [{"code": code, "name": row["title"]} for code, row in found.items()
                if not statuses or row["status"] in statuses]
        return {"options": rows, "more": False}
    rows = [_row(task) for task in _tasks() if not statuses or task["status"] in statuses]
    return _page(rows, txt, start, page_length)


# ── 记数：物料 + 数量 ────────────────────────────────────────────────────────────


def item_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这张盘点的物料行（同录入框的服务端查询 `counting.search`：名称 / 编码 / 条码）；``codes`` 给了逐个按行号核（`counting.lookup`）。"""
    from pearnly_cowork.stocktake import counting

    count = str(_answer(ctx, "count"))
    if codes is not None:
        rows = []
        for code in codes:
            try:
                found = counting.lookup(count, item_id=str(code))["item"]  # loop-query: 只核这次交来的几行
            except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError):
                frappe.clear_messages()
                continue
            if found["id"] == str(code):
                rows.append({"code": found["id"], "name": found["product_name"] or found["id"]})
        return {"options": rows, "more": False}
    size = max(1, min(int(page_length or 20), 50))
    found = counting.search(count, txt, start, size + 1)
    return {"options": [{"code": row[0], "name": row[1] or row[0], **({"note": row[2]} if row[2] else {})}
                        for row in found[:size]], "more": len(found) > size}


def mode_options(ctx, txt="", start=0, page_length=20, codes=None):
    rows = [{"code": code, "name": _(label)} for code, label in MODES]
    if codes is not None:
        rows = [row for row in rows if row["code"] in {str(code) for code in codes}]
    return {"options": rows, "more": False}


def mode_default(ctx):
    """记法：默认累加（同扫码录入）；进「已定」，可改成重新盘点该物料。"""
    return {"value": ADD, "value_label": _(MODES[0][1]), "source": "default"}


# ── 新建：文件 ──────────────────────────────────────────────────────────────────


def file_check(ctx, value) -> str:
    """只收一份、本对话里的 .xlsx（盘点页同一口径：只认 Excel 模板的列）。"""
    from pearnly_core.assistant import files as chat_files

    if not isinstance(value, list) or len(value) != 1:
        return "stocktake.file_invalid"
    with _conversation(ctx):
        try:
            name, _content = chat_files.attachment(str(value[0]))
        except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            return "stocktake.file_invalid"
    return "" if str(name).lower().endswith(".xlsx") else "stocktake.file_invalid"


# ── 草稿、复核、确定 ────────────────────────────────────────────────────────────


def draft(ctx, values) -> dict:
    """答完了：不建任何东西，只记下「动作@盘点单@答案摘要」给复核与确定用（同读回）。"""
    action = values["action"]
    target = values.get("count") or ",".join(values.get("counts") or ())
    digest = hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return {"draft": DRAFT_SEP.join((action, str(target), digest))}


def _line(key, label_key, value, step=None):
    out = {"key": key, "label_key": label_key, "value": value}
    if step:
        out["step"] = step
    return out


def _named(ctx, names):
    from pearnly_cowork.stocktake import upload

    found = upload.summaries(list(names))
    return [{"code": name, "name": (found.get(name) or {}).get("title") or name, "amount": ""} for name in names]


def _count_rows(ctx, rows):
    from pearnly_cowork.stocktake import counting

    count = str(_answer(ctx, "count"))
    out = []
    for row in rows:
        try:
            found = counting.lookup(count, item_id=str(row["code"]))["item"]  # loop-query: 复核这次要记的几行
        except (frappe.ValidationError, frappe.DoesNotExistError, frappe.PermissionError):
            frappe.clear_messages()
            found = {}
        note = " · ".join(filter(None, (found.get("product_code"), found.get("barcode"))))
        out.append({"code": row["code"], "name": found.get("product_name") or row["code"], "amount": row["amount"],
                    **({"note": note} if note else {})})
    return out


def _differences(count):
    from pearnly_cowork.stocktake import desk_api

    review = desk_api.api_stock_count_review(count)
    rows = [{"code": item["name"], "name": item["product_name"] or item["code"] or item["name"],
             "note": item.get("code") or "", "amount": item["difference"]}
            for item in review.get("items") or () if item.get("counted") and item.get("difference")]
    return review, rows[:REVIEW_ROWS]


def review(ctx, draft) -> dict:
    """复核卡：做什么 + 对象；记数列要记的各行与数量；结束列已盘 / 未盘与差异表（只列有差异的）；删除列要删的盘点单。"""
    from pearnly_cowork.stocktake import upload

    action = _answer(ctx, "action")
    label = dict(ACTIONS).get(action, "")
    out = {"version": draft, "ready": True, "problem": "", "problem_text": "",
           "lines": [_line("action", "guide.step.stocktake.action", _(label), "action")], "tables": [], "total": ""}
    if action == NEW:
        out["lines"].append(_line("title", "guide.step.stocktake.title", _answer(ctx, "title"), "title"))
    if action in (COUNT, CLOSE, EXPORT):
        count = str(_answer(ctx, "count"))
        summary = upload.summaries([count]).get(count) or {"title": count, "total": 0, "counted": 0}
        out["lines"].append(_line("count", "guide.step.stocktake.count", summary["title"], "count"))
        if action == CLOSE:
            out["lines"] += [_line("counted", "cowork.stock.count.countedRows", summary["counted"]),
                             _line("uncounted", "cowork.stock.count.rowUncounted",
                                   max(summary["total"] - summary["counted"], 0))]
            _review, rows = _differences(count)
            out["tables"].append({"key": "differences", "label_key": "cowork.stock.workbench.quantityDiff",
                                  "step": "count", "rows": rows})
    if action == COUNT:
        mode = _answer(ctx, "mode", ADD)
        out["lines"].append(_line("mode", "guide.step.stocktake.mode", _(dict(MODES)[mode]), "mode"))
        out["tables"].append({"key": "rows", "label_key": "guide.step.stocktake.rows", "step": "rows",
                              "rows": _count_rows(ctx, _answer(ctx, "rows", []))})
    if action == DELETE:
        out["tables"].append({"key": "counts", "label_key": "guide.step.stocktake.counts", "step": "counts",
                              "rows": _named(ctx, _answer(ctx, "counts", []))})
    return out


def _tool(name, payload):
    """调 CC 工具同一个函数（模板、新建、导出：文件进对话的写法只在那里一份），回它的 JSON。"""
    from pearnly_cowork.assistant import tools

    return json.loads(getattr(tools, name)(payload))


def _files(found):
    from pearnly_core.assistant import files as chat_files

    path = found.get("file") or ""
    return [{"file": chat_files.resolve(path), "name": path.rsplit("/", 1)[-1]}] if path else []


def _refused(step, error):
    frappe.db.rollback()
    frappe.clear_messages()
    return {"returned": {step: {"reason": "stocktake_refused", "text": str(error)[:300]}}}


def _record(ctx, count, rows, mode):
    """记数：每行同扫码录入（仓、仓位默认这一项的账面位置）；重新盘点先作废这件物料已记的数。全有或全无。"""
    from pearnly_cowork.stocktake import desk_api

    done = 0
    for row in rows:
        item = str(row["code"])
        found = desk_api.api_stock_count_lookup(count, product=item)  # loop-query: 一行一笔，同页面逐条记
        book = (found.get("rows") or [{}])[0]
        if mode == RECOUNT:
            desk_api.api_stock_count_recount(count, item)
        # 同一运行同一行只记一笔（重放回同一回执，`service.write` 的操作号）
        request = str(uuid5(NAMESPACE_URL, f"pearnly-guide:{ctx.get('run')}:{count}:{item}"))
        desk_api.api_stock_count_add_entry(count, row["amount"], item=item, warehouse=book.get("warehouse") or "",
                                           location=book.get("location") or "", request_id=request)
        done += 1
    return done


def _label(ctx, count) -> str:
    """结果卡上这次盘点给人看的名字（盘点选项里的名字；查不到不写，不出内部编号）。"""
    if not count:
        return ""
    from pearnly_cowork.stocktake import upload

    return str((upload.summaries([str(count)]).get(str(count)) or {}).get("title") or "")


def confirm(ctx, values, draft, consent) -> dict:
    from pearnly_cowork.stocktake import desk_api, upload

    action = values["action"]
    with _conversation(ctx):
        if action == TEMPLATE:
            return {"status": "entered", "files": _files(_tool("stocktake_template", {}))}
        if action == NEW:
            made = _tool("stocktake_create", {"file": values["file"][0], "title": values.get("title") or ""})
            if made.get("status") == "not_saved":
                return {"returned": {"file": {"reason": "stocktake_refused", "text": str(made.get("reason") or "")}}}
            return {"status": "entered", "label": _label(ctx, made.get("id")) or str(values.get("title") or ""), "imported": made.get("imported"),
                    "counted": made.get("counted"), "problems": made.get("problems") or []}
        count = str(values.get("count") or "")
        try:
            if action == COUNT:
                return {"status": "entered", "label": _label(ctx, count),
                        "recorded": _record(ctx, count, values["rows"], values.get("mode") or ADD)}
            if action == CLOSE:
                desk_api.api_stock_count_close(count)
            if action == DELETE:
                found = upload.delete(list(values["counts"]))
                return {"status": "entered", "deleted": found.get("ids") or []}
        except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError) as error:
            return _refused("counts" if action == DELETE else "count", error)
        # 结束、导出：差异工作簿放进对话（同 CC 工具 cowork_stocktake_export 的文件名）
        found = _tool("stocktake_export", {"count": count})
        if found.get("status") != "exported":
            return {"status": "failed", "label": _label(ctx, count), "reason_text": str(found.get("reason") or "")}
        return {"status": "entered", "label": _label(ctx, count), "files": _files(found)}


# ── 声明 ─────────────────────────────────────────────────────────────────────

GUIDES = [
    {"id": "stocktake", "label_key": "guide.business.stocktake", "icon": "clipboard-list", "order": 60,
     "modes": ["chat"], "ledgers": ["express_local", "express_cloud", "mrerp"],
     "allowed": "pearnly_cowork.assistant.guided.stocktake.allowed",
     "opening_key": "guide.open.stocktake", "source": "form", "writes": True,
     "consent": {"key": "action", "in": ["close", "delete"]},
     "steps": [
         {"key": "action", "input": "choice", "rule": "always", "options": "pearnly_cowork.assistant.guided.stocktake.action_options"},
         {"key": "file", "input": "file", "rule": "always", "after": ["action"], "when": {"key": "action", "value": "new"},
          "check": "pearnly_cowork.assistant.guided.stocktake.file_check"},
         {"key": "title", "input": "text", "rule": "always", "required": False, "after": ["action"],
          "when": {"key": "action", "value": "new"}},
         {"key": "count", "input": "choice", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "in": ["count", "close", "export"]},
          "options": "pearnly_cowork.assistant.guided.stocktake.count_options"},
         {"key": "counts", "input": "multi", "rule": "always", "search": True, "after": ["action"],
          "when": {"key": "action", "value": "delete"}, "options": "pearnly_cowork.assistant.guided.stocktake.count_options"},
         {"key": "rows", "input": "amounts", "rule": "always", "search": True, "zero": True, "after": ["count"],
          "when": {"key": "action", "value": "count"}, "options": "pearnly_cowork.assistant.guided.stocktake.item_options"},
         {"key": "mode", "input": "choice", "rule": "auto", "after": ["count"], "when": {"key": "action", "value": "count"},
          "options": "pearnly_cowork.assistant.guided.stocktake.mode_options",
          "default": "pearnly_cowork.assistant.guided.stocktake.mode_default"},
     ],
     "draft": "pearnly_cowork.assistant.guided.stocktake.draft", "review": "pearnly_cowork.assistant.guided.stocktake.review",
     "confirm": "pearnly_cowork.assistant.guided.stocktake.confirm", "result": "pearnly_core.assistant.guide.form_result.result",
     "tool": "cowork_stocktake_count"},
]
