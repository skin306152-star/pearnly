"""Complete per-reader snapshots, atomically replaced only after successful reads.

No function in this module logs into DMS. A missing/incomplete snapshot is not an
empty customer list. Native DocType storage survives Redis eviction/restarts;
generic customer CRUD and sharing are not granted. All timestamps are UTC epoch.
"""
import copy
from contextlib import contextmanager
import hashlib
import json
import math
import re
import time
import uuid

import frappe

from pearnly_core.endpoints import read_password
from pearnly_core.session_lock import dms_login_key
from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.contracts.endpoint import endpoint_name

DT = "DMS Read Snapshot"
INTERVAL = 300
MAX_BYTES = 8 * 1024 * 1024
MAX_REQUESTS = 32
REQUEST_TTL = 7 * 86400
#: 变更流水的水位（模块事件总数 + 已处理的流水号）。不是快照，只走内部读写。
CHANGE_DATASET = "changes"
AUTH_ERRORS = {"ERR_DMS_AUTH", "ERR_DMS_ADMIN_AUTH", "ERR_DMS_NO_ADMIN_CREDS",
               "ERR_DMS_QUERY_FORBIDDEN", "ERR_DMS_QUERY_SCOPE", "ERR_NO_CREDS"}


class SnapshotUnavailable(DmsClientError):
    def __init__(self, code="ERR_DMS_SNAPSHOT_PENDING"):
        super().__init__("A complete DMS snapshot is not available", code)


def _current_password(name, field, database):
    """Current locking Auth read on the independent cache connection."""
    from frappe.utils.password import decrypt
    from pearnly_core.endpoints import _quiet_decrypt
    rows = database.sql("SELECT password FROM __Auth WHERE doctype=%s AND name=%s "
        "AND fieldname=%s AND encrypted=1 LOCK IN SHARE MODE",
        ("Pearnly ERP Endpoint", name, field))
    if not rows or not rows[0][0]:
        return ""
    try:
        with _quiet_decrypt():
            return decrypt(rows[0][0])
    except frappe.ValidationError:
        return ""


def scope(endpoint, *, reader="authority", current=False, _database=None):
    """Resolve current credentials, never accept a caller-provided scope hash."""
    if reader not in ("authority", "sales"):
        raise ValueError("Unknown DMS snapshot reader")
    if current and _database is None:
        raise ValueError("Current scope locks require an independent cache connection")
    database = _database or frappe.db
    name = endpoint_name(endpoint)
    if current:
        rows = database.sql("SELECT enabled, adapter, system_url, username, admin_username, owner_user "
            "FROM `tabPearnly ERP Endpoint` WHERE name=%s", name, as_dict=True)
        row = rows[0] if rows else None
    else:
        row = database.get_value("Pearnly ERP Endpoint", name,
            ["enabled", "adapter", "system_url", "username", "admin_username", "owner_user"], as_dict=True)
    if current and row:
        # Lock the operator before its owned Endpoint, following sync_operator's
        # update order. Hold only during the short local publish, never during IO.
        if row.owner_user:
            database.sql("SELECT name FROM `tabDMS Operator` WHERE user=%s LOCK IN SHARE MODE", row.owner_user)
        rows = database.sql("SELECT enabled, adapter, system_url, username, admin_username, owner_user "
            "FROM `tabPearnly ERP Endpoint` WHERE name=%s LOCK IN SHARE MODE", name, as_dict=True)
        row = rows[0] if rows else None
    if not row or not row.enabled or row.adapter != "mrerp_dms":
        raise SnapshotUnavailable("ERR_NO_CREDS")
    owner = str(row.owner_user or "")
    operator = None
    if owner:
        if current:
            rows = database.sql("SELECT status, can_query_dms, modified FROM `tabDMS Operator` "
                "WHERE user=%s LOCK IN SHARE MODE", owner, as_dict=True)
            operator = rows[0] if len(rows) == 1 else None
            users = database.sql("SELECT enabled FROM `tabUser` WHERE name=%s LOCK IN SHARE MODE", owner)
            enabled = bool(users and users[0][0])
        else:
            operator = frappe.db.get_value("DMS Operator", {"user": owner},
                ["status", "can_query_dms", "modified"], as_dict=True)
            enabled = frappe.db.get_value("User", owner, "enabled")
        if (not enabled or not operator
                or operator.status != "active"):
            raise SnapshotUnavailable("ERR_NO_CREDS")
    def password_value(field):
        return _current_password(name, field, database) if current else read_password("Pearnly ERP Endpoint", name, field)
    admin_pw = password_value("admin_password") if reader == "authority" else ""
    use_admin = bool(row.admin_username and admin_pw)
    username = row.admin_username if use_admin else row.username
    password = admin_pw if use_admin else password_value("password")
    if not username or not password:
        raise SnapshotUnavailable("ERR_NO_CREDS")
    if reader == "sales" and (not owner or not operator.can_query_dms):
        raise SnapshotUnavailable("ERR_DMS_QUERY_FORBIDDEN")
    identity = [frappe.local.site, reader, dms_login_key(row.system_url, username), password]
    if reader == "sales":
        identity += [name, owner, str(operator.modified)]
    return hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()


def _name(scope_key, dataset):
    if not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,95}", str(dataset)):
        raise ValueError("Invalid snapshot dataset")
    return "DMSR-" + hashlib.sha256((scope_key + "\0" + dataset).encode()).hexdigest()[:40]


def _dump(data):
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(raw.encode()) > MAX_BYTES:
        raise DmsClientError("DMS snapshot exceeds storage budget", "ERR_DMS_SNAPSHOT_INCOMPLETE")
    return raw


def _ensure(name, scope_key, dataset, *, _database=None, exclusive=False):
    # SQL writes are private service operations. User-facing Document mutations
    # are denied by the controller, including import/rename/share bypasses.
    database = _database or frappe.db
    insert = "INSERT" if exclusive else "INSERT IGNORE"
    suffix = " ON DUPLICATE KEY UPDATE name=VALUES(name)" if exclusive else ""
    database.sql(f"""{insert} INTO `tab{DT}`
        (name, creation, modified, owner, modified_by, docstatus, scope_key, dataset,
         source_started_at, completed_at, requested_at)
        VALUES (%s, UTC_TIMESTAMP(6), UTC_TIMESTAMP(6), 'Administrator', 'Administrator',
                0, %s, %s, 0, 0, 0){suffix}""", (name, scope_key, dataset))


def read(endpoint, dataset="directory", *, reader="authority", max_age=None, refresh_interval=INTERVAL):
    key = scope(endpoint, reader=reader)
    rejection = frappe.db.get_value(DT, _name(key, "authorization"), "last_error")
    if rejection in AUTH_ERRORS:
        raise SnapshotUnavailable(rejection)
    row = frappe.db.get_value(DT, _name(key, dataset),
        ["payload", "source_started_at", "completed_at", "generation", "last_error"], as_dict=True)
    if not row or not row.generation or not row.payload:
        raise SnapshotUnavailable()
    if row.last_error in AUTH_ERRORS:
        # A known authorization rejection cannot use the transient stale fallback.
        raise SnapshotUnavailable(row.last_error)
    # Manual refresh: data age is a visible warning, not an implicit login or a
    # global 30-minute outage. Callers can still require a bounded-age result.
    maximum = int(max_age) if max_age is not None else None
    age = max(0, time.time() - float(row.source_started_at or 0))
    if maximum is not None and age > max(INTERVAL, maximum):
        raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_STALE")
    result = {"data": json.loads(row.payload), "as_of": float(row.source_started_at),
              "completed_at": float(row.completed_at), "generation": row.generation,
              "stale": age > refresh_interval or bool(row.last_error)}
    status_key = "dms_snapshot_geo_status" if dataset == "geo" else "dms_snapshot_status"
    previous = frappe.flags.get(status_key)
    status = {k: result[k] for k in ("as_of", "completed_at", "generation", "stale")}
    if not previous or status["as_of"] < previous["as_of"]:
        frappe.flags[status_key] = status
    return result


def publish(endpoint, dataset, data, source_started_at, *, reader="authority", expected_scope=None):
    """The caller must finish and validate every page before publishing.

    A credential change in flight discards the old result. A late older collector
    cannot replace newer data. Customer write-through overlays win over scans
    started before the verified write. No global timestamp is advanced by edits.
    """
    stamp = float(source_started_at)
    if not math.isfinite(stamp) or stamp <= 0 or stamp > time.time() + 1:
        raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
    key = scope(endpoint, reader=reader)
    if expected_scope is not None and key != expected_scope:
        raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
    name = _name(key, dataset)
    _ensure(name, key, dataset)
    rows = frappe.db.sql(f"SELECT source_started_at, overlays FROM `tab{DT}` WHERE name=%s FOR UPDATE",
                         name, as_dict=True)
    if float(rows[0].source_started_at or 0) > float(source_started_at):
        return False
    data = copy.deepcopy(data)
    overlays = json.loads(rows[0].overlays or "{}")
    if dataset in ("directory", "customers"):
        required = ("masters", "paints", "customers") if dataset == "directory" else ("customers",)
        if not all(isinstance(data.get(k), dict) for k in required):
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
        for cid, item in overlays.items():
            if float(item["at"]) >= float(source_started_at):
                data["customers"][cid] = item["fields"]
            elif cid not in data["customers"]:
                raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
        # A missing ID can be a newly-created customer still hidden by DMS search.
        # The collector directly rereads known IDs; until then retain its verified
        # overlay rather than erasing a successful write from the local directory.
        overlays = {cid: {**item, "fields": data["customers"][cid]} for cid, item in overlays.items()}
    elif dataset in ("vehicles", "company"):
        required = ("masters", "catalogues", "paints") if dataset == "vehicles" else ("masters", "catalogues")
        if not all(isinstance(data.get(k), dict) for k in required) or not data["catalogues"]:
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
    elif dataset == "masters":
        if not all(isinstance(data.get(k), dict) for k in ("masters", "paints")):
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
    elif dataset == "geo":
        from pearnly_dms.contracts.geo_matching import complete_tree
        tree = {key: data.get(key) for key in ("provinces", "districts", "subdistricts", "zipcodes")}
        if not complete_tree(tree) or ("catalogues" in data and not isinstance(data["catalogues"], dict)):
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_INCOMPLETE")
    frappe.db.set_value(DT, name, {"payload": _dump(data), "overlays": _dump(overlays),
        "source_started_at": float(source_started_at), "completed_at": time.time(),
        "generation": uuid.uuid4().hex, "last_error": ""})
    # A verified complete collection proves this reader can authenticate again.
    # A transient failure never clears a known denial from another dataset.
    frappe.db.set_value(DT, _name(key, "authorization"), "last_error", "")
    return True


def stage(endpoint, dataset="geo"):
    """Private resumable work, never visible through read() without a generation."""
    key = scope(endpoint)
    raw = frappe.db.get_value(DT, _name(key, dataset), "staging")
    return json.loads(raw) if raw else None


def checkpoint(endpoint, data, *, dataset="geo", expected_scope):
    key = scope(endpoint)
    if key != expected_scope:
        raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
    name = _name(key, dataset)
    _ensure(name, key, dataset)
    frappe.db.set_value(DT, name, "staging", _dump(data) if data is not None else "")


def failed(endpoint, dataset, code, *, reader="authority", expected_scope=None):
    key = scope(endpoint, reader=reader)
    if expected_scope is not None and key != expected_scope:
        return
    name = _name(key, dataset)
    _ensure(name, key, dataset)
    # Never record upstream HTML, credentials, customer data or exception repr.
    safe = str(code) if re.fullmatch(r"[A-Z_0-9]{1,100}", str(code)) else "ERR_DMS_SNAPSHOT_INCOMPLETE"
    frappe.db.set_value(DT, name, "last_error", safe)
    if safe in AUTH_ERRORS:
        guard = _name(key, "authorization")
        _ensure(guard, key, "authorization")
        frappe.db.set_value(DT, guard, "last_error", safe)


def overlay_customer_ids(endpoint):
    key = scope(endpoint)
    raw = frappe.db.get_value(DT, _name(key, "customers"), "overlays")
    return tuple(json.loads(raw or "{}"))


def change_state(endpoint):
    """变更流水的水位；没有就返回 None（＝还没建过基线，不能做增量判断）。

    只读本地元数据，不登录第三方。存的是 `staging` 列 —— 那一列本来就是
    "按数据集的私有中间状态"，而 `changes` 这个数据集没有采集器，不会互相覆盖。
    """
    key = scope(endpoint)
    raw = frappe.db.get_value(DT, _name(key, CHANGE_DATASET), "staging")
    return json.loads(raw) if raw else None


def _furthest(stored, incoming):
    """两个水位取更靠前的那个（并发时另一个进程可能已经推进过）。"""
    merged = dict(incoming)
    for field in ("actor", "seeded_at"):
        if not merged.get(field) and stored.get(field):
            merged[field] = stored[field]
    stored_modules = stored.get("modules") or {}
    incoming_modules = incoming.get("modules") or {}
    modules = {}
    for module in set(stored_modules) | set(incoming_modules):
        old, new = stored_modules.get(module) or {}, incoming_modules.get(module) or {}
        modules[module] = new if int(new.get("seq") or 0) >= int(old.get("seq") or 0) else old
    merged["modules"] = modules
    stored_pending = stored.get("pending") or {}
    incoming_pending = incoming.get("pending") or {}
    merged["pending"] = {
        dataset: max(stored_pending.get(dataset, 0), incoming_pending.get(dataset, 0))
        for dataset in set(stored_pending) | set(incoming_pending)}
    return merged


def merge_change_state(endpoint, incoming, *, base, expected_scope=None):
    """把水位写回。基线变了（另一个进程先写了）就按更靠前的合并，绝不回退。

    外部 IO 已经做完才调这里；锁内只做本地读写。私有短事务、独立提交，
    不牵动调用方（操作员那一笔）的业务事务。
    """
    with _cache_connection() as database:
        key = scope(endpoint, current=True, _database=database)
        if expected_scope is not None and key != expected_scope:
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
        name = _name(key, CHANGE_DATASET)
        _ensure(name, key, CHANGE_DATASET, _database=database, exclusive=True)
        rows = database.sql(f"SELECT staging FROM `tab{DT}` WHERE name=%s FOR UPDATE",
                            name, as_dict=True)
        stored = json.loads(rows[0].staging or "{}")
        merged = incoming if stored == (base or {}) else _furthest(stored, incoming)
        database.sql(f"UPDATE `tab{DT}` SET staging=%s, modified=UTC_TIMESTAMP(6) WHERE name=%s",
                     (_dump(merged), name))
        return merged


def customer_index(endpoint):
    """本地缓存的 身份证号 → 客户号（归一化后唯一才算入索引）。

    **一次读、一次解析**：一轮增量可能点名好几条客户，逐条去解析整份 payload
    在大租户上是白花的开销。身份证号重复的客户不进索引 —— 有歧义宁可去 DMS 反查。
    """
    from pearnly_dms.contracts.id_validate import normalize_thai_id

    key = scope(endpoint)
    raw = frappe.db.get_value(DT, _name(key, "customers"), "payload")
    index, ambiguous = {}, set()
    for cid, item in ((json.loads(raw).get("customers") if raw else {}) or {}).items():
        people = normalize_thai_id((item or {}).get("people_id"))
        if not people:
            continue
        if people in index:
            ambiguous.add(people)
        index[people] = cid
    for people in ambiguous:
        index.pop(people, None)
    return index


@contextmanager
def _cache_connection():
    """A short cache-only transaction, isolated from the caller's business log."""
    from frappe.database import get_db
    conf = frappe.conf
    database = get_db(socket=conf.db_socket, host=conf.db_host, port=conf.db_port,
        user=conf.db_user or conf.db_name, password=conf.db_password, cur_db_name=conf.db_name)
    try:
        # This is a private connection, never the user's business transaction.
        # Current reads after waiting on the per-scope guard must see its last
        # committer instead of a previously established RR snapshot.
        database.sql("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
        database.sql("SET SESSION innodb_lock_wait_timeout=2")
        yield database
        database.commit()
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


def verified_customer(endpoint, customer_id, fields, *, expected_scope=None):
    """Persist only an externally verified customer; never commit caller state.

    Neither native deadlocks nor stale RR views may reverse pending business
    evidence. Cache publication is immediate on a separate short connection.
    """
    with _cache_connection() as database:
        _verified_customer(database, endpoint, customer_id, fields, expected_scope=expected_scope)


def _verified_customer(database, endpoint, customer_id, fields, *, expected_scope):
    key = scope(endpoint, current=True, _database=database)
    if expected_scope is not None and key != expected_scope:
        raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_SCOPE_CHANGED")
    name = _name(key, "customers")
    _ensure(name, key, "customers", _database=database, exclusive=True)
    rows = database.sql(f"SELECT payload, overlays, generation FROM `tab{DT}` WHERE name=%s FOR UPDATE", name, as_dict=True)
    cid = str(customer_id)
    fields = {**fields, "customer_id": cid}
    overlays = json.loads(rows[0].overlays or "{}")
    overlays[cid] = {"at": time.time(), "fields": fields}
    values = {"overlays": _dump(overlays)}
    if rows[0].payload:
        data = json.loads(rows[0].payload)
        data["customers"][cid] = fields
        values["payload"] = _dump(data)
        values["generation"] = uuid.uuid4().hex
    database.sql(f"UPDATE `tab{DT}` SET overlays=%s, payload=%s, generation=%s, "
        "modified=UTC_TIMESTAMP(6) WHERE name=%s",
        (values["overlays"], values.get("payload", rows[0].payload),
         values.get("generation", rows[0].generation), name))


def request(endpoint, dataset, params, *, reader="sales"):
    raw = _dump(params)
    if len(raw) > 2048:
        raise ValueError("Snapshot query parameters too large")
    # Serialize registrations per employee through a native transaction lock.
    # A current (locking) read sees the previous registrant after waiting even
    # when this request already has a MariaDB REPEATABLE READ snapshot.
    with _cache_connection() as database:
        key = scope(endpoint, reader=reader, current=True, _database=database)
        name = _name(key, dataset)
        guard = _name(key, "request_guard")
        _ensure(guard, key, "request_guard", exclusive=True, _database=database)
        active = database.sql(f"SELECT dataset FROM `tab{DT}` WHERE scope_key=%s "
            "AND requested_at>%s AND COALESCE(request_json, '')!='' FOR UPDATE",
            (key, time.time() - REQUEST_TTL), pluck=True)
        if len(active) >= MAX_REQUESTS and dataset not in active:
            raise SnapshotUnavailable("ERR_DMS_SNAPSHOT_REQUEST_LIMIT")
        _ensure(name, key, dataset, _database=database)
        database.sql(f"UPDATE `tab{DT}` SET request_json=%s, requested_at=%s, "
            "modified=UTC_TIMESTAMP(6) WHERE name=%s", (raw, time.time(), name))


def requested(endpoint, *, reader="sales"):
    key = scope(endpoint, reader=reader)
    rows = frappe.get_all(DT, filters={"scope_key": key, "requested_at": [">", time.time() - REQUEST_TTL]},
        fields=["dataset", "request_json"], order_by="requested_at desc", limit_page_length=MAX_REQUESTS)
    return [{"dataset": row.dataset, "params": json.loads(row.request_json)} for row in rows if row.request_json]
