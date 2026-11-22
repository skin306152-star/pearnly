# -*- coding: utf-8 -*-
"""LINE 上要等结果的动作：先显示 LINE 自带的三个点，结果到了用这次的回复令牌回结果；等不到回兜底（用户 11-22）。

多产品共用（ERP、Cowork；DMS 已有自己的同类做法 `pearnly_dms.line.out._SlowReply`）。用户 11-22：「LINE 这边全部会触发执行状态
的指令都要优先用 LINE 原生的状态，我们的文案才是兜底」。评估：docs/work/2026-11-22-line-save-result-评估.md。

一个动作一个键（产品自己起，如 ``erp:doc:<单据>``）：

- :func:`start`（webhook 里、交单之后）：发 loading（`line.start_loading`，秒数取不小于等待时长的 5 的倍数、最多 60），在不淘汰的
  Redis（`execution.state`，键按站点加前缀）记「待回」（带回复令牌，约 70 秒过期），再起一个 :data:`Timer` 计时线程；到点原子取走
  「待回」（GETDEL），取到才用这张令牌回兜底消息（「录入中」+【查看结果】之类）。兜底消息在 start 时、请求上下文里翻译好建好，
  计时线程里不碰站点上下文与数据库。
- :func:`settle`（结果落库那一刻，请求或后台任务里都行）：原子取走「待回」，取到就用同一张令牌回结果；取不到（计时线程已回过兜底、
  或没在等）回 False。同一动作只回一次，令牌只用一次：两边都靠同一个 GETDEL，谁先取到谁回。
- 结果落库的地方常常不在发起人的身份里（电脑的回执请求、每分钟的轮询），也拼不出这个人那句话：:func:`start` 带 ``resume``（产品
  的函数路径与参数）与 ``topics``，落库处调 :func:`wake`（按键）或 :func:`wake_topic`（按主题，如共用机器通道的请求号）；有人在等
  就排一个短任务 :func:`resume`：按发起人的身份与语言调产品的函数，回了消息就 :func:`settle`，回 None（还没好）就继续等。

为什么不用 ``frappe.enqueue`` 排一个睡 20 秒的任务、或排一个延时任务：锁定版 Frappe 的 worker 关了 RQ 的定时投递
（``background_jobs.py`` 的 ``with_scheduler=False``），调度器最快按分钟轮询，做不了 20 秒的兜底；睡等又要占一个 short 进程
（原来收款放行就是这样占着等）。计时线程只占一个线程栈，不占请求槽与队列；网页进程在等待期间重启，计时线程丢失 → 这一单不回兜底，
结果仍在落库时回（令牌还有效时），否则人在网页 / 下次操作看到状态（评估第 8 栏，记录、不另补）。

LINE 规则（官方文档）：回复令牌只能用一次（用过的会被拒），收到后约 1 分钟内有效；loading 只在一对一聊天里显示，新消息到达或到时
就消失。这里只回复、不推送。
"""
import json
import logging
import secrets
import threading

import frappe

logger = logging.getLogger(__name__)

WAIT_SECONDS = 20  # 等结果的时长：先 20 秒（同收款卡原来的等待），真机实测令牌有效时长后再定，不超过 55 秒
LIFE_SECONDS = 70  # 「待回」在 Redis 里留多久：比令牌有效期略长，过了就当没在等
_KEY = "pearnly:line:receipt:"
_TOPIC = "pearnly:line:receipt-topic:"
# 计时器可注入（离线测试换成不睡的假计时器）：签名同 ``threading.Timer(seconds, function)``，要有 ``daemon`` 与 ``start()``
Timer = threading.Timer


def _store():
    from pearnly_core.execution import state

    return state.store()


def _line():
    from pearnly_core import line

    return line


def _decode(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    except (ValueError, AttributeError):
        return None


def loading_seconds(wait) -> int:
    """loading 的秒数：不小于等待时长的 5 的倍数，5–60（LINE 只认这些值）。"""
    seconds = max(5, int(wait or 0))
    return min(60, -(-seconds // 5) * 5)


def start(key, line_user_id, reply_token, fallback_messages, wait=None, *, resume="", args=None,
          topics=(), user=None) -> bool:
    """开始等这个动作的结果（见模块说明）。没有令牌或没有键回 False，什么都不做（调用方照旧当场回）。

    ``resume``：产品的函数路径（``fn(**args) -> list | None``，在发起人 ``user``（缺省当前会话用户）的身份与当前语言里跑），
    结果落库时 :func:`wake` / :func:`wake_topic` 排它；``topics``：落库处按主题叫醒用的名字。"""
    if not key or not reply_token:
        return False
    wait = int(wait or WAIT_SECONDS)
    line = _line()
    try:
        line.start_loading(line_user_id, loading_seconds(wait))
    except Exception:  # noqa: BLE001 —— 三个点出不来只是少了等待的样子，不挡后面
        logger.warning("[line receipt] loading unavailable", exc_info=True)
    try:
        client = _store()
        full = client.make_key(_KEY + key)
        mark = secrets.token_hex(8)
        client.set(full, json.dumps({
            "token": reply_token, "to": line_user_id or "", "mark": mark, "resume": resume or "", "args": args or {},
            "user": user or getattr(getattr(frappe, "session", None), "user", "") or "",
            "lang": getattr(frappe.local, "lang", None) or ""}, ensure_ascii=False, default=str), ex=LIFE_SECONDS)
        for topic in topics or ():
            name = client.make_key(_TOPIC + topic)
            client.sadd(name, key)
            client.expire(name, LIFE_SECONDS)
    except Exception:  # noqa: BLE001 —— 记不下就当场回兜底：不能让人什么都收不到
        logger.warning("[line receipt] could not note the wait", exc_info=True)
        return line.reply(reply_token, list(fallback_messages or ()))
    send = line.reply_sender(reply_token)
    messages = list(fallback_messages or ())

    def fire():
        try:
            raw = client.getdel(full)
        except Exception:  # noqa: BLE001
            logger.warning("[line receipt] fallback could not read the wait")
            return
        found = _decode(raw)
        if not found:
            return  # 结果先到、已由 settle 回过
        if found.get("mark") != mark:
            # 同一个键又开始了一轮（又点了一次）：那一轮的「待回」还给它，由它自己的计时器或结果去回
            try:
                client.set(full, raw, ex=LIFE_SECONDS, nx=True)
            except Exception:  # noqa: BLE001
                pass
            return
        send(messages)

    timer = Timer(wait, fire)
    timer.daemon = True
    timer.start()
    return True


def _take(key):
    try:
        client = _store()
        return _decode(client.getdel(client.make_key(_KEY + key)))
    except Exception:  # noqa: BLE001
        logger.warning("[line receipt] could not take the wait", exc_info=True)
        return None


def _peek(key):
    try:
        client = _store()
        return _decode(client.get(client.make_key(_KEY + key)))
    except Exception:  # noqa: BLE001
        return None


def pending(key) -> bool:
    """这个动作此刻还在等结果（还没回过结果、也没回过兜底）。"""
    return bool(key) and _peek(key) is not None


def settle(key, messages) -> bool:
    """结果到了：原子取走「待回」，取到就用同一张令牌回 ``messages``（回复成功回 True）；取不到回 False（已回过兜底或没在等）。"""
    if not key or not messages:
        return False
    found = _take(key)
    if not found or not found.get("token"):
        return False
    return bool(_line().reply(found["token"], list(messages)[:5]))


def wake(key) -> bool:
    """结果落库处叫（同一事务里即可）：这个动作有人在等、登记了 ``resume`` 就排一个提交后的短任务 :func:`resume`。"""
    found = _peek(key) if key else None
    if not found or not found.get("resume"):
        return False
    frappe.enqueue("pearnly_core.line_ui.receipt.resume", queue="short", enqueue_after_commit=True, key=key)
    return True


def wake_topic(topic) -> int:
    """按主题叫醒（如共用机器通道读好了一件请求）：主题下还在等的动作逐个 :func:`wake`。回叫醒了几个。"""
    if not topic:
        return 0
    try:
        client = _store()
        keys = client.smembers(client.make_key(_TOPIC + topic)) or ()
    except Exception:  # noqa: BLE001 —— 叫不醒只是等到兜底，不影响落库本身
        logger.warning("[line receipt] could not read the topic", exc_info=True)
        return 0
    woken = 0
    for key in keys:
        woken += int(wake(key.decode() if isinstance(key, bytes) else key))
    return woken


def resume(key):
    """（短任务）按发起人的身份与语言调产品的函数；回了消息就 :func:`settle`，回 None 就继续等（落库处下次再叫，或到点回兜底）。"""
    from pearnly_core.execution import language

    found = _peek(key)
    if not found or not found.get("resume"):
        return
    previous = frappe.session.user
    try:
        if found.get("user"):
            frappe.set_user(found["user"])
        with language.use(found.get("lang") or ""):
            messages = frappe.get_attr(found["resume"])(**(found.get("args") or {}))
            if messages:
                settle(key, messages)
    except Exception:  # noqa: BLE001 —— 拼不出结果就等兜底；记日志
        frappe.db.rollback()
        frappe.log_error(title="LINE receipt resume failed", message=frappe.get_traceback()[-1500:])
    finally:
        frappe.set_user(previous)


def after_commit(function) -> None:
    """落库处用：事务提交之后再做（回执还没提交就回了「成功」，回滚了就错了）。没有提交钩子（离线替身）就当场做。"""
    hooks = getattr(getattr(frappe, "db", None), "after_commit", None)
    if hooks is not None and hasattr(hooks, "add"):
        hooks.add(function)
    else:
        function()


__all__ = ["LIFE_SECONDS", "Timer", "WAIT_SECONDS", "after_commit", "loading_seconds", "pending", "resume", "settle",
           "start", "wake", "wake_topic"]
