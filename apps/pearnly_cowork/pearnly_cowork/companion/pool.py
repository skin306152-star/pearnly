"""池的状态（ADR-021，方案 2.10）：录入入口、LINE、CC 工具、定时任务问「Express 现在能不能选、能不能写」的唯一判据。

``state(connection, account_key=None)`` → ``{online, writers_known, needs_update}``：

- ``writers_known``：池里至少一台电脑够得到（这份 / 任一份）账套、能写，它的密钥有效、签发人此刻仍有权、没退役——
  有它就能选 Express、确认、排队（D4：电脑关着也排，开机后写）。与确认同一个判据（`connections.pool.writers`）；
- ``online``：这些电脑里有一台此刻在线（任一把有效密钥 3 分钟内报过到），与以前的「助手在线」同一个意思；
- ``needs_update``：给了账套、它有排队的单，而够得到它的电脑没有一台支持排头要的载荷版本（只能等人更新 CC 桌面版）。

只读，不加锁，调用方负责权限（`api.online` 先过连接使用授权）。MR.ERP 由服务端直连写，这里只看有没有读文件夹的电脑在线。
"""
from __future__ import annotations

import frappe

from pearnly_cowork.connections import access
from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import credentials, queue_heads, route_contracts

CONNECTION = 'Cowork ERP Connection'


def state(connection, account_key=None, *, now=None):
    doc = frappe.get_doc(CONNECTION, connection)
    peers = credentials.pool_peers(doc.name, now)
    if doc.adapter != 'express':
        online = any(peer.get('online') for peer in peers.values())
        return {'online': online, 'writers_known': bool(peers), 'needs_update': False}
    key = normalize_express_account_key(account_key) if account_key else ''
    # 与确认的判据（`push.native.service.has_writer`）同一个：`connections.pool.writers`
    writers = account_pool.writers(doc, key or None, peers=peers)
    online = any(peers[device].get('online') for device in writers)
    needs_update = False
    if key and writers:
        # 与电脑领取时看的是同一张排头（`queue_heads`）
        head = queue_heads.heads(doc.name, doc.workspace, doc.client, [key])
        if head:
            version = queue_heads.payload_versions(head)[head[0].name]
            known = {device: route_contracts.Peer(payload_version=peers[device].get('payload_version'))
                     for device in writers}
            needs_update = route_contracts.needs_update(version, writers, known)
    return {'online': online, 'writers_known': bool(writers), 'needs_update': needs_update}


def for_user(connection, account_key=None):
    """当前用户能用这条连接时的 :func:`state`（录入入口、LINE、CC 都走它；不要求管理权限）。不能用就抛权限错误。"""
    doc = frappe.get_doc(CONNECTION, connection)
    access.require_connection(doc, lock=False)
    return state(doc.name, account_key)


__all__ = ['for_user', 'state']
