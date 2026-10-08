"""文件夹录入的后台推进：已上传 → 建原件 → 受理识别 → 扣费成功 → 自动推送 → 推送有结果 → 可取表格。

由我们自己服务器上的 Frappe 定时器每分钟叫一次（站点总闸没开时立即返回、不查库，不产生任何云上费用），
小助手传完一份也会马上叫一次。每份文件一步一提交，任何一份出错只影响它自己。

不挤现有客户（影响评估 2026-10-06）：
- 识别：同时在识别的文件夹单不超过商户识别并发的一半（`ocr_inflight` 为 0 时自动取），且商户与全站的识别排队都低于上限一半才交，
  给网页 / LINE / 对账留足名额；排不上的文件留在本表，下一分钟再看，不变成会过期的识别单；
- 推送：每条连接同时「录入中」的文件夹票不超过 `push_pending`（默认 5），会计手动推的单最多排在这几张后面；
- 推送前扣费已成功（用户 2026-10-07：扣费成功后才推送）。
"""
import json
import time
from contextlib import contextmanager
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, now_datetime

from pearnly_cowork.folder_intake import contracts, destinations, inbox, settings
from pearnly_cowork.folder_intake.access import FILE, RULE, read, write
from pearnly_cowork.folder_intake.machine import owner_ready, request_key
from pearnly_cowork.folder_intake.push import push_one

BATCH = "Cowork OCR Batch"
WORK = "Pearnly OCR Work"
HISTORY = "Cowork OCR History"
ACTIVE = ("queued", "running", "deferred")
LOCK = "cowork:folder:pump"
LOCK_SECONDS = 120
STEP_SECONDS = 45
# 推送出了结论但不是成功的票（待人选、失败）：这么多天内人在网页处理好，结果表格跟着更新。
FOLLOW_DAYS = 30
# 准备时小助手没报上账套（重启、断线）：每 10 分钟重推一次，6 小时后仍不行就照常记失败。
OFFLINE_SECONDS = 600
OFFLINE_RETRIES = 36


def kick():
    frappe.enqueue("pearnly_cowork.folder_intake.pump.run", queue="default", enqueue_after_commit=True,
                   job_id="cowork-folder-pump", deduplicate=True)


@contextmanager
def _as(user):
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


def _lock():
    key = frappe.cache.make_key(LOCK)
    return bool(frappe.cache.set(key, "1", nx=True, ex=LOCK_SECONDS)), key


def run():
    cfg = settings.config()
    acquired, key = _lock()
    if not acquired:
        return
    deadline = time.monotonic() + STEP_SECONDS
    try:
        for step in (_store_step, _submit_step, _recognized_step, _push_step, _settle_step, _follow_step):
            if time.monotonic() > deadline:
                break
            step(cfg, deadline)
    finally:
        frappe.cache.delete(key)


def _due(state, limit=50):
    return frappe.get_all(FILE, filters={"state": state}, or_filters=[["next_attempt_at", "is", "not set"],
                          ["next_attempt_at", "<=", now_datetime()]], pluck="name", order_by="registered_at asc",
                          limit=limit, ignore_permissions=True)


def _later(entry, seconds, code):
    entry.next_attempt_at = add_to_date(now_datetime(), seconds=seconds)
    entry.attempts = int(entry.attempts or 0) + 1
    entry.last_code = code
    write(entry)
    frappe.db.commit()


def _fail(entry, code):
    entry.state, entry.last_code, entry.finished_at = "failed", str(code or "")[:140], now_datetime()
    entry.next_attempt_at = None
    write(entry)
    frappe.db.commit()
    if entry.route == "inline":
        inbox.drop(entry.name)


def _adapter(rule):
    return frappe.db.get_value("Cowork ERP Connection", rule.connection, "adapter")


def _live_rule(entry):
    """规则还启用、负责人还能录入；不然这份不再往下走（已受理识别的照常识别完，只是不自动推）。"""
    rule = read(RULE, entry.rule)
    if not rule.enabled:
        return rule, "cowork.folder.rule_disabled"
    if not owner_ready(rule):
        return rule, "cowork.folder.owner_not_ready"
    return rule, ""


def _each(names, deadline, handle):
    for name in names:
        if time.monotonic() > deadline:
            return
        try:
            entry = read(FILE, name, for_update=True)
        except frappe.DoesNotExistError:
            # 规则刚被删除，它的文件进度一起删了：跳过，不打断这一轮其他文件。
            frappe.clear_messages()
            continue
        try:
            handle(entry)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Cowork folder intake step failed", message=frappe.get_traceback()[-1500:])
            entry = read(FILE, name, for_update=True)
            _later(entry, 600, "cowork.folder.step_error")


# ── 已上传 → 原件 ─────────────────────────────────────────────────────────────


def _store_step(cfg, deadline):
    _each(_due("uploaded"), deadline, _store)


def _store(entry):
    from pearnly_cowork.files import service as originals

    rule, problem = _live_rule(entry)
    if problem:
        return _fail(entry, problem)
    key = request_key(entry.name)
    with _as(entry.actor):
        try:
            if entry.route == "inline":
                content = inbox.take(entry.name)
                if content is None:
                    return _fail(entry, "cowork.folder.upload_lost")
                result = originals.create(rule.workspace, entry.filename, content, key, rule.client or None)
            else:
                result = originals.finish_direct(rule.workspace, entry.filename, int(entry.byte_size), key,
                                                 rule.client or None)
        except frappe.ValidationError as error:
            frappe.db.rollback()
            frappe.clear_messages()
            entry = read(FILE, entry.name, for_update=True)
            return _fail(entry, str(error))
    if "retry" in result:
        return _later(entry, max(5, int(result["retry"] or 5)), "cowork.folder.inspect_busy")
    # 直传的原件由云端核对后报回摘要，须与小助手声明的一致；经接口上传的在收下时已逐字节核过，
    # 而 HEIC 照片建原件时会转成 JPEG（`files.service.create`），摘要本来就不同（10-07 测试机实测踩到）。
    if entry.route == "direct" and result.get("sha256") and result["sha256"] != entry.sha256:
        return _fail(entry, "cowork.folder.content_mismatch")
    entry.source_file, entry.state, entry.next_attempt_at, entry.last_code = result["name"], "source_ready", None, ""
    write(entry)
    frappe.db.commit()
    if entry.route == "inline":
        inbox.drop(entry.name)


# ── 原件 → 受理识别（限流） ─────────────────────────────────────────────────────────


def _capacity(cfg):
    """还能再交几份文件夹识别。上限缺省是商户识别并发的一半（另一半始终留给网页 / LINE，它们来了不用排在
    文件夹后面）；商户与全站的识别排队都还要低于上限一半。读不到就当没有余量。

    10-07 测试机实测：原来固定一次只交一份、靠每分钟的定时推进，41 份要一个多小时（每份间隔约 90 秒），
    不适合生产；现在按并发名额交，识别一结束就由钩子马上接下一份（`events.recognized`）。"""
    try:
        from pearnly_core.execution import budget

        limits = budget.config()
        client = budget.redis()
        pending = client.zcard(budget.PREFIX + "pending")
        tenant = client.zcard(budget.PREFIX + "pending:" + budget.tenant())
    except Exception:
        return 0
    if pending * 2 >= limits["pending"] or tenant * 2 >= limits["tenant_pending"]:
        return 0
    ceiling = cfg["ocr_inflight"] or max(1, limits["tenant_concurrency"] // 2)
    return max(0, ceiling - frappe.db.count(FILE, {"state": "submitted"}))


def _submit_step(cfg, deadline):
    room = _capacity(cfg)
    if room <= 0:
        return
    _each(_due("source_ready", room), deadline, _submit)


def _submit(entry):
    from pearnly_core.execution.errors import Deferred, QueueFull

    from pearnly_cowork.ocr.execution import service as recognition

    rule, problem = _live_rule(entry)
    if problem:
        return _fail(entry, problem)
    name = entry.name
    with _as(entry.actor):
        try:
            result = recognition.submit(entry.source_file, request_key(entry.name), direction=rule.direction,
                                        posting_kind=rule.posting_kind, connection=rule.connection,
                                        account_set_key=rule.account_set_key,
                                        target=destinations.of(_adapter(rule)).intake_target)
        except (Deferred, QueueFull) as error:
            frappe.db.rollback()
            entry = read(FILE, name, for_update=True)
            code = error.reason if isinstance(error, Deferred) else "queue_full"
            # 余额不足：停下，充值后下一分钟起自然接着交（用户 2026-10-07：扣费成功后才推送、余额不足就停）。
            return _later(entry, 600 if code == "insufficient_balance" else 120, code)
        except frappe.ValidationError as error:
            frappe.db.rollback()
            frappe.clear_messages()
            entry = read(FILE, name, for_update=True)
            return _fail(entry, str(error))
    entry = read(FILE, name, for_update=True)
    entry.batch, entry.state, entry.next_attempt_at, entry.last_code = result["name"], "submitted", None, ""
    write(entry)
    frappe.db.commit()


# ── 识别有结论 → 扣费成功才往下 ─────────────────────────────────────────────────────


def _recognized_step(cfg, deadline):
    _each(_due("submitted", 200), deadline, _recognized)


def _recognized(entry):
    batch = frappe.db.get_value(BATCH, entry.batch, ["phase", "billing_state", "history_records", "work_name"],
                                as_dict=True)
    if not batch:
        return _fail(entry, "cowork.folder.batch_missing")
    if batch.phase == "not_invoice":
        entry.state, entry.finished_at, entry.workbook_revision = "not_invoice", now_datetime(), 1
        write(entry)
        return frappe.db.commit()
    if batch.phase == "saved":
        if batch.billing_state not in ("charged", "not_billable"):
            return _later(entry, 60, "cowork.folder.billing_pending")
        entry.histories_json = batch.history_records or "[]"
        entry.state, entry.next_attempt_at, entry.last_code = "recognized", None, ""
        write(entry)
        return frappe.db.commit()
    status, code = (frappe.db.get_value(WORK, batch.work_name, ["status", "last_code"]) or (None, None)) \
        if batch.work_name else (None, None)
    if status and status not in ACTIVE and status != "succeeded":
        return _fail(entry, code or status)
    return _later(entry, 30, "")


# ── 自动推送（限流）→ 推送有结论 ────────────────────────────────────────────────────


def _pending_pushes(connection):
    names = frappe.get_all(FILE, filters={"connection": connection, "state": ["in", ["recognized", "pushing"]]},
                           pluck="outcome_json", ignore_permissions=True)
    sent = [history for raw in names for history, outcome in json.loads(raw or "{}").items()
            if isinstance(outcome, dict) and outcome.get("status") == "sent"]
    if not sent:
        return 0
    return frappe.db.count(HISTORY, {"name": ["in", sent], "last_push_status": "pending"})


def _push_step(cfg, deadline):
    for name in _due("recognized", 100):
        if time.monotonic() > deadline:
            return
        entry = read(FILE, name, for_update=True)
        if _pending_pushes(entry.connection) >= cfg["push_pending"]:
            continue
        try:
            _push(entry, cfg, deadline)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
        except Exception:
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Cowork folder intake push failed", message=frappe.get_traceback()[-1500:])
            _later(read(FILE, name, for_update=True), 600, "cowork.folder.step_error")


def _push(entry, cfg, deadline):
    rule, problem = _live_rule(entry)
    outcome = json.loads(entry.outcome_json or "{}")
    name = entry.name
    for history in json.loads(entry.histories_json or "[]"):
        if history in outcome:
            continue
        if problem:
            outcome[history] = {"status": "stopped", "reason": problem}
        else:
            if time.monotonic() > deadline or _pending_pushes(entry.connection) >= cfg["push_pending"]:
                return
            retries = outcome.get("_retry") or {}
            attempt = int(retries.get(history) or 0)
            with _as(entry.actor):
                result = push_one(entry, rule, history, attempt, final=attempt >= OFFLINE_RETRIES)
            if result.get("status") == "retry":
                entry = read(FILE, name, for_update=True)
                outcome["_retry"] = dict(retries, **{history: attempt + 1})
                entry.outcome_json = json.dumps(outcome, sort_keys=True)
                return _later(entry, OFFLINE_SECONDS, result["reason"])
            outcome[history] = result
        entry = read(FILE, name, for_update=True)
        entry.outcome_json = json.dumps(outcome, sort_keys=True)
        write(entry)
        frappe.db.commit()
    entry = read(FILE, name, for_update=True)
    entry.state, entry.next_attempt_at, entry.last_code = "pushing", None, ""
    write(entry)
    frappe.db.commit()


def _signature(entry):
    histories = json.loads(entry.histories_json or "[]")
    if not histories:
        return []
    rows = {row.name: row for row in frappe.get_all(HISTORY, filters={"name": ["in", histories]},
            fields=["name", "last_push_status", "last_push_reason"], ignore_permissions=True)}
    # 票在录入工作台被删了：记 deleted，与成功一样算有了结论（不再等它、不再出它的表格）。
    return [[name, (rows[name].last_push_status or "") if name in rows else contracts.DELETED,
             (rows[name].last_push_reason or "") if name in rows else ""] for name in histories]


def _settle_step(cfg, deadline):
    """推送都有了结论（成功 / 待人工 / 没推）：这份完成，结果表格可以取了。"""
    def settle(entry):
        outcome = json.loads(entry.outcome_json or "{}")
        signature = _signature(entry)
        for name, status, _reason in signature:
            if (outcome.get(name) or {}).get("status") == "sent" and status not in ("success", "manual", contracts.DELETED):
                return _later(entry, 30, "")
        entry.state, entry.finished_at = "done", now_datetime()
        entry.workbook_revision = int(entry.workbook_revision or 0) + 1
        outcome["_signature"] = signature
        entry.outcome_json = json.dumps(outcome, sort_keys=True)
        entry.next_attempt_at = None
        write(entry)
        frappe.db.commit()

    _each(_due("pushing", 200), deadline, settle)


def _follow_step(cfg, deadline):
    """完成后人在网页把没推的票推好了：表格版本加一，小助手下次同步重取。只看这些天内完成、还有没成功的票。"""
    since = now_datetime() - timedelta(days=FOLLOW_DAYS)
    names = frappe.get_all(FILE, filters={"state": "done", "finished_at": [">=", since]},
                           or_filters=[["next_attempt_at", "is", "not set"], ["next_attempt_at", "<=", now_datetime()]],
                           pluck="name", order_by="finished_at asc", limit=100, ignore_permissions=True)

    def follow(entry):
        outcome = json.loads(entry.outcome_json or "{}")
        before = outcome.get("_signature") or []
        if before and all(status in contracts.SETTLED for _name, status, _reason in before):
            entry.next_attempt_at = add_to_date(now_datetime(), days=FOLLOW_DAYS + 1)
            write(entry)
            return frappe.db.commit()
        signature = _signature(entry)
        if signature != before:
            outcome["_signature"] = signature
            entry.outcome_json = json.dumps(outcome, sort_keys=True)
            entry.workbook_revision = int(entry.workbook_revision or 0) + 1
        entry.next_attempt_at = add_to_date(now_datetime(), minutes=10)
        write(entry)
        frappe.db.commit()

    _each(names, deadline, follow)
