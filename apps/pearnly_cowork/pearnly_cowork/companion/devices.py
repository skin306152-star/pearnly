"""CC 桌面版的电脑（ADR-021）：一台电脑（准确说是一台电脑上的一个 Windows 用户）是连接这个「池」里的一个成员。

这里只管电脑这一行：按设备密钥的摘要找、建、认领、记录最近登录的人、退役；密钥本身在 `credentials`。
设备密钥只用来把同一台电脑的几把密钥归成一台、证明「同一台重连」；机器身份仍只来自 `exp_` 密钥。
加锁顺序 Connection → Credential → Device：调用方已按顺序锁好前两者，这里的电脑行总是最后锁。没有事务提交、没有 RPC。
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

import frappe

from pearnly_cowork.connections import pool
from pearnly_cowork.connections.member import Member
from pearnly_cowork.connections.reports import MAPPING_KEYS, SELECTED_KEYS
from pearnly_cowork.targets.identity import host_aliases
from . import contracts, device_contracts
from .device_documents import DOCTYPE, persist
from .documents import persist as persist_key

CREDENTIAL = 'Cowork Express Agent Credential'
CONNECTION = 'Cowork ERP Connection'
_DUPLICATE = (frappe.DuplicateEntryError, frappe.UniqueValidationError)


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def find(digest, *, lock=False):
    rows = frappe.db.sql(f'SELECT name FROM `tab{DOCTYPE}` WHERE secret_digest=%s'
                         + (' FOR UPDATE' if lock else ''), (digest,))
    return rows[0][0] if rows else None


def read(name, *, lock=True):
    return frappe.get_doc(DOCTYPE, name, for_update=lock)


def retired(name):
    """密钥认证时用：这台电脑被移除了，它的密钥一律不再认。"""
    return bool(frappe.db.get_value(DOCTYPE, name, 'retired_at'))


def create(*, workspace, owner, label='', secret_digest=None, signed_in_user=None):
    doc = frappe.get_doc({'doctype': DOCTYPE, 'workspace': workspace, 'device_owner': owner,
                          'signed_in_user': signed_in_user, 'label': (label or '')[:device_contracts.MAX_NAME],
                          'secret_digest': secret_digest or None})
    persist(doc)
    return doc


def note_signed_in(doc, user):
    """这台电脑上最近登录 CC 的账号（只管写账路由）；不改登记人，不吊销任何密钥。"""
    if doc.signed_in_user != user:
        doc.signed_in_user = user
        persist(doc)


def release(doc):
    """被移除的电脑交出设备密钥摘要：同一台电脑再登录时作为新电脑加入，旧记录留作历史。"""
    if doc.retired_at and doc.secret_digest:
        doc.secret_digest = None
        persist(doc)


def retire(doc, actor):
    if not doc.retired_at:
        doc.retired_at, doc.retired_by = _now(), actor
        persist(doc)
        # 它够得到的账套不再算进池目录（下一次发布时），待认定的别的电脑也不再等它（ADR-021，方案 2.4 第 5 条）。
        # 经 `retire_orphan` 来的，可达行在锁电脑行之前已经删了，这里什么都不删
        pool.forget_device(doc.name)


def owners(names):
    """这几台电脑的登记人 ``{电脑: 登记人}``（不加锁；没退役的；不是电脑记录的编号——没挂电脑的旧槽位密钥——不在里面）。"""
    names = [name for name in dict.fromkeys(names or ()) if name]
    if not names:
        return {}
    return {row[0]: row[1] or '' for row in frappe.db.sql(
        f'SELECT name, device_owner FROM `tab{DOCTYPE}` WHERE name IN %s AND retired_at IS NULL', (tuple(names),))}


def labels(names):
    """几台电脑的电脑名 ``{电脑: 名}``（钩子 ``cowork_device_labels``：推送记录「由哪台电脑写入」，ADR-021 第 6 批）。
    退役的也给（写的时候它还在），没名字的不给。只读。"""
    names = [name for name in dict.fromkeys(names or ()) if name]
    if not names:
        return {}
    return {row[0]: row[1] for row in frappe.db.sql(f'SELECT name, label FROM `tab{DOCTYPE}` WHERE name IN %s',
                                                    (tuple(names),)) if row[1]}


def pool_claimed(connection):
    """池里已有电脑认领过（新版 CC 桌面版报过设备密钥）：旧版不带设备密钥的登记不能再顶掉它。

    加锁读（读到最新提交的认领）：调用方已锁好连接和这条连接上要动的密钥，这里最后才碰电脑行。"""
    return bool(frappe.db.sql(f'''SELECT k.name FROM `tab{CREDENTIAL}` k JOIN `tab{DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>''
        AND COALESCE(d.secret_digest,'')<>'' AND d.retired_at IS NULL LIMIT 1 FOR UPDATE''', (connection,)))


def current_key(connection, device, *, exclude=None):
    """这台电脑在这条连接上的有效密钥（最多一把）。"""
    rows = frappe.db.sql(f'''SELECT name FROM `tab{CREDENTIAL}` WHERE connection=%s AND device=%s AND name<>%s
        AND revoked_at IS NULL AND COALESCE(active_key,'')<>'' ORDER BY token_generation DESC FOR UPDATE''',
        (connection, device, exclude or ''))
    return rows[0][0] if rows else None


def _keys_of(device):
    return frappe.db.sql(f'''SELECT name FROM `tab{CREDENTIAL}` WHERE device=%s AND revoked_at IS NULL
        AND COALESCE(active_key,'')<>'' LIMIT 1''', (device,))


def retire_orphan(name, actor):
    """密钥吊销之后调用（密钥行已锁好）：还没被认领的旧电脑（补丁或旧路径建的，没有摘要）一把有效密钥都不剩，
    就再也没人能认领它，退役，免得「电脑」列表里留一台永远离线的假电脑。认领过的电脑留着：同一台再登录还是它。"""
    if not name or _keys_of(name):
        return
    # 一把有效密钥都不剩的电脑够不到任何账套：池目录（下一次发布时）、读请求的分派、别的电脑的待认定都不再算它
    # （ADR-021，方案 2.4 第 5 条）。池里各行在电脑行之前锁。
    pool.forget_device(name)
    doc = read(name)
    if not doc.secret_digest:
        retire(doc, actor)


def _move(connection, old, new):
    """同一台电脑的旧记录 ``old`` 在这条连接上的东西转给 ``new``：池里的可达行与本机登记（`connections.pool.move_device`），
    还有按电脑记的读文件夹任务、在途文件、公司文件夹（钩子 ``cowork_reader_device_moved``，文件夹域不由本域导入）。
    调用方已锁连接与密钥行，电脑行还没锁（加锁顺序）。"""
    pool.move_device(connection, old, new)
    for path in frappe.get_hooks('cowork_reader_device_moved') or []:
        frappe.get_attr(path)(connection, old, new)


def after_revoke(key, actor, *, successor=None):
    """一把密钥吊销之后（密钥行已锁好）：这台电脑在这条连接上一把有效密钥都不剩，它在这条连接上的可达行删掉——不然目录
    还显示它够得到、可写，只有它够得到的账套的读请求永远排队；再按 :func:`retire_orphan` 处理没人认领的旧电脑。
    ``successor``：同一台电脑在这条连接上接手的那台记录（`device_hello` 发现它已有自己的密钥）：可达行与本机登记先转给它。"""
    device = key.get('device')
    if not device:
        return
    if successor:
        _move(key.connection, device, successor)
    if not current_key(key.connection, device):
        pool.forget_device(device, connection=key.connection)
    retire_orphan(device, actor)


def for_old_slot(key, *, label):
    """旧路径（不带设备密钥的旧版）新发的旧槽位密钥：同补丁一样给它建一台「旧电脑」，登记人 = 签发人，摘要留空等
    首次 `device_hello` 认领。`active_key` 不动（仍是旧槽位公式）。密钥行是调用方刚插入、已锁住的。"""
    device = create(workspace=key.workspace, owner=key.issuer, label=label)
    key.device = device.name
    persist_key(key)
    return device


def _saved(write):
    """唯一摘要撞上并发的同一台电脑（两个成员同时报到）：退回到保存点，返回 False。"""
    point = 'cowork_device_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        write()
        return True
    except _DUPLICATE:
        frappe.db.rollback(save_point=point)
        frappe.clear_messages()
        return False


def busy():
    """读计划之后电脑行被并发改了（认领、退役、撞唯一摘要）：整段本地事务回滚重来（调用方对死锁做最多三次完整重试，
    重来时是新快照）。此时还没有任何东西离开服务器。"""
    return frappe.QueryDeadlockError()


def create_claimed(*, workspace, owner, label, secret_digest, signed_in_user=None):
    """建一台已认领的电脑；同一台电脑的另一个成员刚刚并发建好（撞唯一摘要）→ ``busy()``，重来时按已有那台处理。"""
    made = []
    if not _saved(lambda: made.append(create(workspace=workspace, owner=owner, label=label, secret_digest=secret_digest,
                                             signed_in_user=signed_in_user))):
        raise busy()
    return made[0]


def _plain(name):
    return frappe.db.get_value(DOCTYPE, name, ['secret_digest', 'retired_at', 'workspace'], as_dict=True)


def hello(site, key, values):
    """device_hello：把这把已认证、已锁住的密钥 ``key`` 挂到报上来的电脑上。从不改挂按电脑发的密钥、从不改公式。

    - 这把密钥的电脑已被认领、设备密钥对不上（同一配对码抄到了两台电脑）→ ``claimed_elsewhere``，什么都不改；
    - 电脑是还没被认领的「旧电脑」→ 由它认领（记下摘要）；同一台电脑的另一个成员先认领过 → 改挂到那台，
      空出来的旧电脑退役；
    - 那台电脑在这条连接上已经有自己的密钥（新版先 register、后 hello）→ ``superseded``：这把旧槽位密钥多余，
      由调用方吊销（`credentials.hello`），不在这里改；
    - 没有电脑（补丁之前旧路径发、补丁漏掉的）→ 建一台，登记人 = 这把密钥的签发人。
    新挂上的 Express 密钥，下一次心跳回复不带 ``account_sets_unchanged_ok``，逼电脑整份重报。

    加锁顺序 Connection → Credential → Device：先不加锁读出计划，再锁密钥行，最后才锁电脑行并复核计划；
    复核不符（并发认领 / 退役 / 撞唯一摘要）抛 ``busy()``，整段重来。
    """
    digest = contracts.device_digest(site, values['device_key'])
    old_slot = key.active_key == contracts.active_identity(site, key.connection)
    if not key.device and not old_slot:
        return {'status': device_contracts.CLAIMED_ELSEWHERE}
    # 1. 计划：只做不加锁的读
    current = _plain(key.device) if key.device else None
    stale = None
    if current and current.secret_digest:
        if current.secret_digest != digest:
            return {'status': device_contracts.CLAIMED_ELSEWHERE}
        target = key.device
    else:
        target = find(digest)
        if target and retired(target):
            stale, target = target, None
    # 2. 密钥行：一台电脑在一条连接上只留一把；只有迁移来的旧槽位密钥能改挂，按电脑发的生来就在自己的电脑上
    if target and target != key.device:
        if not old_slot or (_plain(target) or {}).get('workspace') != key.workspace:
            return {'status': device_contracts.CLAIMED_ELSEWHERE}
        if current_key(key.connection, target, exclude=key.name):
            return {'status': device_contracts.SUPERSEDED, 'device': target}
    # 池里各行（连接域）在电脑行之前：迁移来的旧槽位密钥改挂到同一台电脑已认领的记录上，旧电脑在这条连接上的可达行、
    # 只属于它的本机登记一并转过去，旧电脑才退役（不然那台第一次整份上报时，同一份账套会被登记出第二个规范键）
    if target and key.device and target != key.device:
        _move(key.connection, key.device, target)
    elif target and not key.device:
        # 没挂电脑的密钥在池里按密钥编号算一台（`credentials.device_id`）：按这个编号接手的任务、在途文件、可达行
        # 一并转给它挂上的电脑（第 4 批审查 L6）
        _move(key.connection, key.name, target)
    # 3. 电脑行，最后锁，并复核第 1 步读到的
    if stale:
        release(read(stale))
    if target:
        doc = read(target)
        if doc.secret_digest != digest or doc.retired_at:
            raise busy()
    elif current is not None:
        doc = read(key.device)
        if doc.secret_digest or doc.retired_at:
            raise busy()
        doc.secret_digest = digest
        if not _saved(lambda: persist(doc)):
            raise busy()
    else:
        doc = create_claimed(workspace=key.workspace, owner=key.issuer, label=values['name'], secret_digest=digest)
        # 同上：没挂电脑的密钥按密钥编号记的东西转给新建的这台（新行刚插入，别的事务锁不到它，不违反加锁顺序）
        _move(key.connection, key.device or key.name, doc.name)
    moved = not (current and current.secret_digest)
    if doc.name != key.device:
        previous, key.device = key.device, doc.name
        persist_key(key)
        retire_orphan(previous, key.issuer)
    doc.label = values['name'] or doc.label
    doc.always_on = 1 if values['always_on'] else 0
    doc.host_names = json.dumps(values['host_names'], ensure_ascii=False)  # 原样存，不筛（第 0 批：含 WSL / VPN 地址）
    persist(doc)
    # 只有 Express 密钥发心跳；MR.ERP 的只读文件夹密钥打上也永远清不掉
    if moved and not key.full_report_due and frappe.db.get_value(CONNECTION, key.connection, 'adapter') == 'express':
        key.full_report_due = 1
        persist_key(key)
    return {'status': device_contracts.ATTACHED, 'device': doc.name}


def seed_legacy(folder_name=None):
    """补丁 `v0_0_19_companion_devices` 第 1 步（方案 4.1）：每把有效、还没挂电脑的密钥建一台「旧电脑」。

    登记人 = 签发人；名字先取心跳报的电脑名（`report_json.agent_device_name`），再取文件夹同步报的
    （``folder_name(连接)``），都没有就用连接名。设备密钥摘要留空，等首次 `device_hello` 认领。
    **`active_key` 一律不动**：现有密钥原样有效，服务端回退也认。只处理还没挂电脑的，重跑不重复建。返回建了几台。
    """
    rows = frappe.db.sql(f'''SELECT k.name, k.connection, k.workspace, k.issuer, c.report_json, c.label
        FROM `tab{CREDENTIAL}` k JOIN `tabCowork ERP Connection` c ON c.name = k.connection
        WHERE k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>'' AND COALESCE(k.device,'')=''
        ORDER BY k.creation, k.name''', as_dict=True)
    for row in rows:
        try:
            reported = json.loads(row.report_json or '{}')
        except ValueError:
            reported = {}
        name = str((reported.get('agent_device_name') if isinstance(reported, dict) else '') or '').strip()
        if not name and folder_name is not None:
            name = str(folder_name(row.connection) or '').strip()
        device = create(workspace=row.workspace, owner=row.issuer, label=name or row.label or row.connection)
        frappe.db.set_value(CREDENTIAL, row.name, 'device', device.name, update_modified=False)
    return len(rows)


# ── 心跳里的这台电脑（第 2 批，方案 2.3）────────────────────────────────────────────

_LOOPBACK = frozenset(("localhost", "127.0.0.1", "::1", "[::1]"))


def _names(value):
    try:
        names = json.loads(value or '[]')
    except (TypeError, ValueError):
        return []
    return [str(name).strip().lower() for name in names if isinstance(name, str) and str(name).strip()
            and str(name).strip().lower() not in _LOOPBACK] if isinstance(names, list) else []


def _host_label(device, hosts):
    """这台电脑的主机名（新登记的、只在本机的账套键写成 ``\\\\主机\\盘$\\…``）：报的第一个名字；同一归属下另一台
    电脑报了同一个名字时加设备短号；还没报过（旧版）用设备短号。"""
    names = hosts.get(device) or []
    short = str(device or '')[-6:].lower()
    if not names:
        return 'pc-' + short
    if any((others or [None])[0] == names[0] for other, others in hosts.items() if other != device):
        return names[0] + '-' + short
    return names[0]


def _row(device):
    if not device:
        return None
    return frappe.db.get_value(DOCTYPE, device, ['name', 'workspace', 'secret_digest', 'label', 'host_names',
                                                 'companion_version', 'max_payload_version', 'selection_json',
                                                 'retired_at'], as_dict=True)


def _selections(row):
    """电脑行上的所选，按连接分开存：``{连接: {所选账套与 6 个科目映射}}``（一台电脑可以在几条连接上各挂一把密钥，
    erpcowork 就有两条启用的 Express 连接：混存的话两条的心跳每分钟互改）。"""
    try:
        found = json.loads((row.selection_json if row else None) or '{}')
    except (TypeError, ValueError):
        found = {}
    return {str(connection): {key: value for key, value in values.items() if key in SELECTED_KEYS | MAPPING_KEYS}
            for connection, values in (found.items() if isinstance(found, dict) else ()) if isinstance(values, dict)}


def state(device, connection):
    """这台电脑在这条连接上上次报的所选账套、6 个科目映射与版本（心跳的 ``previous``，每台、每条连接各自一份，
    不再读连接配置）。"""
    row = _row(device)
    if not row:
        return {}
    result = dict(_selections(row).get(connection) or {})
    if row.companion_version:
        result['companion_version'] = row.companion_version
    if int(row.max_payload_version or 0) >= 1:
        result['max_payload_version'] = int(row.max_payload_version)
    return result


def member(device, workspace, *, connection, peers=None, version=None, payload_version=None, pool_hosts=True):
    """连接域眼里的这台电脑（`connections.member.Member`）：设备编号、是不是旧版、主机名与全池的主机别名、版本、
    在这条连接上上次的所选。只做不加锁的读（电脑行在加锁顺序的最后，`record_report` 才锁）。``pool_hosts=False``（领任务，每 3 秒
    一次）不读全池的主机名：只有认账套（心跳带名单）才用得上。"""
    row = _row(device)
    known = state(device, connection)
    hosts = {}
    if workspace and pool_hosts:
        for item in frappe.db.sql(f'''SELECT name, host_names FROM `tab{DOCTYPE}`
                WHERE workspace=%s AND retired_at IS NULL''', (workspace,), as_dict=True):
            hosts[item.name] = _names(item.host_names)
    if row and device not in hosts:
        hosts[device] = _names(row.host_names)
    return Member(device=device, legacy=not (row and row.secret_digest), host=_host_label(device, hosts),
                  aliases=host_aliases(hosts), version=str(version or known.get('companion_version') or ''),
                  payload_version=payload_version if payload_version is not None
                  else known.get('max_payload_version'),
                  selected=str(known.get('account_set') or known.get('account_dir') or ''), peers=peers)


def record_report(device, connection, plan):
    """心跳之后记到这台电脑上：这条连接上的所选账套与 6 个科目映射（只是它自己的，不再写连接配置）、版本、旧版报的
    电脑名。内容没变不碰电脑行；变了才锁（加锁顺序的最后一位）。"""
    row = _row(device)
    if not row or row.retired_at or plan.offline:
        return False
    selections = _selections(row)
    selection = selections.get(connection) or {}
    updated = {**selection, **{key: value for key, value in plan.patch.items() if key in SELECTED_KEYS | MAPPING_KEYS}}
    values = {}
    if updated != selection:
        values['selection_json'] = json.dumps({**selections, connection: updated}, ensure_ascii=False, sort_keys=True)
    if plan.patch.get('companion_version') and plan.patch['companion_version'] != (row.companion_version or ''):
        values['companion_version'] = str(plan.patch['companion_version'])[:40]
    if plan.patch.get('max_payload_version') and int(plan.patch['max_payload_version']) != int(
            row.max_payload_version or 0):
        values['max_payload_version'] = int(plan.patch['max_payload_version'])
    name = str(plan.patch.get('agent_device_name') or '').strip()
    if name and not row.secret_digest and name != (row.label or ''):
        values['label'] = name[:device_contracts.MAX_NAME]  # 新版的电脑名由 device_hello 报
    if not values:
        return False
    doc = read(device)
    for key, value in values.items():
        setattr(doc, key, value)
    persist(doc)
    return True


def reporter_label(connection):
    """这条连接上最近报到的那台电脑的名字（网页「连接」里显示，第 4 批改成每台电脑一行）；没有回空串。连接行上的
    「上报凭据」第 3 批起不再随上报改（两台轮流上报不来回重写连接行），按各把密钥自己的报到时间取。"""
    rows = frappe.db.sql(f'''SELECT d.label FROM `tab{CREDENTIAL}` k JOIN `tab{DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>'' AND d.retired_at IS NULL
        AND k.last_seen_at IS NOT NULL ORDER BY k.last_seen_at DESC LIMIT 1''', (connection,))
    return str(rows[0][0] or '') if rows else ''


# 领取请求里报的「主档索引还没重建的账套文件号」最多记这么多个（`queue_contracts.MAX_INDEX_PENDING`）
def index_pending(value):
    """电脑行上存的「主档索引还没重建的账套文件号」：没报过（旧版）是 None，报过空的是空集合。"""
    if value in (None, ''):
        return None
    try:
        found = json.loads(value)
    except (TypeError, ValueError):
        return None
    return frozenset(str(item) for item in found if isinstance(item, str) and item) if isinstance(found, list) else None


def note_index_pending(device, uids):
    """领取请求带来的「主档索引还没重建的账套」（1.7.0 起，`index_pending`）记到这台电脑上，别的电脑领取时据此让着它
    （按账套黏住，方案 2.5）。没带（旧版）不动；内容没变不碰电脑行；变了才锁（加锁顺序的最后一位）。"""
    if uids is None:
        return False
    row = frappe.db.get_value(DOCTYPE, device, ['name', 'index_pending_uids', 'retired_at'], as_dict=True) if device else None
    if not row or row.retired_at:
        return False
    encoded = json.dumps(sorted(set(uids)), ensure_ascii=False)
    if (row.index_pending_uids or '') == encoded:
        return False
    doc = read(device)
    doc.index_pending_uids = encoded
    doc.index_pending = 1 if uids else 0
    persist(doc)
    return True


def newest_version(connection):
    """池里（这条连接上有有效密钥、没退役的电脑）最新的 CC 桌面版版本；没报过回空串。"""
    rows = frappe.db.sql(f'''SELECT d.companion_version FROM `tab{CREDENTIAL}` k JOIN `tab{DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>'' AND d.retired_at IS NULL''',
        (connection,))
    versions = [str(row[0]).strip() for row in rows if row and str(row[0] or '').strip()]

    def order(value):
        try:
            return tuple(int(part) for part in value.split('.'))
        except ValueError:
            return (0,)
    return max(versions, key=order) if versions else ''


def seed_state(device, connection, config, report):
    """补丁 `v0_0_20_express_account_sets`：旧电脑接过这条连接的配置里它当时选的账套与 6 个科目映射、上报记录里它的版本
    （从此每台电脑、每条连接各存各的，连接配置不再被心跳改）。这条连接上已经有的不覆盖；重跑不重复写。"""
    row = _row(device)
    if not row or row.retired_at:
        return False
    values = {}
    selection = {key: value for key, value in (config or {}).items()
                 if key in SELECTED_KEYS | MAPPING_KEYS and value not in (None, '')}
    selections = _selections(row)
    if selection and connection not in selections:
        values['selection_json'] = json.dumps({**selections, connection: selection}, ensure_ascii=False,
                                              sort_keys=True)
    if not row.companion_version and str((report or {}).get('companion_version') or '').strip():
        values['companion_version'] = str(report['companion_version']).strip()[:40]
    try:
        payload = int((report or {}).get('max_payload_version') or 0)
    except (TypeError, ValueError):
        payload = 0
    if not int(row.max_payload_version or 0) and payload >= 1:
        values['max_payload_version'] = payload
    if not values:
        return False
    doc = read(device)
    for key, value in values.items():
        setattr(doc, key, value)
    persist(doc)
    return True
