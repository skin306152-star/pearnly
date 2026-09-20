"""Value contracts for the native ledger; no framework, clock or IO."""
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
import hashlib
import json

from pearnly_cowork.targets.identity import normalize_express_account_key

MAX_JSON_BYTES = 8 * 1024 * 1024
ACTIVE_STATUSES = frozenset({'prepared', 'pending', 'leased', 'success'})


class PushLedgerError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def text(value, *, length=140, optional=False):
    if optional and value in (None, ''):
        return ''
    if not isinstance(value, str) or not value.strip() or len(value) > length or '\x00' in value:
        raise PushLedgerError('push.native_request_invalid')
    return value.strip()


def integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise PushLedgerError('push.native_request_invalid')
    return value


def encode(value):
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as error:
        raise PushLedgerError('push.native_request_invalid') from error
    if len(result.encode('utf-8')) > MAX_JSON_BYTES:
        raise PushLedgerError('push.native_payload_too_large')
    return result


def digest(value):
    return hashlib.sha256(encode(value).encode('utf-8')).hexdigest()


def utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PushLedgerError('push.utc_required')
    return value.astimezone(timezone.utc).replace(tzinfo=None)


@dataclass(frozen=True)
class ReservationContext:
    actor: str
    workspace: str
    client: str | None
    history: str
    history_version: int
    connection: str
    account_key: str
    branch: str = 'legacy'
    binding_generation: int = 0
    connection_revision: int = 0

    def __post_init__(self):
        for name in ('actor', 'workspace', 'history', 'connection'):
            text(getattr(self, name))
        text(self.client, optional=True)
        text(self.account_key, length=1024, optional=True)
        integer(self.history_version, minimum=1)
        integer(self.connection_revision)
        integer(self.binding_generation)
        if ((self.branch == 'legacy' and self.binding_generation != 0)
                or (self.branch == 'managed' and self.binding_generation < 1)
                or self.branch not in {'legacy', 'managed'}):
            raise PushLedgerError('push.branch_invalid')

    @property
    def account(self):
        return normalize_express_account_key(self.account_key)


def request_identity(context, request_key):
    return digest([context.workspace, context.actor, text(request_key, length=128)])


def document_identity(context):
    text(context.account_key, length=1024)
    return digest([context.workspace, context.client or '', context.history, context.connection, context.account])


def writer_identities(context, target):
    # Endpoint serialization plus a conservative same-company/account lock.
    # Different connections pointing to the same reported root/data path cannot
    # write together; unrelated companies with the same local folder stay apart.
    text(context.account_key, length=1024)
    endpoint = digest(['endpoint', context.connection])
    account = digest(['account', context.workspace,
                      normalize_express_account_key(target.get('root_key')), context.account])
    return sorted(((endpoint, 'endpoint'), (account, 'account')))


def frozen_values(context, target, payload, disposition, reason, checks):
    text(context.account_key, length=1024)
    if is_dataclass(target):
        target = asdict(target)
    if not isinstance(target, dict) or not isinstance(payload, dict) or not isinstance(checks, list):
        raise PushLedgerError('push.native_request_invalid')
    if disposition not in {'prepared', 'manual'}:
        raise PushLedgerError('push.native_state_invalid')
    if (target.get('endpoint_id') != context.connection
            or normalize_express_account_key(target.get('account_set')) != context.account
            or target.get('binding_generation') != context.binding_generation
            or target.get('adapter') != 'express'):
        raise PushLedgerError('push.native_target_mismatch')
    reason = text(reason, length=500, optional=True)
    values = {'context': asdict(context), 'target': target, 'payload': payload,
              'disposition': disposition, 'reason': reason, 'checks': checks}
    return {'target_json': encode(target), 'payload_json': encode(payload),
            'checks_json': encode(checks), 'request_fingerprint': digest(values), 'reason': reason}


def duplicate_reason(row):
    if row.get('status') == 'success':
        return 'already_success'
    if row.get('status') in {'prepared', 'pending', 'leased'}:
        return 'active'
    if row.get('result_unknown'):
        return 'unknown'
    return None


def business_identity(context, invoice_no, seller_name):
    """Legacy owner/endpoint/account + exact invoice/seller equality, with scope."""
    text(context.account_key, length=1024)
    if not invoice_no:
        return None
    return digest([context.workspace, context.client or '', context.actor, context.connection,
                   context.account, invoice_no, seller_name or ''])
