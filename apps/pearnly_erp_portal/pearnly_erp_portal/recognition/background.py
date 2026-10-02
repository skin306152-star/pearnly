"""网页「上传识别」放后台跑，页面按号轮询结果（2026-10-02）。

原先 `api_recognise_sales` / `api_recognise_purchase` 在网页请求里逐页读：多页 PDF 最多 20 页、每页最多几次模型调用，单次最长
120 秒，会撞 gunicorn 的 120 秒超时（也挡在将来开 Cloudflare 代理的 100 秒前面）。现在请求只排一个任务就返回；任务在 `ocr` 队列
（ERP 识别进程，内存按识别配）以发起人的身份跑同一个 `recognise`，每读完一页写进度，读完写结果（`recognition.progress`）。
LINE 收单本来就在队列里，不经这里。
"""
import frappe
from frappe import _

from pearnly_erp_portal.recognition import policy, progress

MODULES = {"sales": "pearnly_erp_portal.sales.recognition", "purchase": "pearnly_erp_portal.purchasing.recognition"}
JOB_TIMEOUT = 1800  # 20 页 × 每页最多几次调用；正常几十秒


def start(kind, token, **arguments):
    if kind not in MODULES:
        raise ValueError(kind)
    if not progress.valid(token):
        frappe.throw(_("Recognition could not start. Please try again."))
    job_id = f"erp-recognise:{frappe.session.user}:{token}"  # Frappe 再按站点加前缀（`create_job_id`）；`progress.read` 用同一个号查任务
    progress.queued(token, job_id)
    frappe.enqueue("pearnly_erp_portal.recognition.background.run", queue="ocr", timeout=JOB_TIMEOUT, job_id=job_id,
                   deduplicate=True, kind=kind, token=token, user=frappe.session.user, lang=frappe.local.lang,
                   arguments=arguments)
    return {"queued": token}


def run(kind, token, user, lang, arguments):
    frappe.set_user(user)
    frappe.local.lang = lang
    recognise = frappe.get_attr(MODULES[kind] + ".recognise")
    try:
        result = policy.for_web(lambda: recognise(**arguments, progress=progress.reporter(token)))
    except frappe.ValidationError as error:  # 给人看的原因（不是发票、余额不足、忙、权限…）原样转给页面
        frappe.db.rollback()
        progress.finish(token, {"state": "failed", "message": str(error) or _("Recognition failed. Please try again.")})
        return
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title=f"ERP {kind} recognition failed")
        progress.finish(token, {"state": "failed", "message": _("Recognition failed. Please try again.")})
        return
    frappe.db.commit()
    progress.finish(token, {"state": "done", "result": result})
