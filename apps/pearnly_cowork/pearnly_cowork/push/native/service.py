"""One native Site ledger, atomic reservation, fenced attempts and durable locks.

No methods are whitelisted and no scheduler/worker consumer is registered here.
The internal Companion adapter authenticates before constructing the trusted
adapter context; this object capability is not itself machine authentication.
All helpers participate in the caller's transaction; none commits.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.connections import service as connections
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.contracts.push import PushContractError, require_external_history
from pearnly_cowork.push.express import common
from pearnly_cowork.push.lifecycle import acknowledgement, LEASE_SECONDS
from pearnly_cowork.push.native.contracts import (
    ACTIVE_STATUSES, PushLedgerError, ReservationContext, business_identity, digest, document_identity,
    duplicate_reason, encode, frozen_values, integer, request_identity, text, utc,
    writer_identities,
)
from pearnly_cowork.push.native.documents import TYPES, persist
from pearnly_cowork.targets.freeze import freeze_target
from pearnly_cowork.targets.contracts import TargetSelectionError
from pearnly_cowork.targets.identity import normalize_express_account_key

RECORD, ATTEMPT, WRITER_LOCK = TYPES
_ADAPTER_CAPABILITY = object()


class DispatchRejected(frappe.ValidationError):
    """A definite frozen-history/target rejection, safe for queue isolation."""

    def __init__(self, code):
        self.code = code
        super().__init__(_(code))


class LeaseUnavailable(frappe.ValidationError):
    """The current record is no longer a claimable pending queue candidate."""

    def __init__(self):
        self.code = 'push.native_state_invalid'
        super().__init__(_(self.code))


def fail(code):
    frappe.throw(_(code))


def _dispatch_rejected(code):
    raise DispatchRejected(code)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except (PushLedgerError, PushContractError) as error:
        fail(error.code)


def now_utc(value=None):
    return call(utc, value if value is not None else datetime.now(timezone.utc))


@contextmanager
def atomic():
    point = 'cowork_push_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        yield
    except frappe.QueryDeadlockError:
        frappe.db.rollback()
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _site():
    if frappe.conf.get('pearnly_product') != 'cowork':
        frappe.throw(_('Not Permitted'), frappe.PermissionError)


def _new(doctype, name, **values):
    return frappe.get_doc({'doctype': doctype, 'record_id': name, **values})


def _insert_unique(doc):
    point = 'cowork_push_insert_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        persist(doc)
        return True
    except frappe.DuplicateEntryError:
        frappe.db.rollback(save_point=point)
        return False


def _current(doctype, name):
    try:
        return frappe.get_doc(doctype, name, for_update=True)
    except frappe.DoesNotExistError:
        fail('push.native_record_not_found')


def _context(value, *, account=False, session=True):
    _site()
    if not isinstance(value, ReservationContext):
        fail('push.native_request_invalid')
    if session and frappe.session.user != value.actor:
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    if account:
        call(text, value.account_key, length=1024)
    return value


def _authorize(context):
    # Same lock and real explicit grants as the application entry, never a
    # caller-provided `authorized=True` boolean or Administrator bypass.
    return connections.read_for_push(context.connection, context.workspace,
        context.client, actor=context.actor, lock=True)


def _history(context, name=None):
    doc = _current('Cowork OCR History', name or context.history)
    if (doc.workspace, doc.client or '') != (context.workspace, context.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    if not scope.contains(doc.workspace, doc.client, user=context.actor):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    call(require_external_history, {'source': doc.source})
    return doc


def _record(name, context):
    row = _current(RECORD, call(text, name))
    if (row.workspace, row.client or '', row.history, row.connection) != (
            context.workspace, context.client or '', context.history, context.connection):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    if context.account_key and row.account_key != context.account:
        fail('push.native_target_mismatch')
    return row


def _save_record(row, timestamp):
    row.revision = int(row.revision or 0) + 1
    row.updated_at = timestamp
    row.active_key = row.document_key if row.status in ACTIVE_STATUSES or row.result_unknown else None
    persist(row)


def project(row):
    """Internal complete projection. Product API must explicitly remove secrets."""
    result = {key: row.get(key) for key in ('name', 'status', 'revision', 'workspace', 'client',
        'actor', 'history', 'connection', 'account_key', 'history_version', 'connection_revision',
        'branch', 'binding_generation', 'reason', 'fence', 'failure_count', 'wire_attempt',
        'external_docnum', 'invoice_no', 'seller_name', 'blocked_by', 'created_at', 'updated_at', 'completed_at')}
    result['result_unknown'] = bool(row.result_unknown)
    for output, column, default in [('frozen_target','target_json',{}), ('frozen_payload','payload_json',{}),
                                    ('response_body','response_json',None), ('checks','checks_json',[])]:
        result[output] = json.loads(row.get(column)) if row.get(column) else default
    return result


def lookup_request(context, request_key):
    _context(context)
    _authorize(context)
    _history(context)
    key = call(request_identity, context, request_key)
    rows = frappe.db.sql('SELECT name FROM `tabCowork Push Record` WHERE request_digest=%s FOR UPDATE', (key,))
    if not rows:
        return None
    row = _record(rows[0][0], context)
    if row.actor != context.actor:
        fail('push.request_conflict')
    return {**project(row), 'replayed': True}


def _locks(context, target):
    result = []
    for name, kind in call(writer_identities, context, target):
        # Account identity spans clients/connections in one company. Path/root
        # equality is conservative only; no global physical-device proof exists.
        values = {'lock_key': name, 'lock_kind': kind, 'workspace': context.workspace,
                  'client': context.client or '' if kind == 'endpoint' else '',
                  'connection': context.connection if kind == 'endpoint' else '',
                  'account_key': context.account if kind == 'account' else ''}
        if not frappe.db.sql('SELECT name FROM `tabCowork Push Writer Lock` WHERE name=%s FOR UPDATE', (name,)):
            _insert_unique(_new(WRITER_LOCK, name, **values))
        result.append(_current(WRITER_LOCK, name))
    return result


def _validate_target(context, loaded, frozen, *, reject=fail):
    if (context.connection_revision != loaded.revision
            or context.binding_generation != int(loaded.endpoint.get('binding_generation') or 0)):
        reject('push.native_target_changed')
    target = freeze_target(loaded.endpoint, access=loaded.access, evidence=loaded.evidence,
        account_set_key=context.account_key, request_id=frozen.get('catalog_refresh_request_id'),
        revision=frozen.get('catalog_projection_revision'))
    # Additional application fields (intent, prior provenance, preparation date)
    # remain frozen, while every actual target field must match server evidence.
    if any(frozen.get(key) != value for key, value in asdict(target).items()):
        reject('push.native_target_mismatch')


def reserve(*, context, request_key, frozen_target, frozen_payload,
            disposition='prepared', reason='', checks=None):
    _context(context, account=True)
    values = call(frozen_values, context, frozen_target, frozen_payload, disposition, reason, checks or [])
    target = json.loads(values['target_json'])
    request_key = call(request_identity, context, request_key)
    document_key = call(document_identity, context)
    with atomic():
        loaded = _authorize(context)  # connection -> history -> sorted writer locks -> record
        history = _history(context)
        locks = _locks(context, target)
        existing = frappe.db.sql('SELECT name FROM `tabCowork Push Record` WHERE request_digest=%s FOR UPDATE', (request_key,))
        if existing:
            row = _record(existing[0][0], context)
            if row.request_fingerprint != values['request_fingerprint']:
                fail('push.request_conflict')
            return {**project(row), 'replayed': True}
        if int(history.record_version) != context.history_version:
            fail('push.native_history_changed')
        _validate_target(context, loaded, target)
        business_key = call(business_identity, context, history.invoice_no, history.seller_name)
        rows = frappe.db.sql('''SELECT name,status,result_unknown FROM `tabCowork Push Record`
            WHERE document_key=%s ORDER BY creation DESC,name DESC FOR UPDATE''', (document_key,), as_dict=True)
        for priority in ('already_success', 'active', 'unknown'):
            if priority == 'active':
                previous_business = _business_success(business_key)
                if previous_business:
                    return {**project(previous_business), 'replayed': False,
                            'blocked_by': previous_business.name, 'blocked_reason': 'already_success'}
            for candidate in rows:
                if duplicate_reason(candidate) == priority:
                    previous = _current(RECORD, candidate.name)
                    if priority == 'active' and _supersede_pristine(previous, context, loaded):
                        continue
                    return {**project(previous), 'replayed': False,
                            'blocked_by': candidate.name, 'blocked_reason': priority}
        timestamp = now_utc()
        row = _new(RECORD, 'CWP-' + request_key[:48], request_digest=request_key,
            document_key=document_key, business_key=business_key,
            invoice_no=history.invoice_no or '', seller_name=history.seller_name or '',
            active_key=document_key if disposition == 'prepared' else None,
            workspace=context.workspace, client=context.client or '', actor=context.actor,
            history=context.history, history_version=context.history_version,
            connection=context.connection, connection_revision=context.connection_revision,
            account_key=context.account, branch=context.branch, binding_generation=context.binding_generation,
            status=disposition, revision=1, fence=0, wire_attempt=1, failure_count=0,
            created_at=timestamp, updated_at=timestamp, **values)
        if not _insert_unique(row):
            # A DB unique constraint is the final guard, even if a future caller
            # accidentally omits the ordered admission lock.
            fail('push.native_reservation_conflict')
        return {**project(row), 'replayed': False}


def _supersede_pristine(row, context, loaded):
    # Current target authorization has already passed under the connection lock.
    # A changed allow-list increments connection revision; this lets its newly
    # authorized owner replace a revoked actor's never-dispatched preparation.
    if row.status != 'prepared' or row.result_unknown or row.fence or row.active_attempt:
        return False
    attempts = frappe.db.sql('SELECT name FROM `tabCowork Push Attempt` WHERE push_record=%s LIMIT 1 FOR UPDATE',
                             (row.name,))
    if attempts:
        return False
    stale = (int(row.history_version) != context.history_version
             or int(row.connection_revision) != context.connection_revision)
    if not stale:
        try:
            connection_access.require_connection(loaded.doc, actor=row.actor)
        except frappe.PermissionError:
            stale = True  # Only a definite current revocation, never a DB failure.
    if not stale:
        return False
    row.status, row.reason = 'cancelled', 'push.preparation_superseded'
    row.completed_at = now_utc()
    _save_record(row, row.completed_at)  # modified_by records the current actor.
    return True


def _business_success(key, exclude=None):
    if not key:
        return None
    rows = frappe.db.sql("""SELECT name FROM `tabCowork Push Record`
        WHERE business_key=%s AND status='success' AND name<>%s
        ORDER BY creation DESC,name DESC LIMIT 1 FOR UPDATE""", (key, exclude or ''))
    return _current(RECORD, rows[0][0]) if rows else None


def read_success(*, context, history=None):
    _context(context, account=True)
    _authorize(context)
    target_history = history or context.history
    _history(context, target_history)
    candidate = ReservationContext(**{**asdict(context), 'history': target_history})
    rows = frappe.db.sql('''SELECT name FROM `tabCowork Push Record`
        WHERE document_key=%s AND status='success' ORDER BY creation DESC,name DESC LIMIT 1 FOR UPDATE''',
        (call(document_identity, candidate),))
    return project(_current(RECORD, rows[0][0])) if rows else None


@dataclass(frozen=True)
class AdapterContext:
    connection: str
    owner: str
    binding_generation: int
    _capability: object = field(default=None, repr=False, compare=False)


def trusted_adapter_context(connection, owner, binding_generation=0):
    """Internal injection seam, not identity verification or a public credential API.

    The internal Companion transport must authenticate its credential before
    calling; native tests can inject a synthetic adapter through the same seam.
    """
    return AdapterContext(call(text, connection), call(text, owner),
                          call(integer, binding_generation), _ADAPTER_CAPABILITY)


def _adapter(context, adapter):
    _context(context, account=True, session=False)
    if (not isinstance(adapter, AdapterContext) or adapter._capability is not _ADAPTER_CAPABILITY
            or adapter.connection != context.connection
            or adapter.binding_generation != context.binding_generation):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)


def _dispatch_context(context, adapter):
    _adapter(context, adapter)
    if frappe.conf.get('pearnly_cowork_erp_push_enabled') not in (True, 1):
        fail('ERR_EXPRESS_DISABLED')
    loaded = _authorize(context)
    history = _history(context)
    if int(history.record_version) != context.history_version:
        raise DispatchRejected('push.native_history_changed')
    if (loaded.revision != context.connection_revision
            or int(loaded.endpoint.get('binding_generation') or 0) != context.binding_generation):
        raise DispatchRejected('push.native_target_changed')
    return loaded


def _dispatch_record(row, context):
    # Current authorization cannot replace the identity/version accepted when
    # this immutable payload was prepared. Receipt ingestion deliberately does
    # not call this guard, so a valid older attempt can still report its result.
    if (row.actor, int(row.history_version), int(row.connection_revision),
            row.branch, int(row.binding_generation)) != (
            context.actor, context.history_version, context.connection_revision,
            context.branch, context.binding_generation):
        raise DispatchRejected('push.native_target_changed')


def _dispatch_target(context, loaded, target):
    # Snapshot refresh does not have to change the connection revision. Keep
    # the original proof identity and reject an expired/superseded proof; never
    # silently choose a new target or regenerate the frozen payload.
    try:
        _validate_target(context, loaded, target, reject=_dispatch_rejected)
    except TargetSelectionError:
        raise DispatchRejected('push.native_target_changed') from None
    snapshot = loaded.evidence.snapshot or {}
    accounts = snapshot.get('account_sets') or []
    if isinstance(accounts, str):
        accounts = json.loads(accounts)
    for account in accounts:
        if normalize_express_account_key(account.get('source_id')) != context.account:
            continue
        attributes = account.get('attributes') or {}
        if account.get('active', True) is False or attributes.get('writable', True) is False:
            raise DispatchRejected('push.native_target_changed')


def activate(name, *, context, adapter):
    """Internal prepared -> pending transition, without external dispatch."""
    with atomic():
        loaded = _dispatch_context(context, adapter)
        # Target is immutable. Read after connection/history lock before taking
        # its lock identities; final record lock is obtained after those locks.
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        target = json.loads(raw)
        _dispatch_target(context, loaded, target)
        locks = _locks(context, target)
        row = _record(name, context)
        _dispatch_record(row, context)
        if row.status == 'pending':
            return {**project(row), 'replayed': True}
        if row.status != 'prepared' or row.result_unknown:
            fail('push.native_state_invalid')
        row.status, row.reason = 'pending', ''
        _save_record(row, now_utc())
        return project(row)


def lease(name, *, context, adapter, now=None):
    """Claim one known pending record. Never scans or executes an ERP queue."""
    with atomic():
        loaded = _dispatch_context(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        target = json.loads(raw)
        _dispatch_target(context, loaded, target)
        locks = _locks(context, target)
        row = _record(name, context)
        _dispatch_record(row, context)
        if row.status != 'pending' or row.result_unknown:
            raise LeaseUnavailable()
        duplicate = _business_success(row.business_key, row.name)
        if duplicate:
            row.status, row.reason, row.blocked_by = 'manual', 'already_success', duplicate.name
            _save_record(row, now_utc(now))
            return {'leased': False, 'reason': 'already_success', 'record': project(row)}
        if any(lock.holder for lock in locks):
            return {'leased': False, 'reason': 'waiting_lock', 'record': project(row)}
        timestamp = now_utc(now)
        token = secrets.token_urlsafe(32)
        fence = int(row.fence or 0) + 1
        attempt_name = 'CWPA-' + digest([row.name, fence])[:48]
        attempt = _new(ATTEMPT, attempt_name, push_record=row.name, workspace=row.workspace,
            client=row.client or '', connection=row.connection, fence=fence, owner_id=adapter.owner,
            token_digest=hashlib.sha256(token.encode()).hexdigest(), binding_generation=row.binding_generation,
            started_at=timestamp, lease_expires_at=timestamp + timedelta(seconds=LEASE_SECONDS), status='leased')
        persist(attempt)
        for lock in locks:
            lock.holder, lock.holder_fence, lock.lease_expires_at = row.name, fence, attempt.lease_expires_at
            persist(lock)
        row.status, row.fence, row.active_attempt = 'leased', fence, attempt_name
        row.lease_owner, row.lease_expires_at = adapter.owner, attempt.lease_expires_at
        _save_record(row, timestamp)
        return {'leased': True, 'record': project(row), 'fence': fence, 'attempt_token': token,
                'payload': deepcopy(json.loads(row.payload_json)), 'target': deepcopy(json.loads(row.target_json))}


def _receipt_scope(context, adapter):
    _adapter(context, adapter)
    # Receiving an already-authorized attempt's result must remain possible
    # after its human actor loses access. It grants no new write or dispatch.
    connection = _current('Cowork ERP Connection', context.connection)
    if (connection.workspace, connection.client or '') != (context.workspace, context.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    history = _current('Cowork OCR History', context.history)
    if (history.workspace, history.client or '') != (context.workspace, context.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)


def quarantine_pending(name, *, context, adapter, reason):
    """Isolate a definitely rejected queue head without releasing writer locks.

    The internal adapter must first establish a definite authorization or frozen
    target rejection. This path grants no dispatch authority and preserves every
    previous attempt and receipt, including a finished failed attempt's fence.
    """
    reasons = {'push.native_history_changed', 'push.native_target_changed',
               'push.native_target_mismatch', 'companion.queue_access_revoked'}
    if not isinstance(reason, str) or reason not in reasons:
        fail('push.native_request_invalid')
    with atomic():
        _receipt_scope(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        locks = _locks(context, json.loads(raw))
        row = _record(name, context)
        if row.status != 'pending' or row.result_unknown:
            fail('push.native_state_invalid')
        # Another record may own a shared endpoint/account writer. Its lock is
        # irrelevant to this non-dispatching transition and must remain intact.
        if any(lock.holder == row.name for lock in locks):
            fail('push.native_lock_conflict')
        if row.active_attempt:
            attempt = _current(ATTEMPT, row.active_attempt)
            if (attempt.push_record != row.name or int(attempt.fence) != int(row.fence)
                    or attempt.status != 'finished'):
                fail('push.native_stale_attempt')
        elif row.fence or frappe.db.sql(
                'SELECT name FROM `tabCowork Push Attempt` WHERE push_record=%s LIMIT 1 FOR UPDATE',
                (row.name,)):
            # A missing pointer must not erase evidence that dispatch occurred.
            fail('push.native_stale_attempt')
        row.status, row.reason = 'manual', reason
        _save_record(row, now_utc())
        return project(row)


def _attempt(row, adapter, token, fence):
    call(integer, fence, minimum=1)
    call(text, token, length=256)
    if int(row.fence or 0) != fence or row.lease_owner != adapter.owner or not row.active_attempt:
        fail('push.native_stale_attempt')
    attempt = _current(ATTEMPT, row.active_attempt)
    if (attempt.push_record != row.name or int(attempt.fence) != fence or attempt.owner_id != adapter.owner
            or attempt.binding_generation != adapter.binding_generation
            or not hmac.compare_digest(attempt.token_digest, hashlib.sha256(token.encode()).hexdigest())):
        fail('push.native_stale_attempt')
    return attempt


def _release(row, locks):
    for lock in locks:
        if lock.holder != row.name or int(lock.holder_fence or 0) != int(row.fence):
            fail('push.native_lock_conflict')
        lock.holder, lock.holder_fence, lock.lease_expires_at = None, 0, None
        persist(lock)


def _response(branch, stage, docnum, line_modes, meta):
    body = {'ok': stage == common.STAGE_SUCCESS, 'express_docnum': docnum if stage == common.STAGE_SUCCESS else None}
    # Both source ACK callers only pass line modes on success; the response
    # builder's ability to accept them on other stages was not a live contract.
    modes = deepcopy(line_modes) if stage == common.STAGE_SUCCESS else None
    if modes:
        body['line_modes'] = modes
        fallback = [m for m in modes if m.get('mode') == common.ITEM_MODE_DIRECT and m.get('reason')]
        if branch == 'legacy' and fallback:
            body['fallback_count'] = len(fallback)
    clean = common.sanitize_push_meta(meta)
    # The stage in the trusted ACK is authoritative, not a stale metadata field.
    clean['stage'] = stage
    if branch == 'legacy' and modes and 'created_masters' not in clean:
        clean['created_masters'] = [m['stkcod'] for m in modes if m.get('stkcod')]
    body['meta'] = clean
    return body


def ack(name, *, context, adapter, fence, attempt_token, success, outcome=None,
        express_docnum=None, error=None, line_modes=None, meta=None, now=None):
    if type(success) is not bool or (line_modes is not None and (
            not isinstance(line_modes, list) or any(not isinstance(x, dict) for x in line_modes))):
        fail('push.native_request_invalid')
    with atomic():
        _receipt_scope(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        locks = _locks(context, json.loads(raw))
        row = _record(name, context)
        attempt = _attempt(row, adapter, attempt_token, fence)
        if row.status == 'success':
            return {**project(row), 'replayed': True}  # never replace a success receipt
        transition = acknowledgement(branch=row.branch, attempt=int(row.wire_attempt),
            success=success, outcome=outcome, error=error)
        docnum = call(text, express_docnum, length=140, optional=True) or None
        response = _response(row.branch, transition.stage, docnum, line_modes, meta)
        response_json = call(encode, response)
        ack_digest = call(digest, [transition.stage, success, docnum, error, line_modes, meta])
        if attempt.status == 'finished':
            if attempt.ack_digest != ack_digest:
                fail('push.native_ack_conflict')
            return {**project(row), 'replayed': True}
        if row.status not in {'leased', 'manual'} or (row.status == 'manual' and not row.result_unknown):
            fail('push.native_stale_attempt')
        timestamp = now_utc(now)
        _release(row, locks)
        attempt.status, attempt.ack_digest = 'finished', ack_digest
        attempt.outcome, attempt.response_json, attempt.completed_at = transition.stage, response_json, timestamp
        persist(attempt)
        row.status, row.reason, row.response_json = transition.status, transition.reason, response_json
        row.wire_attempt = transition.attempt
        row.failure_count = int(row.failure_count or 0) + int(transition.stage in {common.STAGE_FAILED, common.STAGE_ROLLED_BACK})
        row.result_unknown, row.lease_expires_at = 0, None
        row.external_docnum = docnum if transition.status == 'success' else None
        row.completed_at = timestamp if transition.status in {'success', 'manual'} else None
        # Preserve owner/active_attempt for exact duplicate ACK verification;
        # they no longer confer a lease and the next claim has a higher fence.
        _save_record(row, timestamp)
        return {**project(row), 'replayed': False}


def recover_expired(name, *, context, adapter, now=None):
    """Expired leased work is unknown/manual; retain all writer locks, never retry."""
    with atomic():
        _receipt_scope(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        locks = _locks(context, json.loads(raw))
        row = _record(name, context)
        timestamp = now_utc(now)
        if row.status != 'leased' or not row.lease_expires_at or row.lease_expires_at > timestamp:
            return {**project(row), 'replayed': True}
        attempt = _current(ATTEMPT, row.active_attempt)
        if int(attempt.fence) != int(row.fence) or any(
                lock.holder != row.name or int(lock.holder_fence or 0) != int(row.fence) for lock in locks):
            fail('push.native_lock_conflict')
        attempt.status = 'unknown'
        persist(attempt)
        row.status, row.result_unknown, row.reason = 'manual', 1, 'ERP_CONFIRMED_PUSH_RESULT_UNKNOWN'
        _save_record(row, timestamp)
        return {**project(row), 'replayed': False}


def cancel_prepared(name, *, context, expected_revision):
    """Withdraw only a never-leased preparation, preserving all frozen evidence."""
    _context(context)
    call(integer, expected_revision, minimum=1)
    with atomic():
        _authorize(context)
        _history(context)
        # The connection lock serializes all transitions for this record; no
        # writer is held by a prepared record, so cancellation cannot release one.
        row = _record(name, context)
        if row.actor != context.actor:
            frappe.throw(_('Not Permitted'), frappe.PermissionError)
        if int(row.revision) != expected_revision:
            fail('push.native_revision_conflict')
        if row.status != 'prepared' or row.result_unknown or row.fence or row.active_attempt:
            fail('push.native_state_invalid')
        row.status, row.reason = 'cancelled', 'push.preparation_cancelled'
        row.completed_at = now_utc()
        _save_record(row, row.completed_at)
        return project(row)
