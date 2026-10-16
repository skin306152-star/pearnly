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
from pearnly_cowork.history import push_state
from . import completion
from pearnly_cowork.connections import service as connections
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.connections import preparation as preparation_sources
from pearnly_cowork.contracts.push import PushContractError, require_external_history
from pearnly_cowork.push.express import common
from pearnly_cowork.push.lifecycle import acknowledgement, LEASE_SECONDS
from pearnly_cowork.push.express.referenced import used_facts
from pearnly_cowork.push.native.contracts import (
    ACTIVE_STATUSES, LOCK_SCHEME, PushLedgerError, ReservationContext, business_identity, digest, document_identity,
    duplicate_reason, encode, frozen_values, integer, request_identity, text, utc,
    writer_identities,
)
from pearnly_cowork.push.native.documents import TYPES, persist
from pearnly_cowork.push.native import documents as ledger_documents
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


def _save_record(row, timestamp, *, select_history=False):
    row.revision = int(row.revision or 0) + 1
    row.updated_at = timestamp
    row.active_key = row.document_key if row.status in ACTIVE_STATUSES or row.result_unknown else None
    persist(row)
    push_state.mirror(row, timestamp, select=select_history)


def project(row):
    """Internal complete projection. Product API must explicitly remove secrets."""
    result = {key: row.get(key) for key in ('name', 'status', 'revision', 'workspace', 'client',
        'actor', 'route_device', 'history', 'connection', 'account_key', 'history_version', 'connection_revision',
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
        # Account identity spans clients/connections in one company: canonical root + canonical key, never changed
        # once registered (ADR-021). The company lock (lock scheme 2) is keyed by the frozen content key only.
        values = {'lock_key': name, 'lock_kind': kind, 'workspace': context.workspace,
                  'client': context.client or '' if kind == 'endpoint' else '',
                  'connection': context.connection if kind == 'endpoint' else '',
                  'account_key': context.account if kind == 'account' else ''}
        if not frappe.db.sql('SELECT name FROM `tabCowork Push Writer Lock` WHERE name=%s FOR UPDATE', (name,)):
            _insert_unique(_new(WRITER_LOCK, name, **values))
        result.append(_current(WRITER_LOCK, name))
    return result


_OPEN_WRITE = "(r.status='leased' OR (r.status='manual' AND COALESCE(r.result_unknown,0)=1))"


def _older_writer(row, target, *, lock=True):
    """锁口径 v2 的记录还要让着已经领走或结果不明的别的记录（不论它按哪种口径持锁）：

    - 同一连接、同一账套键：部署前在途的旧记录如果冻结的是空的程序目录（补丁按今天的目录登记了规范根），它持的账套锁与
      v2 记录算出的不是同一把——按账套键再核一次，结果不明的旧单照样挡住这份账套（ADR-021，D6）。调用方已锁连接行：同一
      连接上的记录状态变化都先锁它，这里的加锁读看到的是最新提交的。
    - 同一工作空间里登记的内容键（税号 + 分店 + 期初）与这份账套**现在**登记的相同的（第 3 批审查）：准备那会儿登记还没有
      内容键（冻结的公司锁为空）、或部署前的 v1 记录本来就没有公司锁时，公司锁挡不住同公司另一份拷贝。这些记录可能在别的
      连接上，它们的连接行不归我们锁：不加锁读（不按别的连接的加锁顺序抢行锁，也不在快照隔离下撞 1020），只是多一道挡，
      漏看一眼的由公司锁（冻结了的）兜住。

    ``lock=False``：领取前的不加锁预看（:func:`lock_busy`）。"""
    if target.get('lock_scheme') != LOCK_SCHEME:
        return False
    if frappe.db.sql(f'''SELECT r.name FROM `tabCowork Push Record` r
        WHERE r.connection=%s AND r.account_key=%s AND r.name<>%s AND {_OPEN_WRITE} LIMIT 1'''
                     + (' FOR UPDATE' if lock else ''), (row.connection, row.account_key, row.name)):
        return True
    content = str((account_pool.registration(row.connection, row.account_key) or {}).get('content_key') or '')
    if not content:
        return False
    return bool(frappe.db.sql(f'''SELECT r.name FROM `tabCowork Push Record` r
        INNER JOIN `tab{account_pool.ACCOUNT_SET}` a ON a.connection=r.connection AND a.account_key=r.account_key
        WHERE r.workspace=%s AND a.content_key=%s AND r.name<>%s AND {_OPEN_WRITE} LIMIT 1''',
        (row.workspace, content, row.name)))


def lock_busy(name, *, context):
    """领取前不加锁地先看一眼（第 3 批审查：连接行锁着时少做注定等锁的整份重核与加锁读）：这张单要的写者锁有没有被占、
    （锁口径 v2）有没有该让着的别的记录（:func:`_older_writer`）。看错了无妨：看着空的照常走 :func:`lease` 的加锁核对，
    看着占着的下一次轮询再看。只读、不加锁，不改任何东西。"""
    found = frappe.db.get_value(RECORD, name, ['target_json', 'connection', 'account_key', 'workspace'], as_dict=True)
    if not found or not found.target_json:
        return False
    target = json.loads(found.target_json)
    names = tuple(sorted(lock for lock, _kind in call(writer_identities, context, target)))
    if names and frappe.db.sql(f"SELECT name FROM `tab{WRITER_LOCK}` WHERE name IN %s AND COALESCE(holder,'')<>'' LIMIT 1",
                               (names,)):
        return True
    return _older_writer(frappe._dict(name=name, connection=found.connection, account_key=found.account_key,
                                      workspace=found.workspace), target, lock=False)


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
    return target


def reserve(*, context, request_key, frozen_target, frozen_payload,
            disposition='prepared', reason='', checks=None, route_device=''):
    """``route_device``：定时任务读文件夹的那台电脑（ADR-021 第 51 条，只有它写这张单）；空 = 录入人自己的电脑写。
    不进请求指纹：同一请求号重放回原来那条。"""
    _context(context, account=True)
    route_device = call(text, route_device or '', length=140, optional=True) or ''
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
        # 只按这张票自己（document_key）挡重复准备；同号票（另一次上传、同发票号同卖方）照常推，不查重
        # （用户 2026-09-30 / 10-08：Express 手录不拦重复，我们也不拦；10-09 定时任务实测同号票因此卡在「推送中」）。
        for priority in ('already_success', 'active', 'unknown'):
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
            created_at=timestamp, updated_at=timestamp,
            # 录入人（这张单归他登录过 CC 的电脑写，第 50 条）、定时任务读文件夹的那台（只有它写，第 51 条）与这张单要的
            # 载荷版本（领取时按领取的电脑判，方案 2.5）
            entered_by=context.actor, route_device=route_device or None,
            payload_version=_payload_version(frozen_payload), **values)
        if not _insert_unique(row):
            # A DB unique constraint is the final guard, even if a future caller
            # accidentally omits the ordered admission lock.
            fail('push.native_reservation_conflict')
        if disposition == 'manual':
            # 准备阶段就被拦下：历史列表上要看到「待人工核对 + 原因」（用户 2026-09-26）。
            push_state.mirror(row, timestamp, select=True)
        return {**project(row), 'replayed': False}


def _payload_version(payload):
    try:
        return max(1, int((payload or {}).get('payload_version') or 1))
    except (TypeError, ValueError, AttributeError):
        return 1


def _evidence_version(target):
    try:
        return int((target.get('preparation_evidence') or {}).get('version') or 1)
    except (TypeError, ValueError):
        return 1


def writer_scope(connection, account_key):
    """Express 冻结目标里的写者锁口径 v2（ADR-021 D6）：不锁整条连接；账套锁按规范根 + 规范键（冻结目标里本就有），
    登记有内容键（税号 + 分店 + 期初）的再加一把公司锁（``company_key``）。只读。"""
    found = account_pool.registration(connection, account_key) or {}
    return {'lock_scheme': LOCK_SCHEME, 'company_key': str(found.get('content_key') or '')}


def basis_hash(facts, payload):
    """准备证据 v2 里电脑来领时要核的那一份（D5）：这份载荷实际用到的映射、画像与主档行。准备时与领取时同一个函数。"""
    return preparation_sources.basis(used_facts(payload, facts))


def has_writer(connection, account_key, *, actor, route_device=''):
    """替这张单干活的电脑里至少一台能写这份 Express 账套（不要求在线，D4）：确认与「这份准备还能不能确认」的判据。

    谁的活谁的电脑做（ADR-021 第 51–53 条，用户 10-16）：给了 ``route_device``（定时任务读文件夹的那台）只看它；否则只看
    ``actor`` 自己登录过 CC 的电脑（`connections.pool.owned`）。同事的电脑够得到不算——它不会替他写。与池的状态
    （`companion.pool.state` 的 ``writers_known``，同样按人）同一个判据 `connections.pool.writers`：登记着、可达可写、
    密钥没吊销、签发人此刻仍能用这条连接。只读。"""
    if not connection or not account_key:
        return False
    doc = frappe.get_doc('Cowork ERP Connection', connection)
    mine = account_pool.owned(account_pool.presence(doc.name), actor, device=route_device or '')
    return bool(mine) and bool(account_pool.writers(doc, account_key, peers=mine))


def _still_confirmable(row, context, loaded):
    """这份没发出过的准备现在还能不能确认发送（与 `confirm` 同一套核对）。

    池里没有一台电脑够得到这份账套、准备过了一天、账套资料变了，确认一律被拒；这时再点推送若还返回这份旧准备，
    就永远卡在「推送目标已变更」（2026-09-28 录制）。确认不了的就当过期，由新的准备替换——它没领过、没写过，不碰写者锁。
    证据 v1（绑定准备那会儿上报的那把密钥，ADR-021 之前准备的）一律确认不了。"""
    target = json.loads(row.target_json or '{}')
    if target.get('adapter') == 'express' and (_evidence_version(target) < 2 or not has_writer(
            context.connection, context.account, actor=row.entered_by or row.actor or context.actor,
            route_device=row.route_device or '')):
        return False
    messages = len(frappe.local.message_log or [])
    try:
        _dispatch_target(context, loaded, target, mode='confirm')
    except DispatchRejected:
        # 只是核对：拒绝的提示不带给这次推送的页面。
        del frappe.local.message_log[messages:]
        return False
    return True


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
        stale = not _still_confirmable(row, context, loaded)
    if not stale:
        return False
    row.status, row.reason = 'cancelled', 'push.preparation_superseded'
    row.completed_at = now_utc()
    _save_record(row, row.completed_at)  # modified_by records the current actor.
    return True


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
    # 问责（ADR-021）：哪台电脑来领的、它的登记人、按什么分派（own / reader / sticky，第 51 条）。服务端直连的
    # MR.ERP 执行器不填。它们只记进写入尝试与推送记录，不授予任何权限。
    device: str = ''
    device_owner: str = ''
    route: str = ''


def trusted_adapter_context(connection, owner, binding_generation=0, *, device='', device_owner='', route=''):
    """Internal injection seam, not identity verification or a public credential API.

    The internal Companion transport must authenticate its credential before
    calling; native tests can inject a synthetic adapter through the same seam.
    """
    return AdapterContext(call(text, connection), call(text, owner),
                          call(integer, binding_generation), _ADAPTER_CAPABILITY,
                          call(text, device, optional=True), call(text, device_owner, optional=True),
                          call(text, route, length=20, optional=True))


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


def _dispatch_target(context, loaded, target, *, mode, payload=None):
    """``mode``：``confirm``（人确认、激活、`_still_confirmable`：准备证据整份相等，含日期）或 ``lease``（电脑来领：
    只核这份账套的登记仍在、服务端事实与本单载荷 ``payload`` 实际用到的主档行没变，方案 2.5「准备证据 v2」，D5）。"""
    # Snapshot refresh does not have to change the connection revision. Keep
    # the original proof identity and reject an expired/superseded proof; never
    # silently choose a new target or regenerate the frozen payload.
    try:
        current = _validate_target(context, loaded, target, reject=_dispatch_rejected)
    except TargetSelectionError:
        raise DispatchRejected('push.native_target_changed') from None
    if target.get('preparation_evidence'):
        if target.get('adapter') == 'express' and _evidence_version(target) < 2:
            # 证据 v1 绑定准备那会儿上报的那把密钥、整份重算；多台电脑后不再保留这条核对（ADR-021，方案 4.4）
            raise DispatchRejected('push.native_target_changed')
        rows = (lambda facts: used_facts(payload, facts)) if mode == 'lease' else None
        try:
            preparation_sources.revalidate(loaded, current, target['preparation_evidence'], mode=mode, used=rows)
        except frappe.ValidationError:
            raise DispatchRejected('push.native_target_changed') from None
        own_tax = frappe.db.get_value('Cowork Workspace', context.workspace, 'tax_id', for_update=True) or ''
        if target.get('own_tax_id') != own_tax:
            raise DispatchRejected('push.native_target_changed')
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


def confirm(name, *, context, expected_revision, request_key):
    """Human prepared -> pending CAS with a durable confirmation receipt.

    Express no longer needs any computer's key (ADR-021): the actor's current grant, the account set's registration
    and at least one computer that reaches it (online or not, D4). MR.ERP is executed server-side as before."""
    _context(context, account=True)
    operation = call(request_identity, context, 'confirm:' + call(text, request_key, length=180))
    call(integer, expected_revision, minimum=1)
    with atomic():
        loaded = _authorize(context)
        history = _history(context)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        target = json.loads(raw)
        _locks(context, target)
        row = _record(name, context)
        if row.actor != context.actor:
            frappe.throw(_('Not Permitted'), frappe.PermissionError)
        if row.confirmation_request:
            if row.confirmation_request != operation or int(row.confirmation_revision) != expected_revision:
                fail('push.request_conflict')
            return {**project(row), 'replayed': True}
        if (row.status != 'prepared' or row.result_unknown or row.revision != expected_revision
                or int(history.record_version) != context.history_version):
            fail('push.native_state_invalid')
        if frappe.conf.get('pearnly_cowork_erp_push_enabled') not in (True, 1):
            fail('ERR_EXPRESS_DISABLED')
        _dispatch_record(row, context)
        if target.get('adapter') == 'express':
            if _evidence_version(target) < 2:
                # ADR-021 之前准备的（证据绑定一把密钥）：重推会换一份新的准备（`_still_confirmable`）
                fail('push.native_target_changed')
            if not has_writer(context.connection, context.account, actor=row.entered_by or row.actor,
                              route_device=row.route_device or ''):
                # 录入人自己的电脑（定时任务：读文件夹的那台）没有一台够得到这份账套：排上也没人写，不转给同事的电脑
                # （第 53 条）。电脑关着不算（D4，开机后写）
                fail('push.no_writer')
        _dispatch_target(context, loaded, target, mode='confirm')
        row.confirmation_request, row.confirmation_revision = operation, expected_revision
        row.status, row.reason = 'pending', ''
        _save_record(row, now_utc(), select_history=True)
        return project(row)


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
        _dispatch_target(context, loaded, target, mode='confirm')
        locks = _locks(context, target)
        row = _record(name, context)
        _dispatch_record(row, context)
        if row.status == 'pending':
            return {**project(row), 'replayed': True}
        if row.status != 'prepared' or row.result_unknown:
            fail('push.native_state_invalid')
        row.status, row.reason = 'pending', ''
        _save_record(row, now_utc(), select_history=True)
        return project(row)


def lease(name, *, context, adapter, now=None):
    """Claim one known pending record. Never scans or executes an ERP queue."""
    with atomic():
        loaded = _dispatch_context(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        target = json.loads(raw)
        payload = json.loads(frappe.db.get_value(RECORD, name, 'payload_json') or '{}')
        _dispatch_target(context, loaded, target, mode='lease', payload=payload)
        locks = _locks(context, target)
        row = _record(name, context)
        _dispatch_record(row, context)
        if row.status != 'pending' or row.result_unknown:
            raise LeaseUnavailable()
        if _automation_stale(target, context, loaded):
            return _withdraw_automation(row, target, payload, now)
        if any(lock.holder for lock in locks) or _older_writer(row, target):
            return {'leased': False, 'reason': 'waiting_lock', 'record': project(row)}
        timestamp = now_utc(now)
        token = secrets.token_urlsafe(32)
        fence = int(row.fence or 0) + 1
        attempt_name = 'CWPA-' + digest([row.name, fence])[:48]
        attempt = _new(ATTEMPT, attempt_name, push_record=row.name, workspace=row.workspace,
            client=row.client or '', connection=row.connection, fence=fence, owner_id=adapter.owner,
            token_digest=hashlib.sha256(token.encode()).hexdigest(), binding_generation=row.binding_generation,
            started_at=timestamp, lease_expires_at=timestamp + timedelta(seconds=LEASE_SECONDS), status='leased',
            device=adapter.device or None, device_owner=adapter.device_owner or None, route=adapter.route or None)
        persist(attempt)
        for lock in locks:
            lock.holder, lock.holder_fence, lock.lease_expires_at = row.name, fence, attempt.lease_expires_at
            persist(lock)
        row.status, row.fence, row.active_attempt = 'leased', fence, attempt_name
        row.lease_owner, row.lease_expires_at = adapter.owner, attempt.lease_expires_at
        # 由哪台电脑写、按哪一档分派（问责；推送记录的 actor 仍是录入人）。每台同时只领一件按它判（`companion.transport`）
        row.written_by_device, row.write_route = adapter.device or None, adapter.route or None
        _save_record(row, timestamp)
        return {'leased': True, 'record': project(row), 'fence': fence, 'attempt_token': token,
                'payload': deepcopy(json.loads(row.payload_json)), 'target': deepcopy(json.loads(row.target_json))}


AUTOMATION_CHANGED = 'push.automation_changed'


def _automation_stale(target, context, loaded):
    """自动推的单（冻结了 ``automation``）领单时再核一次（数字会计员第 3 期第 5 条）：票的版本（`_dispatch_context` 已核）、
    这家当前档位、这张单用得着的账套记忆与公司规则，任一变了就不写旧判定。人发起的单没有这一项，不核。"""
    automation = target.get('automation')
    if not isinstance(automation, dict):
        return False
    from pearnly_cowork.push.automation import service as automation_service  # 只有自动推的单才用得着

    rows = (loaded.evidence.snapshot or {}).get('account_sets') or []
    if isinstance(rows, str):
        rows = json.loads(rows)
    account = next((row for row in rows if isinstance(row, dict)
                    and normalize_express_account_key(row.get('source_id')) == context.account), None)
    current = automation_service.current_hash(context.history_version, context.connection, context.account, account,
                                              automation.get('scope'))
    return current != automation.get('hash')


def _withdraw_automation(row, target, payload, now):
    """领单时判定已过时：这条作废（``cancelled``，不写），票改记成提议（冻结这份载荷的做法），人在待处理里推。"""
    from pearnly_cowork.push.automation import rules as automation_rules

    automation = target.get('automation') or {}
    timestamp = now_utc(now)
    row.status, row.reason, row.completed_at = 'cancelled', AUTOMATION_CHANGED, timestamp
    _save_record(row, timestamp)
    proposal = automation_rules.proposal(automation.get('class'), automation.get('tier'), automation.get('trigger'),
                                         payload, {}, at=timestamp.replace(tzinfo=None).isoformat())
    push_state.withdraw_automation(row, 'automation_changed', proposal=proposal)
    return {'leased': False, 'reason': 'automation_changed', 'record': project(row)}


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
    # payload_version_outdated：池里够得到这份账套的电脑没有一台支持这张单的载荷版本（只能等人更新 CC 桌面版），与以前
    # 准备时就拦下同一个原因码与词条；隔离后同一账套后面的单照常写（第 3 批审查）
    reasons = {'push.native_history_changed', 'push.native_target_changed',
               'push.native_target_mismatch', 'companion.queue_access_revoked', 'payload_version_outdated'}
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


# 冻结给某台电脑、还没写完的定时任务单（`reroute` 跟着改挂；`release_route` 那台退役时转人工）
_ROUTED_OPEN = ('prepared', 'pending', 'leased')


def _routed(device, statuses, *, connection=''):
    """冻结给 ``device`` 的这几种状态的记录名：先不加锁挑（``route_device`` 没有索引，加锁读会锁住扫过的每一行），调用方再按
    主键逐张锁住复核。平时没有这种记录。"""
    query = 'SELECT name FROM `tabCowork Push Record` WHERE route_device=%s AND status IN %s'
    values = [device, tuple(statuses)]
    if connection:
        query += ' AND connection=%s'
        values.append(connection)
    return [row[0] for row in frappe.db.sql(query + ' ORDER BY name', tuple(values))]


def reroute(connection, old, new):
    """同一台电脑的旧记录 ``old`` 在这条连接上改挂到 ``new``（`companion.devices._move`：1.6 升 1.7.0 时补丁建的旧电脑被
    新登记的记录接手等）：这条连接上冻结给 ``old``、还没写完的定时任务单（已准备 / 排队 / 正在写）改由 ``new`` 写
    （ADR-021 第 51 条，10-16 审查）。不然 ``new`` 来领时按 ``route_device`` 筛不到它们，``old`` 又已退役，单子永远排着。
    正在写的那件照常由原来的尝试回执（回执不看 ``route_device``），只是租约过期回收后由 ``new`` 接着领。

    调用方已锁这条连接与密钥行（加锁顺序 Connection → Credential → 推送记录 → Device）；不改状态、不碰写者锁，只换这一列
    （升一次版本）。回改了几张。"""
    if not connection or not old or not new or old == new:
        return 0
    moved = 0
    for name in _routed(old, _ROUTED_OPEN, connection=connection):
        row = _current(RECORD, name)
        if row.connection != connection or row.route_device != old or row.status not in _ROUTED_OPEN:
            continue
        row.revision = int(row.revision or 0) + 1
        row.updated_at = now_utc()
        ledger_documents.reroute(row, new)
        moved += 1
    return moved


def release_route(device, *, reason='push.no_writer'):
    """电脑 ``device`` 退役（「电脑」列表移除、没人认领的旧电脑一把密钥都不剩，且没有同一台的新记录接手）：冻结给它、还在排队的
    定时任务单再也没人写，转人工，原因用现有的 ``push.no_writer``（「没有能写这份账套的电脑…」，待处理页显示、能重推），
    不让它永远停在「推送中」（ADR-021 第 51、53 条，10-16 审查）。已准备的确认时自会按同一原因被拒；正在写的照常等回执或
    租约过期转「结果不明」。排队的单不持写者锁（领取时才取），这里不碰锁。

    调用方在锁电脑行之前调用（加锁顺序：推送记录在电脑行之前）。回转了几张。"""
    if not device:
        return 0
    moved = 0
    for name in _routed(device, ('pending',)):
        row = _current(RECORD, name)
        if row.status != 'pending' or row.result_unknown or row.route_device != device:
            continue
        row.status, row.reason = 'manual', reason
        _save_record(row, now_utc())
        moved += 1
    return moved


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


def _response(branch, stage, docnum, line_modes, meta, adapter='express'):
    if adapter == 'mrerp':
        return {'ok': stage == common.STAGE_SUCCESS,
                'mrerp_bill_no': docnum if stage == common.STAGE_SUCCESS else None,
                'meta': {**common.sanitize_push_meta(meta), 'stage': stage}}
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
        target = json.loads(raw)
        locks = _locks(context, target)
        row = _record(name, context)
        attempt = _attempt(row, adapter, attempt_token, fence)
        if row.status == 'success':
            return {**project(row), 'replayed': True}  # never replace a success receipt
        transition = acknowledgement(branch=row.branch, attempt=int(row.wire_attempt),
            success=success, outcome=outcome, error=error)
        docnum = call(text, express_docnum, length=140, optional=True) or None
        response = _response(row.branch, transition.stage, docnum, line_modes, meta, target.get('adapter') or 'express')
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
        if transition.status == 'success':
            completion.success(row, timestamp)
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


def mark_unknown(name, *, context, adapter, fence, attempt_token, reason='ERP_CONFIRMED_PUSH_RESULT_UNKNOWN'):
    """The owning attempt already sent an irreversible write and then lost its result.

    Same effect as an expired lease (`recover_expired`): manual + unknown, all writer
    locks retained, never retried automatically. Used by the server-side MR.ERP
    executor after its import confirmation request was sent.
    """
    with atomic():
        _receipt_scope(context, adapter)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        locks = _locks(context, json.loads(raw))
        row = _record(name, context)
        attempt = _attempt(row, adapter, attempt_token, fence)
        if row.status != 'leased' or attempt.status != 'leased':
            return {**project(row), 'replayed': True}
        if any(lock.holder != row.name or int(lock.holder_fence or 0) != int(row.fence) for lock in locks):
            fail('push.native_lock_conflict')
        attempt.status = 'unknown'
        persist(attempt)
        row.status, row.result_unknown = 'manual', 1
        row.reason = call(text, reason, length=500)
        _save_record(row, now_utc())
        return {**project(row), 'replayed': False}


def resolve_unknown(name, *, context, entered, docnum=None, now=None):
    """结果不明（写入已发出、没拿到结果；或修复前推进了没关联日记账的 MR.ERP 菜单）由人去 ERP 核对后解除。

    已入账 → 记成功（可填 ERP 实际单号），走与正常成功同一个收尾；未入账 → 放开，这张票可以重推。
    这条记录还占着的写者锁一并放掉。只接受「转人工 + 结果不明」的记录，别的状态一律拒绝。"""
    _context(context)
    if type(entered) is not bool:
        fail('push.native_request_invalid')
    with atomic():
        _authorize(context)
        _history(context)
        raw = frappe.db.get_value(RECORD, name, 'target_json')
        if not raw:
            fail('push.native_record_not_found')
        target = json.loads(raw)
        locks = _locks(context, target)
        row = _record(name, context)
        if row.status != 'manual' or not row.result_unknown:
            fail('push.native_state_invalid')
        timestamp = now_utc(now)
        _release(row, [lock for lock in locks if lock.holder == row.name])
        if row.active_attempt:
            attempt = _current(ATTEMPT, row.active_attempt)
            if attempt.status in {'leased', 'unknown'}:
                attempt.status, attempt.completed_at = 'finished', timestamp
                attempt.outcome = common.STAGE_SUCCESS if entered else 'confirmed_not_entered'
                persist(attempt)
        docnum = call(text, docnum, length=140, optional=True) or None
        row.result_unknown, row.lease_expires_at, row.completed_at = 0, None, timestamp
        if entered:
            row.status, row.reason, row.external_docnum = 'success', '', docnum
            row.response_json = call(encode, _response(row.branch, common.STAGE_SUCCESS, docnum, None,
                {'resolution': 'checked_in_erp'}, target.get('adapter') or 'express'))
            completion.success(row, timestamp)
        else:
            row.status, row.reason = 'manual', 'confirmed_not_entered'
        _save_record(row, timestamp)
        return project(row)


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


def open_account_keys(connection):
    """被未了结推送记录（不是成功、也不是作废）引用的账套键：连接域的账套资料不淘汰它们（ADR-021，方案 2.2）。只读。"""
    rows = frappe.db.sql("""SELECT DISTINCT account_key FROM `tabCowork Push Record`
        WHERE connection=%s AND status NOT IN ('success', 'cancelled')""", (connection,))
    return {row[0] for row in rows if row and row[0]}
