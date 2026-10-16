"""公司主档的网页接口（ADR-020；读用 GET，写只收 POST）。逻辑在 `service` / `notes` / `choices` / `pending` / `ledger`。

写入成功后提交（同其它 Cowork 接口），实时事件都在提交后才推。
"""
import frappe

from pearnly_cowork.access import scope
from pearnly_cowork.companies import choices as saved_choices
from pearnly_cowork.companies import ledger, notes, pending as pending_items, service


@frappe.whitelist(methods=["GET"])
def candidates(search=None):
    return service.candidates(search)


@frappe.whitelist(methods=["GET"])
def companies(search=None, start=0, limit=service.PAGE_MAX):
    return service.listing(search, start, limit)


@frappe.whitelist(methods=["POST"])
def create(connection, account_set_key, request_key=None):
    found = service.create(connection, account_set_key, request_key)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["GET"])
def detail(company, period=None):
    found = service.detail(company, period)
    frappe.db.commit()  # 可能顺手补了税号（GET 不自动提交）
    return found


@frappe.whitelist(methods=["GET"])
def month(company, period, limit=None):
    """月结台账（数字会计员第 1 期）：四段与统一异常清单，现算不存（`ledger.month`）。"""
    found = ledger.month(company, period, limit)
    frappe.db.commit()  # 同 `detail`：可能顺手补了税号（GET 不自动提交）
    return found


@frappe.whitelist(methods=["POST"])
def rename(company, title):
    found = service.rename(company, title)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def combined_vat(company, combined=0):
    found = service.set_combined_vat(company, combined)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def delete(company):
    found = service.delete(company)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def note_save(company, text, note=None):
    service.require_write()
    row = service.get(company)
    found = notes.save(row.name, text, note or None, workspace=scope.default_workspace())
    service.announce("upsert", row.name, part="notes")
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def note_delete(company, note):
    service.require_write()
    row = service.get(company)
    found = notes.delete(row.name, note)
    service.announce("upsert", row.name, part="notes")
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def folder_bind(company, binding):
    # CC 桌面版 `pick_folder` 交回的绑定，页面原样（JSON）转来；形状由 `company_folders` 校验
    found = service.folder_bind(company, binding)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def folder_unbind(company, reader):
    found = service.folder_unbind(company, reader)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def choice_forget(company, key):
    service.require_write()
    row = service.get(company)
    found = saved_choices.forget(row, service.books(row, limit=None), key)
    service.announce("upsert", row.name, part="choices")
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def choice_set(company, key, value, period=None):
    service.require_write()
    row = service.get(company)
    found = saved_choices.set_value(row, service.books(row, limit=None), key, value, str(period or ""))
    service.announce("upsert", row.name, part="choices")
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def choice_promote(company, key, branch=None, period=None):
    """「设为全公司」（第 2 期公司规则）：账套行或 Express 历史行提升成这家公司的规则；``branch`` 给了就只管那个分店。"""
    service.require_write()
    row = service.get(company)
    found = saved_choices.promote(row, service.books(row, limit=None), key, str(branch or ""), str(period or ""))
    service.announce("upsert", row.name, part="choices")
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["GET"])
def pending(company=None, limit=None):
    return pending_items.pending(company or None, limit)


@frappe.whitelist(methods=["GET"])
def pending_counts():
    return pending_items.pending_counts()


@frappe.whitelist(methods=["GET"])
def task_overview():
    """定时任务按公司：侧栏每家「定时任务 N」与总览分组（一次取全部，不逐家读详情）。"""
    service.require_read()
    return service.task_overview()
