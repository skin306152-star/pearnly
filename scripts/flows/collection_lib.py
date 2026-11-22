"""收款流程用例共用的几样东西（不是流程，不登记）：收款员账号、这本账套的收款类型与一位客户的未收清发票、
在站点里发模拟 LINE 事件（走真实分发与 ERP 处理器，出站全部截住）。

数据只给新开户那套（`datasets.py` new：公司按正式方式接上 Express、有一本账套）：
- 收款员 `collector@erp.regression`：岗位「收款员」+ LINE，只开这家公司（团队管理同一函数 `team.set_member_roles`）；
- 账套主档里补收款类型（NLTTEST 的几行：现金、两种转账、折扣）、现金科目、两位销售员、RE 单据簿默认部门，原值记下由 cleanup 还原；
- 一位本次专用的客户（Express 客户码 `CF<tag>`，cleanup 删掉）与它的两张未收清发票（快照，同电脑读回落表的函数 `snapshot.replace`）。
"""
from __future__ import annotations

import base64
import json

from erp_lib import company

COLLECTOR = "collector@erp.regression"
# 模拟的 LINE 号：收款员、和一个没有收款员岗位的销售（伪造按钮用）
COLLECTOR_LINE = "U" + "c" * 32
SALES_LINE = "U" + "5" * 32
RECEIPT_TYPES = [
    {"code": "02", "prefix": "CH", "name": "รับเงินสด", "name_en": "", "bank": "", "account": "1111-00", "account_issue": ""},
    {"code": "05", "prefix": "BB", "name": "เงินโอนเข้าบัญชี BBL/ออมทรัพย์", "name_en": "", "bank": "", "account": "1113-01",
     "account_issue": ""},
    {"code": "12", "prefix": "TB", "name": "เงินโอนเข้าบัญชี BBL/กระแส", "name_en": "", "bank": "B1", "account": "1112-01",
     "account_issue": ""},
    {"code": "09", "prefix": "DC", "name": "ส่วนลดเงินสด", "name_en": "", "bank": "", "account": "4100-04", "account_issue": ""},
]
# 1×1 的 PNG：当转账凭证
PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="


def seed(lab, manifest, tag) -> dict:
    """收款员、收款类型、客户与两张发票。回 ``{company, book, customer, invoices, facts_before}``。"""
    co = company(manifest)
    return lab.python(manifest["site"], f"""
from pearnly_erp_portal.collection import service, snapshot
from pearnly_erp_portal.express import facts, target
from pearnly_erp_portal.access import rules
from pearnly_erp_portal.team import service as team
frappe.set_user("admin@erp.regression")
co = {co!r}
if not frappe.db.exists("User", {COLLECTOR!r}):
    frappe.get_doc({{"doctype": "User", "email": {COLLECTOR!r}, "first_name": "collector", "send_welcome_email": 0, "enabled": 1,
                    "language": "zh", "time_zone": "Asia/Bangkok", "user_type": "System User",
                    "roles": [{{"role": "Desk User"}}]}}).insert(ignore_permissions=True)
team.set_member_roles({COLLECTOR!r}, roles=[rules.COLLECTOR_ROLE, rules.LINE_ROLE], companies=[co])
row = frappe.db.get_value("ERP Company", co, ["name", "company_name", "tax_id", "branch"], as_dict=True)
book = service.book_for(row, frappe.utils.nowdate())
before = facts.load(book["key"])
data = dict(before or {{}})
data.update(receipt_types={json.dumps(RECEIPT_TYPES, ensure_ascii=False)}, cash_account="1111-00",
            salesmen=[{{"code": "S1", "name": "flow A"}}, {{"code": "S2", "name": "flow B"}}])
data.setdefault("receipt_run", {{"prefix": "RE", "next": "2400001", "department": "HQ"}})
known = {{str(row.get("code")) for row in data.get("departments") or [] if isinstance(row, dict)}}
data["departments"] = list(data.get("departments") or []) + [row for row in ({{"code": "HQ", "name": "HQ"}}, {{"code": "CPI", "name": "CPI"}})
                                                            if row["code"] not in known]
facts.save(book["key"], data)
code = "CF{tag}".upper()
if not frappe.db.exists("ERP Customer", {{"express_company": co, "express_code": code}}):
    frappe.get_doc({{"doctype": "ERP Customer", "customer_name": "flow collect {tag}", "express_company": co, "express_code": code,
                    "is_active": 1}}).insert(ignore_permissions=True)
invoices = [{{"docnum": "IVA{tag}".upper(), "rectyp": "3", "docdat": "2026-10-01", "customer": code, "salesman": "S1", "department": "",
              "net": "1000.00", "received": "0.00", "remaining": "1000.00"}},
            {{"docnum": "IVB{tag}".upper(), "rectyp": "3", "docdat": "2026-10-05", "customer": code, "salesman": "", "department": "",
              "net": "800.00", "received": "300.00", "remaining": "500.00"}}]
snapshot.replace(co, book["key"], invoices, frappe.utils.now_datetime().isoformat(), customer=code)
frappe.db.commit()
print(json.dumps({{"company": co, "book": book["key"], "customer": code, "invoices": [row["docnum"] for row in invoices],
                  "department": data["receipt_run"].get("department") or "",
                  "facts_before": before}}, default=str, ensure_ascii=False))
""")


def restore(lab, manifest, params):
    """还原账套主档（收款类型；原来没有就删掉，回到没读过）、删掉本次的客户与它的发票快照；本次录的收款没写完的撤回（撤掉写账作业，不留在队列里）。"""
    lab.python(manifest["site"], f"""
from pearnly_erp_portal.collection import contracts, service
from pearnly_erp_portal.express import facts
import os
before = json.loads({json.dumps(params.get("facts_before"), ensure_ascii=False)!r})
if before is not None:
    facts.save({params["book"]!r}, before)
else:
    # 账套原来没读回过资料：还原成「没有」（11-15：留下只有收款类型的一份，后面新开户那套的 cc.operator 开始卡就没有目标）
    try:
        os.remove(facts._path({params["book"]!r}))
    except FileNotFoundError:
        pass
# 本次客户的未收清发票快照（客户删了它们也不会自己走）
frappe.db.delete("ERP Receivable Snapshot", {{"book_key": {params["book"]!r}, "customer": {params["customer"]!r}}})
# 本次的客户删掉（不留给别的流程：客户页的列表与计数按当前公司算，留下的 Express 客户会让 erp.page-lists 对不上）
for customer in frappe.get_all("ERP Customer", filters={{"express_company": {params["company"]!r}, "express_code": {params["customer"]!r}}},
                               pluck="name"):
    frappe.delete_doc("ERP Customer", customer, ignore_permissions=True, force=True)
from pearnly_core import line_session
line_session.clear_session({COLLECTOR_LINE!r})
frappe.set_user({COLLECTOR!r})
for name in frappe.get_all("ERP Collection", filters={{"customer": {params["customer"]!r},
                                                       "status": ["in", [contracts.DRAFT, contracts.QUEUED, contracts.FAILED]]}},
                           pluck="name"):
    try:
        service.withdraw(name)
    except Exception:
        pass
frappe.db.commit()
print(json.dumps({{"ok": True}}))
""")


def line_events(lab, site, events, extra="") -> dict:
    """在站点里依次发模拟 LINE 事件（真实的 `line._dispatch_with_feedback` → ERP 处理器）：``events`` 每项
    ``["postback", data, line_id?]`` / ``["text", 文字, line_id?]`` / ``["image", 消息号, line_id?]``。出站请求只记下来；
    后台任务（存凭证、等写入结果）在同一进程里当场跑，等写入不等（``WAIT_SECONDS = 0``）。
    回每个事件之后：会话停在哪一步、回了哪些消息。``extra``：发事件前在站点里先跑的几行。"""
    return lab.python(site, f"""
import base64, time, types
from pearnly_core import line, line_binding
from pearnly_erp_portal.integrations import line_collection
LINES = {{{COLLECTOR_LINE!r}: {COLLECTOR!r}, {SALES_LINE!r}: "sales@erp.regression"}}
calls = []
class Response:
    status_code, text, content = 200, "{{}}", b"{{}}"
    headers = {{}}
    def __init__(self, url):
        self.url = url
    def json(self):
        return {{"displayName": "flow", "language": "zh"}} if "profile" in self.url else {{}}
def record(method):
    def send(url, *args, **kwargs):
        calls.append({{"url": url, "json": kwargs.get("json")}})
        return Response(url)
    return send
line.requests = types.SimpleNamespace(post=record("POST"), get=record("GET"), request=lambda m, url, *a, **k: record(m)(url, *a, **k),
                                      RequestException=Exception, exceptions=line.requests.exceptions)
line._headers = lambda: {{"Authorization": "Bearer flow"}}
line.can_send = lambda: True
line.user_language = lambda line_user_id, default=None: "zh"
line.download_message_content = lambda message_id, **kwargs: base64.b64decode({PIXEL!r})
real_binding_of = line_binding.binding_of
line_binding.user_of = lambda line_user_id: LINES.get(line_user_id) or (real_binding_of(line_user_id) or frappe._dict()).get("user")
line_binding.binding_of = lambda line_user_id: (frappe._dict(user=LINES[line_user_id], line_user_id=line_user_id, status="active")
                                                if line_user_id in LINES else real_binding_of(line_user_id))
from pearnly_core.line_ui import receipt as line_receipt
# 等结果的计时器当场到点（实验栈没有电脑写账，回执不会来）：放行后的兜底「录入中」+【查看结果】【撤回】在这次事件里就发出
line_receipt.Timer = lambda seconds, function: types.SimpleNamespace(daemon=True, start=function)
def inline(path, **kwargs):
    for key in ("queue", "enqueue_after_commit", "job_id", "deduplicate", "timeout", "now"):
        kwargs.pop(key, None)
    return frappe.get_attr(path)(**kwargs)
frappe.enqueue = inline
{extra}
out = []
for index, item in enumerate({json.dumps(events, ensure_ascii=False)}):
    kind, value = item[0], item[1]
    uid = item[2] if len(item) > 2 else {COLLECTOR_LINE!r}
    event = {{"type": "postback" if kind == "postback" else "message", "webhookEventId": "flowevt%d%d" % (int(time.time()), index),
              "replyToken": "flow-token-%d" % index, "source": {{"type": "user", "userId": uid}}, "timestamp": int(time.time() * 1000)}}
    if kind == "postback":
        event["postback"] = {{"data": value}}
    elif kind == "text":
        event["message"] = {{"type": "text", "id": "m%d" % index, "text": value}}
    else:
        event["message"] = {{"type": "image", "id": value}}
    before = len(calls)
    error = ""
    try:
        handled = line._dispatch_with_feedback(event)
        frappe.db.commit()
    except Exception as exc:
        handled, error = False, (type(exc).__name__ + ": " + str(exc))[:300]
        frappe.db.rollback()
    sent = [message for call in calls[before:] if "/message/" in call["url"] for message in (call["json"] or {{}}).get("messages", [])]
    out.append({{"event": item, "handled": bool(handled), "error": error, "step": line_collection.payload_of(uid).get("step") or "",
                "session": line_collection.payload_of(uid), "messages": sent}})
print(json.dumps({{"events": out}}, ensure_ascii=False, default=str))
""")


def postbacks(messages) -> list:
    """一组消息里全部 postback 按钮的数据（快捷回复与 Flex 都算）。"""
    found = []

    def walk(node):
        if isinstance(node, dict):
            action = node.get("action")
            if isinstance(action, dict) and action.get("type") == "postback":
                found.append(action["data"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(messages)
    return found


def png() -> bytes:
    return base64.b64decode(PIXEL)
