"""排队单的排头（ADR-021，方案 2.5）：每份账套严格按排队先后，只看它的第一张。

写账分派（`transport.lease`）与池的状态（`pool.state` 的「需更新」）用同一个查询、同一套过滤（第 3 批审查：以前池的状态
另写了一条，没排掉内部 ERP 来源、别的分支或客户的单，可能按一张永远不会发给电脑的单算「需更新」）。

按账套各取排头，不再先按建立时间取前若干条再分组：一份账套积压上千张时，别的账套的排头照样看得到（第 3 批审查）。

谁的活谁的电脑做（ADR-021 第 51 条，用户 10-16）：排头只在「这台电脑能写的单」里取（:func:`scope`）——定时任务冻结了读文件夹
的那台（``route_device``）、其余按录入人在这台电脑上登录过 CC。A 的单排在前面而 A 的电脑关着时，B 的电脑照样看得到同一份
账套里 B 的单（以前靠 120 秒后谁都能领才不卡）。池的状态按人取时只看这个人自己的单。只读、不加锁。
"""
import frappe

from . import route_contracts

RECORD = 'Cowork Push Record'

# 进电脑写入队列的单：这条连接的归属、旧分支、代数 0、排队中、不是结果不明；内部 ERP 来源的票不经电脑
_QUEUED = ("{r}.connection=%(connection)s AND {r}.workspace=%(workspace)s AND COALESCE({r}.client,'')=%(client)s "
           "AND {r}.branch='legacy' AND {r}.binding_generation=0 AND {r}.status='pending' "
           "AND COALESCE({r}.result_unknown,0)=0 AND COALESCE({h}.source,'') NOT IN ('erp_web','line_erp')")

# 这台电脑 / 这个人的单（:func:`scope` 给参数）：``everyone`` 不筛；定时任务只归 ``device``；其余按录入人在 ``users`` 里，
# ``anyone`` = 这台电脑没有登录记录（过渡期谁的单都写，`connections.member.serves`）
_OWNED = ("(%(everyone)s=1 OR ({r}.route_device=%(device)s AND %(device)s<>'') "
          "OR (COALESCE({r}.route_device,'')='' AND (%(anyone)s=1 "
          "OR COALESCE(NULLIF({r}.entered_by,''),{r}.actor) IN %(users)s)))")

# 先按账套取最早的建立时间（一次分组），再取那一刻的那几张（同一时刻建的由 `route_contracts.heads` 按名字取第一张）。
# 内外两层同一套过滤：排头是「这台能写的单」里最早的那张
Q_HEADS = ("SELECT r.name, r.account_key, r.actor, r.entered_by, r.route_device, r.created_at, r.payload_version "
           "FROM `tabCowork Push Record` r INNER JOIN `tabCowork OCR History` h ON h.name=r.history "
           "INNER JOIN (SELECT r2.account_key, MIN(r2.created_at) AS first_at FROM `tabCowork Push Record` r2 "
           "INNER JOIN `tabCowork OCR History` h2 ON h2.name=r2.history "
           "WHERE " + _QUEUED.format(r='r2', h='h2') + " AND " + _OWNED.format(r='r2') + " "
           "AND r2.account_key IN %(keys)s "
           "GROUP BY r2.account_key) m ON m.account_key=r.account_key AND m.first_at=r.created_at "
           "WHERE " + _QUEUED.format(r='r', h='h') + " AND " + _OWNED.format(r='r') + " "
           "ORDER BY r.created_at, r.name")


def scope(*, device='', users=None, user=None):
    """排头取谁的单：

    - ``device`` + ``users``（电脑来领）：冻结给这台的定时任务，加上录入人在这台电脑上登录过 CC 的单；``users`` 为空
      （没有登录记录的电脑）是所有不是定时任务的单；
    - ``user``（池的状态按人）：这个人自己录的、不是定时任务的单；
    - ``device`` 不给 ``users``、也不给 ``user``（池的状态按电脑，定时任务）：冻结给这台的单；
    - 都不给：不筛。"""
    if user:
        return {'everyone': 0, 'device': '', 'anyone': 0, 'users': (user,)}
    if device and users is not None:
        members = tuple(sorted({item for item in users if item}))
        return {'everyone': 0, 'device': device, 'anyone': 0 if members else 1, 'users': members or ('',)}
    if device:
        return {'everyone': 0, 'device': device, 'anyone': 0, 'users': ('',)}
    return {'everyone': 1, 'device': '', 'anyone': 0, 'users': ('',)}


def heads(connection, workspace, client, account_keys, *, device='', users=None, user=None):
    """``account_keys`` 里每份账套的排头（按排头的先后），只取分派要用的几列，只在 :func:`scope` 筛出的单里取。
    同一时刻建的按名字取第一张。"""
    keys = tuple(sorted({key for key in account_keys or () if key}))
    if not connection or not keys:
        return []
    rows = frappe.db.sql(Q_HEADS, {'connection': connection, 'workspace': workspace, 'client': client or '',
                                   'keys': keys, **scope(device=device, users=users, user=user)}, as_dict=True)
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


__all__ = ['heads', 'payload_versions', 'scope']
