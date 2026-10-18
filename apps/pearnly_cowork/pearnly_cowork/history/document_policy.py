"""这个租户对转账凭条 / 预扣税凭证选的「录入 / 不录入」（规则见 `contracts/document_policy.py`）。

Cowork 一个租户一个工作空间、不按公司隔离（用户 2026-09-26），所以记在工作空间上，整个租户一条，复核页选一次。
只经这里写：调用方要有录入权限、能读这张票；读出的值不认得的一律当作没选过。
"""
import json

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import document_policy as rules
from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.history import storage

DOCTYPE = "Cowork Workspace"
FIELD = "document_policy"


def _parse(raw):
    try:
        return rules.normalize(json.loads(raw or "{}"))
    except (TypeError, ValueError):
        return {}


def read(workspace) -> dict:
    return _parse(frappe.db.get_value(DOCTYPE, workspace, FIELD)) if workspace else {}


def choose(history, value):
    """复核页点「录入 / 不录入」（CC 的 `cowork_choose_document_policy` 同一处）：按这张票的类别记下，这个租户同类以后都这样
    处理；正在等这个选择的单（推送停在 ``document_policy_needed_<类别>``）一并了结，见 :func:`_settle_waiting`。"""
    scope.require("intake")
    doc = storage.read(history)
    kind = rules.document_class(primary_page_fields(json.loads(doc.pages or "[]")))
    if not kind or value not in rules.VALUES:
        frappe.throw(_("cowork.policy.invalid"))
    policy = _parse(frappe.db.get_value(DOCTYPE, doc.workspace, FIELD, for_update=True))
    policy[kind] = value
    frappe.db.set_value(DOCTYPE, doc.workspace, FIELD, json.dumps(policy, sort_keys=True), update_modified=False)
    return {"document_class": kind, "value": value, "settled": _settle_waiting(doc.workspace, kind, value)}


def _settle_waiting(workspace, kind, value) -> int:
    """这个租户里停在「这类单据录不录」、还没推出去的单，按刚选的了结（第 7 批 B7，2026-10-18 lab：选了不录入，凭条还挂在
    待处理里问同一句，要靠 CC 记得再推一次才离开）。不录入 → 原因记成 ``document_policy_skip_<类别>``，不推、不再列为待处理；
    录入 → 原因清掉，按没推的单等人推（复核页、CC 选完都会接着推这一张；别的那几张不替人推，推送照旧由人发起）。回了结的张数。"""
    from pearnly_cowork.history import push_state

    waiting = frappe.get_all("Cowork OCR History", filters={
        "workspace": workspace, "last_push_reason": rules.NEEDED_PREFIX + kind,
        "last_push_status": ["not in", ["pending", "success"]]}, pluck="name", limit_page_length=0)
    reason = rules.SKIPPED_PREFIX + kind if value == rules.SKIP else ""
    for name in waiting:
        push_state.record_failure(name, reason)
    return len(waiting)
