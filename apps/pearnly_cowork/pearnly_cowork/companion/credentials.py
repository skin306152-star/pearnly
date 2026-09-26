"""Local credential lifecycle and internal current authentication.

No transport, auth hook, heartbeat, account selection, User creation, commit or
whole-transaction retry. Callers own transaction completion. Machine contexts
cannot be built from normal API/user input and do not change the session user.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import secrets
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.connections import access
from . import contracts
from .contracts import AgentContext, CredentialError
from .documents import DOCTYPE, persist

CONNECTION = 'Cowork ERP Connection'
_AUTHORITY = object()


def _error(code):
    frappe.throw(_(code))


def _unauthorized():
    frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)


def _value(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except CredentialError as error:
        _error(error.code)


def _site():
    if frappe.conf.get('pearnly_product') != 'cowork' or not frappe.local.site:
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    return frappe.local.site


def _enabled():
    if frappe.conf.get('pearnly_cowork_erp_push_enabled') not in (True, 1):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@contextmanager
def _atomic():
    point = 'cowork_credential_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        yield
    except frappe.QueryDeadlockError:
        # A deadlock may invalidate the entire transaction/savepoint. Only the
        # top-level local RPC may roll back and retry its complete unit.
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _read(doctype, name):
    try:
        return frappe.get_doc(doctype, name, for_update=True)
    except frappe.DoesNotExistError:
        _error('companion.credential_not_found')


def _connection(name):
    _site()
    name = _value(contracts.text, name)
    doc = _read(CONNECTION, name)
    if doc.adapter != 'express' or int(doc.binding_generation or 0) != 0:
        _error('companion.unsupported_connection')
    return doc


def _management(name, *, use=False):
    actor = access.require_actor(management=True)
    doc = _connection(name)
    access.require_connection(doc, actor=actor, management=True, enabled=use)
    if use:
        # Management does not imply an explicit grant to use this connection.
        access.require_connection(doc, actor=actor)
    return doc, actor


def _active(doc):
    rows = frappe.db.sql('''SELECT name FROM `tabCowork Express Agent Credential`
        WHERE connection=%s AND active_key=%s FOR UPDATE''',
        (doc.name, contracts.active_identity(_site(), doc.name)))
    return _read(DOCTYPE, rows[0][0]) if rows else None


def _latest(doc):
    rows = frappe.db.sql('''SELECT name FROM `tabCowork Express Agent Credential`
        WHERE connection=%s ORDER BY token_generation DESC, name DESC LIMIT 1 FOR UPDATE''', (doc.name,))
    return _read(DOCTYPE, rows[0][0]) if rows else None


def _row_scope(row, doc):
    if ((row.site_name, row.connection, row.workspace, row.client or '', row.branch, int(row.binding_generation or 0))
            != (_site(), doc.name, doc.workspace, doc.client or '', 'legacy', 0)):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)


# 旧线上 `erp-agent-liveness.ts`：3 分钟内有心跳算在线；助手主动下线时时间戳写成 1970。
ONLINE_SECONDS = 180


def _online(doc, row, now=None):
    """助手实际连上：**当前这把**有效密钥最近 3 分钟内报过到。只生成了密钥、旧密钥报的、
    时间戳缺失或读不出，一律算没连上（旧版同样“拿不准一律判离线”）。"""
    if row is None or row.revoked_at or not row.active_key:
        return False
    if (doc.get('report_credential') != row.name
            or int(doc.get('report_generation') or 0) != int(row.token_generation)):
        return False
    try:
        seen = datetime.fromisoformat(str(json.loads(doc.get('report_json') or '{}').get('agent_last_seen_at') or ''))
    except (TypeError, ValueError, AttributeError):
        return False
    if seen.tzinfo is None:
        return False
    age = ((now or datetime.now(timezone.utc)) - seen).total_seconds()
    return 0 <= age < ONLINE_SECONDS


def _dto(doc, row):
    if row is None:
        return {'connection': doc.name, 'credential_id': None, 'generation': 0,
                'state': 'missing', 'tail': '', 'issuer': None, 'issued_at': None,
                'revoked_at': None, 'connection_enabled': bool(doc.enabled), 'online': False}
    _row_scope(row, doc)
    return {'connection': doc.name, 'credential_id': row.name, 'generation': int(row.token_generation),
            'state': 'active' if row.active_key and not row.revoked_at else 'revoked',
            'tail': row.token_tail, 'issuer': row.issuer, 'issued_at': row.issued_at,
            'revoked_at': row.revoked_at, 'connection_enabled': bool(doc.enabled),
            'online': _online(doc, row)}


def _find_receipt(doc, operation, intent):
    # Operations are serialized by Connection, including aliases stored on an
    # earlier generation. Bound JSON paths are SHA256, never client SQL text.
    rows = frappe.db.sql('''SELECT name FROM `tabCowork Express Agent Credential`
        WHERE connection=%s AND JSON_EXTRACT(request_receipts,%s) IS NOT NULL FOR UPDATE''',
        (doc.name, '$."' + operation + '"'))
    if not rows:
        return None
    if len(rows) != 1:
        _error('companion.request_conflict')
    row = _read(DOCTYPE, rows[0][0])
    _row_scope(row, doc)
    receipt = json.loads(row.request_receipts)[operation]
    if receipt.get('intent_hash') != contracts.fingerprint(intent):
        _error('companion.request_conflict')
    return row


def _remember(row, operation, intent, result):
    encoded = _value(contracts.append_receipt, row.request_receipts, operation, intent, result)
    if encoded != row.request_receipts:
        row.request_receipts = encoded
        persist(row)


def _revoke(row, actor, reason):
    if not row.revoked_at:
        row.active_key = None
        row.revoked_at, row.revoked_by, row.revocation_reason = _now(), actor, reason
        persist(row)


def issue(connection, request_key, reset=False, expected_generation=None):
    _enabled()
    intent = _value(contracts.issue_intent, reset, expected_generation)
    with _atomic():
        doc, actor = _management(connection, use=True)
        operation = _value(contracts.operation_identity, _site(), actor, doc.name, request_key)
        old = _find_receipt(doc, operation, intent)
        if old:
            return {**_dto(doc, old), 'status': 'exists', 'replayed': True}
        current = _active(doc)
        if current:
            _row_scope(current, doc)
        if not reset and current:
            _remember(current, operation, intent, 'exists')
            return {**_dto(doc, current), 'status': 'exists', 'replayed': False}
        if reset and (not current or int(current.token_generation) != intent['expected_generation']):
            _error('companion.generation_conflict')
        last = _latest(doc)
        next_generation = _value(contracts.generation, int(last.token_generation) + 1 if last else 1)
        if current:
            _revoke(current, actor, 'reset')
        token = 'exp_' + doc.name + '_' + secrets.token_urlsafe(32)
        row = frappe.get_doc({'doctype': DOCTYPE, 'record_id': 'CWEAC-' + operation[:48],
            'site_name': _site(), 'connection': doc.name, 'workspace': doc.workspace,
            'client': doc.client or '', 'issuer': actor, 'token_generation': next_generation,
            'branch': 'legacy', 'binding_generation': 0,
            'token_digest': contracts.token_digest(token), 'token_tail': token[-4:],
            'active_key': contracts.active_identity(_site(), doc.name),
            'generation_key': contracts.generation_identity(_site(), doc.name, next_generation),
            'issued_request_digest': operation, 'issued_at': _now(),
            'request_receipts': _value(contracts.append_receipt, {}, operation, intent, 'created')})
        persist(row)
        return {**_dto(doc, row), 'status': 'created', 'replayed': False, 'token': token}


def revoke(connection, request_key, expected_generation, reason=''):
    intent = _value(contracts.revoke_intent, expected_generation, reason)
    with _atomic():
        # Revocation remains possible when the connection is disabled or the
        # manager has no use grant. Actual current management/scope is required.
        doc, actor = _management(connection)
        operation = _value(contracts.operation_identity, _site(), actor, doc.name, request_key)
        old = _find_receipt(doc, operation, intent)
        if old:
            return {**_dto(doc, old), 'status': 'revoked', 'replayed': True}
        row = _active(doc) or _latest(doc)
        if not row:
            _error('companion.credential_not_found')
        _row_scope(row, doc)
        if int(row.token_generation) != intent['expected_generation']:
            _error('companion.generation_conflict')
        _revoke(row, actor, intent['reason'])
        _remember(row, operation, intent, 'revoked')
        return {**_dto(doc, row), 'status': 'revoked', 'replayed': False}


def status(connection):
    doc, _ = _management(connection)
    return _dto(doc, _active(doc) or _latest(doc))


def for_confirmation(connection):
    """Internal human submission proof; no token disclosure or machine login."""
    _enabled()
    doc = _connection(connection)
    access.require_connection(doc)
    row = _active(doc)
    if row is None:
        _error('companion.credential_not_found')
    _issuer(doc, row)
    return {'credential_id': row.name, 'generation': int(row.token_generation), 'issuer': row.issuer}


def _issuer(doc, row):
    _row_scope(row, doc)
    if (row.revoked_at or row.active_key != contracts.active_identity(_site(), doc.name)
            or int(row.token_generation or 0) < 1):
        _unauthorized()
    # Removing either original authority or the explicit use grant invalidates
    # machine access immediately; no cached roles or connection list is used.
    access.require_connection(doc, actor=row.issuer, management=True)
    access.require_connection(doc, actor=row.issuer)


def _context(doc, row):
    return AgentContext(_site(), doc.name, doc.workspace, doc.client or '', row.issuer,
        row.name, int(row.token_generation), _capability=_AUTHORITY)


def authenticate(token):
    """Internal token validation only: no session mutation or machine endpoint."""
    _enabled()
    parsed = contracts.parse_token(token)
    if parsed is None:
        _unauthorized()
    _site()
    try:
        doc = frappe.get_doc(CONNECTION, parsed.connection, for_update=True)
        if doc.adapter != 'express' or int(doc.binding_generation or 0) != 0:
            _unauthorized()
        row = _active(doc)
        if row is None or not contracts.token_matches(row.token_digest, parsed.digest):
            _unauthorized()
        _issuer(doc, row)
        return _context(doc, row)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        _unauthorized()


def revalidate(context):
    """Reacquire Connection→Credential and all current issuer grants before IO."""
    _enabled()
    if (not isinstance(context, AgentContext) or context._capability is not _AUTHORITY
            or context.site != _site() or context.branch != 'legacy' or context.binding_generation != 0):
        _error('companion.context_invalid')
    try:
        doc = frappe.get_doc(CONNECTION, context.connection, for_update=True)
        if doc.adapter != 'express' or int(doc.binding_generation or 0) != 0:
            _unauthorized()
        row = frappe.get_doc(DOCTYPE, context.credential_id, for_update=True)
        if (row.issuer, int(row.token_generation), row.workspace, row.client or '') != (
                context.issuer, context.token_generation, context.workspace, context.client):
            _unauthorized()
        _issuer(doc, row)
        return _context(doc, row)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        _unauthorized()
