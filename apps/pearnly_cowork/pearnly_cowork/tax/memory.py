"""预扣税所得类型「问一次记住」（原生 DocType ``Cowork WHT Choice``）。

工作空间内按收款方税号记（``payee:<税号>``）；收款方没记过时按识别出的分类记（``category:<分类>``）兜底。
同一处还可以记这个收款方归哪张表、条件码。照推送选择 / 对账预设的写法：只经本模块写，原生接口的读、写、删、分享
一律拒绝（hooks）；每条记下谁、何时、从哪份草稿定的。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from pearnly_cowork.tax import access, wht

DOCTYPE = "Cowork WHT Choice"
FIELDS = ("income_type", "income_text", "form", "condition")
SAVEPOINT = "cowork_wht_choice"
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def guard(doc):
    if doc.flags.cowork_wht_choice_write is not _WRITE:
        deny()


def _invalid():
    frappe.throw(_("tax.answer_invalid"))


def scope_of(payee_tax_id="", category="") -> str:
    """记在哪：有收款方税号按税号（须 13 位），否则按分类；都没有不合法。"""
    digits = wht.tax_id(payee_tax_id)
    if digits:
        if len(digits) != 13:
            _invalid()
        return "payee:" + digits
    key = wht.category_key(category)
    if not key:
        _invalid()
    return "category:" + key


def _key(workspace, scope) -> str:
    return hashlib.sha256(json.dumps([workspace, scope], ensure_ascii=False).encode()).hexdigest()


def choices(workspace) -> dict:
    """{范围: {income_type, income_text, form, condition}}。调用方（`tax.service`）已核过权限。"""
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace}, fields=["scope", *FIELDS],
                          limit_page_length=0)
    return {row.scope: {key: row.get(key) or "" for key in FIELDS}
            for row in rows if row.get("income_type") in wht.INCOME_TYPES}


def remember(workspace, scope, income_type, income_text="", form="", condition="", payee_name="", draft="") -> str:
    """记下 / 改掉一条（同一范围只有一条）。"""
    access.require(workspace)
    condition = str(condition or "")
    if (income_type not in wht.INCOME_TYPES or (form or "") not in ("", *wht.FORMS)
            or condition not in ("", *wht.CONDITIONS[wht.PND3]) or not scope or len(scope) > 140):
        _invalid()
    name = _key(workspace, scope)
    values = {"income_type": income_type,
              "income_text": str(income_text or "").strip()[:140] if income_type == "other" else "",
              "form": form or "", "condition": condition, "set_by": frappe.session.user, "set_at": now_datetime()}
    if payee_name:
        values["payee_name"] = str(payee_name).strip()[:200]
    if draft:
        values["source_draft"] = str(draft)[:140]
    # 只锁这一条（不锁工作空间行）；两个请求同时首次记同一范围时，后到的撞主键，回到保存点改写已有的那条
    if frappe.db.exists(DOCTYPE, name):
        doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    else:
        doc = frappe.get_doc({"doctype": DOCTYPE, "choice_key": name, "workspace": workspace, "scope": scope})
        _stamp(doc, values)
        frappe.db.savepoint(SAVEPOINT)
        try:
            doc.insert(ignore_permissions=True)
            return name
        except frappe.DuplicateEntryError:
            frappe.db.rollback(save_point=SAVEPOINT)
            doc = frappe.get_doc(DOCTYPE, name, for_update=True)
    _stamp(doc, values)
    doc.save(ignore_permissions=True)
    return name


def _stamp(doc, values):
    doc.update(values)
    doc.flags.cowork_wht_choice_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True


def forget(workspace, scope) -> bool:
    """删掉一条：以后同一收款方 / 分类重新问。"""
    access.require(workspace)
    name = _key(workspace, scope)
    if not frappe.db.exists(DOCTYPE, name):
        return False
    frappe.db.get_value(DOCTYPE, name, "name", for_update=True)
    # 删除时框架重新取文档，对象上的标记带不过去：删除期间在本请求的标记上放同一个对象。
    frappe.flags.cowork_wht_choice_delete = _WRITE
    try:
        frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, force=True)
    finally:
        frappe.flags.cowork_wht_choice_delete = None
    return True


def listing(workspace) -> list:
    access.require(workspace)
    rows = frappe.get_all(DOCTYPE, filters={"workspace": workspace},
                          fields=["scope", "payee_name", *FIELDS, "source_draft", "set_by", "set_at"],
                          order_by="set_at desc", limit_page_length=500)
    return [{**row, "set_at": str(row.set_at or "")} for row in rows]


class ChoiceDocument(Document):
    """只接受本模块的写入（对象标记伪造不了）；范围与工作空间写定后不改。通用读、写、删由 hooks 拒绝。"""

    def autoname(self):
        guard(self)
        self.name = self.choice_key

    def before_validate(self):
        guard(self)

    def validate(self):
        guard(self)
        previous = self.get_doc_before_save()
        if previous and any(self.get(field) != previous.get(field) for field in ("choice_key", "workspace", "scope")):
            deny()

    before_insert = before_validate

    def on_trash(self):
        if frappe.flags.get("cowork_wht_choice_delete") is not _WRITE:
            deny()

    def before_rename(self, *args, **kwargs):
        deny()
