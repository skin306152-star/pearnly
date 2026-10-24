"""LINE 关键流程冒烟（在实验栈站点网页容器里跑）：按产品发几条模拟事件，走真实的分发与产品处理器，出站全部截住。

    env/bin/python /tmp/smoke-line_flow.py <站点> '<JSON 参数>'   # 末行打印 SMOKE {"ok": ..., ...}

- 不写绑定表：进程内把模拟 LINE 号（U + 32 个 0）当成绑定到测试账号（参数 user，默认 Administrator）；
- 不出网：`pearnly_core.line` 用到的 requests 全部换成记录器（回复、推送、三个点都只记下来）；
- 每条事件要求：处理器认了这条事件、至少回了一条、没有异常；三个点若出现，必须在回复之前（不出现在回复之后）。
- 收尾清掉模拟号的会话状态。只给测试租户用。
"""
import json
import sys
import time
import types

import frappe

site = sys.argv[1]
params = json.loads(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else {}
LINE_ID = "U" + "0" * 32
SCENARIOS = {
    "erp": ["เมนู", "ซื้อ", "ยกเลิก", "随便打的字"],
    "cowork": ["เมนู", "随便打的字"],
    "dms": ["เมนู", "随便打的字"],
}

frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False, "events": []}
try:
    product = params.get("product") or frappe.conf.get("pearnly_product") or ""
    user = params.get("user") or "Administrator"
    frappe.set_user(user)
    from pearnly_core import line, line_binding, line_session

    calls = []

    class Response:
        def __init__(self, url):
            self.status_code = 200
            self.text = "{}"
            self.url = url
            self.content = b"{}"

        def json(self):
            return {"displayName": "smoke", "language": "th"} if "profile" in self.url else {}

    def record(method):
        def send(url, *args, **kwargs):
            calls.append({"t": time.monotonic(), "method": method, "url": url, "json": kwargs.get("json")})
            return Response(url)
        return send

    fake = types.SimpleNamespace(post=record("POST"), get=record("GET"), request=lambda m, url, *a, **k: record(m)(url, *a, **k),
                                 RequestException=Exception, exceptions=line.requests.exceptions)
    line.requests = fake
    real_binding_of = line_binding.binding_of
    line_binding.binding_of = lambda line_user_id: (frappe._dict(user=user, line_user_id=line_user_id, status="bound")
                                                    if line_user_id == LINE_ID else real_binding_of(line_user_id))
    line_binding.user_of = lambda line_user_id: user if line_user_id == LINE_ID else real_binding_of(line_user_id)
    for n, text in enumerate(params.get("texts") or SCENARIOS.get(product, ["เมนู"])):
        before = len(calls)
        event = {"type": "message", "mode": "active", "timestamp": int(time.time() * 1000), "webhookEventId": f"smoke{n}",
                 "replyToken": f"smoke-token-{n}", "source": {"type": "user", "userId": LINE_ID},
                 "message": {"type": "text", "id": f"smoke{n}", "text": text}}
        started = time.monotonic()
        error = ""
        try:
            handled = line._dispatch_with_feedback(event)
            frappe.db.commit()
        except Exception as exc:  # noqa: BLE001 —— 冒烟要把异常当结果报出来
            handled, error = False, f"{type(exc).__name__}: {exc}"[:300]
            frappe.db.rollback()
        mine = calls[before:]
        replies = [c for c in mine if c["url"].endswith("/message/reply") or c["url"].endswith("/message/push")]
        loading = [c for c in mine if c["url"].endswith("/chat/loading/start")]
        late_loading = bool(replies and loading and loading[0]["t"] > replies[0]["t"])
        result["events"].append({"text": text, "handled": bool(handled), "replies": len(replies), "loading": len(loading),
                                 "late_loading": late_loading, "seconds": round(time.monotonic() - started, 2), "error": error,
                                 "first": json.dumps((replies[0]["json"] or {}).get("messages", [])[:1], ensure_ascii=False)[:160] if replies else ""})
    result["ok"] = bool(result["events"]) and all(e["handled"] and e["replies"] and not e["error"] and not e["late_loading"]
                                                  for e in result["events"])
finally:
    try:
        from pearnly_core import line_session

        line_session.clear_session(LINE_ID)
        frappe.db.commit()
    except Exception:  # noqa: BLE001
        pass
    frappe.destroy()
print("SMOKE " + json.dumps(result, ensure_ascii=False))
