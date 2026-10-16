"""Local Express queue adapter into the fenced native push ledger.

Internal service only: no HTTP route, worker, scheduler, commit or external ERP
write. The caller must commit before exposing a lease response. Prepared records
are never activated by polling; explicit dispatch activation is a separate step.

Several computers share one Express connection (ADR-021, plan 2.5, lease v2): the
computer that polls decides nothing by itself. For each account set it reaches, only
that account set's queue head is considered; the pure routing rules
(`route_contracts`) say whether this computer may take it now. A head that is not its
turn, waits for its account lock or needs a CC Desktop version another computer has is
skipped (never quarantined), and the next account set is looked at; a head whose payload
version no computer of the pool supports goes to manual review with the pre-batch-3 reason
(``payload_version_outdated``) so the account set's later jobs keep flowing. Each computer
holds at most one lease at a time on a connection. The leased payload copy carries this
computer's own spelling of the account set's location; the ledger's frozen payload never
changes.

The connection row is locked for the whole claim, so the claim reads in batches: the heads
of every account set (`queue_heads`), their reachers and registrations in one query each,
and a non-locking look at the writer locks before the full lease re-check (batch 3 review).
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import frappe
from frappe import _

from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.connections import refresh
from pearnly_cowork.push.express import wire_location
from pearnly_cowork.push.lifecycle import error_code
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.native.contracts import ReservationContext
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import credentials, delivery_store, devices, queue_contracts, queue_heads, receipt_auth, route_contracts

@contextmanager
def _atomic():
    point = 'cowork_delivery_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        yield
    except frappe.QueryDeadlockError:
        # Native ledger may already have rolled back a deadlocked transaction.
        # The transport does not retry or commit any part of the caller's unit.
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


def _value(function, *args):
    try:
        return function(*args)
    except queue_contracts.QueueContractError as error:
        frappe.throw(_(error.code))


def _context(row, credential):
    if ((row.connection, row.workspace, row.client or '', row.branch, int(row.binding_generation or 0)) !=
            (credential.connection, credential.workspace, credential.client, 'legacy', 0)):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    # Actor, versions, target, account and payload always come from stored work.
    return ReservationContext(row.actor, row.workspace, row.client or '', row.history,
        int(row.history_version), row.connection, row.account_key, branch=row.branch,
        binding_generation=int(row.binding_generation), connection_revision=int(row.connection_revision))


def _adapter(connection, owner, **accountability):
    return ledger.trusted_adapter_context(connection, owner, 0, **accountability)


def _recover(credential, now):
    # Only this adapter's persisted deliveries. A reset credential may observe
    # an old attempt's expiry but cannot acknowledge it or release its locks.
    rows = frappe.db.sql('''SELECT r.name, d.owner_id FROM `tabCowork Push Record` r
        INNER JOIN `tabCowork Companion Delivery` d ON d.push_attempt=r.active_attempt
        WHERE r.connection=%s AND r.status='leased' AND r.lease_expires_at<=%s
        ORDER BY r.created_at,r.name''', (credential.connection, ledger.now_utc(now)), as_dict=True)
    for item in rows:
        row = frappe.get_doc(ledger.RECORD, item.name)
        ledger.recover_expired(row.name, context=_context(row, credential),
            adapter=_adapter(credential.connection, item.owner_id), now=now)


def _busy(device, now):
    """这台电脑手里还有一件没到期的写入（每台同时最多一件：桌面端本就单线程，也替代以前整条连接的 endpoint 锁）。

    不加锁读、按整台电脑查：同一台电脑在两条连接上各有一把密钥时，两次领取锁的是不同的连接行，可能各领一件——「每台一件」
    只在一条连接内严格成立（ADR-021 第 16 条）。"""
    return bool(frappe.db.sql('''SELECT name FROM `tabCowork Push Record`
        WHERE written_by_device=%s AND status='leased' AND lease_expires_at>%s LIMIT 1''',
        (device, ledger.now_utc(now))))


def _peers(connection, device, request, now):
    """``{设备: route_contracts.Peer}``；来领的这台在线，它这次报的 ``index_pending`` 立即算数（电脑行在最后才写）。"""
    peers = {}
    for name, peer in credentials.pool_peers(connection, now).items():
        peers[name] = route_contracts.Peer(users=frozenset(peer.get('users') or ()), online=bool(peer.get('online')),
                                           payload_version=peer.get('payload_version'),
                                           index_pending=peer.get('index_pending'))
    mine = peers.get(device) or route_contracts.Peer()
    if 'index_pending' in request:
        mine = mine._replace(index_pending=frozenset(request['index_pending']))
    peers[device] = mine._replace(online=True)
    return peers


def _account(found):
    found = found or {}
    return route_contracts.Account(uid=found.get('uid') or '', last_writer=found.get('last_writer_device') or '',
                                   last_written_at=found.get('last_written_at'))


def _claim(credential, device, request, now):
    """按账套排头分派，最多领一件（方案 2.5「transport.lease v2」；谁的活谁的电脑做，ADR-021 第 51 条）。"""
    reach = account_pool.writable_reach(credential.connection, device)
    if not reach:
        return []  # 这台电脑够不到任何可写的账套（没装 Express、只读文件夹的成员）：领不到写入
    # 只在这台能写的单里取各账套的排头：冻结给它的定时任务 + 录入人在它上面登录过 CC 的（没有登录记录的电脑谁的都写）
    users = devices.signed_in_of(device) if device != credential.credential_id else frozenset()
    heads = queue_heads.heads(credential.connection, credential.workspace, credential.client, reach,
                              device=device, users=users)
    if not heads:
        return []
    keys = [head.account_key for head in heads]
    reachers = account_pool.reachers_of(credential.connection, keys, writable=True)
    registered = account_pool.registrations_of(credential.connection, keys)
    versions = queue_heads.payload_versions(heads)
    peers = _peers(credential.connection, device, request, now)
    owner = frappe.db.get_value(devices.DOCTYPE, device, 'device_owner') if device != credential.credential_id else ''
    clock = ledger.now_utc(now)
    for head in heads:
        key = head.account_key
        job = route_contracts.Job(head.name, key, head.entered_by or head.actor, versions[head.name],
                                  head.route_device or '')
        account = _account(registered.get(key))
        decision = route_contracts.decide(device, job, reachers.get(key) or [], peers, account, clock)
        if not decision.take and decision.reason != route_contracts.NEEDS_UPDATE:
            continue  # 黏在别的电脑上、要更新的 CC 桌面版在录入人的另一台电脑上：跳过，看下一份账套（不隔离）
        row = frappe.get_doc(ledger.RECORD, head.name)
        reservation = _context(row, credential)
        adapter = _adapter(credential.connection, delivery_store.owner(credential), device=device,
                           device_owner=owner or '', route=decision.route)
        if not decision.take:
            # 归这张单、够得到它的电脑没有一台支持这张单的载荷版本：等不来，转人工（与以前准备时就拦下同一个原因与词条），
            # 这份账套后面的单照常写。旧版不报最高版本的不算（`route_contracts.supports`）
            ledger.quarantine_pending(row.name, context=reservation, adapter=adapter, reason='payload_version_outdated')
            continue
        if ledger.lock_busy(row.name, context=reservation):
            continue  # 不加锁先看一眼：锁被占、该让着别的单。省掉注定等锁的整份重核
        try:
            claim = ledger.lease(row.name, context=reservation, adapter=adapter, now=now)
        except ledger.LeaseUnavailable:
            # A pre-existing RR snapshot can select an already-taken row;
            # the locked ledger state wins. No mutation or generic catch.
            continue
        except (ledger.DispatchRejected, frappe.PermissionError) as error:
            # 只有确定失效才隔离：录入人失权、准备时的资料（本单用到的）变了、目标不在了、证据 v1
            reason = error.code if isinstance(error, ledger.DispatchRejected) else 'companion.queue_access_revoked'
            ledger.quarantine_pending(row.name, context=reservation, adapter=adapter, reason=reason)
            continue
        if not claim['leased']:
            continue  # 等账套锁：看别的账套，不再整条连接停下
        spelling = reach[key]
        record = {**claim['record'], 'frozen_payload': wire_location.localize(
            claim['record']['frozen_payload'], path=spelling.path, root=spelling.root, row=spelling.registry_row,
            uid=account.uid)}
        delivery = delivery_store.create(credential=credential, claim=claim)
        # 自动推不弹「账套被占用」（第 3 期第 5 条）
        automated = isinstance((claim.get('target') or {}).get('automation'), dict)
        attended = route_contracts.attended(job._replace(automated=automated), peers.get(device))
        return [_value(queue_contracts.job, delivery.name, record, attended)]
    return []


def lease(context, body):
    """Claim at most one job for the polling computer (lease v2, ADR-021)."""
    request = _value(queue_contracts.lease_request, body)
    with _atomic():
        credential = credentials.revalidate(context)
        now = datetime.now(timezone.utc)
        _recover(credential, now)
        device = credentials.device_of(credential.credential_id) or credential.credential_id
        jobs = [] if _busy(device, now) else _claim(credential, device, request, now)
        doc = frappe.get_doc('Cowork ERP Connection', credential.connection, for_update=True)
        # 没有心跳的所选：按这台电脑上次报的（ADR-021，每台各自一份）
        member = devices.member(device, doc.workspace, connection=doc.name, pool_hosts=False)
        requested_refresh = refresh.lease(doc, member, member.selected, credential.credential_id,
                                          credential.token_generation, ledger.now_utc(now))
        # 电脑行在加锁顺序的最后：这次报的「主档索引还没重建的账套」
        devices.note_index_pending(device if device != credential.credential_id else None,
                                   request.get('index_pending'))
        return _value(queue_contracts.lease_response, jobs, requested_refresh)


def acknowledge(context, body):
    request = _value(queue_contracts.ack_request, body)
    with _atomic():
        delivery = receipt_auth.revalidate(context)
        if request['log_id'] != delivery.name:
            frappe.throw(_('companion.unauthorized'), frappe.AuthenticationError)
        row = frappe.get_doc(ledger.RECORD, delivery.push_record)
        result = ledger.ack(row.name, context=_context(row, context),
            adapter=_adapter(context.connection, delivery.owner_id), fence=int(delivery.fence),
            attempt_token=delivery_store.secret(delivery), success=request['result'] == 'success',
            outcome=request['outcome'], express_docnum=request['express_docnum'], error=request['error'],
            line_modes=request['line_modes'], meta=request['meta'])
        if not result.get('replayed'):
            _after_ack(row, delivery, request, result)
        return _value(queue_contracts.ack_response, result)


def _after_ack(row, delivery, request, result):
    """回执之后池里的两件事（池里各行在记录之后锁，同一连接上都先锁了连接行）：

    - 写成了：记在这份账套的登记上（谁、何时），旧版不报主档索引时按它近似黏住；
    - 写前核文件号对不上（1.7.0 回 ``waiting_lock`` + ``[ERR_ACCOUNT_UID_MISMATCH]``）：这台电脑对这份账套的可达行标
      stale、要它整份重报，单子已回到排队，下次交给别的电脑。"""
    device = credentials.device_of(delivery.credential_id) or delivery.credential_id
    key = normalize_express_account_key(row.account_key)
    if result.get('status') == 'success':
        account_pool.note_written(row.connection, key, device, ledger.now_utc())
    elif (result.get('status') == 'pending'
          and error_code(request.get('error')) == queue_contracts.ERR_ACCOUNT_UID_MISMATCH):
        if account_pool.mark_stale(row.connection, device, key):
            credentials.request_full_report(delivery.credential_id)
