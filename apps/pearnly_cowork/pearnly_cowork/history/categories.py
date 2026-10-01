"""Invoice category tree and scoped review memory. Native DB only, no commit/IO.

These are classification inputs, not ERP accounts. Public access goes through
category_api; native CRUD/sharing is refused even when a caller supplies flags.
"""
import copy
import hashlib
import json
import re
import unicodedata

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import classification as rules

CATEGORY = "Cowork Invoice Category"
MEMORY = "Cowork Supplier Category Memory"
TYPES = (CATEGORY, MEMORY)
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_query(user=None):
    # Throw, not 1=0: native DocShare adds an OR outside query conditions.
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype in TYPES:
        deny()


class CategoryDocument(Document):
    def check_permission(self, *args, **kwargs):
        if self.flags.cowork_category_write is not _WRITE:
            deny()

    def autoname(self):
        self.check_permission()
        self.name = self.record_key

    def before_validate(self):
        self.check_permission()

    validate = before_validate
    before_insert = before_validate

    def on_trash(self):
        deny()

    def before_rename(self, *args, **kwargs):
        deny()


def _key(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def _persist(doc):
    doc.flags.cowork_category_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.notify_update = False
    return doc.insert(ignore_permissions=True) if doc.is_new() else doc.save(ignore_permissions=True)


def _reader(workspace):
    if not any(scope.allowed(f) for f in (*scope.READ_FEATURES, "master_data")):
        deny()
    scope.require_scope(workspace)


def _manager(workspace):
    scope.require("master_data")
    if not scope.administrator():
        deny()
    scope.require_scope(workspace)
    _lock(workspace)


def _lock(workspace):
    value = frappe.db.get_value("Cowork Workspace", workspace, "enabled", for_update=True)
    if not value:
        deny()


def _new(workspace, label, parent, sort, key):
    return frappe.get_doc({"doctype": CATEGORY, "record_key": key, "workspace": workspace,
        "label": label, "parent_category": parent or "", "sort_order": sort,
        "enabled": 1, "record_revision": 1})


def _seed(workspace):
    # 树已经建好（绝大多数时候）：不锁工作空间。原来每存一张识别结果都给工作空间这一行加写锁、一直持有到存结果的事务
    # 结束，同一家的识别结果只能一张一张存（2026-10-01 生产压测：这条锁等待平均 0.6 秒、最长 1.9 秒，还连带挡住
    # 读工作空间的共享锁）。只有第一次建树才加锁、再确认一次，免得两个进程同时建出两棵。
    if frappe.db.exists(CATEGORY, {"workspace": workspace}):
        return
    _lock(workspace)
    # A disabled/custom tree is still a tree: never overwrite user choices.
    if frappe.db.get_value(CATEGORY, {"workspace": workspace}, "name", for_update=True):
        return
    for index, (label, children) in enumerate(rules._PRESET):
        parent = _persist(_new(workspace, label, None, index, _key("preset", workspace, index)))
        for child_index, child_label in enumerate(children):
            _persist(_new(workspace, child_label, parent.name, child_index,
                _key("preset", workspace, index, child_index)))


def _tree(workspace, *, include_disabled=False):
    rows = frappe.db.sql("""SELECT name,label,parent_category,enabled,sort_order,record_revision
        FROM `tabCowork Invoice Category` WHERE workspace=%s
        ORDER BY sort_order,label,name LOCK IN SHARE MODE""", (workspace,), as_dict=True)
    def project(row):
        return {"id": row.name, "name": row.label, "parent_id": row.parent_category or None,
            "is_active": bool(row.enabled), "sort": row.sort_order, "revision": row.record_revision}
    parents = {row.name: dict(project(row), children=[]) for row in rows
        if not row.parent_category and (include_disabled or row.enabled)}
    for row in rows:
        if row.parent_category in parents and (include_disabled or row.enabled):
            parents[row.parent_category]["children"].append(project(row))
    return list(parents.values())


def tree(workspace, include_disabled=False):
    _reader(workspace)
    if include_disabled and not scope.administrator():
        deny()
    return _tree(workspace, include_disabled=include_disabled)


def initialize(workspace):
    _manager(workspace)
    _seed(workspace)
    return _tree(workspace, include_disabled=True)


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        frappe.throw(_("connection.config_invalid"))
    return value.strip()


def _integer(value):
    try:
        number = int(value)
    except (ValueError, TypeError, OverflowError):
        frappe.throw(_("connection.config_invalid"))
    if isinstance(value, bool) or str(value) != str(number) or not -2147483648 <= number <= 2147483647:
        frappe.throw(_("connection.config_invalid"))
    return number


def save(workspace, label, *, parent=None, name=None, request_key=None, expected_revision=0, sort=0):
    _manager(workspace)
    label, sort = _text(label, 200), _integer(sort)
    expected_revision = _integer(expected_revision)
    if parent:
        row = frappe.db.get_value(CATEGORY, {"name": parent, "workspace": workspace},
            ["parent_category", "enabled"], as_dict=True)
        if not row or row.parent_category or not row.enabled:
            frappe.throw(_("connection.config_invalid"))
    if name:
        doc = frappe.get_doc(CATEGORY, name, for_update=True)
        if doc.workspace != workspace:
            deny()
        # Moving a node would alter its historical meaning; create a new node instead.
        if (doc.parent_category or None) != (parent or None):
            frappe.throw(_("connection.config_invalid"))
    else:
        request_key = _text(request_key, 128)
        key = _key("category", workspace, request_key)
        if frappe.db.get_value(CATEGORY, key, "name", for_update=True):
            doc = frappe.get_doc(CATEGORY, key, for_update=True)
            if (doc.label, doc.parent_category or None, doc.sort_order) != (label, parent or None, sort):
                frappe.throw(_("connection.revision_conflict"))
            return {"name": doc.name, "revision": doc.record_revision}
        doc = _new(workspace, label, parent, sort, key)
    if name:
        if expected_revision != doc.record_revision:
            frappe.throw(_("connection.revision_conflict"))
        if (doc.label, doc.sort_order) == (label, sort):
            return {"name": doc.name, "revision": doc.record_revision}
        doc.record_revision += 1
    elif expected_revision != 0:
        frappe.throw(_("connection.revision_conflict"))
    doc.label, doc.sort_order = label, sort
    _persist(doc)
    return {"name": doc.name, "revision": doc.record_revision}


def disable(workspace, name, expected_revision):
    return set_enabled(workspace, name, False, expected_revision)


def set_enabled(workspace, name, enabled, expected_revision):
    _manager(workspace)
    if str(enabled).lower() not in {"0", "1", "true", "false"}:
        frappe.throw(_("connection.config_invalid"))
    enabled = str(enabled).lower() in {"1", "true"}
    doc = frappe.get_doc(CATEGORY, name, for_update=True)
    if doc.workspace != workspace:
        deny()
    if bool(doc.enabled) == enabled:
        return {"name": name, "disabled": not enabled, "revision": doc.record_revision}
    if _integer(expected_revision) != doc.record_revision:
        frappe.throw(_("connection.revision_conflict"))
    if enabled and doc.parent_category and not frappe.db.get_value(CATEGORY,
            {"name": doc.parent_category, "workspace": workspace}, "enabled", for_update=True):
        frappe.throw(_("connection.config_invalid"))
    # Disabling cascades; enabling a parent does not revive individually disabled children.
    names = [name] + ([] if enabled else frappe.db.get_all(CATEGORY, filters={"workspace": workspace,
        "parent_category": name}, pluck="name", limit_page_length=0))
    for item in names:
        row = doc if item == name else frappe.get_doc(CATEGORY, item, for_update=True)
        if bool(row.enabled) != enabled:
            row.enabled = int(enabled)
            row.record_revision += 1
            _persist(row)
    return {"name": name, "disabled": not enabled, "revision": doc.record_revision}


def _primary(pages):
    for page in pages or []:
        if not page.get("is_copy") and not page.get("is_duplicate"):
            return page.setdefault("fields", {})
    return None


def _seller(value):
    return str(value or "").strip()[:200]


def _item(fields):
    """这张票的第一行品名（记忆按"卖方 + 品名"细分用）。"""
    for item in fields.get("items") or []:
        name = str(item.get("name") or "").strip() if isinstance(item, dict) else ""
        if name:
            return name[:200]
    return ""


def _item_key(name):
    # 只为比对：大小写、空白、标点不算区别；泰文声调符保留。
    return re.sub(r"[\s.,()\-_/\\'\"&*#:;]+", "", unicodedata.normalize("NFC", str(name or "")).lower())


def _memory_keys(workspace, client, seller, item=""):
    """先"同一卖方 + 同一品名"，再退回"同一卖方"（用户 2026-09-30：同一卖方卖不同东西时按品名记才准；
    只按卖方记时，销项票卖方永远是自己，所有销项都会被套成同一个分类）。"""
    seller_key = _seller(seller).lower()
    item = _item_key(item)
    keys = [_key(workspace, client or "", seller_key, "item", item)] if item else []
    return keys + [_key(workspace, client or "", seller_key)]


def _memory(workspace, client, seller, item=""):
    if not seller:
        return None
    for key in _memory_keys(workspace, client, seller, item):
        rows = frappe.db.sql("""SELECT category FROM `tabCowork Supplier Category Memory`
            WHERE name=%s LOCK IN SHARE MODE""", (key,))
        if rows:
            return rows[0][0]
    return None


def _optional(function, fallback, label):
    # Restore any partial seed/learning writes without discarding the paid OCR or edit.
    point = "cowork_category_" + label
    frappe.db.savepoint(point)
    try:
        return function()
    except (frappe.PermissionError, frappe.QueryDeadlockError):
        raise
    except Exception as error:
        frappe.db.rollback(save_point=point)
        frappe.logger("pearnly_cowork").warning("category %s unavailable: %s", label, type(error).__name__)
        return fallback


def prepare(workspace, client, pages):
    """Trusted recognition caller only. Never mutate its frozen input/fingerprint."""
    scope.require("intake")
    scope.require_scope(workspace, client)
    prepared = copy.deepcopy(pages)
    fields = _primary(prepared)
    if fields is None:
        return prepared
    def load():
        _seed(workspace)
        return _tree(workspace)
    categories = _optional(load, None, "tree")
    if categories is not None:
        fields["category"] = rules.resolve_tag(fields, categories) or ""
    learned = _optional(lambda: _memory(workspace, client, fields.get("seller_name"), _item(fields)), None, "memory")
    # Empty active tree means intentionally disabled; do not reintroduce its old names.
    if categories != []:
        learned = rules.sanitize_learned(learned, categories)
        if learned:
            fields["category"] = learned
    return prepared


def learn(workspace, client, pages):
    """Called only after an authorized, revision-checked review changed the pages."""
    scope.require("intake")
    scope.require_scope(workspace, client)
    fields = _primary(pages) or {}
    seller = _seller(fields.get("seller_name"))
    category = str(fields.get("category") or "").strip()[:80]
    if not seller or not category:
        return
    item = _item(fields)
    def write():
        _lock(workspace)
        # 两条都记：品名那条优先套用，卖方那条兜底（没见过的品名）。
        keys = _memory_keys(workspace, client, seller, item)
        for key, item_name in zip(keys, [item, ""] if len(keys) == 2 else [""]):
            exists = frappe.db.get_value(MEMORY, key, "name", for_update=True)
            row = frappe.get_doc(MEMORY, key, for_update=True) if exists else frappe.get_doc({
                "doctype": MEMORY, "record_key": key, "workspace": workspace, "client": client or "",
                "seller_name": seller, "item_name": item_name, "use_count": 0})
            row.category, row.use_count = category, int(row.use_count or 0) + 1
            _persist(row)
    _optional(write, None, "learn")
