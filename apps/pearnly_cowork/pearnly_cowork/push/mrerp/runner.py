"""MR.ERP 推送的服务端执行：确认后排一个任务，领取 → 导入 → 回执，都经原生推送账本。

来源：旧线上 ec2e5c957e6b · `services/erp/erp_push.py::push_mrerp_history`（整理票面 → 读映射 →
本方税号锚点 → 登录 → 按票分流上传 → 逐行回执）+ `services/erp/confirmed_push_reservation.py`
（确认后的结果判定：`push.lifecycle.reserved_result` 已迁同一判据）。

与旧版的差异（新平台约束）：
  · 旧版在网页请求里同步推；这里确认后排后台任务（`short` 队列，同一记录只排一次、提交后可见），
    领取 / 回执走账本的围栏与写者锁，同一连接串行；一条结束后接着排同连接下一条待发送的记录；
  · 对外请求期间不持任何行锁（领取一笔提交、回执一笔提交）；
  · 失败分得更细：导入确认（importpc）**没发出**时的任何失败都确定没写入 → 转人工并释放锁；
    **发出之后**的异常才是结果不明（保留写者锁、不自动重推）。旧版技术类失败一律按不明处理；
  · 「发票号已存在」按已推过记成功（旧 `skipped_dup` 中性态；单号由票据 id 确定性派生，同票同号）；
  · 不自动重试（旧版 MR.ERP 同样不重试）；银行流水佐证未接（付款判定仍按人工选择 → 票面）。
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

from pearnly_core.session_lock import SessionLockUnavailable
from pearnly_cowork.connections import service as connections
from pearnly_cowork.push.express import common
from pearnly_cowork.push.mrerp.models import MRERPAuthError, MRERPBusinessError, MRERPTechnicalError
from pearnly_cowork.push.mrerp.transport import MrErpImport
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.native.contracts import ReservationContext
from pearnly_cowork.push.retry import is_already_pushed_error, is_user_data_error

logger = logging.getLogger(__name__)

OWNER = "server:mrerp"
RECORD = "Cowork Push Record"
JOB_TIMEOUT_SECONDS = 600


def dispatch(record):
    """确认后排一个执行任务；同一记录只排一次，提交后才可见。"""
    frappe.enqueue("pearnly_cowork.push.mrerp.runner.run", queue="short",
                   job_id=f"cowork-mrerp-push::{record}", deduplicate=True,
                   enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, record=record)


def dispatch_next(connection):
    """同一连接串行：一条结束后接着排最早一条待发送的 MR.ERP 记录。"""
    rows = frappe.db.sql("""SELECT name, target_json FROM `tabCowork Push Record`
        WHERE connection=%s AND status='pending' AND COALESCE(result_unknown,0)=0
        ORDER BY created_at, name LIMIT 20""", (connection,), as_dict=True)
    for row in rows:
        if (json.loads(row.target_json or "{}").get("adapter")) == "mrerp":
            dispatch(row.name)
            return


def sweep():
    """定时巡检（每 5 分钟）：队列丢了任务（worker / Redis 重启）时把待发送的重新排上；执行中途进程死掉、
    租约已过期的，按账本规则转人工 + 结果不明（`recover_expired`，保留写者锁、不自动重推）。"""
    expired = frappe.db.sql("""SELECT name FROM `tabCowork Push Record`
        WHERE status='leased' AND lease_owner=%s AND lease_expires_at<=%s""", (OWNER, ledger.now_utc()), as_dict=True)
    for item in expired:
        row = frappe.get_doc(RECORD, item.name)
        ledger.recover_expired(row.name, context=_context(row),
                               adapter=ledger.trusted_adapter_context(row.connection, OWNER, 0))
        frappe.db.commit()
    for connection in frappe.db.sql_list("""SELECT DISTINCT connection FROM `tabCowork Push Record`
            WHERE status='pending' AND COALESCE(result_unknown,0)=0"""):
        dispatch_next(connection)


def _context(row):
    return ReservationContext(row.actor, row.workspace, row.client or "", row.history,
        int(row.history_version), row.connection, row.account_key, branch=row.branch,
        binding_generation=int(row.binding_generation or 0), connection_revision=int(row.connection_revision))


def run(record):
    """后台任务入口。只处理 MR.ERP 的待发送记录；其它状态直接返回（重复排队无副作用）。"""
    row = frappe.db.get_value(RECORD, record, ["name", "status", "result_unknown", "target_json", "actor",
        "workspace", "client", "history", "history_version", "connection", "account_key", "branch",
        "binding_generation", "connection_revision"], as_dict=True)
    if not row or row.status != "pending" or row.result_unknown:
        return
    target = json.loads(row.target_json or "{}")
    if target.get("adapter") != "mrerp":
        return
    # 以准备这张票的人执行：读凭据、核权限都按他当前的授权（撤了权就领不到）。
    frappe.set_user(row.actor)
    context, adapter = _context(row), ledger.trusted_adapter_context(row.connection, OWNER, 0)
    try:
        claim = ledger.lease(record, context=context, adapter=adapter)
        if not claim["leased"]:
            frappe.db.commit()  # 已推过被转人工，或等写者锁（持锁那条结束后会接着排）
            return
        loaded = connections.read_for_push(row.connection, row.workspace, row.client, actor=row.actor)
        secrets = connections.credentials_for_use(loaded)
        system_url = (loaded.endpoint.get("config") or {}).get("system_url") or ""
    except ledger.LeaseUnavailable:
        frappe.db.rollback()
        return
    except (ledger.DispatchRejected, frappe.PermissionError) as error:
        frappe.db.rollback()
        reason = error.code if isinstance(error, ledger.DispatchRejected) else "companion.queue_access_revoked"
        ledger.quarantine_pending(record, context=context, adapter=adapter, reason=reason)
        frappe.db.commit()
        dispatch_next(row.connection)
        return
    frappe.db.commit()  # 领取落定、行锁释放，之后才对外请求

    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    outcome = execute(claim["payload"], target, secrets, system_url, today)
    fence, token = claim["fence"], claim["attempt_token"]
    if outcome["status"] == "unknown":
        ledger.mark_unknown(record, context=context, adapter=adapter, fence=fence, attempt_token=token,
                            reason=outcome["reason"])
    else:
        success = outcome["status"] == "success"
        ledger.ack(record, context=context, adapter=adapter, fence=fence, attempt_token=token, success=success,
                   outcome=common.STAGE_SUCCESS if success else common.STAGE_NEEDS_REVIEW,
                   express_docnum=outcome.get("docnum"), error=None if success else outcome["reason"],
                   meta=outcome.get("meta"))
    frappe.db.commit()
    dispatch_next(row.connection)


def execute(payload, target, secrets, system_url, today):
    """一次 MR.ERP 导入（纯网络，不碰数据库）→ {status: success|failed|unknown, reason, docnum, meta}。"""
    comidyear, _, seldb = str(target.get("account_set") or "").partition(":")
    importer = None
    try:
        importer = MrErpImport(system_url=system_url, username=secrets.get("username") or "",
                               password=secrets.get("password") or "", comidyear=comidyear, seldb=seldb)
        with importer:
            result = importer.upload_routed_batch(deepcopy(payload.get("histories") or []),
                                                  deepcopy(payload.get("mappings") or {}), today=today)
    except (MRERPAuthError, MRERPBusinessError, MRERPTechnicalError, SessionLockUnavailable) as error:
        code = {MRERPAuthError: "ERR_AUTH", MRERPBusinessError: "ERR_BUSINESS",
                MRERPTechnicalError: "ERR_TECHNICAL"}.get(type(error), "ERR_SESSION_LOCK")
        text = str(error)
        return _exception(importer, text if text.startswith(code) else f"{code}: {text}")
    except Exception as error:  # noqa: BLE001 — 契约：任何异常都要落到账本，不冒到队列
        logger.exception("MR.ERP push raised")
        return _exception(importer, f"ERR_UNEXPECTED: {type(error).__name__}: {error}")
    meta = {"elapsed_ms": result.elapsed_ms, "xlsx_size_bytes": result.xlsx_size_bytes}
    if result.success:
        return {"status": "success", "docnum": result.success[0].mrerp_bill_no, "reason": "", "meta": meta}
    failed = result.failed[0] if result.failed else None
    reasons = failed.reasons if failed else ["ERR_UNKNOWN_UPLOAD_OUTCOME"]
    text = "; ".join(str(r) for r in reasons)[:500]
    if failed and is_already_pushed_error(text):
        # 单号由票据 id 确定性派生：MR.ERP 说这个号已存在 = 这张票之前已推进去了。
        return {"status": "success", "docnum": failed.invoice_no, "reason": "",
                "meta": {**meta, "already_pushed": True}}
    return {"status": "failed", "reason": text, "meta": {**meta, "user_data": is_user_data_error(text)}}


def _exception(importer, reason):
    sent = bool(importer and importer.import_sent)
    return {"status": "unknown" if sent else "failed", "reason": reason[:500]}
