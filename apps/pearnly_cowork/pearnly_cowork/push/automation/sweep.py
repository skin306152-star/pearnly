"""网页上传 / LINE 的票自动推（数字会计员第 3 期第 4、5、10 条）：每 5 分钟的 cron 顺带扫一次（不新增云上常驻）。

- 只扫：选了 Express 去向、没人动过（``edit_count = 0``、没有推送状态 / 记录 / 原因）、识别完满 `SETTLE_MINUTES`（与「待处理」的
  `companies.pending.SETTLE_MINUTES` 同一口径，刚上传的还在路上）、近 `WINDOW_DAYS` 天、还没判过（``auto_checked_at`` 空）的票，
  每轮最多 `BATCH` 张。定时任务的票由定时任务自己判（钩子 ``cowork_push_task_histories``，push 不导入 folder_intake），不在这里。
- 以上传人身份判与推：推的那一刻照现有判据重核录入权限与连接授权（`application.prepare`），不过就不推、留给人。写的电脑是上传人
  登录过 CC 的电脑（ADR-021 第 50 条）；一台能写的都没有就不推、记 ``no_computer``。
- 只判一次、只推一次：判过就记 ``auto_checked_at``（先提交再推），推不出去、改成提议的都留给人在待处理里推，扫描不再碰。
  请求号由票与版本定，重放回原来那条。
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.history import push_state
from pearnly_cowork.push.application import service as application
from pearnly_cowork.push.automation import rules
from pearnly_cowork.push.native import service as ledger

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"
SETTLE_MINUTES = 15
WINDOW_DAYS = 3
BATCH = 200
SCAN = 1000
TASK_HOOK = "cowork_push_task_histories"
LINE_SOURCE = "cowork_line"
FIELDS = ["name", "owner", "source", "push_connection", "push_account_set", "record_version", "posting_kind",
          "edit_count", "creation"]


def push_key(history, version) -> str:
    return "auto:" + hashlib.sha256(f"{history}\0{version}".encode()).hexdigest()[:56]


def _now():
    return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)


def candidates(now=None) -> list:
    """这一轮要判的票（见文件头），旧 → 新，最多 `BATCH` 张。``now``：站点时区的时间，测试环境手动触发用。"""
    now = frappe.utils.get_datetime(now) if now else frappe.utils.now_datetime()
    rows = frappe.get_all(HISTORY, filters={
        "intake_target": "express", "push_connection": ["is", "set"], "push_account_set": ["is", "set"],
        "last_push_status": ["is", "not set"], "last_push_record": ["is", "not set"],
        "last_push_reason": ["is", "not set"], "auto_checked_at": ["is", "not set"],
        "creation": ["between", [now - timedelta(days=WINDOW_DAYS), now - timedelta(minutes=SETTLE_MINUTES)]]},
        fields=FIELDS, order_by="creation asc, name asc", limit_page_length=SCAN)
    rows = [row for row in rows if not int(row.edit_count or 0)]
    tasks = _task_histories([row.name for row in rows])
    return [row for row in rows if row.name not in tasks][:BATCH]


def _task_histories(names) -> set:
    found = set()
    for path in (frappe.get_hooks(TASK_HOOK) or []) if names else []:
        found |= set(frappe.get_attr(path)(names) or ())
    return found


@contextmanager
def _as(user):
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


def _mark(row, reason, *, cls="", tier="", proposal=None, at=None):
    push_state.record_assessment(row.name, cls=cls, tier=tier, reasons=reason, proposal=proposal,
                                 checked_at=at or _now())


def one(row) -> str:
    """判并（该推的）推一张票，回这一张的结局：pushed / propose / stop / no_permission / no_computer / failed / skipped。"""
    user = str(row.owner or "")
    if not user or user in ("Guest", "Administrator") or not scope.allowed("intake", user):
        _mark(row, "no_permission")
        frappe.db.commit()
        return "no_permission"
    trigger = rules.LINE if row.source == LINE_SOURCE else rules.UPLOAD
    with _as(user):
        frappe.db.commit()  # 判定只看不记，结束整笔回滚
        try:
            found = application.assess(row.name, row.push_connection, row.push_account_set, row.posting_kind or None,
                                       trigger=trigger)
        except (frappe.ValidationError, frappe.PermissionError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            outcome = "no_permission" if isinstance(error, frappe.PermissionError) else "failed"
            _mark(row, outcome)
            frappe.db.commit()
            return outcome
        if not found.get("assessed"):
            _mark(row, "not_express")
            frappe.db.commit()
            return "skipped"
        action, codes = found["action"], found["codes"]
        if action != rules.STOP and not ledger.has_writer(row.push_connection, found["account_set"], actor=user):
            action = rules.PROPOSE  # 上传人一台能写这份账套的电脑都没有：不推，留给人
            codes = ",".join(code for code in (codes, "no_computer") if code)
        _mark(row, codes, cls=found["class"], tier=found["tier"], at=found["checked_at"],
              proposal=found["proposal"] if action == rules.PROPOSE else None)
        frappe.db.commit()
        if action != rules.PUSH:
            return "no_computer" if "no_computer" in codes.split(",") else action
        return _push(row, found)


def _push(row, found) -> str:
    key = push_key(row.name, row.record_version)
    revision = frappe.db.get_value(CONNECTION, row.push_connection, "record_revision")
    result = application.submit(row.name, row.push_connection, key, row.record_version, revision,
                                found["account_set"], None, None, row.posting_kind or None,
                                automation=found["automation"])
    frappe.db.commit()
    if result.get("status") != "prepared":
        return "pushed" if result.get("status") in ("pending", "success") else "failed"
    try:
        application.confirm(result["name"], result["revision"], "confirm:" + key)
        frappe.db.commit()
    except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
        frappe.db.rollback()
        frappe.clear_messages()
        try:
            application.cancel_prepared(result["name"], result["revision"])
            frappe.db.commit()
        except Exception:  # noqa: BLE001 — 撤不掉就留着，确认时会按现有原因拒
            frappe.db.rollback()
            frappe.clear_messages()
        application.submission_failed(row.name, str(error))
        frappe.db.commit()
        return "failed"
    return "pushed"


def run(now=None) -> dict:
    """每 5 分钟（hooks ``scheduler_events`` 的 ``*/5``）：判并推这一轮的票，回各结局的张数。一张出意外只记日志、不挡别的。"""
    if frappe.conf.get("pearnly_product") != "cowork":
        return {}
    counts = {}
    for row in candidates(now):
        try:
            outcome = one(row)
        except Exception:  # noqa: BLE001 — 见上
            frappe.db.rollback()
            frappe.clear_messages()
            frappe.log_error(title="Cowork automatic push sweep failed", message=frappe.get_traceback()[-1500:])
            try:
                _mark(row, "failed")
                frappe.db.commit()
            except Exception:  # noqa: BLE001
                frappe.db.rollback()
            outcome = "failed"
        counts[outcome] = counts.get(outcome, 0) + 1
    return counts


__all__ = ["BATCH", "SETTLE_MINUTES", "WINDOW_DAYS", "candidates", "one", "push_key", "run"]
