"""销项税报告核查的后台任务（旧线上 `recon_jobs/handlers.py::run_salesvat` 的前半段）。

1. 报告：逐份解析（Excel / CSV / 带文字层的 PDF，同收入对账的税表读取），合并时校验同卖方、同期间；
2. 发票：Excel / CSV 格式按行直读（旧线上“结构化发票免识别”）；图片 / PDF 用录入工作台同一个发票识别引擎读、
   按页计费（用户 2026-09-26：“复用录入的发票识别”），见 `invoice_ocr`；读不出的文件列在结果里；
3. 两边交给 `reconciliation.vat_check_service.create` 生成核查工作簿并存档。
"""
import frappe
from frappe import _
from pearnly_core.execution.errors import Deferred, QueueFull

from pearnly_cowork.recon_center import sources
from pearnly_cowork.reconciliation import vat_check_core, vat_check_service, vat_parse

STRUCTURED = sources.EXCEL + sources.CSV


def _report(content, filename):
    parsed = vat_parse.decode(sources.report_source(content, filename))
    if not parsed or not parsed.get("ok") or not parsed.get("rows"):
        frappe.throw(_("reconciliation.vat_report_unparsed"))
    return parsed


def run(workspace, title, request_id, reports, invoices, lang, on_stage=None, invoice_files=None, recon_job=None):
    stage = on_stage or (lambda _name: None)
    stage("report")
    merged = vat_check_core.merge_parsed_reports([_report(content, name) for name, content in reports],
                                                 [name for name, _content in reports])
    if not merged.get("ok"):
        frappe.throw(_(merged["error_code"]).format(*merged.get("error_args", [])))
    stage("invoice")
    records, failed, scanned = [], [], []
    for index, (name, content) in enumerate(invoices):
        if sources.extension(name) in STRUCTURED:
            parsed = vat_parse.decode(sources.report_source(content, name))
            rows = (parsed or {}).get("rows") or []
            if not rows:
                failed.append(name)
            records.extend(vat_check_core.report_row_to_invoice(row, name) for row in rows)
        else:
            scanned.append(((invoice_files or [])[index], name, content))
    if scanned:
        from pearnly_cowork.recon_center import invoice_ocr

        try:
            works, rejected = invoice_ocr.submit(recon_job or request_id, scanned, frappe.session.user)
        except (Deferred, QueueFull) as error:
            # 查不到余额（计费服务一时连不上）要说清楚，不当成识别繁忙（2026-09-28 录制读代码发现）。
            reason = getattr(error, "reason", "")
            frappe.throw(_("vat_check.balance_insufficient") if reason == "insufficient_balance"
                         else _("vat_check.balance_unavailable") if reason == "lookup_error"
                         else _("vat_check.recognition_busy"))
        found, unread = invoice_ocr.wait(works) if works else ([], [])
        records.extend(found)
        failed.extend(rejected + unread)
    if not records:
        frappe.throw(_("vat_check.no_invoices"))
    stage("matching")
    return vat_check_service.create(workspace, title, request_id, {**merged, "invoices": records,
        "report_rows": merged["rows"], "failed_files": failed}, lang)
