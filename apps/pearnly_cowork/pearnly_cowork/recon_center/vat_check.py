"""销项税报告核查的后台任务（旧线上 `recon_jobs/handlers.py::run_salesvat` 的前半段）。

1. 报告：逐份解析（Excel / CSV / 带文字层的 PDF，同收入对账的税表读取），合并时校验同卖方、同期间；
2. 发票：Excel / CSV 格式按行直读（旧线上“结构化发票免识别”）；图片 / PDF 要用录入工作台同一个发票识别引擎读、
   按页计费（用户 2026-09-26：“复用录入的发票识别”）——引擎只能在持久识别任务里跑，这一段待接，现在明确拒收；
3. 两边交给 `reconciliation.vat_check_service.create` 生成核查工作簿并存档。
"""
import frappe
from frappe import _

from pearnly_cowork.recon_center import sources
from pearnly_cowork.reconciliation import vat_check_core, vat_check_service, vat_parse

STRUCTURED = sources.EXCEL + sources.CSV


def _report(content, filename):
    parsed = vat_parse.decode(sources.report_source(content, filename))
    if not parsed or not parsed.get("ok") or not parsed.get("rows"):
        frappe.throw(_("reconciliation.vat_report_unparsed"))
    return parsed


def run(workspace, title, request_id, reports, invoices, lang, on_stage=None):
    stage = on_stage or (lambda _name: None)
    stage("report")
    merged = vat_check_core.merge_parsed_reports([_report(content, name) for name, content in reports],
                                                 [name for name, _content in reports])
    if not merged.get("ok"):
        frappe.throw(_(merged["error_code"]).format(*merged.get("error_args", [])))
    stage("invoice")
    records, failed = [], []
    scanned = [(name, content) for name, content in invoices if sources.extension(name) not in STRUCTURED]
    for name, content in invoices:
        if sources.extension(name) in STRUCTURED:
            parsed = vat_parse.decode(sources.report_source(content, name))
            rows = (parsed or {}).get("rows") or []
            if not rows:
                failed.append(name)
            records.extend(vat_check_core.report_row_to_invoice(row, name) for row in rows)
    if scanned:
        # 图片 / PDF 发票要走识别引擎的持久任务（Pearnly OCR Work，防重复计费与限流），
        # 这一段还在接（见任务记录第九批）；先明确拒收，不悄悄当成“没有发票”。
        frappe.throw(_("vat_check.scanned_not_ready"))
    if not records:
        frappe.throw(_("vat_check.no_invoices"))
    stage("matching")
    return vat_check_service.create(workspace, title, request_id, {**merged, "invoices": records,
        "report_rows": merged["rows"], "failed_files": failed}, lang)
