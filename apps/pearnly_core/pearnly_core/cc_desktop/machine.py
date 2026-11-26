"""CC 桌面版共用机器通道的机器方法（多产品共用，ADR-025）：令牌身份（`http_auth`），精确 POST，提交成功才回数据。

  POST /api/method/pearnly_core.cc_desktop.machine.heartbeat          心跳：报账套名单与版本（同 Cowork heartbeat.report 的请求体）；
                                                                      可带 ``ledger_changes``（盯着的账套变了哪几张表），回复带
                                                                      ``watch_books``（要盯哪几本，:func:`_watch_reply`）
  POST /api/method/pearnly_core.cc_desktop.machine.lease              领一件写账（同 Cowork queue_api.lease）
  POST /api/method/pearnly_core.cc_desktop.machine.acknowledge        回报写账结果（同 Cowork queue_api.acknowledge）
  POST /api/method/pearnly_core.cc_desktop.machine.next_request       领一件按需请求（同 Cowork request_api.next_request）
  POST /api/method/pearnly_core.cc_desktop.machine.complete_request   交按需请求的结果
  POST /api/method/pearnly_core.cc_desktop.machine.leave              电脑自己断开（作废这把令牌）
  POST /api/method/pearnly_core.cc_desktop.machine.mrerp_login        MR.ERP 登录在 CC 里授权：{ticket, username, password}
                                                                      （ADR-026 第 7 条；产品没接回 ``{ok: false, code: "unsupported"}``）
  POST /api/method/pearnly_core.cc_desktop.machine.operator_ai        代录 AI 接手的模型请求：{batch, kind, request}
                                                                      （`express_operator.ai`；回 ``{ok, response}``）

请求体与回复的形状与 Cowork 那几条逐字段相同，CC 桌面版只按成员的通道换方法路径。本地事务撞上 1020 / 1213 整笔回滚、退避后
重来（共用的 `transaction.conflicts`），用尽回「忙」（503），CC 桌面版下一轮再来。
"""
import frappe

from pearnly_core.cc_desktop import books, devices, http_auth, jobs, login_tickets, products, release, requests, rescan
from pearnly_core.cc_desktop.wire import queue_contracts, request_contracts
from pearnly_core.ledger_sync import contracts as ledger_contracts
from pearnly_core.transaction import conflicts

PROTOCOL = 1
CHANGES = "ledger_changes"  # 心跳请求体：``[{key, tables: [表名 | "*"]}]``（CC 桌面版文件变更通知，评估 2026-11-22）
WATCH = "watch_books"       # 心跳回复：``[{key, path, root}]``，这台电脑要盯的账套（本机写法）
WATCH_CACHE = "pcc-watch-books:"
WATCH_TTL = 300             # 要盯哪几本按电脑缓存这么久（秒）：每分钟一拍心跳不每次查公司与账套


def _changes(context, body):
    """心跳带来的账套变化：只认这台电脑报过的账套、认识的表，交给产品排读取（产品不在这里读、不等电脑）。"""
    raw = body.get(CHANGES)
    if not raw:
        return
    changes = ledger_contracts.express_changes(raw, books.readable(context.device).keys())
    if changes:
        frappe.db.savepoint("pcc_ledger_changed")
        try:
            products.ledger_changed(context.device, changes)
        except Exception as error:  # noqa: BLE001 — 排读取出错不挡心跳（10 分钟标记检查照样兜底）
            if conflicts.is_conflict(error):
                raise  # 1020 / 1213 / 关隔离后的保存前比对：整笔重来（`conflicts.retried`），不当成排读取出错吞掉（B ①）
            frappe.db.rollback(save_point="pcc_ledger_changed")
            frappe.log_error(title="CC ledger change not queued", message=frappe.get_traceback()[-3000:])


def _watch_reply(device) -> list:
    """要这台电脑盯着的账套（本机路径）。产品没声明回空；按电脑缓存 :data:`WATCH_TTL` 秒。"""
    cached = frappe.cache.get_value(WATCH_CACHE + device)
    if cached is not None:
        return cached
    try:
        keys = set(products.watch_books(device))
    except Exception:  # noqa: BLE001 — 算不出来这一拍不回，不挡心跳
        frappe.log_error(title="CC watch list failed", message=frappe.get_traceback()[-3000:])
        return []
    rows = books.readable(device) if keys else {}
    found = [{"key": key, "path": row.path, "root": row.root or ""} for key, row in sorted(rows.items()) if key in keys]
    frappe.cache.set_value(WATCH_CACHE + device, found, expires_in_sec=WATCH_TTL)
    return found


def forget_watch(device=None) -> None:
    """要盯的账套变了（公司接上 / 断开、换了现用账套）：下一拍心跳重新算。不给电脑就全部重算。"""
    if device:
        frappe.cache.delete_value(WATCH_CACHE + device)
    else:
        frappe.cache.delete_keys(WATCH_CACHE)


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
        rescan.acknowledge(context.device, body)  # 网页要的整份重扫已随这份名单报上来
        forget_watch(context.device)
    if not offline:
        _changes(context, body)
    version = body.get("companion_version")
    payload_version = body.get("max_payload_version")
    due = devices.record_report(context, version=version if isinstance(version, str) else None,
                                payload_version=payload_version if type(payload_version) is int else None,
                                offline=offline, full=full, runner=body.get("reindex_runner"),
                                targets=body.get("operator_targets"), logins=body.get("mrerp_logins"),
                                users=body.get("mrerp_users"), link=body.get("link"))
    # 给托盘显示「连的是谁」（CC 桌面版 `_sync_connection_identity`）：电脑编号、站点、令牌的签发人
    reply = {"ok": True, "connection": {"endpoint_id": context.device, "endpoint_name": frappe.local.site,
                                        "pearnly_account": context.issuer}}
    if not due:
        # 账套名单没变时可以只报「没变」；刚发的令牌要先收到一次整份名单
        reply["account_sets_unchanged_ok"] = True
    latest = release.latest()
    if latest:
        reply["companion_latest"] = latest
    watch = _watch_reply(context.device)
    if watch:
        reply[WATCH] = watch
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


def _mrerp_login(context, found, username, password):
    """MR.ERP 登录在 CC 桌面版里授权（ADR-026 第 7 条）：票已取出作废（:func:`_login`），这里核发票时那台电脑就是令牌认出的
    这台、产品按发票的人**此刻**的权限再核一次，写进连接的 Password 字段，并把这条连接记进这台电脑已授权的清单。回给电脑的
    只有连接号、MR.ERP 网址与「已授权：用户名 · 电脑 · 时间」，从不回密码。撞上 1020 / 1213 整段重来（票不再取）。"""
    context = devices.revalidate(context)
    login_tickets.matches(found, site=context.site, channel="core", device=context.device)
    scope = found.get("scope") or "site"
    now = products.mrerp_login_scope(found["user"], found["connection"])
    # 票上是 site 而此刻已不是管理员：拒写（服务端判，不靠 CC 或页面）；此刻已不能用这条连接：都拒
    if not now or (scope == "site" and now != "site"):
        return {"ok": False, "code": "denied"}
    result = products.mrerp_login_store(found["connection"], username, password, user=found["user"],
                                        device_label=devices.label(context.device), scope=scope)
    devices.note_mrerp_login(context, found["connection"])
    return {"connection": found["connection"], "system_url": str(result.get("system_url") or ""),
            "written": bool(result.get("written")) and scope == "site",
            "authorized": {key: result.get(key) for key in ("username", "device", "at")}}


def _login(operation):
    """授权这一条：报文与票的问题回 200 + ``{ok: false, code}``；产品没接回 ``unsupported``。密码读完就从请求副本里拿掉，
    任何出错都不带内部信息与堆栈。票只取一次（取出即作废）：之后的本地事务重来不再取。"""
    context, body = http_auth.current_request(operation)
    http_auth.drop_secret("password")
    try:
        ticket, username, password = login_tickets.login_request(body)
        body = None
        if not products.mrerp_login_supported():
            return {"ok": False, "code": "unsupported"}
        found = login_tickets.redeem(ticket)
        result = conflicts.retried(_mrerp_login, context, found, username, password)
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage=operation)
    except frappe.AuthenticationError:
        raise
    except login_tickets.TicketError as error:
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": error.code}
    except frappe.PermissionError:
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return {"ok": False, "code": "denied"}
    except Exception as error:  # noqa: BLE001 — 只回「忙」，不带内部信息（报文里有密码）
        http_auth.raise_busy(error, stage=operation)
    finally:
        password = body = None
    if result.get("ok") is False:
        frappe.db.rollback()
        http_auth.clear_private_messages()
        return result
    _commit(operation)
    http_auth.clear_private_messages()
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


@frappe.whitelist(methods=["POST"])
def mrerp_login(**ignored):
    return _login("mrerp_login")


__all__ = ["acknowledge", "complete_request", "heartbeat", "lease", "leave", "mrerp_login", "next_request"]


@frappe.whitelist(methods=["POST"])
def operator_ai(**ignored):
    """代录 AI 接手（`express_operator.ai.forward`）：认过电脑密钥后先提交、放掉鉴权时的行锁，再转发模型请求（几十秒，
    不占数据库事务；不走整笔重来，免得同一轮付两次费）。业务上的「不行」在 ``response.error`` 里。"""
    from pearnly_core.express_operator import ai

    _context, body = http_auth.current_request("operator_ai")
    _commit("operator_ai")
    response = ai.forward(body)
    http_auth.clear_private_messages()
    return {"ok": True, "protocol": PROTOCOL, "response": response}
