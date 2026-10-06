"""识别完的文件夹票自动推送：以规则负责人的身份，做与网页「提交」完全相同的事，只调用现有入口、不改推送规则。

网页「提交」的顺序（`public/desk/intake-upload.js` 的 `submit`）：先看这张票在这份账套里还缺哪项要人选
（`choices_view`），缺就弹框问、关掉弹框就不推、不留推送记录；都有了再「准备」（`submit`）并「确认发送」
（Express 的确认入口，`companion/submission.py`）。这里照同一顺序：

- 缺的项里规则设了「这一类以后都这样处理」的，用现有的「记住选择」记下（与会计在复核页选一次完全一样，
  `push.choices.record`），这份账套以后同类的票都照它走；
- 仍缺的（典型是同税号有几个档）只在票上记原因（`push_state.record_failure`，网页列表照常显示「失败 + 选择」按钮），
  不建推送记录；
- 人已经动过的票（改过、推过）不碰，交给人；
- 确认发送失败就用现有的撤回入口把自己那条「已准备」撤掉，不把它留成卡住别人的记录。
"""
import hashlib
import json

import frappe

from pearnly_cowork.folder_intake import contracts, destinations
from pearnly_cowork.history import push_state
from pearnly_cowork.push import choices as choice_store
from pearnly_cowork.push.application import service as application

HISTORY = "Cowork OCR History"
CONNECTION = "Cowork ERP Connection"


def push_key(entry, history, version):
    return "folder:" + hashlib.sha256(f"{entry}\0{history}\0{version}".encode()).hexdigest()[:56]


def push_one(entry, rule, history):
    """推一张票，回这张票的去向：``sent`` / ``held`` / ``failed`` / ``human`` / ``missing``。调用方已切成负责人身份。"""
    row = frappe.db.get_value(HISTORY, history, ["edit_count", "last_push_status", "last_push_record",
                                                 "record_version"], as_dict=True)
    if not row:
        return {"status": "missing"}
    if int(row.edit_count or 0) or row.last_push_status or row.last_push_record:
        return {"status": "human"}
    frappe.db.commit()  # choices_view 只看不记，结束时整笔回滚：先把前面的落掉
    try:
        view = application.choices_view(history, rule.connection, rule.account_set_key, rule.posting_kind)
        defaults = json.loads(rule.defaults_json or "{}")
        applied = False
        for item in view.get("needed") or []:
            value = contracts.default_value(defaults, item.get("kind"))
            if value:
                applied = choice_store.record(rule.connection, view.get("account_set") or rule.account_set_key,
                                              item["kind"], item["subject"], value, item.get("label")) or applied
        if applied:
            frappe.db.commit()
            view = application.choices_view(history, rule.connection, rule.account_set_key, rule.posting_kind)
    except (frappe.ValidationError, frappe.PermissionError) as error:
        # 与网页「提交」失败同一做法：原因记在票上（列表照常显示「失败」+ 原因），这份文件照样往下走、出表格。
        reason = str(error)[:300]
        application.submission_failed(history, reason)
        frappe.db.commit()
        return {"status": "failed", "reason": reason}
    remaining = view.get("needed") or []
    if remaining:
        reason = "choice_needed:" + ",".join(dict.fromkeys(item["kind"] for item in remaining))
        push_state.record_failure(history, reason)
        frappe.db.commit()
        return {"status": "held", "reason": reason}
    revision = frappe.db.get_value(CONNECTION, rule.connection, "record_revision")
    key = push_key(entry.name, history, row.record_version)
    result = application.submit(history, rule.connection, key, row.record_version, revision,
                                rule.account_set_key, None, None, rule.posting_kind)
    frappe.db.commit()
    if result.get("status") == "failed":
        return {"status": "failed", "reason": str(result.get("reason") or "")[:300]}
    if result.get("status") == "prepared":
        adapter = frappe.db.get_value(CONNECTION, rule.connection, "adapter")
        try:
            destinations.of(adapter).confirm(rule.connection, result, "confirm:" + key)
            frappe.db.commit()
        except (frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError) as error:
            frappe.db.rollback()
            frappe.clear_messages()
            _withdraw(result)
            application.submission_failed(history, str(error))
            frappe.db.commit()
            return {"status": "failed", "reason": str(error)[:300]}
    return {"status": "sent", "record": result.get("name") or ""}


def _withdraw(prepared):
    try:
        application.cancel_prepared(prepared["name"], prepared["revision"])
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.clear_messages()
