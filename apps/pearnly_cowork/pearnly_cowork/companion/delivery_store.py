"""Durable internal handle -> fenced attempt mapping, without RPC or commits.

The random public UUID is not a secret. Native Password encryption retains the
attempt capability for a later authenticated ACK. Callers must wrap create and
native.lease in the same transaction/savepoint; neither may survive alone.
"""
from datetime import datetime, timezone
import hashlib
import hmac
import json
from uuid import UUID, uuid4

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

from . import credentials
from .contracts import AgentContext
from .delivery_documents import DOCTYPE, DeliveryDocument, insert


def _error(code='companion.delivery_invalid'):
    frappe.throw(_(code))


def _site():
    if not frappe.local.site or frappe.conf.get('pearnly_product') != 'cowork':
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    return frappe.local.site


def owner(credential):
    """Stable owner label, not an authentication operation or a device identity."""
    if (not isinstance(credential, AgentContext) or credential.site != _site()
            or not isinstance(credential.credential_id, str) or not credential.credential_id
            or type(credential.token_generation) is not int or credential.token_generation < 1):
        _error()
    identity = json.dumps([credential.site, credential.credential_id, credential.token_generation],
                          ensure_ascii=False, separators=(',', ':'))
    return 'companion:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()[:48]


def _name(value):
    try:
        valid = isinstance(value, str) and str(UUID(value)) == value
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        _error('companion.delivery_not_found')
    return value


def get(handle, *, lock=True):
    """Private current-Site lookup; caller supplies its machine authorization."""
    site = _site()
    if type(lock) is not bool:
        _error()
    try:
        doc = frappe.get_doc(DOCTYPE, _name(handle), for_update=lock)
    except frappe.DoesNotExistError:
        _error('companion.delivery_not_found')
    if doc.site_name != site:
        _error('companion.delivery_not_found')
    return doc


def secret(doc):
    """Decrypt only the persisted Password; never trust an in-memory replacement."""
    if not isinstance(doc, DeliveryDocument) or doc.doctype != DOCTYPE or doc.site_name != _site():
        _error()
    row = get(doc.name)
    return get_decrypted_password(DOCTYPE, row.name, 'attempt_secret', raise_exception=True)


def create(*, credential, claim):
    # Revalidate the real private AgentContext, not merely the dataclass shape.
    # The transport already holds Connection/Credential before native.lease;
    # these are reentrant locks in that same caller-owned transaction.
    credential = credentials.revalidate(credential)
    if (not isinstance(claim, dict) or claim.get('leased') is not True
            or not isinstance(claim.get('record'), dict)
            or claim['record'].get('status') != 'leased'
            or type(claim.get('fence')) is not int or claim['fence'] < 1):
        _error()
    record = claim['record']
    password = claim.get('attempt_token')
    if (not isinstance(password, str) or not 1 <= len(password) <= 256
            or '\x00' in password or not record.get('name')):
        _error()
    expected_scope = (credential.workspace, credential.client or '', credential.connection)
    if (record.get('workspace'), record.get('client') or '', record.get('connection')) != expected_scope:
        _error()
    current = frappe.get_doc('Cowork Push Record', record['name'], for_update=True)
    if ((current.workspace, current.client or '', current.connection) != expected_scope
            or current.status != 'leased' or current.result_unknown
            or not current.active_attempt or int(current.fence) != claim['fence']
            or record.get('fence') != claim['fence'] or current.lease_owner != owner(credential)
            or current.branch != credential.branch
            or int(current.binding_generation) != credential.binding_generation):
        _error()
    attempt = frappe.get_doc('Cowork Push Attempt', current.active_attempt, for_update=True)
    if ((attempt.workspace, attempt.client or '', attempt.connection) != expected_scope
            or attempt.push_record != current.name or attempt.status != 'leased'
            or int(attempt.fence) != claim['fence'] or attempt.owner_id != current.lease_owner
            or int(attempt.binding_generation) != credential.binding_generation
            or not hmac.compare_digest(attempt.token_digest, hashlib.sha256(password.encode()).hexdigest())):
        _error()
    doc = frappe.get_doc({'doctype': DOCTYPE, 'record_id': str(uuid4()),
        'site_name': credential.site, 'connection': credential.connection,
        'workspace': credential.workspace, 'client': credential.client or '',
        'credential_id': credential.credential_id, 'token_generation': credential.token_generation,
        'push_record': current.name, 'push_attempt': attempt.name, 'fence': claim['fence'],
        'owner_id': attempt.owner_id, 'issued_at': datetime.now(timezone.utc).replace(tzinfo=None),
        'attempt_secret': password})
    return insert(doc)
