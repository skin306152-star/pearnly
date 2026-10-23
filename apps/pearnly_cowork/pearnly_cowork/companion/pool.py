"""池的状态（ADR-021，方案 2.10；按人，第 51–53 条）：录入入口、LINE、CC 工具、定时任务问「Express 现在能不能选、能不能写」的
唯一判据。

谁的活谁的电脑做（用户 10-16）：只看替这件活干的电脑——给了 ``device``（定时任务读文件夹的那台）就是它，否则是 ``user``
自己登录过 CC 的电脑（`connections.pool.owned`；没有登录记录的电脑过渡期照旧算）。同事的电脑在线、够得到都不算他的。

``state(connection, account_key=None, *, user=None, device=None)`` → ``{online, writers_known, needs_update, computers}``：

- ``writers_known``：这些电脑里至少一台够得到（这份 / 任一份）账套、能写，它的密钥有效、签发人此刻仍有权、没退役——
  有它就能选 Express、确认、排队（D4：电脑关着也排，开机后写）。与确认同一个判据（`push.native.service.has_writer`）；
- ``online``：这些能写的电脑里有一台此刻在线（任一把有效密钥 3 分钟内报过到）。能写、都不在线 → 「电脑离线（仍可选）」；
- ``needs_update``：给了账套、这个人（这台电脑）有排队的单，而他能写的电脑没有一台支持排头要的载荷版本；
- ``computers``：他有没有电脑（有一把有效密钥、没退役，能不能写都算）。录入入口据此分清「未连接电脑」（一台都没有）
  与「没有能写的电脑」（有电脑，但都够不到能写的账套，如账套只在别人电脑的 C 盘上、只读文件夹的成员、或登记人已失权）。

``user`` 与 ``device`` 都不给时看整个池（不按人，只给内部核对）。只读，不加锁，调用方负责权限（`api.online` 先过连接
使用授权）。MR.ERP 由服务端直连写，这里只看有没有读文件夹的电脑在线。
"""
from __future__ import annotations

import frappe

from pearnly_cowork.connections import access
from pearnly_cowork.connections import pool as account_pool
from pearnly_core.express_rules.targets.identity import normalize_express_account_key
from pearnly_core.cc_desktop.wire import route_contracts

from . import credentials, queue_heads

CONNECTION = 'Cowork ERP Connection'


def state(connection, account_key=None, *, user=None, device=None, now=None):
    doc = frappe.get_doc(CONNECTION, connection)
    peers = credentials.pool_peers(doc.name, now)
    if doc.adapter != 'express':
        online = any(peer.get('online') for peer in peers.values())
        return {'online': online, 'writers_known': bool(peers), 'needs_update': False, 'computers': bool(peers)}
    mine = account_pool.owned(peers, user or '', device=device or '') if (user or device) else peers
    key = normalize_express_account_key(account_key) if account_key else ''
    # 与确认的判据（`push.native.service.has_writer`）同一个：`connections.pool.writers`，只在他的电脑里找
    writers = account_pool.writers(doc, key or None, peers=mine) if mine else []
    online = any(mine[item].get('online') for item in writers)
    needs_update = False
    if key and writers:
        # 与电脑领取时看的是同一张排头（`queue_heads`），只看他的单
        head = queue_heads.heads(doc.name, doc.workspace, doc.client, [key], device=device or '',
                                 user=None if device else user)
        if head:
            version = queue_heads.payload_versions(head)[head[0].name]
            known = {item: route_contracts.Peer(payload_version=mine[item].get('payload_version')) for item in writers}
            needs_update = route_contracts.needs_update(version, writers, known)
    return {'online': online, 'writers_known': bool(writers), 'needs_update': needs_update, 'computers': bool(mine)}


def for_user(connection, account_key=None, *, device=''):
    """当前用户能用这条连接时的 :func:`state`，按当前用户自己的电脑（给了 ``device`` 就是那台）算（录入入口、LINE、CC、
    定时任务都走它；不要求管理权限）。不能用就抛权限错误。"""
    doc = frappe.get_doc(CONNECTION, connection)
    access.require_connection(doc, lock=False)
    return state(doc.name, account_key, user=frappe.session.user, device=device or None)


__all__ = ['for_user', 'state']
