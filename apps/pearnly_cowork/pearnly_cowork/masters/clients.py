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
from pearnly_cowork.masters import buyer_resolve, client_import, client_records

CLIENT = "Cowork Client"
MEMORY = "Cowork Buyer Memory"
HISTORY = "Cowork OCR History"
MANAGER_FEATURE = "master_data"
# 名录导入的三态（旧侧 client_import_routes.py 的 reason 机器码）与"税号已占用"。
DUPLICATE_TAX = "workspace.tax_id_duplicate"
GENERIC_IMPORT_ERROR = "client_import.err_generic"
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
    if tax_id_taken(workspace, cleaned.get("tax_id")):
        frappe.throw(_("This tax id is already used by another client."), frappe.ValidationError)
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
    if cleaned.get("tax_id") and tax_id_taken(workspace, cleaned["tax_id"], exclude=doc.name):
        frappe.throw(_("This tax id is already used by another client."), frappe.ValidationError)
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


def _normalized_tax(value):
    """税号比较用的规范形：只留数字。

    旧侧那一闸是拿**原样字符串**比（`tax_id = %s`），所以同一家客户写成
    ``0-1055-61000-13-3`` 与 ``0105561000013`` 时旧侧会放过去两条档案；本仓按数字比，
    因为客户身份就靠这 13 位（前端查税号也是先剥非数字），留下同号两条会让买家匹配与
    ERP 对账都指错人。这是有意差异，记在批次记录里。
    """
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def normalized_tax_ids(workspace):
    """本公司在用客户的税号集合（一次查询；名录导入按行判重要用它）。"""
    rows = frappe.db.sql("""SELECT REGEXP_REPLACE(COALESCE(tax_id, ''), '[^0-9]', '')
        FROM `tabCowork Client` WHERE workspace = %s AND enabled = 1""", (workspace,))
    return {row[0] for row in rows if row[0]}


def tax_id_taken(workspace, tax_id, exclude=None):
    """同公司已有在用客户占了这个税号？（旧侧 tax_id_in_use：空税号永不算重复）"""
    normalized = _normalized_tax(tax_id)
    if not normalized:
        return False
    conditions = "workspace = %s AND enabled = 1 AND REGEXP_REPLACE(COALESCE(tax_id, ''), '[^0-9]', '') = %s"
    values = [workspace, normalized]
    if exclude:
        conditions += " AND name <> %s"
        values.append(exclude)
    return bool(frappe.db.sql(f"SELECT 1 FROM `tab{CLIENT}` WHERE {conditions} LIMIT 1",
        tuple(values)))


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


def _row_list(rows):
    """名录导入的行参数：JSON 字符串或列表，统一成列表；超上限按旧侧口径报错。"""
    if isinstance(rows, str):
        try:
            rows = json.loads(rows or "[]")
        except ValueError as error:
            raise frappe.ValidationError(_("The import rows are invalid.")) from error
    if not isinstance(rows, (list, tuple)):
        raise frappe.ValidationError(_("The import rows are invalid."))
    if len(rows) > client_import.MAX_ROWS:
        frappe.throw(_("Too many rows to import."), frappe.ValidationError)
    if any(not isinstance(row, dict) for row in rows):
        raise frappe.ValidationError(_("The import rows are invalid."))
    return list(rows)


def _import_row(workspace, row, *, dry_run, taken):
    """单行判定：结构校验 → 税号查重 → （预览只判）/ 建档。

    三态与旧侧同码：``error``（缺名/税号格式错）、``skip``（税号已被占用）、
    ``valid``（预览会建）与 ``created``（已建）。结果始终回显 name/tax_id，报错行也能看出
    是哪一行。字段映射照旧侧：行里的 ``phone`` 落到原生 ``contact_phone``。
    """
    name = str((row or {}).get("name") or "").strip()
    tax = str((row or {}).get("tax_id") or "").strip()
    base = {"row_index": (row or {}).get("row_index"), "name": name, "tax_id": tax}
    error = client_import.structural_error(name, tax)
    if error:
        return {**base, "status": "error", "reason": error}
    normalized = _normalized_tax(tax)
    if normalized and normalized in taken:
        return {**base, "status": "skip", "reason": DUPLICATE_TAX}
    if dry_run:
        return {**base, "status": "valid"}
    try:
        created = create_client(workspace, {"name": name, "tax_id": tax,
            "branch": row.get("branch"), "contact_phone": row.get("phone"),
            "address": row.get("address")})
    except frappe.ValidationError:
        return {**base, "status": "error", "reason": GENERIC_IMPORT_ERROR}
    if normalized:
        # 同一批里后面同样的税号要按 skip 走（旧侧是逐行走同一段校验，效果相同）。
        taken.add(normalized)
    return {**base, "status": "created", "id": created["name"]}


def _import_rows(workspace, rows, *, dry_run):
    taken = normalized_tax_ids(workspace)
    results = [_import_row(workspace, row, dry_run=dry_run, taken=taken)
        for row in _row_list(rows)]
    if dry_run:
        return {"preview": results, "total": len(results),
            "valid_count": sum(1 for row in results if row["status"] == "valid"),
            "skip_count": sum(1 for row in results if row["status"] == "skip"),
            "error_count": sum(1 for row in results if row["status"] == "error")}
    return {"results": results, "total": len(results),
        "created": sum(1 for row in results if row["status"] == "created"),
        "skipped": sum(1 for row in results if row["status"] == "skip"),
        "errors": sum(1 for row in results if row["status"] == "error")}


def preview_import(workspace, rows):
    """名录导入预览：逐行三态，**不写库**（旧侧 parse 端点的 dry_run 一路）。"""
    require_manager()
    scope.require_scope(workspace)
    return _import_rows(workspace, rows, dry_run=True)


def commit_import(workspace, rows):
    """确认后逐行落库；重导同一批时已建的行按税号全落 ``skip``（幂等）。"""
    require_manager()
    scope.require_scope(workspace)
    return _import_rows(workspace, rows, dry_run=False)
