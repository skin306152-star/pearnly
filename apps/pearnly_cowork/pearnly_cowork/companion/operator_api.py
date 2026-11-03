"""代录 AI 接手的机器方法（配对码身份，精确 POST，登记在 `http_auth.OPERATOR_METHODS`，2026-11-03）：
  POST /api/method/pearnly_cowork.companion.operator_api.operator_ai   {batch, kind, request} → {ok, response}

转发与把关在共用层（`pearnly_core.express_operator.ai.forward`，ERP 的共用机器通道同一份）。认过电脑后先提交、放掉鉴权时
锁住的连接行，再转发（几十秒，不占数据库事务；不整笔重来，免得同一轮付两次费）。
"""
import frappe

from pearnly_cowork.companion import http_auth

PROTOCOL = 1


@frappe.whitelist(methods=["POST"])
def operator_ai(**ignored):
    from pearnly_core.express_operator import ai

    _context, body = http_auth.current_request("operator_ai")
    frappe.local.cowork_private_response = True
    frappe.db.commit()
    response = ai.forward(body)
    http_auth.clear_private_messages()
    return {"ok": True, "protocol": PROTOCOL, "response": response}
