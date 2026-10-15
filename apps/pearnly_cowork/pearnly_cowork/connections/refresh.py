"""Local Express refresh leases; a wire request ID is a single-lease handle.

No external call, commit, retry or reverse dependency on Companion. The caller
holds Connection then its authenticated Credential throughout this transaction.

Several computers share an Express connection (ADR-021, plan 2.7). A computer is an
opaque device string handed in by Companion (`member.Member`):

- an account-set read is leased to one computer that reaches that account set,
  the requester's own computer first, then an always-on one, then any other; a
  lower tier takes it only when no better one is online or after
  :data:`HANDOFF_SECONDS`;
- a whole-directory read (``@endpoint``) gives each computer that reports within
  :data:`JOIN_SECONDS` its own child lease (``parent_request``); every child that
  comes back refreshes that computer's reach and republishes the union directory.
  The request completes when every computer of the pool has come back, or the join
  window is over and every child that joined is done; an expired child is not a
  failure (that computer keeps its previous reach);
- a lease belongs to its computer (``lease_device``): another computer's report
  never fails it, only the same computer reporting with another key does.
"""
from datetime import timedelta
from uuid import uuid4

import frappe
from frappe import _
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import access, model, pool, state

LEASE_SECONDS = 900
# 整目录读：请求后（第一台电脑领到起）这么久内来报到的电脑各领一份子租约
JOIN_SECONDS = 120
# 账套级读：更合适的电脑在线时先留给它，排了这么久还没人领就谁够得到谁读（同写账分派，方案 2.5）
HANDOFF_SECONDS = 120
# 读这台电脑上任一份账套（不只是它当前选的那份）的完整主档：科目表、商品、客户、供应商、部门、存货科目组。
# 按单个账套读（`projection_refresh.collect_account`）从 1.2.3 起读完整目录，供应商与部门从 1.3.0 起才报；
# 更早的 CC 桌面端只把它选的那份交给它读，其他账套的请求留着、照实告诉用户要升级（`account_masters`）。
ANY_ACCOUNT_SET_MIN_VERSION = "1.3.0"


def version_tuple(value):
    try:
        return tuple(int(part) for part in str(value or "").strip().split("."))
    except ValueError:
        return (0,)


def reads_any_account_set(version):
    """这个版本的 CC 桌面端能读它当前选的账套以外的账套。"""
    return bool(str(version or "").strip()) and version_tuple(version) >= version_tuple(ANY_ACCOUNT_SET_MIN_VERSION)


def tier(actor, peer):
    """这台电脑读这份请求的优先档：0 = 登录它的就是发起人，1 = 常驻电脑，2 = 其他。"""
    peer = peer or {}
    if actor and peer.get("user") == actor:
        return 0
    return 1 if peer.get("always_on") else 2


def my_turn(device, actor, reachers, peers, waited_seconds):
    """纯函数：``device`` 现在能不能领这份账套级读。``reachers`` 是够得到它的电脑，``peers`` 是
    ``{设备: {"user", "always_on", "online"}}``。在线的够得到它的电脑里，最好的一档轮到；排了
    :data:`HANDOFF_SECONDS` 还没人领就谁都行。来领的这台本身就在线。"""
    if device not in reachers:
        return False
    if waited_seconds >= HANDOFF_SECONDS:
        return True
    peers = peers or {}
    online = [item for item in reachers if item == device or (peers.get(item) or {}).get("online")]
    best = min(tier(actor, peers.get(item)) for item in online)
    return tier(actor, peers.get(device)) <= best


def _rows(doc, statuses=("requested", "leased")):
    placeholders = ','.join(['%s'] * len(statuses))
    names = frappe.db.sql(f'''SELECT name FROM `tabCowork ERP Refresh`
        WHERE connection=%s AND status IN ({placeholders}) ORDER BY requested_at,name FOR UPDATE''',
        (doc.name, *statuses))
    return [state.read(state.REFRESH, row[0]) for row in names]


def _children(parent):
    """这一轮（父请求开始之后）的子租约。"""
    names = frappe.db.sql('''SELECT name FROM `tabCowork ERP Refresh`
        WHERE parent_request=%s ORDER BY requested_at,name FOR UPDATE''', (parent.name,))
    rows = [state.read(state.REFRESH, row[0]) for row in names]
    return [row for row in rows if parent.started_at and row.started_at and row.started_at >= parent.started_at]


def _is_parent(row):
    """整目录读的父请求：从不自己出租约，由各台电脑的子租约完成。"""
    return (row.scope_kind == 'endpoint' and not row.get('parent_request') and not row.get('lease_handle'))


def _clear_lease(row):
    row.lease_handle = None
    row.lease_owner = None
    row.lease_generation = None
    row.lease_expires_at = None


def _head_status(doc, row, status, error=None):
    heads = state.heads(doc)
    _, _, key = state.scope(None if row.scope_kind == 'endpoint' else row.scope_key, doc.adapter)
    if heads.get(key, {}).get('request_id') == row.name:
        heads[key] = {**heads[key], 'status': status, 'error_code': error}
        doc.projection_heads = model.dumps(heads)
        access.write(doc)


def fail(doc, row, code, now):
    row.status, row.error_code, row.completed_at = 'failed', code[:200], now
    row.result_revision = None
    _clear_lease(row)
    access.write(row)
    _head_status(doc, row, 'error', code)
    if row.get('parent_request'):
        settle(doc, row.parent_request, now)


def _requeue(doc, row):
    row.status, row.started_at, row.completed_at, row.error_code = 'requested', None, None, None
    row.result_revision = None
    _clear_lease(row)
    access.write(row)
    _head_status(doc, row, 'pending')


def settle(doc, parent, now):
    """整目录读：子租约都交回（或到期）后，看这一轮能不能结束（见模块说明）。总是重读父请求：同一事务里它可能
    刚被另一份子租约的结束改过，不拿旧的那份去写。"""
    parent = state.read(state.REFRESH, parent if isinstance(parent, str) else parent.name)
    if parent.status != 'leased' or not _is_parent(parent):
        return
    children = _children(parent)
    if not children or any(row.status == 'leased' for row in children):
        return
    joined = {row.lease_device for row in children}
    if not (pool.devices(doc.name) <= joined or now >= parent.started_at + timedelta(seconds=JOIN_SECONDS)):
        return
    if parent.requested_at > parent.started_at:
        _requeue(doc, parent)
        return
    done = [row for row in children if row.status == 'succeeded']
    if not done:
        # 电脑报上来的原因优先于「租约到期」（到期只说明那台没交回）
        errors = sorted({row.error_code for row in children if row.error_code},
                        key=lambda code: (code == 'ERR_REFRESH_LEASE_EXPIRED', code))
        fail(doc, parent, errors[0] if errors else 'ERR_REFRESH_LEASE_EXPIRED', now)
        return
    _, _, key = state.scope(None)
    parent.status, parent.completed_at, parent.error_code = 'succeeded', now, None
    parent.result_revision = (state.heads(doc).get(key) or {}).get('revision') or max(
        int(row.result_revision or 0) for row in done)
    access.write(parent)
    _head_status(doc, parent, 'fresh')


def expire(doc, now, *, credential_id=None, token_generation=None, device=None):
    for listed in _rows(doc):
        # 一份子租约结束会顺带结束它的父请求（`settle`）：逐行重读，不拿循环开始时的旧值去写
        row = state.read(state.REFRESH, listed.name)
        if row.status not in ('requested', 'leased'):
            continue
        if (row.connection_revision != doc.record_revision or row.binding_generation != doc.binding_generation):
            fail(doc, row, 'ERR_CONNECTION_CHANGED', now)
        elif row.status == 'leased' and _is_parent(row):
            settle(doc, row, now)
        elif row.status == 'leased':
            owner = row.get('lease_device')
            if (credential_id is not None and ((owner and owner == device) or not owner)
                    and (row.lease_owner != credential_id or row.lease_generation != token_generation)):
                # Rotation must not let a newly issued key finish an old lease. A lease held by another computer
                # of the pool is not this report's business (ADR-021).
                fail(doc, row, 'ERR_AGENT_CREDENTIAL_CHANGED', now)
            elif not row.lease_expires_at or row.lease_expires_at <= now:
                if row.get('parent_request'):
                    fail(doc, row, 'ERR_REFRESH_LEASE_EXPIRED', now)  # that computer keeps its previous reach
                elif row.started_at and row.requested_at > row.started_at:
                    _requeue(doc, row)
                else:
                    fail(doc, row, 'ERR_REFRESH_LEASE_EXPIRED', now)


def supersede(doc, row, now):
    """A new request replaces this one. Children of a whole-directory read keep their leases and may still report;
    each of them only refreshes its own computer's reach."""
    if row.status == 'leased':
        fail(doc, row, 'ERR_REFRESH_SUPERSEDED', now)


def validate_completion(doc, completion, credential_id, token_generation, now, *, device=None,
                        canonical=normalize_express_account_key):
    """``canonical`` turns the computer's own spelling of the account set back into the canonical key."""
    if completion is None:
        return None
    handle = completion.get('request_id')
    if not isinstance(handle, str) or not handle or len(handle) > 140:
        frappe.throw(_('connection.refresh_stale'))
    rows = frappe.db.sql('''SELECT name FROM `tabCowork ERP Refresh`
        WHERE connection=%s AND lease_handle=%s FOR UPDATE''', (doc.name, handle))
    if len(rows) != 1:
        frappe.throw(_('connection.refresh_stale'))
    row = state.read(state.REFRESH, rows[0][0])
    key = '@endpoint' if completion.get('scope') == 'endpoint' else canonical(completion.get('account_set'))
    if (row.status != 'leased' or row.adapter != 'express' or row.account_set_key != key
            or row.scope_kind != completion.get('scope')
            or row.connection_revision != doc.record_revision or row.binding_generation != doc.binding_generation
            or row.lease_owner != credential_id or row.lease_generation != token_generation
            or (row.get('lease_device') and row.lease_device != device)
            or not row.lease_expires_at or row.lease_expires_at <= now):
        frappe.throw(_('connection.refresh_stale'))
    return row


def finish(doc, row, *, revision=None, error=None, now):
    if row is None:
        return
    if row.get('parent_request'):
        if error:
            fail(doc, row, str(error), now)
        elif revision is not None:
            row.status, row.completed_at, row.error_code = 'succeeded', now, None
            row.result_revision = revision
            _clear_lease(row)
            access.write(row)
            settle(doc, row.parent_request, now)
        return
    if row.requested_at > row.started_at:
        _requeue(doc, row)
    elif error:
        fail(doc, row, str(error), now)
    elif revision is not None:
        row.status, row.completed_at, row.error_code = 'succeeded', now, None
        row.result_revision = revision
        _clear_lease(row)
        access.write(row)
        _head_status(doc, row, 'fresh')
    # A valid heartbeat that omits the requested component is not completion.


def _held(row, member, credential_id):
    return row.status == 'leased' and (row.get('lease_device') == member.device
                                       or (not row.get('lease_device') and row.lease_owner == credential_id))


def _take(row, member, credential_id, token_generation, now):
    row.status, row.started_at = 'leased', now
    row.lease_handle = str(uuid4())
    row.lease_owner, row.lease_generation = credential_id, token_generation
    row.lease_device = member.device
    row.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
    row.completed_at, row.error_code, row.result_revision = None, None, None
    access.write(row)
    return row


def _child(doc, parent, member, credential_id, token_generation, now):
    if parent.status == 'requested':
        parent.status, parent.started_at = 'leased', now
        parent.completed_at, parent.error_code, parent.result_revision = None, None, None
        access.write(parent)
    row = frappe.get_doc({"doctype": state.REFRESH, "record_key": str(uuid4()), "connection": doc.name,
        "workspace": doc.workspace, "client": doc.client or "", "actor": parent.actor,
        "connection_revision": parent.connection_revision, "binding_generation": parent.binding_generation,
        "adapter": parent.adapter, "scope_kind": "endpoint", "scope_key": parent.scope_key,
        "account_set_key": "@endpoint", "request_hash": parent.request_hash, "status": "requested",
        "requested_at": parent.requested_at, "result_revision": 0, "parent_request": parent.name})
    return _take(row, member, credential_id, token_generation, now)


def lease(doc, member, selected, credential_id, token_generation, now):
    """Hand at most one requested read to this computer (one lease per computer at a time).

    ``member`` is this computer (`member.Member`); ``selected`` is the account set this heartbeat (or, for the
    poll lease, the computer's last report) has selected, as the computer spells it. The selected account set and
    the endpoint directory are read by every CC Desktop; any other account set only by one that reads any account
    set (`reads_any_account_set`) and reaches it, in turn (:func:`my_turn`). A request for an account set no
    computer of the pool reaches fails instead of waiting forever.
    """
    expire(doc, now, credential_id=credential_id, token_generation=token_generation, device=member.device)
    rows = _rows(doc)
    if not rows or any(_held(row, member, credential_id) for row in rows):
        return None  # 平时没有读的请求：不再查池（每台电脑每 3 秒领一次任务）
    mine = pool.local_paths(doc.name, member.device)
    selected = pool.canonical(doc.name, member.device, selected) if selected else ""
    capable = reads_any_account_set(member.version)
    peers, found = None, []
    for row in rows:
        if row.adapter != 'express' or row.get('parent_request'):
            continue
        if row.scope_kind == 'endpoint':
            if row.status == 'requested' or (row.status == 'leased' and _is_parent(row)):
                if row.status == 'leased' and (
                        now >= row.started_at + timedelta(seconds=JOIN_SECONDS)
                        or any(child.lease_device == member.device for child in _children(row))):
                    continue
                found.append((0, row))
            continue
        if row.status != 'requested':
            continue
        if row.account_set_key == selected:
            found.append((1, row))
            continue
        if not capable:
            continue
        reachers = pool.reachers(doc.name, row.account_set_key)
        if not reachers:
            if pool.any_reach(doc.name):
                fail(doc, row, 'ERR_ACCOUNT_SET_UNAVAILABLE', now)
            continue  # no directory yet: the endpoint read comes first, then this one
        if member.device not in reachers:
            continue
        if peers is None:
            peers = member.peers() if callable(member.peers) else {}
        if not my_turn(member.device, row.actor, reachers, peers, (now - row.requested_at).total_seconds()):
            continue
        found.append((2, row))
    if not found:
        return None
    found.sort(key=lambda item: (item[0], item[1].requested_at, item[1].name))
    rank, row = found[0]
    if row.scope_kind == 'endpoint':
        child = _child(doc, row, member, credential_id, token_generation, now)
        return {'request_id': child.lease_handle, 'account_set_key': child.account_set_key, 'scope_kind': 'endpoint'}
    row = _take(row, member, credential_id, token_generation, now)
    # 交给电脑的是它自己的写法（同一份账套在各台电脑上的路径可能不同）；旧版的规范键就是它的路径
    wire_key = normalize_express_account_key(mine.get(row.account_set_key)) or row.account_set_key
    return {'request_id': row.lease_handle, 'account_set_key': wire_key, 'scope_kind': row.scope_kind}


def create(doc, kind, key, head_key, *, actor, identity, signature, now):
    """Record one requested read and point its scope head at it (caller holds the Connection lock)."""
    heads = state.heads(doc)
    previous = heads.get(head_key, {})
    row = frappe.get_doc({"doctype": state.REFRESH, "record_key": identity, "connection": doc.name,
        "workspace": doc.workspace, "client": doc.client or "", "actor": actor,
        "connection_revision": doc.record_revision, "binding_generation": doc.binding_generation,
        "adapter": doc.adapter, "scope_kind": kind, "scope_key": key,
        "account_set_key": "@endpoint" if kind == "endpoint" else key,
        "request_hash": signature, "status": "requested", "requested_at": now, "result_revision": 0})
    access.write(row)
    heads[head_key] = {**previous, "request_id": identity, "status": "pending", "error_code": None}
    doc.projection_heads = model.dumps(heads)
    return row
