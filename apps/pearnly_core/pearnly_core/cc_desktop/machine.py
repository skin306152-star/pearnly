"""CC 桌面版共用机器通道的机器方法（多产品共用，ADR-025）：令牌身份（`http_auth`），精确 POST，提交成功才回数据。

  POST /api/method/pearnly_core.cc_desktop.machine.heartbeat          心跳：报账套名单与版本（同 Cowork heartbeat.report 的请求体）
  POST /api/method/pearnly_core.cc_desktop.machine.lease              领一件写账（同 Cowork queue_api.lease）
  POST /api/method/pearnly_core.cc_desktop.machine.acknowledge        回报写账结果（同 Cowork queue_api.acknowledge）
  POST /api/method/pearnly_core.cc_desktop.machine.next_request       领一件按需请求（同 Cowork request_api.next_request）
  POST /api/method/pearnly_core.cc_desktop.machine.complete_request   交按需请求的结果
  POST /api/method/pearnly_core.cc_desktop.machine.leave              电脑自己断开（作废这把令牌）

请求体与回复的形状与 Cowork 那几条逐字段相同，CC 桌面版只按成员的通道换方法路径。本地事务撞上 1020 / 1213 整笔回滚、退避后
重来（共用的 `transaction.conflicts`），用尽回「忙」（503），CC 桌面版下一轮再来。
"""
import frappe

from pearnly_core.cc_desktop import books, devices, http_auth, jobs, release, requests
from pearnly_core.cc_desktop.wire import queue_contracts, request_contracts
from pearnly_core.transaction import conflicts

PROTOCOL = 1


def _commit(operation):
    try:
        frappe.db.commit()
    except Exception as error:
        http_auth.raise_busy(error, stage=operation + "_commit")


def _heartbeat(context, body):
    context = devices.revalidate(context)
    offline = body.get("offline") is True
    full = body.get("account_sets") is not None and not offline
    if full:
        books.replace(context.device, body["account_sets"], devices.now())
    version = body.get("companion_version")
    payload_version = body.get("max_payload_version")
    due = devices.record_report(context, version=version if isinstance(version, str) else None,
                                payload_version=payload_version if type(payload_version) is int else None,
                                offline=offline, full=full)
    # 给托盘显示「连的是谁」（CC 桌面版 `_sync_connection_identity`）：电脑编号、站点、令牌的签发人
    reply = {"ok": True, "connection": {"endpoint_id": context.device, "endpoint_name": frappe.local.site,
                                        "pearnly_account": context.issuer}}
    if not due:
        # 账套名单没变时可以只报「没变」；刚发的令牌要先收到一次整份名单
        reply["account_sets_unchanged_ok"] = True
    latest = release.latest()
    if latest:
        reply["companion_latest"] = latest
    return reply


def _run(operation, service):
    context, body = http_auth.current_request(operation)
    try:
        result = conflicts.retried(service, context, body)
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage=operation)
    except frappe.AuthenticationError:
        raise
    except frappe.PermissionError:
        http_auth.deny_permission()
    except queue_contracts.QueueContractError as error:
        http_auth.reject_request(error.code)
    except frappe.ValidationError:
        http_auth.reject_request("cc_desktop.request_invalid")
    except Exception as error:
        http_auth.raise_busy(error, stage=operation)
    _commit(operation)
    http_auth.clear_private_messages()
    return result


def _guarded(operation, service):
    """按需请求与断开：业务上的「不行」回 200 + ``{ok: false, code}``（同 Cowork），身份问题才是 401 / 403。"""
    context, body = http_auth.current_request(operation)
    try:
        result = conflicts.retried(service, context, body)
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage=operation)
    except frappe.AuthenticationError:
        raise
    except request_contracts.ContractError as error:
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": error.code, **error.extra}
    except (frappe.ValidationError, frappe.PermissionError, frappe.DoesNotExistError):
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": "companion.on_demand_invalid"}
    _commit(operation)
    http_auth.clear_private_messages()
    if result.get("ok") is False:
        return result
    return {"ok": True, "protocol": PROTOCOL, **result}


@frappe.whitelist(methods=["POST"])
def heartbeat(**ignored):
    return _run("heartbeat", _heartbeat)


@frappe.whitelist(methods=["POST"])
def lease(**ignored):
    return _run("lease", jobs.lease)


@frappe.whitelist(methods=["POST"])
def acknowledge(**ignored):
    return _run("ack", jobs.acknowledge)


@frappe.whitelist(methods=["POST"])
def next_request(**ignored):
    return _guarded("request_next", requests.lease_next)


@frappe.whitelist(methods=["POST"])
def complete_request(**ignored):
    return _guarded("request_complete", requests.complete)


@frappe.whitelist(methods=["POST"])
def leave(**ignored):
    return _guarded("leave", lambda context, body: devices.leave(devices.revalidate(context)))


__all__ = ["acknowledge", "complete_request", "heartbeat", "lease", "leave", "next_request"]
