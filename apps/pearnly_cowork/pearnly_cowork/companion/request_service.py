"""CC 桌面端按需请求的服务（口径与加一种请求的做法见 `request_contracts` 文件头）。

用户一侧（业务域以当前用户身份调用，权限在业务域核过之后这里再核一次连接使用授权）：
- ``create(kind, connection, params, request_key, *, target_device, account_key)``：建请求；同一请求号还在等、在做或
  做完没取走时原样返回（幂等），失败 / 过期的换一条新的。读文件夹带 ``target_device``（持有那个根目录的电脑），读账套带
  ``account_key``（这份账套的规范键，ADR-021 第 4 批，口径见 `request_contracts` 文件头）；
- ``find(kind, connection, request_key)``：同一请求号此刻还在等、在做或做完没取走的那一件（不建）；业务域先找它，
  找到就接着等，不再问电脑在不在（电脑正忙着读、传这一件时可能好一阵不来领）；
- ``status(name)`` / ``result(name)`` / ``drop(name)``：只给发起人；``drop`` 连行带结果字节一起删。结果属于连接与账套的
  种类（``request_contracts.SHARED_KINDS``，科目建议的历史）同一请求号谁建都是同一件，此刻能用这条连接的人都能看、取、删；
- ``presence(connection, device)``：这台电脑最近什么时候来领过、什么版本、能做哪几种，手上有没做完的租约也算在线
  （按电脑记：同一个池里好几台电脑各来各的）；``waiting(connection, kind, known_version, device=…, account_key=…)``
  据此回「能领」或 waiting_for_cc（离线 / 要升级），业务域建请求前先问：给了电脑只问那台，给了账套问够得到它的各台。

机器一侧（`request_api` 在配对码身份下调用，身份核对在 `http_auth`）：
- ``lease_next(context, body)``：先把这条连接上等太久没人领、领了超时的标成过期，再按先来先做领一件：只看定向给这台
  电脑的、没定向的；读账套的只扫这台够得到的那几份（`connections.pool.readable_keys`），按分派顺序轮到它
  （`route_contracts.decide`，发起人当录入人），交出去的参数换成它的本机写法。发起人此刻已不能用这条连接的直接判失败，不交给电脑；
- ``complete(context, body)``：交结果，一块一块按已收字节续传，最后一块核对大小与 sha256；或者交失败原因。
"""
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import frappe
from frappe import _
from frappe.utils import get_datetime
from pearnly_core.execution import state as pearnly_state

from pearnly_cowork.companion import credentials, route_contracts
from pearnly_cowork.companion import request_contracts as contracts
from pearnly_cowork.companion import request_store as store
from pearnly_cowork.companion.request_access import REQUEST, read, write
from pearnly_cowork.connections import access as connection_access
from pearnly_cowork.connections import pool as account_pool
from pearnly_cowork.targets.identity import normalize_express_account_key

CONNECTION = "Cowork ERP Connection"
PRESENCE = "cowork:companion:requests:"
PRESENCE_TTL = 7 * 86400
_NAME = re.compile(r"^[0-9a-f]{32}$")
# 一次领取最多看这么多件排队的请求（先不加锁挑出轮到这台的，再锁那一件）
LEASE_SCAN = 50


def _now():
    """库里一律存 UTC（裸时间）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _at(value):
    """库里读出的时间（可能是字符串）→ datetime；空是 None。"""
    return get_datetime(value) if value else None


def _json(value):
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _error(code):
    frappe.throw(_(code))


def _later(name):
    """提交以后再删结果字节（回滚时不丢）。"""
    frappe.db.after_commit.add(lambda: store.drop(name))


def _remove(name):
    frappe.db.delete(REQUEST, {"name": name})
    _later(name)


def view(row, now=None) -> dict:
    status, code = contracts.effective(row.status, row.error_code, _at(row.requested_at), _at(row.lease_expires_at),
                                       now or _now())
    return {"request": row.name, "kind": row.kind, "status": status, "code": code,
            "received_bytes": int(row.received_bytes or 0), "result_bytes": int(row.result_bytes or 0),
            "params": _json(row.params_json), "meta": _json(row.meta_json),
            "companion_version": row.companion_version or ""}


# ── 用户一侧 ────────────────────────────────────────────────────────────────


def _same(row, actor, connection, kind) -> bool:
    """同一请求号的那一行是不是这个人在这条连接上的这种请求（结果属于连接的种类不论是谁建的）。"""
    return (row.connection, row.kind) == (connection, kind) and (contracts.shared(kind) or row.actor == actor)


def create(kind, connection, params, request_key, *, target_device="", account_key="") -> dict:
    try:
        kind = contracts.kind(kind)
        key = contracts.request_key(request_key)
        params = contracts.params(params)
        target_device, account_key = contracts.target(kind, target_device, account_key)
    except contracts.ContractError as error:
        _error(error.code)
    account_key = normalize_express_account_key(account_key) if account_key else ""
    if not isinstance(connection, str) or not connection or len(connection) > 140:
        _error("companion.on_demand_invalid")
    doc = frappe.get_doc(CONNECTION, connection)
    actor = connection_access.require_connection(doc)
    if kind not in contracts.ADAPTER_KINDS.get(doc.adapter, ()):
        _error("companion.on_demand_unsupported")
    now = _now()
    existing = frappe.db.get_value(REQUEST, {"request_key": key}, "name")
    if existing:
        row = read(existing, for_update=True)
        if not _same(row, actor, connection, kind):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        current = view(row, now)
        if current["status"] in ("requested", "leased", "done"):
            return current
        _remove(row.name)  # 失败或过期的：换一条新的
    _sweep(connection, now)
    waiting = frappe.db.sql(f"""SELECT COUNT(*) FROM `tab{REQUEST}` WHERE connection = %s AND actor = %s
        AND status IN ('requested', 'leased')""", (connection, actor))[0][0]
    if waiting >= contracts.OPEN_PER_ACTOR:
        _error("companion.on_demand_busy")
    row = frappe.new_doc(REQUEST)
    row.update({"request_id": uuid4().hex, "request_key": key, "connection": connection, "kind": kind, "actor": actor,
                "target_device": target_device, "account_key": account_key,
                "params_json": json.dumps(params, ensure_ascii=False, sort_keys=True), "status": "requested",
                "requested_at": now, "received_bytes": 0, "result_bytes": 0})
    point = "cowork_companion_request"
    frappe.db.savepoint(point)
    try:
        write(row)
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        # 同一请求号同时建了两次：用先建成的那一条
        frappe.db.rollback(save_point=point)
        frappe.clear_messages()
        first = _committed(key)
        if first is None:
            _error("companion.on_demand_busy")  # 先建成的那条又被删了：稍后再试
        if not _same(first, actor, connection, kind):
            frappe.throw(_("Not Permitted"), frappe.PermissionError)
        return view(first, now)
    return view(row, now)


_VIEW_FIELDS = ("name, actor, connection, kind, status, error_code, requested_at, lease_expires_at, received_bytes, "
                "result_bytes, params_json, meta_json, companion_version")


def _committed(key):
    """撞上唯一键以后读先建成的那一行。普通读在可重复读下看的是本事务开头的快照，看不到别的事务刚提交的行；
    加锁读看最新提交的。用共享锁：同一请求号同时撞上的几个调用互不等待、不互锁。"""
    rows = frappe.db.sql(f"""SELECT {_VIEW_FIELDS} FROM `tab{REQUEST}` WHERE request_key = %s LOCK IN SHARE MODE""",
                         (key,), as_dict=True)
    return rows[0] if rows else None


def find(kind, connection, request_key):
    """这个人在这条连接上用这个请求号建的、此刻还在等、在做或做完没取走的那一件（``view``）；没有、不是本人的、
    已失败或过期的回 None。只读，不建。调用方先核过连接权限。"""
    try:
        kind = contracts.kind(kind)
        key = contracts.request_key(request_key)
    except contracts.ContractError as error:
        _error(error.code)
    name = frappe.db.get_value(REQUEST, {"request_key": key}, "name")
    if not name:
        return None
    row = read(name)
    if not _same(row, frappe.session.user, connection, kind):
        return None
    current = view(row)
    return current if current["status"] in ("requested", "leased", "done") else None


def _sweep(connection, now):
    """这条连接上很久以前的请求连同没取走的结果一起删（不另开定时任务）。"""
    cutoff = now - timedelta(seconds=contracts.KEEP_SECONDS)
    for (name,) in frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE connection = %s AND requested_at < %s
            LIMIT 200""", (connection, cutoff)):
        _remove(name)


def _own(name):
    if not isinstance(name, str) or not _NAME.fullmatch(name) or not frappe.db.exists(REQUEST, name):
        frappe.throw(_("companion.on_demand_missing"), frappe.DoesNotExistError)
    row = read(name)
    if row.actor != frappe.session.user and not (contracts.shared(row.kind) and _usable(row.connection)):
        frappe.throw(_("companion.on_demand_missing"), frappe.DoesNotExistError)
    return row


def _usable(connection) -> bool:
    """当前用户此刻能用这条连接（录入权限 + 连接使用授权，同网页）。"""
    try:
        connection_access.require_connection(frappe.get_doc(CONNECTION, connection))
        return True
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        frappe.clear_messages()
        return False


def status(name) -> dict:
    return view(_own(name))


def result(name) -> tuple:
    """做完的结果（字节, meta）；还没做完、结果字节对不上都报错。"""
    row = _own(name)
    content = store.read(row.name) if row.status == "done" else None
    if content is None or len(content) != int(row.result_bytes or 0) \
            or hashlib.sha256(content).hexdigest() != row.result_sha256:
        _error("companion.on_demand_not_ready")
    return content, _json(row.meta_json)


def drop(name) -> None:
    _remove(_own(name).name)


def _presence_key(connection, device):
    return PRESENCE + connection + (("|" + device) if device else "")


def _busy(connection, device, now):
    """这台电脑在这条连接上领了、租约还没到期的一件（kind, 版本）：小助手一次只做一件、做完才再来领，读得久、传得慢的
    时候好一阵不来领，但它在线。"""
    found = frappe.db.sql(f"""SELECT kind, companion_version FROM `tab{REQUEST}` WHERE connection = %s
        AND status = 'leased' AND lease_expires_at > %s AND IFNULL(lease_device, '') = %s
        ORDER BY lease_expires_at DESC LIMIT 1""", (connection, now, device or ""))
    return tuple(found[0]) if found else None


def presence(connection, device="") -> dict:
    """这台电脑（``device``，`credentials.device_id`）在这条连接上最近一次来领按需请求：{seen（SEEN_SECONDS 内来过，
    或手上有一件租约没到期的）, version, kinds, at}；从没来过（旧版 CC 桌面端不来领）是 seen 假、version 空。
    调用方先核过连接权限。"""
    now = _now()
    try:
        value = pearnly_state.store().get_value(_presence_key(connection, device), use_local_cache=False)
    except Exception:
        value = None
    try:
        at = datetime.fromisoformat(str(value["at"])) if isinstance(value, dict) and value.get("at") else None
    except ValueError:
        at = None
    if at is None:
        value = {}
    seen = contracts.seen_recently(at, now)
    busy = None if seen else _busy(connection, device, now)
    if busy and not value:
        # 记的「来过」丢了（缓存清过）：按它在做的那一件
        value = {"version": busy[1] or "", "kinds": [busy[0]]}
    return {"seen": seen or bool(busy), "version": str(value.get("version") or ""),
            "kinds": [kind for kind in value.get("kinds") or [] if kind in contracts.KINDS],
            "at": value.get("at") if at else None}


def _candidates(connection, device=None, account_key=None):
    """问谁能不能领：给了电脑就是那台（给了空串是一台都没有）；给了账套是够得到它（状态正常）的各台；都没给是这条连接上的每台电脑。
    回 ``{电脑: 心跳 / 电脑行报的版本}``。"""
    peers = credentials.pool_peers(connection)
    if device is not None:
        # 认不出电脑（空）就谁都不问：不落到部署前按连接记的在场与没记电脑的旧租约上（第 4 批审查 L2）
        names = [device] if device else []
    elif account_key:
        names = account_pool.reachers(connection, account_key)
    else:
        names = sorted(peers)
    return {name: str((peers.get(name) or {}).get("version") or "") for name in names}


def waiting(connection, kind, known_version="", *, device=None, account_key=None) -> dict:
    """此刻有没有电脑能领这种请求：能就回 ``{}``；不能回 ``{"status": "waiting_for_cc", "reason": "offline" |
    "update_needed", ...}``（业务域据此不建请求、照实告诉用户）。``device``：定向的那台（读文件夹）；``account_key``：
    够得到这份账套的各台里有一台能领就行（读账套，ADR-021 第 4 批）。``known_version`` 是别处报过的 CC 桌面端版本
    （文件夹同步），电脑从没来领过按需请求时与电脑行上的版本一起分辨「离线」与「要升级」。调用方先核过连接权限。"""
    candidates = _candidates(connection, device, account_key)
    seen_any, versions = False, []
    for name, reported in candidates.items():
        seen = presence(connection, name)
        if seen["seen"] and kind in seen["kinds"]:
            return {}
        seen_any = seen_any or seen["seen"]
        versions.append(seen["version"] or reported)
    versions = [value for value in versions if value]
    version = max(versions, key=contracts.version_tuple) if versions else str(known_version or "")
    if seen_any or (version and not contracts.supports(version)):
        return {"status": "waiting_for_cc", "reason": "update_needed", "version": version,
                "minimum": contracts.MIN_COMPANION}
    return {"status": "waiting_for_cc", "reason": "offline"}


# ── 机器一侧 ────────────────────────────────────────────────────────────────


def _remember(connection, device, version, kinds, now):
    try:
        pearnly_state.store().set_value(_presence_key(connection, device),
                                        {"version": version, "kinds": kinds, "at": now.isoformat()},
                                        expires_in_sec=PRESENCE_TTL)
    except Exception:
        # 记不下「来过」不挡领取（只影响工具判断电脑在不在）
        frappe.log_error(title="Cowork companion request presence failed", message=frappe.get_traceback()[-1500:])


def _close(row, status, code, now):
    row.update({"status": status, "error_code": code, "completed_at": now, "lease_handle": None})
    write(row)
    _later(row.name)


def _expire(connection, now):
    """这条连接上等太久没人领、领了超时的标成过期。先不加锁挑出到期的，再逐件锁住复核：每台电脑每 15 秒来领一次，
    不为它锁住整条连接上所有在途的请求（ADR-021 第 4 批，方案 2.7）。"""
    for (name,) in frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE connection = %s
            AND ((status = 'requested' AND requested_at < %s)
                 OR (status = 'leased' AND (lease_expires_at IS NULL OR lease_expires_at <= %s)))
            ORDER BY requested_at, name""", (connection, now - timedelta(seconds=contracts.CLAIM_SECONDS), now)):
        if not frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE name = %s
                AND status IN ('requested', 'leased') FOR UPDATE""", (name,)):
            continue
        row = read(name)
        status, code = contracts.effective(row.status, row.error_code, _at(row.requested_at), _at(row.lease_expires_at),
                                           now)
        if status == "expired":
            _close(row, "expired", code, now)


def _actor_ready(row) -> bool:
    """发起人此刻仍能用这条连接（录入权限 + 连接使用授权，与网页同一判据）。"""
    try:
        connection_access.require_connection(frappe.get_doc(CONNECTION, row.connection), actor=row.actor)
        return True
    except (frappe.PermissionError, frappe.DoesNotExistError, frappe.ValidationError):
        frappe.clear_messages()
        return False


def _turns(connection, device, rows, now):
    """这几件读账套的请求里轮到这台电脑的：``{请求名: 这台电脑那份账套的可达行}``。资格与顺序同写账分派
    （`route_contracts.decide`）：这台够得到那份账套（状态正常，读不要求能写）；发起人自己的电脑 → 常驻 → 其他，排了
    ``HANDOFF_SECONDS`` 谁都行。「在线」按领按需请求算（这一种 SEEN_SECONDS 内来领过），不按心跳：心跳在、却从不来领
    按需请求的旧版不该让别的电脑干等。不看载荷版本、不黏住（只读）。"""
    keys = {row.account_key for row in rows if row.account_key}
    mine = account_pool.readable_reach(connection, device, keys)
    if not mine:
        return {}
    reachers = account_pool.reachers_of(connection, set(mine))
    peers, known = credentials.pool_peers(connection), {}
    result = {}
    for row in rows:
        if row.account_key not in mine:
            continue
        for name in reachers.get(row.account_key) or ():
            if (name, row.kind) not in known:
                seen = presence(connection, name)
                peer = peers.get(name) or {}
                known[(name, row.kind)] = route_contracts.Peer(
                    user=peer.get("user") or "", always_on=bool(peer.get("always_on")),
                    online=bool(seen["seen"] and row.kind in seen["kinds"]))
        job = route_contracts.Job(name=row.name, account_key=row.account_key, actor=row.actor,
                                  queued_at=_at(row.requested_at) or now)
        decision = route_contracts.decide(
            device, job, reachers.get(row.account_key) or (), {
                name: known[(name, row.kind)] for name in reachers.get(row.account_key) or ()},
            route_contracts.Account(), now)
        if decision.take:
            result[row.name] = mine[row.account_key]
    return result


def lease_next(context, body) -> dict:
    wanted = contracts.next_request(body)
    now = _now()
    device = credentials.device_id(context.credential_id)
    adapter = frappe.db.get_value(CONNECTION, context.connection, "adapter") or ""
    kinds = contracts.allowed_kinds(adapter, wanted["kinds"])
    _remember(context.connection, device, wanted["version"], kinds, now)
    _expire(context.connection, now)
    if not kinds:
        return {"request": None}
    marks = ", ".join(["%s"] * len(kinds))
    # 只看这台能领的：定向给它的、没定向的；读账套的只看它够得到的那几份（不然别的电脑才够得到的一批会占满扫描名额，
    # 后面定向给它的取文件要等那批过期，第 4 批审查 L1），轮没轮到它再按分派顺序看。先不加锁挑，再锁挑中的那一件
    reach = sorted(account_pool.readable_keys(context.connection, device))
    books = f"OR account_key IN ({', '.join(['%s'] * len(reach))})" if reach else ""
    rows = frappe.db.sql(f"""SELECT name, kind, actor, account_key, requested_at FROM `tab{REQUEST}`
        WHERE connection = %s AND status = 'requested' AND kind IN ({marks}) AND IFNULL(target_device, '') IN (%s, '')
        AND (IFNULL(account_key, '') = '' {books})
        ORDER BY requested_at, name LIMIT {LEASE_SCAN}""", (context.connection, *kinds, device, *reach), as_dict=True)
    accounts = [row for row in rows if row.account_key]
    turns = _turns(context.connection, device, accounts, now) if accounts else {}
    for candidate in rows:
        if candidate.account_key and candidate.name not in turns:
            continue
        if not frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE name = %s AND status = 'requested'
                FOR UPDATE""", (candidate.name,)):
            continue  # 别的电脑刚领走
        row = read(candidate.name)
        if not _actor_ready(row):
            _close(row, "failed", "actor_not_allowed", now)
            continue
        handle = secrets.token_hex(16)
        seconds = contracts.LEASE_SECONDS[row.kind]
        row.update({"status": "leased", "lease_handle": handle, "lease_credential": context.credential_id,
                    "lease_device": device, "leased_at": now, "lease_expires_at": now + timedelta(seconds=seconds),
                    "companion_version": wanted["version"], "received_bytes": 0})
        write(row)
        params = _json(row.params_json)
        reach = turns.get(row.name)
        if reach is not None:
            params = contracts.localize(params, reach.path, reach.root)
        return {"request": {"id": row.name, "kind": row.kind, "params": params, "handle": handle,
                            "lease_seconds": seconds, "chunk_bytes": contracts.CHUNK_BYTES,
                            "max_bytes": contracts.MAX_BYTES[row.kind]}}
    return {"request": None}


def complete(context, body) -> dict:
    data = contracts.complete_request(body)
    now = _now()
    found = frappe.db.sql(f"SELECT name FROM `tab{REQUEST}` WHERE name = %s FOR UPDATE", (data["request"],))
    row = read(found[0][0]) if found else None
    if (row is None or row.connection != context.connection or row.status != "leased"
            or row.lease_handle != data["handle"] or row.lease_credential != context.credential_id
            or not row.lease_expires_at or _at(row.lease_expires_at) <= now):
        # 不是这台电脑领的、已经交过、超时被收回：这件不用再交了
        raise contracts.ContractError("companion.on_demand_stale")
    if not data["ok"]:
        _close(row, "failed", data["code"], now)
        return {"status": "failed"}
    received = int(row.received_bytes or 0)
    if data["offset"] != received:
        return {"ok": False, "code": "companion.on_demand_offset", "received": received}
    total = received + len(data["content"])
    if total > contracts.MAX_BYTES[row.kind]:
        _close(row, "failed", "too_large", now)
        return {"ok": False, "code": "companion.on_demand_too_large"}
    try:
        store.write(row.name, received, data["content"])
    except ValueError:
        _close(row, "failed", "stored_part_missing", now)
        return {"ok": False, "code": "companion.on_demand_stale"}
    row.received_bytes = total
    row.lease_expires_at = max(_at(row.lease_expires_at), now + timedelta(seconds=contracts.LEASE_SECONDS[row.kind]))
    if data["final"]:
        size, digest = store.digest(row.name)
        if data["size"] != total or size != total or digest != data["sha256"]:
            _close(row, "failed", "content_mismatch", now)
            return {"ok": False, "code": "companion.on_demand_mismatch"}
        row.update({"status": "done", "result_bytes": total, "result_sha256": digest, "completed_at": now,
                    "meta_json": json.dumps(data["meta"], ensure_ascii=False, sort_keys=True), "lease_handle": None})
    write(row)
    return {"status": row.status, "received": total}
