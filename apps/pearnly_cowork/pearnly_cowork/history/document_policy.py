"""这个租户对转账凭条 / 预扣税凭证 / 还款收据选的「录入 / 不录入」（规则见 `contracts/document_policy.py`）。

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
    """复核页点「录入 / 不录入」：按这张票的类别记下，这个租户同类以后都这样处理。"""
    scope.require("intake")
    doc = storage.read(history)
    kind = rules.document_class(primary_page_fields(json.loads(doc.pages or "[]")))
    if not kind or value not in rules.VALUES:
        frappe.throw(_("cowork.policy.invalid"))
    policy = _parse(frappe.db.get_value(DOCTYPE, doc.workspace, FIELD, for_update=True))
    policy[kind] = value
    frappe.db.set_value(DOCTYPE, doc.workspace, FIELD, json.dumps(policy, sort_keys=True), update_modified=False)
    return {"document_class": kind, "value": value}
