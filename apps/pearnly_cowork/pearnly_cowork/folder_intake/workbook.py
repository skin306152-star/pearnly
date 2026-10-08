"""每张发票一份结果表格：就是网页「导出」的那份复核表（同一模板、同一组推送事实），只是一张一份。

不改复核表的任何一列（`roundtrip/*` 零改动，回导照旧认得）；取数照 `roundtrip.service.export` 的同一组公开入口，
只多一件事：没进推送账本就停下的票（例如要人选一次），把票上记的原因按「待人工」写进状态列，核对的人一眼看到。
以规则负责人的身份生成（调用方切好身份），读不到的票不出表。
"""
import json

import frappe
from frappe import _

from pearnly_cowork.folder_intake import contracts
from pearnly_cowork.history import storage
from pearnly_cowork.push import export_facts
from pearnly_cowork.roundtrip.export import ExportError, build_review_workbook
from pearnly_cowork.roundtrip.service import merge_pages

HISTORY = "Cowork OCR History"
FINISHED = ("done", "not_invoice", "failed")


def _filename(invoice_no, seller_tax, fallback):
    """结果表格文件名；没有发票号时的前缀按当前语言（取表格时已切到规则负责人的语言）取词条。"""
    key = "cowork.folder.no_number_prefix"
    prefix = _(key)
    return contracts.workbook_name(invoice_no, seller_tax, fallback, "" if prefix == key else prefix)


def status(entry):
    """给小助手的在途文件状态：到哪一步、失败原因，以及已经可以取表格的发票（带版本，变了重取）。"""
    items = []
    if entry.state == "done":
        for history in json.loads(entry.histories_json or "[]"):
            row = frappe.db.get_value(HISTORY, history, ["invoice_no", "seller_tax"], as_dict=True)
            if row:
                items.append({"history": history, "revision": int(entry.workbook_revision or 0),
                              "filename": _filename(row.invoice_no, row.seller_tax, entry.filename)})
    # settled：这份文件里的票都已推送成功、而且表格的当前版本已经是「全部成功」那一版 —— 小助手可以不再来问。
    # 只看票的实时状态不够：人在网页推成功后，表格要等下一次后台推进才升版本；小助手若在这之间就停止询问，
    # 文件夹里会永远留着旧表格（10-07 测试机实测踩到）。所以以随表格版本一起记下的那份状态为准。
    recorded = json.loads(entry.outcome_json or "{}").get("_signature") or []
    return {"state": entry.state, "code": entry.last_code or "", "items": items,
            "finished": entry.state in FINISHED,
            "settled": entry.state in ("not_invoice", "failed") or (
                entry.state == "done" and bool(recorded) and all(status == "success" for _name, status, _reason in recorded))}


def render(entry, history):
    if entry.state != "done" or history not in json.loads(entry.histories_json or "[]"):
        frappe.throw(_("cowork.folder.workbook_not_ready"))
    doc = storage.read(history)
    filename = _filename(doc.invoice_no, doc.seller_tax, entry.filename)
    try:
        content = _review(doc)
    except ExportError as error:
        # 金额等不能按复核表口径写出的票：给一份只有说明的表（表名、表头都不是复核表的，回导不会认错），
        # 核对的人照样知道这张票在、去网页看。
        content = _notice(entry.filename, doc, error.code)
    return filename, content, int(entry.workbook_revision or 0)


def _notice(source_name, doc, code):
    from io import BytesIO

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "Pearnly"
    for row in ((_("cowork.folder.wb.file"), source_name), (_("cowork.folder.wb.invoice"), doc.invoice_no or ""),
                (_("cowork.folder.wb.reason"), _(code))):
        sheet.append(list(row))
    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = 60
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _review(doc):
    canonical = bool(doc.source_file)
    if canonical:
        original = frappe.get_doc("Cowork Source File", doc.source_file)
        if not original.binding_complete or doc.name not in storage.parse(original.binding_records or "[]"):
            raise ExportError("roundtrip.export_multipage_unverified")
    fields = merge_pages(storage.parse(doc.pages), canonical=canonical)
    push_facts = {}
    if doc.last_push_status:
        push_facts[doc.name] = {"state": {"status": doc.last_push_status}}
    elif doc.last_push_reason:
        push_facts[doc.name] = {"state": {"status": "manual", "reason": export_facts.reason_text(doc.last_push_reason)}}
    for name, facts in export_facts.for_histories([doc]).items():
        push_facts.setdefault(name, {}).update(facts)
    # 取表格时已切到规则负责人的语言（machine._as），表格按这个人的语言出
    return build_review_workbook([{"history_id": doc.name, "merged_fields": fields}], push_facts=push_facts,
                                 lang=getattr(frappe.local, "lang", None) or "th")
