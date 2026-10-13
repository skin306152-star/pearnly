"""Persist authenticated Express reports and lease local directory refresh work.

The machine ingress owns current credential validation and the transaction. This
module has no HTTP, authentication, commit or external IO. Directory observations
never attest the separate preparation facts needed to push an ERP document.
"""
from dataclasses import replace
from datetime import datetime, timezone

import frappe
from frappe import _
from pearnly_cowork.targets.identity import normalize_express_account_key
from pearnly_cowork.targets.projection import normalize_projection
from . import access, model, preparation, refresh, snapshots, state
from .reports import CACHE_KEYS, MAPPING_KEYS, SELECTED_KEYS


# 按单个账套刷新带回来的主档，属于那份账套（用户 2026-09-27：推到非默认账套也要先做库存检查）。
ACCOUNT_CACHE_KEYS = ('reported_accounts', 'accounts_seen_at', 'reported_products', 'reported_customers',
                      'reported_suppliers', 'reported_departments',
                      'catalog_seen_at', 'catalog_fingerprint', 'reported_stock_acc_groups', 'stock_acc_groups_seen_at')
# 每次心跳都会变、又没有读的人：不写进上报记录。小助手每分钟报到一次，留着它们会让内容没变的上报记录
# （生产每条几百 KB）每分钟整份重写一遍、全量进数据库日志。报到时间由心跳入口记在密钥那一小行上
# （`companion.credentials.record_seen`），在线判断读那里。
VOLATILE_KEYS = frozenset(('agent_last_seen_at', 'account_sets_seen_at', 'accounts_seen_at',
                           'catalog_seen_at', 'stock_acc_groups_seen_at'))


def _keep_account_caches(report, plan, credential_id, token_generation, now):
    """别的账套的主档不覆盖小助手默认账套的缓存，也不记成它的：取出来存进那份账套自己的目录快照（`snapshots.publish`）。

    以前存在上报记录的 `_account_caches[账套]` 里、只留最近 10 份：一台电脑做几十家客户的账时互相挤掉，上报记录也越来越大
    （整行重写进数据库日志）。现在每份账套各存一份，上报记录里那份旧缓存在下一次写入时去掉。
    回 ``(去掉这些键后的 plan, 这份账套的主档或 None)``。"""
    report.pop('_account_caches', None)
    account = plan.refreshed_account
    if not account or account == normalize_express_account_key(plan.lease_selection):
        return plan, None
    patch = dict(plan.patch)
    moved = {key: patch.pop(key) for key in ACCOUNT_CACHE_KEYS if key in patch}
    reported = None
    if moved:
        reported = {key: value for key, value in moved.items() if key not in VOLATILE_KEYS}
        reported.update({'_credential': credential_id, '_generation': int(token_generation)})
    return replace(plan, patch=patch), reported


def ingest(doc, plan, *, credential_id, token_generation, issuer, now=None):
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    if (doc.adapter != 'express' or not doc.enabled or int(doc.binding_generation or 0) != 0):
        frappe.throw(_('connection.refresh_stale'))
    # Validate authority before any report mutation. An obsolete or foreign
    # completion cannot publish a directory as a side effect of being rejected.
    completed = refresh.validate_completion(doc, plan.completion, credential_id, token_generation, now)
    config = model.parse(doc.config_json, {})
    updated = {**config, **{key: value for key, value in plan.patch.items() if key in SELECTED_KEYS | MAPPING_KEYS}}
    updated = model.configuration(doc.adapter, updated)
    changed = updated != config
    if changed:
        doc.config_json = model.dumps(updated)
        doc.record_revision += 1
        doc.projection_heads = '{}'
    report = model.parse(doc.get('report_json'), {})
    plan, account_masters = _keep_account_caches(report, plan, credential_id, token_generation, now)
    report.update({key: value for key, value in plan.patch.items() if key in CACHE_KEYS and key not in VOLATILE_KEYS})
    for key in VOLATILE_KEYS:
        report.pop(key, None)
    preparation.attribute_report(doc, report, plan, credential_id, token_generation, issuer)
    encoded = model.dumps(report)
    if len(encoded.encode()) > 16 * 1024 * 1024:
        frappe.throw(_('connection.snapshot_too_large'))
    # 内容、配置、上报凭据都没变就不写连接这一行（整行写入会把上报记录一起带进数据库日志）。
    # `report_received_at` 因此是「上报内容最近一次变化」的时间；报到时间看密钥行。
    if (changed or encoded != (doc.get('report_json') or '') or doc.report_credential != credential_id
            or int(doc.report_generation or 0) != int(token_generation)):
        doc.report_json = encoded
        doc.report_received_at = now
        doc.report_credential = credential_id
        doc.report_generation = token_generation
        access.write(doc)
    result = {'ok': True, 'endpoint_id': doc.name, **plan.response,
              'connection': {'endpoint_id': doc.name, 'endpoint_name': doc.label or 'Express',
                             'pearnly_account': issuer}}
    if plan.offline:
        return result
    # A config-changing report remains a valid observation, but cannot complete
    # a request authorized under the previous config revision.
    refresh.expire(doc, now, credential_id=credential_id, token_generation=token_generation)
    if changed:
        completed = None
    published = {}
    for observation in plan.observations:
        normalized = normalize_projection(observation)
        kind, key, _ = state.scope(None if normalized.scope_kind == 'endpoint' else normalized.scope_key, doc.adapter)
        # 按单个账套读回来的主档跟着这份账套的目录快照一起存（读失败、没带目录时没有这一份）。
        own = account_masters if kind == 'account_set' and key == plan.refreshed_account else None
        published[(kind, key)] = snapshots.publish(doc, normalized, preserve_pending=True, reported=own)['revision']
    if plan.projection_error:
        error = plan.projection_error
        kind, key, head_key = state.scope(None if error['scope'] == 'endpoint' else error['account_set'], doc.adapter)
        heads = state.heads(doc)
        heads[head_key] = {**heads.get(head_key, {}), 'status': 'error', 'error_code': str(error['error_code'])[:200]}
        doc.projection_heads = model.dumps(heads)
        access.write(doc)
    if completed is not None:
        revision = published.get((completed.scope_kind, completed.scope_key))
        refresh.finish(doc, completed, revision=revision, error=plan.completion.get('error_code'), now=now)
    leased = refresh.lease(doc, plan.lease_selection, credential_id, token_generation, now)
    if leased is not None:
        result['master_refresh'] = leased
    return result
