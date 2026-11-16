"""ERP 单据写进 Express 时要人拿主意的项，按公司选一次、记住复用（用户 10-23：要人拿主意的单在 ERP 里处理）。

哪几类、怎么判要不要问，是共用的写账规则（`pearnly_core.express_rules.push.choices.resolve`，与 Cowork 同一套）；这里只存选过的值：
``ERP Express Choice`` 按「公司 + 哪一类 + 对象」一行（对象就是写账规则给的 ``subject``）。另加两类 ERP 自己的：

- ``account_set``：这家公司同期有两份账套时用哪一份（按公司 + 会计年度记，用户 10-17「两份同期账套按公司按年度问一次」）；
- ``stock_group``：要新建库存品时挂哪个存货科目组（账套一个库存品都没有，或已有库存品、这张单有要新建的、合格的组两个以上，写账规则回
  ``stock_acc_group_required``，11-16 真机 H5 起后一种也问）；
- ``operator_form``：代录停在定制菜单的子菜单、里面几个命令时这份账套这种单走哪一个（对象 = 「账套 | 单据种类」，值 = 菜单文字，
  `pearnly_core.express_operator.menu_choice`，评估 2026-11-05 修法 D）。它不是写账规则：不进 :func:`remembered`（写账准备的输入照旧），
  交代录时经 :func:`operator_form` 读、带进载荷 ``operator.form_choice``。

选过的值在账套里已经没有了（被删 / 改了），写账规则当没选过、重新问，不静默换别的。

写账前置判定（`express.precheck`，开始前确认卡片）要一次收齐所有要人定的项：判定时把预选值或人刚答的值临时当作选过
（:func:`assuming`，只在这次判定的调用里、不写库），写账准备才走得到后面几道。真正写入时不在这个范围里，读的只有记住的值。
"""
import contextlib
import contextvars
import hashlib

import frappe

from pearnly_core.express_operator.menu_choice import KIND as OPERATOR_FORM

CHOICE = "ERP Express Choice"
ACCOUNT_SET = "account_set"
STOCK_GROUP = "stock_group"


_ASSUMED = contextvars.ContextVar("erp_express_assumed", default=None)


@contextlib.contextmanager
def assuming(company, values):
    """这个范围里把 ``values``（``{kind: {subject: value}}``）当作这家公司选过的（判定专用，不写库）。"""
    token = _ASSUMED.set((company, values or {}))
    try:
        yield
    finally:
        _ASSUMED.reset(token)


def assumed(company, kind, subject) -> str:
    """判定范围里临时当作选过的值；不在范围里、不是这家公司回空。"""
    found = _ASSUMED.get()
    if not found or found[0] != company:
        return ""
    return str((found[1].get(kind) or {}).get(subject) or "")


def _name(company, kind, subject):
    return "EXC-" + hashlib.sha256("\0".join([company, kind, subject]).encode("utf-8")).hexdigest()[:24]


def remembered(company) -> dict:
    """``{kind: {subject: value}}``：这家公司选过的（代录子菜单 ``operator_form`` 不在内：不是写账规则）。"""
    found = {}
    for row in frappe.get_all(CHOICE, filters={"company": company, "kind": ["!=", OPERATOR_FORM]},
                              fields=["kind", "subject", "value"], limit_page_length=0):
        found.setdefault(row.kind, {})[row.subject or ""] = row.value or ""
    current = _ASSUMED.get()
    if current and current[0] == company:
        for kind, values in current[1].items():
            found.setdefault(kind, {}).update({subject: value for subject, value in values.items() if value})
    return found


def chosen(company, kind, subject) -> str:
    return assumed(company, kind, subject) or frappe.db.get_value(CHOICE, _name(company, kind, subject), "value") or ""


def remember(company, kind, subject, value, label=""):
    """记下一项（同一项再选就改这一行）。调用方已核过权限与值在账套里。"""
    name = _name(company, kind, subject)
    values = {"value": str(value or "")[:200], "label": str(label or "")[:200], "chosen_by": frappe.session.user}
    if frappe.db.exists(CHOICE, name):
        doc = frappe.get_doc(CHOICE, name)
        doc.update(values)
        doc.save(ignore_permissions=True)
        return
    doc = frappe.get_doc({"doctype": CHOICE, "name": name, "company": company, "kind": kind, "subject": subject,
                          **values})
    doc.flags.name_set = True
    doc.insert(ignore_permissions=True, set_name=name)
    if kind in PER_DOCUMENT:
        _prune_per_document(company, kind)


# 只管一张单的答案（A7 明细对不上，键带票号）：存着供再准备时用；记一条新的时顺手删掉这家公司 90 天前的同类，不越积越多
# （不开定时任务；还没写的旧单到时重问一次）
PER_DOCUMENT, PER_DOCUMENT_DAYS = ("detail",), 90


def _prune_per_document(company, kind) -> None:
    from frappe.utils import add_days, now_datetime

    frappe.db.delete(CHOICE, {"company": company, "kind": kind, "modified": ["<", add_days(now_datetime(), -PER_DOCUMENT_DAYS)]})


def operator_subject(book_key, doc_type) -> str:
    """代录子菜单那一类的对象：「账套 | 单据种类（大写）」（同一家公司几份账套、几种单各记各的）。"""
    return f"{str(book_key or '').strip()}|{str(doc_type or '').strip().upper()}"


def operator_form(company, book_key, doc_type) -> str:
    """这家公司这份账套这种单（``HP`` / ``RR`` …）记住的代录子菜单文字（代录载荷 ``operator.form_choice``）；没记过回空串。"""
    if not company or not str(book_key or "").strip() or not str(doc_type or "").strip():
        return ""
    return frappe.db.get_value(CHOICE, _name(company, OPERATOR_FORM, operator_subject(book_key, doc_type)), "value") or ""
