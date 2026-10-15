"""智能体对话页的接口（站点会话登录；读用 GET，写只收 POST）。逻辑都在 `sessions`，对话归在哪家在 `projects`。"""
import html
import json
import pathlib

import frappe
from frappe import _

from pearnly_core.assistant import files, sessions, tools
from pearnly_core.assistant import projects as filed  # 接口名 `projects` 留给下面的方法


@frappe.whitelist(methods=["GET"])
def listing(archived=0, after=None, check=None, grouped=0, group=None):
    # group：不传是 None；传空串是「最近」这一组
    return sessions.listing(archived=frappe.utils.cint(archived) == 1, after=after or None, check=check or None,
                            grouped=frappe.utils.cint(grouped) == 1, group=group)


@frappe.whitelist(methods=["POST"])
def create(project=None, period=None, scope=None):
    return sessions.create(project=project or None, period=period or None, scope=scope or None)


@frappe.whitelist(methods=["POST"])
def move(session_id, project="", period=None, scope=None):
    return sessions.move(session_id, project or "", period=period or None, scope=scope or None)


# 对话归在哪家（ADR-020，Cowork 按公司主档；产品没声明时 kind 为 None）
@frappe.whitelist(methods=["GET"])
def projects():
    return filed.listing()


@frappe.whitelist(methods=["GET"])
def get(session_id):
    return sessions.get(session_id)


@frappe.whitelist(methods=["POST"])
def rename(session_id, title):
    sessions.rename(session_id, title)


@frappe.whitelist(methods=["POST"])
def star(session_id, starred=0):
    sessions.star(session_id, frappe.utils.cint(starred) == 1)


@frappe.whitelist(methods=["POST"])
def archive(session_id):
    sessions.archive(session_id)


@frappe.whitelist(methods=["POST"])
def delete(session_id):
    sessions.delete(session_id)


@frappe.whitelist(methods=["GET"])
def events(session_id, after=None, status=0):
    return sessions.events(session_id, after=after or None, status=frappe.utils.cint(status) == 1)


@frappe.whitelist(methods=["POST"])
def notices_seen(session_id):
    sessions.notices_seen(session_id)


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


@frappe.whitelist(methods=["POST"])
def upload(session_id):
    return files.upload(session_id)


@frappe.whitelist(methods=["GET"])
def session_files(session_id):
    return files.listing(session_id)


@frappe.whitelist(methods=["GET"])
def download(session_id, file_id, inline=0):
    files.download(session_id, file_id, inline=frappe.utils.cint(inline) == 1)


@frappe.whitelist(methods=["GET"])
def preview(session_id, file_id):
    return files.preview(session_id, file_id)


@frappe.whitelist(methods=["GET"])
def tool_labels():
    return tools.labels()


@frappe.whitelist(methods=["GET"])
def tool_steps():
    return tools.steps()


# 后台脚本（ADR-019：安卓等手机、电脑浏览器能把 CC「安装」成应用）。浏览器只认同一站点里的脚本，网站目录又不发 .js，
# 所以由这里发出；响应头允许它管 /assistant（注册时 scope 也只给 /assistant，不碰后台 /app 等页面）。
# 内容是静态的（不含任何用户数据），不要求登录：登录过期时浏览器照样能更新它。
SERVICE_WORKER = pathlib.Path(__file__).with_name("service_worker.js")
OFFLINE_PAGE = (
    '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
    '<meta name="color-scheme" content="light dark">'
    '<meta name="theme-color" content="#fcfcfb" media="(prefers-color-scheme: light)">'
    '<meta name="theme-color" content="#151515" media="(prefers-color-scheme: dark)">'
    "<title>CC</title><style>body{margin:0;min-height:100vh;display:flex;flex-direction:column;align-items:center;"
    "justify-content:center;gap:16px;background:#fcfcfb;color:#3d3d3a;font:16px system-ui,-apple-system,sans-serif}"
    "button{padding:10px 20px;border-radius:999px;border:1px solid rgba(31,30,29,.16);background:#fff;font:inherit;color:#1a1a1a}"
    "@media (prefers-color-scheme:dark){body{background:#151515;color:#e5e3da}button{background:#30302e;color:#faf9f5;"
    "border-color:rgba(250,249,245,.2)}}</style></head><body><div>{title}</div>"
    '<button onclick="location.reload()">{retry}</button></body></html>'
)


@frappe.whitelist(allow_guest=True, methods=["GET"])
def service_worker():
    from werkzeug.wrappers import Response

    page = OFFLINE_PAGE.replace("{title}", html.escape(_("You're offline"))).replace("{retry}", html.escape(_("Try again")))
    body = SERVICE_WORKER.read_text(encoding="utf-8").replace("__OFFLINE_PAGE__", json.dumps(page))
    return Response(body, mimetype="application/javascript",
                    headers={"Service-Worker-Allowed": "/assistant", "Cache-Control": "no-cache"})


# 手机 / 电脑浏览器的网页推送（送达通道 2，见 `push`）：对话页取公钥、认领或退掉本设备的订阅。
@frappe.whitelist(methods=["GET"])
def push_key():
    from pearnly_core.assistant import push

    return {"key": push.public_key()}


@frappe.whitelist(methods=["POST"])
def push_subscribe(endpoint, p256dh, auth):
    from pearnly_core.assistant import contracts, push

    try:
        return push.subscribe(endpoint, p256dh, auth)
    except contracts.ContractError:
        frappe.throw(_("This device cannot receive notifications."))


@frappe.whitelist(methods=["POST"])
def push_unsubscribe(endpoint):
    from pearnly_core.assistant import push

    push.unsubscribe(endpoint)

