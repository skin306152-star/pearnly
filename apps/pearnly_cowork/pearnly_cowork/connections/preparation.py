"""Server collection of native business facts and attributed machine caches.

No browser/heartbeat supplied completeness flag is consumed. Empty successful
reads differ from unavailable optional evidence. The caller owns the transaction.
"""
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from pymysql.err import OperationalError, ProgrammingError

from pearnly_cowork.masters import push_inputs
from pearnly_cowork.reconciliation import bank_evidence
from pearnly_cowork.targets.identity import normalize_express_account_key as identity
from . import access, model, state


def attribute_report(doc, report, plan, credential_id, generation, issuer):
    """Only collector.ingest calls this after machine authentication.

    Attribute each cache separately: a heartbeat without a chart must never
    relabel the previous account's cached chart as belonging to the new account.
    """
    config = model.parse(doc.config_json, {})
    binding = {'account_set': identity(config.get('account_set') or config.get('account_dir')),
        'root_key': identity(config.get('express_root')), 'credential_id': credential_id,
        'generation': int(generation), 'issuer': issuer}
    # Account-specific cache data requires this report's explicit selection.
    selected = identity(plan.lease_selection)
    if selected and selected == binding['account_set'] and not plan.offline:
        report['_preparation_binding'] = {**binding, 'online': True}
    elif plan.offline:
        report['_preparation_binding'] = {**(report.get('_preparation_binding') or {}), 'online': False}
    origins = report.setdefault('_preparation_origins', {})
    for key in model.REPORTED_KEYS:
        if key in plan.patch:
            origins[key] = binding if selected and selected == binding['account_set'] else None
    return report


def _read_optional(function, default, *args):
    try:
        result = function(*args)
    except (OperationalError, ProgrammingError) as error:
        # Fixed source treats an absent optional table/column as no evidence.
        # Never swallow deadlocks, lost connections or failed transactions.
        if not error.args or error.args[0] not in (1054, 1146):
            raise
        return deepcopy(default), 'unavailable'
    present = any(result.values()) if isinstance(result, dict) else bool(result)
    return result, 'present' if present else 'empty'


def reported_writable(report, account, root):
    """小助手本次上报的账套目录里有这个账套（同一数据根）且没标不可写。"""
    for row in report.get('reported_account_sets') or []:
        if not isinstance(row, dict) or row.get('writable') is False:
            continue
        if identity(row.get('path') or row.get('code')) == account and (not root or identity(row.get('root')) == root):
            return True
    return False


def collect(loaded, target, *, seller_tax='', invoice_date=None):
    doc = state.read(state.CONNECTION, loaded.doc.name)
    access.require_connection(doc, actor=loaded.access.user_id)
    if int(doc.record_revision) != loaded.revision:
        frappe.throw(_('connection.revision_conflict'))
    account, root = identity(target.account_set), identity(target.root_key)
    if (target.endpoint_id != doc.name or target.user_id != loaded.access.user_id
            or target.tenant_id != frappe.local.site or target.binding_generation != doc.binding_generation):
        frappe.throw(_('connection.preparation_invalid'))
    heads = state.heads(doc)
    account_head = state.scope(account, 'express')[2]
    endpoint_head = state.scope(None)[2]
    head = heads.get(account_head, heads.get(endpoint_head))
    if head and head.get('status') != 'fresh':
        frappe.throw(_('connection.preparation_incomplete'))
    report = model.parse(doc.report_json, {})
    binding = report.get('_preparation_binding') or {}
    credential = None
    online = (binding.get('online') is True and binding.get('credential_id') == doc.report_credential
              and binding.get('generation') == doc.report_generation)
    default = online and binding.get('account_set') == account and binding.get('root_key') == root
    # 账套以网页第一步的选择为准，小助手不再选（用户 2026-09-26）：助手在线、且它上报的可写账套里有这一个，
    # 凭据同样成立；但小助手默认账套的缓存（科目表等）不挪给别的账套用。
    if default or (online and reported_writable(report, account, root)):
        credential = {k: binding[k] for k in ('credential_id', 'generation', 'issuer')}
    origins = report.get('_preparation_origins') or {}
    expected = {k: binding[k] for k in binding if k != 'online'}
    reported = {k: deepcopy(report[k]) for k in model.REPORTED_KEYS
                if default and credential and k in report and origins.get(k) == expected}
    maps, map_state = _read_optional(push_inputs.mappings, {'accounts': [], 'clients': []}, doc.client)
    profiles, profile_state = (_read_optional(push_inputs.profiles, {}, doc.workspace, seller_tax)
        if seller_tax else ({}, 'not_applicable'))
    owner = credential['issuer'] if credential else None
    bank, bank_state = _read_optional(bank_evidence.window, [], owner, invoice_date)
    if not owner or not invoice_date:
        bank_state = 'not_applicable'
    facts = {'complete': True, 'account_set': target.account_set, 'root_key': target.root_key,
        'mappings': maps, 'supplier_profiles': profiles, 'bank_index': bank, 'reported_config': reported}
    try:
        facts = model.preparation(facts)
    except model.ConnectionError as error:
        frappe.throw(_(error.code))
    evidence = {'version': 1, 'query': {'seller_tax': seller_tax, 'invoice_date': invoice_date},
        'credential': credential, 'states': {'mappings': map_state, 'supplier_profiles': profile_state,
            'bank_index': bank_state, 'reported_config': 'present' if reported else 'empty'},
        'facts_hash': model.fingerprint(facts),
        'date': datetime.now(ZoneInfo('Asia/Bangkok')).date().isoformat()}
    return facts, evidence


def revalidate(loaded, target, evidence):
    facts, current = collect(loaded, target, **evidence['query'])
    if current != evidence:
        frappe.throw(_('connection.preparation_incomplete'))
