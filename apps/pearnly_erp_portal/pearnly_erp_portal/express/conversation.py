"""CC 对话 / 代录任务里 ERP 的单写进哪本：本对话账套（A 线 11-12，D2：ERP 的 CC 与 Cowork 同口径）。

用户已给定的上下文是权威输入：对话的「本对话账套」（共用层 `pearnly_core.assistant.ledger`，ERP 的编号 = 账套键，provider 见
`masters.cc_projects.ledgers`）就是写入目标，不按票据日期另判、不问。写账准备（`prepare` / `stock_entry` / `pos_day`）、写账前置判定
（`precheck`）在 CC 对话里或代录任务里执行时经 :func:`wanted` 拿到这一本，交给 `target.pick(..., wanted)`。

- 不在 CC 对话里、也不在代录任务里执行（网页、LINE、每分钟重试、定时）：:func:`wanted` 回 None，照旧按票据日期选（ERP 网页 / LINE 现状）。
- 对话挂的不是这家公司、这家不用本对话账套（不接 Express）：None，照旧。
- 本对话账套未定（0 本 / 多本）：回空串，`target.pick` 停在 ``ledger_needed``（写入闸先拦，问一次、用 ``pearnly_conversation_ledger`` 设）。
- 代录：交单时 `push.send(operator=批次)` 用 :func:`for_task` 把任务号带进来（批次信息的 ``task``，共用层 `express_operator.api.start` /
  `tasks._resubmit`），按任务会话的账套写；代录对话里的判定照对话本身的。
"""
import contextlib
import contextvars

import frappe

SESSION = "Pearnly Assistant Session"
LEDGER_NEEDED = "ledger_needed"  # 同 Cowork `write_scope.LEDGER_NEEDED`

_TASK = contextvars.ContextVar("erp_conversation_task", default="")


@contextlib.contextmanager
def for_task(task):
    """这个范围里按代录任务 ``task`` 的会话取本对话账套（任务号空时不变）。"""
    token = _TASK.set(str(task or "") or _TASK.get())
    try:
        yield
    finally:
        _TASK.reset(token)


def session() -> str:
    """CC 此刻执行工具的那段对话（不在对话里回空串）。"""
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def _conversation_of(task):
    """(对话挂的公司, 本对话账套 `ledger.current` 的样子或 None)：代录任务按任务的会话，否则按此刻的对话；都不在回 ("", None)。"""
    from pearnly_core.assistant import ledger, projects

    if task:
        project = frappe.db.get_value(SESSION, {"operator_task": task}, "project") or ""
        return project, ledger.of_task(task) if project else None
    sid = session()
    if not sid:
        return "", None
    project = projects.of_session(sid)[0]
    return project, ledger.of_session(sid) if project else None


def pinned(company_row, task=None) -> dict | None:
    """这家公司此刻的本对话账套：``{"status": "set" | "needed", "key", "label", "options"}``；不在对话 / 代录里、对话挂的不是这家、
    这家不用本对话账套回 None（照旧按日期）。"""
    row = company_row or {}
    company = str((row.get("name") if hasattr(row, "get") else getattr(row, "name", "")) or "")
    if not company:
        return None
    project, found = _conversation_of(str(task or _TASK.get() or ""))
    if project != company or not found:
        return None
    key = str(found.get("id") or "") if found.get("status") == "set" else ""
    return {"status": "set" if key else "needed", "key": key, "label": str(found.get("label") or key),
            "options": list(found.get("options") or ())}


def wanted(company_row, task=None):
    """交给 `target.pick` 的 ``wanted``：None 照日期选；账套键；空串 = 本对话账套未定。"""
    pin = pinned(company_row, task)
    return None if pin is None else pin["key"]


__all__ = ["LEDGER_NEEDED", "for_task", "pinned", "session", "wanted"]
