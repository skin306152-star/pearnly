"""ERP 采购 / 销售网页识别的进度与结果（用户 2026-09-30：「录入工作台按页分进度，不要按份，ERP 那边一样」）。

网页识别在后台跑（`recognition.background`，2026-10-02：同步请求读多页 PDF 会超过 gunicorn 120 秒）。页面发起时带一个随机号，
后台每读完一页写一次「已读页数 / 总页数」，读完写结果或失败原因，并推给发起人；页面收到推送就读，轮询只作兜底。按当前用户隔离、1 小时过期。
存在不淘汰的状态存储（`pearnly_core.execution.state`）：结果丢了页面就会一直等，不能放会被挤掉的 redis-cache。
"""
import re

import frappe

PREFIX = "erp:recognise-pages:"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EXPIRES = 3600


def valid(token):
    return isinstance(token, str) and bool(TOKEN.match(token))


def _key(token, user=None):
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store(), f"{PREFIX}{user or frappe.session.user}:{token}"


def _write(token, value):
    store, key = _key(token)
    store.set_value(key, value, expires_in_sec=EXPIRES)  # 1 小时后自动删，不常留


EVENT = "pearnly_erp_recognise"


def _push(payload):
    """推给发起人（原生实时推送）：页面收到就更新页数 / 立刻读结果，轮询只作兜底（同 Cowork 录入，`intake-upload.js`）。推不了照样靠轮询。"""
    try:
        frappe.publish_realtime(EVENT, payload, user=frappe.session.user, after_commit=False)
    except Exception:
        pass


def _read(token):
    store, key = _key(token)
    value = store.get_value(key)
    return value if isinstance(value, dict) else {}


def reporter(token):
    """发起识别时带的号 → ``(已读页数, 总页数)`` 的回报函数；没带或格式不对就不回报。"""
    if not valid(token):
        return None

    def report(done, total):
        try:
            _write(token, {**_read(token), "state": "running", "done": int(done), "total": int(total)})
        except Exception:
            pass  # 页数只作展示
        _push({"token": token, "done": int(done), "total": int(total)})
    return report


def queued(token, job_id):
    _write(token, {"state": "queued", "job": job_id})


def finish(token, outcome):
    """``outcome``：``{"state": "done", "result": …}`` 或 ``{"state": "failed", "message": …}``。写不进要报出来（页面会一直等）。"""
    _write(token, {**_read(token), **outcome})
    _push({"token": token, "state": outcome["state"]})


def read(token):
    """``{state, done, total, result | message}``；还没发起或号不对是空。后台任务已经不在（进程重启、超时）却没写结果的，
    按失败回，页面不会一直等。"""
    if not valid(token):
        return {}
    value = _read(token)
    if value.get("state") in ("queued", "running") and value.get("job") and not _job_alive(value["job"]):
        value = {**_read(token)}  # 任务刚写完结果的竞态：再读一次
        if value.get("state") in ("queued", "running"):
            from frappe import _

            value = {**value, "state": "failed", "message": _("Recognition stopped before it finished. Please try again.")}
    return value


def _job_alive(job_id):
    from frappe.utils.background_jobs import get_job

    job = get_job(job_id)
    return bool(job) and job.get_status(refresh=False) in ("queued", "started", "deferred", "scheduled")
