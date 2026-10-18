"""账套选择的存取（规则见 `push/express/choices.py`）：按「连接 + 账套 + 类别 + 归类键」记会计选的一个值。

只经推送服务写（调用方已按这张票与这条连接的推送权限核过）；原生接口的读、建、改、删、分享一律拒绝。
每次写入记下谁、什么时候选的，重复写同一个值不产生新版本。

改值记录（`Cowork Express Choice Change`，数字会计员第 3 期 R1，10-18）：账套记忆里科目、现 / 赊（`automation.OVERRIDE_KINDS`）记过的值被人
改成别的——复核页 / 待处理页选的（`service.save_choices`）、复核页改的付款方式推出去时记下的（`remember_payment`）、主档页改的
（`companies.choices.set_value`）都经 `record`；存公司规则顶掉同键记忆的经 `forget(replaced_by=…)`——各记一行「账套、类、归类键、
旧值 → 新值、谁、何时」。自动推判档时 `overrides` 取这份账套近 `automation.OVERRIDE_DAYS` 天的（一次查询、≤ `OVERRIDE_SCAN_MAX` 行），
`automation.overruled` 判：别家（或这家）刚被人改掉的同一个记忆值，这家凭改法之前攒的证据不再算规则定。保留 `OVERRIDE_KEEP_DAYS`
天：每记一行顺手删这份账套更早的（零常驻、不加定时任务）。同一事务里写，推送准备整笔回滚时（复核页「要选的项」、自动推判定只看不记）
一起回滚。
"""
import datetime as dt
import hashlib
import json

import frappe
from frappe import _
from frappe.utils import now_datetime

from pearnly_cowork.push.express.automation import OVERRIDE_DAYS, OVERRIDE_KINDS
from pearnly_cowork.push.express.choices import KINDS
from pearnly_cowork.targets.identity import normalize_express_account_key

DOCTYPE = "Cowork Express Choice"
OVERRIDE_DOCTYPE = "Cowork Express Choice Change"
DOCTYPES = (DOCTYPE, OVERRIDE_DOCTYPE)
# 改值记录留多久（> R1 的 60 天窗口，留一个季度给统计与复核）与一次最多取几行（按改的时间新 → 旧）
OVERRIDE_KEEP_DAYS = 90
OVERRIDE_SCAN_MAX = 200
_WRITE = object()


def deny(*args, **kwargs):
    frappe.throw(_("Not Permitted"), frappe.PermissionError)


def deny_query(*args, **kwargs):
    return "1=0"


def reject_share(doc, method=None):
    if doc.share_doctype in DOCTYPES:
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
    before = str(doc.value or "").strip() if existing else ""
    doc.update({"value": value, "label": str(label or "")[:200], "chosen_by": frappe.session.user,
                "chosen_at": now_datetime(), "record_revision": int(doc.record_revision or 0) + 1})
    doc.flags.cowork_choice_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.flags.ignore_version = True
    if existing:
        doc.save(ignore_permissions=True)
    else:
        doc.insert(ignore_permissions=True)
    _log_override(connection, account_set, kind, subject, before, value, "choice")
    return True


def _log_override(connection, account_set, kind, subject, before, after, source):
    """R1 的改值记录：账套记忆里这一类记过的值 ``before`` 被人改成 ``after``（`OVERRIDE_KINDS`；第一次记、同值不记）。顺手删这份
    账套 `OVERRIDE_KEEP_DAYS` 天前的（按账套、按时间，参数化）。``account_set`` 已规范化。"""
    before, after = str(before or "").strip(), str(after or "").strip()
    if kind not in OVERRIDE_KINDS or not before or before == after:
        return
    now = now_datetime()
    doc = frappe.get_doc({"doctype": OVERRIDE_DOCTYPE, "connection": connection, "account_set": account_set,
                          "kind": kind, "subject": subject, "old_value": before[:60], "new_value": after[:60],
                          "changed_by": frappe.session.user, "changed_at": now, "source": source})
    doc.flags.cowork_choice_write = _WRITE
    doc.flags.ignore_permissions = True
    doc.insert(ignore_permissions=True)
    frappe.db.sql(f"""DELETE FROM `tab{OVERRIDE_DOCTYPE}` WHERE connection=%s AND account_set=%s AND changed_at < %s""",
                  (connection, account_set, now - dt.timedelta(days=OVERRIDE_KEEP_DAYS)))


def overrides(connection, account_set, values, since=None) -> list:
    """R1：这份账套里近 `automation.OVERRIDE_DAYS` 天（或 ``since`` 之后）有人把账套记忆的这些值改掉的记录 ``[{kind, old, new,
    at}]``（新 → 旧，最多 `OVERRIDE_SCAN_MAX` 行）。``values``：``{类: 记忆的值}``（科目码、现 / 赊 cash / credit，只认
    `OVERRIDE_KINDS`）；都没有就不查库。一次查询，走 ``connection_kind_changed_index``（连接 + 类 + 时间）。只读，调用方已核过权限。"""
    wanted = {kind: str(value or "").strip() for kind, value in (values or {}).items()
              if kind in OVERRIDE_KINDS and str(value or "").strip()}
    account_set = normalize_express_account_key(account_set)
    if not connection or not account_set or not wanted:
        return []
    since = since or (now_datetime() - dt.timedelta(days=OVERRIDE_DAYS))
    rows = frappe.db.sql(f"""SELECT kind, old_value, new_value, changed_at FROM `tab{OVERRIDE_DOCTYPE}`
        WHERE connection=%s AND kind IN %s AND changed_at >= %s AND account_set=%s AND old_value IN %s
        ORDER BY changed_at DESC LIMIT %s""",
                         (connection, tuple(sorted(wanted)), since, account_set, tuple(sorted(set(wanted.values()))),
                          OVERRIDE_SCAN_MAX), as_dict=True)
    return [{"kind": row.kind, "old": row.old_value, "new": row.new_value or "", "at": row.changed_at} for row in rows]


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


def forget(connection, account_sets, kind, subjects, replaced_by=None) -> int:
    """删掉这几份账套里这一类、这些归类键的选择（真删）：一家公司的所有年度一起删，否则 `latest()` 会从别的年度把它
    找回来。回删掉的条数。调用方已核过这条连接的推送权限。``replaced_by``：存公司规则顶掉同键记忆时规则的值（`companies.choices`），
    记过的值与它不同就记一行改值（R1，来源 ``rule``）；主档页直接删一条不给（删不是改成别的）。"""
    sets = sorted({normalize_express_account_key(item) for item in account_sets or () if item})
    subjects = sorted({subject for subject in subjects or () if isinstance(subject, str) and subject})
    if not connection or not sets or not subjects or kind not in KINDS:
        return 0
    rows = frappe.db.sql(f"""SELECT name, account_set, subject, value FROM `tab{DOCTYPE}` WHERE connection=%s AND kind=%s
        AND account_set IN %s AND subject IN %s FOR UPDATE""", (connection, kind, tuple(sets), tuple(subjects)),
                         as_dict=True)
    if rows:
        frappe.db.delete(DOCTYPE, {"name": ["in", [row.name for row in rows]]})
        if replaced_by is not None:
            for row in rows:
                _log_override(connection, row.account_set, kind, row.subject, row.value, replaced_by, "rule")
    return len(rows)


__all__ = ["CHOICE_FIELDS", "DOCTYPE", "DOCTYPES", "OVERRIDE_DOCTYPE", "OVERRIDE_KEEP_DAYS", "OVERRIDE_SCAN_MAX", "deny",
           "deny_query", "forget", "guard", "latest", "of_sets", "overrides", "record", "reject_share", "remembered"]
