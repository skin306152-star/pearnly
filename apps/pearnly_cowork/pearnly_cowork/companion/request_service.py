"""CC 桌面端按需请求的服务（口径与加一种请求的做法见 `request_contracts` 文件头）。

用户一侧（业务域以当前用户身份调用，权限在业务域核过之后这里再核一次连接使用授权）：
- ``create(kind, connection, params, request_key)``：建请求；同一请求号还在等、在做或做完没取走时原样返回（幂等），
  失败 / 过期的换一条新的；
- ``find(kind, connection, request_key)``：同一请求号此刻还在等、在做或做完没取走的那一件（不建）；业务域先找它，
  找到就接着等，不再问电脑在不在（电脑正忙着读、传这一件时可能好一阵不来领）；
- ``status(name)`` / ``result(name)`` / ``drop(name)``：只给发起人；``drop`` 连行带结果字节一起删；
- ``presence(connection)``：这条连接的电脑最近什么时候来领过、什么版本、能做哪几种，手上有没做完的租约也算在线；
  ``waiting(connection, kind, known_version)`` 据此回「能领」或 waiting_for_cc（离线 / 要升级），业务域建请求前先问。

机器一侧（`request_api` 在配对码身份下调用，身份核对在 `http_auth`）：
- ``lease_next(context, body)``：先把这条连接上等太久没人领、领了超时的标成过期，再按先来先做领一件（发起人此刻已
  不能用这条连接的直接判失败，不交给电脑）；
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

from pearnly_cowork.companion import request_contracts as contracts
from pearnly_cowork.companion import request_store as store
from pearnly_cowork.companion.request_access import REQUEST, read, write
from pearnly_cowork.connections import access as connection_access

CONNECTION = "Cowork ERP Connection"
PRESENCE = "cowork:companion:requests:"
PRESENCE_TTL = 7 * 86400
_NAME = re.compile(r"^[0-9a-f]{32}$")


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


def create(kind, connection, params, request_key) -> dict:
    try:
        kind = contracts.kind(kind)
        key = contracts.request_key(request_key)
        params = contracts.params(params)
    except contracts.ContractError as error:
        _error(error.code)
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
        if (row.actor, row.connection, row.kind) != (actor, connection, kind):
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
        if (first.actor, first.connection, first.kind) != (actor, connection, kind):
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
    if (row.actor, row.connection, row.kind) != (frappe.session.user, connection, kind):
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
    if row.actor != frappe.session.user:
        frappe.throw(_("companion.on_demand_missing"), frappe.DoesNotExistError)
    return row


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


def _busy(connection, now):
    """这条连接上电脑领了、租约还没到期的一件（kind, 版本）：小助手一次只做一件、做完才再来领，读得久、传得慢的时候
    好一阵不来领，但它在线。"""
    found = frappe.db.sql(f"""SELECT kind, companion_version FROM `tab{REQUEST}` WHERE connection = %s
        AND status = 'leased' AND lease_expires_at > %s ORDER BY lease_expires_at DESC LIMIT 1""", (connection, now))
    return tuple(found[0]) if found else None


def presence(connection) -> dict:
    """这条连接的电脑最近一次来领按需请求：{seen（SEEN_SECONDS 内来过，或手上有一件租约没到期的）, version, kinds, at}；
    从没来过（旧版 CC 桌面端不来领）是 seen 假、version 空。调用方先核过连接权限。"""
    now = _now()
    try:
        value = pearnly_state.store().get_value(PRESENCE + connection, use_local_cache=False)
    except Exception:
        value = None
    try:
        at = datetime.fromisoformat(str(value["at"])) if isinstance(value, dict) and value.get("at") else None
    except ValueError:
        at = None
    if at is None:
        value = {}
    seen = contracts.seen_recently(at, now)
    busy = None if seen else _busy(connection, now)
    if busy and not value:
        # 记的「来过」丢了（缓存清过）：按它在做的那一件
        value = {"version": busy[1] or "", "kinds": [busy[0]]}
    return {"seen": seen or bool(busy), "version": str(value.get("version") or ""),
            "kinds": [kind for kind in value.get("kinds") or [] if kind in contracts.KINDS],
            "at": value.get("at") if at else None}


def waiting(connection, kind, known_version="") -> dict:
    """这台电脑此刻能不能领这种请求：能就回 ``{}``；不能回 ``{"status": "waiting_for_cc", "reason": "offline" |
    "update_needed", ...}``（业务域据此不建请求、照实告诉用户）。``known_version`` 是别处报过的这台电脑的 CC 桌面端版本
    （文件夹同步、心跳），它从没来领过按需请求时据此分辨「离线」与「要升级」。调用方先核过连接权限。"""
    seen = presence(connection)
    if seen["seen"] and kind in seen["kinds"]:
        return {}
    version = seen["version"] or str(known_version or "")
    if seen["seen"] or (version and not contracts.supports(version)):
        return {"status": "waiting_for_cc", "reason": "update_needed", "version": version,
                "minimum": contracts.MIN_COMPANION}
    return {"status": "waiting_for_cc", "reason": "offline"}


# ── 机器一侧 ────────────────────────────────────────────────────────────────


def _remember(connection, version, kinds, now):
    try:
        pearnly_state.store().set_value(PRESENCE + connection, {"version": version, "kinds": kinds,
                                                                "at": now.isoformat()}, expires_in_sec=PRESENCE_TTL)
    except Exception:
        # 记不下「来过」不挡领取（只影响工具判断电脑在不在）
        frappe.log_error(title="Cowork companion request presence failed", message=frappe.get_traceback()[-1500:])


def _close(row, status, code, now):
    row.update({"status": status, "error_code": code, "completed_at": now, "lease_handle": None})
    write(row)
    _later(row.name)


def _expire(connection, now):
    for (name,) in frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE connection = %s
            AND status IN ('requested', 'leased') ORDER BY requested_at, name FOR UPDATE""", (connection,)):
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


def lease_next(context, body) -> dict:
    wanted = contracts.next_request(body)
    now = _now()
    adapter = frappe.db.get_value(CONNECTION, context.connection, "adapter") or ""
    kinds = contracts.allowed_kinds(adapter, wanted["kinds"])
    _remember(context.connection, wanted["version"], kinds, now)
    _expire(context.connection, now)
    if not kinds:
        return {"request": None}
    marks = ", ".join(["%s"] * len(kinds))
    for _attempt in range(contracts.OPEN_PER_ACTOR):
        found = frappe.db.sql(f"""SELECT name FROM `tab{REQUEST}` WHERE connection = %s AND status = 'requested'
            AND kind IN ({marks}) ORDER BY requested_at, name LIMIT 1 FOR UPDATE""", (context.connection, *kinds))
        if not found:
            return {"request": None}
        row = read(found[0][0])
        if not _actor_ready(row):
            _close(row, "failed", "actor_not_allowed", now)
            continue
        handle = secrets.token_hex(16)
        seconds = contracts.LEASE_SECONDS[row.kind]
        row.update({"status": "leased", "lease_handle": handle, "lease_credential": context.credential_id,
                    "leased_at": now, "lease_expires_at": now + timedelta(seconds=seconds),
                    "companion_version": wanted["version"], "received_bytes": 0})
        write(row)
        return {"request": {"id": row.name, "kind": row.kind, "params": _json(row.params_json), "handle": handle,
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
