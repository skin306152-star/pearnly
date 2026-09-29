"""对账三类文档（银行账单 / 总账 / 销项税报告）要模型读时：扫描件、照片、要模型兜底的 PDF。

用户 2026-09-29（经「语料盘点与压测准备」窗口转达）：「旧项目能用，这里就要能用」—— 扫描 PDF 与手机照片原来一律读不出。

旧线上（ec2e5c957e6b ``services/ocr/pipeline.py``）读长表文档的非企业档路：PDF 先试文字层（平均每页 ≥ 200 字才算），
不够就按 200 DPI 逐页渲染、Google Vision 取字，再用文档提示词逐页读成行；只有一页的扫描件 / 照片由模型直接看图读。
这里照同一路走：文字层 / Vision 文字 → ``document_tier.read_pages``（同一份提示词、列来源校验、余额链闸门与一次升级重读），
一页的扫描件 / 照片把页面图一起给模型。旧线上另有企业档（Document AI 版面重建），三类文档当时是否走它取决于已删除的旧库配置，
无法核实，这里不迁（见 docs/work/2026-09-29-cowork-recon-scanned-docs.md）。

执行：每份文件一个持久识别任务（Pearnly OCR Work，kind ``cowork.recon_document``），在识别工作进程里跑，与录入 / 销项税核查
相同的冻结配置、限流、超时让出；服务商调用记在 Work 的检查点，重跑不重复付费。原来这条模型路直接在对账后台任务里调，
服务商层要求在 Work 里（``ocr.durable_work_required``），需要模型的文件整单失败（2026-09-29 读代码发现，带文字层的收入总账 PDF 也中）。

模型：三类文档都用识别配置里的升级档（旧线上把银行 / 总账 / 税报钉在强模型档：轻量档读长表余额链断点 40 处、强模型 2 处，
``engine_policy.py`` 2026-07-22 真料记录）。

计费：读出行的文件按页扣（与录入发票同价、同档位），参考号固定为 Work 名，重试只补记账不重复扣；读不出不扣。
"""
import hashlib
import time

import frappe
from frappe import _
from pearnly_core import billing_client, ocr
from pearnly_core.execution import work as core_work
from pearnly_core.execution.errors import Deferred, QueueFull

from pearnly_cowork.files.service import inspect_content
from pearnly_cowork.ocr.engine import document_tier
from pearnly_cowork.ocr.engine.errors import ContentError
from pearnly_cowork.ocr.engine.runner import page_text
from pearnly_cowork.ocr.execution import service as intake, settings as ocr_settings, work_guard
from pearnly_cowork.ocr.execution.access import WORK
from pearnly_cowork.ocr.inputs_runtime.source import PageSource
from pearnly_cowork.ocr.policy import invoice_policy
from pearnly_cowork.ocr.providers.core import build_client

KIND = "cowork.recon_document"
RESULT = KIND + ":result"
BILLED = KIND + ":billed"
TERMINAL = ("succeeded", "failed", "cancelled", "uncertain")
YIELD_SECONDS = 420
WAIT_SECONDS = 25 * 60
POLL_SECONDS = 3
PAGE_TIMEOUT_SECONDS = 110
IDENTITY = ("backend", "project", "location", "credentials_path")


# ── 发起与等待（在对账后台任务里）────────────────────────────────────────────


def submit(recon_job, side, file_name, filename, content, document_type, user):
    """建一个识别任务；文件不是 PDF / 图片返回 None。余额不足直接提示（不排一个注定顺延的任务）。"""
    if not document_tier.admitted(document_type):
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
        with work_guard.work_creation():
            return core_work.submit(KIND, f"{recon_job}:{side}:{document_type}", arguments, user)
    except (Deferred, QueueFull) as error:
        # 与销项税核查读发票同一口径：余额不足、查不到余额、识别繁忙分开说。
        reason = getattr(error, "reason", "")
        frappe.throw(_("reconciliation.balance_insufficient") if reason == "insufficient_balance"
                     else _("vat_check.balance_unavailable") if reason == "lookup_error"
                     else _("reconciliation.recognition_busy"))


def wait(name):
    """等这一份读完：成功回旧线上管线的结果形状（``pages`` + ``_needs_review``），读不出回 None。"""
    deadline = time.monotonic() + WAIT_SECONDS
    while True:
        frappe.db.commit()  # 结束读快照，读到 Work 的最新状态
        row = frappe.db.get_value(WORK, name, ["status", "last_code", "checkpoints"], as_dict=True)
        if row and row.status in TERMINAL:
            break
        if row and row.status == "deferred" and row.last_code == "insufficient_balance":
            frappe.throw(_("reconciliation.balance_insufficient"))
        if time.monotonic() > deadline:
            frappe.throw(_("reconciliation.recognition_timeout"))
        time.sleep(POLL_SECONDS)
    if row.status != "succeeded":
        return None
    saved = frappe.parse_json(row.checkpoints or "{}").get(RESULT) or {}
    return (saved.get("body") or {}).get("legacy")


def reader(recon_job, side, file_name, user):
    """给对账读取层的模型接缝：``(document_type, content, filename) → 管线结果 / None``。"""
    def read(document_type, content, filename):
        name = submit(recon_job, side, file_name, filename, content, document_type, user)
        return wait(name) if name else None
    return read


# ── Work 里跑：逐页取字 → 读成行 → 存结果 → 扣费 ──────────────────────────────


def _rows_found(legacy):
    return any((page.get("document") or {}).get("entries") for page in (legacy or {}).get("pages") or [])


def read_pages(source, document_type, model_call, vision_call, *, model, escalated_model):
    """逐页：文字层有字就用，没有就渲染这一页送 Vision 取字；只有一页时把页面图一起给模型。"""
    texts = source.text_pages
    pages = []
    for number in range(1, source.page_count + 1):
        text = page_text(texts[number - 1]) if texts else ""
        images = ()
        if not str(text or "").strip():
            frame = source.frame(number)
            text = page_text(vision_call(frame, stage="layer1", page_number=number).page)
            if source.page_count == 1:
                images = ((frame.image_bytes, frame.mime_type),)
            del frame  # 多页扫描件逐页释放渲染图，不攒在内存里
        pages.append((number, text, images))
    legacy, _warnings = document_tier.read_pages(document_type, pages, model_call, model=model,
        escalated_model=escalated_model, timeout_s=PAGE_TIMEOUT_SECONDS)
    return legacy


def _read(content, filename, pages, frozen, document_type):
    environment = frozen["environment"]
    selected = invoice_policy(environment, frozen["policy"])
    client = build_client(environment, core_identity={key: frozen["target"][key] for key in IDENTITY})
    started = time.monotonic()

    def guarded(callback):
        def invoke(*args, **kwargs):
            # 每次付费调用前结束读快照；超过时限就让出，重跑时已完成的调用从检查点取回。
            frappe.db.commit()
            if time.monotonic() - started > YIELD_SECONDS:
                raise Deferred("progress_yield", 1)
            return callback(*args, **kwargs)
        return invoke

    strong = selected.l3_model or selected.first_model
    with PageSource(content, filename, expected_page_count=int(pages), enable_text_path=True,
                    image_max_long_edge=int(environment.get("OCR_IMG_MAX_LONG_EDGE", "2400")),
                    orient=ocr.orient) as source:
        return read_pages(source, document_type, guarded(client.model_call), guarded(client.vision_call),
            model=strong, escalated_model=strong)


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
            job = frappe.cache.get_value(f"cowork:recon-job:{recon_job}") or {}
            if job.get("state") in ("failed", "done") or not frappe.db.exists("File", file):
                return {"cancelled": "recon_job_closed"}
            doc = frappe.get_doc("File", file)
            content = doc.get_content()
            content = content if isinstance(content, bytes) else content.encode()
            if hashlib.sha256(content).hexdigest() != sha256:
                return {"failed": "source_changed"}
            ocr_settings.revalidate(frozen)
            intake.balance(int(pages), frozen["billing_tier"])
            try:
                legacy = _read(content, filename, pages, frozen, document_type)
            except ContentError as error:
                # 读不出（模型回的不是这类文档、整份没有字）：记成读不出，不扣费、不重跑付费调用。
                frappe.log_error(title="cowork recon document", message=f"{filename}: {error}")
                legacy = None
            saved = core_work.checkpoint(RESULT, "done", {"legacy": legacy})
        if _rows_found(saved["body"]["legacy"]) and not core_work.checkpoint(BILLED):
            _charge(name, pages, frozen)
            core_work.checkpoint(BILLED, "done", {"units": int(pages)})
        return {}
    finally:
        frappe.set_user(previous)
