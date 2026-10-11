"""引擎回调的转发表（ADR-019，只在计费站点 `billing.internal` 运行）：远端会话 → 租户站点。

生产只在引擎控制台登记一个回调地址（入口 fatcc.com），不必每开一家就去控制台加一次：租户站点建对话、接续新的一段时
经计费通道登记「这段会话是本站的」（`record`），入口收到回调后按会话编号查到站点（`site_for`），原样转过去，由那个站点
验签、执行。只登记已在「租户站点」登记过、且属于该租户的站点，一个租户不能把会话指到别人的站点。
"""
import frappe

from pearnly_control.assistant_entry import contracts

ROUTE = "Pearnly Assistant Route"
SITE = "Pearnly Tenant Site"


def record(tenant: str, site: str, session_id: str) -> None:
    site, session_id = contracts.site(site), contracts.session(session_id)
    if frappe.db.get_value(SITE, site, "tenant") != tenant:
        raise contracts.ContractError("site not registered for this tenant")
    if frappe.db.exists(ROUTE, session_id):
        frappe.db.set_value(ROUTE, session_id, {"site": site, "tenant": tenant})
        return
    frappe.get_doc({"doctype": ROUTE, "session_id": session_id, "site": site, "tenant": tenant}).insert(
        ignore_permissions=True)


def site_for(session_id: str) -> str:
    return frappe.db.get_value(ROUTE, contracts.session(session_id), "site") or ""
