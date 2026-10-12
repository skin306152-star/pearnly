"""文件夹录入的后台推进：已上传 → 建原件 → 受理识别 → 扣费成功 → 自动推送 → 推送有结果 → 可取表格。

由我们自己服务器上的 Frappe 定时器每分钟叫一次（站点总闸没开时立即返回、不查库，不产生任何云上费用），
小助手传完一份也会马上叫一次。每份文件一步一提交，任何一份出错只影响它自己。

不挤现有客户（影响评估 2026-10-06）：
- 识别：同时在识别的文件夹单不超过商户识别并发的一半（`ocr_inflight` 为 0 时自动取），且商户与全站的识别排队都低于上限一半才交，
  给网页 / LINE / 对账留足名额；排不上的文件留在本表，下一分钟再看，不变成会过期的识别单；
- 推送：每条连接同时「录入中」的文件夹票不超过 `push_pending`（默认 5），会计手动推的单最多排在这几张后面；
- 推送前扣费已成功（用户 2026-10-07：扣费成功后才推送）。

先后（F7，2026-10-12）：同一写入连接、同一账套里，存货销售文件等同账套的存货进货文件推送有了结论（写完或停下待人）
再准备（`contracts.waits_for_purchases`，最多等 12 小时），否则销售会卖成负库存、不按进货的均价结转成本。

POS 导出（2026-10-13，交接 L9）：销售任务里的 Excel / CSV 不交识别，建原件后由 `pos_import` 按天建票（不扣费），
之后与识别出的票同一条路推送、出结果表格。列还没认好（这家公司第一次见这种导出、又认不准；或记住的版式遇到没登记过
的状态值）就停在 source_ready 等人答一次（`contracts.POS_LAYOUT_NEEDED`，每 5 分钟只看这份版式自停下以后有没有记上 /
改过、公司键变没变，不重读文件，见 `pos_import.service.layout_changed`）。
"""
import json
import time
from contextlib import contextmanager
from datetime import timedelta

import frappe
from frappe.utils import add_to_date, now_datetime

from pearnly_cowork.folder_intake import contracts, destinations, inbox, settings
from pearnly_cowork.folder_intake.access import FILE, RULE, RUN, read, write
from pearnly_cowork.folder_intake.machine import owner_ready, request_key
from pearnly_cowork.folder_intake.push import push_one
from pearnly_cowork.targets.identity import normalize_express_account_key

BATCH = "Cowork OCR Batch"
WORK = "Pearnly OCR Work"
HISTORY = "Cowork OCR History"
ACTIVE = ("queued", "running", "deferred")
IN_FLIGHT = ("prepared", "pending", "leased")  # 推送还在路上的票状态（见 `_settle_step`）
LOCK = "cowork:folder:pump"
LOCK_SECONDS = 120
STEP_SECONDS = 45
# 推送出了结论但不是成功的票（待人选、失败）：这么多天内人在网页处理好，结果表格跟着更新。
FOLLOW_DAYS = 30
# 准备时小助手没报上账套（重启、断线）：每 10 分钟重推一次，6 小时后仍不行就照常记失败。
OFFLINE_SECONDS = 600
OFFLINE_RETRIES = 36
# 存货销售等同账套的存货进货先写：每分钟再看一次
ORDER_RECHECK_SECONDS = 60
# POS 导出等人认列：每 5 分钟看一次版式记上 / 改过了没有
LAYOUT_RECHECK_SECONDS = 300


def kick():
    frappe.enqueue("pearnly_cowork.folder_intake.pump.run", queue="default", enqueue_after_commit=True,
                   job_id="cowork-folder-pump", deduplicate=True)


@contextmanager
def _as(user):
    """以负责人的身份推进；语言也换成负责人的网页语言 —— 退回原因、结果表格都按这个人的语言记，
    不随后台进程的语言变（10-09 实测：同一原因到点运行存泰文、立即运行存英文）。与 `machine._as` 同一做法。"""
    from frappe.translate import get_user_lang

    previous, previous_lang = frappe.session.user, getattr(frappe.local, "lang", None)
    frappe.set_user(user)
    frappe.local.lang = get_user_lang(user)
    # Frappe 16 后台任务第一次翻译时会按排队用户重设语言（`execution.work.job_language`）：标成已定，不被冲掉。
    job = getattr(frappe.local, "job", None)
    if job is not None:
        job.lang_resolved = True
    try:
        yield
    finally:
        frappe.set_user(previous)
        frappe.local.lang = previous_lang


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
        for step in (_store_step, _pos_step, _submit_step, _recognized_step, _push_step, _settle_step, _follow_step):
            if time.monotonic() > deadline:
                break
            step(cfg, deadline)
    finally:
        frappe.cache.delete(key)


def _due(state, limit=50):
    return frappe.get_all(FILE, filters={"state": state}, or_filters=[["next_attempt_at", "is", "not set"],
                          ["next_attempt_at", "<=", now_datetime()]], pluck="name", order_by="registered_at asc",
                          limit=limit, ignore_permissions=True)


def _due_kind(state, limit, tables):
    """到期的文件，只取 POS 导出（``tables``）或只取票据（按文件名后缀分；登记时已按任务核过收不收）。"""
    like = " OR ".join(["filename LIKE %s"] * len(contracts.TABLE_EXTENSIONS))
    return frappe.db.sql_list(f"""SELECT name FROM `tab{FILE}` WHERE state = %s
        AND (next_attempt_at IS NULL OR next_attempt_at <= %s) AND {"" if tables else "NOT "}({like})
        ORDER BY registered_at ASC LIMIT %s""",
                              (state, now_datetime(), *["%" + ext for ext in contracts.TABLE_EXTENSIONS], limit))


def _later(entry, seconds, code):
    entry.next_attempt_at = add_to_date(now_datetime(), seconds=seconds)
    entry.attempts = int(entry.attempts or 0) + 1
    entry.last_code = code
    write(entry)
    frappe.db.commit()


def _fail(entry, code):
    # 记词条原文（不是按后台语言翻好的句子）：之后谁看都按自己的语言翻（`push_state.source_text`，10-09）。
    from pearnly_cowork.history.push_state import source_text

    entry.state, entry.last_code, entry.finished_at = "failed", source_text(code)[:140], now_datetime()
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


# ── 原件（POS 导出）→ 按天建票（不识别、不扣费） ────────────────────────────────────────


def _pos_step(cfg, deadline):
    _each(_due_kind("source_ready", 20, True), deadline, _pos)


def _pos(entry):
    from pearnly_cowork.pos_import import service as pos_import

    rule, problem = _live_rule(entry)
    if problem:
        return _fail(entry, problem)
    name = entry.name
    held = json.loads(entry.outcome_json or "{}").get("_pos") or {}
    with _as(entry.actor):
        if held.get("fingerprint") and not pos_import.layout_changed(rule.workspace, rule.connection,
                                                                      rule.account_set_key, held):
            return _later(entry, LAYOUT_RECHECK_SECONDS, contracts.POS_LAYOUT_NEEDED)
        try:
            result = pos_import.admit(entry.source_file, request_key(name), workspace=rule.workspace,
                                      client=rule.client or None, connection=rule.connection,
                                      account_set_key=rule.account_set_key, posting_kind=rule.posting_kind or None)
        except frappe.ValidationError as error:
            frappe.db.rollback()
            frappe.clear_messages()
            return _fail(read(FILE, name, for_update=True), str(error))
    entry = read(FILE, name, for_update=True)
    outcome = json.loads(entry.outcome_json or "{}")
    if result.get("status") == "layout_needed":
        outcome["_pos"] = {"fingerprint": result["fingerprint"], "company": result["company"],
                           "layout_revision": result.get("layout_revision") or ""}
        entry.outcome_json = json.dumps(outcome, sort_keys=True)
        return _later(entry, LAYOUT_RECHECK_SECONDS, contracts.POS_LAYOUT_NEEDED)
    if result.get("status") != "ok":
        return _fail(entry, result.get("code") or "pos.file_unreadable")
    outcome.pop("_pos", None)
    entry.outcome_json = json.dumps(outcome, sort_keys=True)
    entry.histories_json = json.dumps(result["histories"])
    entry.state, entry.next_attempt_at, entry.last_code = "recognized", None, ""
    write(entry)
    frappe.db.commit()


def resume(entry):
    """人刚认好列：这份停着的 POS 导出马上再看一次（不等 5 分钟）。"""
    entry.next_attempt_at = None
    write(entry)
    kick()


def _submit_step(cfg, deadline):
    room = _capacity(cfg)
    if room <= 0:
        return
    _each(_due_kind("source_ready", room, False), deadline, _submit)


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
                                        posting_kind=rule.posting_kind or None, payment=rule.payment or None,
                                        connection=rule.connection,
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


def _order_key(connection, account_set_key):
    return connection or "", normalize_express_account_key(account_set_key)


def _purchases_in_progress(now):
    """同账套还在路上的存货进货：{(连接, 账套): {"files": [...], "runs": [...]}}（每轮推送最多查一次）。"""
    found = {}
    for row in frappe.db.sql(f"""SELECT f.state, f.registered_at, r.connection, r.account_set_key
            FROM `tab{FILE}` f INNER JOIN `tab{RULE}` r ON r.name = f.rule
            WHERE f.state IN %s AND r.direction = 'purchase' AND r.posting_kind = 'stock'""",
                             (contracts.ORDER_WAITING,), as_dict=True):
        found.setdefault(_order_key(row.connection, row.account_set_key), {"files": [], "runs": []})["files"].append(row)
    for row in frappe.db.sql(f"""SELECT r.connection, r.account_set_key, r.run_request_at,
            (SELECT MAX(u.started_at) FROM `tab{RUN}` u WHERE u.rule = r.name AND IFNULL(u.note, '') != 'retry')
                AS last_reported_at
            FROM `tab{RULE}` r WHERE r.enabled = 1 AND r.direction = 'purchase' AND r.posting_kind = 'stock'
              AND r.run_request_at >= %s""", (now - contracts.ORDER_RUN_GRACE,), as_dict=True):
        found.setdefault(_order_key(row.connection, row.account_set_key), {"files": [], "runs": []})["runs"].append(row)
    return found


def _waits_for_purchases(entry, cache, now):
    """这份文件是存货销售、同账套的存货进货还在路上：先不准备（停用的任务、负责人失效的照常走 `_push` 记停下，不拖）。"""
    rule = frappe.db.get_value(RULE, entry.rule, ["enabled", "connection", "account_set_key", "direction",
                                                  "posting_kind"], as_dict=True)
    if not rule or not rule.enabled or contracts.stock_role(rule.direction, rule.posting_kind) != "sale":
        return False
    if "groups" not in cache:
        cache["groups"] = _purchases_in_progress(now)  # 有存货销售要判时才查，空闲时开销不变
    group = cache["groups"].get(_order_key(rule.connection, rule.account_set_key)) or {}
    return contracts.waits_for_purchases(entry.registered_at, group.get("files") or [], group.get("runs") or [], now)


def _push_step(cfg, deadline):
    cache, now = {}, now_datetime()
    for name in _due("recognized", 100):
        if time.monotonic() > deadline:
            return
        entry = read(FILE, name, for_update=True)
        if _pending_pushes(entry.connection) >= cfg["push_pending"]:
            continue
        try:
            if _waits_for_purchases(entry, cache, now):
                _later(entry, ORDER_RECHECK_SECONDS, contracts.WAITING_PURCHASES)
                continue
            if entry.last_code == contracts.WAITING_PURCHASES:
                entry.last_code = ""
                write(entry)
                frappe.db.commit()
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
    """推送都有了结论（成功 / 待人工 / 没推）：这份完成，结果表格可以取了。

    只等还在路上的（已准备 / 排队 / 小助手正在写）；其余状态都算有了结论，不会因为某种没想到的状态一直等
    （10-09 实测：同号票没建推送记录，票上一直没有状态，整份永远停在「推送中」）。"""
    def settle(entry):
        outcome = json.loads(entry.outcome_json or "{}")
        signature = _signature(entry)
        for name, status, _reason in signature:
            if (outcome.get(name) or {}).get("status") == "sent" and status in IN_FLIGHT:
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
