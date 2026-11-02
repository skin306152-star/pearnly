"""CC 桌面版共用机器通道给对话页的入口（多产品共用，ADR-025）：人在 CC 窗口里登录了站点，对话页带本机设备密钥登记这台电脑。

  POST /api/method/pearnly_core.cc_desktop.api.register   {request_key, device_key, device_name, rekey?, rejoin?}
  POST /api/method/pearnly_core.cc_desktop.api.mrerp_login_ticket   {connection, device_key}
       MR.ERP 登录在 CC 桌面版里授权的一次性票（ADR-026 第 7 条，`login_tickets`）：产品给出这个人的范围（管理员 site 写站点、
       使用成员 local 只存本机）、对话页交来的本机设备密钥认得出这台电脑，才发票；产品没接回 ``{ok: false, code: "unsupported"}``（ERP 在第 6 步接 MR.ERP 连接之前）。

Frappe 会话 + CSRF（对话页本来就带）；令牌只在 ``created`` 时回一次、只交给本机（对话页转交 CC 桌面版配对，配对码
``pcc_…@<站点>``）。没声明接共用通道的站点（Cowork、DMS）一律拒绝（Cowork 站点仍用 `pearnly_cowork.companion.api.register`）。
"""
import frappe

from pearnly_core.cc_desktop import devices, login_tickets, products
from pearnly_core.transaction import conflicts


@frappe.whitelist(methods=["POST"])
def register(request_key=None, device_key=None, device_name=None, rekey=False, rejoin=False):
    try:
        result = conflicts.retried(devices.register, request_key, device_key, device_name, rekey, rejoin)
    except (conflicts.Exhausted, conflicts.RollbackFailed):
        frappe.throw(frappe._("cc_desktop.busy"))
    frappe.db.commit()
    return result


@frappe.whitelist(methods=["POST"])
def mrerp_login_ticket(connection=None, device_key=None):
    if not products.mrerp_login_supported():
        return {"ok": False, "code": "unsupported"}
    user = frappe.session.user
    if not isinstance(connection, str) or not connection.strip() or len(connection) > 140:
        return {"ok": False, "code": "login_invalid"}
    try:
        device = devices.by_device_key(device_key)
    except ValueError:  # 设备密钥形状不对（`contracts.ChannelError`）
        return {"ok": False, "code": "login_invalid"}
    scope = products.mrerp_login_scope(user, connection.strip())
    if not scope:
        frappe.throw(frappe._("Not Permitted"), frappe.PermissionError)
    if not device:
        return {"ok": False, "code": "device_unknown"}
    ticket = login_tickets.issue(site=frappe.local.site, channel="core", connection=connection.strip(), user=user,
                                 device=device, scope=scope)
    return {"ok": True, "ticket": ticket, "expires_in": login_tickets.TTL_SECONDS, "scope": scope}


__all__ = ["mrerp_login_ticket", "register"]
