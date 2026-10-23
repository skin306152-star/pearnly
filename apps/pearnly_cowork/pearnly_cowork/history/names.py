"""识别入库时统一买卖方名称与品名，复核时记住人定下的写法（判定口径见 `contracts/recognized_names.py`）。

- 入库（`prepare`）：同一税号人改正过名称、这次读出的又是相近写法 → 换成人定的。只有数字 / 泰文单位的「品名」
  清空；空品名按「卖方税号 + 单价」以前人补过、而且只补过一种的名称补上，补不出就留空。
- 复核保存（`learn`）：人改过的名称按税号记住；人补的品名按「卖方税号 + 单价」记住。
  同一原件里还没人动过的草稿、相近写法的跟着改（`renamed`，由 `storage` 写回）。

只改值，不加提示、不让人选（用户 2026-10-02：录入口只提升识别质量、把结果显示出来）。
模型原读法保存在记录的 `ai_raw`，这里只改入库后的票面。记忆是工作空间内的私有表，原生接口一律拒绝。
"""
import copy
import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document

from pearnly_cowork.access import scope
from pearnly_cowork.contracts import recognized_names as rules
from pearnly_core.express_rules.contracts.history import primary_page

MEMORY = "Cowork Name Memory"
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def native_query(user=None):
    # Throw, not 1=0: native DocShare adds an OR outside query conditions.
    deny()


def reject_share(doc, method=None):
    if doc.share_doctype == MEMORY:
        deny()


class NameMemoryDocument(Document):
    def check_permission(self, *args, **kwargs):
        if self.flags.cowork_name_write is not _WRITE:
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


def _optional(function, fallback, label):
    # 记忆读写失败不能丢掉已付费的识别结果或人的修改：回滚这一小段，照常往下走。
    point = "cowork_names_" + label
    frappe.db.savepoint(point)
    try:
        return function()
    except (frappe.PermissionError, frappe.QueryDeadlockError):
        raise
    except Exception as error:
        frappe.db.rollback(save_point=point)
        frappe.logger("pearnly_cowork").warning("name memory %s unavailable: %s", label, type(error).__name__)
        return fallback


def _fields(pages):
    page = primary_page(pages) if isinstance(pages, list) else {}
    fields = page.get("fields") if isinstance(page, dict) else None
    return page, fields if isinstance(fields, dict) else None


def _party_memory(workspace, tax):
    rows = frappe.db.sql(f"SELECT value FROM `tab{MEMORY}` WHERE name=%s", (_key(workspace, "party", tax),))
    return rows[0][0] if rows else None


def _item_memory(workspace, tax, price):
    # 同一卖方同一单价人补过两种以上的名称（卖的不是同一样东西）就不补，留给人。
    rows = frappe.db.sql(f"""SELECT DISTINCT value FROM `tab{MEMORY}`
        WHERE workspace=%s AND kind='item' AND tax_id=%s AND price=%s LIMIT 2""", (workspace, tax, price))
    return rows[0][0] if len(rows) == 1 else None


def prepare(workspace, pages):
    """可信的识别入库调用方专用；不改调用方冻结的输入（指纹按原样算）。"""
    scope.require("intake")
    scope.require_scope(workspace)
    prepared = copy.deepcopy(pages)
    fields = _fields(prepared)[1]
    if fields is None:
        return prepared
    for party in rules.PARTIES:
        field, tax = f"{party}_name", rules.tax_key(fields.get(f"{party}_tax"))
        if not tax or not rules.name_key(fields.get(field)):
            continue
        remembered = _optional(lambda: _party_memory(workspace, tax), None, "party")
        if remembered and rules.similar(remembered, fields.get(field)):
            fields[field] = remembered
    seller_tax = rules.tax_key(fields.get("seller_tax"))
    for item in fields.get("items") or []:
        if not isinstance(item, dict):
            continue
        if rules.placeholder_item_name(item.get("name")):
            item["name"] = ""
        price = rules.price_key(item.get("price"))
        if not rules.clean_name(item.get("name")) and seller_tax and price:
            remembered = _optional(lambda: _item_memory(workspace, seller_tax, price), None, "item")
            if remembered:
                item["name"] = remembered
    return prepared


def _remember(workspace, kind, tax, value, price=""):
    key = _key(workspace, "party", tax) if kind == "party" else _key(workspace, "item", tax, price,
                                                                       rules.name_key(value))
    exists = frappe.db.get_value(MEMORY, key, "name", for_update=True)
    row = frappe.get_doc(MEMORY, key, for_update=True) if exists else frappe.get_doc({
        "doctype": MEMORY, "record_key": key, "workspace": workspace, "kind": kind, "tax_id": tax,
        "price": price, "value": value, "use_count": 0})
    row.value, row.use_count = value, int(row.use_count or 0) + 1
    row.flags.cowork_name_write = _WRITE
    row.flags.ignore_permissions = True
    row.flags.notify_update = False
    row.insert(ignore_permissions=True) if row.is_new() else row.save(ignore_permissions=True)


def learn(workspace, previous_pages, pages):
    """复核保存（已过权限与版本检查）之后调用 → 这次人定下的 ``{税号: 名称}``（给同一原件的草稿跟着改）。"""
    scope.require("intake")
    scope.require_scope(workspace)
    before, after = _fields(previous_pages)[1] or {}, _fields(pages)[1] or {}
    decided = {}
    for party in rules.PARTIES:
        field, tax = f"{party}_name", rules.tax_key(after.get(f"{party}_tax"))
        name = rules.clean_name(after.get(field))
        if tax and rules.name_key(name) and name != rules.clean_name(before.get(field)):
            decided[tax] = name
    learned = []
    seller_tax = rules.tax_key(after.get("seller_tax"))
    earlier = before.get("items") if isinstance(before.get("items"), list) else []
    for position, item in enumerate(after.get("items") or []):
        if not isinstance(item, dict) or not seller_tax:
            continue
        name, price = rules.clean_name(item.get("name")), rules.price_key(item.get("price"))
        old = earlier[position] if position < len(earlier) and isinstance(earlier[position], dict) else {}
        if name and price and not rules.placeholder_item_name(name) and name != rules.clean_name(old.get("name")):
            learned.append((price, name))

    def write():
        for tax, name in decided.items():
            _remember(workspace, "party", tax, name)
        for price, name in learned:
            _remember(workspace, "item", seller_tax, name, price)
    _optional(write, None, "learn")
    return decided


def renamed(pages, decided):
    """同一原件的另一张草稿：同一税号、相近写法的名称换成人刚定的 → 新的 pages；没有要换的回 None。"""
    prepared = copy.deepcopy(pages)
    fields = _fields(prepared)[1]
    if not fields or not decided:
        return None
    changed = False
    for party in rules.PARTIES:
        field, tax = f"{party}_name", rules.tax_key(fields.get(f"{party}_tax"))
        if (tax in decided and rules.clean_name(fields.get(field)) != decided[tax]
                and rules.similar(decided[tax], fields.get(field))):
            fields[field] = decided[tax]
            changed = True
    return prepared if changed else None


__all__ = ["MEMORY", "NameMemoryDocument", "deny", "learn", "native_query", "prepare",
           "reject_share", "renamed"]
