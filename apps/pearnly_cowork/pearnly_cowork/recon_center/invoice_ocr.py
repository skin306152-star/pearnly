"""销项税报告核查的图片 / PDF 发票：用录入工作台同一个发票识别引擎读（用户 2026-09-26：“旧项目能用，这里就要能用”）。

旧线上（`vat_ocr_extract.py`）每份发票文件调一次模型、只取第一页的 8 个字段。这里改用录入的识别引擎
（`ocr.engine.run_invoice`）：每份文件一个持久识别任务（Pearnly OCR Work，kind `cowork.vat_check`），
与录入相同的冻结配置、限流、超时让出；服务商调用记在 Work 的检查点里，重跑不重复付费。
结果按页展开、按发票分组（一份 PDF 里有几张就是几张，比旧线上多认出同文件的后几张），映射成核查用的字段。

计费：识别出发票的文件按页扣（与录入同价、同档位），参考号固定为 Work 名，重试只补记账、不重复扣费；
认不出发票（整份都不是发票）不扣。发起的对账已放弃（失败 / 超时）时，还没识别的任务直接取消，不再付费。

结果存在 Work 自己的检查点里（`cowork.vat_check:result`），对账后台任务等全部 Work 结束后取回。
"""
import hashlib
import time
from datetime import date

import frappe
from frappe import _
from pearnly_core import billing_client, ocr
from pearnly_core.execution import pages as core_pages, work as core_work
from pearnly_core.execution.errors import Deferred
from pearnly_core.tax_documents.amounts import repaired_tax_id

from pearnly_cowork.files.service import inspect_content
from pearnly_cowork.recon_center import progress
from pearnly_cowork.ocr.engine import run_invoice
from pearnly_cowork.ocr.engine.config import config_from_environment
from pearnly_cowork.ocr.engine.contracts import ProviderFailure
from pearnly_cowork.ocr.engine.errors import EngineError
from pearnly_cowork.ocr.execution import service as intake, settings as ocr_settings, work_guard
from pearnly_cowork.ocr.execution.access import WORK
from pearnly_cowork.ocr.grouping import group_invoices
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.pages import all_pages_not_invoice, assess_confidence, flatten_pipeline_pages
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.core import build_client
from pearnly_cowork.recon_center import admission, waiting
from pearnly_cowork.ocr import recon_reading
from pearnly_cowork.ocr.remote import forward
from pearnly_cowork.ocr.schema import PipelineResult

KIND = "cowork.vat_check"
RESULT = KIND + ":result"
BILLED = KIND + ":billed"
BILLING_SENT = KIND + ":billing_sent"
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
# 让出前最多跑的时间：发票读图在这里至少等 MODEL_TIMEOUT_SECONDS、等名额最多 60 秒、结果不明再重发一次，最坏
# 120 + 2 ×（60 + 120）= 480 秒，在识别任务 600 秒的硬上限内（原 200 秒在忙时被硬时限杀在调用中途，2026-09-30 压测第 18 轮）。
YIELD_SECONDS = recon_reading.VAT_YIELD_SECONDS
# 多页扫描发票读一页要 60 秒以上（2026-09-30 压测 V04：5 份 PDF 读超时、结果不明后整份丢了），与账单路径一样放宽。
MODEL_TIMEOUT_SECONDS = recon_reading.VAT_MODEL_TIMEOUT_SECONDS
# 等发票的时限（2026-09-30 压测 rO：V02、V03 税报 PDF 等满 50 分钟整份判「发票识别超时」，排队的时间也算了进去）。
# 排队不算读取时限；开始读了以后只按总时限判，读满了就用已经读出的发票核对，没读完的文件点名请重新上传。
# 不按「几分钟没进展」放弃（rQ 2026-09-30：网络差时一次调用结果不明要等租约或退避十来分钟，5 分钟没进展是常态；
# V02 五份大文件都读了大半、付了费，被这条规则取消，核查只剩 4 张发票）。27 页的发票 PDF 网络差时要 60–90 分钟。
QUEUE_LIMIT = 90 * 60           # 一份都还没开始读：排队最多等这么久
READ_LIMIT = 150 * 60           # 从开始读起最多等这么久（排队 + 读取 + 前后步骤在销项核查后台任务 5 小时内，jobs.TIMEOUTS）
PROGRESS_CHECK_SECONDS = 60


def _text(value):
    return "" if value is None else str(value).strip()


def _money(value):
    try:
        return round(float(str(value).replace(",", "")), 2) if _text(value) else None
    except ValueError:
        return None


def _tax(value):
    """泰国税号固定 13 位：读出 12 位数字的是开头的 0 丢了，补回（2026-09-30 压测第 17 轮 V03：「105541034263 ≈
    0105541034263」只算疑似匹配）。用全平台共用的 `repaired_tax_id`：补完过得了 MOD-11 校验位才补（用户 2026-09-30）。"""
    return _text(repaired_tax_id(_text(value)))


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
    # 已经交过的（等识别的对账每轮从头跑、被杀续跑）直接用：余额只在第一次交时查一次（与原来一样），读完扣过费的余额
    # 不能拿来再判一次；也不按当前设置重新冻结（设置变了会被当成「同一参考号换了内容」拒掉）。
    references = [f"{recon_job}:{index}" for index in range(len(inspected))]
    names = [core_work.name_for(KIND, reference) for reference in references]
    existing = set(frappe.get_all(WORK, filters={"name": ["in", names]}, pluck="name"))
    if not existing:
        waiting.balance(lambda: intake.balance(sum(info["page_count"] for _f, _n, info in inspected),
                                               frozen["billing_tier"]), f"{recon_job}:invoices")
    works = []
    for index, (file_name, filename, info) in enumerate(inspected):
        arguments = {"recon_job": recon_job, "file": file_name, "filename": filename, "pages": info["page_count"],
                     "sha256": info["sha256"], "settings": frozen}
        work = names[index] if names[index] in existing else admission.when_room(
            lambda index=index, arguments=arguments: _create(references[index], arguments, user), key=references[index])
        works.append((work, filename))
        progress.advance(file_name, work=work)
    for file_name, filename, _content in files:
        if filename in rejected:
            progress.advance(file_name, state="failed")
    return works, rejected


def _finished(recon_job, file, filename, pages, invoices):
    """看板：这份发票读完了；每张读出的发票进「刚读出」（发票号、日期、含税金额）。只作展示，失败不影响识别。"""
    try:
        progress.advance(file, state="done" if invoices else "failed", read=int(pages), done=int(pages))
        for invoice in invoices[-progress.LATEST:]:
            # 发票读出的金额没有余额链可校，只说读出了哪张（发票号、日期）。
            progress.latest(recon_job, {"name": invoice.get("invoice_no") or filename, "page": 0, "rows": 0,
                                        "date": progress._plain_date(invoice.get("invoice_date")), "kind": "",
                                        "amount": ""})
    except Exception:
        frappe.log_error(title="cowork recon progress", message=frappe.get_traceback())


def _create(reference, arguments, user):
    with work_guard.work_creation():
        return core_work.submit(KIND, reference, arguments, user)


def wait(works, on_stage=None):
    """等 Work 结束，取回每份的发票。返回 (发票记录, 没读出的文件名)。

    余额不足时 Work 会一直顺延：所有没结束的都卡在余额上就直接报余额不足（这些 Work 随后自行取消）。
    排队不算读取时限；开始读了以后读满 ``READ_LIMIT``，没读完的文件算没读出（核查照常出结果、点名这些文件），
    它们在核查结束后下一次付费调用前自行取消、不扣费（``_recon_closed``）。一张发票都没读出才整份报超时。
    还没读完时不在这里守着（``waiting``）：对账后台任务这一轮先结束、交还进程，都有结论或到了时限再接着跑。
    返回 ``(发票, 没读出的文件名, 读到时限还没读完的份数)``。
    """
    names = [name for name, _f in works]

    def evaluate(clock, now):
        frappe.db.commit()  # 结束读快照，读到 Work 的最新状态
        rows = {row.name: row for row in frappe.get_all(WORK, filters={"name": ["in", names]},
                                                         fields=["name", "status", "last_code"])}
        open_names = [name for name in names if not rows.get(name) or rows[name].status not in TERMINAL]
        if not open_names:
            return set()
        open_rows = [rows.get(name) for name in open_names]
        if all(row and row.status == "deferred" and row.last_code == "insufficient_balance" for row in open_rows):
            frappe.throw(_("vat_check.balance_insufficient"))
        entered = clock.setdefault("entered", now)
        if clock.get("reading") is None:
            # 开始读了：从最早开始读的那一份起算读取时限（不早于开始等的时候）
            firsts = [calls["since_first"] for calls in core_work.call_progress(names).values()
                      if calls.get("since_first") is not None]
            if firsts:
                clock["reading"] = max(entered, now - max(firsts))
            elif len(names) > len(open_names) or any(row and row.status not in ("queued", "deferred")
                                                     for row in open_rows):
                clock["reading"] = now
        if clock.get("reading") is None:
            if now - entered > QUEUE_LIMIT:
                frappe.throw(_("vat_check.recognition_busy"))
            raise waiting.Pending(open_names, due=entered + QUEUE_LIMIT, start=True)
        if now - clock["reading"] > READ_LIMIT:
            return set(open_names)
        raise waiting.Pending(open_names, due=clock["reading"] + READ_LIMIT)

    stopped = waiting.settle("invoices", evaluate) if names else set()
    finished = [name for name in names if name not in stopped]
    succeeded = set(frappe.get_all(WORK, filters={"name": ["in", finished], "status": "succeeded"},
                                   pluck="name")) if finished else set()
    invoices, failed = [], []
    for name, filename in works:
        saved = core_work.saved_checkpoint(name, RESULT) if name in succeeded else None
        found = (saved or {}).get("body", {}).get("invoices") if saved else None
        if found:
            invoices.extend(found)
        else:
            failed.append(filename)
    if stopped and not invoices:
        frappe.throw(_("vat_check.recognition_timeout"))
    return invoices, failed, len(stopped)


# ── Work 里跑：识别 → 存结果 → 扣费 ─────────────────────────────────────────


def _recognize(content, filename, pages, frozen, recon_job=None, file=None):
    from pearnly_cowork.recon_center import document_ocr

    environment = frozen["environment"]
    if forward.settings():
        # 在 Cloud Run 上读（ADR-015）：同一个引擎、同一份核查包装（recon_center.reading）；发出前、收到后各看一次核查是否已结束。
        if recon_job and document_ocr._recon_closed(recon_job, file):
            raise document_ocr.ReconClosed()
        work_name = frappe.flags.get("pearnly_ocr_work")
        pipeline, _evidence = forward.send(work_name=work_name, batch=work_name, filename=filename, content=content,
            page_count=int(pages), anchor_date=frozen["anchor_date"], frozen=frozen, variant="vat_check",
            yield_seconds=YIELD_SECONDS)
        if recon_job and document_ocr._recon_closed(recon_job, file):
            raise document_ocr.ReconClosed()
        anchor = date.fromisoformat(frozen["anchor_date"])
        return records(PipelineResult.model_validate_json(pipeline, context={"anchor_year": anchor.year}).pages, filename)
    selected = invoice_policy(environment, frozen["policy"])
    config = config_from_environment(environment, selected)
    client = build_client(environment, core_identity={key: frozen["target"][key] for key in
                                                      ("backend", "project", "location", "credentials_path")})
    started = time.monotonic()

    def guarded(callback):
        def invoke(*args, **kwargs):
            # 每次付费调用前结束读快照；核查已经结束（没等这份就出了结果）就不再调用；超过时限就让出，
            # 重跑时已完成的调用从检查点取回。
            frappe.db.commit()
            if recon_job and document_ocr._recon_closed(recon_job, file):
                raise document_ocr.ReconClosed()
            if time.monotonic() - started > YIELD_SECONDS:
                raise Deferred("progress_yield", 1)
            return callback(*args, **kwargs)
        return invoke

    model_call, vision_call = recon_reading.vat_callbacks(client)

    anchor = date.fromisoformat(frozen["anchor_date"])
    with PageSource(content, filename, expected_page_count=int(pages), enable_text_path=selected.text_fast_path,
                    image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")), orient=ocr.orient) as source:
        # 多页文件各页同时读（开关 PEARNLY_OCR_PAGE_THREADS）；每页的 last 记录按页码分开，互不干扰。
        result = run_invoice(source, guarded(model_call), guarded(vision_call), config=config,
                             anchor_year=anchor.year, today=anchor,
                             map_pages=core_pages.mapper() if int(pages) > 1 else None)
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
            try:
                # 本机读才占本进程的识别名额，交给 Cloud Run 的不占（document_ocr.reading_slot）。
                with document_ocr.reading_slot():
                    invoices = _recognize(content, filename, pages, frozen, recon_job, file)
            except document_ocr.ReconClosed:
                return {"cancelled": "recon_job_closed"}
            except (EngineError, ProviderFailure) as error:
                # 整份读不出（每页都没结果、不是发票）：记成没读出，不扣费、不重跑付费调用；核查结果里点名这份文件
                # （原来冒到共用层被当成程序错误，2026-09-30 压测第 21 轮 V04）。
                frappe.log_error(title="cowork vat check invoice", message=f"{filename}: {error}")
                invoices = []
            saved = core_work.checkpoint(RESULT, "done", {"invoices": invoices})
            _finished(recon_job, file, filename, pages, invoices)
        if saved["body"]["invoices"] and not core_work.checkpoint(BILLED):
            # 扣费前再看一次核查：已经结束（失败或完成）就用不到这份结果，不扣客户的钱（与账单路径 document_ocr 同一
            # 口径；2026-09-30 压测 V01：核查因识别繁忙失败后，读完的小票照样扣费）。
            from pearnly_cowork.recon_center import document_ocr

            # 发过一次扣费就照同一参考号记完，不因核查已结束而取消（同 document_ocr.run，独立复查 2026-10-01）。
            if not core_work.checkpoint(BILLING_SENT) and document_ocr._recon_closed(recon_job, file):
                return {"cancelled": "recon_job_closed"}
            core_work.checkpoint(BILLING_SENT, "done", {})
            _charge(name, pages, frozen)
            core_work.checkpoint(BILLED, "done", {"units": int(pages)})
        return {}
    finally:
        frappe.set_user(previous)
