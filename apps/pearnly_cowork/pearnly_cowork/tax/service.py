"""报税草稿的编排：读数据 → 纯计算（`pp30` / `input_vat` / `wht`）→ 存草稿 → 回摘要与工作簿。

- 同一公司（税号 + 分店）同月同类只留一份草稿（``Cowork Tax Draft``），重出时刷新数字、保留会计的选项与逐行改动。
  草稿表只存各栏、合计与问题计数（加选项与逐行改动）：行级明细（回读的对方名称、税号、金额，识别记录派生的收款方）
  不长期存，工作簿由本次请求当场算出的摘要排版。草稿可删（`delete`）。
- 先读数据、做完计算，写之前才锁，且只锁这份草稿（已有时锁行；两个请求同时首次写同一份时，后到的撞主键，
  回到保存点按已有的那份重算再写）；不锁工作空间行（推送准备也要锁它）。
- 确定性计算、零费用：不调模型、不识别、不扣费；只出草稿，不写 Express、不代为申报。
- ภ.พ.30 用当前用户该账套该月最近一次 Express 回读；没有就回 ``readback_needed``（先回读）。
- ภ.ง.ด.3 / 53 用识别记录与 50 ทวิ 参考记录；所得类型问一次记住（`memory`）。
"""
import hashlib
import json
from collections import Counter

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.tax import access, documents, input_vat, memory, periods, pp30, sources, wht

DOCTYPE = access.TASK
VAT, WHT = access.VAT, access.WHT
EDITS_MAX = 200
ANSWERS_MAX = 50
DELETE_MAX = 50
SAVEPOINT = "cowork_tax_draft"
# 草稿表里留的：各栏、合计、问题计数与来源说明；行级明细不留
KEPT = {VAT: ("status", "draft", "kind", "period", "company", "connection", "account_set_key", "readback", "boxes",
              "outcome", "options", "sales", "purchases", "claimable_if_fixed", "previous_excess", "deadlines",
              "documents_checked", "issues_by_code", "tax_total"),
        WHT: ("status", "draft", "kind", "period", "company", "deadlines", "issues_by_code", "totals",
              "documents_read", "truncated", "tax_total")}
KEPT_FORM = ("payees", "sheets", "base", "tax", "sheet_totals")


def _period(text):
    try:
        return periods.parse_period(text)
    except ValueError:
        frappe.throw(_("tax.period_invalid"))


def _workspace() -> str:
    access.require()
    workspace = scope.default_workspace()
    access.require(workspace)
    return workspace


def record_id(workspace, kind, tax_id, branch, period, fallback="") -> str:
    key = [frappe.local.site, workspace, kind, tax_id or fallback, branch or "", period]
    return "CTD-" + hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:40]


def _load(name):
    return frappe.get_doc(DOCTYPE, name, for_update=True) if frappe.db.exists(DOCTYPE, name) else None


def _stored(doc) -> dict:
    try:
        value = json.loads(doc.options_json or "{}") if doc else {}
    except ValueError:
        value = {}
    return value if isinstance(value, dict) else {}


def kept(summary) -> dict:
    """草稿表里存的摘要：各栏、合计与问题计数（见 ``KEPT``）。"""
    out = {key: summary[key] for key in KEPT[summary["kind"]] if key in summary}
    if summary["kind"] == WHT:
        out["forms"] = {form: {key: data.get(key) for key in KEPT_FORM}
                        for form, data in (summary.get("forms") or {}).items()}
    return out


def _store(doc, name, workspace, kind, summary, options, **values):
    if doc is None:
        doc = frappe.get_doc({"doctype": DOCTYPE, "record_id": name, "workspace": workspace, "kind": kind,
                              "period": summary["period"], "created_by": frappe.session.user})
    company = summary.get("company") or {}
    doc.update({"company_tax_id": company.get("tax_id") or "", "company_branch": company.get("branch") or "",
                "company_name": str(company.get("name") or "")[:200],
                "issue_count": sum((summary.get("issues_by_code") or {}).values()),
                "tax_total": summary.get("tax_total") or "",
                "options_json": json.dumps(options, ensure_ascii=False, sort_keys=True),
                "summary_json": json.dumps(kept(summary), ensure_ascii=False, default=str),
                "updated_at": now_datetime(), **values})
    documents.persist(doc)
    return doc


def _write(name, workspace, kind, build) -> dict:
    """存草稿：``build(已存的选项)`` → (摘要, 选项, 其他字段)，纯计算，在这份草稿的行锁里做（选项不丢更新）。"""
    existing = _load(name)
    if existing is None:
        summary, options, values = build({})
        frappe.db.savepoint(SAVEPOINT)
        try:
            _store(None, name, workspace, kind, summary, options, **values)
            return summary
        except frappe.DuplicateEntryError:
            # 另一个请求刚写了同一份：回到保存点，锁住它（当前读看得到刚提交的行），按它存的选项重算再写
            frappe.db.rollback(save_point=SAVEPOINT)
            existing = frappe.get_doc(DOCTYPE, name, for_update=True)
    summary, options, values = build(_stored(existing))
    _store(existing, name, workspace, kind, summary, options, **values)
    return summary


# ---------------- ภ.พ.30 ----------------

def _vat_given(given) -> dict:
    """本次给的选项（核过）；没给的不出现。"""
    out = {}
    for key in ("exempt_sales", "excess_brought_forward"):
        if (given or {}).get(key) not in (None, ""):
            value = pp30.money(given[key])
            if value is None or value < 0:
                frappe.throw(_("tax.option_invalid"))
            out[key] = str(value)
    if (given or {}).get("zero_vat_as"):
        if given["zero_vat_as"] not in pp30.ZERO_VAT_AS:
            frappe.throw(_("tax.option_invalid"))
        out["zero_vat_as"] = given["zero_vat_as"]
    return out


def _vat_options(given, stored) -> dict:
    return {"exempt_sales": stored.get("exempt_sales") or "0.00",
            "excess_brought_forward": stored.get("excess_brought_forward") or "0.00",
            "zero_vat_as": stored.get("zero_vat_as") or "zero_rated", **given}


def _previous_excess(workspace, tax_id, branch, year, month) -> str:
    """上月同一公司草稿的第 12 栏（多缴可结转）；没有回空。只作提示：会计可能已申请退税，不自动带入第 10 栏。"""
    if not tax_id:
        return ""
    name = record_id(workspace, VAT, tax_id, branch, periods.text(*periods.shift(year, month, -1)))
    raw = frappe.db.get_value(DOCTYPE, name, "summary_json")
    try:
        value = (json.loads(raw or "{}").get("boxes") or {}).get("12") or ""
    except ValueError:
        return ""
    return value if pp30.money(value) and pp30.money(value) > 0 else ""


def vat_draft(connection, account_set_key, period, options=None) -> dict:
    workspace = _workspace()
    year, month = _period(period)
    connection, account_set_key = str(connection or "").strip(), str(account_set_key or "").strip()
    if not connection or not account_set_key:
        frappe.throw(_("tax.request_invalid"))
    given = _vat_given(options)
    data = sources.vat_readback(connection, account_set_key, year, month)
    if data is None:
        return {"status": "readback_needed", "period": periods.text(year, month), "connection": connection,
                "account_set_key": account_set_key}
    header = data.get("header") or {}
    tax_id, branch = wht.tax_id(header.get("tax_id")), str(header.get("branch") or "").strip()[:10]
    name = record_id(workspace, VAT, tax_id, branch, periods.text(year, month),
                     fallback=f"{connection}|{account_set_key}")
    rows, unreadable = pp30.records(data.get("records") or [])
    selection = pp30.select(rows, year, month)
    checked = sources.can_read_documents()
    linked = sources.purchase_documents(selection["purchases"]) if checked else {}
    issues = input_vat.check(selection, linked, tax_id, year, month)
    if not checked:  # 没有读识别记录的权限：票种与买方没核，不说「没有识别记录」
        issues = [item for item in issues if item["code"] != "input_no_document"]
    issues += input_vat.unreadable(unreadable)
    claim_amount, claim_vat = input_vat.claimable(selection["purchases"], issues)
    sales_amount, sales_vat = pp30.total(selection["sales"])
    purchase_amount, purchase_vat = pp30.total(selection["purchases"])
    due = periods.deadlines("pp30", year, month)
    previous = _previous_excess(workspace, tax_id, branch, year, month)

    def build(stored):
        chosen = _vat_options(given, stored)
        boxes = pp30.boxes(selection["sales"], selection["purchases"], **chosen)
        summary = {
            "status": "drafted", "draft": name, "kind": VAT, "period": periods.text(year, month),
            "company": {"name": str(header.get("company") or "")[:200], "tax_id": tax_id, "branch": branch},
            "connection": connection, "account_set_key": account_set_key,
            "readback": {"ref": str(data.get("ref") or ""), "read_at": str(data.get("read_at") or "")},
            "boxes": {key: "" if value is None else str(value) for key, value in boxes.items()},
            "outcome": pp30.outcome(boxes), "options": chosen,
            "sales": {"rows": len(selection["sales"]), "amount": str(sales_amount), "vat": str(sales_vat)},
            "purchases": {"rows": len(selection["purchases"]), "amount": str(purchase_amount),
                          "vat": str(purchase_vat)},
            "claimable_if_fixed": {"amount": str(claim_amount), "vat": str(claim_vat)},
            "previous_excess": previous,
            "deadlines": {key: value.isoformat() if value else "" for key, value in due.items()},
            "documents_checked": checked, "issues_by_code": input_vat.counts(issues), "issues": issues,
            "unreadable_rows": unreadable,
            "sales_rows": [record.as_dict() for record in selection["sales"]],
            "purchase_rows": [record.as_dict() for record in selection["purchases"]],
            "excluded_rows": [{**record.as_dict(), "reason": reason} for record, reason in selection["excluded"]],
            "tax_total": str(boxes["11"] - boxes["12"]),  # 应缴为正、多缴为负
        }
        return summary, chosen, {"connection": connection[:140], "account_set": account_set_key[:500],
                                 "source_ref": str(data.get("ref") or "")[:140]}

    return _write(name, workspace, VAT, build)


# ---------------- ภ.ง.ด.3 / ภ.ง.ด.53 ----------------

def _edits(raw, overrides, known) -> tuple[dict, list]:
    """合并逐行改动：{"row": 记录名, ...改动} 或 {"row", "reset": true}；行不在本次取到的记录里、或改动不合法的不收，
    回 (新 overrides, 拒收的)。"""
    merged, rejected = {key: dict(value) for key, value in overrides.items()}, []
    for item in (raw or [])[:EDITS_MAX]:
        row = str((item or {}).get("row") or "").strip()[:140]
        if not row or row not in known:
            rejected.append({"row": row, "reason": "row_unknown"})
            continue
        if item.get("reset") is True:
            merged.pop(row, None)
            continue
        try:
            clean = wht.clean_edit(item)
        except ValueError:
            rejected.append({"row": row, "reason": "edit_invalid"})
            continue
        if clean:
            merged[row] = {**merged.get(row, {}), **clean}
    return merged, rejected


def _company_name(documents, tax_id) -> str:
    names = Counter(str(item["fields"].get("buyer_name") or "").strip() for item in documents
                    if wht.tax_id(item["fields"].get("buyer_tax")) == tax_id)
    names.pop("", None)
    return names.most_common(1)[0][0] if names else ""


def wht_draft(company_tax_id, period, edits=None, company_name="", company_branch="") -> dict:
    workspace = _workspace()
    scope.require_read()
    tax_id = wht.tax_id(company_tax_id)
    if len(tax_id) != 13:
        frappe.throw(_("tax.company_invalid"))
    year, month = _period(period)
    branch = str(company_branch or "").strip()[:10] or "00000"
    start, end = periods.bounds(year, month)
    name = record_id(workspace, WHT, tax_id, branch, periods.text(year, month))
    found, truncated = sources.wht_documents(workspace, tax_id, start, end)
    remembered = memory.choices(workspace)
    payees = [key.split(":", 1)[1] for key in remembered if key.startswith("payee:")]
    skipped = {}
    payments, notes = wht.collect(found, tax_id, payees, skipped)
    known = {ref for payment in payments for ref in payment["refs"]}
    due = periods.deadlines("pnd", year, month)

    def build(stored):
        overrides, rejected = _edits(edits, stored.get("overrides") or {}, known)
        result = wht.draft(payments, notes, year, month, remembered, overrides, found, skipped)
        shown_name = str(company_name or stored.get("company_name") or _company_name(found, tax_id))[:200]
        summary = {
            "status": "drafted", "draft": name, "kind": WHT, "period": periods.text(year, month),
            "company": {"name": shown_name, "tax_id": tax_id, "branch": branch}, **result,
            "deadlines": {key: value.isoformat() if value else "" for key, value in due.items()},
            "documents_read": len(found), "truncated": truncated, "edits_rejected": rejected,
            "tax_total": result["totals"]["tax"],
        }
        options = {"overrides": overrides,
                   "company_name": str(company_name or stored.get("company_name") or "")[:200]}
        return summary, options, {}

    return _write(name, workspace, WHT, build)


def answer(answers, draft="") -> dict:
    """记住所得类型（按收款方税号，或按分类）：[{payee_tax_id | category, income_type, income_text?, form?,
    condition?, payee_name?}]。之后重出草稿即套用。"""
    workspace = _workspace()
    items = list(answers or [])[:ANSWERS_MAX]
    if not items:
        frappe.throw(_("tax.answer_invalid"))
    saved = []
    for item in items:
        item = item or {}
        key = memory.scope_of(item.get("payee_tax_id"), item.get("category"))
        memory.remember(workspace, key, item.get("income_type"), item.get("income_text") or "",
                        item.get("form") or "", item.get("condition") or "", item.get("payee_name") or "", draft)
        saved.append(key)
    return {"status": "saved", "saved": saved}


def forget(payee_tax_id="", category="") -> dict:
    workspace = _workspace()
    key = memory.scope_of(payee_tax_id, category)
    return {"status": "removed" if memory.forget(workspace, key) else "not_found", "scope": key}


def remembered() -> list:
    return memory.listing(_workspace())


def drafts_of(tax_id, branch=None, limit=24) -> list:
    """一家公司（税号；给了分店号就只看这个分店）的申报草稿，期间新 → 旧（公司主档用，ADR-020）：
    `[{name, kind, period, branch, updated_at}]`。只读；权限同读草稿（`_workspace`），ภ.ง.ด. 草稿还要读识别记录的权限，
    没有就不列。"""
    workspace = _workspace()
    filters = {"workspace": workspace, "company_tax_id": str(tax_id or "").strip()}
    if not filters["company_tax_id"]:
        return []
    if branch is not None:
        filters["company_branch"] = str(branch or "")
    if not scope.allowed_any(scope.READ_FEATURES):
        filters["kind"] = VAT
    rows = frappe.get_all(DOCTYPE, filters=filters,
                          fields=["name", "kind", "period", "company_branch", "updated_at", "modified"],
                          order_by="period desc, kind asc", ignore_permissions=True,
                          limit_page_length=max(1, min(int(limit or 24), 100)))
    return [{"name": row.name, "kind": row.kind, "period": row.period, "branch": row.company_branch or "",
             "updated_at": str(row.updated_at or row.modified or "")} for row in rows]


def delete(drafts) -> dict:
    """删草稿（连同存着的选项与逐行改动）：[草稿名]；ภ.ง.ด. 草稿同读一样要读识别记录的权限。回删掉的与没找到的。"""
    workspace = _workspace()
    names = [str(name or "").strip()[:140] for name in list(drafts or [])[:DELETE_MAX]]
    names = [name for name in dict.fromkeys(names) if name]
    if not names:
        frappe.throw(_("tax.request_invalid"))
    deleted, missing = [], []
    for name in names:
        row = frappe.db.get_value(DOCTYPE, name, ["workspace", "kind"], as_dict=True, for_update=True)
        if not row:
            missing.append(name)
            continue
        if row.workspace != workspace:
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        if row.kind == WHT:
            scope.require_read()
        frappe.db.delete(DOCTYPE, {"name": name})
        deleted.append(name)
    return {"status": "deleted", "deleted": deleted, "missing": missing}


def workbook(summary, lang) -> tuple[str, bytes]:
    """(文件名, xlsx)：ภ.พ.30 草稿 + 销项 / 进项报告 + 核查；或 ภ.ง.ด.53 / ภ.ง.ด.3 附表 + 待核。"""
    from pearnly_cowork.tax import workbook as books

    stamp = frappe.utils.now_datetime().strftime("%Y%m%d-%H%M%S")
    company = (summary.get("company") or {}).get("tax_id") or "company"
    if summary["kind"] == VAT:
        return f"PP30-{summary['period']}-{company}-{stamp}.xlsx", books.vat_book(summary, lang)
    return f"PND3-PND53-{summary['period']}-{company}-{stamp}.xlsx", books.wht_book(summary, lang)
