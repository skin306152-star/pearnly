"""Read-only, actor-scoped receipt facts from the native push ledger.

The roundtrip service supplies already authorized History documents. This reader
also checks current connection/log access, including revocation. No raw response,
target, credential or request payload leaves this adapter.
"""
import json
import re

import frappe
from frappe import _

from pearnly_cowork.connections import service as connections
from pearnly_cowork.push.export_actions import parse_actions


_REASON_CODES = (re.compile(r"^\[([A-Z0-9_]+)\]"), re.compile(r"^EXPRESS_MANUAL:\s*([a-z_]+)"),
                 re.compile(r"^([A-Za-z][A-Za-z0-9_]*)(?=$|:)"))


def reason_text(reason):
    """推送账本记的原因 → 给人看的话，与网页列表 `p.intake_push_reason` 同一套词条 `intake.error.<码>`：
    小助手回报 `[码] 原文`、服务端预检 `码` 或 `码:细节`；其余是存的词条原文（`push_state.source_text`），
    按当前语言再翻一次（结果表格按负责人的语言出）；翻不了的原样返回。"""
    text = str(reason or "").strip()
    for pattern in _REASON_CODES:
        match = pattern.match(text)
        if match:
            # 小助手回报的码是大写，词条有的是小写：两种都认（与网页 `p.intake_push_reason` 同一做法）。
            for key in dict.fromkeys(("intake.error." + match.group(1), "intake.error." + match.group(1).lower())):
                translated = _(key)
                if translated != key:
                    return translated
            return _(text)
    return _(text) if text else text


def for_histories(histories):
    if not histories:
        return {}
    docs = {doc.name: doc for doc in histories}
    # Select the newest successful receipt independently of History's latest
    # selected push state. An unauthorized latest success must not reveal an
    # older receipt instead. Actor scope matches the deployed export query.
    candidates = frappe.db.sql('''SELECT name, history, connection, workspace, client FROM (
        SELECT name, history, connection, workspace, client,
            ROW_NUMBER() OVER (PARTITION BY history ORDER BY created_at DESC, name DESC) AS position
        FROM `tabCowork Push Record` WHERE actor=%s AND history IN %s AND status='success'
        ) ranked WHERE position=1 ORDER BY connection, history''',
        (frappe.session.user, tuple(docs)), as_dict=True)
    successes = {row.name for row in candidates}
    selected = tuple(doc.last_push_record for doc in histories if doc.last_push_record)
    if selected:
        candidates += frappe.db.sql('''SELECT name, history, connection, workspace, client
            FROM `tabCowork Push Record` WHERE actor=%s AND history IN %s AND name IN %s''',
            (frappe.session.user, tuple(docs), selected), as_dict=True)
    candidates = sorted({row.name: row for row in candidates}.values(),
                        key=lambda row: (row.connection, row.history, row.name))
    result = {}
    for candidate in candidates:
        doc = docs[candidate.history]
        if (candidate.workspace, candidate.client or '') != (doc.workspace, doc.client or ''):
            continue
        try:
            connections.read_for_log(candidate.connection, doc.workspace, doc.client)
        except frappe.PermissionError:
            # History export remains available without push-log access; only
            # the additional ERP facts are omitted, never the invoice itself.
            continue
        row = frappe.get_doc('Cowork Push Record', candidate.name, for_update=True)
        if (row.actor != frappe.session.user
                or (row.history, row.connection, row.workspace, row.client or '') !=
                   (doc.name, candidate.connection, doc.workspace, doc.client or '')):
            continue
        if row.name == doc.last_push_record and doc.last_push_status:
            result.setdefault(doc.name, {})['state'] = {'status': doc.last_push_status, 'reason': reason_text(row.reason)}
        if row.name not in successes or row.status != 'success':
            continue
        target = json.loads(row.target_json)
        if target.get('adapter') != 'express':
            continue
        groups = connections.stock_groups_for_log(candidate.connection, target)
        actions = parse_actions(row.response_json, row.payload_json, groups)
        if row.history_version != doc.record_version:
            # The reference remains a fact, but edits can reorder/replace lines
            # or change direction/party. Do not attach old creation flags to it.
            actions.update(items=[], item_codes=[], customer=None, created_party=False, direction='')
        result.setdefault(doc.name, {})['actions'] = actions
    return result
