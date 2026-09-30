"""对账三类文档（银行账单 / 总账 / 销项税报告）要模型读时：扫描件、照片、要模型兜底的 PDF。

用户 2026-09-29（经「语料盘点与压测准备」窗口转达）：「旧项目能用，这里就要能用」—— 扫描 PDF 与手机照片原来一律读不出。

按旧线上实际在跑的路迁：2026-09-05 22:19 起旧线上把银行 / 总账 / 税报切到**企业档**（旧仓 ``docs/deployment/MIGRATION_STATUS.md``）：
Google Document AI 读字（每页约 0.05 泰铢），银行与总账先按版面坐标在本地拼行，余额链算得平就直接用、不调大模型；
算不平（以及税报，它没有本地拼行器）才逐页让 3.8 LOW 看原图 + 识别原文按冻结契约抽取。见 ``ocr.enterprise``。
用户 2026-09-29 选定「迁企业档 Document AI」。

执行：每份文件一个持久识别任务（Pearnly OCR Work，kind ``cowork.recon_document``），在识别工作进程里跑，与录入 / 销项税核查
相同的冻结配置、限流、超时让出；服务商调用记在 Work 的检查点，重跑不重复付费。原来这条模型路直接在对账后台任务里调，
服务商层要求在 Work 里（``ocr.durable_work_required``），需要模型的文件整单失败（2026-09-29 读代码发现，带文字层的收入总账 PDF 也中）。

计费：读出行的文件按页扣（与录入发票同价、同档位；旧线上对账同样按页扣），参考号固定为 Work 名，重试只补记账不重复扣；读不出不扣。
"""
import functools
import hashlib
import io
import json
import time

import frappe
from frappe import _
from pearnly_core import billing_client, ocr
from pearnly_core.execution import work as core_work
from pearnly_core.execution.errors import Deferred, QueueFull, Uncertain

from pearnly_cowork.files.service import inspect_content
from pearnly_cowork.recon_center import progress
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.enterprise import pipeline as enterprise
from pearnly_cowork.ocr.execution import service as intake, settings as ocr_settings, work_guard
from pearnly_cowork.ocr.execution.access import WORK
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.providers.core import build_client

KIND = "cowork.recon_document"
RESULT = KIND + ":result"
BILLED = KIND + ":billed"
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
# 让出前最多跑的时间：一次抽取最长 180 秒、等名额最多 60 秒、结果不明再重发一次，最坏 2 ×（60 + 180）= 480 秒；读字
# 3 ×（60 + 90）= 450 秒。90 + 480 = 570 仍在识别任务 600 秒的硬上限内 —— 原来 200 秒会越过上限，调用中途被杀、留下
# 「started」，续跑整份作废（2026-09-30 压测第 18 轮）。
YIELD_SECONDS = 90
WAIT_SECONDS = 25 * 60
# 到了 WAIT_SECONDS 还在读、但最近 PROGRESS_WINDOW 内还有页读完（名额挤、排着队，不是卡住）：接着等，最多
# WAIT_LIMIT（2026-09-30 压测 B02：29 页读到 23 页时 25 分钟到了，整套失败、已付的钱白付）。后台任务时限
# （jobs.TIMEOUTS）与页面等待（62 分钟）都在它之上。
PROGRESS_WINDOW = 5 * 60
PROGRESS_CHECK_SECONDS = 60
WAIT_LIMIT = 50 * 60
# 还在排队、一次调用都没做（前面排着别家的大批照片）的时间不算进上面两个时限，另算：最多排这么久
# （2026-09-30 压测 rH B08-01：2 页的 PDF 排在近 100 张照片后面，25 分钟到了还没开始读，整套失败）。
QUEUE_LIMIT = 90 * 60
POLL_SECONDS = 3
IDENTITY = ("backend", "project", "location", "credentials_path")


# ── 发起与等待（在对账后台任务里）────────────────────────────────────────────


def submit(recon_job, side, file_name, filename, content, document_type, user):
    """建一个识别任务；文件不是 PDF / 图片返回 None。余额不足直接提示（不排一个注定顺延的任务）。"""
    if document_type not in enterprise.CATEGORIES:
        return None
    try:
        info = inspect_content(filename, content)
    except frappe.ValidationError:
        frappe.clear_last_message()
        frappe.throw(_("reconciliation.document_unreadable"))
    if info["kind"] not in ("pdf", "image"):
        return None
    frozen = ocr_settings.snapshot()
    arguments = {"recon_job": recon_job, "file": file_name, "filename": filename, "pages": int(info["page_count"]),
                 "sha256": info["sha256"], "document_type": document_type, "settings": frozen}
    try:
        intake.balance(int(info["page_count"]), frozen["billing_tier"])
        from pearnly_cowork.recon_center import admission

        def create():
            with work_guard.work_creation():
                return core_work.submit(KIND, f"{recon_job}:{side}:{document_type}", arguments, user)
        name = admission.when_room(create)
        _progress(file_name, work=name)
        return name
    except (Deferred, QueueFull) as error:
        # 与销项税核查读发票同一口径：余额不足、查不到余额、识别繁忙分开说。
        reason = getattr(error, "reason", "")
        frappe.throw(_("reconciliation.balance_insufficient") if reason == "insufficient_balance"
                     else _("vat_check.balance_unavailable") if reason == "lookup_error"
                     else _("reconciliation.recognition_busy"))


# 等到时限时识别已经读完（结果已存、只差扣费收尾）就再等这么久，不在这时判超时：否则识别刚扣完费、对账却判了失败，
# 客户付了钱拿不到结果（2026-09-29 压测 BAY-CA-03：对账 25 分钟判超时的同一分钟，识别读完扣了 12 页）。
FINISHING_GRACE_SECONDS = 120


def wait(name):
    """等这一份读完：成功回旧线上管线的结果形状（``pages`` + ``_needs_review``），读不出回 None。"""
    entered = started = time.monotonic()
    deadline = started + WAIT_SECONDS
    extended = False
    done, progressed, checked = -1, started, 0.0
    waiting_in_queue = True
    while True:
        frappe.db.commit()  # 结束读快照，读到 Work 的最新状态
        row = frappe.db.get_value(WORK, name, ["status", "last_code"], as_dict=True)
        if row and row.status in TERMINAL:
            break
        if row and row.status == "deferred" and row.last_code == "insufficient_balance":
            frappe.throw(_("reconciliation.balance_insufficient"))
        now = time.monotonic()
        if now - checked >= PROGRESS_CHECK_SECONDS or now > deadline:
            checked = now
            count = completed_calls(name)
            if count > done:
                done, progressed = count, now
        if waiting_in_queue:
            if row and (row.status not in ("queued", "deferred") or done > 0):
                waiting_in_queue = False  # 开始读了：从这一刻起算读取时限
            elif now - entered > QUEUE_LIMIT:
                frappe.throw(_("reconciliation.recognition_busy"))
            else:
                started, progressed, deadline = now, now, now + WAIT_SECONDS
        if now > deadline:
            if not extended and RESULT in frappe.parse_json(frappe.db.get_value(WORK, name, "checkpoints") or "{}"):
                extended = True
                deadline = now + FINISHING_GRACE_SECONDS
            elif now - progressed <= PROGRESS_WINDOW and now - started < WAIT_LIMIT:
                deadline = now + PROGRESS_CHECK_SECONDS  # 还在一页一页读完：再看一分钟
            else:
                frappe.throw(_("reconciliation.recognition_timeout"))
        time.sleep(POLL_SECONDS)
    if row.status != "succeeded":
        return None
    saved = frappe.parse_json(frappe.db.get_value(WORK, name, "checkpoints") or "{}").get(RESULT) or {}
    return (saved.get("body") or {}).get("legacy")


def _progress(file, **values):
    """看板进度只作展示（只往前走，见 ``progress.advance``）：缓存写失败不影响识别本身。"""
    try:
        progress.advance(file, **values)
    except Exception:
        frappe.log_error(title="cowork recon progress", message=frappe.get_traceback())


def _finished(recon_job, file, filename, pages, legacy):
    """整份读完：本地拼行一次出来的（没逐页抽取）这里补上每页的「刚读出」。"""
    if legacy is None:
        _progress(file, state="failed")
        return
    _progress(file, read=int(pages), done=int(pages), state="done")
    try:
        for item in progress.legacy_summaries(legacy, filename)[-progress.LATEST:]:
            progress.latest(recon_job, item)
    except Exception:
        frappe.log_error(title="cowork recon progress", message=frappe.get_traceback())


def completed_calls(name):
    """这份识别已经做完了几次调用（读字的块、抽取的页）：检查点里记成 done 的个数。"""
    values = frappe.parse_json(frappe.db.get_value(WORK, name, "checkpoints") or "{}") or {}
    return sum(1 for value in values.values() if isinstance(value, dict) and value.get("state") == "done")


def reader(recon_job, side, file_name, user):
    """给对账读取层的模型接缝：``(document_type, content, filename) → 管线结果 / None``。

    这份文件在这个对账号下已经有识别任务（照片预先提交过，或答了预设问题后用原文件重跑）就直接等它、取它存的结果，
    不再提交：重跑的人可能不是当初上传的人，按原参数再提交会被当成「同一参考号换了内容」拒掉，也不该再过一次余额与名额。"""
    def read(document_type, content, filename):
        name = frappe.db.get_value(WORK, {"kind": KIND, "reference": f"{recon_job}:{side}:{document_type}"}, "name")
        if name:
            _progress(file_name, work=name)
        else:
            name = submit(recon_job, side, file_name, filename, content, document_type, user)
        return wait(name) if name else None
    return read


# ── Work 里跑：逐页取字 → 读成行 → 存结果 → 扣费 ──────────────────────────────


def _rows_found(legacy):
    return any((page.get("document") or {}).get("entries") for page in (legacy or {}).get("pages") or [])


def read_file(source, content, is_pdf, mime, document_type, document_ai_call, model_call):
    """企业档读一份文件 → 旧线上管线结果形状（多页、带 engine）。页面图只在要 3.8 抽取时逐页渲染。"""
    reads = enterprise.pdf_chunks(content, source.page_count) if is_pdf else [(content, mime, 1)]

    def render(number):
        frame = source.frame(number)
        return frame.image_bytes, frame.mime_type

    result = enterprise.run(enterprise.CATEGORIES[document_type], page_count=source.page_count, reads=reads,
        render=render, document_ai_call=document_ai_call, model_call=model_call)
    return enterprise.to_legacy(result)


def _mime(content):
    from PIL import Image

    with Image.open(io.BytesIO(content)) as image:
        return Image.MIME[image.format]


class ReconClosed(Exception):
    """对账已经结束（失败或完成），这份识别结果不会再被用到。"""


def _recon_closed(recon_job, file=None):
    """对账已结束：状态缓存写着失败 / 完成，或者它存的源文件已经删了（对账结束时一定删；部署、清缓存会清掉状态缓存，
    只看缓存会把已结束的对账当成还在等），或者对账后台任务本身已经没了（部署 / 重启时被杀、判为弃置：源文件还在、
    状态缓存也被清了，识别却还在一页页付费读，没人用；2026-09-30 测试机重载打断压测 rI）。"""
    job = frappe.cache.get_value(f"cowork:recon-job:{recon_job}") or {}
    if job.get("state") in ("failed", "done") or bool(file and not frappe.db.exists("File", file)):
        return True
    from pearnly_cowork.recon_center import jobs

    try:
        return jobs._job_ended(recon_job)
    except Exception:
        return False  # 队列一时查不到：不下结论，照常读


def _read(content, filename, pages, frozen, document_type, recon_job=None, file=None):
    environment = frozen["environment"]
    client = build_client(environment, core_identity={key: frozen["target"][key] for key in IDENTITY})
    started = time.monotonic()

    def guarded(callback):
        def invoke(*args, **kwargs):
            # 每次付费调用前结束读快照；超过时限就让出，重跑时已完成的调用从检查点取回。对账已经结束就不再调用
            # （2026-09-29 压测 BAY-CA-03：对账判失败后识别任务还在读、占名额）。
            frappe.db.commit()
            if recon_job and _recon_closed(recon_job, file):
                raise ReconClosed()
            if time.monotonic() - started > YIELD_SECONDS:
                raise Deferred("progress_yield", 1)
            return callback(*args, **kwargs)
        return invoke

    _progress(file, state="reading")  # 识别一开始就算读取中（状态只往前走，让出续跑时不退回排队）
    # 看板进度（``progress``）：这次运行里从 0 数起（续跑时已完成的调用从检查点取回、照样经过这里），写进去取较大的。
    counted = {"read": 0, "done": 0}

    def reading(callback):
        def invoke(*args, **kwargs):
            result = callback(*args, **kwargs)
            counted["read"] += int(kwargs.get("expected_pages") or 1)
            _progress(file, read=counted["read"], state="reading")
            return result
        return invoke

    def extracting(callback):
        def invoke(request, *args, **kwargs):
            try:
                response = callback(request, *args, **kwargs)
            except ContentError as error:
                if error.code == enterprise.UNREAD_PAGE:
                    progress.unread(file, getattr(request, "page_number", 0))
                raise
            if getattr(request, "stage", "") == enterprise.REREAD_STAGE:
                return response  # 自核没过、这页再读一次：看板不重复记页数、不再报一次「刚读出」
            counted["done"] += 1
            _progress(file, done=counted["done"])
            try:
                document = json.loads(response.text or "{}")
            except (TypeError, ValueError):
                document = None
            progress.latest(recon_job, progress.page_summary(document, filename, getattr(request, "page_number", 0),
                                                             amounts=False))
            return response
        return invoke

    is_pdf = filename.lower().endswith(".pdf")
    with PageSource(content, filename, expected_page_count=int(pages), enable_text_path=False,
                    image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")),
                    orient=ocr.orient) as source:
        # 结果不明（上传超时、连接中断）时重发一次：读字每页约 0.05 泰铢、抽取每页约 0.5 泰铢，只在出事时多花；
        # 不重发的话整份文件读空、整套对账失败，用户只能整份重传（2026-09-29 压测 45 个识别任务里 7 个）。
        return read_file(source, content, is_pdf, None if is_pdf else _mime(content), document_type,
            reading(guarded(functools.partial(client.document_ai_call, retry_unknown=True))),
            extracting(page_failure(guarded(functools.partial(client.model_call, retry_unknown=True)))))


def page_failure(call):
    """一页的抽取重发后仍结果不明（模型读超时、连接中断；续跑时遇到上次没收尾的调用也一样）：换成「这一页读不出」，
    流程只丢这一页、其余页照常（``pipeline.UNREAD_PAGE``；2026-09-30 压测第 23 轮 BAY-CA-03：一页 180 秒读超时，
    12 页整份作废、整套对账失败）。读字（Document AI）结果不明照旧整份作废：没有字就没有页可抽。"""
    def invoke(request, *args, **kwargs):
        try:
            return call(request, *args, **kwargs)
        except Uncertain as error:
            raise ContentError(enterprise.UNREAD_PAGE, stage=str(getattr(request, "stage", "")),
                               page_number=int(getattr(request, "page_number", 0) or 0)) from error
    return invoke


def _charge(work_name, pages, frozen):
    try:
        result = billing_client.charge("cowork", "pdf", int(pages), tier=frozen["billing_tier"],
            reference=KIND + ":" + work_name, description="Cowork reconciliation document recognition")
    except Exception:
        result = {"ok": False}  # 结果不明也按“待记账”：参考号固定，重试安全
    if not (isinstance(result, dict) and result.get("ok") is True):
        raise Deferred("billing_pending", 60)


def run(recon_job, file, filename, pages, sha256, document_type, settings):
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
            if _recon_closed(recon_job, file):
                return {"cancelled": "recon_job_closed"}
            from pearnly_cowork.recon_center import storage

            _stored_name, content = storage.read(file)
            if hashlib.sha256(content).hexdigest() != sha256:
                return {"failed": "source_changed"}
            ocr_settings.revalidate(frozen)
            intake.balance(int(pages), frozen["billing_tier"])
            try:
                legacy = _read(content, filename, pages, frozen, document_type, recon_job, file)
            except ReconClosed:
                return {"cancelled": "recon_job_closed"}
            except ContentError as error:
                # 读不出（模型回的不是这类文档、整份没有字）：记成读不出，不扣费、不重跑付费调用。
                frappe.log_error(title="cowork recon document", message=f"{filename}: {error}")
                legacy = None
            saved = core_work.checkpoint(RESULT, "done", {"legacy": legacy})
            _finished(recon_job, file, filename, pages, legacy)
        if _rows_found(saved["body"]["legacy"]) and not core_work.checkpoint(BILLED):
            # 扣费前再看一次对账：已经失败（等满 25 分钟判失败）或已经结束，这份结果不会再被用到 —— 不扣客户的钱，
            # 任务记成取消（2026-09-29 压测：BAY 12 页读了 46 分钟，对账早已判失败，识别读完照样扣费）。用到结果的
            # 识别一定在对账结束前扣费（对账等它成功才往下走）。已发出的供应商调用费由平台承担。
            if _recon_closed(recon_job, file):
                return {"cancelled": "recon_job_closed"}
            _charge(name, pages, frozen)
            core_work.checkpoint(BILLED, "done", {"units": int(pages)})
        return {}
    finally:
        frappe.set_user(previous)
