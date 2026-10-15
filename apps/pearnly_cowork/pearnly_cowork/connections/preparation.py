"""Server collection of native business facts and per-account-set masters (preparation evidence v2, ADR-021).

No browser/heartbeat supplied completeness flag is consumed. Empty successful
reads differ from unavailable optional evidence. The caller owns the transaction.

Evidence v2 binds no computer and no key: ``route = {connection, account_key}``. A human
confirmation (confirm / activate / still-confirmable) still compares the whole evidence,
including the Bangkok date. A computer's lease only re-checks the route and ``basis_hash``:
the server facts (mappings, supplier profiles, bank window) plus the master rows the frozen
payload actually uses (`push.express.referenced`, handed in by the push ledger as ``used``).
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

VERSION = 2


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
    account = identity(target.account_set)
    if (target.endpoint_id != doc.name or target.user_id != loaded.access.user_id
            or target.tenant_id != frappe.local.site or target.binding_generation != doc.binding_generation):
        frappe.throw(_('connection.preparation_invalid'))
    # 目录刷新进行中 / 失败不拦推送（用户 2026-09-27 选 A）：检查用得上的资料就用，没有就留给电脑写入时核。
    # 主档取这份账套自己的账套资料（哪台电脑读的都一样，同一份物理账套）；不含版本号：载荷版本在领取时按领取的电脑判。
    reported = deepcopy(pool.masters(pool.facts(doc.name, account))) if doc.adapter == 'express' else {}
    maps, map_state = _read_optional(push_inputs.mappings, {'accounts': [], 'clients': []}, doc.client)
    profiles, profile_state = (_read_optional(push_inputs.profiles, {}, doc.workspace, seller_tax)
        if seller_tax else ({}, 'not_applicable'))
    # 银行流水窗口按录入人取（以前按电脑的签发人）；今天表里没有数据，不作为决策
    actor = loaded.access.user_id
    bank, bank_state = _read_optional(bank_evidence.window, [], actor, invoice_date)
    if not invoice_date:
        bank_state = 'not_applicable'
    facts = {'complete': True, 'account_set': target.account_set, 'root_key': target.root_key,
        'mappings': maps, 'supplier_profiles': profiles, 'bank_index': bank, 'reported_config': reported}
    try:
        facts = model.preparation(facts)
    except model.ConnectionError as error:
        frappe.throw(_(error.code))
    evidence = {'version': VERSION, 'query': {'seller_tax': seller_tax, 'invoice_date': invoice_date},
        'route': {'connection': doc.name, 'account_key': account},
        'states': {'mappings': map_state, 'supplier_profiles': profile_state,
            'bank_index': bank_state, 'reported_config': 'present' if reported else 'empty'},
        'facts_hash': model.fingerprint(facts),
        'date': datetime.now(ZoneInfo('Asia/Bangkok')).date().isoformat()}
    return facts, evidence


def basis(facts, used):
    """领取时要核的那一份：服务端事实 + 本单载荷实际用到的主档行（``used``，`push.express.referenced.used_masters`）。"""
    return model.fingerprint({'mappings': facts['mappings'], 'supplier_profiles': facts['supplier_profiles'],
                              'bank_index': facts['bank_index'], 'masters': used})


def revalidate(loaded, target, evidence, *, mode='confirm', used=None):
    """``confirm``：整份相等（除领取用的 ``basis_hash``），含日期——人确认的就是当天这份。
    ``lease``：这份账套的登记还在、``route`` 没变、``basis_hash`` 与按现在的事实重算的相同（``used(主档)`` 挑本单用到的行）。"""
    facts, current = collect(loaded, target, **evidence['query'])
    if mode == 'lease':
        route = current['route']
        if (evidence.get('route') != route or not pool.registration(route['connection'], route['account_key'])
                or used is None or basis(facts, used(facts['reported_config'])) != evidence.get('basis_hash')):
            frappe.throw(_('connection.preparation_incomplete'))
        return
    if current != {key: value for key, value in evidence.items() if key != 'basis_hash'}:
        frappe.throw(_('connection.preparation_incomplete'))
