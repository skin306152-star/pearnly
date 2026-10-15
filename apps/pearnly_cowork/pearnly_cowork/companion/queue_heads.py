"""排队单的排头（ADR-021，方案 2.5）：每份账套严格按排队先后，只看它的第一张。

写账分派（`transport.lease`）与池的状态（`pool.state` 的「需更新」）用同一个查询、同一套过滤（第 3 批审查：以前池的状态
另写了一条，没排掉内部 ERP 来源、别的分支或客户的单，可能按一张永远不会发给电脑的单算「需更新」）。

按账套各取排头，不再先按建立时间取前若干条再分组：一份账套积压上千张时，别的账套的排头照样看得到（第 3 批审查）。
只读、不加锁。
"""
import frappe

from . import route_contracts

RECORD = 'Cowork Push Record'

# 进电脑写入队列的单：这条连接的归属、旧分支、代数 0、排队中、不是结果不明；内部 ERP 来源的票不经电脑
_QUEUED = ("{r}.connection=%(connection)s AND {r}.workspace=%(workspace)s AND COALESCE({r}.client,'')=%(client)s "
           "AND {r}.branch='legacy' AND {r}.binding_generation=0 AND {r}.status='pending' "
           "AND COALESCE({r}.result_unknown,0)=0 AND COALESCE({h}.source,'') NOT IN ('erp_web','line_erp')")

# 先按账套取最早的建立时间（一次分组），再取那一刻的那几张（同一时刻建的由 `route_contracts.heads` 按名字取第一张）
Q_HEADS = ("SELECT r.name, r.account_key, r.actor, r.entered_by, r.queued_at, r.created_at, r.payload_version "
           "FROM `tabCowork Push Record` r INNER JOIN `tabCowork OCR History` h ON h.name=r.history "
           "INNER JOIN (SELECT r2.account_key, MIN(r2.created_at) AS first_at FROM `tabCowork Push Record` r2 "
           "INNER JOIN `tabCowork OCR History` h2 ON h2.name=r2.history "
           "WHERE " + _QUEUED.format(r='r2', h='h2') + " AND r2.account_key IN %(keys)s "
           "GROUP BY r2.account_key) m ON m.account_key=r.account_key AND m.first_at=r.created_at "
           "WHERE " + _QUEUED.format(r='r', h='h') + " "
           "ORDER BY r.created_at, r.name")


def heads(connection, workspace, client, account_keys):
    """``account_keys`` 里每份账套的排头（按排头的先后），只取分派要用的几列。同一时刻建的按名字取第一张。"""
    keys = tuple(sorted({key for key in account_keys or () if key}))
    if not connection or not keys:
        return []
    rows = frappe.db.sql(Q_HEADS, {'connection': connection, 'workspace': workspace, 'client': client or '',
                                   'keys': keys}, as_dict=True)
    return route_contracts.heads(rows, limit=max(1, len(keys)))


def _version(payload_json):
    payload = frappe.parse_json(payload_json or '{}') or {}
    try:
        return max(1, int(payload.get('payload_version') or 1))
    except (TypeError, ValueError, AttributeError):
        return 1


def payload_versions(rows):
    """``{记录名: 这张单要的载荷版本}``。这一批之前准备的单没有这一列：一条查询读回它们冻结的载荷。"""
    missing = tuple(sorted(row.name for row in rows if not row.payload_version))
    frozen = {}
    if missing:
        frozen = {item.name: item.payload_json for item in frappe.db.sql(
            'SELECT name, payload_json FROM `tabCowork Push Record` WHERE name IN %s', (missing,), as_dict=True)}
    return {row.name: int(row.payload_version) if row.payload_version else _version(frozen.get(row.name))
            for row in rows}


__all__ = ['heads', 'payload_versions']
