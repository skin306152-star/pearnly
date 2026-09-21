# -*- coding: utf-8 -*-
"""客户名册的存储层：档案 CRUD、发票归属、买家记忆与买家解析。

来源：ec2e5c957e6b ``services/clients/store.py`` 的 CRUD（字段规则已在
``masters/client_records.py``）与 ``services/clients/buyer_resolve.py`` 的写路径
（``learn_buyer_to_client`` / ``try_resolve_buyer_to_client`` / ``resolve_or_create``）
以及 ``list_clients`` 的三个聚合。

**原生与旧侧的结构性差异（都是有意为之）**：

1. 作用域从"用户 + 租户"换成**公司（`Cowork Workspace`）**：本仓的权限模型是 User Permission
   绑公司/客户（`access.scope`），所以每张客户档案都属于一个公司，读写都先过 `require_scope`；
2. **买家记忆的唯一键仍是那三段**：旧侧靠数据库唯一索引
   `(COALESCE(tenant_id::text,user_id::text), LOWER(buyer_name), COALESCE(buyer_tax,''))`；
   原生把买家名**小写后存**，按（公司 + 小写名 + 税号）查 —— 同一买家同一公司只可能有一行，
   与旧索引同一语义，且不依赖文档名的生成方式；
3. **"把发票改挂到别的客户"与"删客户时级联解绑"这两条写路径没有搬**：原生 history 域把
   记录的 `client` 定为**不可变**（`storage.IMMUTABLE`），只允许复核服务带着令牌改记录 ——
   那是本平台有意加的保护。绕过它去 `set_value` 会把这道闸拆掉，所以本模块的删除改为
   **还有发票就拒绝并报数**，改挂归属要等 history 域的复核契约开口（属跨域决定，不是主数据
   自己能定的）。

写路径的判定全部复用前两批的纯模块：字段清洗用 `client_records`，归属决策用 `buyer_resolve`。
本模块只负责"读数据、写文档、过权限"，不做判定。
"""

import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.access import scope
from pearnly_cowork.masters import buyer_resolve, client_records

CLIENT = "Cowork Client"
MEMORY = "Cowork Buyer Memory"
HISTORY = "Cowork OCR History"
MANAGER_FEATURE = "master_data"
# 名册投影：旧侧 `SELECT c.*` 的字段（display_name 对应旧侧 name）。
CLIENT_FIELDS = ("name", "workspace", "display_name", "short_name", "tax_id", "party_type",
    "branch", "promptpay_id", "address", "contact_person", "contact_phone", "contact_email",
    "notes", "color", "enabled")


def require_manager():
    """写路径要求主数据功能 + 公司管理员（与既有 RPC 同口径）。"""
    scope.require(MANAGER_FEATURE)
    if not scope.administrator():
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def require_reader():
    """读路径：识别记录或主数据任一功能即可（旧侧"同租户成员都能看"的对应物）。"""
    if not any(scope.allowed(feature) for feature in (*scope.READ_FEATURES, MANAGER_FEATURE)):
        frappe.throw(_("Not Permitted"), frappe.PermissionError)


def _project(doc):
    return {field: doc.get(field) for field in CLIENT_FIELDS if field != "name"} | {
        "name": doc.name}


def _aggregates(workspace, client_names):
    """每个客户的发票数 / 合计金额 / 最近一张时间（旧侧 list_clients 的三个子查询）。"""
    if not client_names:
        return {}
    rows = frappe.db.get_all(HISTORY, filters={"workspace": workspace,
        "client": ("in", list(client_names))},
        fields=["client", {"COUNT": "name", "as": "invoice_count"},
            {"SUM": "total_amount", "as": "total_amount"},
            {"MAX": "creation", "as": "last_invoice_at"}],
        group_by="client")
    return {row["client"]: row for row in rows}


def list_clients(workspace, start=0, limit=50, include_inactive=False):
    """名册列表：部署版排序 `is_active DESC, name ASC` + 三个聚合。"""
    require_reader()
    scope.require_scope(workspace)
    start, limit = max(0, int(start)), max(1, min(int(limit), 500))
    if include_inactive:
        # The ORM permission query for this DocType hides disabled clients by design
        # (`enabled=1` is part of it), so the "include inactive" branch reads through
        # the database after the service's own checks: the feature gate and
        # `require_scope(workspace)` above already cover exactly this company.
        rows = frappe.db.get_all(CLIENT, filters={"workspace": workspace},
            fields=list(CLIENT_FIELDS),
            order_by="enabled desc, display_name asc, name asc",
            limit_start=start, limit_page_length=limit)
    else:
        rows = frappe.get_list(CLIENT, filters={"workspace": workspace, "enabled": 1},
            fields=list(CLIENT_FIELDS), order_by="enabled desc, display_name asc, name asc",
            limit_start=start, limit_page_length=limit)
    aggregates = _aggregates(workspace, [row["name"] for row in rows])
    for row in rows:
        totals = aggregates.get(row["name"]) or {}
        row["invoice_count"] = int(totals.get("invoice_count") or 0)
        row["total_amount"] = float(totals.get("total_amount") or 0)
        row["last_invoice_at"] = totals.get("last_invoice_at")
    return rows


def get_client(workspace, name):
    require_reader()
    scope.require_scope(workspace)
    doc = frappe.get_doc(CLIENT, name)
    if doc.workspace != workspace:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    return _project(doc)


def create_client(workspace, values):
    """建档：字段按部署版规则清洗；空名不建档（旧侧返回 None，这里显式报错）。"""
    require_manager()
    scope.require_scope(workspace)
    cleaned = client_records.create_values(_scalar(values))
    if cleaned is None:
        frappe.throw(_("A client needs a name."))
    doc = frappe.get_doc({"doctype": CLIENT, "workspace": workspace,
        "display_name": cleaned["name"], "enabled": 1, **{
            field: cleaned[field] for field in cleaned if field != "name"}})
    doc.insert()
    return _project(doc)


def update_client(workspace, name, values):
    """更新：只写给了值的字段；一个都没有就不动（旧侧返回 False）。"""
    require_manager()
    scope.require_scope(workspace)
    doc = frappe.get_doc(CLIENT, name)
    if doc.workspace != workspace:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    cleaned = client_records.update_values(_native_names(_scalar(values)))
    if cleaned is None:
        return {"name": doc.name, "updated": False}
    for field, value in cleaned.items():
        if field == "name":
            doc.display_name = value
        elif field == "is_active":
            doc.enabled = 1 if value else 0
        else:
            doc.set(field, value)
    doc.save()
    return {"name": doc.name, "updated": True, "client": _project(doc)}


def delete_client(workspace, name):
    """删除一张客户档案；**它下面还有发票时拒绝**（见模块说明里那条不迁的差异）。"""
    require_manager()
    scope.require_scope(workspace)
    doc = frappe.get_doc(CLIENT, name)
    if doc.workspace != workspace:
        frappe.throw(_("Not Permitted"), frappe.PermissionError)
    linked = frappe.db.count(HISTORY, {"workspace": workspace, "client": name})
    if linked:
        # The deployed code silently unbound these records; here the record's client is
        # immutable by the history domain's own contract, so the honest answer is to
        # refuse and say how many records are in the way.
        frappe.throw(_("This client still has {0} invoice record(s). Reassign them first.")
            .format(linked))
    frappe.delete_doc(CLIENT, name, ignore_permissions=True)
    return {"name": name, "deleted": True}


def _memory_filter(workspace, buyer_name, buyer_tax):
    """The deployed unique key, expressed as fields: company + lowercased name + tax.

    The old schema made the pair unique through a database index on
    ``LOWER(buyer_name)``; here the name is stored lowercased and the lookup uses the
    same three values, so one buyer can only have one row per company.
    """
    return {"workspace": workspace, "buyer_name": (buyer_name or "").strip().lower(),
            "buyer_tax": (buyer_tax or "").strip()}


def memory_row(workspace, buyer_name, buyer_tax):
    """The learned mapping for this buyer, as ``{client_id, client_name}``, or None."""
    if not buyer_name or not buyer_name.strip():
        return None
    rows = frappe.get_all(MEMORY, filters=_memory_filter(workspace, buyer_name, buyer_tax),
        fields=["client"], limit_page_length=1)
    client = rows and rows[0].get("client")
    if not client or not frappe.db.exists(CLIENT, {"name": client, "workspace": workspace,
            "enabled": 1}):
        return None
    return {"client_id": client, "client_name": frappe.db.get_value(CLIENT, client,
        "display_name")}


def learn_buyer(workspace, buyer_name, buyer_tax, client):
    """Learn buyer → client: an existing row is re-pointed and counted up, as deployed."""
    if not buyer_name or not buyer_name.strip() or not client:
        return None
    existing = frappe.get_all(MEMORY, filters=_memory_filter(workspace, buyer_name, buyer_tax),
        fields=["name"], limit_page_length=1)
    if existing:
        row = frappe.get_doc(MEMORY, existing[0]["name"])
        row.client = client
        row.use_count = int(row.use_count or 0) + 1
        row.last_used_at = now_datetime()
        row.save(ignore_permissions=True)
        return row.name
    doc = frappe.get_doc({"doctype": MEMORY, "workspace": workspace,
        "buyer_name": (buyer_name or "").strip().lower()[:200],
        "buyer_tax": (buyer_tax or "").strip()[:30], "client": client, "use_count": 1,
        "last_used_at": now_datetime()})
    doc.insert(ignore_permissions=True)
    return doc.name


def _scoped_clients(workspace):
    """匹配层看到的客户表（旧侧"启用中"的那一份），client_id 就是文档名。"""
    rows = frappe.get_list(CLIENT, filters={"workspace": workspace, "enabled": 1},
        fields=["name", "display_name", "short_name", "tax_id"],
        limit_page_length=0)
    return [{"client_id": row["name"], "name": row["display_name"],
        "short_name": row.get("short_name"), "tax_id": row.get("tax_id")} for row in rows]


def find_or_create_client_by_tax_id(workspace, tax_clean, buyer_name):
    """按税号查或建（去重）：命中同税号的启用客户就返回它，否则按税号建一张新档案。"""
    if not tax_clean:
        return None
    for row in _scoped_clients(workspace):
        if buyer_resolve.normalize_tax(row.get("tax_id")) == tax_clean:
            return row["client_id"]
    doc = frappe.get_doc({"doctype": CLIENT, "workspace": workspace,
        "display_name": (buyer_name or "").strip()[:200], "tax_id": tax_clean[:20],
        "color": client_records.DEFAULT_COLOR, "enabled": 1})
    doc.insert()
    return doc.name


def resolve_buyer(workspace, buyer_name, buyer_tax=None, buyer_candidates=None):
    """把买方解析到一个客户：数据从本模块取，判定用 ``buyer_resolve``（不写识别记录）。"""
    require_manager()
    scope.require_scope(workspace)
    return buyer_resolve.resolve_buyer_client(buyer_name, buyer_tax,
        memory=memory_row(workspace, buyer_name, buyer_tax),
        clients=_scoped_clients(workspace),
        buyer_candidates=buyer_candidates,
        create_by_tax_id=lambda tax, name: find_or_create_client_by_tax_id(workspace, tax, name),
        learn=lambda client: learn_buyer(workspace, buyer_name, buyer_tax, client))


def _native_names(values):
    """Accept the native field name on input as well as the deployed one.

    The deployed column is ``name``; the native DocType calls it ``display_name``
    (``name`` is the document id). Callers may use either, but not both.
    """
    if "display_name" not in values:
        return values
    if "name" in values:
        raise frappe.ValidationError(_("The client fields are invalid."))
    renamed = dict(values)
    renamed["name"] = renamed.pop("display_name")
    return renamed


def _scalar(values):
    """RPC 参数可能是 JSON 字符串（表单直接发）或字典，统一成字典。"""
    if values in (None, ""):
        return {}
    if isinstance(values, str):
        try:
            decoded = json.loads(values)
        except ValueError as error:
            raise frappe.ValidationError(_("The client fields are invalid.")) from error
        if not isinstance(decoded, dict):
            raise frappe.ValidationError(_("The client fields are invalid."))
        return decoded
    if not isinstance(values, dict):
        raise frappe.ValidationError(_("The client fields are invalid."))
    return values
