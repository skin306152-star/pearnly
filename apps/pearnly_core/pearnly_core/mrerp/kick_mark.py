"""MR.ERP 账号「被踢」标记（11-09 风险 7，主会话定 A 方案；多产品共用）。

MR.ERP 一个账号同时只能在一处登录。直写复用上一次登录的会话（`session_cache`），平时不重新登录；会计本人在浏览器里登录同一账号时，
我们的会话被踢（`session.KICKED_TEXT`）。这时在这里按账号记一个标记：

- 有标记时，所有**自动**推送（同批后续、定时巡检、自动推档位、文件夹定时、ERP 提交新单 / 每分钟巡检……）都在导入入口
  （`push.transport.MrErpImport.__enter__`）快速失败成 ``ERR_MRERP_ACCOUNT_IN_USE``，不登录、不发任何请求，记录照常进「待处理」；
- 只有人在明确的推送入口点了（Cowork 网页推送 / 待处理再推、Cowork 与 ERP 的 CC 推送工具），入口先 :func:`clear`，这次才重新登录；
- 标记与同账号互斥锁（`session_lock.account_lock`）同一层：不淘汰的状态 Redis（`execution.state.store`），键不带站点前缀、按
  `session.lock_key`（网址 + 用户名）取摘要，Cowork 与 ERP 看到同一个标记；值里带时间、哪个站点、谁，以及最近几次标记 / 清除的记录。

状态 Redis 的键都要带过期时间：标记 :data:`TTL_SECONDS`（30 天）后自动消失，相当于很久没人处理后恢复自动推送。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

CODE = "ERR_MRERP_ACCOUNT_IN_USE"
TTL_SECONDS = 30 * 24 * 3600
HISTORY_MAX = 20
_PREFIX = "pearnly:mrerp-kicked:"


def _store():
    from pearnly_core.execution import state

    return state.store()


def _name(account_key) -> str:
    # 原文（含用户名）不进键名，同 `session_lock._lock_name`
    return _PREFIX + hashlib.sha256(str(account_key or "").encode("utf-8")).hexdigest()[:32]


def _who() -> dict:
    """记录里的来源：哪个站点、谁（后台任务里是准备这张票的人）。本层不依赖 Frappe（`tests/test_mrerp_core_boundary.py`）：
    进程里已经载入了 Frappe 才读它的请求上下文，没有、或上下文没绑定，这一项空着。"""
    out = {"site": "", "by": ""}
    frappe = sys.modules.get("frappe")
    if frappe is None:
        return out
    for field, read in (("site", lambda: frappe.local.site), ("by", lambda: frappe.session.user)):
        try:
            out[field] = str(read() or "")
        except Exception:  # noqa: BLE001 — 没有请求上下文（Local 未绑定）时这一项空着
            out[field] = ""
    return out


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _load(raw) -> dict:
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _read(name) -> dict:
    """读不到状态 Redis 时当没有：紧接着的同账号互斥锁（`session_lock.account_lock`）用的是同一个 Redis，会照实失败
    （``ERR_SESSION_LOCK``），不会在读不到标记的情况下去登录。"""
    try:
        return _load(_store().get(name))
    except Exception as error:  # noqa: BLE001
        logger.warning("[mrerp-kick] state Redis unreadable, the account lock decides: %s", type(error).__name__)
        return {}


def state(account_key):
    """这个账号此刻的标记（``{"at", "site", "by", "history"}``），没有回 None。"""
    value = _read(_name(account_key))
    return value if value.get("at") else None  # 只剩记录（清过）= 没有标记


def _history(value) -> list:
    history = value.get("history")
    return list(history)[-(HISTORY_MAX - 1):] if isinstance(history, list) else []


def mark(account_key) -> dict:
    """记「被踢」：之后的自动推送快速失败，等人在明确的推送入口点了再登录。"""
    name, who, at = _name(account_key), _who(), _now()
    value = {"at": at, **who, "history": _history(_read(name)) + [{"event": "kicked", "at": at, **who}]}
    _store().set(name, json.dumps(value, ensure_ascii=False), ex=TTL_SECONDS)
    logger.warning("[mrerp-kick] account marked as signed in elsewhere (site %s, by %s)", who["site"], who["by"])
    return value


def clear(account_key) -> bool:
    """人在明确的推送入口点了：清掉标记（记录留着），这次照常登录。原来有标记回 True。"""
    name = _name(account_key)
    value = _read(name)
    if not value.get("at"):
        return False
    who, at = _who(), _now()
    kept = {"at": "", "history": _history(value) + [{"event": "cleared", "at": at, **who}]}
    _store().set(name, json.dumps(kept, ensure_ascii=False), ex=TTL_SECONDS)
    logger.info("[mrerp-kick] mark cleared by a manual push (site %s, by %s)", who["site"], who["by"])
    return True


__all__ = ["CODE", "TTL_SECONDS", "clear", "mark", "state"]
