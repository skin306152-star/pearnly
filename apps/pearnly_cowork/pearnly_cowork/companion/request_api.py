"""CC 桌面端按需请求的两条机器方法（配对码身份，精确 POST，登记在 `http_auth.REQUEST_METHODS`）：

  POST /api/method/pearnly_cowork.companion.request_api.next_request      领一件（没有回 request: null）
  POST /api/method/pearnly_cowork.companion.request_api.complete_request  交结果（分块）或失败原因

与文件夹机器接口同一做法：鉴权后先提交、放掉鉴权时锁住的连接行（不与同一台电脑的心跳 / 领推送任务争锁）；
业务上的「不行」一律回 200 + ``{ok: false, code}``（续传时另带 ``received``），只有身份 / 总闸问题才是 401 / 403。
"""
import frappe

from pearnly_cowork.companion import http_auth, request_service
from pearnly_core.cc_desktop.wire import request_contracts as contracts
from pearnly_cowork.transaction import conflicts

PROTOCOL = 1


def _guarded(operation, handler):
    context, body = http_auth.current_request(operation)
    frappe.local.cowork_private_response = True
    frappe.db.commit()
    try:
        # 领一件 / 交一块是一整笔本地事务（结果分块按 offset 覆盖写，重来写同一块）：撞上 1020 / 1213（两台同时领同一件）
        # 整笔回滚、退避后重来（`transaction.conflicts`），用尽同别的机器方法回「忙」，CC 桌面版下一轮再来。
        result = conflicts.retried(handler, context, body)
        frappe.db.commit()
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage=operation)
    except contracts.ContractError as error:
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": error.code, **error.extra}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": "companion.on_demand_invalid"}
    http_auth.clear_private_messages()
    if result.get("ok") is False:
        return result
    return {"ok": True, "protocol": PROTOCOL, **result}


@frappe.whitelist(methods=["POST"])
def next_request(**ignored):
    return _guarded("request_next", request_service.lease_next)


@frappe.whitelist(methods=["POST"])
def complete_request(**ignored):
    return _guarded("request_complete", request_service.complete)


__all__ = ["complete_request", "next_request"]
