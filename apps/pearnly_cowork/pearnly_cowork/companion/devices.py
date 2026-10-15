"""CC 桌面版的电脑（ADR-021）：一台电脑（准确说是一台电脑上的一个 Windows 用户）是连接这个「池」里的一个成员。

这里只管电脑这一行：按设备密钥的摘要找、建、认领、记录最近登录的人、退役；密钥本身在 `credentials`。
设备密钥只用来把同一台电脑的几把密钥归成一台、证明「同一台重连」；机器身份仍只来自 `exp_` 密钥。
加锁顺序 Connection → Credential → Device：调用方已按顺序锁好前两者，这里的电脑行总是最后锁。没有事务提交、没有 RPC。
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

import frappe

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
    doc = read(name)
    if not doc.secret_digest:
        retire(doc, actor)


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
