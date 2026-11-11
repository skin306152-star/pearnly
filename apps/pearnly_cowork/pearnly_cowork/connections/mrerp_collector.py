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
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import frappe
from frappe.utils.password import get_decrypted_password
from pearnly_cowork.targets.projection import ProjectionContractError, normalize_projection
from pearnly_core.mrerp import catalog as mrerp_catalog
from . import access, model, refresh, snapshots, state
from pearnly_core.mrerp.session import public_host  # noqa: F401 —— public_host 仍是本模块对外名
# 登录、列账套、补期间与税号（纯网络）在共用层（2026-11-03 搬去，ERP 接 MR.ERP 同用一份）；本模块对外名 `collect` 照旧。
from pearnly_core.mrerp.collect import collect  # noqa: F401

logger = logging.getLogger(__name__)

LEASE_SECONDS = 180
LEASE_OWNER = "server:mrerp"
CLAIM_ATTEMPTS = 3
_DEFAULT_URL = "https://www.mrerp4sme.com"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 调度 ──────────────────────────────────────────────────────────────────


def dispatch(result, *, manual):
    """刷新申请落库后排一个采集任务；同一申请只排一次，提交后才可见。

    ``manual``：人点的（网页 / CC 的申请接口 `api.request_refresh`，11-11）—— 采集时先清被踢标记、这次照常登录；由调用入口显式给。"""
    request_id = (result or {}).get("request_id")
    if not request_id or result.get("status") != "requested":
        return
    row = frappe.db.get_value(state.REFRESH, request_id, ["adapter", "connection"], as_dict=True)
    if not row or row.adapter != "mrerp":
        return
    frappe.enqueue("pearnly_cowork.connections.mrerp_collector.run", queue="short",
                   job_id=f"cowork-mrerp-catalog::{request_id}", deduplicate=True,
                   enqueue_after_commit=True, timeout=LEASE_SECONDS + 60,
                   connection=row.connection, request_id=request_id, manual=bool(manual))


def run(connection, request_id, manual=False):
    """后台任务入口：领租约 → 不持锁读 MR.ERP → 围栏校验后发布。"""
    for _ in range(CLAIM_ATTEMPTS):
        lease = _lease(connection, request_id)
        frappe.db.commit()
        if lease is None:
            return
        # 后台任务自己不算人点的：只有排任务的入口说了是才算，而且只在第一次领到时清标记（旧任务没带这个参数照不算）
        result = collect(lease, manual=bool(manual))
        manual = False
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
