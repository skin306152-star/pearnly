"""池的状态（ADR-021，方案 2.10）：录入入口、LINE、CC 工具、定时任务问「Express 现在能不能选、能不能写」的唯一判据。

``state(connection, account_key=None)`` → ``{online, writers_known, needs_update}``：

- ``writers_known``：池里至少一台电脑够得到（这份 / 任一份）账套、能写，它的密钥有效、签发人此刻仍有权、没退役——
  有它就能选 Express、确认、排队（D4：电脑关着也排，开机后写）；
- ``online``：这些电脑里有一台此刻在线（任一把有效密钥 3 分钟内报过到），与以前的「助手在线」同一个意思；
- ``needs_update``：给了账套、它有排队的单，而够得到它的电脑没有一台支持排头要的载荷版本（只能等人更新 CC 桌面版）。

只读，不加锁，调用方负责权限（`api.online` 先过连接使用授权）。MR.ERP 由服务端直连写，这里只看有没有读文件夹的电脑在线。
"""
from __future__ import annotations

import frappe

from pearnly_cowork.connections import access
from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.targets.identity import normalize_express_account_key
from . import credentials, route_contracts

CONNECTION = 'Cowork ERP Connection'


def _writers(doc, peers, key):
    devices = (account_pool.reachers(doc.name, key, writable=True) if key
               else account_pool.writer_devices(doc.name))
    issuers = {}

    def valid(issuer):
        if issuer not in issuers:
            issuers[issuer] = credentials.issuer_valid(doc, issuer)
        return issuers[issuer]

    return [device for device in sorted(devices)
            if device in peers and any(valid(issuer) for issuer in peers[device].get('issuers') or ())]


def _head_version(connection, key):
    rows = frappe.db.sql('''SELECT name, payload_version FROM `tabCowork Push Record`
        WHERE connection=%s AND account_key=%s AND status='pending' AND COALESCE(result_unknown,0)=0
        ORDER BY created_at, name LIMIT 1''', (connection, key), as_dict=True)
    if not rows:
        return None
    if rows[0].payload_version:
        return int(rows[0].payload_version)
    payload = frappe.parse_json(frappe.db.get_value('Cowork Push Record', rows[0].name, 'payload_json') or '{}') or {}
    try:
        return max(1, int(payload.get('payload_version') or 1))
    except (TypeError, ValueError):
        return 1


def state(connection, account_key=None, *, now=None):
    doc = frappe.get_doc(CONNECTION, connection)
    peers = credentials.pool_peers(doc.name, now)
    if doc.adapter != 'express':
        online = any(peer.get('online') for peer in peers.values())
        return {'online': online, 'writers_known': bool(peers), 'needs_update': False}
    key = normalize_express_account_key(account_key) if account_key else ''
    writers = _writers(doc, peers, key)
    online = any(peers[device].get('online') for device in writers)
    needs_update = False
    if key and writers:
        version = _head_version(doc.name, key)
        if version is not None:
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
