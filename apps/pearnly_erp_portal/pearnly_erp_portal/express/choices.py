"""ERP 单据写进 Express 时要人拿主意的项，按公司选一次、记住复用（用户 10-23：要人拿主意的单在 ERP 里处理）。

哪几类、怎么判要不要问，是共用的写账规则（`pearnly_core.express_rules.push.choices.resolve`，与 Cowork 同一套）；这里只存选过的值：
``ERP Express Choice`` 按「公司 + 哪一类 + 对象」一行（对象就是写账规则给的 ``subject``）。另加两类 ERP 自己的：

- ``account_set``：这家公司同期有两份账套时用哪一份（按公司 + 会计年度记，用户 10-17「两份同期账套按公司按年度问一次」）；
- ``stock_group``：账套一个库存品都没有、要建第一个时挂哪个存货科目组（写账规则回 ``stock_acc_group_required``）。

选过的值在账套里已经没有了（被删 / 改了），写账规则当没选过、重新问，不静默换别的。
"""
import hashlib

import frappe

CHOICE = "ERP Express Choice"
ACCOUNT_SET = "account_set"
STOCK_GROUP = "stock_group"


def _name(company, kind, subject):
    return "EXC-" + hashlib.sha256("\0".join([company, kind, subject]).encode("utf-8")).hexdigest()[:24]


def remembered(company) -> dict:
    """``{kind: {subject: value}}``：这家公司选过的。"""
    found = {}
    for row in frappe.get_all(CHOICE, filters={"company": company}, fields=["kind", "subject", "value"],
                              limit_page_length=0):
        found.setdefault(row.kind, {})[row.subject or ""] = row.value or ""
    return found


def chosen(company, kind, subject) -> str:
    return frappe.db.get_value(CHOICE, _name(company, kind, subject), "value") or ""


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
