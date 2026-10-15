"""账套选择的存取（规则见 `push/express/choices.py`）：按「连接 + 账套 + 类别 + 归类键」记会计选的一个值。

只经推送服务写（调用方已按这张票与这条连接的推送权限核过）；原生接口的读、建、改、删、分享一律拒绝。
每次写入记下谁、什么时候选的，重复写同一个值不产生新版本。
"""
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.push.express.choices import KINDS
from pearnly_cowork.targets.identity import normalize_express_account_key

DOCTYPE = "Cowork Express Choice"
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype == DOCTYPE:
        deny()


def guard(doc):
    if doc.flags.cowork_choice_write is not _WRITE:
        deny()


def _key(connection, account_set, kind, subject):
    return hashlib.sha256(json.dumps([connection, account_set, kind, subject], ensure_ascii=False)
                          .encode()).hexdigest()


def remembered(connection, account_set) -> dict:
    """这份账套里记过的选择：`{kind: {subject: value}}`（值为空的不算）。"""
    account_set = normalize_express_account_key(account_set)
    rows = frappe.db.sql(f"""SELECT kind, subject, value FROM `tab{DOCTYPE}`
        WHERE connection=%s AND account_set=%s AND COALESCE(value, '')!=''""", (connection, account_set), as_dict=True)
    found = {kind: {} for kind in KINDS}
    for row in rows:
        found.setdefault(row.kind, {})[row.subject] = row.value
    return found


def chosen_at(connection, account_set, kind, subject):
    """这份账套里这一项（类别 + 归类键）最近一次选的时间；没记过（或值为空）回 None。"""
    account_set = normalize_express_account_key(account_set)
    found = frappe.db.sql(f"""SELECT chosen_at FROM `tab{DOCTYPE}`
        WHERE connection=%s AND account_set=%s AND kind=%s AND subject=%s AND COALESCE(value, '')!=''
        LIMIT 1""", (connection, account_set, kind, subject))
    return found[0][0] if found else None


def latest(connection, account_sets, kind, subjects) -> dict:
    """几份账套里同一类别、这些归类键最近一次选的值：`{subject: value}`（按选的时间，新的优先；值为空的不算）。"""
    sets = sorted({normalize_express_account_key(item) for item in account_sets or () if item})
    subjects = sorted({subject for subject in subjects or () if isinstance(subject, str) and subject})
    if not sets or not subjects or kind not in KINDS:
        return {}
    rows = frappe.db.sql(f"""SELECT subject, value FROM `tab{DOCTYPE}`
        WHERE connection=%s AND kind=%s AND account_set IN %s AND subject IN %s AND COALESCE(value, '')!=''
        ORDER BY chosen_at DESC""", (connection, kind, tuple(sets), tuple(subjects)), as_dict=True)
    found = {}
    for row in rows:
        found.setdefault(row.subject, row.value)
    return found


def record(connection, account_set, kind, subject, value, label=""):
    """记一个选择（同键覆盖）；返回是否有变化。"""
    if kind not in KINDS or not isinstance(subject, str) or not subject or len(subject) > 1000:
        frappe.throw(_("connection.config_invalid"))
    value = str(value or "").strip()
    if len(value) > 60:
        frappe.throw(_("connection.config_invalid"))
    account_set = normalize_express_account_key(account_set)
    name = _key(connection, account_set, kind, subject)
    existing = frappe.db.exists(DOCTYPE, name)
    doc = frappe.get_doc(DOCTYPE, name, for_update=True) if existing else frappe.get_doc({
        "doctype": DOCTYPE, "input_key": name, "connection": connection, "account_set": account_set,
        "kind": kind, "subject": subject, "record_revision": 0})
    if existing and (doc.value or "") == value:
        return False
    doc.update({"value": value, "label": str(label or "")[:200], "chosen_by": frappe.session.user,
                "chosen_at": now_datetime(), "record_revision": int(doc.record_revision or 0) + 1})
    doc.flags.cowork_choice_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    return True


CHOICE_FIELDS = ["name", "account_set", "kind", "subject", "label", "value", "chosen_by", "chosen_at"]


def of_sets(connection, account_sets, limit=200) -> list:
    """一家公司各年度账套（公司主档，ADR-020）里记过的选择，新 → 旧，最多 `limit` 条（值为空的不算）。只读；调用方已核过
    这条连接的推送权限，合并同类（同一类别、同一归类键只留最新一条）由调用方做。"""
    sets = sorted({normalize_express_account_key(item) for item in account_sets or () if item})
    if not connection or not sets:
        return []
    return frappe.db.sql(f"""SELECT {", ".join(CHOICE_FIELDS)} FROM `tab{DOCTYPE}`
        WHERE connection=%s AND account_set IN %s AND COALESCE(value, '')!=''
        ORDER BY chosen_at DESC LIMIT %s""", (connection, tuple(sets), int(limit)), as_dict=True)


def forget(connection, account_sets, kind, subjects) -> int:
    """删掉这几份账套里这一类、这些归类键的选择（真删）：一家公司的所有年度一起删，否则 `latest()` 会从别的年度把它
    找回来。回删掉的条数。调用方已核过这条连接的推送权限。"""
    sets = sorted({normalize_express_account_key(item) for item in account_sets or () if item})
    subjects = sorted({subject for subject in subjects or () if isinstance(subject, str) and subject})
    if not connection or not sets or not subjects or kind not in KINDS:
        return 0
    names = frappe.db.sql(f"""SELECT name FROM `tab{DOCTYPE}` WHERE connection=%s AND kind=%s AND account_set IN %s
        AND subject IN %s FOR UPDATE""", (connection, kind, tuple(sets), tuple(subjects)), pluck=True)
    if names:
        frappe.db.delete(DOCTYPE, {"name": ["in", list(names)]})
    return len(names)


__all__ = ["CHOICE_FIELDS", "DOCTYPE", "deny", "deny_query", "forget", "guard", "latest", "of_sets", "record",
           "reject_share", "remembered"]
