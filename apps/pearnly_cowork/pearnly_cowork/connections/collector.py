"""Persist authenticated Express reports and lease local directory refresh work.

The machine ingress owns current credential validation and the transaction. This
module has no HTTP, authentication, commit or external IO. Directory observations
never attest the separate preparation facts needed to push an ERP document.
"""
from datetime import datetime, timezone

import frappe
from frappe import _
from pearnly_cowork.targets.projection import normalize_projection
from . import access, model, refresh, snapshots, state
from .reports import CACHE_KEYS, MAPPING_KEYS, SELECTED_KEYS


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
    report = {**model.parse(doc.get('report_json'), {}),
              **{key: value for key, value in plan.patch.items() if key in CACHE_KEYS}}
    if len(model.dumps(report).encode()) > 16 * 1024 * 1024:
        frappe.throw(_('connection.snapshot_too_large'))
    doc.report_json = model.dumps(report)
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
        published[(kind, key)] = snapshots.publish(doc, normalized, preserve_pending=True)['revision']
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
