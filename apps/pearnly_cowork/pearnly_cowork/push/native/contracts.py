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
        # Only the legacy branch exists; the never-enabled managed branch is retired (D9).
        if self.branch != 'legacy' or self.binding_generation != 0:
            raise PushLedgerError('push.branch_invalid')

    @property
    def account(self):
        return normalize_express_account_key(self.account_key)


def request_identity(context, request_key):
    return digest([context.workspace, context.actor, text(request_key, length=128)])


def document_identity(context):
    text(context.account_key, length=1024)
    return digest([context.workspace, context.client or '', context.history, context.connection, context.account])


# 锁口径（ADR-021 D6）。冻结目标里写着 ``lock_scheme: 2`` 的 Express 记录用 v2；没写的（这一批之前的、MR.ERP）永远按 v1 算：
# 领取、回执、过期回收、隔离、人工核对每一步都从冻结目标重算锁，口径一改旧记录就放不掉锁。
LOCK_SCHEME = 2


def writer_identities(context, target):
    """这条记录要持有的写者锁 ``[(锁键, 种类)]``。

    v1：一把整条连接的 endpoint 锁 + 一把账套锁（工作空间 + 数据根 + 账套键，同一份账套跨连接也互斥）。
    v2（Express，``lock_scheme: 2``）：不再锁整条连接（D6）；账套锁公式不变（规范根 + 规范键，登记后永不改，与部署前在途的
    旧记录取同一把）；冻结了内容键（税号 + 分店 + 期初）的再加一把公司锁：认定万一把同一份账套拆成两份登记、或同公司同期
    在两台电脑本机各有一份拷贝时，也不会同时写。一张结果不明只挡这份账套和同公司同期的副本。"""
    text(context.account_key, length=1024)
    account = digest(['account', context.workspace,
                      normalize_express_account_key(target.get('root_key')), context.account])
    if target.get('lock_scheme') == LOCK_SCHEME:
        identities = [(account, 'account')]
        company = str(target.get('company_key') or '').strip()
        if company:
            identities.append((digest(['company', context.workspace, company]), 'company'))
        return sorted(identities)
    endpoint = digest(['endpoint', context.connection])
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
            or target.get('adapter') not in ('express', 'mrerp')):
        raise PushLedgerError('push.native_target_mismatch')
    # v2 锁口径只给 Express（电脑领取、每台同时一件）；MR.ERP 由服务端直连执行，仍靠整条连接的 endpoint 锁串行
    if (target.get('lock_scheme') not in (None, LOCK_SCHEME)
            or (target.get('lock_scheme') == LOCK_SCHEME and target.get('adapter') != 'express')
            or not isinstance(target.get('company_key', ''), str)):
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
