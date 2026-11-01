"""物料分类（岳父 11-01：分类要能写中 / 泰 / 英文字和数字，例如拿来记尺寸）。

原来商品上的「分类」是一格裸整数（旧系统 `products.category_id` 照搬，两边都没有分类表）；现在是主档 `ERP Product Category`，
商品的 `category` 选它（原生 Link，打字服务端搜，下拉里原地新建 / 改名，`products.js` 的 `link_editors`）。

- 范围：租户（站点）内共用，不按公司分 —— 本地商品本来就是各公司共用的，分类跟着商品走；Express 没有对应（商品组 STKGRP 不接）。
- 权限（主会话 11-01 定）：读跟 `products.view`；新建要 `products.manage`，或盘点里就地新建商品的 `count.write`；改名 / 停用 / 删除只给
  `products.manage`。控制器（`ERPProductCategory`）在原生写入上同样判，原生接口绕不过去。
- 同名（数据库排序规则下不分大小写）不许重复；被商品用着的不能删，只能停用；停用的不进选择框，已挂的商品照常显示。
"""

import frappe
from frappe import _

from pearnly_erp_portal.access import guard

DOCTYPE = "ERP Product Category"
MAX_NAME = 140
NAME_REQUIRED = "A category needs a name"
NAME_EXISTS = "This category already exists"
NOT_FOUND = "Category not found"
CREATE_CAPS = ("products.manage", "count.write")


def can_create(user=None) -> bool:
    return any(guard.has(cap, user) for cap in CREATE_CAPS)


def require_create(user=None) -> None:
    if not can_create(user):
        frappe.throw(_("Not permitted"), frappe.PermissionError)


def _clean(value) -> str:
    return " ".join(str(value or "").split())[:MAX_NAME]


def find(text) -> str | None:
    """按名称找（库的排序规则比，不分大小写，泰文同 `products.service.taken` 口径），停用的也算。"""
    text = _clean(text)
    return frappe.db.get_value(DOCTYPE, {"category_name": text}, "name") if text else None


def resolve(value) -> str | None:
    """页面给的是分类单号（Link 的值），导入 / LINE 页给的可能是名称：两种都认；认不到回 None。"""
    value = str(value or "").strip()
    if not value:
        return None
    if frappe.db.exists(DOCTYPE, value):
        return value
    return find(value)


def require(value) -> str | None:
    """建档 / 改商品时收到的分类：空就是不设；给了就必须是现有分类（停用的只是不进选择框，原样带回来照收）。"""
    if not str(value or "").strip():
        return None
    name = resolve(value)
    if not name:
        frappe.throw(_(NOT_FOUND))
    return name


def create(category_name) -> dict:
    require_create()
    text = _clean(category_name)
    if not text:
        frappe.throw(_(NAME_REQUIRED))
    if find(text):
        frappe.throw(_(NAME_EXISTS))
    doc = frappe.get_doc({"doctype": DOCTYPE, "category_name": text, "is_active": 1})
    doc.insert(ignore_permissions=True)  # 权限上面已判（盘点岗也能建），原生 DocPerm 不给盘点岗写
    return {"name": doc.name, "category_name": doc.category_name, "is_active": 1}


def ensure(category_name) -> str | None:
    """导入表里写的分类名称：有就用，没有且有权新建就建（同一份校验）；没权新建报「分类不存在」。"""
    text = _clean(category_name)
    if not text:
        return None
    return find(text) or (create(text)["name"] if can_create() else require(text))


def accept(value) -> str | None:
    """盘点里就地新建商品时收到的分类：Desk 选的是分类单号，LINE 页与盘点表写的是名称 —— 认得就用，名称没有就建
    （盘点录入权也能建，主会话 11-01 批复 ③）。"""
    return resolve(value) or ensure(value)


def active_names(limit=200) -> list:
    """在用分类的名称（LINE 盘点页新建卡片那一格的候选，打字也照收）。"""
    guard.require("products.view")
    return frappe.get_list(DOCTYPE, filters={"is_active": 1}, pluck="category_name", order_by="category_name asc",
                           limit_page_length=int(limit))


def update(name, *, category_name=None, is_active=None) -> dict:
    guard.require("products.manage")
    if not frappe.db.exists(DOCTYPE, name or ""):
        frappe.throw(_(NOT_FOUND))
    doc = frappe.get_doc(DOCTYPE, name)
    if category_name is not None:
        text = _clean(category_name)
        if not text:
            frappe.throw(_(NAME_REQUIRED))
        if find(text) not in (None, doc.name):
            frappe.throw(_(NAME_EXISTS))
        doc.category_name = text
    if is_active is not None:
        doc.is_active = 1 if str(is_active) in ("1", "True", "true") else 0
    doc.save(ignore_permissions=True)
    return {"name": doc.name, "category_name": doc.category_name, "is_active": doc.is_active}


def get(name) -> dict:
    guard.require("products.view")
    row = frappe.db.get_value(DOCTYPE, name or "", ["name", "category_name", "is_active"], as_dict=True)
    if not row:
        frappe.throw(_(NOT_FOUND))
    return {**row, "can_edit": guard.has("products.manage")}


def names(values) -> dict:
    """分类单号 → 名称（显示用，一次取完；系统内部取数，不按人过滤 —— 能看到商品就能看到它的分类名）。"""
    values = sorted({value for value in values if value})
    if not values:
        return {}
    return dict(frappe.get_all(DOCTYPE, filters={"name": ["in", values]}, fields=["name", "category_name"],  # bounded: 这批商品的分类
                               as_list=True, limit_page_length=0))


def matching(text) -> list:
    """按名称模糊找分类单号（商品列表搜索、库存一览搜索把分类名也算进去）。"""
    text = str(text or "").strip()
    if not text:
        return []
    return frappe.get_all(DOCTYPE, filters={"category_name": ["like", f"%{text}%"]}, pluck="name",
                          limit_page_length=50)


def spec(category_label, attribute) -> str:
    """「分类 · 特性」一行（打印副行、品名后缀、选择下拉）：有哪格印哪格。"""
    return " · ".join(part for part in (str(category_label or "").strip(), str(attribute or "").strip()) if part)


@frappe.whitelist(methods=["POST"])
def api_create_category(category_name=None, **kwargs):
    return create(category_name)


@frappe.whitelist(methods=["POST"])
def api_update_category(name=None, category_name=None, is_active=None, **kwargs):
    return update(name, category_name=category_name, is_active=is_active)


@frappe.whitelist()
def api_get_category(name=None, **kwargs):
    return get(name)
