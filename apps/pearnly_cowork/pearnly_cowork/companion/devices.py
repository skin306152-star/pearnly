"""CC 桌面版的电脑（ADR-021）：一台电脑（准确说是一台电脑上的一个 Windows 用户）是连接这个「池」里的一个成员。

这里只管电脑这一行：按设备密钥的摘要找、建、认领、记录最近登录的人、退役；密钥本身在 `credentials`。
设备密钥只用来把同一台电脑的几把密钥归成一台、证明「同一台重连」；机器身份仍只来自 `exp_` 密钥。
加锁顺序 Connection → Credential → Device：调用方已按顺序锁好前两者。没有事务提交、没有 RPC。
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

import frappe

from . import contracts, device_contracts
from .device_documents import DOCTYPE, persist
from .documents import persist as persist_key

CREDENTIAL = 'Cowork Express Agent Credential'
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
    """池里已有电脑认领过（新版 CC 桌面版报过设备密钥）：旧版不带设备密钥的登记不能再顶掉它。"""
    return bool(frappe.db.sql(f'''SELECT k.name FROM `tab{CREDENTIAL}` k JOIN `tab{DOCTYPE}` d ON d.name = k.device
        WHERE k.connection=%s AND k.revoked_at IS NULL AND COALESCE(k.active_key,'')<>''
        AND COALESCE(d.secret_digest,'')<>'' AND d.retired_at IS NULL LIMIT 1''', (connection,)))


def current_key(connection, device, *, exclude=None):
    """这台电脑在这条连接上的有效密钥（最多一把）。"""
    rows = frappe.db.sql(f'''SELECT name FROM `tab{CREDENTIAL}` WHERE connection=%s AND device=%s AND name<>%s
        AND revoked_at IS NULL AND COALESCE(active_key,'')<>'' ORDER BY token_generation DESC FOR UPDATE''',
        (connection, device, exclude or ''))
    return rows[0][0] if rows else None


def _keys_of(device):
    return frappe.db.sql(f'''SELECT name FROM `tab{CREDENTIAL}` WHERE device=%s AND revoked_at IS NULL
        AND COALESCE(active_key,'')<>'' LIMIT 1''', (device,))


def _saved(write):
    """唯一摘要撞上并发的同一台电脑（两个成员同时报到）：退回到保存点，由调用方按已有的那台重来。"""
    point = 'cowork_device_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        write()
        return True
    except _DUPLICATE:
        frappe.db.rollback(save_point=point)
        frappe.clear_messages()
        return False


def hello(site, key, values):
    """device_hello：把这把已认证、已锁住的密钥 ``key`` 挂到报上来的电脑上。**永远不改 `active_key`。**

    - 这把密钥的电脑已被认领、设备密钥对不上（同一配对码抄到了两台电脑）→ ``claimed_elsewhere``，什么都不改；
    - 电脑是补丁建的「旧电脑」、还没被认领 → 由它认领（记下摘要）；同一台电脑的另一个成员先认领过 → 改挂到那台，
      空出来的旧电脑退役；
    - 没有电脑（补丁之后旧路径新发的密钥）→ 建一台，登记人 = 这把密钥的签发人。
    新挂上的，下一次心跳回复不带 ``account_sets_unchanged_ok``，逼电脑整份重报。
    """
    digest = contracts.device_digest(site, values['device_key'])
    current = read(key.device) if key.device else None
    if current is None and key.active_key != contracts.active_identity(site, key.connection):
        return {'status': device_contracts.CLAIMED_ELSEWHERE}
    target, moved = current, False
    if not (current and current.secret_digest):
        target = None
        for _ in range(2):
            found = find(digest, lock=True)
            target = read(found) if found else None
            if target and target.retired_at:
                release(target)
                target = None
            if target is not None:
                break
            if current is not None:
                current.secret_digest = digest
                if _saved(lambda: persist(current)):
                    current.reload()
                    target = current
                    break
                current.reload()
            else:
                made = []
                if _saved(lambda: made.append(create(workspace=key.workspace, owner=key.issuer, label=values['name'],
                                                     secret_digest=digest))):
                    target = made[0]
                    break
        if target is None:
            return {'status': device_contracts.CLAIMED_ELSEWHERE}
        moved = True
    elif current.secret_digest != digest:
        return {'status': device_contracts.CLAIMED_ELSEWHERE}
    if target.name != key.device:
        # 只有迁移来的旧槽位密钥能改挂；按电脑发的密钥生来就在自己的电脑上。一台电脑在一条连接上只留一把。
        if (target.workspace != key.workspace or key.active_key != contracts.active_identity(site, key.connection)
                or current_key(key.connection, target.name, exclude=key.name)):
            return {'status': device_contracts.CLAIMED_ELSEWHERE}
        key.device = target.name
        persist_key(key)
        if current is not None and not current.secret_digest and not _keys_of(current.name):
            retire(current, key.issuer)
    target.label = values['name'] or target.label
    target.always_on = 1 if values['always_on'] else 0
    target.host_names = json.dumps(values['host_names'], ensure_ascii=False)  # 原样存，不筛（第 0 批：含 WSL / VPN 地址）
    persist(target)
    if moved and not key.full_report_due:
        key.full_report_due = 1
        persist_key(key)
    return {'status': device_contracts.ATTACHED, 'device': target.name}


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
