"""出错时不把 Python 堆栈回给页面（用户 2026-10-18：系统设置 allow_error_traceback「关掉」）。

框架原生的系统设置 `allow_error_traceback`（「显示完整错误」，默认开）开着时，系统用户的请求出错，回复的 `exc` 里带整段堆栈。
对话页的接口在 before_request 里已按请求关掉（`pearnly_core.assistant.api.no_traceback`），但进 before_request 之前就抛的错
（例如 CSRF 校验失败）、Desk 与其它接口仍照这项设置。产品站点一律关掉：堆栈照旧记进错误日志，页面只显示原文提示。
每次 migrate 都核一次（新开的站点不跑旧补丁）；管理员临时打开排查，下次 migrate 会再关上。控制面不动。"""
import frappe


def ensure_traceback_hidden():
    if frappe.conf.get("pearnly_product") not in {"dms", "cowork", "erp"}:
        return
    if frappe.db.get_single_value("System Settings", "allow_error_traceback"):
        frappe.db.set_single_value("System Settings", "allow_error_traceback", 0)
