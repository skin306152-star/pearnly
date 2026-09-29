"""对账预设的页面接口：结果页回答问题、列表页查看 / 修改 / 删除预设（口径见 ``reconciliation.presets``）。

范围（哪个账户 / 公司）一律由服务端从任务算，页面只给规则和答案；改 / 删只针对已有的预设。
"""
import frappe
from frappe import _

from pearnly_cowork.access import scope
from pearnly_cowork.recon_center import sheets
from pearnly_cowork.reconciliation import bank_recon_service, preset_store, presets, revision


def _task_scope(kind, doc, subject):
    """问题所在的范围由服务端从任务算：银行按账户；收入按税报上的本公司税号 + 这份总账里真有的科目。"""
    if kind == "bank":
        return bank_recon_service.task_scope(doc, revision.bank_inputs(doc).get("gl") or [])
    if kind == "income":
        accounts = {str(row.get("account_code") or "").strip() for row in (revision.stored_inputs(doc) or {}).get("gl") or []}
        return presets.income_scope(revision.income_tax_id(doc), subject) if subject in accounts else ""
    return ""


@frappe.whitelist(methods=["POST"])
def answer(kind, task_id, rule, value, subject=None, lang=None):
    """回答结果页的一个问题：记成这个账户 / 公司的预设，这份结果按它重算；已完成的任务连同导出一起更新。"""
    doc = sheets._task(kind, task_id, for_update=True)
    found = _task_scope(kind, doc, str(subject or "").strip())
    if not found:
        frappe.throw(_("reconciliation.request_invalid"))
    preset_store.remember(doc.workspace, kind, found, rule, value, task=doc.name)
    if kind == "bank" and doc.status == "completed":
        # 存着的结果行、汇总与导出也按预设更新（需复核的任务等人保存时一起算）。
        revision.bank_save(doc, revision.bank_inputs(doc))
    elif kind == "income":
        revision.income_save(doc, revision.income_inputs(doc))
    frappe.db.commit()
    return sheets.load(kind, task_id, lang)


@frappe.whitelist()
def listing(kind):
    return preset_store.listing(scope.default_workspace(), kind)


@frappe.whitelist(methods=["POST"])
def change(kind, scope_key, rule, value):
    workspace = scope.default_workspace()
    if not any(item["scope"] == scope_key and item["rule"] == rule for item in preset_store.listing(workspace, kind)):
        frappe.throw(_("reconciliation.request_invalid"))
    preset_store.remember(workspace, kind, scope_key, rule, value)
    frappe.db.commit()
    return preset_store.listing(workspace, kind)


@frappe.whitelist(methods=["POST"])
def remove(kind, scope_key, rule):
    workspace = scope.default_workspace()
    preset_store.forget(workspace, kind, scope_key, rule)
    frappe.db.commit()
    return preset_store.listing(workspace, kind)
