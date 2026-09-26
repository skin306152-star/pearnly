"""对账结果页 = 导出的复核表（用户 2026-09-26）：取表、改后重算、保存。

页面上显示的每一页都由**导出同一个函数**在内存里生成的工作簿读出来，所以页面与导出的 Excel
逐格一致；可改的只有两边的输入（银行：对账单明细、总账明细；收入：差异明细的税表一侧与总账金额），
改了就用建任务时同一套比对重算。保存整份替换结果，并用任务的修改时间防止两个人同时改。
"""
import io
import json

import frappe
from frappe import _

from pearnly_cowork.reconciliation import income_recon_service, revision

# 由输入生成、页面只读显示的那几页（按导出工作簿里的顺序取）。
READ_ONLY_SHEETS = {"bank": (0, 1), "income": (1, 2)}


def _lang(lang):
    lang = str(lang or frappe.local.lang or "th")[:2]
    return lang if lang in ("th", "en", "zh", "ja") else "th"


def _cells(content, indexes):
    from openpyxl import load_workbook

    book = load_workbook(io.BytesIO(content))
    out = []
    for index in indexes:
        sheet = book.worksheets[index]
        rows = [[cell for cell in row] for row in sheet.iter_rows(values_only=True)]
        out.append({"name": sheet.title, "rows": [row for row in rows if any(v not in (None, "") for v in row)]})
    return out


def _view(kind, doc, inputs, lang):
    if kind == "bank":
        _rows, _header, results, summary = revision.bank_compute(doc, inputs)
        content = revision.bank_workbook_bytes(doc, results, summary, lang)
        matched = sum(1 for r in results if r.match_status == "matched" and (r.match_layer or 1) == 1)
        pending = sum(1 for r in results if r.match_status == "matched" and (r.match_layer or 1) > 1)
        counts = {"total": len(results), "matched": matched, "pending": pending,
                  "unmatched": len(results) - matched - pending}
    else:
        _rows, results, summary = revision.income_compute(doc, inputs)
        content = revision.income_workbook_bytes(results, summary, lang)
        matched, findings, unmatched = income_recon_service.result_counts(results)
        counts = {"total": len(results), "matched": matched, "pending": findings, "unmatched": unmatched}
        # 差异明细里每行的差额与状态跟着重算走（页面那张可改的表要显示它们）。
        for row, result in zip(inputs.get("vat") or [], results):
            row["diff"] = result.diff
            row["gl_amount"] = result.gl_amount if row.get("has_gl") else row.get("gl_amount")
    return {"sheets": _cells(content, READ_ONLY_SHEETS[kind]), "counts": counts, "inputs": inputs}


def _parse(inputs):
    value = frappe.parse_json(inputs) if isinstance(inputs, str) else inputs
    if not isinstance(value, dict):
        frappe.throw(_("reconciliation.request_invalid"))
    return value


@frappe.whitelist()
def load(kind, task_id, lang=None):
    doc = revision.task(kind, task_id)
    inputs = revision.bank_inputs(doc) if kind == "bank" else revision.income_inputs(doc)
    return {"task": {"name": doc.name, "title": doc.title, "status": doc.status, "modified": str(doc.modified),
                     "revision": revision.revision(doc)}, **_view(kind, doc, inputs, _lang(lang))}


@frappe.whitelist(methods=["POST"])
def recalc(kind, task_id, inputs, lang=None):
    doc = revision.task(kind, task_id)
    return _view(kind, doc, _parse(inputs), _lang(lang))


@frappe.whitelist(methods=["POST"])
def save(kind, task_id, inputs, modified, lang=None):
    doc = revision.task(kind, task_id, for_update=True)
    if str(doc.modified) != str(modified):
        frappe.throw(_("reconciliation.edit_conflict"))
    inputs = _parse(inputs)
    if kind == "bank":
        revision.bank_save(doc, inputs)
    else:
        revision.income_save(doc, inputs)
    frappe.db.commit()
    return load(kind, task_id, lang)
