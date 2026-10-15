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
from . import access, model, pool, state


def bind_report(report, plan, credential_id, generation, issuer, device):
    """Only collector.ingest calls this after machine authentication.

    The report binding names the key (and its computer) whose report is the connection's current one. It no
    longer carries an account set: the pool has no connection-level default (ADR-021) and every account set's
    masters live in its own Account Facts (`pool`), not in this report.
    """
    if plan.offline:
        report['_preparation_binding'] = {**(report.get('_preparation_binding') or {}), 'online': False}
    else:
        report['_preparation_binding'] = {'credential_id': credential_id, 'generation': int(generation),
                                          'issuer': issuer, 'device': device, 'online': True}
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


def current_binding(doc, report):
    """The connection's current reporter (key, generation, issuer, computer), while it is still the current one."""
    binding = report.get('_preparation_binding') or {}
    current = (binding.get('credential_id') == doc.report_credential
               and binding.get('generation') == doc.report_generation)
    return binding if current else {}


def reported_stock_groups(doc, account):
    """这份账套读到的存货科目组（账套资料，`pool.facts`）；没读过是 None，不当成「没有组」。与推送准备同一来源。"""
    groups = (pool.facts(doc.name, account) or {}).get('reported_stock_acc_groups')
    return deepcopy(groups) if isinstance(groups, list) else None


MASTER_LISTS = ('reported_accounts', 'reported_departments')


def reported_masters(doc, account):
    """这份账套读到的科目表与部门（账套资料）：文件夹自动录入的规则页选默认科目 / 部门用。

    没读过返回 ``None``，不当成「没有科目 / 部门」。只读。哪台电脑读的都一样：同一份物理账套（ADR-021）。"""
    found = pool.facts(doc.name, account) or {}
    result = {key: deepcopy(found[key]) for key in MASTER_LISTS if isinstance(found.get(key), list)}
    return result or None


def collect(loaded, target, *, seller_tax='', invoice_date=None):
    doc = state.read(state.CONNECTION, loaded.doc.name)
    access.require_connection(doc, actor=loaded.access.user_id)
    if int(doc.record_revision) != loaded.revision:
        frappe.throw(_('connection.revision_conflict'))
    account, root = identity(target.account_set), identity(target.root_key)
    if (target.endpoint_id != doc.name or target.user_id != loaded.access.user_id
            or target.tenant_id != frappe.local.site or target.binding_generation != doc.binding_generation):
        frappe.throw(_('connection.preparation_invalid'))
    # 目录刷新进行中 / 失败不拦推送（用户 2026-09-27 选 A）：检查用得上的资料就用，没有就留给小助手写入时核。
    report = model.parse(doc.report_json, {})
    binding = current_binding(doc, report)
    credential = None
    # 账套以网页第一步的选择为准（用户 2026-09-26）。证据 v1 仍冻结当前上报的那把密钥（第 3 批换成按账套分派）：
    # 它在线、它的电脑够得到这份账套（同一规范根）且可写，凭据才成立。池里没有连接级默认账套（ADR-021）。
    if binding.get('online') is True and pool.reaches(doc.name, binding.get('device'), account, root):
        credential = {k: binding[k] for k in ('credential_id', 'generation', 'issuer')}
    reported = {}
    if credential:
        # 主档取这份账套自己的资料（哪台电脑读的都行，同一份物理账套）；电脑能力（载荷版本、版本号）取上报的那台，
        # 预检据它拦旧版解析不了的载荷（第 3 批改到领取时按领取的电脑判）
        reported = deepcopy(pool.masters(pool.facts(doc.name, account)))
        reported.update({k: report[k] for k in ('max_payload_version', 'companion_version') if k in report})
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
