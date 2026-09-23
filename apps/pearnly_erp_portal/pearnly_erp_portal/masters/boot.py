"""公司域给前端的启动数据：多公司那两项（切换公司要用）。

官方给 App 的扩展点就是 `boot_session`（框架启动时按已装 App 逐个调），所以公司清单
从 ERP 自己这里给 —— 不塞进共用层：DMS / Cowork 站点没装这个 App，共用层也不能依赖它。
"""
import frappe


def boot_session(bootinfo):
    if not frappe.conf.get("pearnly_product"):
        return
    try:
        from pearnly_erp_portal.masters import company as companies

        bootinfo.pearnly_companies = companies.company_options()
        bootinfo.pearnly_company = companies.active_company()
    except Exception:
        # boot 失败会让整个 desk 打不开，这里绝不往外抛。
        frappe.log_error(title="pearnly_erp_portal.masters.boot 失败")
