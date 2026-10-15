"""Express 连接这个「池」里的账套登记、可达与账套资料（ADR-021，方案 2.2–2.4）。

- 登记 `Cowork Express Account Set`：一份物理账套一行，按（连接，规范键）唯一。**规范键与规范根登记后永不改**
  （账套锁按它们算，`push.native.contracts`）；只有文件号、网络路径、显示用的属性跟着最新上报变。
- 可达 `Cowork Express Reach`：哪台电脑（不透明的设备编号）够得到哪份登记、用它本机的哪种写法、能不能写，
  状态 ``ok`` / ``pending_identity``（待认定，指向它在等的那份，不派写入）/ ``stale``（写前文件号对不上，第 3 批）。
- 账套资料 `Cowork Express Account Facts`：按（连接，规范键）存的主档（科目、商品、客户、供应商、部门、存货科目组、
  目录指纹），不含版本号；每条连接留 :data:`FACTS_PER_CONNECTION` 份，被未了结推送记录引用的不淘汰。

认定规则是纯函数 `targets.identity.resolve`；这里只读写本域这三张表。调用方已锁连接行，负责提交；加锁顺序
Connection → Credential → 本模块各行（及目录快照、刷新行）→ Device。没有 RPC、没有外部 IO。
"""
from __future__ import annotations

from datetime import timedelta

import frappe
from frappe import _

from pearnly_cowork.targets import identity as rules
from pearnly_cowork.targets.identity import normalize_express_account_key as identity
from pearnly_cowork.targets.projection import MAX_ACCOUNT_SETS
from . import access, model, state
from .member import Member

ACCOUNT_SET, REACH, FACTS = access.POOL_TYPES
OK, PENDING_IDENTITY, STALE = "ok", "pending_identity", "stale"
FACTS_PER_CONNECTION = 20
# 待认定的退出条件（方案 2.4 第 5 条）：够得到那份旧登记的电脑都 7 天没报到（或被移除）就不再等
PENDING_EXIT = timedelta(days=7)
# 名单没变的心跳只报「没变」：可达行的报到时间隔一天才补记一次，不为它每分钟写库
TOUCH_EVERY = timedelta(days=1)
# 账套资料里存的键：上报的主档，不含两个版本号（版本属于电脑，不属于账套）
FACT_KEYS = model.REPORTED_KEYS - {"max_payload_version", "companion_version"}
# 资料内容没变时，隔这么久才补记一次读到的时间（主档按期上报，`account_masters` 据此判断要不要再读）
FACTS_TOUCH_EVERY = timedelta(minutes=10)
# 属于某台电脑的写法，不进登记上全池共用的显示属性
_PER_COMPUTER = frozenset(("path", "root", "uid", "share", "writable"))
_REACH_FIELDS = ("path", "root", "registry_row", "writable", "state", "uid", "share", "content_key", "details_json")

Q_REGISTRATIONS = (f"SELECT name, account_key, root_key, path, root, label, attributes_json, uid, location, content_key, "
                   f"scope_device FROM `tab{ACCOUNT_SET}` WHERE connection=%s ORDER BY creation, name")
Q_REACH = (f"SELECT name, device, account_key, path, root, registry_row, writable, state, uid, share, content_key, "
           f"details_json, seen_at FROM `tab{REACH}` WHERE connection=%s ORDER BY creation, name")
Q_MINE = (f"SELECT account_key, path, state FROM `tab{REACH}` WHERE connection=%s AND device=%s "
          f"AND state<>'pending_identity'")
Q_REACHERS = f"SELECT device, writable FROM `tab{REACH}` WHERE connection=%s AND account_key=%s AND state='ok'"
Q_REACHERS_OF = (f"SELECT account_key, device, writable FROM `tab{REACH}` WHERE connection=%s AND account_key IN %s "
                 f"AND state='ok'")
Q_REGISTRATIONS_OF = (f"SELECT account_key, uid, content_key, last_writer_device, last_written_at FROM `tab{ACCOUNT_SET}` "
                      f"WHERE name IN %s")
Q_WRITABLE = (f"SELECT account_key, path, root, registry_row FROM `tab{REACH}` WHERE connection=%s AND device=%s "
              f"AND state='ok' AND writable=1")
Q_READABLE = (f"SELECT account_key, path, root, registry_row FROM `tab{REACH}` WHERE connection=%s AND device=%s "
              f"AND state='ok' AND account_key IN %s")
Q_READABLE_KEYS = f"SELECT DISTINCT account_key FROM `tab{REACH}` WHERE connection=%s AND device=%s AND state='ok'"
Q_WRITERS = f"SELECT DISTINCT device FROM `tab{REACH}` WHERE connection=%s AND state='ok' AND writable=1"
Q_ANY_REACH = f"SELECT name FROM `tab{REACH}` WHERE connection=%s LIMIT 1"
Q_DEVICES = f"SELECT DISTINCT device FROM `tab{REACH}` WHERE connection=%s"
Q_PENDING = f"SELECT name FROM `tab{REACH}` WHERE connection=%s AND device=%s AND state='pending_identity' LIMIT 1"
Q_TOUCH = f"UPDATE `tab{REACH}` SET seen_at=%s WHERE connection=%s AND device=%s AND (seen_at IS NULL OR seen_at<%s)"
Q_FACTS = f"SELECT name, account_key, seen_at FROM `tab{FACTS}` WHERE connection=%s ORDER BY seen_at DESC, name"


def _key(prefix, *parts):
    return prefix + model.fingerprint(list(parts))[:40]


def registration_name(connection, account_key):
    return _key("CWAS-", connection, account_key)


def reach_name(connection, device, account_key):
    return _key("CWAR-", connection, device, account_key)


def facts_name(connection, account_key):
    return _key("CWAF-", connection, account_key)


def _rows(query, values, lock=False):
    return frappe.db.sql(query + (" FOR UPDATE" if lock else ""), values, as_dict=True)


def _details(row):
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    return {key: value for key, value in attributes.items() if key not in _PER_COMPUTER}


def _writable(row):
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    return 1 if attributes.get("writable", True) is not False else 0


# ── 读 ─────────────────────────────────────────────────────────────────────────────


def registration(connection, account_key):
    """这份账套的登记（只读）；没登记回 None。"""
    key = identity(account_key)
    if not connection or not key:
        return None
    return frappe.db.get_value(ACCOUNT_SET, registration_name(connection, key),
                               ["account_key", "root_key", "path", "root", "label", "uid", "location", "content_key",
                                "scope_device", "last_writer_device", "last_written_at"], as_dict=True)


def reaches(connection, device, account_key, root=None):
    """这台电脑够得到这份账套、状态正常、能写；给了 ``root`` 还要它与登记的规范根相同（都非空时才比）。"""
    key = identity(account_key)
    if not connection or not device or not key:
        return False
    row = frappe.db.get_value(REACH, reach_name(connection, device, key), ["state", "writable"], as_dict=True)
    if not row or row.state != OK or not row.writable:
        return False
    if root is None:
        return True
    found = registration(connection, key)
    wanted = identity(root)
    return bool(found) and not (found.root_key and wanted and found.root_key != wanted)


def presence(connection):
    """池里各台电脑此刻的情况：``{设备: {"user", "always_on", "online", "version", "payload_version", "index_pending"}}``
    （`companion.credentials.pool_peers`）。电脑与密钥归 Companion，连接域不依赖它：经 ``cowork_pool_presence`` 钩子取，
    没有钩子回空。只读。"""
    for path in frappe.get_hooks("cowork_pool_presence") or []:
        return frappe.get_attr(path)(connection) or {}
    return {}


def writable_reach(connection, device):
    """这台电脑够得到、状态正常、能写的账套：``{规范键: 可达行（本机写法 path / root / registry_row）}``。"""
    return {row.account_key: row for row in _rows(Q_WRITABLE, (connection, device))}


def readable_reach(connection, device, account_keys):
    """这台电脑够得到（状态正常，不论能不能写）的这几份账套：``{规范键: 可达行（本机写法 path / root）}``。按需读账套
    （回读、科目历史）领取时据此判断能不能领、交给它哪种写法（ADR-021 第 4 批）。只读。"""
    keys = sorted({identity(key) for key in account_keys or () if identity(key)})
    if not connection or not device or not keys:
        return {}
    return {row.account_key: row for row in _rows(Q_READABLE, (connection, device, tuple(keys)))}


def readable_keys(connection, device):
    """这台电脑够得到（状态正常，不论能不能写）的全部账套的规范键。领取按需请求时据此只扫它读得了的读账套请求，
    别的电脑才够得到的一批不占它的扫描名额（第 4 批审查 L1）。只读。"""
    if not connection or not device:
        return set()
    return {row.account_key for row in _rows(Q_READABLE_KEYS, (connection, device)) if row.account_key}


def writer_devices(connection):
    """够得到池里任一份账套、状态正常、能写的电脑。"""
    return {row.device for row in _rows(Q_WRITERS, (connection,))}


def mark_stale(connection, device, account_key):
    """写前核文件号对不上（1.7.0 回 ``ERR_ACCOUNT_UID_MISMATCH``）：这台电脑对这份账套的可达行标 ``stale``，不再派写入，
    直到它下一次整份上报（调用方同时要它整份重报）。回有没有改。调用方已锁连接行。"""
    key = identity(account_key)
    name = reach_name(connection, device, key) if connection and device and key else ""
    if not name or not frappe.db.get_value(REACH, name, "name", for_update=True):
        return False
    record = frappe.get_doc(REACH, name, for_update=True)
    if record.state != OK:
        return False
    record.state = STALE
    access.write(record)
    return True


def note_written(connection, account_key, device, at):
    """这台电脑刚写成这份账套（回执成功）：记在登记上。旧版 CC 桌面版不报主档索引还没重建的账套，按「最近写过」近似黏住
    （`companion.route_contracts.sticky_holder`）。没登记不编。调用方已锁连接行。"""
    key = identity(account_key)
    name = registration_name(connection, key) if connection and key else ""
    if not name or not device or not frappe.db.get_value(ACCOUNT_SET, name, "name", for_update=True):
        return False
    record = frappe.get_doc(ACCOUNT_SET, name, for_update=True)
    record.update({"last_writer_device": str(device)[:140], "last_written_at": at})
    access.write(record)
    return True


def reachers(connection, account_key, *, writable=False):
    """够得到这份账套（状态正常）的电脑。"""
    rows = _rows(Q_REACHERS, (connection, identity(account_key)))
    return sorted({row.device for row in rows if row.writable or not writable})


def reachers_of(connection, account_keys, *, writable=False):
    """:func:`reachers` 一次取多份：``{规范键: [设备…]}``（每份账套都有键，没人够得到是空表）。写账分派每次领取要看
    好几份账套的排头，一条查询取回，不在锁着连接行时逐份查（第 3 批审查）。"""
    keys = sorted({identity(key) for key in account_keys or () if identity(key)})
    result = {key: set() for key in keys}
    if connection and keys:
        for row in _rows(Q_REACHERS_OF, (connection, tuple(keys))):
            if row.writable or not writable:
                result.setdefault(row.account_key, set()).add(row.device)
    return {key: sorted(devices) for key, devices in result.items()}


def registrations_of(connection, account_keys):
    """:func:`registration` 一次取多份（分派要的几列：文件号、内容键、最近写它的电脑与时刻）：``{规范键: 行}``，没登记的
    不在里面。只读。"""
    keys = sorted({identity(key) for key in account_keys or () if identity(key)})
    if not connection or not keys:
        return {}
    names = tuple(registration_name(connection, key) for key in keys)
    return {row.account_key: row for row in _rows(Q_REGISTRATIONS_OF, (names,))}


def writers(doc, account_key=None, *, peers=None):
    """池里能写（这份 / 任一份）账套的电脑：可达行状态正常、可写（给了账套还要它登记着），这台在 :func:`presence` 里
    （有一把没吊销的密钥、没退役），而且至少一把密钥的签发人此刻仍能用这条连接（:func:`access.usable_by`）。不要求在线（D4）。

    「能不能确认 / 能不能选 Express」（`push.native.service.has_writer`）与池的状态（`companion.pool.state`）都按它，
    不再各算各的（第 3 批审查：签发人被移出使用名单后，一边说没有能写的电脑、另一边照样确认，单子排着没人写）。只读。"""
    key = identity(account_key) if account_key else ""
    if key and not registration(doc.name, key):
        return []
    devices = reachers(doc.name, key, writable=True) if key else writer_devices(doc.name)
    peers = presence(doc.name) if peers is None else peers
    checked = {}

    def valid(issuer):
        if issuer not in checked:
            checked[issuer] = bool(issuer) and access.usable_by(doc, issuer)
        return checked[issuer]

    return [device for device in sorted(devices)
            if device in peers and any(valid(issuer) for issuer in sorted(peers[device].get("issuers") or ()))]


def any_reach(connection):
    """池里有没有任何一台电脑报过账套名单（没有时还谈不上「目录里没有这份」）。"""
    return bool(_rows(Q_ANY_REACH, (connection,)))


def devices(connection):
    """池里报过账套名单的电脑。"""
    return {row.device for row in _rows(Q_DEVICES, (connection,))}


def local_keys(connection, device):
    """这台电脑的 ``{本机写法（规整）: 规范键}``（状态正常或文件号对不上的；待认定的不算它够得到）。"""
    return {identity(row.path): row.account_key for row in _rows(Q_MINE, (connection, device)) if identity(row.path)}


def local_paths(connection, device):
    """这台电脑的 ``{规范键: 本机写法（原样）}``。"""
    return {row.account_key: row.path for row in _rows(Q_MINE, (connection, device)) if row.path}


def canonical(connection, device, local):
    """电脑报来的本机写法 → 规范键：这台电脑状态正常的可达行里用这个写法的那份；没有就回空串（待认定的、文件号对不上的、
    名单里没有的都不算：不把它读到的主档记到别的账套名下，也不按「它选的」把那份账套的读请求交给它）。
    旧版电脑照常有可达行（规范键就是它的路径），不受影响。"""
    key = identity(local)
    if not key:
        return ""
    for row in _rows(Q_MINE, (connection, device)):
        if row.state == OK and identity(row.path) == key:
            return row.account_key
    return ""


def catalog(connection, *, lock=False):
    """池目录 = 各台电脑可达行的并集（方案 2.3 第 3 条）：每份登记一行，属性取登记里的规范写法，
    ``writable`` 取任一台够得到它、状态正常且可写。待认定的可达行不进目录。最多 ``MAX_ACCOUNT_SETS`` 行（:func:`listing`）。"""
    return listing(connection, lock=lock)[0]


def listing(connection, *, lock=False):
    """:func:`catalog` 与截掉了几份：各台电脑的名单各自不超过上限，并集可能超过；超过时按确定的顺序（可写的、有网络路径的
    在前，再按规范键）留前 ``MAX_ACCOUNT_SETS`` 份，不让目录发布失败（那会让整个心跳回滚，这台电脑一直显示离线）。"""
    reach = _rows(Q_REACH, (connection,), lock)
    listed, writable = set(), {}
    for row in reach:
        if row.state in (OK, STALE):
            listed.add(row.account_key)
            writable[row.account_key] = writable.get(row.account_key) or bool(row.state == OK and row.writable)
    rows = [row for row in _rows(Q_REGISTRATIONS, (connection,), lock) if row.account_key in listed]
    if len(rows) > MAX_ACCOUNT_SETS:
        rows.sort(key=lambda row: (not writable[row.account_key], not row.location, row.account_key))
    result = []
    for row in rows[:MAX_ACCOUNT_SETS]:
        attributes = {**(model.parse(row.attributes_json, {}) or {}), "path": row.path or row.account_key,
                      "root": row.root or row.root_key, "writable": bool(writable[row.account_key])}
        result.append({"source_id": row.account_key, "label": row.label or row.account_key,
                       "attributes": {key: value for key, value in attributes.items() if value not in (None, "")}})
    return result, max(0, len(rows) - MAX_ACCOUNT_SETS)


def union_observation(connection, observed_at, *, lock=True):
    """并集目录作为端点观测发布（`snapshots.publish`）；采集者不带版本号，各台电脑轮流上报时内容相同就不出新版本。
    回 ``(观测, 截掉了几份)``。"""
    rows, dropped = listing(connection, lock=lock)
    return ({"adapter": "express", "observed_at": observed_at, "collector": {"kind": "companion"},
             "account_sets": rows, "masters": {}, "form_schema": {"fields": []}, "capabilities": {"actions": []}},
            dropped)


# ── 认定与可达 ───────────────────────────────────────────────────────────────────────


def _registered(registrations, reach, device, now):
    others, live, uids, holders = {}, {}, {}, {}
    for row in reach:
        if row.state == PENDING_IDENTITY:
            continue
        holders.setdefault(row.account_key, set()).add(row.device)
        if row.device == device:
            continue
        others[row.account_key] = True
        if row.seen_at and row.seen_at >= now - PENDING_EXIT:
            live[row.account_key] = True
            if row.uid:
                uids.setdefault(row.account_key, set()).add(row.uid)
    return [rules.Registered(row.account_key, row.root_key or "", row.uid or "", row.location or "",
                             row.content_key or "", others.get(row.account_key, False), live.get(row.account_key, False),
                             row.scope_device or "",
                             bool(row.scope_device and row.scope_device != device
                                  and row.scope_device in holders.get(row.account_key, ())),
                             frozenset(uids.get(row.account_key, ())))
            for row in registrations]


def _root_for(row, member):
    """登记缺规范根时（补丁按推送记录 / 定时任务补的、没有可达行的键）由第一台够得到它的电脑补上；有了就永不改。"""
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    root = attributes.get("root")
    if member.legacy:
        return identity(root)
    location = rules.normalize_location(attributes.get("share"), aliases=member.aliases, own_host=member.host)
    if location:
        return rules.network_root(attributes.get("path") or row.get("source_id"), location, root, member.host,
                                  member.aliases)
    return rules.host_qualified(root, member.host)


def _register(doc, decision, row, member, now):
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    legacy_spelling = decision.rule == 4
    record = frappe.get_doc({
        "doctype": ACCOUNT_SET, "record_key": registration_name(doc.name, decision.account_key),
        "workspace": doc.workspace, "connection": doc.name, "account_key": decision.account_key,
        "root_key": decision.root_key,
        # 显示用的写法：旧版报的路径原样（今天的目录就是这样），新登记的用规范键本身
        "path": str(attributes.get("path") or decision.account_key)[:500] if legacy_spelling else decision.account_key,
        "root": str(attributes.get("root") or "")[:500] if legacy_spelling else decision.root_key,
        "label": str(row.get("label") or "")[:500], "attributes_json": model.dumps(_details(row)),
        "uid": decision.uid, "location": decision.location, "content_key": decision.content_key,
        "scope_device": member.device if decision.local else "", "registered_at": now})
    access.write(record)
    return record


def _refresh_registration(name, decision, row, member, protected=None):
    """认上了：只更新文件号、内容键与显示属性；网络路径只在原来为空时补（同一处换种共享写法不来回改）；规范键永不改，
    规范根只在原来为空、且没有未了结的推送记录引用这份账套时补（``protected()``：那些推送冻结的是空根，账套锁按它算，
    补上了新单就换了一把锁）。只属于一台已经够不到它的电脑（被移除、换了配对码）的本机登记，归这台。"""
    record = frappe.get_doc(ACCOUNT_SET, name, for_update=True)
    values = {"label": str(row.get("label") or record.label or "")[:500],
              "attributes_json": model.dumps({**(model.parse(record.attributes_json, {}) or {}), **_details(row)}),
              "content_key": decision.content_key or record.content_key}
    if decision.uid:
        values["uid"] = decision.uid
    if decision.location and not record.location:
        values["location"] = decision.location
    if record.scope_device and record.scope_device != member.device and not decision.location:
        values["scope_device"] = member.device  # resolve 不会把仍属于另一台的本机登记认给它（``foreign``）
    if not record.root_key and identity(record.account_key) not in _held(protected):
        root = _root_for(row, member)
        if root:
            values["root_key"] = root
            values["root"] = str((row.get("attributes") or {}).get("root") or root)[:500] if member.legacy else root
    changed = {key: value for key, value in values.items() if (record.get(key) or "") != (value or "")}
    if changed:
        record.update(changed)
        access.write(record)
    return bool({"uid", "location", "root_key"} & set(changed))


def _held(protected):
    return {identity(value) for value in (protected() if callable(protected) else protected or ())}


def _reach_values(decision, row):
    attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
    try:
        registry_row = max(0, int(attributes.get("row") or 0))
    except (TypeError, ValueError):
        registry_row = 0
    pending = decision.kind == rules.PENDING
    return {"path": str(attributes.get("path") or row.get("source_id") or "")[:500],
            "root": str(attributes.get("root") or "")[:500], "registry_row": registry_row,
            "writable": _writable(row), "state": PENDING_IDENTITY if pending else OK,
            "uid": rules.clean_uid(attributes.get("uid")), "share": str(attributes.get("share") or "")[:500],
            "content_key": decision.content_key,
            # 待认定的行留着当初报的显示属性：认定后（可能是新登记）不用等下一次整份上报
            "details_json": model.dumps({"label": row.get("label") or "", "attributes": _details(row)})
            if pending else ""}


def _same(record, values):
    return all((record.get(key) if record.get(key) is not None else "") == (value if value is not None else "")
               for key, value in values.items())


def _save_reach(doc, device, key, values, current, now):
    """写一行可达；内容没变只在报到时间旧了一天以上时补记。回「目录要不要重发」。"""
    if current is None:
        access.write(frappe.get_doc({"doctype": REACH, "record_key": reach_name(doc.name, device, key),
                                     "workspace": doc.workspace, "connection": doc.name, "device": device,
                                     "account_key": key, "seen_at": now, **values}))
        return True
    if _same(current, values) and current.seen_at and current.seen_at >= now - TOUCH_EVERY:
        return False
    record = frappe.get_doc(REACH, current.name, for_update=True)
    changed = not _same(current, values)
    record.update({**values, "seen_at": now})
    access.write(record)
    return changed


def apply_report(doc, member, rows, now, *, protected=None):
    """这台电脑整份上报的账套名单（投影行）：逐行认定（`targets.identity.resolve`），新登记、补文件号 / 网络路径，
    再整份替换这台电脑的可达行（名单里没有的删掉；报了空名单只清它自己的，不动别的电脑）。

    回 ``(目录要不要重发, 有没有登记补上了身份)``；后者为真时别的电脑待认定的行要重核（:func:`reconsider`）。
    ``protected()``：被未了结推送记录引用的账套键（它们的空规范根不补）。"""
    registrations = _rows(Q_REGISTRATIONS, (doc.name,), True)
    reach = _rows(Q_REACH, (doc.name,), True)
    names = {row.account_key: row.name for row in registrations}
    mine = {row.account_key: row for row in reach if row.device == member.device}
    own = {identity(row.path): row.account_key for row in mine.values() if row.state != PENDING_IDENTITY and row.path}
    registered = _registered(registrations, reach, member.device, now)
    taken, changed, identified = {}, False, False
    for row in rows or []:
        decision = rules.resolve(row, registered=registered, own_reach=own, legacy=member.legacy, host=member.host,
                                 aliases=member.aliases)
        if decision.account_key in taken:
            continue  # 同一份账套在名单里出现两次（两种写法）：只记第一行
        if decision.kind == rules.NEW:
            record = _register(doc, decision, row, member, now)
            names[decision.account_key] = record.name
            registered.append(rules.Registered(decision.account_key, decision.root_key, decision.uid,
                                               decision.location, decision.content_key,
                                               scope_device=member.device if decision.local else ""))
            changed = identified = True
        elif decision.kind == rules.MATCH:
            if _refresh_registration(names[decision.account_key], decision, row, member, protected):
                identified = True
                registered = [item._replace(uid=decision.uid or item.uid, location=item.location or decision.location)
                              if item.account_key == decision.account_key else item for item in registered]
        taken[decision.account_key] = (decision, row)
    for key, (decision, row) in taken.items():
        changed = _save_reach(doc, member.device, key, _reach_values(decision, row), mine.pop(key, None), now) \
            or changed
    if mine:
        frappe.db.delete(REACH, {"name": ("in", [row.name for row in mine.values()])})
        changed = True
    return changed, identified


def _pending_row(row):
    """把一行待认定的可达行还原成当初上报的投影行（位置、文件号、网络路径在可达行上，显示属性在 ``details_json``）。"""
    details = model.parse(row.details_json, {}) or {}
    attributes = {**(details.get("attributes") or {}), "path": row.path, "root": row.root, "uid": row.uid,
                  "share": row.share, "row": row.registry_row, "writable": bool(row.writable)}
    return {"source_id": identity(row.path), "label": details.get("label") or "", "attributes": attributes}


def has_pending(connection, device):
    return bool(_rows(Q_PENDING, (connection, device)))


def reconsider(doc, member, now, *, others=False, protected=None):
    """重核待认定的可达行：这台电脑自己的（每次心跳，含 7 天退出条件）整条规则重跑；``others`` 时也看别的电脑的，
    但只认文件号 / 网络路径认上的（第 1、2 条），其余留给它们自己下次报到时重核（新登记要用它们的主机名）。
    回目录要不要重发。"""
    registrations = _rows(Q_REGISTRATIONS, (doc.name,), True)
    reach = _rows(Q_REACH, (doc.name,), True)
    names = {row.account_key: row.name for row in registrations}
    changed = False
    for row in reach:
        if row.state != PENDING_IDENTITY or (row.device != member.device and not others):
            continue
        mine = row.device == member.device
        registered = _registered(registrations, reach, row.device, now)
        own = {identity(item.path): item.account_key for item in reach
               if item.device == row.device and item.state != PENDING_IDENTITY and item.path}
        projected = _pending_row(row)
        decision = rules.resolve(projected, registered=registered, own_reach=own, legacy=mine and member.legacy,
                                 host=member.host if mine else "", aliases=member.aliases)
        if decision.kind == rules.PENDING or (not mine and decision.rule not in (1, 2)):
            continue
        if decision.kind == rules.NEW:
            record = _register(doc, decision, projected, member, now)
            names[decision.account_key] = record.name
            registrations = _rows(Q_REGISTRATIONS, (doc.name,), True)
        else:
            _refresh_registration(names[decision.account_key], decision, projected, member, protected)
        frappe.db.delete(REACH, {"name": ("in", [row.name])})
        current = frappe.db.get_value(REACH, reach_name(doc.name, row.device, decision.account_key),
                                      ["name", *_REACH_FIELDS, "seen_at"], as_dict=True)
        _save_reach(doc, row.device, decision.account_key, _reach_values(decision, projected), current, now)
        reach = _rows(Q_REACH, (doc.name,), True)
        changed = True
    return changed


def touch(connection, device, now):
    """名单没变的心跳：可达行的报到时间隔一天补记一次（待认定的退出条件按它算）。不改内容，目录不重发。"""
    frappe.db.sql(Q_TOUCH, (now, connection, device, now - TOUCH_EVERY))


def forget_device(device, *, connection=None):
    """电脑被移除 / 退役（``connection`` 给了：它在这条连接上一把有效密钥都不剩）：删它的可达行（登记留着，别的电脑或
    历史还在用），读请求的分派、待认定的等待与下次发布的目录都不再算它够得到。"""
    if device:
        frappe.db.delete(REACH, {"device": device, **({"connection": connection} if connection else {})})


def move_device(connection, old, new):
    """一把迁移来的旧槽位密钥改挂到另一台电脑（`companion.devices.hello`：同一台电脑的另一个成员先认领过）：旧电脑在
    这条连接上的可达行、只属于它的本机登记，转给那台，再由调用方让旧电脑退役。否则那台第一次整份上报时没有可达行可认
    （第 3 条），旧电脑的行又已删掉（第 5 条不等），同一份账套会被登记出第二个规范键。调用方已锁连接与密钥，电脑行
    还没锁（加锁顺序）。回转了几行可达。"""
    if not connection or not old or not new or old == new:
        return 0
    reach = _rows(Q_REACH, (connection,), True)
    theirs = {row.account_key: row for row in reach if row.device == new}
    moved = 0
    for row in reach:
        if row.device != old:
            continue
        record = frappe.get_doc(REACH, row.name, for_update=True)
        values = {field: record.get(field) for field in _REACH_FIELDS}
        frappe.db.delete(REACH, {"name": ("in", [row.name])})
        current = theirs.get(row.account_key)
        if current is not None and current.state == PENDING_IDENTITY and row.state != PENDING_IDENTITY:
            frappe.db.delete(REACH, {"name": ("in", [current.name])})  # 那台在等的正是旧电脑这份：就是它
            current = None
        if current is None:  # 那台自己也够到这份（状态正常）：留那台自己的写法
            access.write(frappe.get_doc({"doctype": REACH, "record_key": reach_name(connection, new, row.account_key),
                                         "workspace": record.workspace, "connection": connection, "device": new,
                                         "account_key": row.account_key, "seen_at": record.seen_at, **values}))
        moved += 1
    for row in _rows(Q_REGISTRATIONS, (connection,), True):
        if row.scope_device == old:
            record = frappe.get_doc(ACCOUNT_SET, row.name, for_update=True)
            record.scope_device = new
            access.write(record)
    return moved


# ── 账套资料 ─────────────────────────────────────────────────────────────────────────


def facts(connection, account_key):
    """这份账套最近一次读到的主档（`FACT_KEYS` 里有的），附 ``_routine`` / ``_seen_at`` / ``_device``；没读过回 None。"""
    key = identity(account_key)
    if not connection or not key:
        return None
    row = frappe.db.get_value(FACTS, facts_name(connection, key), ["body", "routine", "seen_at", "source_device"],
                              as_dict=True)
    if not row:
        return None
    body = model.parse(row.body, {}) or {}
    return {**{k: v for k, v in body.items() if k in FACT_KEYS}, "_routine": bool(row.routine),
            "_seen_at": row.seen_at, "_device": row.source_device or ""}


def masters(found):
    """:func:`facts` 去掉附注，只剩主档。"""
    return {key: value for key, value in (found or {}).items() if key in FACT_KEYS}


def put_facts(doc, account_key, values, *, device, routine, now, protected=None):
    """记下这份账套刚读到的主档（与已存的合并：这次没带的键保留上次的）。内容没变只隔一阵补记读到的时间。
    超过每条连接 20 份时淘汰最久没读的，被未了结推送记录引用的（``protected()``）不淘汰。"""
    key = identity(account_key)
    values = {k: v for k, v in (values or {}).items() if k in FACT_KEYS}
    if not key or not values:
        return False
    name = facts_name(doc.name, key)
    if frappe.db.get_value(FACTS, name, "name", for_update=True):
        record = frappe.get_doc(FACTS, name, for_update=True)
        body = {**(model.parse(record.body, {}) or {}), **values}
        encoded = model.dumps(body)
        if (encoded == record.body and bool(record.routine) == bool(routine) and record.seen_at
                and record.seen_at >= now - FACTS_TOUCH_EVERY):
            return False
        record.update({"body": encoded, "routine": 1 if routine else 0, "source_device": device, "seen_at": now})
        access.write(record)
        return True
    body = model.dumps(values)
    if len(body.encode()) > 16 * 1024 * 1024:
        frappe.throw(_("connection.snapshot_too_large"))
    access.write(frappe.get_doc({"doctype": FACTS, "record_key": name, "workspace": doc.workspace,
                                 "connection": doc.name, "account_key": key, "body": body,
                                 "routine": 1 if routine else 0, "source_device": device, "seen_at": now}))
    retain(doc.name, protected=protected, keep=key)
    return True


def retain(connection, *, protected=None, keep=None):
    """每条连接最多留 :data:`FACTS_PER_CONNECTION` 份资料：淘汰最久没读的；被未了结推送记录引用的、刚写的不淘汰。"""
    rows = _rows(Q_FACTS, (connection,), True)
    if len(rows) <= FACTS_PER_CONNECTION:
        return []
    held = {identity(value) for value in (protected() if callable(protected) else protected or ())}
    held.add(keep)
    spare = len(rows) - FACTS_PER_CONNECTION
    gone = [row.name for row in reversed(rows) if row.account_key not in held][:spare]
    if gone:
        frappe.db.delete(FACTS, {"name": ("in", gone)})
    return gone


def add_stock_masters(connection, account_key, count, root=None):
    """推送回执说新建了几个存货主档：累加到这份账套资料的目录指纹上（新一次读取仍是权威）。
    登记的规范根与推送时冻结的不同、没有资料或没有指纹时如实回状态，不编。"""
    key = identity(account_key)
    found = registration(connection, key)
    wanted = identity(root)
    if found is None or (found.root_key and wanted and found.root_key != wanted):
        return {"count": count, "state": "target_changed"}
    name = facts_name(connection, key)
    if not frappe.db.get_value(FACTS, name, "name", for_update=True):
        return {"count": count, "state": "unavailable"}
    record = frappe.get_doc(FACTS, name, for_update=True)
    body = model.parse(record.body, {}) or {}
    fingerprint = body.get("catalog_fingerprint")
    before = fingerprint.get("stock_master_count", 0) if isinstance(fingerprint, dict) else None
    if type(before) is not int or before < 0:
        return {"count": count, "state": "unavailable"}
    body["catalog_fingerprint"] = {**fingerprint, "stock_master_count": before + count}
    record.body = model.dumps(body)
    # 推导出来的资料更新，不是一次新的读取：不改读到的时间与来源
    access.write(record)
    return {"count": count, "state": "applied", "before": before, "after": before + count}


# ── 老客户迁移（补丁 `v0_0_20_express_account_sets`，方案 4.1 第 2–3 步）─────────────────────────


def _legacy_rows(doc, report, config):
    """今天的目录：当前端点快照的账套行，加上报记录里的原始名单、连接配置里的默认账套（快照里没有的才补）。"""
    from .reports import _account_sets

    rows, seen = [], set()

    def add(row):
        key = identity((row.get("attributes") or {}).get("path") or row.get("source_id"))
        if key and key not in seen:
            seen.add(key)
            rows.append(row)

    _, _, head_key = state.scope(None)
    head = state.heads(doc).get(head_key) or {}
    if head.get("snapshot_id"):
        snapshot = frappe.db.get_value(state.SNAPSHOT, head["snapshot_id"], ["connection", "connection_revision", "body"],
                                       as_dict=True)
        if snapshot and snapshot.connection == doc.name and int(snapshot.connection_revision or 0) == int(
                doc.record_revision or 0):
            for row in (model.parse(snapshot.body, {}) or {}).get("account_sets") or []:
                if isinstance(row, dict):
                    add(row)
    for row in _account_sets(report.get("reported_account_sets")):
        add(row)
    selected = str(config.get("account_set") or config.get("account_dir") or "").strip()
    if selected:
        add({"source_id": identity(selected), "label": str(config.get("account_company") or selected)[:500],
             "attributes": {"path": str(config.get("account_dir") or selected), "root": config.get("express_root") or "",
                            "writable": True}})
    return rows


def _legacy_facts(doc, report):
    """今天散在各处的主档 → ``{规范键: (主档, 是不是按期上报的所选账套, 读到的时间)}``：上报记录里记在默认账套名下的
    那份、旧的 ``_account_caches``、各账套目录快照里按账套读回的那份（`collector` 2026-10-13 起存在那里）。"""
    found = {}
    binding = report.get("_preparation_binding") or {}
    expected = {key: value for key, value in binding.items() if key != "online"}
    origins = report.get("_preparation_origins") or {}
    default = identity(binding.get("account_set"))
    if default and binding.get("credential_id") == doc.report_credential:
        values = {key: report[key] for key in FACT_KEYS if key in report and origins.get(key) == expected}
        if values:
            found[default] = (values, True, None)
    for account, cache in (report.get("_account_caches") or {}).items():
        values = {key: cache[key] for key in FACT_KEYS if isinstance(cache, dict) and key in cache}
        if identity(account) and values:
            found.setdefault(identity(account), (values, False, None))
    for head in state.heads(doc).values():
        if not isinstance(head, dict) or not head.get("snapshot_id"):
            continue
        snapshot = frappe.db.get_value(state.SNAPSHOT, head["snapshot_id"],
                                       ["connection", "connection_revision", "scope_kind", "scope_key", "body",
                                        "observed_at"], as_dict=True)
        if (not snapshot or snapshot.connection != doc.name or snapshot.scope_kind != "account_set"
                or int(snapshot.connection_revision or 0) != int(doc.record_revision or 0)):
            continue
        cache = (model.parse(snapshot.body, {}) or {}).get("reported")
        values = {key: cache[key] for key in FACT_KEYS if isinstance(cache, dict) and key in cache}
        if values and identity(snapshot.scope_key):
            found.setdefault(identity(snapshot.scope_key), (values, False, snapshot.observed_at))
    return found


def ensure_registered(doc, account_key, *, root="", now):
    """补一份没有可达行的登记（推送记录、定时任务还引用、目录里已经没有的键）：规范键 = 那个键，规范根 = 推送时冻结的
    根（不知道就留空，由第一台够得到它的电脑补上）。已登记的不动。"""
    key = identity(account_key)
    if not key or frappe.db.get_value(ACCOUNT_SET, registration_name(doc.name, key), "name"):
        return False
    access.write(frappe.get_doc({"doctype": ACCOUNT_SET, "record_key": registration_name(doc.name, key),
                                 "workspace": doc.workspace, "connection": doc.name, "account_key": key,
                                 "root_key": identity(root), "path": key, "root": identity(root), "label": "",
                                 "attributes_json": "{}", "uid": "", "location": "", "content_key": "",
                                 "scope_device": "", "registered_at": now}))
    return True


def seed_legacy(connection, *, device, referenced=None, now, protected=None):
    """一条 Express 连接的老数据进池：登记（规范键 = 今天的 ``source_id``，规范根 = 今天的根）、旧电脑的可达行、
    推送记录 / 定时任务还引用的键、账套资料。幂等：已登记的、已有资料的不动；旧电脑在这条连接上已有可达行（新版心跳
    报过）就不再按旧目录播种它。不碰连接配置、版本号、目录快照与推送记录。回各建了几行。"""
    doc = state.read(state.CONNECTION, connection)
    if doc.adapter != "express" or int(doc.binding_generation or 0) != 0:
        return {}
    report = model.parse(doc.get("report_json"), {}) or {}
    config = model.parse(doc.config_json, {}) or {}
    rows = _legacy_rows(doc, report, config)
    before = len(_rows(Q_REGISTRATIONS, (doc.name,), True))
    if device and device not in devices(doc.name):
        apply_report(doc, Member(device=device, legacy=True), rows, now, protected=protected)
    else:
        for row in rows:
            attributes = row.get("attributes") or {}
            ensure_registered(doc, attributes.get("path") or row.get("source_id"), root=attributes.get("root"),
                              now=now)
    for key, root in (referenced or {}).items():
        ensure_registered(doc, key, root=root, now=now)
    seeded = 0
    for key, (values, routine, seen) in _legacy_facts(doc, report).items():
        if frappe.db.get_value(FACTS, facts_name(doc.name, key), "name"):
            continue
        access.write(frappe.get_doc({"doctype": FACTS, "record_key": facts_name(doc.name, key),
                                     "workspace": doc.workspace, "connection": doc.name, "account_key": key,
                                     "body": model.dumps(values), "routine": 1 if routine else 0,
                                     "source_device": device or "", "seen_at": seen or now}))
        seeded += 1
    retain(doc.name, protected=protected)
    return {"registrations": len(_rows(Q_REGISTRATIONS, (doc.name,), True)) - before, "facts": seeded}


__all__ = ["ACCOUNT_SET", "FACTS", "FACTS_PER_CONNECTION", "FACT_KEYS", "OK", "PENDING_IDENTITY", "REACH", "STALE",
           "add_stock_masters", "any_reach", "apply_report", "ensure_registered", "seed_legacy", "canonical", "catalog", "devices", "facts",
           "facts_name", "forget_device", "has_pending", "listing", "local_keys", "local_paths", "mark_stale", "masters",
           "move_device", "note_written", "presence", "put_facts", "readable_reach", "writable_reach", "writer_devices",
           "reach_name", "reachers", "reachers_of", "reaches", "reconsider", "registration", "registration_name",
           "registrations_of", "retain", "touch", "union_observation", "writers"]
