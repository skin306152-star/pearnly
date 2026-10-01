"""Durable, bounded OCR work on Frappe's native RQ queue.

Only registered idempotent OCR workflows are accepted. External order writers are
never retried here. MariaDB is the authoritative backlog; RQ is a delivery hint,
so a lost Redis job or worker interruption cannot silently lose accepted work.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json

import frappe
from redis.exceptions import RedisError, LockError

from . import budget
from .errors import Deferred, QueueFull, Uncertain

DT = "Pearnly OCR Work"
# 检查点按行存（2026-10-01 生产：每次调用都把整块检查点 JSON 重写进 Work，多页文件几 MB，几十个连接同时写把 MariaDB 的
# 连接内存撑到 2.5G、被内核杀了两次）。每次调用只写自己那一行；Work 上的 `checkpoints` 是部署前的旧格式，读时作底、新行覆盖。
CP = "Pearnly OCR Checkpoint"
ACTIVE = ("queued", "running", "deferred")
JOB_TIMEOUT = 600
PRUNE_BATCH = 500
# 付过费的任务在排队时限之后还能续跑多久（续跑只补剩下的页，已付的部分不会重复扣）。
PAID_GRACE = timedelta(hours=3)


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def handler(kind):
    paths = frappe.get_hooks("pearnly_ocr_handlers").get(kind) or []
    if not paths:
        raise ValueError("Unregistered OCR workflow")
    return paths[-1] if isinstance(paths, list) else paths


def submit(kind, reference, arguments, user):
    try:
        return _submit(kind, reference, arguments, user)
    except (RedisError, LockError) as exc:
        raise Deferred("capacity_unavailable", 15) from exc


def _submit(kind, reference, arguments, user):
    if frappe.conf.get("pearnly_ocr_paused") or frappe.conf.maintenance_mode:
        raise Deferred("ocr_paused", 30)
    handler(kind)
    if not reference or len(reference) > 140 or not user:
        raise ValueError("OCR work requires an owner and stable reference")
    encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
    if len(encoded.encode()) > 32768:
        raise ValueError("Queue payload must contain references, not document bytes")
    name = hashlib.sha256((kind + "\0" + reference).encode()).hexdigest()
    cfg, client = budget.config(), budget.redis()
    admission = budget.PREFIX + "pending"
    tenant_pending = admission + ":" + budget.tenant()
    member = hashlib.sha256((frappe.local.site + ":" + name).encode()).hexdigest()
    # This short shared lock only allocates durable slots. Never held during OCR.
    # 等锁最多 15 秒（原 3 秒）：持锁时要查库、写库、提交，数据库忙时（2026-09-30 生产压测，数据库约 90%）
    # 同时交 16 张就有 2 张 3 秒内排不上锁、被当成 capacity_unavailable 拒收。页面遇到这类暂时满会自动重交。
    with client.lock(budget.PREFIX + "admission", timeout=15, blocking_timeout=15):
        existing = frappe.db.get_value(DT, name, ["arguments", "user", "status"], as_dict=True)
        if existing:
            if existing.arguments != encoded or existing.user != user:
                raise ValueError("OCR reference was reused with different content")
            return name
        client.zremrangebyscore(admission, "-inf", utcnow().replace(tzinfo=timezone.utc).timestamp())
        client.zremrangebyscore(tenant_pending, "-inf", utcnow().replace(tzinfo=timezone.utc).timestamp())
        local_count = frappe.db.count(DT, {"status": ["in", ACTIVE]})
        if (local_count >= cfg["tenant_pending"] or client.zcard(tenant_pending) >= cfg["tenant_pending"]
                or client.zcard(admission) >= cfg["pending"]):
            raise QueueFull("OCR queue is full")
        expiry = utcnow() + timedelta(seconds=cfg["max_age"])
        score = {member: expiry.replace(tzinfo=timezone.utc).timestamp()}
        client.zadd(admission, score)
        client.zadd(tenant_pending, score)
        try:
            frappe.get_doc({"doctype": DT, "name": name, "kind": kind,
                "product": kind.split(".")[0], "reference": reference, "arguments": encoded,
                "user": user, "status": "queued", "next_run": utcnow(),
                "expires_at": expiry, "admission_key": member}).insert(ignore_permissions=True)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            client.zrem(admission, member)
            client.zrem(tenant_pending, member)
            raise
    kick(name)
    return name


def kick(name):
    try:
        frappe.enqueue("pearnly_core.execution.work.run", name=name, queue="ocr", timeout=JOB_TIMEOUT,
                       job_id="ocr-" + name, deduplicate=True)
        return True
    except Exception:
        # A scheduler sweep delivers the durable record when RQ is available again.
        frappe.logger("pearnly.capacity").exception("OCR queue delivery deferred")
        return False


def sweep():
    """Called every minute; bounded delivery, not an unbounded job-per-page flood."""
    # Cursor avoids a corrupt/undeliverable oldest page starving all later work.
    cache = frappe.cache
    cursor_key = "pearnly:ocr:sweep_cursor"
    cursor = cache.get_value(cursor_key) or ""
    now = utcnow()
    rows = frappe.db.sql(f"""SELECT name FROM `tab{DT}` WHERE name > %s AND
        ((status = 'running' AND lease_until < %s) OR
         (status IN ('queued', 'deferred') AND next_run <= %s))
        ORDER BY name LIMIT 50""", (cursor, now, now), as_dict=True)
    for row in rows:
        kick(row.name)
    cache.set_value(cursor_key, rows[-1].name if len(rows) == 50 else "", expires_in_sec=600)


def _finish(name, status, code="", delay=0, notify=True):
    # End the workflow snapshot before locking the outcome. Cancellation can win
    # while a worker is in flight, and must not be overwritten by that worker.
    frappe.db.commit()
    rows = frappe.db.sql(f"SELECT status FROM `tab{DT}` WHERE name=%s FOR UPDATE", name)
    if not rows or rows[0][0] not in ACTIVE:
        frappe.db.rollback()
        return
    values = {"status": status, "last_code": code, "lease_until": None,
              "next_run": utcnow() + timedelta(seconds=delay)}
    frappe.db.set_value(DT, name, values, update_modified=False)
    frappe.db.commit()
    if notify and status in ("failed", "uncertain"):
        _notify(name, code)
    if status == "deferred":
        from .dispatch import later
        try:
            later(name, delay)
        except RedisError:
            pass  # Native scheduler repairs the delivery hint.
    if status not in ACTIVE:
        # 有结论就推给提交人（原生实时推送）：等候页收到立刻读结果，不用每一两秒轮询一次（2026-10-01 生产压测：几十份
        # 同时识别，页面轮询占了网页 CPU 的一大块）。只作提醒，推送不了页面照样按兜底轮询拿到结果。
        try:
            owner = frappe.db.get_value(DT, name, ["user", "kind", "reference"], as_dict=True)
            if owner and owner.user:
                frappe.publish_realtime("pearnly_ocr_work", {"kind": owner.kind, "reference": owner.reference,
                                                             "status": status}, user=owner.user, after_commit=False)
        except Exception:
            pass
        from .inputs import remove
        remove(name)
        try:
            member = frappe.db.get_value(DT, name, "admission_key")
            client = budget.redis()
            client.zrem(budget.PREFIX + "pending", member)
            client.zrem(budget.PREFIX + "pending:" + budget.tenant(), member)
        except (RedisError, Deferred):
            pass  # Admission reservation expires conservatively.


def job_language(user):
    """识别任务开跑前把界面语言定为提交识别的那个人的语言，并告诉 Frappe「已定好」。

    Frappe 16 的后台任务在第一次不带 lang 的 `_()` 时，会按**排队那一刻的会话用户**重设
    `frappe.local.lang`（`frappe.utils.translations._` → `set_user_lang(job.user)`）。续跑的任务是
    调度器以 Administrator 排的队，于是产品在处理函数里设好的语言（如 LINE 手机语言）会被
    冲成站点默认语言：2026-09-27 ERP 销售识别第一次被限流、续跑后整张卡变成泰文（第一次就跑完的
    采购卡是中文）。这里先定下，产品处理函数再按自己的口径改 `frappe.local.lang` 就不会被覆盖。"""
    from frappe.utils.translations import set_user_lang

    if user:
        set_user_lang(user)
    job = getattr(frappe.local, "job", None)
    if job is not None:
        job.lang_resolved = True


@contextmanager
def context(name):
    previous = frappe.flags.get("pearnly_ocr_work")
    frappe.flags.pearnly_ocr_work = name
    frappe.flags.pearnly_ocr_checkpoints = None
    try:
        yield
    finally:
        frappe.flags.pearnly_ocr_work = previous
        frappe.flags.pearnly_ocr_checkpoints = None


def run(name):
    if frappe.conf.get("pearnly_ocr_paused") or frappe.conf.maintenance_mode:
        return  # Durable SQL repair resumes it after maintenance.
    # 开了多线程的识别进程：交给空闲线程跑这一份，RQ 立即去取下一份（见 pool.py）。
    from .pool import handoff
    if handoff("pearnly_core.execution.work.run", {"name": name}):
        return
    # Independent SQL claim: duplicate RQ delivery cannot run the same item twice.
    frappe.db.begin()
    rows = frappe.db.sql(f"SELECT * FROM `tab{DT}` WHERE name=%s FOR UPDATE", name, as_dict=True)
    if not rows or rows[0].status not in ACTIVE:
        frappe.db.rollback()
        return
    doc, now = rows[0], utcnow()
    if doc.status == "running" and doc.lease_until and doc.lease_until > now:
        frappe.db.rollback()
        return
    if doc.next_run and doc.next_run > now:
        frappe.db.rollback()
        return
    stages = stored_checkpoints(name)
    # 残留「started」（上一个进程在调用中途死了，租约已过）：默认整份判结果不明、不再付费重放。声明了自己能按调用
    # 处理结果不明的类别（hooks `pearnly_ocr_resume_unknown`）照常续跑：已完成的调用从检查点取回，那一次调用由
    # 处理函数自己决定（有限次重发 / 当成这一页读不出）—— Cowork 对账：25 页扫描件在调用中途被硬时限杀掉，
    # 续跑时整份作废（2026-09-30 压测第 18 轮）。
    if (any(value.get("state") == "started" for value in stages.values())
            and doc.kind not in (frappe.get_hooks("pearnly_ocr_resume_unknown") or [])):
        _finish(name, "uncertain", "provider_outcome_unknown")
        return
    # 排队时限：已经付过费（有完成的检查点）的任务续跑时不按提交时间判过期，否则已付费的调用作废、用户只能重传
    # （2026-09-28 Cowork 压测：66 份一起提交，处理中途续跑被判排队超时）。还没付费的照旧按时限过期（开跑后在准入 /
    # 限流上一直被推迟的也算，免得几小时后才真正扣费）；付过费的也有绝对上限，不无限期拖（DMS 窗口 2026-09-28 意见）。
    paid = any(value.get("state") == "done" for value in stages.values())
    if (not paid and doc.expires_at <= now) or doc.expires_at + PAID_GRACE <= now:
        _finish(name, "failed", "queue_expired")
        return
    frappe.db.set_value(DT, name, {"status": "running", "attempts": (doc.attempts or 0) + 1,
                                 "lease_until": now + timedelta(seconds=JOB_TIMEOUT + 60)}, update_modified=False)
    frappe.db.commit()
    try:
        from .resources import check_memory
        check_memory()
        with context(name):
            job_language(doc.user)
            result = frappe.get_attr(handler(doc.kind))(**json.loads(doc.arguments))
        outcome = result or {}
        if outcome.get("cancelled"):
            _finish(name, "cancelled", outcome["cancelled"])
        elif outcome.get("failed"):
            _finish(name, "failed", outcome["failed"], notify=not outcome.get("notified"))
        else:
            _finish(name, "succeeded")
    except Deferred as exc:
        frappe.db.rollback()
        _finish(name, "deferred", exc.reason, exc.delay)
    except Uncertain:
        frappe.db.rollback()
        _finish(name, "uncertain", "provider_outcome_unknown")
    except Exception:
        frappe.db.rollback()
        # A bug/permission failure is not permission to replay a paid provider call.
        _finish(name, "failed", "workflow_error")
        frappe.log_error(title="OCR workflow failed")
        raise


def checkpoint_key(payload, model):
    return hashlib.sha256((model + "\0" + json.dumps(payload, sort_keys=True)).encode()).hexdigest()


def _row_name(name, key):
    return hashlib.sha256((name + "\0" + key).encode()).hexdigest()


def _legacy(name):
    """部署前写在 Work 上的整块检查点（新任务是空的）。"""
    return json.loads(frappe.db.get_value(DT, name, "checkpoints") or "{}") or {}


def stored_checkpoints(name):
    """这份 Work 的全部检查点 ``{key: {"state", "body"}}``：旧格式作底，按行存的覆盖。"""
    values = _legacy(name)
    for row in frappe.db.sql(f"SELECT checkpoint_key, state, body FROM `tab{CP}` WHERE work=%s", name, as_dict=True):
        values[row.checkpoint_key] = {"state": row.state, "body": json.loads(row.body) if row.body else None}
    return values


def saved_checkpoint(name, key):
    """任一份 Work 的某个检查点（不必在它的运行里）：按主键取一行；没有再看旧格式。"""
    rows = frappe.db.sql(f"SELECT state, body FROM `tab{CP}` WHERE name=%s", _row_name(name, key), as_dict=True)
    if rows:
        return {"state": rows[0].state, "body": json.loads(rows[0].body) if rows[0].body else None}
    return _legacy(name).get(key)


def done_calls(names):
    """每份 Work 已完成（done）的调用次数 ``{name: n}``：按行计数，不把内容读回来。"""
    names = tuple(dict.fromkeys(names or ()))
    if not names:
        return {}
    counts = {name: 0 for name in names}
    for work, count in frappe.db.sql(f"""SELECT work, COUNT(*) FROM `tab{CP}` WHERE work IN %(names)s
            AND state='done' GROUP BY work""", {"names": names}):
        counts[work] = count
    legacy = frappe.db.sql(f"""SELECT name FROM `tab{DT}` WHERE name IN %(names)s
        AND checkpoints IS NOT NULL AND checkpoints NOT IN ('', '{{}}')""", {"names": names})
    for (name,) in legacy:  # 部署前开跑的任务：新旧合并后再数，同一个调用不重复算
        counts[name] = sum(1 for value in stored_checkpoints(name).values()
                           if isinstance(value, dict) and value.get("state") == "done")
    return counts


def _stages(name):
    """本次运行的检查点：每次运行只从库里读一次（同一时刻只有持租约的这一个运行在写）。"""
    cached = frappe.flags.get("pearnly_ocr_checkpoints")
    if not cached or cached[0] != name:
        cached = (name, stored_checkpoints(name))
        frappe.flags.pearnly_ocr_checkpoints = cached
    return cached[1]


def checkpoint(key, state=None, body=None):
    name = frappe.flags.get("pearnly_ocr_work")
    if not name:
        return None
    stages = _stages(name)
    if state is None:
        return stages.get(key)
    stages[key] = {"state": state, "body": body}
    now, user = frappe.utils.now(), frappe.session.user
    # 只写这一个调用的那一行（同一调用再写就覆盖：started → done / rejected）。
    frappe.db.sql(f"""INSERT INTO `tab{CP}` (name, creation, modified, modified_by, owner, docstatus, idx,
            work, checkpoint_key, state, body) VALUES (%s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE state=VALUES(state), body=VALUES(body), modified=VALUES(modified)""",
        (_row_name(name, key), now, now, user, user, name, key, state, json.dumps(body)))
    frappe.db.commit()
    return stages[key]


def has_completed_call():
    name = frappe.flags.get("pearnly_ocr_work")
    if not name:
        return False
    return done_calls([name])[name] > 0


def _notify(name, code):
    row = frappe.db.get_value(DT, name, ["kind", "arguments"], as_dict=True)
    # A Site with no product notification hook gets [] from Frappe, not {}.
    # Terminal persistence/slot release must still finish on such product Sites.
    for path in (frappe.get_hooks("pearnly_ocr_failure_handlers") or {}).get(row.kind) or []:
        try:
            frappe.get_attr(path)(arguments=json.loads(row.arguments), code=code)
        except Exception:
            frappe.logger("pearnly.capacity").exception("OCR terminal notification failed")


def prune_checkpoints():
    """Keep idempotency receipts; remove completed OCR content after seven days."""
    # Uncertain/failed work retains evidence for operator review; no auto replay.
    cutoff = utcnow() - timedelta(days=7)
    rows = frappe.get_all(DT, filters={"status": ["in", ["succeeded", "cancelled"]],
        "next_run": ["<", cutoff], "checkpoints": ["not in", ["", "{}"]]},
        pluck="name", limit_page_length=PRUNE_BATCH)
    for name in rows:
        frappe.db.set_value(DT, name, "checkpoints", "{}", update_modified=False)
    finished = [work for (work,) in frappe.db.sql(f"""SELECT DISTINCT c.work FROM `tab{CP}` c
        JOIN `tab{DT}` w ON w.name = c.work WHERE w.status IN ('succeeded', 'cancelled') AND w.next_run < %s
        LIMIT {PRUNE_BATCH}""", cutoff)]
    if finished:
        frappe.db.sql(f"DELETE FROM `tab{CP}` WHERE work IN %(names)s", {"names": tuple(finished)})
    from .inputs import remove
    # Cleanup does not alter the retained receipt, so always selecting the first
    # batch would permanently miss later files after a failed immediate cleanup.
    cursor_key = "pearnly:ocr:prune_cursor"
    cursor = frappe.cache.get_value(cursor_key) or ""
    terminal = frappe.get_all(DT, filters={"status": ["not in", ACTIVE],
            "next_run": ["<", cutoff], "name": [">", cursor]}, pluck="name",
            order_by="name asc", limit_page_length=PRUNE_BATCH)
    for name in terminal:
        remove(name)
    frappe.cache.set_value(cursor_key, terminal[-1] if len(terminal) == PRUNE_BATCH else "")


def metrics():
    """Operator CLI readback; no document contents or customer identity in output."""
    rows = frappe.db.sql(f"""SELECT status, COUNT(*) AS count, MIN(expires_at) AS oldest_expiry
        FROM `tab{DT}` GROUP BY status""", as_dict=True)
    now, cfg = utcnow(), budget.config()
    return {"site": frappe.local.site, "limits": cfg, "states": [
        {"status": row.status, "count": row.count,
         "oldest_age_seconds": max(0, int((now - row.oldest_expiry).total_seconds()) + cfg["max_age"])
         if row.status in ACTIVE and row.oldest_expiry else None} for row in rows]}
