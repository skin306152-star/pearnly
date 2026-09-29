"""对账后台任务：接两份文件 → 排队 → 按阶段报进度 → 出任务（照旧线上 recon_jobs 的做法）。

旧线上提交后拿 job_id，前端每 1.5 秒问一次状态，只显示阶段文字（解析 / 核对 / 保存），不显示百分比；
用户 2026-09-26 定的也是“只显示阶段文字”。这里同样：状态只有 queued / running / done / failed，
另带当前阶段；PDF 走识别时可能要几十秒，所以放到后台队列，不占网页请求。

两份文件先存成本人的私有 File（不挂任何单据），任务读完即删；状态放缓存（24 小时），
只有发起的人能查。对账结果本身落在各自的任务表里，缓存丢了也不影响结果。
"""
import json
import uuid

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.reconciliation import bank_recon_access, bank_recon_service, income_recon_access
from pearnly_cowork.reconciliation import income_recon_service, vat_check_access

KINDS = {"bank": bank_recon_access, "income": income_recon_access, "vat": vat_check_access}
# 每个上传口可以放几份：收入各一份；销项税核查的报告可多份（同卖方同期间合并）、发票可很多张；
# 银行对账两边都可多份（旧线上 `bank_v2_submit` 收多个账单 / 总账文件逐份读再合并，存折一页一张照片就靠它）。
MULTIPLE = {"vat": ("left", "right"), "bank": ("left", "right"),
            # 收入对账的税报可多份（一页一张照片；用户 2026-09-30 定），总账一份。
            "income": ("right",)}
MAX_FILES = 200
MAX_BYTES = 25 * 1024 * 1024
TTL = 24 * 3600
# 后台任务时限：销项核查的发票可能上百张、按识别名额分批读（2026-09-30 压测 V01 72 张小票），给到一小时。
# 银行、收入同样给到一小时：识别名额挤的时候一份还在一页一页读完就接着等（document_ocr.WAIT_LIMIT 50 分钟），
# 不能先被后台任务时限杀掉（2026-09-30 压测 B02）。
TIMEOUTS = {"vat": 3600, "bank": 3600, "income": 3600}


def _key(job_id):
    return f"cowork:recon-job:{job_id}"


def _state(job_id, **values):
    current = frappe.cache.get_value(_key(job_id)) or {}
    current.update(values)
    frappe.cache.set_value(_key(job_id), current, expires_in_sec=TTL)
    return current


def _save(content, filename):
    # 原生 File 插入，按站点上限（默认 25MB，与 MAX_BYTES 一致）；旧的 file_manager.save_file 另有 10MB 默认上限，
    # 10.7MB / 14.8MB 的发票扫描件整单被拒（2026-09-29 压测 V03）。与发票原件同内容的另存（storage）。
    from pearnly_cowork.recon_center import storage

    return storage.save(content, filename)


def start(kind, title, sides, options=None):
    """sides: {"left": [(filename, bytes), ...], "right": [...]}——左、右两个上传口。"""
    if kind not in KINDS:
        frappe.throw(_("reconciliation.type_invalid"))
    workspace = scope.default_workspace()
    KINDS[kind].require(workspace)
    everything = [item for side in ("left", "right") for item in sides.get(side) or []]
    if (any(not sides.get(side) for side in ("left", "right")) or any(not content for _name, content in everything)
            or any(len(sides[side]) != 1 for side in ("left", "right") if side not in MULTIPLE.get(kind, ()))
            or len(everything) > MAX_FILES):
        frappe.throw(_("reconciliation.files_required"))
    if any(len(content) > MAX_BYTES for _name, content in everything):
        frappe.throw(_("reconciliation.file_too_large"))
    title = str(title or "").strip() or " / ".join(name for name, _content in everything[:2])
    job_id = uuid.uuid4().hex
    saved = {side: [_save(content, name) for name, content in sides[side]] for side in ("left", "right")}
    _state(job_id, user=frappe.session.user, kind=kind, state="queued", stage="queued", task=None, error=None)
    frappe.enqueue("pearnly_cowork.recon_center.jobs.run", queue="long", timeout=TIMEOUTS.get(kind, 1800),
                   job_id=f"cowork-recon-{job_id}",
                   enqueue_after_commit=True, recon_job=job_id, kind=kind, workspace=workspace, title=title[:140],
                   files=saved, options=json.dumps(options or {}))
    return {"job": job_id}


# 三种对账任务的任务名都由「站点 | 用户 | 请求号」算出（请求号就是本任务号），前缀各自不同。
TASK_NAMES = {"bank": ("Cowork Bank Reconciliation", "CBR"), "income": ("Cowork Income Reconciliation", "CIR"),
              "vat": ("Cowork VAT Check", "CVC")}


def status(job_id):
    job_id = str(job_id or "")
    current = frappe.cache.get_value(_key(job_id))
    if current and current.get("user") == frappe.session.user:
        if current.get("state") in ("queued", "running") and _job_ended(job_id):
            # 后台任务已经没了（部署 / 重启时被判弃置、移进失败队列），缓存还停在「进行中」：页面会一直转圈
            # （2026-09-29 测试机同步后 5 套对账一直显示 running）。结果落了库就算完成，否则报失败。
            return _recovered(job_id) or {"kind": current.get("kind"), "state": "failed", "stage": "failed",
                                          "task": None, "error": _("reconciliation.system_busy")}
        return {key: current.get(key) for key in ("kind", "state", "stage", "task", "error")}
    recovered = _recovered(job_id)
    if not recovered:
        frappe.throw(_("reconciliation.task_not_found"), frappe.DoesNotExistError)
    return recovered


def _job_ended(job_id):
    """后台任务已结束或已被弃置（失败、停止、取消）；查不到任务时不下结论。"""
    from frappe.utils.background_jobs import get_job

    job = get_job(f"cowork-recon-{job_id}")
    return bool(job) and job.get_status(refresh=False) in ("failed", "stopped", "canceled")


def _recovered(job_id):
    """缓存里的状态没了（部署迁移、clear-cache 会清掉；2026-09-29 压测：页面显示任务不存在，后台照跑、结果照落库）。

    结果已落库：按同一规则算出任务名直接找到。还在队列里：看后台任务本身（只认本人发起的）。都没有就是不存在。
    """
    if not job_id or len(job_id) > 64:
        return None
    import hashlib

    key = hashlib.sha256(f"{frappe.local.site}|{frappe.session.user}|{job_id}".encode()).hexdigest()
    for kind, (doctype, prefix) in TASK_NAMES.items():
        name = f"{prefix}-{key[:40]}"
        if frappe.db.exists(doctype, name):
            return {"kind": kind, "state": "done", "stage": "done", "task": name, "error": None}
    from frappe.utils.background_jobs import get_job

    job = get_job(f"cowork-recon-{job_id}")
    arguments = (job.kwargs or {}) if job else {}
    if not job or arguments.get("user") != frappe.session.user:
        return None
    kind = (arguments.get("kwargs") or {}).get("kind")
    state = job.get_status(refresh=False)
    if state in ("queued", "deferred", "scheduled"):
        return {"kind": kind, "state": "queued", "stage": "queued", "task": None, "error": None}
    if state == "started":
        return {"kind": kind, "state": "running", "stage": "reading", "task": None, "error": None}
    return {"kind": kind, "state": "failed", "stage": "failed", "task": None, "error": _("reconciliation.failed")}


def _names(files, limit=140):
    """几份文件存一个名字：放得下就全列，放不下写「第一份 +其余份数」（21 张税报照片的名字连起来超过 140 字，
    识别费都付了、最后存档时整单失败；2026-09-30 压测第 19 轮 I01）。"""
    joined = "、".join(name for name, _content in files)
    if len(joined) <= limit:
        return joined
    suffix = f" +{len(files) - 1}"
    return files[0][0][:limit - len(suffix)] + suffix


def _read(names):
    from pearnly_cowork.recon_center import storage

    return [storage.read(name) for name in names]


def run(recon_job, kind, workspace, title, files, options="{}"):
    options = json.loads(options or "{}")
    # 后台任务按提交时选的语言写错误与提示（默认是站点语言：2026-09-29 压测 lang=zh 提交，失败提示是英文 /
    # 泰文）。
    if options.get("lang") in ("th", "zh", "en", "ja"):
        frappe.local.lang = options["lang"]
        # Frappe 16 在后台任务里第一次翻译时按任务用户的语言重设 frappe.local.lang（`lang_resolved` 标记），会把上面
        # 这句盖掉：失败提示照样是泰文（2026-09-30 压测 BBL-CA-01，lang=zh）。标成已定，不再重设。
        job = getattr(frappe.local, "job", None)
        if job is not None and hasattr(job, "lang_resolved"):
            job.lang_resolved = True
    stage = lambda name: _state(recon_job, state="running", stage=name)  # noqa: E731
    try:
        stage("reading")
        lefts, rights = _read(files["left"]), _read(files["right"])
        request_id = recon_job
        if kind == "vat":
            from pearnly_cowork.recon_center import vat_check

            result = vat_check.run(workspace, title, request_id, lefts, rights, options.get("lang") or "th",
                                   on_stage=stage, invoice_files=files["right"], recon_job=recon_job,
                                   report_files=files["left"])
        elif kind == "bank":
            from pearnly_cowork.pearnly_cowork.doctype.cowork_bank_reconciliation.cowork_bank_reconciliation import (
                document_model_rows,
            )
            from pearnly_cowork.ocr.enterprise import text_layer
            from pearnly_cowork.recon_center import document_ocr, sources

            account = str(options.get("gl_account_code") or "")
            # 扫描件 / 照片 / 要模型兜底的 PDF：每份文件一个持久识别任务（按读到的是哪一份文件找它存的 File）。
            stored = [(content, file_name, side) for side, loaded in (("left", lefts), ("right", rights))
                      for (_name, content), file_name in zip(loaded, files[side])]

            def read(document_type, content, filename):
                # 带文字层的电子 PDF 先按文字层的坐标拼行（不花钱）；余额链算得平就用，否则才付费识别
                # （2026-09-30 压测 B02 TTB：29 页电子账单每次都付费识别，负载下读超时）。
                free = text_layer.read(content, document_type)
                if free is not None:
                    return free
                index, (_content, file_name, side) = next((index, item) for index, item in enumerate(stored)
                                                          if item[0] is content)
                return document_ocr.reader(recon_job, f"{side}{index}", file_name, frappe.session.user)(
                    document_type, content, filename)

            # 照片先全部提交识别，识别进程并行读，再按顺序取结果（旧线上一侧多份 4 份并行；一张一张提交再等，18 张
            # 存折照片要排很久，2026-09-29 压测）。同一参考号、同样参数的提交返回同一个识别任务，读取层再提交不会重复付费；
            # PDF 不预先提交：带文字层的银行 PDF 先走免费解析。
            names = [name for loaded in (lefts, rights) for name, _content in loaded]
            for index, ((content, file_name, side), name) in enumerate(zip(stored, names)):
                if name.rsplit(".", 1)[-1].lower() in sources.IMAGES:
                    document_ocr.submit(recon_job, f"{side}{index}", file_name, name, content,
                                        "bank_statement" if side == "left" else "general_ledger", frappe.session.user)
            frappe.db.commit()
            (left_name, left), (right_name, right) = lefts[0], rights[0]
            result = bank_recon_service.create_from_documents(workspace, title, request_id, left, left_name,
                right, right_name, gl_account_code=account, model_rows=document_model_rows(account, read=read),
                lang=options.get("lang") or "th", on_stage=stage,
                statement_files=[(content, name) for name, content in lefts],
                gl_files=[(content, name) for name, content in rights])
        else:
            from pearnly_cowork.recon_center import document_ocr, sources

            (left_name, left), (right_name, right) = lefts[0], rights[0]
            stage("ledger")
            gl_source = sources.ledger_source(left, left_name,
                document_ocr.reader(recon_job, "left", files["left"][0], frappe.session.user))
            stage("report")
            if len(rights) == 1:
                vat_source = sources.report_source(right, right_name,
                    document_ocr.reader(recon_job, "right", files["right"][0], frappe.session.user))
                stage("matching")
                result = income_recon_service.create_from_parsed(workspace, title, request_id, gl_source,
                    vat_source=vat_source, gl_filename=left_name, vat_filename=right_name)
            else:
                from pearnly_cowork.recon_center import vat_check

                vat_rows, warnings, tax_id = vat_check.merged_report_rows(recon_job, rights, files["right"])
                stage("matching")
                result = income_recon_service.create_from_parsed(workspace, title, request_id, gl_source,
                    vat_rows=vat_rows, gl_filename=left_name, vat_filename=_names(rights),
                    report_warnings=warnings, tax_id=tax_id)
        frappe.db.commit()
        _state(recon_job, state="done", stage="done", task=result["name"])
    except Exception as error:
        frappe.db.rollback()
        from pearnly_cowork.reconciliation.messages import error_text

        # 每次失败都记日志：共用一句提示的校验错误原来不记，压测时查不到真正原因（B01，2026-09-29）。数据库锁 / 超时
        # 这类系统问题单独提示稍后重试，不说成文件格式不对。
        frappe.log_error(title="cowork reconciliation job", message=frappe.get_traceback())
        system = isinstance(error, (frappe.QueryTimeoutError, frappe.QueryDeadlockError)) or not isinstance(
            error, (frappe.ValidationError, ValueError))
        message = frappe._("reconciliation.system_busy") if system else error_text(error)
        _state(recon_job, state="failed", stage="failed", error=frappe.utils.strip_html(message)[:500])
    finally:
        # 一份一提交：删除时的文件保护检查会锁住查到的来源行，二十张照片放在一个事务里删，并发的另一个对账
        # 就会等锁超时（2026-09-29 压测）。结果已提交，清理失败只记日志、不把任务改成失败；没删掉的临时文件
        # 是本人私有、不挂单据，按日志里的文件名处理。
        for name in files["left"] + files["right"]:
            try:
                if frappe.db.exists("File", name):
                    frappe.delete_doc("File", name, ignore_permissions=True, force=True)
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="cowork reconciliation cleanup", message=frappe.get_traceback())
