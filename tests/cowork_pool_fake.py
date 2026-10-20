"""连接域测试用的内存 Frappe 替身（不连库、不依赖 Frappe）：目录快照、刷新请求、账套登记 / 可达 / 账套资料（ADR-021）。

`install()` 把替身装进 ``sys.modules``（连同连接授权、主档映射、银行流水的最小替身），之后真代码
`pearnly_cowork.connections.*` 就跑在这个内存库上。连接域的 SQL 只认这里列出的几种写法，别的一律报错（防止
测试悄悄放过没覆盖的查询）。``FOR UPDATE`` 记进 ``DB.locks``。
"""
import itertools
import json
import pathlib
import re
import sys
import types
from datetime import datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "pearnly_cowork"


class ValidationError(Exception):
    pass


class PermissionError_(ValidationError):
    pass


class QueryDeadlockError(Exception):
    pass


class TimestampMismatchError(Exception):
    pass


_ORDER = itertools.count(1)


class Doc(dict):
    """够用的 Frappe 文档：属性读写、`get`、insert / save 落到内存库；有控制器的跑它的 ``validate``。"""

    def __getattr__(self, key):
        if key.startswith("__"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value

    @property
    def flags(self):
        return types.SimpleNamespace()

    def is_new(self):
        return not self.get("name") or self["name"] not in DB.docs.get(self["doctype"], {})

    def get_doc_before_save(self):
        return DB.saved.get((self["doctype"], self.get("name")))

    def insert(self, **_ignored):
        self["name"] = self.get("name") or self["record_key"]
        if self["name"] in DB.docs.get(self["doctype"], {}):
            raise ValidationError("duplicate " + self["name"])
        self.setdefault("creation", next(_ORDER))
        self["modified"] = next(_ORDER)
        DB.check(self)
        DB.docs.setdefault(self["doctype"], {})[self["name"]] = self
        DB.saved[(self["doctype"], self["name"])] = Doc(self)
        return self

    def save(self, **_ignored):
        # Frappe 的 check_if_latest：拿着同一事务里已被别处改过的旧副本去存会报 TimestampMismatchError
        stored = DB.docs.get(self["doctype"], {}).get(self.get("name"))
        if stored is not None and stored is not self and stored.get("modified") != self.get("modified"):
            raise TimestampMismatchError(self["doctype"] + " " + self["name"])
        self["modified"] = next(_ORDER)
        DB.check(self)
        DB.docs.setdefault(self["doctype"], {})[self["name"]] = self
        DB.saved[(self["doctype"], self["name"])] = Doc(self)
        return self


def _match(row, filters):
    for key, wanted in filters.items():
        if isinstance(wanted, tuple) and wanted[0] == "in":
            if row.get(key) not in wanted[1]:
                return False
        elif row.get(key) != wanted:
            return False
    return True


class Db:
    def __init__(self):
        self.docs, self.saved, self.locks, self.checks = {}, {}, [], {}
        # 池的批量写：每次批量插入 / 批量改列记一条（表，行数）
        self.bulk_inserts, self.updates = [], []

    def clear(self):
        self.docs.clear()
        self.saved.clear()
        self.locks.clear()
        self.bulk_inserts.clear()
        self.updates.clear()
        PRESENCE.clear()
        LOST_ACCESS.clear()

    def check(self, doc):
        check = self.checks.get(doc["doctype"])
        if check:
            check(doc, self.saved.get((doc["doctype"], doc.get("name"))))

    def rows(self, doctype):
        return sorted(self.docs.get(doctype, {}).values(), key=lambda row: (row.get("creation") or 0, row["name"]))

    def get_value(self, doctype, name, fields, as_dict=False, for_update=False):
        row = self.docs.get(doctype, {}).get(name)
        if for_update and row is not None:
            self.locks.append((doctype, name))
        if row is None:
            return None
        if fields == "name":
            return name
        if isinstance(fields, str):
            return row.get(fields)
        return Doc({field: row.get(field) for field in fields})

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        self.docs[doctype][name].update(field if isinstance(field, dict) else {field: value})

    def _insert(self, q, values):
        """池的批量插入（`pool._insert_rows`）：``INSERT INTO `tab…` (`列`, …) VALUES (%s, …), …``。按给的顺序插入（创建时刻
        换成替身自己的递增序号，排序同真库按 creation）；名字重复整批报错。"""
        doctype = re.match(r"INSERT INTO `tab([^`]+)` ", q).group(1)
        fields = re.findall(r"`(\w+)`", q[q.index("(") + 1:q.index(") VALUES")])
        values = list(values)
        assert len(values) % len(fields) == 0, q
        rows = [Doc(dict(zip(fields, values[start:start + len(fields)])), doctype=doctype)
                for start in range(0, len(values), len(fields))]
        table = self.docs.setdefault(doctype, {})
        names = [row["name"] for row in rows]
        if len(set(names)) != len(names) or any(name in table for name in names):
            raise ValidationError("duplicate " + doctype)
        for row in rows:
            row["creation"], row["modified"] = next(_ORDER), next(_ORDER)
            self.check(row)
            table[row["name"]] = row
            self.saved[(doctype, row["name"])] = Doc(row)
        self.bulk_inserts.append((doctype, len(rows)))
        return []

    def _case_update(self, q, values):
        """池的批量改列（`pool._update_rows`）：``UPDATE `tab…` SET `列`=CASE name WHEN %s THEN %s … END, …,
        `modified`=%s, `modified_by`=%s WHERE name IN %s``。照真库逐行改，改完跑一次控制器校验（规范键 / 根不变量）。"""
        doctype = re.match(r"UPDATE `tab([^`]+)` SET ", q).group(1)
        columns = re.findall(r"`(\w+)`=CASE name((?: WHEN %s THEN %s)+) END", q)
        names = values[-1]
        params = list(values[:-3])
        changes = {name: {} for name in names}
        for column, whens in columns:
            for _ in range(whens.count("WHEN")):
                name, value = params.pop(0), params.pop(0)
                changes[name][column] = value
        assert not params, q
        table = self.docs[doctype]
        for name, update in changes.items():
            row = table[name]
            row.update(update)
            row["modified"] = next(_ORDER)
            self.check(row)
            self.saved[(doctype, name)] = Doc(row)
        self.updates.append((doctype, len(changes)))
        return []

    def delete(self, doctype, filters):
        table = self.docs.get(doctype, {})
        for name in [name for name, row in table.items() if _match(row, filters)]:
            table.pop(name)
            self.saved.pop((doctype, name), None)

    def sql(self, query, values=(), as_dict=False):
        q = " ".join(query.split())
        found = self._sql(q, tuple(values or ()))
        if q.endswith("FOR UPDATE") and found and isinstance(found, list):
            self.locks.extend(("sql", row.get("name") if isinstance(row, dict) else row[0]) for row in found)
        return found

    def _sql(self, q, values):
        pool = sys.modules.get("pearnly_cowork.connections.pool")
        base = q.removesuffix(" FOR UPDATE")
        refresh = self.rows("Cowork ERP Refresh")
        if "`tabCowork ERP Refresh`" in q:
            if "lease_handle=%s" in q:
                return [(row.name,) for row in refresh if row.connection == values[0] and row.lease_handle == values[1]]
            if "parent_request=%s" in q:
                return [(row.name,) for row in sorted(refresh, key=lambda row: (row.requested_at, row.name))
                        if row.get("parent_request") == values[0]]
            if "status IN" in q:
                connection, statuses = values[0], set(values[1:])
                return [(row.name,) for row in sorted(refresh, key=lambda row: (row.requested_at, row.name))
                        if row.connection == connection and row.status in statuses]
        if pool is not None and q.startswith("UPDATE `tab") and "=CASE name WHEN" in q:
            return self._case_update(q, values)
        if pool is not None and q.startswith("INSERT INTO `tab"):
            return self._insert(q, values)
        if pool is not None:
            reach = [row for row in self.rows(pool.REACH)]
            if base == " ".join(pool.Q_REGISTRATIONS.split()):
                return [Doc(row) for row in self.rows(pool.ACCOUNT_SET) if row.connection == values[0]]
            if base == " ".join(pool.Q_REACH.split()):
                return [Doc(row) for row in reach if row.connection == values[0]]
            if base == " ".join(pool.Q_MINE.split()):
                return [Doc(account_key=row.account_key, path=row.path, state=row.state) for row in reach
                        if row.connection == values[0] and row.device == values[1]
                        and row.state != "pending_identity"]
            if base == " ".join(pool.Q_REACHERS.split()):
                return [Doc(device=row.device, writable=row.writable) for row in reach
                        if row.connection == values[0] and row.account_key == values[1] and row.state == "ok"]
            if base == " ".join(pool.Q_REACHERS_OF.split()):
                keys = set(values[1])
                return [Doc(account_key=row.account_key, device=row.device, writable=row.writable) for row in reach
                        if row.connection == values[0] and row.account_key in keys and row.state == "ok"]
            if base == " ".join(pool.Q_REGISTRATIONS_OF.split()):
                names = set(values[0])
                return [Doc(account_key=row.account_key, uid=row.get("uid"), content_key=row.get("content_key"),
                            last_writer_device=row.get("last_writer_device"), last_written_at=row.get("last_written_at"))
                        for row in self.rows(pool.ACCOUNT_SET) if row.name in names]
            if base == " ".join(pool.Q_ENTERED_OF.split()):
                names = set(values[0])
                return [Doc(account_key=row.account_key, attributes_json=row.get("attributes_json"))
                        for row in self.rows(pool.ACCOUNT_SET) if row.name in names]
            if base == " ".join(pool.Q_WRITABLE.split()):
                return [Doc(account_key=row.account_key, path=row.path, root=row.root, registry_row=row.registry_row)
                        for row in reach if row.connection == values[0] and row.device == values[1]
                        and row.state == "ok" and row.writable]
            if base == " ".join(pool.Q_WRITERS.split()):
                return [Doc(device=device) for device in sorted({row.device for row in reach if row.connection == values[0]
                                                                 and row.state == "ok" and row.writable})]
            if base == " ".join(pool.Q_ANY_REACH.split()):
                return [Doc(name=row.name) for row in reach if row.connection == values[0]][:1]
            if base == " ".join(pool.Q_DEVICES.split()):
                return [Doc(device=device) for device in sorted({row.device for row in reach
                                                                 if row.connection == values[0]})]
            if base == " ".join(pool.Q_PENDING.split()):
                return [Doc(name=row.name) for row in reach if row.connection == values[0]
                        and row.device == values[1] and row.state == "pending_identity"][:1]
            if base == " ".join(pool.Q_TOUCH.split()):
                now, connection, device, before = values
                for row in reach:
                    if row.connection == connection and row.device == device and (not row.seen_at
                                                                                  or row.seen_at < before):
                        row.seen_at = now
                return []
            if base == " ".join(pool.Q_FACTS.split()):
                rows = [row for row in self.rows(pool.FACTS) if row.connection == values[0]]
                rows.sort(key=lambda row: row.name)
                rows.sort(key=lambda row: row.seen_at, reverse=True)
                return [Doc(name=row.name, account_key=row.account_key, seen_at=row.seen_at) for row in rows]
        raise AssertionError("unexpected query: " + q)


DB = Db()
# 池里各台电脑此刻的情况（真站点上由 companion 经 ``cowork_pool_presence`` 钩子给，`connections.pool.presence`）：
# ``{连接: {设备: {"online", "version", ...}}}``；`Computer.report` 报到时记上
PRESENCE = {}
# 失去这条连接使用权的人（`access.usable_by` 的替身按它判）
LOST_ACCESS = set()
_HOOKS = {"cowork_pool_presence": ["fake.presence"]}
_ATTRS = {"fake.presence": lambda connection: PRESENCE.get(connection, {})}


class _Field(types.SimpleNamespace):
    pass


class _Meta:
    """`frappe.get_meta` 的够用替身：读本仓 DocType 定义里的字段（类型、长度、唯一）。"""

    def __init__(self, doctype):
        folder = doctype.lower().replace(" ", "_")
        path = APP / "pearnly_cowork" / "pearnly_cowork" / "doctype" / folder / (folder + ".json")
        fields = json.loads(path.read_text())["fields"]
        self._fields = {field["fieldname"]: _Field(fieldtype=field["fieldtype"], length=field.get("length") or 0,
                                                   unique=field.get("unique") or 0) for field in fields}

    def get_field(self, fieldname):
        return self._fields.get(fieldname)

    def has_field(self, fieldname):
        return fieldname in self._fields


_METAS = {}


def _get_meta(doctype):
    if doctype not in _METAS:
        _METAS[doctype] = _Meta(doctype)
    return _METAS[doctype]


def _throw(message, exc=None):
    raise (exc or ValidationError)(message)


def _get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        return Doc(doctype)
    if for_update:
        DB.locks.append((doctype, name))
    try:
        return Doc(DB.docs[doctype][name])  # 每次一份新副本，同真 Frappe
    except KeyError:
        raise ValidationError("not found " + str(name)) from None


def install(*, push_inputs=None, bank_window=None):
    """装好替身，回 frappe 替身。连接的授权、主档映射、银行流水在别的测试里守着：这里只给最小替身。"""
    sys.path.insert(0, str(APP))
    frappe = types.ModuleType("frappe")
    frappe.__dict__.update(db=DB, throw=_throw, _=lambda text: text, get_doc=_get_doc, ValidationError=ValidationError,
                           PermissionError=PermissionError_, QueryDeadlockError=QueryDeadlockError,
                           DoesNotExistError=ValidationError, clear_messages=lambda: None,
                           local=types.SimpleNamespace(site="testcowork.pearnly.com"),
                           session=types.SimpleNamespace(user="owner@example.com"), _dict=Doc,
                           get_hooks=lambda name: list(_HOOKS.get(name, [])), get_attr=lambda path: _ATTRS[path],
                           get_meta=_get_meta)
    utils = types.ModuleType("frappe.utils")
    utils.now_datetime = datetime.now
    frappe.utils = utils
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = Doc
    model.document = document
    frappe.model = model
    pymysql = types.ModuleType("pymysql")
    pymysql_err = types.ModuleType("pymysql.err")
    pymysql_err.OperationalError = type("OperationalError", (Exception,), {})
    pymysql_err.ProgrammingError = type("ProgrammingError", (Exception,), {})
    access = types.ModuleType("pearnly_cowork.connections.access")
    access.TYPES = ("Cowork ERP Connection", "Cowork ERP Snapshot", "Cowork ERP Refresh")
    access.POOL_TYPES = ("Cowork Express Account Set", "Cowork Express Reach", "Cowork Express Account Facts")
    access.write = lambda doc: doc.insert() if doc.is_new() else doc.save()
    access.require_connection = lambda doc, actor=None, **kwargs: actor or "owner@example.com"
    # 签发人此刻能不能用这条连接（真站点：录入或管理 + 使用名单）；这里按 ``LOST_ACCESS`` 判
    access.usable_by = lambda doc, actor: bool(actor) and actor not in LOST_ACCESS

    def deny():
        raise PermissionError_("Not Permitted")

    access.deny = deny
    access.guard = lambda doc: None
    masters = types.ModuleType("pearnly_cowork.masters")
    masters.push_inputs = push_inputs or types.SimpleNamespace(
        mappings=lambda client: {"accounts": [], "clients": []}, profiles=lambda workspace, tax: {})
    reconciliation = types.ModuleType("pearnly_cowork.reconciliation")
    reconciliation.bank_evidence = types.SimpleNamespace(window=bank_window or (lambda owner, date: []))
    sys.modules.update({"frappe": frappe, "frappe.model": model, "frappe.model.document": document, "frappe.utils": utils,
                        "pymysql": pymysql, "pymysql.err": pymysql_err,
                        "pearnly_cowork.connections.access": access, "pearnly_cowork.masters": masters,
                        "pearnly_cowork.masters.push_inputs": masters.push_inputs,
                        "pearnly_cowork.reconciliation": reconciliation,
                        "pearnly_cowork.reconciliation.bank_evidence": reconciliation.bank_evidence})
    return frappe


class Computer:
    """一台装了 CC 桌面版的电脑往一条 Express 连接上报（真 `plan_report` + `collector.ingest`）。

    ``legacy``：没认领过的旧版（1.7.0 之前，只按路径认账套）；``hosts``：新版 ``device_hello`` 报的主机名。
    这台电脑上次报的所选、版本由它自己记着（服务端在电脑行上，`companion.devices`）。"""

    def __init__(self, doc, device, *, credential="CRED", generation=1, issuer="owner@example.com", legacy=True,
                 host="", aliases=None, version="1.5.1"):
        self.doc, self.device, self.credential, self.generation, self.issuer = doc, device, credential, generation, issuer
        self.legacy, self.host, self.aliases, self.version = legacy, host, aliases or {}, version
        self.state = {}

    def member(self, plan=None):
        from pearnly_cowork.connections.member import Member

        version = (plan.patch.get("companion_version") if plan else None) or self.state.get("companion_version")
        return Member(device=self.device, legacy=self.legacy, host=self.host, aliases=self.aliases,
                      version=version or "", payload_version=self.state.get("max_payload_version"),
                      selected=str(self.state.get("account_set") or ""))

    def report(self, body, now, *, protected=None):
        from datetime import timezone
        from pearnly_cowork.connections import collector, model
        from pearnly_cowork.connections.reports import MAPPING_KEYS, SELECTED_KEYS, plan_report

        shared = {k: v for k, v in model.parse(self.doc.report_json, {}).items()
                  if k not in ("companion_version", "max_payload_version")}
        plan = plan_report(body, {**shared, **self.state}, now.replace(tzinfo=timezone.utc))
        result = collector.ingest(self.doc, plan, credential_id=self.credential, token_generation=self.generation,
                                  issuer=self.issuer, member=self.member(plan), protected=protected, now=now)
        if not plan.offline:
            self.state.update({k: v for k, v in plan.patch.items()
                               if k in SELECTED_KEYS | MAPPING_KEYS | {"companion_version", "max_payload_version"}})
        PRESENCE.setdefault(self.doc.name, {})[self.device] = {
            "users": frozenset(), "online": not plan.offline,
            "version": str(self.state.get("companion_version") or ""),
            "payload_version": self.state.get("max_payload_version"), "index_pending": None, "issuers": {self.issuer}}
        return result

    def lease(self, now, selected=None):
        from pearnly_cowork.connections import refresh

        member = self.member()
        return refresh.lease(self.doc, member, member.selected if selected is None else selected, self.credential,
                             self.generation, now)
