"""智能体对话页的接口（站点会话登录；读用 GET，写只收 POST）。逻辑都在 `sessions`。"""
import frappe

from pearnly_core.assistant import sessions


@frappe.whitelist(methods=["GET"])
def listing(archived=0):
    return sessions.listing(archived=frappe.utils.cint(archived) == 1)


@frappe.whitelist(methods=["POST"])
def create():
    return sessions.create()


@frappe.whitelist(methods=["GET"])
def get(session_id):
    return sessions.get(session_id)


@frappe.whitelist(methods=["POST"])
def rename(session_id, title):
    sessions.rename(session_id, title)


@frappe.whitelist(methods=["POST"])
def archive(session_id):
    sessions.archive(session_id)


@frappe.whitelist(methods=["POST"])
def delete(session_id):
    sessions.delete(session_id)


@frappe.whitelist(methods=["GET"])
def events(session_id, after=None):
    return sessions.events(session_id, after=after or None)


@frappe.whitelist(methods=["POST"])
def send(session_id, text):
    sessions.send(session_id, text)


@frappe.whitelist(methods=["POST"])
def confirm(session_id, confirmations):
    return {"confirmed": sessions.confirm(session_id, frappe.parse_json(confirmations))}


@frappe.whitelist(methods=["POST"])
def interrupt(session_id):
    sessions.interrupt(session_id)


@frappe.whitelist(methods=["POST"])
def run_tool(session_id, tool_use_id):
    return sessions.run_tool(session_id, tool_use_id)
