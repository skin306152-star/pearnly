"""CC 桌面版共用机器通道：按需请求（多产品共用，ADR-025）。产品请电脑读一样东西交回来（目前是 ``express_masters``：一本账套的
整套主档、各仓结存与准备载荷要用的账套资料）。

线上形状与 Cowork 的按需请求完全相同（共用的 `wire.request_contracts`）：电脑领一件（带一次性 handle）、分块交回、最后一块带
大小与 sha256；结果存站点私有目录（`store`），产品取走即删。读账套的请求带账套键：共享种类（账套的公共资料）够得到它的电脑
谁先来谁读；其余只由发起人登录过 CC 的、够得到它的电脑读。产品的固定顺序：:func:`find` 同一请求号还没了结的那件 → 没有就
:func:`waiting` 看有没有电脑能做 → :func:`create`；之后 :func:`status` / :func:`result` / :func:`drop`。
"""
import json
import secrets
from datetime import timedelta

import frappe
from frappe import _

from pearnly_core.cc_desktop import books, devices, documents, products, store
from pearnly_core.cc_desktop.wire import request_contracts as contracts
from pearnly_core.cc_desktop.wire import route_contracts

REQUEST = documents.REQUEST
LEASE_SCAN = 50
FIELDS = ("name", "request_key", "kind", "actor", "book_key", "params", "status", "code", "requested_at",
          "lease_handle", "lease_device", "lease_expires_at", "received_bytes", "result_bytes", "result_sha256",
          "meta_json", "completed_at")


def _now():
    return devices.now()


def _row(name):
    rows = frappe.get_all(REQUEST, filters={"name": name}, fields=list(FIELDS), limit=1)
    return rows[0] if rows else None


def _view(row):
    status, code = contracts.effective(row.status, row.code, row.requested_at, row.lease_expires_at, _now())
    return {"request": row.name, "kind": row.kind, "status": status, "code": code, "book_key": row.book_key,
            "requested_at": row.requested_at, "completed_at": row.completed_at}


def find(kind, request_key):
    """同一请求号还没了结（排队 / 在读 / 读好了没取走）的那件；没有回 None。"""
    rows = frappe.get_all(REQUEST, filters={"request_key": request_key, "kind": kind}, fields=list(FIELDS), limit=1)
    if not rows:
        return None
    view = _view(rows[0])
    return view if view["status"] in ("requested", "leased", "done") else None


def waiting(kind, book_key, actor=None):
    """此刻没有电脑能做这件就说为什么（不建请求）：``missing``（发起人一台登录过 CC 的电脑都没有，非共享种类）、
    ``unreachable``（没有一台够得到这份账套）、``offline``（够得到的都关着）、``update_needed``（CC 桌面版太旧）。能做回 None。"""
    peers = devices.peers()
    reach = (books.reachers([book_key], writable_only=False) or {}).get(book_key) or []
    if not contracts.shared(kind):
        mine = [name for name, peer in peers.items() if route_contracts.serves(peer.users, actor)]
        if not mine:
            return {"reason": "missing"}
        reach = [name for name in reach if name in mine]
    if not reach:
        return {"reason": "unreachable"}
    online = [name for name in reach if (peers.get(name) or route_contracts.Peer()).online]
    if not online:
        return {"reason": "offline"}
    if not any(contracts.supports(devices.version_of(name), contracts.minimum(kind)) for name in online):
        return {"reason": "update_needed"}
    return None


def create(kind, request_key, params, *, book_key, actor):
    """建一件（同一请求号已经有了结的旧行就换掉）。回 ``{request, status}``。调用方先 :func:`find`、:func:`waiting`。"""
    kind = contracts.kind(kind)
    if kind not in products.kinds():
        frappe.throw(_("cc_desktop.request_invalid"))
    request_key = contracts.request_key(request_key)
    params = contracts.params(params)
    old = frappe.get_all(REQUEST, filters={"request_key": request_key}, pluck="name")
    for name in old:
        store.drop(name)
        documents.remove(REQUEST, name)
    _clean()
    name = secrets.token_hex(16)
    documents.new(REQUEST, name, {"request_key": request_key, "kind": kind, "actor": actor, "book_key": book_key,
                                  "params": json.dumps(params, ensure_ascii=False, sort_keys=True), "status": "requested",
                                  "requested_at": _now(), "received_bytes": 0})
    return {"request": name, "status": "requested"}


def _clean():
    """一周前的旧请求（含没取走的结果）顺手删掉，不另开定时任务。"""
    edge = _now() - timedelta(seconds=contracts.KEEP_SECONDS)
    for name in frappe.get_all(REQUEST, filters={"requested_at": ["<", edge]}, pluck="name", limit=200):
        store.drop(name)
        documents.remove(REQUEST, name)


def status(name):
    row = _row(name)
    return _view(row) if row else None


def result(name):
    """读好的结果 ``(字节, meta)``；没读好回 (None, {})。"""
    row = _row(name)
    if not row or row.status != "done":
        return None, {}
    try:
        meta = json.loads(row.meta_json or "{}")
    except ValueError:
        meta = {}
    return store.read(name), meta


def drop(name):
    store.drop(name)
    documents.remove(REQUEST, name)


# ── 电脑领请求、交结果 ─────────────────────────────────────────────────────────


def _close(doc, state, code, moment):
    doc.update({"status": state, "code": code, "completed_at": moment, "lease_handle": None})
    documents.persist(doc)
    if state != "done":
        store.drop(doc.name)


def _expire(moment):
    edge = moment - timedelta(seconds=contracts.CLAIM_SECONDS)
    for row in frappe.get_all(REQUEST, filters={"status": ["in", ["requested", "leased"]]},
                              fields=["name", "status", "requested_at", "lease_expires_at"], limit=200):
        if (row.status == "requested" and row.requested_at and row.requested_at < edge) or (
                row.status == "leased" and (not row.lease_expires_at or row.lease_expires_at <= moment)):
            doc = frappe.get_doc(REQUEST, row.name, for_update=True)
            if doc.status in ("requested", "leased"):
                _close(doc, "expired", "not_claimed" if doc.status == "requested" else "lease_expired", moment)


def lease_next(context, body):
    wanted = contracts.next_request(body)
    context = devices.revalidate(context)
    moment = _now()
    devices.touch(context)
    kinds = [kind for kind in wanted["kinds"] if kind in products.kinds()]
    _expire(moment)
    if not kinds:
        return {"request": None}
    reach = books.readable(context.device)
    if not reach:
        return {"request": None}
    users = devices.signed_in(context.device)
    marks, keys = ", ".join(["%s"] * len(kinds)), sorted(reach)
    rows = frappe.db.sql(f"""SELECT name, kind, actor, book_key FROM `tab{REQUEST}` WHERE status = 'requested'
        AND kind IN ({marks}) AND book_key IN ({', '.join(['%s'] * len(keys))}) ORDER BY requested_at, name
        LIMIT {LEASE_SCAN}""", (*kinds, *keys), as_dict=True)
    for candidate in rows:
        if not contracts.shared(candidate.kind) and not route_contracts.serves(users, candidate.actor):
            continue
        doc = frappe.get_doc(REQUEST, candidate.name, for_update=True)
        if doc.status != "requested":
            continue  # 别的电脑刚领走
        handle = secrets.token_hex(16)
        seconds = contracts.LEASE_SECONDS[doc.kind]
        doc.update({"status": "leased", "lease_handle": handle, "lease_device": context.device, "leased_at": moment,
                    "lease_expires_at": moment + timedelta(seconds=seconds), "companion_version": wanted["version"],
                    "received_bytes": 0})
        documents.persist(doc)
        spelling = reach[doc.book_key]
        params = contracts.localize(json.loads(doc.params or "{}"), spelling.path, spelling.root)
        return {"request": {"id": doc.name, "kind": doc.kind, "params": params, "handle": handle,
                            "lease_seconds": seconds, "chunk_bytes": contracts.CHUNK_BYTES,
                            "max_bytes": contracts.MAX_BYTES[doc.kind]}}
    return {"request": None}


def complete(context, body):
    data = contracts.complete_request(body)
    context = devices.revalidate(context)
    moment = _now()
    found = frappe.db.sql(f"SELECT name FROM `tab{REQUEST}` WHERE name = %s FOR UPDATE", (data["request"],))
    doc = frappe.get_doc(REQUEST, found[0][0]) if found else None
    if (doc is None or doc.status != "leased" or doc.lease_handle != data["handle"]
            or doc.lease_device != context.device or not doc.lease_expires_at or doc.lease_expires_at <= moment):
        raise contracts.ContractError("companion.on_demand_stale")
    if not data["ok"]:
        _close(doc, "failed", data["code"], moment)
        return {"status": "failed"}
    received = int(doc.received_bytes or 0)
    if data["offset"] != received:
        return {"ok": False, "code": "companion.on_demand_offset", "received": received}
    total = received + len(data["content"])
    if total > contracts.MAX_BYTES[doc.kind]:
        _close(doc, "failed", "too_large", moment)
        return {"ok": False, "code": "companion.on_demand_too_large"}
    try:
        store.write(doc.name, received, data["content"])
    except ValueError:
        _close(doc, "failed", "stored_part_missing", moment)
        return {"ok": False, "code": "companion.on_demand_stale"}
    doc.received_bytes = total
    doc.lease_expires_at = max(doc.lease_expires_at, moment + timedelta(seconds=contracts.LEASE_SECONDS[doc.kind]))
    if data["final"]:
        size, digest = store.digest(doc.name)
        if data["size"] != total or size != total or digest != data["sha256"]:
            _close(doc, "failed", "content_mismatch", moment)
            return {"ok": False, "code": "companion.on_demand_mismatch"}
        doc.update({"status": "done", "result_bytes": total, "result_sha256": digest, "completed_at": moment,
                    "meta_json": json.dumps(data["meta"], ensure_ascii=False, sort_keys=True), "lease_handle": None})
    documents.persist(doc)
    return {"status": doc.status, "received": total}
