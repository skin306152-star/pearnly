"""这个租户对转账凭条 / 预扣税凭证选的「录入 / 不录入」（规则见 `contracts/document_policy.py`）。

Cowork 一个租户一个工作空间、不按公司隔离（用户 2026-09-26），所以记在工作空间上，整个租户一条，复核页选一次。
同一个字段另记按公司选一次的商品单入账方式（键 ``goods_posting|<公司身份>``，`contracts.document_policy`）：公司身份要按推往的
账套目录现算，由推送域（`push.application.goods_posting.choose`）核过权限后经 :func:`record` 写；两份同期账套时这家公司现用的那份
（键 ``current_book|…``）由 `assistant.pending_api`（`choose_account_set` 推成了才记、`rechoose_current_book` 改记）按
companies 现算的候选核过后经 :func:`record` 写。
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
    record(doc.workspace, kind, value)
    return {"document_class": kind, "value": value}


def record(workspace, key, value):
    """记一项（同键覆盖）；认不出的键与值不记（`rules.normalize`）。调用方已核过权限。"""
    policy = _parse(frappe.db.get_value(DOCTYPE, workspace, FIELD, for_update=True))
    if rules.normalize({key: value}) != {key: value}:
        frappe.throw(_("cowork.policy.invalid"))
    policy[key] = value
    frappe.db.set_value(DOCTYPE, workspace, FIELD, json.dumps(policy, sort_keys=True), update_modified=False)
