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


# 能发配对码的连接：Express（读写账套、领推送任务、读文件夹）；MR.ERP 的小助手只读文件夹（定时任务，2026-10-09），
# 它的配对码只能调用文件夹的几个机器方法（`http_auth.FOLDER_METHODS`），心跳 / 领任务 / 回执一律拒绝。
HELPER_ADAPTERS = ('express', 'mrerp')
FOLDER_ONLY_ADAPTERS = ('mrerp',)


def _read(doctype, name):
    try:
        return frappe.get_doc(doctype, name, for_update=True)
    except frappe.DoesNotExistError:
        _error('companion.credential_not_found')


def _connection(name):
    _site()
    name = _value(contracts.text, name)
    doc = _read(CONNECTION, name)
    if doc.adapter not in HELPER_ADAPTERS or int(doc.binding_generation or 0) != 0:
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
    时间缺失或读不出，一律算没连上（旧版同样“拿不准一律判离线”）。

    报到时间记在密钥这一小行上（`record_seen`），不在连接的上报记录里：心跳每分钟一次，
    记在上报记录里会让整份（几百 KB）每分钟重写一遍、全量进数据库日志。"""
    if row is None or row.revoked_at or not row.active_key:
        return False
    if (doc.get('report_credential') != row.name
            or int(doc.get('report_generation') or 0) != int(row.token_generation)):
        return False
    seen = getattr(row, 'last_seen_at', None)
    if int(getattr(row, 'last_seen_generation', None) or 0) != int(row.token_generation):
        return False
    if isinstance(seen, str):
        try:
            seen = datetime.fromisoformat(seen)
        except ValueError:
            return False
    if not isinstance(seen, datetime):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)  # 库里存的是 UTC 无时区
    age = ((now or datetime.now(timezone.utc)) - seen).total_seconds()
    return 0 <= age < ONLINE_SECONDS


def record_seen(credential_id, generation, *, offline=False, now=None):
    """心跳报到：只改这把密钥的两列（调用方已持有连接 → 密钥锁，并负责提交）。小助手说自己下线就清空。"""
    frappe.db.set_value(DOCTYPE, credential_id, {
        'last_seen_at': None if offline else (now or _now()),
        'last_seen_generation': int(generation)}, update_modified=False)


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


def _device_connection(express):
    """这台电脑该连哪条连接：装了 Express 的连 Express；没装的连 MR.ERP（只读文件夹给定时任务），
    没有 MR.ERP 再退回 Express。只看本站那一份归属下启用的连接，网页上每种只用一条。"""
    rows = frappe.db.sql('''SELECT name, adapter FROM `tabCowork ERP Connection`
        WHERE workspace=%s AND COALESCE(client,'')='' AND enabled=1 AND binding_generation=0
        AND adapter IN %s ORDER BY creation, name''', (access.default_workspace(), HELPER_ADAPTERS), as_dict=True)
    order = ('express', 'mrerp') if express else ('mrerp', 'express')
    for adapter in order:
        found = next((row.name for row in rows if row.adapter == adapter), None)
        if found:
            return found
    return None


def register(request_key, express=False, replace=False, expected_generation=None):
    """CC 桌面端用登录的账号连上这台电脑（2026-10-10 起取代手抄配对码，旧配对码照常有效）。

    权限与网页「连接 Express」同一判据：管理员、且在这条连接的可用名单里；密钥照旧以本人为签发人，
    本人失去权限密钥即失效。这条连接已有电脑连着时不抢：回 `in_use`，用户确认「改用这台电脑」后
    带上那把密钥的代数再来（`replace`），旧电脑立即断开。
    """
    _enabled()
    actor = access.require_actor(management=True)
    express, replace = (contracts.wire_flag(v) in (True, 1) for v in (express, replace))
    # 同一归属下串行：两台电脑同时登录不会各建一条 Express 连接
    frappe.db.get_value('Cowork Workspace', access.default_workspace(), 'name', for_update=True)
    name = _device_connection(express)
    if name is None:
        if not express:
            return {'status': 'no_connection'}
        from pearnly_cowork.connections import model, service
        try:
            name = service.configure(None, 'express', request_key, 'Express', {})['name']
        except model.ConnectionError as error:
            _error(error.code)
    result = issue(name, request_key, replace, expected_generation if replace else None)
    adapter = frappe.db.get_value(CONNECTION, name, 'adapter')
    if result.get('token'):
        return {'status': 'created', 'connection': name, 'adapter': adapter, 'token': result['token']}
    return {'status': 'in_use', 'connection': name, 'adapter': adapter, 'generation': result['generation'],
            'online': result['online'], 'issuer': result['issuer'], 'self': result['issuer'] == actor}


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


def online(connection):
    """录入入口（LINE 里选目标）看 Express 能不能选：本人能用这条连接、助手当前在线。不要求管理权限。"""
    doc = _connection(connection)
    access.require_connection(doc)
    return _online(doc, _active(doc))


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
        if doc.adapter not in HELPER_ADAPTERS or int(doc.binding_generation or 0) != 0:
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
        if doc.adapter not in HELPER_ADAPTERS or int(doc.binding_generation or 0) != 0:
            _unauthorized()
        row = frappe.get_doc(DOCTYPE, context.credential_id, for_update=True)
        if (row.issuer, int(row.token_generation), row.workspace, row.client or '') != (
                context.issuer, context.token_generation, context.workspace, context.client):
            _unauthorized()
        _issuer(doc, row)
        return _context(doc, row)
    except (frappe.DoesNotExistError, frappe.PermissionError):
        _unauthorized()
