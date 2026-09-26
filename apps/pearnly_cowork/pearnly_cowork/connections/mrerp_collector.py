"""MR.ERP 账套目录的服务端采集：登录 MR.ERP、读选公司页、发布「公司 → 年度」目录快照。

来源（旧线上最后版本 `ec2e5c957e6b`，旧云 2026-09-24 退役后冻结）：
  · `services/erp/mrerp_http/session.py` —— 登录三步、选公司页、试选账套并抓 idus（凭据真伪
    只有进 mainmenu 才验得出来：选公司页任何会话都能打开）；
  · `services/erp/erp_mrerp_listing.py::test_mrerp_endpoint` —— 列账套后用第一个试选、错误码；
  · `services/erp/mrerp_target_projection.py::_run_live` —— 网络类失败隔 2 秒重试一次；
  · `services/erp/target_refresh.py::process_mrerp_request` + `mrerp_refresh_worker.py` ——
    租约 180 秒、最多领 3 次；完成时核对租约仍是自己的、配置没变，期间又被申请就重排；
  · `services/erp/ssrf_guard.py` —— 用户可填的系统地址只许解析到公网。

只处理整站（`@endpoint`）目录：选定账套那一层要列商品/客户，旧版走 Playwright 适配器，
那一层本仓未迁，按范围申请会明确失败（`ERR_ACCOUNT_SCOPE_UNSUPPORTED`），不假装成功。
与旧版的差异：对外请求期间不持有任何行锁（租约一笔提交、完成一笔提交）；跳转不许离开原站点；
公司按选公司页的表格行分组（见 `mrerp_catalog`）。已知残留同旧版：DNS rebinding 不在本层覆盖。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import frappe
from frappe.utils.password import get_decrypted_password
from pearnly_core.session_lock import SessionLockUnavailable, account_lock
from pearnly_cowork.targets.projection import ProjectionContractError, normalize_projection
from . import access, model, mrerp_catalog, refresh, snapshots, state
from .mrerp_session import MrErpSession, lock_key, public_host  # noqa: F401 —— public_host 仍是本模块对外名
from .mrerp_session import SessionError as CollectError

logger = logging.getLogger(__name__)

LEASE_SECONDS = 180
LEASE_OWNER = "server:mrerp"
CLAIM_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 2.0
LOCK_WAIT_SECONDS = 120  # 等推送释放同账号的会话；短于采集租约
_DEFAULT_URL = "https://www.mrerp4sme.com"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 调度 ──────────────────────────────────────────────────────────────────


def dispatch(result):
    """刷新申请落库后排一个采集任务；同一申请只排一次，提交后才可见。"""
    request_id = (result or {}).get("request_id")
    if not request_id or result.get("status") != "requested":
        return
    row = frappe.db.get_value(state.REFRESH, request_id, ["adapter", "connection"], as_dict=True)
    if not row or row.adapter != "mrerp":
        return
    frappe.enqueue("pearnly_cowork.connections.mrerp_collector.run", queue="short",
                   job_id=f"cowork-mrerp-catalog::{request_id}", deduplicate=True,
                   enqueue_after_commit=True, timeout=LEASE_SECONDS + 60,
                   connection=row.connection, request_id=request_id)


def run(connection, request_id):
    """后台任务入口：领租约 → 不持锁读 MR.ERP → 围栏校验后发布。"""
    for _ in range(CLAIM_ATTEMPTS):
        lease = _lease(connection, request_id)
        frappe.db.commit()
        if lease is None:
            return
        result = collect(lease)
        again = _complete(connection, request_id, lease, result)
        frappe.db.commit()
        if not again:
            return


def _lease(connection, request_id):
    doc = state.read(state.CONNECTION, connection)
    now = _now()
    refresh.expire(doc, now)
    row = state.read(state.REFRESH, request_id)
    heads = state.heads(doc)
    _, _, head_key = state.scope(None if row.scope_kind == "endpoint" else row.scope_key, doc.adapter)
    if (row.connection != doc.name or row.adapter != "mrerp" or doc.adapter != "mrerp"
            or row.status != "requested" or heads.get(head_key, {}).get("request_id") != row.name):
        return None
    if not doc.enabled:
        refresh.fail(doc, row, "ERR_CONNECTION_DISABLED", now)
        return None
    if row.scope_kind != "endpoint":
        refresh.fail(doc, row, "ERR_ACCOUNT_SCOPE_UNSUPPORTED", now)
        return None
    row.status, row.started_at = "leased", now
    row.lease_handle, row.lease_owner, row.lease_generation = str(uuid4()), LEASE_OWNER, 0
    row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    row.completed_at, row.error_code, row.result_revision = None, None, None
    access.write(row)
    config = model.parse(doc.config_json, {})
    secret = {key: get_decrypted_password(state.CONNECTION, doc.name, "credential_" + key,
                                          raise_exception=False) or ""
              for key in ("username", "password")}
    return {"handle": row.lease_handle, "revision": int(doc.record_revision),
            "system_url": str(config.get("system_url") or _DEFAULT_URL).strip(), **secret}


def _complete(connection, request_id, lease, result):
    """返回要不要再领一次：采集期间又被申请（或租约过期被重排）就重读，其余都已有结论。"""
    doc = state.read(state.CONNECTION, connection)
    now = _now()
    refresh.expire(doc, now)  # 配置改过 / 租约过期的，在这里先落结论
    row = state.read(state.REFRESH, request_id)
    if row.status == "requested":
        return True  # 本次结果作废（按旧版：期间有新申请就重排，不发布旧观察）
    if (row.status != "leased" or row.lease_handle != lease["handle"]
            or int(doc.record_revision) != lease["revision"]):
        return False  # 已被新申请顶替或配置已改：丢弃本次读到的目录
    if not doc.enabled:
        refresh.fail(doc, row, "ERR_CONNECTION_DISABLED", now)
        return False
    error, revision = result.get("error_code"), None
    if not error:
        try:
            normalized = normalize_projection(mrerp_catalog.observation(result["observed_at"], result["rows"]))
        except ProjectionContractError as exc:
            error = "ERR_CATALOG_INVALID:" + exc.code
        else:
            if not normalized.account_sets:
                error = "ERR_ACCOUNT_SET_EMPTY"
            elif row.requested_at <= row.started_at:
                revision = snapshots.publish(doc, normalized, request_id=row.name)["revision"]
    refresh.finish(doc, row, revision=revision, error=error, now=now)
    return row.status == "requested"


# ── 对外读取（不碰数据库） ────────────────────────────────────────────────


def collect(lease):
    """登录并列账套；网络类失败隔 2 秒重试一次。永不抛：失败写成错误码。"""
    result = {}
    for attempt in range(2):
        result = _collect_once(lease)
        if not result.get("error_code") or result["error_code"] not in mrerp_catalog.TRANSIENT_ERRORS:
            return result
        if attempt == 0:
            time.sleep(RETRY_DELAY_SECONDS)
    return result


def _collect_once(lease):
    if not lease.get("username") or not lease.get("password"):
        return {"error_code": "ERR_NO_CREDS"}
    # 与推送共用同一 MR.ERP 账号：老 PHP 单账号单会话，并发登录会把正在导入的推送踢掉。
    try:
        with account_lock(lock_key(lease["system_url"], lease["username"]), timeout_sec=LOCK_WAIT_SECONDS):
            return _collect_locked(lease)
    except SessionLockUnavailable:
        return {"error_code": "ERR_TECHNICAL"}


def _collect_locked(lease):
    observed_at = datetime.now(timezone.utc)
    try:
        session = MrErpSession(lease["system_url"], lease["username"], lease["password"])
    except CollectError as exc:
        return {"error_code": exc.code}
    try:
        rows = session.account_sets()
        if rows:
            # 选公司页任何会话都能列出账套，只有真进一个账套才验得出账号密码（旧版同法）。
            session.select(rows[0]["comidyear"], rows[0]["seldb"])
        return {"rows": rows, "observed_at": observed_at}
    except CollectError as exc:
        logger.info("MR.ERP catalog collection failed: %s", exc)
        return {"error_code": exc.code}
    except Exception:  # noqa: BLE001 — 契约：采集失败只落错误码，不冒到任务队列
        logger.exception("MR.ERP catalog collection raised")
        return {"error_code": "ERR_UNEXPECTED"}
    finally:
        session.close()
