"""Attempt-bound receipt authority, deliberately separate from dispatch rights.

A revoked credential can report only its own already-issued Delivery. It cannot
lease, heartbeat, choose another record, restore a grant or create a new attempt.
No RPC, session mutation, transaction commit or external IO is registered here.
"""
from dataclasses import dataclass, field

import frappe
from frappe import _

from . import contracts, delivery_store
from .documents import DOCTYPE as CREDENTIAL

_AUTHORITY = object()


@dataclass(frozen=True)
class ReceiptContext:
    site: str
    delivery: str
    connection: str
    workspace: str
    client: str
    credential_id: str
    token_generation: int
    push_record: str
    push_attempt: str
    fence: int
    owner: str
    _capability: object = field(default=None, repr=False, compare=False)


def _deny():
    frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)


def _site():
    if not frappe.local.site or frappe.conf.get('pearnly_product') != 'cowork':
        _deny()
    return frappe.local.site


def _load(handle):
    """Connection -> Credential; immutable Delivery is never locked first."""
    site = _site()
    try:
        delivery = delivery_store.get(handle, lock=False)
        if delivery.site_name != site:
            _deny()
        connection = frappe.get_doc('Cowork ERP Connection', delivery.connection, for_update=True)
        credential = frappe.get_doc(CREDENTIAL, delivery.credential_id, for_update=True)
    except (frappe.DoesNotExistError, frappe.ValidationError):
        _deny()
    # Do not inspect the dispatch switch, active_key, issuer's current grants,
    # connection enabled/config revision or current token generation here. They
    # may have changed after the external write; none expands this receipt scope.
    if ((delivery.connection, delivery.workspace, delivery.client or '') !=
            (connection.name, connection.workspace, connection.client or '')
        or (credential.site_name, credential.connection, credential.workspace, credential.client or '',
            int(credential.token_generation), credential.branch, int(credential.binding_generation or 0)) !=
           (site, delivery.connection, delivery.workspace, delivery.client or '',
            int(delivery.token_generation), 'legacy', 0)):
        _deny()
    return delivery, credential


def _context(row):
    return ReceiptContext(row.site_name, row.name, row.connection, row.workspace, row.client or '',
        row.credential_id, int(row.token_generation), row.push_record, row.push_attempt,
        int(row.fence), row.owner_id, _AUTHORITY)


def authenticate(token, delivery):
    """Authenticate a historical secret against one existing delivery only."""
    parsed = contracts.parse_token(token)
    if parsed is None:
        _deny()
    row, credential = _load(delivery)
    if parsed.connection != row.connection or not contracts.token_matches(credential.token_digest, parsed.digest):
        _deny()
    return _context(row)


def revalidate(context):
    if (not isinstance(context, ReceiptContext) or context._capability is not _AUTHORITY
            or context.site != _site()):
        _deny()
    row, _ = _load(context.delivery)
    if context != _context(row):
        _deny()
    return row
