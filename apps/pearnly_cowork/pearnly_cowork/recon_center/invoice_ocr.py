"""销项税报告核查的图片 / PDF 发票：用录入工作台同一个发票识别引擎读（用户 2026-09-26：“旧项目能用，这里就要能用”）。

旧线上（`vat_ocr_extract.py`）每份发票文件调一次模型、只取第一页的 8 个字段。这里改用录入的识别引擎
（`ocr.engine.run_invoice`）：每份文件一个持久识别任务（Pearnly OCR Work，kind `cowork.vat_check`），
与录入相同的冻结配置、限流、超时让出；服务商调用记在 Work 的检查点里，重跑不重复付费。
结果按页展开、按发票分组（一份 PDF 里有几张就是几张，比旧线上多认出同文件的后几张），映射成核查用的字段。

计费：识别出发票的文件按页扣（与录入同价、同档位），参考号固定为 Work 名，重试只补记账、不重复扣费；
认不出发票（整份都不是发票）不扣。发起的对账已放弃（失败 / 超时）时，还没识别的任务直接取消，不再付费。

结果存在 Work 自己的检查点里（`cowork.vat_check:result`），对账后台任务等全部 Work 结束后取回。
"""
import dataclasses
import hashlib
import time
from datetime import date

import frappe
from frappe import _
from pearnly_core import billing_client, ocr
from pearnly_core.execution import work as core_work
from pearnly_core.execution.errors import Deferred, Uncertain

from pearnly_cowork.files.service import inspect_content
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
from pearnly_cowork.ocr.execution import service as intake, settings as ocr_settings, work_guard
from pearnly_cowork.ocr.execution.access import WORK
from pearnly_cowork.ocr.grouping import group_invoices
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.pages import all_pages_not_invoice, assess_confidence, flatten_pipeline_pages
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.core import build_client
from pearnly_cowork.recon_center import admission

KIND = "cowork.vat_check"
RESULT = KIND + ":result"
BILLED = KIND + ":billed"
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
# 让出前最多跑的时间：发票读图在这里至少等 MODEL_TIMEOUT_SECONDS、等名额最多 60 秒、结果不明再重发一次，最坏
# 120 + 2 ×（60 + 120）= 480 秒，在识别任务 600 秒的硬上限内（原 200 秒在忙时被硬时限杀在调用中途，2026-09-30 压测第 18 轮）。
YIELD_SECONDS = 120
# 多页扫描发票读一页要 60 秒以上（2026-09-30 压测 V04：5 份 PDF 读超时、结果不明后整份丢了），与账单路径一样放宽。
MODEL_TIMEOUT_SECONDS = 120
# 发票可能上百张、按名额分批读：等得比账单久（对账后台任务的时限相应放宽，jobs.TIMEOUTS）。
WAIT_SECONDS = 50 * 60
POLL_SECONDS = 3


def _text(value):
    return "" if value is None else str(value).strip()


def _money(value):
    try:
        return round(float(str(value).replace(",", "")), 2) if _text(value) else None
    except ValueError:
        return None


def _tax(value):
    """泰国税号固定 13 位：读出 12 位数字的是开头的 0 丢了，补回（与税报表格同一规则；2026-09-30 压测第 17 轮 V03：
    「105541034263 ≈ 0105541034263」只算疑似匹配）。"""
    text = _text(value)
    digits = "".join(char for char in text if char.isdigit())
    return "0" + digits if len(digits) == 12 and len(text) == len(digits) else text


def record(fields, filename):
    """识别出的一张发票 → 核查用的发票记录（旧线上 `_extract_invoice_via_pipeline` 的同一套映射）。"""
    return {"filename": filename, "buyer_tax_id": _tax(fields.get("buyer_tax")),
            "buyer_name": _text(fields.get("buyer_name")), "buyer_branch": _text(fields.get("buyer_branch")),
            "invoice_no": _text(fields.get("invoice_number")), "invoice_date": _text(fields.get("date")),
            "period": "", "amount_pre_vat": _money(fields.get("subtotal")),
            "vat_amount": _money(fields.get("vat")), "total_amount": _money(fields.get("total_amount"))}


def records(pages, filename):
    """引擎结果的各页 → 发票记录；整份都不是发票就是空。"""
    raw = flatten_pipeline_pages(pages)
    if not raw or all_pages_not_invoice(raw):
        return []
    return [record(group["invoice_fields"], filename) for group in group_invoices(assess_confidence(raw)["pages"])]


# ── 发起：每份文件一个持久识别任务 ─────────────────────────────────────────


def submit(recon_job, files, user):
    """files: [(File 名, 文件名, 内容)]。先冻结识别配置、查余额，再逐份建 Work；返回 ([(Work 名, 文件名)], 读不了的文件名)。

    一份文件读不了（加密、超 50 页、坏图片、不支持的类型）只把它记成读不出，其余照常识别
    （原来一份坏文件整批失败 —— 2026-09-28 录制读代码发现）。"""
    frozen = ocr_settings.snapshot()
    inspected, rejected = [], []
    for file_name, filename, content in files:
        try:
            info = inspect_content(filename, content)
        except frappe.ValidationError:
            frappe.clear_last_message()
            rejected.append(filename)
            continue
        if info["kind"] not in ("pdf", "image"):
            rejected.append(filename)
            continue
        inspected.append((file_name, filename, info))
    if not inspected:
        return [], rejected
    intake.balance(sum(info["page_count"] for _f, _n, info in inspected), frozen["billing_tier"])
    works = []
    for index, (file_name, filename, info) in enumerate(inspected):
        arguments = {"recon_job": recon_job, "file": file_name, "filename": filename, "pages": info["page_count"],
                     "sha256": info["sha256"], "settings": frozen}
        works.append((admission.when_room(lambda index=index, arguments=arguments: _create(
            f"{recon_job}:{index}", arguments, user)), filename))
    return works, rejected


def _create(reference, arguments, user):
    with work_guard.work_creation():
        return core_work.submit(KIND, reference, arguments, user)


def wait(works, on_stage=None):
    """等全部 Work 结束，取回每份的发票。返回 (发票记录, 没读出的文件名)。

    余额不足时 Work 会一直顺延：所有没结束的都卡在余额上就直接报余额不足（这些 Work 随后自行取消）。
    """
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        frappe.db.commit()  # 结束读快照，读到 Work 的最新状态
        rows = {row.name: row for row in frappe.get_all(WORK, filters={"name": ["in", [name for name, _f in works]]},
                                                         fields=["name", "status", "last_code", "checkpoints"])}
        open_rows = [rows.get(name) for name, _f in works if not rows.get(name) or rows[name].status not in TERMINAL]
        if not open_rows:
            break
        if all(row and row.status == "deferred" and row.last_code == "insufficient_balance" for row in open_rows):
            frappe.throw(_("vat_check.balance_insufficient"))
        if time.monotonic() > deadline:
            frappe.throw(_("vat_check.recognition_timeout"))
        time.sleep(POLL_SECONDS)
    invoices, failed = [], []
    for name, filename in works:
        row = rows[name]
        saved = frappe.parse_json(row.checkpoints or "{}").get(RESULT) if row.status == "succeeded" else None
        found = (saved or {}).get("body", {}).get("invoices") if saved else None
        if found:
            invoices.extend(found)
        else:
            failed.append(filename)
    return invoices, failed


# ── Work 里跑：识别 → 存结果 → 扣费 ─────────────────────────────────────────


def _recognize(content, filename, pages, frozen):
    environment = frozen["environment"]
    selected = invoice_policy(environment, frozen["policy"])
    config = config_from_environment(environment, selected)
    client = build_client(environment, core_identity={key: frozen["target"][key] for key in
                                                      ("backend", "project", "location", "credentials_path")})
    started = time.monotonic()

    def guarded(callback):
        def invoke(*args, **kwargs):
            # 每次付费调用前结束读快照；超过时限就让出，重跑时已完成的调用从检查点取回。
            frappe.db.commit()
            if time.monotonic() - started > YIELD_SECONDS:
                raise Deferred("progress_yield", 1)
            return callback(*args, **kwargs)
        return invoke

    def page_failure(call):
        # 重发后仍结果不明的那一页交给引擎的按页兜底（明确失败），不让整份作废：25 页的扫描件一页不明，25 张发票
        # 全丢了（2026-09-30 压测第 17 轮 V03）。已发出的那次调用续跑时从检查点认出、同样处理，不重付。
        def invoke(*args, **kwargs):
            try:
                return call(*args, **kwargs)
            except Uncertain as error:
                raise ProviderFailure("ocr.provider_outcome_unknown") from error
        return invoke

    @page_failure
    def model_call(request):
        # 结果不明（读超时、连接中断）重发一次、等得更久：与账单路径同一口径（document_ocr）；发票录入照旧。
        return client.model_call(dataclasses.replace(request, timeout_s=max(request.timeout_s, MODEL_TIMEOUT_SECONDS)),
                                 retry_unknown=True)

    anchor = date.fromisoformat(frozen["anchor_date"])
    with PageSource(content, filename, expected_page_count=int(pages), enable_text_path=selected.text_fast_path,
                    image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")), orient=ocr.orient) as source:
        result = run_invoice(source, guarded(model_call), guarded(page_failure(client.vision_call)), config=config,
                             anchor_year=anchor.year, today=anchor)
    return records(result.pipeline.pages, filename)


def _charge(work_name, pages, frozen):
    try:
        result = billing_client.charge("cowork", "pdf", int(pages), tier=frozen["billing_tier"],
            reference=KIND + ":" + work_name, description="Cowork VAT check invoice recognition")
    except Exception:
        result = {"ok": False}  # 结果不明也按“待记账”：参考号固定，重试安全
    if not (isinstance(result, dict) and result.get("ok") is True):
        raise Deferred("billing_pending", 60)


def run(recon_job, file, filename, pages, sha256, settings):
    name = frappe.flags.get("pearnly_ocr_work")
    current = frappe.db.get_value(WORK, name, ["kind", "user", "status"], as_dict=True) if name else None
    if not current or current.kind != KIND or current.status != "running":
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    frozen = settings
    previous = frappe.session.user
    frappe.set_user(current.user)
    try:
        saved = core_work.checkpoint(RESULT)
        if not saved:
            from pearnly_cowork.recon_center import document_ocr

            if document_ocr._recon_closed(recon_job, file):
                return {"cancelled": "recon_job_closed"}
            from pearnly_cowork.recon_center import storage

            _stored_name, content = storage.read(file)
            if hashlib.sha256(content).hexdigest() != sha256:
                return {"failed": "source_changed"}
            # 识别连接在发起后变了（换了服务商 / 项目）就不跑，与录入同一个判据。
            ocr_settings.revalidate(frozen)
            intake.balance(int(pages), frozen["billing_tier"])
            # 用上传时的原文件名（核查表“原文件”一栏显示它；存成临时 File 时名字可能被加了后缀）。
            invoices = _recognize(content, filename, pages, frozen)
            saved = core_work.checkpoint(RESULT, "done", {"invoices": invoices})
        if saved["body"]["invoices"] and not core_work.checkpoint(BILLED):
            # 扣费前再看一次核查：已经结束（失败或完成）就用不到这份结果，不扣客户的钱（与账单路径 document_ocr 同一
            # 口径；2026-09-30 压测 V01：核查因识别繁忙失败后，读完的小票照样扣费）。
            from pearnly_cowork.recon_center import document_ocr

            if document_ocr._recon_closed(recon_job, file):
                return {"cancelled": "recon_job_closed"}
            _charge(name, pages, frozen)
            core_work.checkpoint(BILLED, "done", {"units": int(pages)})
        return {}
    finally:
        frappe.set_user(previous)
