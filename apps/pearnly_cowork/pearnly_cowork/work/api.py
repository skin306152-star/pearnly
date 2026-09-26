"""工作协调看板的专用 RPC：读用 GET，写用 POST；判据与写入都在 `service`。"""
import json

import frappe
from frappe import _

from pearnly_cowork.work import service
from pearnly_cowork.work.contracts import Invalid


def _run(function, *args):
    # 输入错误按术语键翻成当前语言报给页面；并发死锁在本事务内有限重试（同盘点接口）。
    for attempt in range(3):
        try:
            return function(*args)
        except Invalid as error:
            # 抛出后框架会回滚本次请求。
            frappe.throw(_(error.args[0]))
        except frappe.QueryDeadlockError:
            frappe.db.rollback()
            if attempt == 2:
                raise


def _json(value, default):
    if value in (None, ""):
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            frappe.throw(_("cowork.work.error.request"))
    return value


@frappe.whitelist(methods=["GET"])
def board():
    return _run(service.board)


@frappe.whitelist(methods=["GET"])
def card(name):
    return _run(service.card, name)


@frappe.whitelist(methods=["GET"])
def archived():
    return _run(service.archived)


@frappe.whitelist(methods=["POST"])
def create(title, status="todo"):
    return _run(service.create, title, status)


@frappe.whitelist(methods=["POST"])
def update(name, modified, values):
    return _run(service.update, name, modified, _json(values, {}))


@frappe.whitelist(methods=["POST"])
def move(name, status, before=None, after=None):
    return _run(service.move, name, status, before, after)


@frappe.whitelist(methods=["POST"])
def archive(name):
    return _run(service.archive, name)


@frappe.whitelist(methods=["POST"])
def restore(name):
    return _run(service.restore, name)


@frappe.whitelist(methods=["POST"])
def delete(name):
    return _run(service.delete, name)


@frappe.whitelist(methods=["POST"])
def comment(name, content):
    return _run(service.comment, name, content)


@frappe.whitelist(methods=["POST"])
def delete_comment(activity):
    return _run(service.delete_comment, activity)


@frappe.whitelist(methods=["POST"])
def attach(name):
    files = frappe.request.files if frappe.request else None
    return _run(service.attach, name, files.get("file") if files else None)


@frappe.whitelist(methods=["POST"])
def detach(name, file):
    return _run(service.detach, name, file)


@frappe.whitelist(methods=["POST"])
def save_labels(rows):
    return _run(service.save_labels, _json(rows, []))
