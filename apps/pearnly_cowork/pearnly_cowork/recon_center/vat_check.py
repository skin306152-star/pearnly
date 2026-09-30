"""销项税报告核查的后台任务（旧线上 `recon_jobs/handlers.py::run_salesvat` 的前半段）。

1. 报告：逐份解析（Excel / CSV / PDF / 照片，同收入对账的税表读取；扫描件与照片走 `document_ocr`），合并时校验同卖方、同期间；
2. 发票：Excel / CSV 格式按行直读（旧线上“结构化发票免识别”）；图片 / PDF 用录入工作台同一个发票识别引擎读、
   按页计费（用户 2026-09-26：“复用录入的发票识别”），见 `invoice_ocr`；读不出的文件列在结果里；
3. 两边交给 `reconciliation.vat_check_service.create` 生成核查工作簿并存档。
"""
import frappe
from frappe import _
from pearnly_core.execution.errors import Deferred, QueueFull

from pearnly_cowork.ocr.enterprise import self_check
from pearnly_cowork.recon_center import sources
from pearnly_cowork.reconciliation import vat_check_core, vat_check_service, vat_parse

STRUCTURED = sources.EXCEL + sources.CSV


def _report(content, filename, read=None):
    source = sources.report_source(content, filename, read)
    parsed = vat_parse.decode(source)
    if not parsed or not parsed.get("ok") or not parsed.get("rows"):
        frappe.throw(_("reconciliation.vat_report_unparsed"))
    return {**parsed, "pages": sources.report_pages(source)}


def unverified_note(named_pages):
    """识别读出的税报里行合计对不上页合计、或没读到页合计的页（逐页自核，自核没过的已自动再读过一次）：
    点名文件和页，请对照原件核对（压测窗口 2026-09-30：照片税报一页 8 行金额错位，没读到页合计，没法自核）。
    ``named_pages``：``[(文件名, [(页号, document), …]), …]``，按上传顺序。"""
    pages = self_check.unverified_report_pages(named_pages)
    if not pages:
        return ""
    return _("reconciliation.report_pages_unverified").format(
        "；".join(f"{name}: {'、'.join(str(number) for number in numbers)}" for name, numbers in pages))


def photo_pages(named_pages):
    """识别读出的税报每页有哪些单号：``[[页的名字, [单号…]], …]``。收入结果页据此提示「同一页好几行和总账对不上，
    多半是照片读错」（压测窗口 2026-09-30：一页 8 行税基税额整体下移一行）。"""
    out = []
    for name, pages in named_pages:
        for number, document in pages:
            refs = [str(entry.get("invoice_no") or "").strip() for entry in document.get("entries") or []]
            refs = [ref for ref in refs if ref]
            if refs:
                out.append([name if len(pages) == 1 else f"{name} p.{number}", refs])
    return out


def report_reading(named_pages):
    """识别读出的税报的两样附带信息：``(提示列表, 每页单号)``；表格 / 文字层 PDF 没有页，两样都空。"""
    note = unverified_note(named_pages)
    return ([note] if note else []), photo_pages(named_pages)


def merged_report_rows(recon_job, reports, report_files, pages=None):
    """收入对账的税报分几份（一页一张照片、分段的 PDF；用户 2026-09-30 定可多份）：逐份读、按上传顺序合并成一份。

    照片先全部提交识别再按顺序取结果（与银行存折照片同一做法）；一份读不出不整单失败、结果里点名；几份里重复的同一行
    （单号 / 日期 / 未税 / 税额都相同，重复拍的页）只留第一份的；卖方或期间不一致照旧报错（与销项核查同一口径）。
    返回 ``(income 存储要的税报行, 提示, 税报表头的本公司税号)``；给了 ``pages``（列表）就把识别读出的每页单号追加进去。"""
    from pearnly_cowork.recon_center import document_ocr
    from pearnly_cowork.reconciliation.normalization import normalize_invoice_no

    user = frappe.session.user
    for index, (name, content) in enumerate(reports):
        if sources.extension(name) in sources.IMAGES:
            document_ocr.submit(recon_job, f"right{index}", report_files[index], name, content, "vat_report", user)
    frappe.db.commit()
    parsed, names, unread = [], [], []
    for index, (name, content) in enumerate(reports):
        try:
            parsed.append(_report(content, name, document_ocr.reader(recon_job, f"right{index}", report_files[index],
                                                                   user)))
            names.append(name)
        except frappe.ValidationError:
            frappe.clear_last_message()
            unread.append(name)
    if not parsed:
        frappe.throw(_("reconciliation.vat_report_unparsed"))
    merged = vat_check_core.merge_parsed_reports(parsed, names)
    if not merged.get("ok"):
        frappe.throw(_(merged["error_code"]).format(*merged.get("error_args", [])))
    seen, rows, repeated = {}, [], 0
    for row in merged["rows"]:
        key = (normalize_invoice_no(row.get("report_invoice_no") or row.get("report_ref_no") or ""),
               str(row.get("report_date") or ""), round(float(row.get("report_amount_pre_vat") or 0), 2),
               round(float(row.get("report_vat_amount") or 0), 2))
        if key in seen and seen[key] != row.get("source_filename"):
            repeated += 1
            continue
        seen.setdefault(key, row.get("source_filename"))
        rows.append(row)
    warnings = []
    if unread:
        warnings.append(_("reconciliation.files_unread").format("、".join(unread)))
    if repeated:
        warnings.append(_("reconciliation.report_rows_repeated").format(repeated))
    notes, by_page = report_reading([(name, item.get("pages") or []) for name, item in zip(names, parsed)])
    warnings += notes
    if pages is not None:
        pages.extend(by_page)
    tax_id = next((item.get("issuer_hint") for item in parsed if item.get("issuer_hint")), "")
    return vat_parse.decoded_rows(rows), warnings, tax_id


def swapped_dates(records, report_rows):
    """发票日期不在税报的月份里、日和月对调后正好在：是 dd/mm 读反了（Big C 发票「07/01」读成 1 月 7 日；2026-09-30
    压测第 17 轮 V03 出现「差 +175 天 / 01/2026 ≠ 07/2026」），按对调后的日期比。"""
    from datetime import date as date_type

    from pearnly_cowork.reconciliation.normalization import parse_date

    def day(value):
        try:
            return parse_date(value)
        except Exception:
            return None

    months = {(value.year, value.month) for value in map(day, (row.get("report_date") for row in report_rows)) if value}
    if not months:
        return records
    fixed = []
    for record in records:
        value = day(record.get("invoice_date"))
        if value and (value.year, value.month) not in months and value.day <= 12 and (value.year, value.day) in months:
            try:
                record = {**record, "invoice_date": date_type(value.year, value.day, value.month).isoformat()}
            except ValueError:
                pass
        fixed.append(record)
    return fixed


def run(workspace, title, request_id, reports, invoices, lang, on_stage=None, invoice_files=None, recon_job=None,
        report_files=None):
    stage = on_stage or (lambda _name: None)
    stage("report")
    readers = [None] * len(reports)
    if recon_job and report_files:
        from pearnly_cowork.recon_center import document_ocr

        readers = [document_ocr.reader(recon_job, f"report{index}", report_files[index], frappe.session.user)
                   for index in range(len(reports))]
        # 税报照片先全部提交识别、识别进程并行读，再按顺序取结果（与银行对账同一做法）：原来一份读完才提交下一份，
        # 21 张照片要十几分钟（2026-09-30 压测第 17 轮 V01）。同一参考号、同样参数的提交返回同一个识别任务，读取层
        # 再提交不会重复付费；PDF 不预先提交（带文字层的先走免费解析）。
        for index, (name, content) in enumerate(reports):
            if sources.extension(name) in sources.IMAGES:
                document_ocr.submit(recon_job, f"report{index}", report_files[index], name, content, "vat_report",
                                    frappe.session.user)
        frappe.db.commit()
    # 几份税报里一份读不出（照片识别超时）：点名跳过，其余照常核对（与收入对账同一做法；2026-09-30 压测 rH V01
    # IMG_2543 超时，原来整份报「读不出税表」）。一份都读不出才报错。
    parsed_reports, report_names, unread_reports = [], [], []
    for index, (name, content) in enumerate(reports):
        try:
            parsed_reports.append(_report(content, name, readers[index]))
            report_names.append(name)
        except frappe.ValidationError:
            if len(reports) == 1:
                raise
            frappe.clear_last_message()
            unread_reports.append(name)
    if not parsed_reports:
        frappe.throw(_("reconciliation.vat_report_unparsed"))
    merged = vat_check_core.merge_parsed_reports(parsed_reports, report_names)
    if not merged.get("ok"):
        frappe.throw(_(merged["error_code"]).format(*merged.get("error_args", [])))
    if not merged.get("seller_tax_id"):
        # 税报没带卖方信息时取表头印的本公司税号（结果页据此提示进项发票放错口；2026-09-30）。
        merged["seller_tax_id"] = next((item.get("issuer_hint") for item in parsed_reports if item.get("issuer_hint")), "")
    stage("invoice")
    records, failed, scanned, stopped = [], [], [], 0
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
        found, unread, stopped = invoice_ocr.wait(works) if works else ([], [], 0)
        records.extend(found)
        failed.extend(rejected + unread)
    if not records:
        frappe.throw(_("vat_check.no_invoices"))
    records = swapped_dates(records, merged["rows"])
    stage("matching")
    notes, _by_page = report_reading([(name, item.get("pages") or [])
                                      for name, item in zip(report_names, parsed_reports)])
    if unread_reports:
        notes.insert(0, _("reconciliation.files_unread").format("、".join(unread_reports)))
    if scanned and stopped * 2 > len(scanned):
        # 读到时限还没读完的发票过半（网络差、文件大）：差异大多是「报告有 · 发票无」，不能当正常结果看，放在最前面说清楚
        # （2026-09-30 压测 rQ）。已读出的照常核对、没读完的照常点名。
        notes.insert(0, _("vat_check.mostly_unread").format(stopped, len(scanned)))
    return vat_check_service.create(workspace, title, request_id, {**merged, "invoices": records,
        "report_rows": merged["rows"], "failed_files": failed, "report_notes": notes}, lang)
