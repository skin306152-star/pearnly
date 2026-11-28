# -*- coding: utf-8 -*-
"""LINE 事件按用户一条道（三个产品共用，④ 11-28）。

为什么：同一个人连发几条（10 张图、图加手机号、连点按钮），LINE 会几乎同时投来好几个 webhook 请求。以前每个请求
在自己的网页线程里当场跑处理器，几路同时读改写这个人的会话：ERP 回「Could not store this document」那张丢了、
Cowork 等锁满 60 秒报错、DMS 一条没回复。

现在：

- **收件**（:func:`store`，webhook 里）：验签之后每条一对一事件落一行 `Pearnly LINE Event`（queued，只存处理器要读的
  几项，不存图片本体）→ 提交 → 回 200。重投的同一事件号插不进去，跳过。
- **道**（:func:`acquire` / :func:`drain`）：每个 LINE 用户一行 `Pearnly LINE Lane`，带条件的 UPDATE 影响 1 行才算拿到；
  拿不到直接走（持道的会接着取到这几条），不等、不报错。拿到的按 LINE 时间、imageSet 序号、到达时刻逐条跑现有的
  处理器（`line._dispatch_with_feedback`），每条开头提交、在 `line_session.serialized` 块里跑，会话读到的一定是最新的。
  道空了先放道再查一次，免得放道的那一刻新到的事件没人管。
- **在哪跑**：webhook 回了 200 之后在网页进程里起一个线程排空（自己建、关站点上下文和数据库连接）；每进程最多
  :data:`THREADS` 条，满了交给短队列。每分钟 :func:`sweep` 兜底：有排队却没人持道（进程被回收、线程死了）的用户交给短队列。
- **不重放**：处理到一半进程没了的那条（processing 且租约到点）记 failed，不再跑一遍——处理器里有不按消息号去重的
  写入和扣费。排队超过 :data:`EXPIRE_SECONDS` 才轮到的是真故障：记 expired，按「请稍后重试」推送告诉本人（罕见，记数）。
- **保留期**：处理完那一刻清掉内容（文字、回复令牌）；行留 :data:`KEEP_DAYS` 天挡 LINE 重投，之后 :func:`prune` 删掉。
"""

from __future__ import annotations

import contextvars
import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import frappe

logger = logging.getLogger(__name__)

EVENT = "Pearnly LINE Event"
LANE = "Pearnly LINE Lane"
# 道的租约：一条事件最多等串行锁 60 秒再加处理时间；进程死了到点后扫描（最长 60 秒）接走，合计在过期线以内
LEASE_SECONDS = 90
# 排队这么久才轮到：真故障，不再跑处理器（回复令牌早过期），告诉本人重发
EXPIRE_SECONDS = 180
# 刚到的给持道的人一点时间，扫描不抢
SWEEP_GRACE_SECONDS = 5
SWEEP_LIMIT = 50
THREADS = 8
KEEP_DAYS = 7
PRUNE_BATCH = 1000
PRUNE_ROUNDS = 10
FINISHED = ("done", "ignored", "failed", "expired")

_slots = threading.BoundedSemaphore(THREADS)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── 收件 ────────────────────────────────────────────────────────────────────


def user_of(event: dict) -> str:
    """一对一聊天的事件 → 发事件的 LINE 用户；群组、没有用户的 → ""（照旧当场处理）。"""
    source = (event or {}).get("source") or {}
    if source.get("type", "user") != "user":
        return ""
    return str(source.get("userId") or "")


def content_of(event: dict) -> dict:
    """处理器会读的那几项（三个产品逐个查过）；不存图片本体，文件只存消息号。"""
    out = {key: event[key] for key in ("type", "mode", "timestamp", "webhookEventId", "replyToken") if key in event}
    source = event.get("source") or {}
    out["source"] = {key: source[key] for key in ("type", "userId") if key in source}
    message = event.get("message")
    if isinstance(message, dict):
        out["message"] = {key: message[key] for key in ("id", "type", "text", "fileName", "fileSize", "imageSet",
                                                         "quotedMessageId", "packageId", "stickerId") if key in message}
    postback = event.get("postback")
    if isinstance(postback, dict):
        out["postback"] = {key: postback[key] for key in ("data", "params") if key in postback}
    delivery = event.get("deliveryContext")
    if isinstance(delivery, dict) and "isRedelivery" in delivery:
        out["deliveryContext"] = {"isRedelivery": delivery["isRedelivery"]}
    return out


def store(event: dict, line_user_id: str, arrived_at: Optional[datetime] = None) -> bool:
    """落一行 queued（调用方提交）。同一事件号已经有了 → False（LINE 重投，跳过）。"""
    event_id = str(event.get("webhookEventId") or "") or "local-" + uuid.uuid4().hex
    if frappe.db.exists(EVENT, event_id):
        return False
    arrived_at = arrived_at or utcnow()
    try:
        stamp = int(event.get("timestamp") or 0)
    except (TypeError, ValueError):
        stamp = 0
    if stamp <= 0:
        stamp = int(arrived_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
    image_set = ((event.get("message") or {}).get("imageSet") or {}) if isinstance(event.get("message"), dict) else {}
    try:
        set_index = int(image_set.get("index") or 0)
    except (TypeError, ValueError):
        set_index = 0
    frappe.get_doc({
        "doctype": EVENT, "event_id": event_id, "source": "line_webhook", "status": "queued",
        "line_user_id": line_user_id, "arrived_at": arrived_at, "line_timestamp": stamp, "set_index": set_index,
        "attempts": 0, "content": json.dumps(content_of(event), ensure_ascii=False),
    }).insert(ignore_permissions=True, set_name=event_id, ignore_if_duplicate=True)
    return True


def start(line_user_id: str) -> None:
    """收件提交之后调：拿到道就排空（回了 200 之后起线程；没有请求的进程内调用当场排空），拿不到就走。"""
    holder = uuid.uuid4().hex
    if not acquire(line_user_id, holder):
        return
    request = getattr(frappe.local, "request", None)
    after = getattr(request, "after_response", None)
    if after is None:
        _inline(line_user_id, holder)
        return
    site, sites_path = frappe.local.site, getattr(frappe.local, "sites_path", ".")
    after.add(lambda: _spawn(site, sites_path, line_user_id, holder))


def _inline(line_user_id: str, holder: str) -> None:
    """进程内脚本 / 离线流程：在当前上下文里排空，完了把身份还回去。"""
    previous = getattr(getattr(frappe, "session", None), "user", None) or "Guest"
    try:
        drain(line_user_id, holder)
    finally:
        frappe.set_user(previous)


# ── 排空线程 ────────────────────────────────────────────────────────────────


def _spawn(site: str, sites_path: str, line_user_id: str, holder: str) -> None:
    """回应发出之后、仍在请求线程里：只起线程，不在这里排空（会占住网页线程）。线程满了交短队列。"""
    if not _slots.acquire(blocking=False):
        _hand_over(site, sites_path, line_user_id, holder)
        return
    args = (site, sites_path, line_user_id, holder)
    try:
        try:
            # 空上下文起线程：frappe.local 是上下文变量，不能和请求线程共用数据库连接（Python 3.14 起支持）
            thread = threading.Thread(target=_thread_main, args=args, name="pearnly-line-lane",
                                      context=contextvars.Context())
        except TypeError:
            thread = threading.Thread(target=_thread_main, args=args, name="pearnly-line-lane")
        # 非守护线程：网页进程按 max-requests 回收时等它把手上这条跑完（优雅退出时限内）
        thread.daemon = False
        thread.start()
    except Exception:  # noqa: BLE001
        _slots.release()
        logger.warning("[line_lane] 起线程失败，交短队列", exc_info=True)
        _hand_over(site, sites_path, line_user_id, holder)


def _thread_main(site: str, sites_path: str, line_user_id: str, holder: str) -> None:
    try:
        frappe.init(site, sites_path=sites_path, force=True)
        frappe.connect(set_admin_as_user=False)
        frappe.set_user("Guest")
        drain(line_user_id, holder)
    except Exception:  # noqa: BLE001 —— 线程里的异常没人接：记下，道到点由扫描接走
        logger.warning("[line_lane] 排空线程出错", exc_info=True)
    finally:
        try:
            if getattr(frappe.local, "db", None):
                frappe.db.rollback()
        except Exception:  # noqa: BLE001
            pass
        try:
            frappe.destroy()
        except Exception:  # noqa: BLE001
            pass
        _slots.release()


def _hand_over(site: str, sites_path: str, line_user_id: str, holder: str) -> None:
    """线程满了：在一个临时上下文里放道、排短队列（请求线程的上下文此时可能已在收尾）。"""
    try:
        if getattr(frappe.local, "site", None) == site and getattr(frappe.local, "db", None):
            release(line_user_id, holder)
            frappe.db.commit()
            _enqueue(line_user_id)
            return
    except Exception:  # noqa: BLE001
        logger.warning("[line_lane] 交短队列失败，等扫描", exc_info=True)
        return
    logger.warning("[line_lane] 站点上下文不在，等扫描接走")


def run(line_user_id: str) -> None:
    """短队列任务（扫描、线程满了）：拿到道就排空，拿不到就走。"""
    holder = uuid.uuid4().hex
    if acquire(line_user_id, holder):
        frappe.set_user("Guest")
        drain(line_user_id, holder)


def _enqueue(line_user_id: str) -> None:
    job = "pearnly-line-lane:" + str(uuid.uuid5(uuid.NAMESPACE_URL, line_user_id))
    frappe.enqueue("pearnly_core.line_lane.lane.run", queue="short", job_id=job, deduplicate=True,
                   line_user_id=line_user_id)


# ── 租约 ────────────────────────────────────────────────────────────────────


def _changed() -> bool:
    return frappe.db._cursor.rowcount == 1


def acquire(line_user_id: str, holder: str) -> bool:
    """抢道：租约空或到点才拿得到（影响 1 行）。提交。"""
    if not frappe.db.exists(LANE, line_user_id):
        frappe.get_doc({"doctype": LANE, "line_user_id": line_user_id}).insert(
            ignore_permissions=True, ignore_if_duplicate=True)
        frappe.db.commit()
    now = utcnow()
    frappe.db.sql(
        "update `tabPearnly LINE Lane` set holder=%s, lease_until=%s, modified=%s "
        "where name=%s and (lease_until is null or lease_until<%s)",
        (holder, now + timedelta(seconds=LEASE_SECONDS), now, line_user_id, now),
    )
    got = _changed()
    frappe.db.commit()
    return got


def renew(line_user_id: str, holder: str) -> bool:
    now = utcnow()
    frappe.db.sql(
        "update `tabPearnly LINE Lane` set lease_until=%s, modified=%s where name=%s and holder=%s",
        (now + timedelta(seconds=LEASE_SECONDS), now, line_user_id, holder),
    )
    got = _changed()
    frappe.db.commit()
    return got


def release(line_user_id: str, holder: str) -> None:
    frappe.db.sql(
        "update `tabPearnly LINE Lane` set holder=null, lease_until=null, modified=%s where name=%s and holder=%s",
        (utcnow(), line_user_id, holder),
    )


# ── 排空 ────────────────────────────────────────────────────────────────────


def drain(line_user_id: str, holder: str) -> None:
    """持道时一条一条处理本人排队的事件；道被接走或排空就走。"""
    while True:
        frappe.db.commit()  # 结束读视图：下一条读到的都是最新的
        if not renew(line_user_id, holder):  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句
            return
        _interrupted(line_user_id)  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句
        name = _next(line_user_id)  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句
        if not name:
            release(line_user_id, holder)  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句
            frappe.db.commit()
            # 放道的那一刻新到的事件：它的 webhook 可能刚好没抢到道
            if not _next(line_user_id) or not acquire(line_user_id, holder):  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句
                return
            continue
        _run(name, line_user_id, holder)  # loop-query: 道里一条事件一轮（逐条处理就是道的本意），每轮几条主键 / 索引语句


def _next(line_user_id: str) -> str:
    rows = frappe.db.sql(
        "select name from `tabPearnly LINE Event` where line_user_id=%s and status='queued' "
        "order by line_timestamp, set_index, arrived_at, name limit 1",
        (line_user_id,),
    )
    return rows[0][0] if rows else ""


def _interrupted(line_user_id: str) -> None:
    """处理到一半进程没了的那条：记 failed，不重放（处理器里有不按消息号去重的写入和扣费）。

    按事实分（主会话 11-28）：处理器已经回过本人（:func:`replied`）→ 只记 failed 与日志，不再打扰；没回过 → 本人发了消息
    却什么也没收到，归入真故障，推「请稍后重试」。"""
    now = utcnow()
    rows = frappe.db.sql(
        "select name, content, replied from `tabPearnly LINE Event` where line_user_id=%s and status='processing' "
        "and lease_until<%s order by line_timestamp limit 20",
        (line_user_id, now),
        as_dict=True,
    )
    for row in rows:
        answered = bool(row.get("replied")) or replied(row["name"])
        frappe.db.sql(  # loop-query: 本人中断的事件最多 20 条，逐条带条件转出
            "update `tabPearnly LINE Event` set status='failed', detail=%s, replied=%s, content=null, "
            "lease_until=null, modified=%s where name=%s and status='processing'",
            ("interrupted: not replayed" + ("" if answered else "; sender told to retry"), int(answered), now,
             row["name"]),
        )
        if not _changed():
            continue
        frappe.db.commit()
        _count("lane_interrupted")
        logger.warning("[line_lane] 事件处理到一半中断，记 failed 不重放（%s）", "已回复过本人" if answered else "没回复过，推送重试")
        if not answered:
            content = row.get("content")
            event = json.loads(content) if isinstance(content, str) and content else (content or {})
            _tell_retry(line_user_id, event, "interrupted")  # loop-query: 同上，中断事件有界


def _claim(name: str, status: str) -> bool:
    """排队中的这条认领成 processing / expired：每条事件只处理一次，就靠这一句。"""
    now = utcnow()
    lease = now + timedelta(seconds=LEASE_SECONDS) if status == "processing" else None
    frappe.db.sql(
        "update `tabPearnly LINE Event` set status=%s, attempts=attempts+1, lease_until=%s, modified=%s "
        "where name=%s and status='queued'",
        (status, lease, now, name),
    )
    got = _changed()
    frappe.db.commit()
    return got


def _finish(name: str, status: str, detail: str = "") -> None:
    """处理完：记结论与「回过本人没有」，清掉内容（文字、回复令牌）和租约。只从 processing 转出，被判中断的不改回。"""
    frappe.db.sql(
        "update `tabPearnly LINE Event` set status=%s, detail=%s, replied=%s, content=null, lease_until=null, "
        "modified=%s where name=%s and status='processing'",
        (status, (detail or "")[:500], int(replied(name)), utcnow(), name),
    )
    frappe.db.commit()


# ── 回过本人没有 ────────────────────────────────────────────────────────────
#
# 处理器发出回复 / 推送之前（`line._lane_sent`）落一个标记：宁可多记不可漏记——发到一半进程没了也算回过，免得让他重发、
# 事情办了两遍。标记放 Redis，不跟处理器的数据库事务走：事务要到处理完才提交，中途进程没了就什么都不剩；
# 处理完 / 判中断时再抄到事件行的 `replied` 上。

REPLIED_PREFIX = "pearnly:line:lane-replied:"
REPLIED_SECONDS = 86400


def _mark_for(name: str):
    """给这条事件的「回过本人」标记函数：可在任何线程调（计时线程里的兜底回复也算），不碰站点上下文。"""
    try:
        cache = frappe.cache()
        key = cache.make_key(REPLIED_PREFIX + name)
    except Exception:  # noqa: BLE001
        logger.warning("[line_lane] 取不到 Redis，回复标记记不上", exc_info=True)
        return lambda: None

    def mark():
        try:
            cache.execute_command("SET", key, "1", "EX", REPLIED_SECONDS)
        except Exception:  # noqa: BLE001 —— 记不上不影响发送本身
            logger.warning("[line_lane] 回复标记记不上", exc_info=True)

    return mark


def replied(name: str) -> bool:
    try:
        cache = frappe.cache()
        return bool(cache.execute_command("GET", cache.make_key(REPLIED_PREFIX + name)))
    except Exception:  # noqa: BLE001 —— 读不到按「回过」算：宁可少打扰一次，不让他把办成的事再办一遍
        logger.warning("[line_lane] 读不到回复标记，按已回复处理", exc_info=True)
        return True


def _run(name: str, line_user_id: str, holder: str) -> None:
    from pearnly_core import line, line_session

    row = frappe.db.get_value(EVENT, name, ["content", "arrived_at"], as_dict=True) or {}
    content = row.get("content")
    event = json.loads(content) if isinstance(content, str) and content else (content or {})
    arrived = frappe.utils.get_datetime(row.get("arrived_at")) if row.get("arrived_at") else utcnow()
    waited = max(0.0, (utcnow() - arrived).total_seconds())
    if waited > EXPIRE_SECONDS:
        if _claim(name, "expired"):
            frappe.db.sql("update `tabPearnly LINE Event` set content=null where name=%s", (name,))
            frappe.db.commit()
            _tell_expired(line_user_id, event, waited)
        return
    if not _claim(name, "processing"):
        return
    _reset()
    frappe.local.pearnly_line_lane_mark = {"user": line_user_id, "mark": _mark_for(name)}
    try:
        with line_session.serialized(line_user_id):
            handled = line._dispatch_with_feedback(event, waited=waited)
        _finish(name, "done" if handled else "ignored")
    except Exception as exc:  # noqa: BLE001
        # 先回滚处理器做了一半的库内写，再记日志（log_error 是插入、不提交，回滚在后会把它一起冲掉）
        frappe.db.rollback()
        try:
            frappe.log_error(title="LINE 事件处理失败",
                             message=f"event={name}\nhandler={getattr(exc, 'pearnly_line_handler', '')}\n{str(exc)[:600]}")
            frappe.db.commit()
        except Exception:  # noqa: BLE001 —— 记不上日志也要把这条记成 failed
            frappe.db.rollback()
            logger.warning("[line_lane] 记错误日志失败", exc_info=True)
        _finish(name, "failed", str(exc)[:400])
    finally:
        _reset()


def _reset() -> None:
    """每条事件之间：身份回 Guest、语言回站点默认、上一条的攒发 / 三个点 / 读到的会话版本不带进下一条。"""
    from pearnly_core import line_session

    try:
        frappe.set_user("Guest")
    except Exception:  # noqa: BLE001
        pass
    local = frappe.local
    lane_lang = getattr(local, "pearnly_line_lane_lang", None)
    if lane_lang is None:
        lane_lang = getattr(local, "lang", None) or "en"
        local.pearnly_line_lane_lang = lane_lang
    local.lang = lane_lang
    local.pearnly_line_batch = None
    local.pearnly_line_feedback = None
    local.pearnly_line_lane_mark = None
    line_session.forget_seen()


def _count(kind: str) -> None:
    from pearnly_core import line

    line._count(kind)


def _tell_expired(line_user_id: str, event: dict, waited: float) -> None:
    """排队超过时限才轮到：处理器不跑了，推送「请稍后重试」（回复令牌早过期；罕见路径，记数）。"""
    _count("lane_expired")
    logger.warning("[line_lane] 事件排队 %.0f 秒才轮到，记 expired", waited)
    _tell_retry(line_user_id, event, "expired")


def _tell_retry(line_user_id: str, event: dict, why: str) -> None:
    """真故障告诉本人一次：只对他发的消息 / 点的按钮；推送带按事件号算的去重键，扫描重进也只推一条。"""
    from pearnly_core import line

    if event.get("type") not in ("message", "postback"):
        return
    try:
        text = frappe._("Please try again later", lang=line.user_language(line_user_id))
        key = str(uuid.uuid5(uuid.NAMESPACE_URL, "pearnly-line-%s:%s" % (why, event.get("webhookEventId") or "")))
        line.push(line_user_id, [{"type": "text", "text": text}], retry_key=key)
    except Exception:  # noqa: BLE001
        logger.warning("[line_lane] 重试通知没发出去", exc_info=True)


# ── 兜底与清理 ──────────────────────────────────────────────────────────────


def sweep() -> None:
    """每分钟：有排队 / 处理中却没人持道（进程被回收、线程死了、线程满了）的用户交给短队列。"""
    now = utcnow()
    users = frappe.db.sql(
        "select distinct e.line_user_id from `tabPearnly LINE Event` e "
        "left join `tabPearnly LINE Lane` l on l.name=e.line_user_id "
        "where e.status in ('queued', 'processing') and e.arrived_at<%s and e.line_user_id>'' "
        "and (l.lease_until is null or l.lease_until<%s) limit %s",
        (now - timedelta(seconds=SWEEP_GRACE_SECONDS), now, SWEEP_LIMIT),
    )
    for (line_user_id,) in users:
        try:
            _enqueue(line_user_id)
        except Exception:  # noqa: BLE001 —— 一个用户排不进（含 rq 去重读旧任务出错）不卡住整轮
            logger.warning("[line_lane] 扫描排队失败", exc_info=True)


def prune() -> None:
    """每天：处理完超过 :data:`KEEP_DAYS` 天的事件行删掉（挡重投够了）；闲了一天、没有待办的道删掉。删不完明天接着删。"""
    cut = utcnow() - timedelta(days=KEEP_DAYS)
    for _round in range(PRUNE_ROUNDS):
        frappe.db.sql(  # loop-query: 分批删，最多 PRUNE_ROUNDS 批、每批 LIMIT
            "delete from `tabPearnly LINE Event` where status in %s and modified<%s limit %s",
            (FINISHED, cut, PRUNE_BATCH),
        )
        gone = frappe.db._cursor.rowcount
        frappe.db.commit()
        if gone < PRUNE_BATCH:
            break
    idle = utcnow() - timedelta(days=1)
    frappe.db.sql(
        "delete from `tabPearnly LINE Lane` where (lease_until is null or lease_until<%s) and modified<%s "
        "and not exists (select 1 from `tabPearnly LINE Event` e where e.line_user_id=`tabPearnly LINE Lane`.name "
        "and e.status in ('queued', 'processing')) limit %s",
        (utcnow(), idle, PRUNE_BATCH),
    )
    frappe.db.commit()
