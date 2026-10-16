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

from pearnly_cowork.connections import access, pool
from . import contracts, device_contracts, devices
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
# 它的配对码只能调用文件夹的几个机器方法（`http_auth.FOLDER_METHODS`）、按需请求与认电脑（`REQUEST_METHODS`、
# `DEVICE_METHODS`），心跳 / 领任务 / 回执一律拒绝。
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


def _management(name):
    actor = access.require_actor(management=True)
    doc = _connection(name)
    access.require_connection(doc, actor=actor, management=True, enabled=False)
    return doc, actor


def _use(name):
    """Issuing a key for one's own computer (D1, ADR-021): entry or management role plus the explicit grant to use
    this enabled connection. Creating the connection (pool) itself stays management-only (`connections.service`)."""
    actor = access.require_actor()
    doc = _connection(name)
    access.require_connection(doc, actor=actor)
    return doc, actor


def _manager(actor):
    try:
        access.require_actor(actor, management=True)
    except frappe.PermissionError:
        frappe.clear_messages()
        return False
    return True


def _active(doc):
    """The key in the original per-connection slot (keys issued before computers were known, and old CC Desktop)."""
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


def _fresh(seen, seen_generation, token_generation, now=None):
    """这把密钥最近 3 分钟内报过到（旧线上 `erp-agent-liveness.ts` 同一判据）。时间缺失、读不出、换钥前的报到一律算没连上
    （旧版同样「拿不准一律判离线」）。报到时间记在密钥这一小行上（`record_seen`），不在连接的上报记录里。

    多台电脑后不再要求它是连接当前的上报凭据（ADR-021）：池里每台电脑各看自己的密钥（`pool_peers`）。"""
    if int(seen_generation or 0) != int(token_generation or 0):
        return False
    if isinstance(seen, str):
        try:
            seen = datetime.fromisoformat(seen)
        except ValueError:
            return False
    if not isinstance(seen, datetime):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = ((now or datetime.now(timezone.utc)) - seen).total_seconds()
    return 0 <= age < ONLINE_SECONDS


def device_of(credential_id):
    """这把密钥挂在哪台电脑上（补丁之前旧路径发的、补丁漏掉的没有电脑：None）。"""
    return frappe.db.get_value(DOCTYPE, credential_id, 'device') or None


def device_id(credential_id):
    """池里认这把密钥的电脑用的编号：挂了电脑就是电脑，没挂的按密钥编号算一台（同 :func:`pool_peers`）。"""
    return device_of(credential_id) or credential_id


def pool_peers(connection, now=None):
    """池里各台电脑此刻的情况（ADR-021：目录刷新与写账分派、`connections.pool.presence`、`pool.state`）：
    ``{设备: {"user", "always_on", "online", "version", "payload_version", "index_pending", "issuers", "label"}}``。

    ``user`` 是这台电脑上最近登录 CC 的人（不是登记人）；任一把有效密钥 3 分钟内报过到就算在线；``payload_version`` 没报过
    是 None（旧版，不拿它拦）；``index_pending`` 是它主档索引还没重建的账套文件号，旧版不报是 None。补丁之前旧路径发、
    还没挂电脑的密钥按密钥编号算一台（它上报的可达行也记在这个编号下）。只读。"""
    rows = frappe.db.sql(f'''SELECT k.name, k.device, k.issuer, k.token_generation, k.last_seen_at, k.last_seen_generation,
        d.signed_in_user, d.always_on, d.companion_version, d.max_payload_version, d.index_pending_uids, d.label
        FROM `tab{DOCTYPE}` k LEFT JOIN `tab{devices.DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>''
        AND (k.device IS NULL OR k.device='' OR d.retired_at IS NULL)''', (connection,), as_dict=True)
    peers = {}
    for row in rows:
        payload = int(row.max_payload_version or 0)
        peer = peers.setdefault(row.device or row.name, {
            'user': row.signed_in_user or '', 'always_on': bool(row.always_on), 'online': False,
            'version': str(row.companion_version or ''), 'payload_version': payload if payload >= 1 else None,
            'index_pending': devices.index_pending(row.index_pending_uids), 'issuers': set(),
            'label': str(row.label or '')})
        peer['online'] = peer['online'] or _fresh(row.last_seen_at, row.last_seen_generation, row.token_generation, now)
        peer['issuers'].add(row.issuer)
    return peers


def issuer_valid(connection_doc, issuer):
    """签发人此刻仍能让机器用这条连接（录入或管理 + 使用名单，同 `_issuer`）；不抛错、不加锁（`connections.access.usable_by`，
    池里「能写的电脑」也按它核）。"""
    return access.usable_by(connection_doc, issuer)


def request_full_report(credential_id):
    """要这把密钥的电脑下一次心跳整份重报账套名单（写前文件号对不上之后，可达行标了 stale，等整份名单才恢复）。
    只给 Express 密钥打。密钥行由调用方锁好。"""
    row = frappe.db.get_value(DOCTYPE, credential_id, ['connection', 'full_report_due'], as_dict=True)
    if not row or row.full_report_due or frappe.db.get_value(CONNECTION, row.connection, 'adapter') != 'express':
        return False
    frappe.db.set_value(DOCTYPE, credential_id, 'full_report_due', 1, update_modified=False)
    return True


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
            'online': bool(row.active_key and not row.revoked_at
                           and _fresh(row.get('last_seen_at'), row.get('last_seen_generation'), row.token_generation))}


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


def _new_key(doc, actor, operation, intent, device=None):
    """Insert one key; the generation still increments per connection. ``device`` None = the original slot."""
    last = _latest(doc)
    next_generation = _value(contracts.generation, int(last.token_generation) + 1 if last else 1)
    token = 'exp_' + doc.name + '_' + secrets.token_urlsafe(32)
    row = frappe.get_doc({'doctype': DOCTYPE, 'record_id': 'CWEAC-' + operation[:48],
        'site_name': _site(), 'connection': doc.name, 'workspace': doc.workspace, 'device': device,
        'client': doc.client or '', 'issuer': actor, 'token_generation': next_generation,
        'branch': 'legacy', 'binding_generation': 0,
        'token_digest': contracts.token_digest(token), 'token_tail': token[-4:],
        'active_key': contracts.active_identity(_site(), doc.name, device),
        'generation_key': contracts.generation_identity(_site(), doc.name, next_generation),
        'issued_request_digest': operation, 'issued_at': _now(),
        'request_receipts': _value(contracts.append_receipt, {}, operation, intent, 'created')})
    persist(row)
    return row, token


def issue(connection, request_key, reset=False, expected_generation=None):
    """The original per-connection slot (old CC Desktop without a device key; `register` and lab scripts, no RPC).

    Once a computer of the pool has been claimed (new CC Desktop) the slot is closed to old clients: ``update_needed``.
    Checked only after the connection and its key rows are locked, so a claim committed meanwhile is seen and an old
    client can never displace a new one. Each new old-slot key gets an unclaimed "old computer", as the patch does."""
    _enabled()
    intent = _value(contracts.issue_intent, reset, expected_generation)
    with _atomic():
        doc, actor = _use(connection)
        operation = _value(contracts.operation_identity, _site(), actor, doc.name, request_key)
        old = _find_receipt(doc, operation, intent)
        if old:
            return {**_dto(doc, old), 'status': 'exists', 'replayed': True}
        current = _active(doc)
        if current:
            _row_scope(current, doc)
        _latest(doc)  # the generation row `_new_key` takes: every key row is locked before any computer row
        if devices.pool_claimed(doc.name):
            return {'status': 'update_needed', 'replayed': False}
        if not reset and current:
            _remember(current, operation, intent, 'exists')
            return {**_dto(doc, current), 'status': 'exists', 'replayed': False}
        if reset and (not current or int(current.token_generation) != intent['expected_generation']):
            _error('companion.generation_conflict')
        # Taking the slot from another computer stays with management; the entry role may replace only its own key.
        if reset and current.issuer != actor and not _manager(actor):
            access.deny()
        if current:
            _revoke(current, actor, 'reset')
            devices.after_revoke(current, actor)
        row, token = _new_key(doc, actor, operation, intent)
        devices.for_old_slot(row, label=doc.label or doc.name)
        return {**_dto(doc, row), 'status': 'created', 'replayed': False, 'token': token}


def _device_connection(express):
    """这台电脑该连哪条连接：装了 Express 的只连 Express（本站还没有就回 None，由 `register` 新建），绝不退到
    MR.ERP——退过去这台电脑就写不了 Express、定时任务也用不了（2026-10-13 录制：testcowork 只剩 MR.ERP 连接时，
    装了 Express 的新笔记本连成了 MR.ERP）；没装的连 MR.ERP（只读文件夹给定时任务），没有 MR.ERP 再退回 Express。
    每种 ERP 只认主连接（`connections.service.main_connections`：最早建、启用、不挂客户），与网页、LINE、CC 同一条。"""
    from pearnly_cowork.connections import service

    found = service.main_connections(access.default_workspace())
    order = ('express',) if express else ('mrerp', 'express')
    return next((found[adapter] for adapter in order if found.get(adapter)), None)


def register(request_key, express=False, replace=False, expected_generation=None,
             device_key=None, device_name=None, rekey=False):
    """CC 桌面端用登录的账号连上这台电脑（2026-10-10 起取代手抄配对码，旧配对码照常有效）。

    权限（D1，ADR-021）：录入或管理角色、且在这条连接的可用名单里，就能给**自己的**电脑发密钥，签发人 = 本人，
    本人失去权限密钥即失效；本站还没有连接时只有管理员能建（非管理员回 `no_connection`）。

    带设备密钥（新版 CC 桌面版）：每台电脑在池里各有自己的密钥，彼此不顶替（`_register_device`）。
    不带（旧版）：池里还没有电脑认领过时，照旧一条连接一个槽位——已有电脑连着时不抢，回 `in_use`，用户确认
    「改用这台电脑」后带上那把密钥的代数再来（`replace`），旧电脑立即断开；池里已有认领过的电脑就回
    `update_needed`，旧版不能再顶掉新版。
    """
    _enabled()
    actor = access.require_actor()
    express, replace, rekey = (contracts.wire_flag(v) in (True, 1) for v in (express, replace, rekey))
    # 同一归属下串行：两台电脑同时登录不会各建一条 Express 连接
    frappe.db.get_value('Cowork Workspace', access.default_workspace(), 'name', for_update=True)
    name = _device_connection(express)
    if name is None:
        if not express or not _manager(actor):
            return {'status': 'no_connection'}
        from pearnly_cowork.connections import model, service
        try:
            name = service.configure(None, 'express', request_key, 'Express', {})['name']
        except model.ConnectionError as error:
            _error(error.code)
    adapter = frappe.db.get_value(CONNECTION, name, 'adapter')
    if device_key not in (None, ''):
        return _register_device(name, adapter, actor, request_key, device_key, device_name, rekey)
    result = issue(name, request_key, replace, expected_generation if replace else None)
    if result.get('status') == 'update_needed':
        return {'status': 'update_needed', 'connection': name, 'adapter': adapter}
    if result.get('token'):
        return {'status': 'created', 'connection': name, 'adapter': adapter, 'token': result['token']}
    return {'status': 'in_use', 'connection': name, 'adapter': adapter, 'generation': result['generation'],
            'online': result['online'], 'issuer': result['issuer'], 'self': result['issuer'] == actor}


def _register_device(name, adapter, actor, request_key, device_key, device_name, rekey):
    """register v2：这台电脑在池里的一把密钥，同一事务里认电脑、发 / 轮换它自己的密钥，从不吊销别的电脑。

    - 新电脑：建电脑（登记人 = 本人）并发密钥 → `created`；
    - 这台已有有效密钥：只记最近登录的人 → `exists`，不改登记人、不吊销（全所共用一台、各人用自己账号登录）；
    - 同上且 `rekey`（本机已没有这家的成员，比如重装过），本人是登记人或管理员：吊销旧的、发新的 → `created`；
    - 电脑被移除过：旧记录交出摘要，这台作为新电脑加入。
    加锁顺序 Connection → Credential → Device：先不加锁地按摘要找电脑，锁完这条连接上要动的密钥行，最后才锁电脑行
    并复核；电脑行在这期间被并发改了（退役、另一个成员刚建好同一台）就 ``devices.busy()``，整段重来。
    没装 Express 的电脑由 `_device_connection` 挂到 MR.ERP 主连接，没有就挂 Express 池当只读文件夹成员；
    不论哪种都不会顶掉写账的电脑。
    """
    key = _value(contracts.device_key, device_key)
    label = _value(contracts.text, device_name, maximum=140, optional=True) if device_name is not None else ''
    digest = contracts.device_digest(_site(), key)
    with _atomic():
        doc, actor = _use(name)
        operation = _value(contracts.operation_identity, _site(), actor, doc.name, request_key)
        found, stale = devices.find(digest), None  # plain read: no computer row is locked yet
        if found and devices.retired(found):
            stale, found = found, None
        current = None
        if found:
            intent = _value(contracts.device_issue_intent, found, rekey)
            old = _find_receipt(doc, operation, intent)
            if old:
                return {'status': 'exists', 'connection': doc.name, 'adapter': adapter, 'device': found,
                        'generation': int(old.token_generation), 'replayed': True}
            found_key = devices.current_key(doc.name, found)
            current = _read(DOCTYPE, found_key) if found_key else None
            if current is not None:
                _row_scope(current, doc)
        _latest(doc)  # the generation row `_new_key` takes
        if stale:
            devices.release(devices.read(stale))
        if found:
            device = devices.read(found)  # lock order Connection -> Credential -> Device
            if device.secret_digest != digest or device.retired_at:
                raise devices.busy()
            devices.note_signed_in(device, actor)
            if current is not None and not rekey:
                return {'status': 'exists', 'connection': doc.name, 'adapter': adapter, 'device': device.name,
                        'generation': int(current.token_generation)}
            if current is not None and actor != device.device_owner and not _manager(actor):
                access.deny()
        else:
            device = devices.create_claimed(workspace=doc.workspace, owner=actor, label=label, secret_digest=digest,
                                            signed_in_user=actor)
            intent = _value(contracts.device_issue_intent, device.name, rekey)
        if current is not None:
            _revoke(current, actor, 'rekey')
        row, token = _new_key(doc, actor, operation, intent, device=device.name)
        return {'status': 'created', 'connection': doc.name, 'adapter': adapter, 'device': device.name,
                'token': token}


def manages(actor):
    """这个人是不是管理员（不抛错）：「电脑」列表据此给 ``can_remove``（登记人或管理员才能移除一台电脑）。"""
    return _manager(actor)


def remove_device(device):
    """「电脑」列表的「···」移除一台电脑（ADR-021 第 6 批）：吊销它在各条连接上的每一把密钥，再让电脑退役。

    权限同 ``rekey``：这台电脑的登记人或管理员；别的归属的电脑、不存在的编号一律按无权拒（不说有没有）。已经移除过的
    回 ``removed``（``replayed``），重复点、两人同时点都只移除一次。退役后它的密钥不再认（认证核「电脑没退役」），可达行
    随退役删掉，它读的定时任务与公司文件夹留着、标「电脑已移除」等负责人重选（不自动换电脑读）。同一台电脑以后再登录
    CC，对话页按设备密钥 `register` 时旧记录交出摘要，这台作为新电脑加入。

    加锁顺序 Connection → Credential → Device：一台电脑可以在好几条连接上各有密钥、各有可达行（第 2 批记录：「移除一台
    够得到多条连接的电脑时要先锁那些连接」），先不加锁地找出这些连接，按名字顺序逐条锁，再锁它的密钥行，最后才锁电脑行
    并复核；电脑行在这期间被并发改了（另一个人刚移除）就按已移除回。"""
    _enabled()
    actor = access.require_actor()
    name = _value(contracts.text, device)
    with _atomic():
        found = frappe.db.get_value(devices.DOCTYPE, name, ['workspace', 'device_owner', 'retired_at'], as_dict=True)
        if not found or found.workspace != access.default_workspace():
            access.deny()
        if actor != found.device_owner and not _manager(actor):
            access.deny()
        if found.retired_at:
            return {'status': 'removed', 'device': name, 'replayed': True}
        connections = sorted({row[0] for row in frappe.db.sql(f'''SELECT connection FROM `tab{DOCTYPE}`
            WHERE device=%s AND revoked_at IS NULL AND COALESCE(active_key,'')<>''
            ''', (name,)) if row[0]}
            | set(pool.device_connections(name)))
        for connection in connections:
            frappe.db.get_value(CONNECTION, connection, 'name', for_update=True)
        keys = frappe.db.sql(f'''SELECT name FROM `tab{DOCTYPE}` WHERE device=%s AND revoked_at IS NULL
            ORDER BY name FOR UPDATE''', (name,))
        for (key,) in keys:
            _revoke(_read(DOCTYPE, key), actor, 'device_removed')
        doc = devices.read(name)  # lock order Connection -> Credential -> Device
        if doc.retired_at:
            return {'status': 'removed', 'device': name, 'replayed': True}
        devices.retire(doc, actor)
    return {'status': 'removed', 'device': name, 'replayed': False}


def revoke(connection, request_key, expected_generation, reason=''):
    """Management-only, internal (lab and operations scripts); no RPC since ADR-021. Original slot first."""
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
        devices.after_revoke(row, actor)
        _remember(row, operation, intent, 'revoked')
        return {**_dto(doc, row), 'status': 'revoked', 'replayed': False}


def status(connection):
    """Management-only, internal (lab and operations scripts); no RPC since ADR-021. Original slot first."""
    doc, _ = _management(connection)
    return _dto(doc, _active(doc) or _latest(doc))


def _issuer(doc, row):
    _row_scope(row, doc)
    if (row.revoked_at or not contracts.active_matches(row.active_key, _site(), doc.name, row.get('device'))
            or int(row.token_generation or 0) < 1):
        _unauthorized()
    if row.get('device') and devices.retired(row.device):
        _unauthorized()
    # Removing the issuer's entry/management role or the explicit use grant invalidates machine access
    # immediately; no cached roles or connection list is used. D1 (ADR-021): an entry-role issuer is enough.
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
        # Several computers share a connection: find the key by its own digest (unique), then require it to
        # belong to the connection named in the token. Lock order stays Connection -> Credential.
        rows = frappe.db.sql('''SELECT name FROM `tabCowork Express Agent Credential`
            WHERE token_digest=%s FOR UPDATE''', (parsed.digest,))
        if not rows:
            _unauthorized()
        row = frappe.get_doc(DOCTYPE, rows[0][0], for_update=True)
        if row.connection != doc.name or not contracts.token_matches(row.token_digest, parsed.digest):
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


def machine_key(context):
    """Device methods: the authenticated key itself, current and locked (Connection -> Credential)."""
    context = revalidate(context)
    return frappe.get_doc(DOCTYPE, context.credential_id, for_update=True)


def hello(context, values):
    """device_hello (ADR-021): attach the authenticated key to its computer (`devices.hello`). When that computer
    already holds its own key on this connection the old-slot key is redundant: it is revoked here, like `leave`."""
    key = machine_key(context)
    result = devices.hello(context.site, key, values)
    if result['status'] == device_contracts.SUPERSEDED:
        _revoke(key, key.issuer, 'superseded')
        # 这台电脑自己的密钥接手：旧电脑在这条连接上的可达行与本机登记转给它（同 `devices.hello` 改挂），再退役旧电脑
        devices.after_revoke(key, key.issuer, successor=result['device'])
    return result


def leave(context):
    """device_leave: this computer disconnects; only this key is revoked. A delivery it already holds can still be
    acknowledged (`receipt_auth`). An unclaimed old computer left without any key is retired with it."""
    row = machine_key(context)
    _revoke(row, row.issuer, 'device_leave')
    devices.after_revoke(row, row.issuer)


def full_report_due(credential_id, *, full_report):
    """After a key is newly attached to its computer the next heartbeat reply omits ``account_sets_unchanged_ok`` so
    the computer reports its whole list again. Cleared by the first full report. The key row is already locked."""
    if not frappe.db.get_value(DOCTYPE, credential_id, 'full_report_due'):
        return False
    if full_report:
        frappe.db.set_value(DOCTYPE, credential_id, 'full_report_due', 0, update_modified=False)
        return False
    return True
