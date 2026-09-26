"""启动数据：个人信息页要不要出现「LINE」那一块（与绑定接口同一判据）。"""
import frappe


def boot_session(bootinfo):
    if frappe.conf.get("pearnly_product") != "cowork":
        return
    try:
        from pearnly_cowork.line import binding

        bootinfo.pearnly_line = binding.enabled()
    except Exception:
        # boot 失败会让整个 Desk 打不开，这里绝不往外抛。
        frappe.log_error(title="pearnly_cowork.line.boot failed")
