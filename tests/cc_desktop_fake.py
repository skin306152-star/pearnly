"""共用机器通道（`pearnly_core.cc_desktop`）离线测试用的极简 Frappe 替身：内存里的四张表、唯一约束、行锁不做（单线程）。

只实现通道代码真用到的那几样：``get_all`` / ``get_doc`` / ``new_doc`` / ``db.get_value`` / ``db.set_value`` / ``db.sql``（按通道里
那几条查询的形状认）/ 保存点与回滚 / ``get_hooks`` / ``get_attr``。唯一约束照 DocType JSON 里 ``unique`` 的字段核，撞了抛
``UniqueValidationError``（写者锁 ``writing_key`` 靠它）。
"""
from copy import deepcopy
from datetime import datetime
import json
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCTYPES = (ROOT / "apps/pearnly_core/pearnly_core/pearnly_core/doctype",
            ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal/pearnly_erp_portal/doctype")


class ValidationError(Exception):
    pass


class UniqueValidationError(ValidationError):
    pass


class PermissionError_(Exception):
    pass


class AuthenticationError(Exception):
    http_status_code = 401


class DoesNotExistError(ValidationError):
    pass


def _unique_fields(doctype):
    slug = doctype.lower().replace(" ", "_")
    folder = next(base / slug for base in DOCTYPES if (base / slug).is_dir())
    spec = json.loads((folder / (folder.name + ".json")).read_text(encoding="utf-8"))
    return [field["fieldname"] for field in spec["fields"] if field.get("unique")]


class DB:
    def __init__(self):
        self.tables = {}
        self.points = []
        self.clock = 0

    def table(self, doctype):
        return self.tables.setdefault(doctype, {})

    def snapshot(self):
        return deepcopy(self.tables)


STATE = types.SimpleNamespace(db=DB(), hooks={}, attrs={}, users={})


class Doc(dict):
    def __init__(self, doctype, values=None, new=True):
        super().__init__(values or {})
        self.doctype = doctype
        self.flags = types.SimpleNamespace(cc_desktop_write=None, cc_desktop_name=None, ignore_permissions=False,
                                           ignore_version=False, notify_update=True)
        self._new = new
        self.setdefault("doctype", doctype)

    def __getattr__(self, key):
        if key.startswith("_") or key in ("doctype", "flags"):
            raise AttributeError(key)
        return self.get(key)

    def __setattr__(self, key, value):
        if key in ("doctype", "flags", "_new"):
            object.__setattr__(self, key, value)
        else:
            self[key] = value

    def is_new(self):
        return self._new

    def as_dict(self):
        return dict(self)

    def _check_unique(self):
        rows = STATE.db.table(self.doctype)
        for field in _unique_fields(self.doctype):
            value = self.get(field)
            if value in (None, ""):
                continue
            if any(name != self.get("name") and row.get(field) == value for name, row in rows.items()):
                raise UniqueValidationError(field)

    def _run(self, hook):
        controller = STATE.controllers.get(self.doctype)
        if controller is not None:
            getattr(controller, hook)(self)

    def insert(self, ignore_permissions=False, set_name=None):
        self._run("autoname")
        self._run("before_insert")
        self._check_unique()
        STATE.db.clock += 1
        self["creation"] = self.get("creation") or datetime(2026, 10, 23, 0, 0, STATE.db.clock % 60,
                                                            STATE.db.clock)
        STATE.db.table(self.doctype)[self["name"]] = dict(self)
        self._new = False
        return self

    def save(self, ignore_permissions=False, ignore_version=False):
        self._run("validate")
        self._check_unique()
        STATE.db.table(self.doctype)[self["name"]] = dict(self)
        return self


class _Controller:
    """把 `documents.ChannelDocument` 的守卫套到替身文档上（autoname 取本域交进来的名字，守卫核标记）。"""

    def __init__(self, documents):
        self.documents = documents

    def _guard(self, doc):
        if doc.flags.cc_desktop_write is not self.documents._WRITE:
            raise PermissionError_("guard")

    def autoname(self, doc):
        self._guard(doc)
        doc["name"] = doc.flags.cc_desktop_name

    def before_insert(self, doc):
        self._guard(doc)

    def validate(self, doc):
        self._guard(doc)


def _match(row, filters):
    for key, wanted in (filters or {}).items():
        value = row.get(key)
        if isinstance(wanted, (list, tuple)):
            op, operand = wanted[0], wanted[1]
            if op == "in" and value not in operand:
                return False
            if op == "not in" and value in operand:
                return False
            if op == "<=" and not (value is not None and value <= operand):
                return False
            if op == "<" and not (value is not None and value < operand):
                return False
            if op == "is" and operand == "set" and value in (None, ""):
                return False
            if op == "is" and operand == "not set" and value not in (None, ""):
                return False
            if op == "!=" and value == operand:
                return False
        elif value != wanted:
            return False
    return True


class _Flags(dict):
    """同 Frappe 的 ``frappe.flags``：没设过的取出来是 None，也能按属性设。"""

    def __getattr__(self, key):
        return self.get(key)

    def __setattr__(self, key, value):
        self[key] = value


class _Row(dict):
    def __getattr__(self, key):
        return self.get(key)


def get_all(doctype, filters=None, fields=None, pluck=None, limit=None, order_by=None, limit_page_length=None):
    rows = [row for row in STATE.db.table(doctype).values() if _match(row, filters)]
    rows.sort(key=lambda row: (row.get("creation") or datetime.min, row.get("name")))
    count = limit or limit_page_length
    if count:
        rows = rows[:count]
    if pluck:
        return [row.get(pluck) for row in rows]
    keys = fields or ["name"]
    return [_Row({key: row.get(key) for key in keys}) for row in rows]


def get_doc(doctype, name=None, for_update=False):
    if isinstance(doctype, dict):
        return Doc(doctype["doctype"], doctype)
    row = STATE.db.table(doctype).get(name)
    if row is None:
        raise DoesNotExistError(name)
    return Doc(doctype, deepcopy(row), new=False)


def new_doc(doctype):
    return Doc(doctype)


class _DBApi:
    def get_value(self, doctype, name, fields="name", as_dict=False, for_update=False):
        if isinstance(name, dict):
            rows = get_all(doctype, filters=name, fields=["name"])
            if not rows:
                return None
            name = rows[0]["name"]
        row = STATE.db.table(doctype).get(name) if doctype != "User" else STATE.users.get(name)
        if row is None:
            return None
        if isinstance(fields, (list, tuple)):
            values = _Row({key: row.get(key) for key in fields})
            return values if as_dict else tuple(values.values())
        return row.get(fields)

    def set_value(self, doctype, name, field, value=None, update_modified=True):
        values = field if isinstance(field, dict) else {field: value}
        STATE.db.table(doctype)[name].update(values)

    def exists(self, doctype, name=None):
        return name in STATE.db.table(doctype) if isinstance(name, str) else bool(STATE.db.table(doctype))

    def delete(self, doctype, filters):
        for name in [n for n, row in STATE.db.table(doctype).items() if _match(row, filters)]:
            del STATE.db.table(doctype)[name]

    def savepoint(self, point):
        STATE.db.points.append((point, STATE.db.snapshot()))

    def rollback(self, save_point=None):
        if save_point:
            for index in range(len(STATE.db.points) - 1, -1, -1):
                if STATE.db.points[index][0] == save_point:
                    STATE.db.tables = STATE.db.points[index][1]
                    del STATE.db.points[index:]
                    return

    def commit(self):
        pass

    def sql(self, query, values=(), as_dict=False):
        text = " ".join(query.split())
        values = list(values or ())
        table = re.search(r"`tab([^`]+)`", text).group(1)
        rows = list(STATE.db.table(table).values())
        rows.sort(key=lambda row: (row.get("creation") or datetime.min, row.get("name")))
        if "FOR UPDATE" in text and "WHERE name = %s" in text:
            found = [row for row in rows if row.get("name") == values[0]]
            if "status = 'requested'" in text:
                found = [row for row in found if row.get("status") == "requested"]
        elif "WHERE device_digest = %s" in text:
            found = [row for row in rows if row.get("device_digest") == values[0]]
        elif "lease_device = %s AND status = 'leased'" in text:
            found = [row for row in rows if row.get("lease_device") == values[0] and row.get("status") == "leased"
                     and row.get("lease_expires_at") and row["lease_expires_at"] > values[1]][:1]
        elif text.startswith("SELECT DISTINCT book_key"):
            found = [row for row in rows if row.get("status") == "leased" and row.get("book_key") in values]
            found = list({row["book_key"]: row for row in found}.values())
        elif "status = 'queued' AND book_key IN" in text:
            found = [row for row in rows if row.get("status") == "queued" and row.get("book_key") in values]
        elif "status = 'requested' AND kind IN" in text:
            kinds_count = text.count("%s", text.index("kind IN"), text.index(") AND book_key"))
            kinds, keys = values[:kinds_count], values[kinds_count:]
            found = [row for row in rows if row.get("status") == "requested" and row.get("kind") in kinds
                     and row.get("book_key") in keys]
        else:
            raise AssertionError("query not faked: " + text)
        columns = re.match(r"SELECT (?:DISTINCT )?(.+?) FROM", text).group(1).split(", ")
        out = [_Row({column: row.get(column) for column in columns}) for row in found]
        return out if as_dict else [tuple(row.values()) for row in out]


def install():
    """装好替身并回它（同一进程里只装一次；每个测试开头 :func:`reset`）。"""
    if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_cc_fake", False):
        return sys.modules["frappe"]
    frappe = types.ModuleType("frappe")
    frappe._cc_fake = True
    frappe.ValidationError = ValidationError
    frappe.UniqueValidationError = UniqueValidationError
    frappe.PermissionError = PermissionError_
    frappe.AuthenticationError = AuthenticationError
    frappe.DoesNotExistError = DoesNotExistError
    frappe.QueryDeadlockError = type("QueryDeadlockError", (Exception,), {})

    def throw(message, exc=ValidationError):
        raise exc(message)

    frappe.throw = throw
    frappe._ = lambda text: text
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.new_doc = new_doc
    frappe.db = _DBApi()
    frappe.local = types.SimpleNamespace(site="testerp.localhost", message_log=[])
    frappe.session = types.SimpleNamespace(user="Guest")
    frappe.flags = _Flags()
    frappe.get_hooks = lambda name: deepcopy(STATE.hooks.get(name) or {})
    frappe.get_attr = lambda path: STATE.attrs[path]
    frappe.generate_hash = lambda length=10: __import__("secrets").token_hex(length)[:length]
    frappe.whitelist = lambda **kw: (lambda fn: fn)
    frappe.get_site_path = lambda *parts: str(pathlib.Path(STATE.site_dir, *parts))
    frappe.logger = lambda *a, **k: types.SimpleNamespace(warning=lambda *a, **k: None)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = object
    recorder = types.ModuleType("frappe.recorder")
    recorder.do_not_record = lambda fn: fn
    sys.modules.update({"frappe": frappe, "frappe.model": model, "frappe.model.document": document,
                        "frappe.recorder": recorder})
    return frappe


def reset(site_dir):
    STATE.db = DB()
    STATE.hooks, STATE.attrs, STATE.users = {}, {}, {}
    STATE.site_dir = str(site_dir)
    from pearnly_core.cc_desktop import documents

    STATE.controllers = {doctype: _Controller(documents) for doctype in documents.DOCTYPES}
