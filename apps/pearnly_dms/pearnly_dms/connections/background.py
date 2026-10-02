"""连接页「真试一次再存」放后台跑，页面推送优先、轮询兜底（2026-10-02）。

读档 / 改写账号保存前要按用途真登一次 DMS（Playwright，账号锁最长等 180 秒，再加登录与核对权限），在网页请求里同步做会
撞 gunicorn 的 120 秒上限（也挡在将来开 Cloudflare 代理的 100 秒前面）。现在请求只做权限与必填检查、排一个任务就返回；
任务在 ``long`` 队列以发起人的身份跑原来的那段逻辑（`api._set_read_account` / `api._save_write_accounts`），结果或失败原因
写进不淘汰的状态存储并推给发起人。

**密码不进队列**：任务参数会原样留在 Redis 的任务记录里（失败的还会留更久），所以账号密码用站点密钥加密后单独存 15 分钟，
任务读到就删；查状态的接口不回它。
"""
import json
import re

import frappe
from frappe import _

PREFIX = "dms:account-check:"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EXPIRES = 900
EVENT = "pearnly_dms_account_check"
JOB_TIMEOUT = 600
KINDS = {"read": "pearnly_dms.connections.api._set_read_account",
         "write": "pearnly_dms.connections.api._save_write_accounts"}


def _store():
    """不淘汰的状态存储（``pearnly_core.execution.state``；用到时才导入，离线检查导入本模块不需要共用层）。"""
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _key(token, part="state", user=None):
    return f"{PREFIX}{user or frappe.session.user}:{token}:{part}"


def _write(token, value):
    _store().set_value(_key(token), value, expires_in_sec=EXPIRES)


def _read(token):
    value = _store().get_value(_key(token))
    return value if isinstance(value, dict) else {}


def _push(token, state):
    try:
        frappe.publish_realtime(EVENT, {"token": token, "state": state}, user=frappe.session.user, after_commit=False)
    except Exception:
        pass  # 推不了页面照样靠轮询


def start(kind, token, secrets):
    """排任务就返回 ``{"queued": 号}``；``secrets``（含密码）加密后单独存，不进任务参数。"""
    from frappe.utils.password import encrypt

    if kind not in KINDS:
        raise ValueError(kind)
    if not isinstance(token, str) or not TOKEN.match(token):
        frappe.throw(_("The check could not start. Please try again."))
    job_id = f"dms-account-check:{frappe.session.user}:{token}"
    _store().set_value(_key(token, "secret"), encrypt(json.dumps(secrets)), expires_in_sec=EXPIRES)
    _write(token, {"state": "queued", "job": job_id})
    frappe.enqueue("pearnly_dms.connections.background.run", queue="long", timeout=JOB_TIMEOUT, job_id=job_id,
                   deduplicate=True, kind=kind, token=token, user=frappe.session.user, lang=frappe.local.lang)
    return {"queued": token}


def run(kind, token, user, lang):
    from frappe.utils.password import decrypt

    frappe.set_user(user)
    frappe.local.lang = lang
    store = _store()
    sealed = store.get_value(_key(token, "secret"))
    store.delete_value(_key(token, "secret"))
    try:
        if not sealed:
            frappe.throw(_("The check could not start. Please try again."))
        result = frappe.get_attr(KINDS[kind])(**json.loads(decrypt(sealed)))
    except frappe.ValidationError as error:  # 给人看的原因（账号被占用、缺权限、必填…）原样转给页面
        frappe.db.rollback()
        outcome = {"state": "failed", "message": str(error) or _("The check failed. Please try again.")}
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title=f"DMS {kind} account check failed")
        outcome = {"state": "failed", "message": _("The check failed. Please try again.")}
    else:
        frappe.db.commit()
        outcome = {"state": "done", "result": result}
    _write(token, {**_read(token), **outcome})
    _push(token, outcome["state"])


def status(token):
    """``{state, result | message}``；任务已不在（进程重启、超时）却没写结果的按失败回，页面不会一直等。"""
    if not isinstance(token, str) or not TOKEN.match(token):
        return {}
    value = _read(token)
    if value.get("state") == "queued" and value.get("job") and not _job_alive(value["job"]):
        value = _read(token)
        if value.get("state") == "queued":
            value = {**value, "state": "failed", "message": _("The check stopped before it finished. Please try again.")}
    value.pop("job", None)
    return value


def _job_alive(job_id):
    from frappe.utils.background_jobs import get_job

    job = get_job(job_id)
    return bool(job) and job.get_status(refresh=False) in ("queued", "started", "deferred", "scheduled")
