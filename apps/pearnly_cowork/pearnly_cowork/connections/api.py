"""Dedicated RPCs; there is no browser snapshot-install or credential-read API."""
import frappe
from frappe import _
from . import model, mrerp_collector, service


def _call(method, *args, **kwargs):
    # This RPC owns only its local transaction. Internal callers use service
    # directly; rolling back their larger transaction here would be incorrect.
    for attempt in range(3):
        try:
            return method(*args, **kwargs)
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise
        except model.ConnectionError as error:
            frappe.throw(_(error.code))


@frappe.whitelist(methods=["POST"])
def configure(workspace=None, adapter=None, request_key=None, label=None, config=None, client=None,
              users=None, name=None, expected_revision=None, enabled=True, credentials=None):
    # 连接也要落到一份归属上（推送目标按它解析），但界面上不让用户选：
    # 不给就挂站点内部那一份归属（见 service.configure）。
    # 参数顺序与原来一致（有调用方按位置传），只是 workspace 变成可选。
    return _call(service.configure, workspace, adapter, request_key, label, config, client,
                 users, name, expected_revision, enabled, credentials)


@frappe.whitelist()
def detail(name):
    return _call(service.detail, name)


@frappe.whitelist()
def targets(name):
    return _call(service.targets, name)


@frappe.whitelist()
def records(workspace=None, client=None, start=0, limit=50):
    return _call(service.records, workspace, client, start, limit)


@frappe.whitelist(methods=["POST"])
def request_refresh(name, request_key, account_set_key=None):
    result = _call(service.request_refresh, name, request_key, account_set_key)
    # MR.ERP 没有小助手来领：申请落库后由服务端采集任务去读（提交后才入队）。
    mrerp_collector.dispatch(result)
    return result


@frappe.whitelist()
def refresh_status(name, request_id):
    return _call(service.refresh_status, name, request_id)


@frappe.whitelist()
def stock_acc_group(name, account_set_key):
    return _call(service.stock_acc_group, name, account_set_key)


@frappe.whitelist(methods=["POST"])
def choose_stock_acc_group(name, account_set_key, acccod):
    return _call(service.choose_stock_acc_group, name, account_set_key, acccod)
