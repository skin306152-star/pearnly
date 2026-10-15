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

from pearnly_cowork.connections import access
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


def _reporter(doc):
    """The key whose report is this connection's current one, while it is still current; otherwise the original slot.

    With one computer both are the same key, as before. Online state and the confirmation proof follow the report,
    which is what the preparation evidence was taken from."""
    name = doc.get('report_credential')
    if name:
        rows = frappe.db.sql('''SELECT name FROM `tabCowork Express Agent Credential`
            WHERE name=%s AND connection=%s AND revoked_at IS NULL AND COALESCE(active_key,'')<>'' FOR UPDATE''',
            (name, doc.name))
        if rows:
            return _read(DOCTYPE, rows[0][0])
    return _active(doc)


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


def _online(doc, row, now=None):
    """助手实际连上：**当前这把**有效密钥最近 3 分钟内报过到。只生成了密钥、旧密钥报的、
    时间缺失或读不出，一律算没连上（旧版同样“拿不准一律判离线”）。

    报到时间记在密钥这一小行上（`record_seen`），不在连接的上报记录里：心跳每分钟一次，
    记在上报记录里会让整份（几百 KB）每分钟重写一遍、全量进数据库日志。"""
    if row is None or row.revoked_at or not row.active_key:
        return False
    if (doc.get('report_credential') != row.name
            or int(doc.get('report_generation') or 0) != int(row.token_generation)):
        return False
    seen = getattr(row, 'last_seen_at', None)
    if int(getattr(row, 'last_seen_generation', None) or 0) != int(row.token_generation):
        return False
    if isinstance(seen, str):
        try:
            seen = datetime.fromisoformat(seen)
        except ValueError:
            return False
    if not isinstance(seen, datetime):
        return False
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)  # 库里存的是 UTC 无时区
    age = ((now or datetime.now(timezone.utc)) - seen).total_seconds()
    return 0 <= age < ONLINE_SECONDS


def _fresh(seen, seen_generation, token_generation, now=None):
    """这把密钥最近 3 分钟内报过到（同 `_online` 的判据，不看它是不是连接的上报凭据）。"""
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


def pool_peers(connection, now=None):
    """池里各台电脑此刻的情况（目录刷新分派用，ADR-021）：``{设备: {"user", "always_on", "online"}}``。
    ``user`` 是这台电脑上最近登录 CC 的人（不是登记人）；任一把有效密钥 3 分钟内报过到就算在线。只读。"""
    rows = frappe.db.sql(f'''SELECT k.device, k.token_generation, k.last_seen_at, k.last_seen_generation,
        d.signed_in_user, d.always_on FROM `tab{DOCTYPE}` k JOIN `tab{devices.DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>'' AND d.retired_at IS NULL''',
        (connection,), as_dict=True)
    peers = {}
    for row in rows:
        peer = peers.setdefault(row.device, {'user': row.signed_in_user, 'always_on': bool(row.always_on),
                                             'online': False})
        peer['online'] = peer['online'] or _fresh(row.last_seen_at, row.last_seen_generation, row.token_generation, now)
    return peers


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
            'online': _online(doc, row)}


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
    只看本站那一份归属下启用的连接，网页上每种只用一条。"""
    rows = frappe.db.sql('''SELECT name, adapter FROM `tabCowork ERP Connection`
        WHERE workspace=%s AND COALESCE(client,'')='' AND enabled=1 AND binding_generation=0
        AND adapter IN %s ORDER BY creation, name''', (access.default_workspace(), HELPER_ADAPTERS), as_dict=True)
    order = ('express',) if express else ('mrerp', 'express')
    for adapter in order:
        found = next((row.name for row in rows if row.adapter == adapter), None)
        if found:
            return found
    return None


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


def online(connection):
    """录入入口（LINE 里选目标）看 Express 能不能选：本人能用这条连接、助手当前在线。不要求管理权限。"""
    doc = _connection(connection)
    access.require_connection(doc)
    return _online(doc, _reporter(doc))


def for_confirmation(connection):
    """Internal human submission proof; no token disclosure or machine login."""
    _enabled()
    doc = _connection(connection)
    access.require_connection(doc)
    row = _reporter(doc)
    if row is None:
        _error('companion.credential_not_found')
    _issuer(doc, row)
    return {'credential_id': row.name, 'generation': int(row.token_generation), 'issuer': row.issuer}


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
