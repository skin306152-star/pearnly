"""ERP 版 CC 的公司接口（ADR-020，2026-11-02）：共用层 `projects.listing` 把这个前缀（`cc_projects.API`）交给 CC 对话页，页面的
公司列表、公司页、知识库、待处理都调这里。方法名与形状同 Cowork（`pearnly_cowork.companies.api`），只有 Cowork 前端已有的
那几个，字段是子集；选择公司弹窗同 Cowork（`choices` 列已建的与账套目录里还没建的，`create` 选中即建），改名、删、停用在 ERP 公司页。
读用 GET、写只收 POST；逻辑与权限在 `cc_service`。写入成功后提交，实时事件在提交后才推。
"""
import frappe

from pearnly_erp_portal.masters import cc_service as service


@frappe.whitelist(methods=["GET"])
def companies(search=None, start=0, limit=service.PAGE_MAX):
    return service.listing(search, start, limit)


@frappe.whitelist(methods=["GET"])
def choices(search=None, adapter=None, start=0, limit=service.PAGE_MAX):
    """对话里选公司的弹窗：本人可见的公司与目录里还没建的合成一张表（`service.choices`）。"""
    return service.choices(search, adapter, start, limit)


@frappe.whitelist(methods=["GET"])
def detail(company, period=None):
    return service.detail(company, period)


@frappe.whitelist(methods=["POST"])
def create(connection=None, account_set_key=None, request_key=None):
    return service.create(connection, account_set_key, request_key)  # 建好在锁里已提交


@frappe.whitelist(methods=["POST"])
def note_save(company, text, note=None):
    found = service.note_save(company, text, note)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def note_delete(company, note):
    found = service.note_delete(company, note)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["GET"])
def computers():
    """设置「连接」里的电脑（只读，欠账 B50）。"""
    return service.computers()


@frappe.whitelist(methods=["POST"])
def remove_computer(device):
    """「电脑」组「···」移除一台不在线的电脑（云端 Express 第二阶段 4.10，同 Cowork 的 remove_device）。"""
    found = service.remove_computer(device)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["POST"])
def choice_forget(company, key):
    """公司页「记住的选择」删一条（欠账 B51，同 Cowork 的 choice_forget）。"""
    found = service.choice_forget(company, key)
    frappe.db.commit()
    return found


@frappe.whitelist(methods=["GET"])
def pending(company=None, limit=None, search=None):
    from pearnly_erp_portal.express import pending as pending_items

    service.require_read()
    return pending_items.pending(company or None, limit, search)


@frappe.whitelist(methods=["GET"])
def pending_counts():
    from pearnly_erp_portal.express import pending as pending_items

    service.require_read()
    return pending_items.pending_counts()
