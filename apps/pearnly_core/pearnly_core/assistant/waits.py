"""挂着的工具调用（评估 docs/work/2026-11-06-cc-ocr-auto-continue-评估.md，主会话 11-06 审定）：工具交给后台、几分钟后才有
结论的事（识别）时，这次调用先不交回引擎，等那件事有了结论再交，CC 在同一轮里接着做原本要做的事，用户不必再问一句。

- 挂起：工具在执行中调 `park(等待键, 核对函数)`，照常返回一段「还在做」的结果文本。`toolrun._finish` 看到挂起就不交回，
  执行记录照旧 `running`（对话页按「本站正在执行」显示这一步），记下等待键、核对函数、远端会话和上限时刻
  （`wait_until`，有值 = 挂着）。会话停在等工具结果，不开模型、不花 token。
- 交回（`_settle`）：按执行记录里的会话主人身份调核对函数 `check(工具参数, 挂起时的结果文本, final)`，回
  `{"done", "text", "is_error", "progress"}`。有结论（或 `final` 给了原因，必须交）就抢占这条记录（`wait_until` 清空、状态写成
  done / error，只交一次），推执行记录（带 `settled`，对话页马上补拉、接上推送），再把结果交回引擎。
- 什么时候交：等的那件事结束时产品调 `finished(键)`（排一个短任务）；用户在挂着时又发消息，先按 `user_message` 交回再发
  （引擎停在等工具结果时不收用户消息）；到上限（`WAIT_MINUTES`）按 `deadline` 交回；共用的每分钟巡检（`sweep`）把结束了却
  没交回的（结束提醒漏了、进程重启）补交。点停止、这一轮结束：作废（`cancel`），之后结束了也不再交。
- 进度：等的事有了新进度时产品调 `touch(键)`，按核对函数回的 `progress` 推一条 `pearnly.progress`（按调用编号）；对话页
  重开、补拉时由 `progress_events` 现算并进来。挂起之前工具当场等的那段，工具自己按同一形状调 `report(progress)`。只用真实数字。
- 核对函数由声明工具的产品给（点路径），共用层不认识产品的等待内容。
"""
import json
from datetime import datetime, timedelta, timezone

import frappe

from pearnly_core.transaction import snapshot

from pearnly_core.assistant import contracts

TOOL_RUN = "Pearnly Assistant Tool Run"
# 挂着的上限：按识别实际最长耗时定（一份 12 页约 3–4 分钟，排队高峰再加几分钟），到了就把「还在做」交回、结束这一轮
WAIT_MINUTES = 15
KEYS_MAX = 20
# 当场等时报的最近一条进度留多久（对话页重开、补拉时并进来，`report`）：比当场等的上限长、比挂着的上限短
INLINE_TTL = 5 * 60
SWEEP_ROWS = 100
SETTLE_JOB = "pearnly_core.assistant.waits.settle_key"
# 交回时的原因（核对函数据此写给模型的说明）：上限到了 / 用户在挂着时又发了消息 / 收尾时等的事还没结束（巡检核对用）
DEADLINE, USER_MESSAGE = "deadline", "user_message"
FIELDS = ["name", "tool_use_id", "session_id", "user", "tool", "status", "tool_input", "result", "wait_keys", "wait_check",
          "wait_remote", "wait_until"]


def _now():
    return frappe.utils.now_datetime()


# ── 工具执行中：要求挂起 ─────────────────────────────────────────────────────
def park(keys, check: str) -> None:
    """工具执行中调用：这次结果先不交回，等 `keys`（如 ``ocr:cowork.invoice:<批次>``）都结束再交。`check` 是核对函数的点路径。
    不在对话的工具执行里（没有 `toolrun` 放的对话信息）就不挂，照常交回。"""
    keys = [str(key) for key in dict.fromkeys(keys or ()) if key][:KEYS_MAX]
    context = getattr(frappe.local, "pearnly_assistant", None)
    if not keys or not check or context is None:
        return
    context["wait"] = {"keys": keys, "check": str(check)}


def requested() -> dict | None:
    """这次执行有没有要求挂起（`toolrun._finish` 读）。"""
    return (getattr(frappe.local, "pearnly_assistant", None) or {}).get("wait")


def hold(run_name: str, remote: str, wait: dict) -> None:
    """`toolrun._finish`：这条执行记录挂起（结果文本已写进 `result`）。之后的交回都靠这几列。"""
    frappe.db.set_value(TOOL_RUN, run_name, {"wait_keys": json.dumps(wait["keys"]), "wait_check": wait["check"],
                                              "wait_remote": remote,
                                              "wait_until": _now() + timedelta(minutes=WAIT_MINUTES)})


# ── 交回 ────────────────────────────────────────────────────────────────────
def _input(row) -> dict:
    try:
        value = json.loads(row.get("tool_input") or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _check(row, final):
    """以会话主人身份调核对函数；出错时不是必须交就当没结论，必须交就交回挂起时的结果。"""
    from pearnly_core.assistant import toolrun

    toolrun.fresh()
    toolrun._use_lang(row.session_id)
    try:
        found = frappe.get_attr(row.wait_check)(_input(row), row.result or "", final)
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Assistant wait check failed")
        frappe.db.commit()  # 日志随即提交：后面结束读快照的回滚不把它带走（11-19 回滚审计）
        found = None
    if not isinstance(found, dict):
        return {"done": bool(final), "text": row.result or "", "is_error": False} if final else None
    return found


def _as_owner(user):
    class _Owner:
        def __enter__(self):
            self.before = frappe.session.user if getattr(frappe.local, "session", None) else None
            if self.before != user:
                frappe.set_user(user)

        def __exit__(self, *exc):
            if self.before and self.before != user:
                frappe.set_user(self.before)
            return False
    return _Owner()


def _row(name):
    found = frappe.db.get_value(TOOL_RUN, name, FIELDS, as_dict=True)
    return found if found and found.status == "running" and found.wait_until else None


def _settle(name: str, final: str | None = None) -> bool:
    """核对一条挂着的调用；有结论（或 `final` 给了原因）就交回。返回是否交回了。同一条只交一次（抢占）。"""
    from pearnly_core.assistant import toolrun

    snapshot.fresh("waits._settle")  # 结束调用方的读快照（同 `toolrun` 文件头「锁与快照」）
    row = _row(name)
    if row is None:
        return False
    with _as_owner(row.user):
        if toolrun.owner_disabled():
            found = {"done": True, "text": toolrun.OWNER_DISABLED, "is_error": True}
        else:
            found = _check(row, final)
        if not found or not (found.get("done") or final):
            snapshot.fresh("waits._settle")
            return False
        text, is_error = str(found.get("text") or row.result or ""), bool(found.get("is_error"))
        frappe.db.commit()  # 核对函数自己写的（如把进 / 销项写到票上）先提交，执行记录另起短事务
        try:
            frappe.db.sql(f"UPDATE `tab{TOOL_RUN}` SET status=%s, result=%s, wait_until=NULL, modified=%s "
                          "WHERE name=%s AND status='running' AND wait_until IS NOT NULL",
                          ("error" if is_error else "done", text[:60000], frappe.utils.now(), name))
            claimed = frappe.db._cursor.rowcount == 1
            frappe.db.commit()
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            return False
        if not claimed:
            return False
        toolrun._publish_run(row.session_id, row.tool_use_id, "error" if is_error else "done", row.user, settled=True)
        toolrun._hand_back(row.session_id, row.wait_remote, row.tool_use_id, text, is_error)
        try:
            # 交回后会话在跑：记最后活动时间（侧栏按它排），同用户发消息
            from pearnly_core.assistant import context

            session = frappe.db.get_value(context.SESSION, {"session_id": row.session_id}, "name")
            if session and context.active(session, wait=False):
                frappe.db.commit()
        except Exception:
            frappe.db.rollback()
    return True


def _parked(filters_sql: str, values: tuple, limit: int = SWEEP_ROWS) -> list:
    """挂着的执行记录（`wait_until` 有值的才在索引里，只扫挂着的）。"""
    rows = frappe.db.sql(f"SELECT name FROM `tab{TOOL_RUN}` WHERE wait_until IS NOT NULL AND status='running' "
                         f"{filters_sql} ORDER BY wait_until LIMIT {int(limit)}", values)
    return [row[0] for row in rows or ()]


def quietly(label: str, fn, *args, default=0):
    """附带的一步（发消息前交回、停止时作废）：出错回滚、记一笔，不挡主流程（发消息、停止照常）。"""
    try:
        return fn(*args)
    except Exception as err:
        try:
            frappe.db.rollback()
            frappe.log_error(title=label)
        except Exception:
            print(f"assistant wait {label}: {type(err).__name__}", flush=True)
        return default


def _by_key(key: str) -> list:
    return _parked("AND wait_keys LIKE %s", (f'%{json.dumps(str(key))}%',))


def finished(key: str) -> None:
    """产品：等的这件事结束了（成功、失败都算）。有挂着等它的调用就排一个短任务去核对、交回（提交后；同一个键只排一个）。"""
    try:
        if not _by_key(key):
            return
        frappe.enqueue(SETTLE_JOB, queue="short", enqueue_after_commit=True, job_id=f"assistant-wait-{key}"[:140],
                       deduplicate=True, key=key)
    except Exception:
        frappe.log_error(title="Assistant wait notify failed")


def settle_key(key: str) -> None:
    """后台：等这个键的调用逐条核对，键都结束了的交回。"""
    for name in _by_key(key):
        try:
            _settle(name)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Assistant wait settle failed")
            frappe.db.commit()  # 日志随即提交：后面结束读快照的回滚不把它带走（11-19 回滚审计）


def release(conversation: str, reason: str = USER_MESSAGE) -> int:
    """这段对话里挂着的调用全部交回（用户在挂着时又发消息：先交回，引擎才收这条消息）。返回交回了几条。"""
    count = 0
    for name in _parked("AND session_id=%s", (conversation,)):
        count += _settle(name, final=reason)
    return count


def cancel(conversation: str) -> int:
    """点了停止 / 这一轮结束：挂着的作废（引擎不再等这些结果），之后等的事结束了也不再交回。返回作废了几条。"""
    from pearnly_core.assistant import toolrun

    snapshot.fresh("waits.cancel")
    names = _parked("AND session_id=%s", (conversation,))
    changed = []
    for name in names:
        try:
            frappe.db.sql(f"UPDATE `tab{TOOL_RUN}` SET status='cancelled', wait_until=NULL, modified=%s "
                          "WHERE name=%s AND status='running' AND wait_until IS NOT NULL", (frappe.utils.now(), name))
            hit = frappe.db._cursor.rowcount == 1
            frappe.db.commit()
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            continue
        if hit:
            changed.append(name)
    for name in changed:
        row = frappe.db.get_value(TOOL_RUN, name, ["session_id", "tool_use_id", "user"], as_dict=True)
        if row:
            toolrun._publish_run(row.session_id, row.tool_use_id, "cancelled", row.user)
    return len(changed)


def sweep() -> None:
    """每分钟（共用识别巡检同一个时刻）：到上限的交回「还在做」；没到的核对一次，结束了却没交回的补交。"""
    snapshot.fresh("waits.sweep")
    now = _now()
    for name in _parked("", ()):
        try:
            until = frappe.db.get_value(TOOL_RUN, name, "wait_until")
            _settle(name, final=DEADLINE if until and until <= now else None)
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Assistant wait sweep failed")
            frappe.db.commit()  # 日志随即提交：后面结束读快照的回滚不把它带走（11-19 回滚审计）


# ── 进度 ────────────────────────────────────────────────────────────────────
def _progress(row) -> dict | None:
    found = _check(row, None)
    return _event(row.tool_use_id, (found or {}).get("progress"))


def _event(use_id, progress) -> dict | None:
    """一次调用此刻的进度事件（挂着时与当场等时同一个写法、同一个编号：同样的数页面只收一次）。数不对回 None。"""
    if not isinstance(progress, dict):
        return None
    try:
        at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        done, total = int(progress.get("done") or 0), int(progress.get("total") or 0)
        label = str(progress.get("label") or "")
        return contracts.progress_event(f"pwait_{use_id}_{label}_{done}_{total}"[:120], at, use_id, done, total, label)
    except (contracts.ContractError, ValueError, TypeError):
        return None


def report(progress) -> None:
    """工具当场等后台的事时（推送线程里先等一会儿再决定挂不挂，例如识别）：按这次调用推一条此刻的进度，`progress` 同核对函数
    回的 ``{"done", "total", "label"}``。11-10 线上压测：识别挂起前当场等的 90 秒没有进度，状态行只写「识别中」。
    最近一条另记在缓存里（`INLINE_TTL`）：对话页重开、实时通道断了靠补拉时由 `progress_events` 并进来。
    不在对话的工具执行里（没有这次调用的编号）、数不对就不推；只作展示，出错不影响工具。"""
    try:
        context = getattr(frappe.local, "pearnly_assistant", None) or {}
        use_id, conversation = context.get("tool_use_id"), context.get("session_id")
        event = _event(use_id, progress) if use_id and conversation else None
        if not event:
            return
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": conversation, "event": event},
                                user=frappe.session.user, after_commit=False)
        kept = frappe.cache.get_value(_inline_key(conversation)) or {}
        frappe.cache.set_value(_inline_key(conversation), {**kept, use_id: event}, expires_in_sec=INLINE_TTL)
    except Exception:
        pass


def _inline_key(conversation: str) -> str:
    return f"pearnly_assistant:inline_progress:{conversation}"


def touch(key: str) -> None:
    """产品：等的这件事有了新进度（读到第几页、开始读了）。推给挂着等它的那段对话（只作展示，出错不影响识别）。"""
    try:
        for name in _by_key(key):
            _push_progress(name)
    except Exception:
        pass


def announce(run_name: str) -> None:
    """`toolrun._finish`：刚挂起（已提交）就推一次此刻的进度。挂起之前识别「开始读」的那次推送还没有挂着的记录、没人收，
    要等读到下一页才再推（11-08 线上：BJC 前 8 页约 4 分钟一直没有进度）；推了页面马上显示「识别中 · 共 N 页」。"""
    try:
        _push_progress(run_name)
    except Exception:
        pass


def _push_progress(name: str) -> None:
    row = _row(name)
    if row is None:
        return
    with _as_owner(row.user):
        event = _progress(row)
    if event:
        frappe.publish_realtime(contracts.REALTIME_EVENT, {"session_id": row.session_id, "event": event},
                                user=row.user, after_commit=False)


def progress_events(conversation: str) -> list:
    """对话页重开、补拉：这段对话里挂着的调用此刻的进度（现算，不存），加上还在当场等、没挂起的调用最近报的那一条
    （`report`）。读不出就不带，不挡取事件。"""
    return quietly("Assistant wait progress failed", _progress_events, conversation, default=[])


def _progress_events(conversation: str) -> list:
    out = []
    for name in _parked("AND session_id=%s", (conversation,), limit=KEYS_MAX):
        row = _row(name)
        if row is None:
            continue
        with _as_owner(row.user):
            event = _progress(row)
        if event:
            out.append(event)
    covered = {event["tool_use_id"] for event in out}
    for use_id, event in (frappe.cache.get_value(_inline_key(conversation)) or {}).items():
        if use_id not in covered and frappe.db.get_value(TOOL_RUN, {"tool_use_id": use_id, "session_id": conversation},
                                                         "status") == "running":
            out.append(event)
    return out
