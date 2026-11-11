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
  · 成功只认回读（2026-09-28 有意变化，修 MR.ERP 假成功）：导入受理后在所选菜单列表里查到才成功，回执记 MR.ERP
    实际单号；「发票号已存在」也要在所选菜单查到才算已推过；受理了却查不到 = 结果不明（保留写者锁、不自动重推，
    同导入确认发出后异常）。菜单定不下来（要人选 / 选过的失效 / 没有已关联日记账的）在导入前停下、转人工；
    这次读到的菜单选项存回连接，给选择入口用；
  · 不自动重试（旧版 MR.ERP 同样不重试）；银行流水佐证未接（付款判定仍按人工选择 → 票面）；
  · 交给代录的记录（``entry_mode=operator``，ADR-026 第 1 条，2026-11-03）不领、不排：由选定电脑的 CC 桌面版在网页里录；
  · 同账号互斥（ADR-026 第 8 条，2026-11-02）：账号正被 CC 桌面版代录占着时不领、照旧待发送（`account_busy`），
    不算失败、不转人工；代录放开账号（或电脑不在、租约过期）后由定时巡检再排。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe

from pearnly_core.express_operator.contracts import is_operator
from pearnly_core.mrerp.session import lock_key
from pearnly_core.session_lock import operator_holds
from pearnly_cowork.connections import service as connections
from pearnly_core.express_rules.push import common
# 一次导入的执行（纯网络）在共用层（2026-11-03 搬去，ERP 接 MR.ERP 导入直写同用一份）；本模块对外名 `execute` 照旧。
from pearnly_core.mrerp.push.execute import execute  # noqa: F401
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.native.contracts import ReservationContext

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
    # 代录记录（`entry_mode=operator`，ADR-026）由选定电脑的 CC 桌面版领，不在这里排；它们都带 ``route_device``，直写的 MR.ERP
    # 记录没有（`assistant.tools._submit_one` 只给 Express 与 MR.ERP 代录传），先在查询里排掉，再逐条按载荷核一次
    rows = frappe.db.sql("""SELECT name, target_json, payload_json FROM `tabCowork Push Record`
        WHERE connection=%s AND status='pending' AND COALESCE(result_unknown,0)=0 AND COALESCE(route_device,'')=''
        ORDER BY created_at, name LIMIT 20""", (connection,), as_dict=True)
    for row in rows:
        if (json.loads(row.target_json or "{}").get("adapter")) == "mrerp" and not is_operator(row.payload_json):
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
    if is_operator(frappe.db.get_value(RECORD, record, "payload_json")):
        return  # 交给代录的单（ADR-026 第 1 条）：由选定电脑的 CC 桌面版在 MR.ERP 网页里录，直写一律不接
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
        if account_busy(system_url, secrets.get("username") or ""):
            # 同账号互斥（ADR-026 第 8 条）：这个 MR.ERP 账号正被代录批次占着 → 不领，记录照旧待发送（同写者锁「等锁」），
            # 定时巡检 / 代录放开账号后再排；这一次领取整笔撤回
            frappe.db.rollback()
            return
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
    _remember_menus(row.connection, target.get("account_set"), outcome.get("menus"), outcome.get("masters"))
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


def release_kick(connection, workspace, client=None):
    """人在明确的推送入口点了（网页推送 / 待处理再推 `application.api.submit`、CC 推送工具 `assistant.tools`）：清掉这个 MR.ERP
    账号的「被踢」标记（`pearnly_core.mrerp.kick_mark`，11-09 风险 7），这次照常登录。自动推送（同批后续、定时巡检、自动推档位、
    文件夹定时）都不调这里，有标记时在导入入口快速失败。不是 MR.ERP 连接、读不到连接或凭据的不动（推送本身照常报原因）。

    连接照推送的规矩加锁读（`read_for_push` 只许加锁读，不加锁直接抛「先锁连接」；11-11 真站点实测：原来传 ``lock=False``，
    每次都被这条吞成「读不到」，网页推送清不掉标记）。两个调用方都在推送准备的同一事务里，准备时已锁过这条连接。"""
    from pearnly_core.mrerp import kick_mark as account_kick

    if not connection or connections.adapter_of(connection) != "mrerp":
        return False
    try:
        loaded = connections.read_for_push(connection, workspace, client)
        secrets = connections.credentials_for_use(loaded)
    except (frappe.ValidationError, frappe.PermissionError):
        return False  # 读不到：这次推送会照常报这个原因，标记留着
    username = secrets.get("username") or ""
    system_url = (loaded.endpoint.get("config") or {}).get("system_url") or ""
    return bool(username) and account_kick.clear(lock_key(system_url, username))


def account_busy(system_url, username):
    """这个 MR.ERP 账号此刻被 CC 桌面版的代录批次占着（与直写导入的短锁同一把，`session_lock.operator_holds`）。"""
    return bool(username) and operator_holds(lock_key(system_url, username))


def _remember_menus(connection, account_set, menus, masters=None):
    """把这次读到的导入页菜单选项与主档可用值存回连接（选择入口用）；存不了不影响推送结果。"""
    if not menus and not masters:
        return
    try:
        if menus:
            connections.observe_mrerp_menus(connection, account_set, menus)
        if masters:
            connections.observe_mrerp_masters(connection, account_set, masters)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — 只是给选择入口的缓存
        frappe.db.rollback()
        logger.exception("MR.ERP menu options not stored")
