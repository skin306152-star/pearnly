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
from pearnly_core.express_rules.push import wire_location
from pearnly_core.cc_desktop.wire.ack_rules import error_code
from pearnly_cowork.push.native import service as ledger
from pearnly_cowork.push.native.contracts import ReservationContext
from pearnly_core.express_rules.targets.identity import normalize_express_account_key
from pearnly_core.cc_desktop.wire import queue_contracts, request_contracts, route_contracts
from pearnly_core.express_operator import contracts as operator_target
from . import credentials, delivery_store, devices, mrerp_jobs, queue_heads, receipt_auth, request_service

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


def _row_context(row, credential, device):
    """这把密钥手上的一条记录 → 推送上下文：Express 记录与密钥同一条连接（原样）；MR.ERP 代录单在同一工作空间的 MR.ERP 连接上
    （`mrerp_jobs.context`，ADR-026 第 3 步）。"""
    if row.connection == credential.connection:
        return _context(row, credential)
    return mrerp_jobs.context(row, credential, device)


def _recover(credential, now, device):
    # Only this adapter's persisted deliveries. A reset credential may observe
    # an old attempt's expiry but cannot acknowledge it or release its locks.
    # 领它的电脑还在（按需请求线程 90 秒内来过，`request_service.presence`；那条线程与写账线分开跑）就续租约，不判结果不明：
    # 代录一张单可以合法地超过租约（ADR-021 11-02 修订）。来领的这台自己手上的已在 `_redeliver` 里处理。
    # 按投递记录挂的连接（来领的密钥那条）找：Express 记录就是这条连接；MR.ERP 代录单在别的连接上、投递仍挂在这里（第 3 步）
    rows = frappe.db.sql('''SELECT r.name, d.owner_id, r.written_by_device FROM `tabCowork Push Record` r
        INNER JOIN `tabCowork Companion Delivery` d ON d.push_attempt=r.active_attempt
        WHERE d.connection=%s AND r.status='leased' AND r.lease_expires_at<=%s
        ORDER BY r.created_at,r.name''', (credential.connection, ledger.now_utc(now)), as_dict=True)
    for item in rows:
        writer = item.written_by_device or ''
        if writer == device:
            continue
        row = frappe.get_doc(ledger.RECORD, item.name)
        adapter = _adapter(row.connection, item.owner_id)
        mrerp = row.connection != credential.connection
        try:
            reservation = _row_context(row, credential, writer)
        except frappe.PermissionError:
            if not mrerp:
                raise
            continue  # 已不归这台电脑的 MR.ERP 代录单（不该发生）：不动，由它自己的密钥收拾
        if writer and request_service.presence(credential.connection, writer)['seen']:
            ledger.extend(row.name, context=reservation, adapter=adapter, now=now)
            if mrerp:
                mrerp_jobs.take(row, writer)  # 电脑在场：账号租约随领单续期
        else:
            ledger.recover_expired(row.name, context=reservation, adapter=adapter, now=now)


def _redeliver(credential, device, request, now):
    """这台电脑手上丢了的单重发给它自己（ADR-021 11-02 修订，同 ERP 通道 `cc_desktop.jobs`）：

    - 它还「领取中」却又来领单：写账线单线程、做完才再领，来领就说明手上那件丢了（CC 被结束 / 重启、看门狗重启写账线、回执
      没送到）；
    - 它领过、过期时它不在（别的电脑领单时转成了结果不明）、人还没核对的。

    写者锁一直由这条记录持有（同账套别的单照样等它），电脑本机按 ``task_key`` 记着写没写过（代录按批次文件读账套），写过的回上次
    单号、不写第二张。重发次数满了、或这条已不能再交给它（失权、目标变了），领取中的转人工 + 结果不明，留给人核对。"""
    if not request_contracts.supports(_dispatch_adapter(credential, device, '').version,
                                      queue_contracts.TASK_KEY_MIN_COMPANION):
        return []  # 认不出重发的同一条记录的旧版：不重发，照旧到期转结果不明（兼容）
    reach = account_pool.writable_reach(credential.connection, device)
    rows = frappe.db.sql('''SELECT r.name, r.account_key, d.owner_id FROM `tabCowork Push Record` r
        INNER JOIN `tabCowork Companion Delivery` d ON d.push_attempt=r.active_attempt
        WHERE d.connection=%s AND r.written_by_device=%s
          AND (r.status='leased' OR (r.status='manual' AND r.result_unknown=1 AND r.reason=%s))
        ORDER BY r.status='leased' DESC, r.created_at, r.name''',
        (credential.connection, device, ledger.UNKNOWN_REASON), as_dict=True)
    for item in rows:
        row = frappe.get_doc(ledger.RECORD, item.name)
        lost = _adapter(row.connection, item.owner_id)
        mrerp = row.connection != credential.connection
        key = normalize_express_account_key(row.account_key)
        if not mrerp and key not in reach:
            continue  # 这会儿够不到这份账套（网络盘没连上）：先不动，连上后再重发；这台也不领别的（`_busy`）
        if mrerp and not mrerp_jobs.take(row, device):
            continue  # MR.ERP 代录单：账号租约过期后被直写 / 别的批次占了，先不重发，放开后再发（ADR-026 第 8 条）
        claim = None
        try:
            reservation = _row_context(row, credential, device)
        except frappe.PermissionError:
            if not mrerp:
                raise
            continue
        if ledger.redeliverable(row, device):
            try:
                claim = ledger.lease(row.name, context=reservation,
                                     adapter=_dispatch_adapter(credential, device, row.write_route or '',
                                                               connection=row.connection),
                                     now=now, redeliver=True)
            except (frappe.ValidationError, frappe.PermissionError):
                claim = None  # 不能再交给它（失权、目标变了、锁对不上）：下面按丢了处理
        if claim is None:
            if row.status == 'leased':
                ledger.recover_expired(row.name, context=reservation, adapter=lost, now=now, lost=True)
            continue
        job = route_contracts.Job(row.name, key, row.entered_by or row.actor, 1, row.route_device or '')
        peer = _peers(credential.connection, device, request, now).get(device)
        if mrerp:
            return [_deliver(credential, claim, None, '', job._replace(target=operator_target.TARGET_MRERP), peer,
                             connection=row.connection)]
        uid = _account(account_pool.registrations_of(credential.connection, [key]).get(key)).uid
        return [_deliver(credential, claim, reach[key], uid, job, peer)]
    return []


def _dispatch_adapter(credential, device, route, *, connection=None):
    found = frappe.db.get_value(devices.DOCTYPE, device, ['device_owner', 'companion_version'], as_dict=True) \
        if device != credential.credential_id else None
    owner, version = (found.device_owner or '', found.companion_version or '') if found else ('', '')
    return _adapter(connection or credential.connection, delivery_store.owner(credential), device=device,
                    device_owner=owner, route=route, version=version)


def _deliver(credential, claim, spelling, uid, job, peer, *, connection=None):
    """领到的一件交给这台电脑：载荷换成它对这份账套的写法（与这份账套登记的文件号 ``uid``）、建投递记录、带上稳定键（ADR-021
    11-02 修订）与录入人可能在不在这台电脑前。MR.ERP 代录单（``connection`` 是它的 MR.ERP 连接）没有账套位置，载荷原样交。"""
    if spelling is None:
        record = dict(claim['record'])
    else:
        record = {**claim['record'], 'frozen_payload': wire_location.localize(
            claim['record']['frozen_payload'], path=spelling.path, root=spelling.root, row=spelling.registry_row,
            uid=uid)}
    delivery = delivery_store.create(credential=credential, claim=claim, **({'connection': connection} if connection else {}))
    # 自动推不弹「账套被占用」（第 3 期第 5 条）
    automated = isinstance((claim.get('target') or {}).get('automation'), dict)
    attended = route_contracts.attended(job._replace(automated=automated), peer)
    return _value(queue_contracts.job, delivery.name, record, attended, queue_contracts.task_key(claim['record']['name']))


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
                                           index_pending=peer.get('index_pending'), targets=peer.get('targets'))
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
    found = frappe.db.get_value(devices.DOCTYPE, device, ['device_owner', 'companion_version'], as_dict=True) \
        if device != credential.credential_id else None
    owner, version = (found.device_owner or '', found.companion_version or '') if found else ('', '')
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
                           device_owner=owner or '', route=decision.route, version=version)
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
        return [_deliver(credential, claim, reach[key], account.uid, job, peers.get(device))]
    return []


def _claim_mrerp(credential, device, request, now):
    """这台电脑这个工作空间里派给它的 MR.ERP 代录单（ADR-026 第 5 条，第 3 步）：只派给发起人选定的这台、它报过 ``mrerp``、载荷版本够
    （`route_contracts.decide` 的目标分支）；不会这个目标的不领、单子等它；报了目标版本不够转人工（同 Express「没有一台支持」的口径）。
    领前按「网址 + 用户名」租这个 MR.ERP 账号（`mrerp_jobs.take`），租不到不发、照旧排着。"""
    if not operator_target.MRERP_OPERATOR_ENABLED:
        return []
    heads = mrerp_jobs.heads(credential, device)
    if not heads:
        return []
    peers = _peers(credential.connection, device, request, now)
    clock = ledger.now_utc(now)
    for head in heads:
        job = route_contracts.Job(head.name, head.account_key, head.entered_by or head.actor,
                                  max(1, int(head.payload_version or 1)), head.route_device or '',
                                  target=operator_target.TARGET_MRERP)
        decision = route_contracts.decide(device, job, [], peers, route_contracts.Account(), clock)
        if not decision.take and decision.reason != route_contracts.NEEDS_UPDATE:
            continue  # 不是这台的 / 这台还不会代录 MR.ERP：等它
        row = frappe.get_doc(ledger.RECORD, head.name)
        reservation = mrerp_jobs.context(row, credential, device)
        adapter = _dispatch_adapter(credential, device, decision.route, connection=row.connection)
        if not decision.take:
            ledger.quarantine_pending(row.name, context=reservation, adapter=adapter, reason='payload_version_outdated')
            continue
        if ledger.lock_busy(row.name, context=reservation):
            continue
        point = 'cowork_mrerp_claim_' + uuid4().hex
        frappe.db.savepoint(point)
        try:
            claim = ledger.lease(row.name, context=reservation, adapter=adapter, now=now)
        except ledger.LeaseUnavailable:
            frappe.db.rollback(save_point=point)
            continue
        except (ledger.DispatchRejected, frappe.PermissionError) as error:
            frappe.db.rollback(save_point=point)
            reason = error.code if isinstance(error, ledger.DispatchRejected) else 'companion.queue_access_revoked'
            ledger.quarantine_pending(row.name, context=reservation, adapter=adapter, reason=reason)
            continue
        if not claim['leased']:
            continue
        if not mrerp_jobs.take(row, device):
            frappe.db.rollback(save_point=point)  # 账号被直写 / 别的批次占着：这张不发，照旧排着（ADR-026 第 8 条）
            continue
        return [_deliver(credential, claim, None, '', job, peers.get(device), connection=row.connection)]
    return []


def lease(context, body):
    """Claim at most one job for the polling computer (lease v2, ADR-021)."""
    request = _value(queue_contracts.lease_request, body)
    with _atomic():
        credential = credentials.revalidate(context)
        now = datetime.now(timezone.utc)
        device = credentials.device_of(credential.credential_id) or credential.credential_id
        jobs = _redeliver(credential, device, request, now)
        _recover(credential, now, device)
        if not jobs:
            jobs = [] if _busy(device, now) else (_claim(credential, device, request, now)
                                                  or _claim_mrerp(credential, device, request, now))
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
        device = credentials.device_of(delivery.credential_id) or delivery.credential_id
        result = ledger.ack(row.name, context=_row_context(row, context, device),
            adapter=_adapter(row.connection, delivery.owner_id), fence=int(delivery.fence),
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
    if row.connection != getattr(delivery, 'connection', row.connection) and mrerp_jobs.is_job(row):
        # MR.ERP 代录单：这批再没有排着 / 领着的单就放开账号（ADR-026 第 8 条）；没有 Express 账套登记要记
        if result.get('status') != 'leased':
            mrerp_jobs.release_if_done(row, device)
        return
    key = normalize_express_account_key(row.account_key)
    if result.get('status') == 'success':
        account_pool.note_written(row.connection, key, device, ledger.now_utc())
    elif (result.get('status') == 'pending'
          and error_code(request.get('error')) == queue_contracts.ERR_ACCOUNT_UID_MISMATCH):
        if account_pool.mark_stale(row.connection, device, key):
            credentials.request_full_report(delivery.credential_id)
