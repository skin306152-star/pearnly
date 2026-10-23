"""CC 桌面版共用机器通道给对话页的入口（多产品共用，ADR-025）：人在 CC 窗口里登录了站点，对话页带本机设备密钥登记这台电脑。

  POST /api/method/pearnly_core.cc_desktop.api.register   {request_key, device_key, device_name, rekey?, rejoin?}

Frappe 会话 + CSRF（对话页本来就带）；令牌只在 ``created`` 时回一次、只交给本机（对话页转交 CC 桌面版配对，配对码
``pcc_…@<站点>``）。没声明接共用通道的站点（Cowork、DMS）一律拒绝（Cowork 站点仍用 `pearnly_cowork.companion.api.register`）。
"""
import frappe

from pearnly_core.cc_desktop import devices
from pearnly_core.transaction import conflicts


@frappe.whitelist(methods=["POST"])
def register(request_key=None, device_key=None, device_name=None, rekey=False, rejoin=False):
    try:
        result = conflicts.retried(devices.register, request_key, device_key, device_name, rekey, rejoin)
    except (conflicts.Exhausted, conflicts.RollbackFailed):
        frappe.throw(frappe._("cc_desktop.busy"))
    frappe.db.commit()
    return result


__all__ = ["register"]
