"""MR.ERP 登录在 CC 桌面版里授权的机器方法（配对码身份，精确 POST，登记在 `http_auth.LOGIN_METHODS`，ADR-026 第 7 条）：

  POST /api/method/pearnly_cowork.companion.credential_api.mrerp_login   {ticket, username, password}

CC 桌面版弹原生小窗收下用户名和密码，带对话页发的一次性票（`api.mrerp_login_ticket`）交来：取出并作废这张票，核同一个
站点、Cowork 通道、发票时那台电脑就是这把密钥挂着的电脑，再按发票的人**此刻**的权限办（`connections.mrerp_login.store`）：
管理员（票上 ``site``）写进 MR.ERP 连接的 Password 字段；使用名单上的成员（票上 ``local``）不写站点、只回网址给 CC 存本机
（``written: false``）；票上是 ``site`` 而此刻已不是管理员一律拒写（``denied``）。都把这条连接记进这台电脑已授权的清单。MR.ERP 的只读文件夹密钥也能调（没装 Express 的
电脑也要能授权）。

回给电脑的只有连接号、MR.ERP 网址与「已授权：用户名 · 电脑 · 时间」，从不回密码；报文、票、权限的问题回 200 +
``{ok: false, code}``，身份问题才是 401 / 403。密码读完就从请求副本里拿掉，出错一律不带内部信息与堆栈。
"""
from uuid import uuid4

import frappe

from pearnly_core.cc_desktop import login_tickets
from pearnly_core.transaction import conflicts
from pearnly_cowork.connections import mrerp_login as logins

from . import credentials, devices, http_auth


def _store(context, found, username, password):
    device = credentials.login_device(context)
    login_tickets.matches(found, site=context.site, channel='cowork', device=device)
    result = logins.store(found['connection'], username, password, actor=found['user'],
                          device_label=frappe.db.get_value(devices.DOCTYPE, device, 'label') or '',
                          scope=found.get('scope') or logins.SITE)
    devices.note_mrerp_login(device, found['connection'])
    return {'connection': found['connection'], 'system_url': result['system_url'], 'written': bool(result['written']),
            'authorized': {key: result[key] for key in ('username', 'device', 'at')}}


def _attempt(context, found, username, password):
    point = 'cowork_login_api_' + uuid4().hex
    frappe.db.savepoint(point)
    try:
        return _store(context, found, username, password)
    except frappe.QueryDeadlockError:  # conflict-ok: 保存点：所有异常都往外抛（死锁不回保存点），外层 retried 接得到
        raise
    except Exception:
        frappe.db.rollback(save_point=point)
        raise


@frappe.whitelist(methods=['POST'])
def mrerp_login(**ignored):
    context, body = http_auth.current_request('mrerp_login')
    http_auth.drop_secret('password')
    frappe.local.cowork_private_response = True
    try:
        ticket, username, password = login_tickets.login_request(body)
        body = None
        # 票只取一次（取出即作废）；之后撞上 1020 / 1213 整段重来不再取（`transaction.conflicts`）
        found = login_tickets.redeem(ticket)
        result = conflicts.retried(_attempt, context, found, username, password)
    except (conflicts.Exhausted, conflicts.RollbackFailed) as error:
        http_auth.raise_busy(error, stage='mrerp_login')
    except frappe.AuthenticationError:
        raise
    except login_tickets.TicketError as error:
        http_auth.clear_private_messages()
        return {'ok': False, 'code': error.code}
    except frappe.PermissionError:
        http_auth.clear_private_messages()
        return {'ok': False, 'code': 'denied'}
    except frappe.ValidationError:
        http_auth.clear_private_messages()
        return {'ok': False, 'code': 'login_invalid'}
    except Exception as error:  # noqa: BLE001 — 只回「忙」，不带内部信息（报文里有密码）
        http_auth.raise_busy(error, stage='mrerp_login')
    finally:
        password = body = None
    http_auth.clear_private_messages()
    return {'ok': True, 'protocol': 1, **result}


__all__ = ['mrerp_login']
