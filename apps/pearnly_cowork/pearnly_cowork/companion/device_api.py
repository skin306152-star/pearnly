"""CC 桌面版认电脑的两条机器方法（配对码身份，精确 POST，登记在 `http_auth.DEVICE_METHODS`，ADR-021）：

  POST /api/method/pearnly_cowork.companion.device_api.hello   这把密钥挂到这台电脑上（不改公式；这台在这条连接上
       已有自己的密钥时回 superseded 并吊销这把多余的旧槽位密钥）
  POST /api/method/pearnly_cowork.companion.device_api.leave   吊销这把密钥本身（电脑上「断开连接」）

MR.ERP 的只读文件夹密钥也能调（同一台电脑的 Express 成员和 MR.ERP 成员合成一台，只有 MR.ERP 的电脑也能断开）。
报文不对回 200 + ``{ok: false, code}``；身份 / 总闸问题才是 401 / 403。旧服务端没有这两条，桌面端把 401 / 403 / 404
都当旧服务端处理（第 5 批）。
"""
from uuid import uuid4

import frappe

from . import credentials, device_contracts, http_auth
from .contracts import CredentialError


def _hello(context, body):
    return credentials.hello(context, device_contracts.hello_request(body))


def _leave(context, body):
    credentials.leave(context)
    return {'status': device_contracts.LEFT}


def _guarded(operation, handler):
    context, body = http_auth.current_request(operation)
    frappe.local.cowork_private_response = True
    for attempt in range(3):
        point = 'cowork_device_api_' + uuid4().hex
        frappe.db.savepoint(point)
        try:
            result = handler(context, body)
            break
        except frappe.QueryDeadlockError as error:
            # Nothing has left this server yet; the whole local unit may run again.
            frappe.db.rollback()
            http_auth.clear_private_messages()
            if attempt == 2:
                http_auth.raise_busy(error, stage=operation)
        except CredentialError as error:
            frappe.db.rollback(save_point=point)
            http_auth.clear_private_messages()
            return {'ok': False, 'code': error.code}
    http_auth.clear_private_messages()
    return {'ok': True, 'protocol': device_contracts.PROTOCOL, **result}


@frappe.whitelist(methods=['POST'])
def hello(**ignored):
    return _guarded('device_hello', _hello)


@frappe.whitelist(methods=['POST'])
def leave(**ignored):
    return _guarded('device_leave', _leave)


__all__ = ['hello', 'leave']
