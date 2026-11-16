"""验收场景的站点侧脚本（在测试租户的 ERP 网页容器里跑）：收款的不正常使用（第 6 批）。末行打印 ACC {json}。

    env/bin/python /tmp/acc-erp_collection_misuse.py <站点> '<JSON 参数>'

参数：user（有收款权限的员工）、company_tax / branch、ledger（验收账套路径，只认它）、mode：

- ``submit``：对 invoice 这张发票按 entries（``[{entry: web | line | cc, cash}]``）各录一笔并放行（网页 `service.submit_web`；
  LINE / CC `service.create_draft` + `service.release`，同 `erp_collection_post.py`），然后**只看不等**：watch 秒内每 3 秒读一次
  每笔的状态、写账件、`service.express_waiting`（收款员看到的「Express 开着」那一句的判定）。交回记录号与看到的样子。
  场景在账套被占着（M08）或电脑停着（M07）时调它，之后放开现场再调 ``settle``。
- ``settle``：等 names 里每一笔有结果（写成 / 失败 / 撤回），交回与 `erp_collection_post.py` 同形状的记录，另带这一笔名下的写账件数。
- ``line_redelivered``（M02）：员工绑一个验收专用的假 LINE 号（员工已绑了别的号就拒绝，不顶掉）→ 建 LINE 草稿、出确认卡 →
  同一个【确认】按 LINE webhook 的口径投三次：首投、同一事件号重投（LINE 补发）、新事件号同一按钮（连点）→ 等写成。
- ``line_unbound``（M16）：同上出确认卡 → 解绑 → 用旧卡点【确认】；再用一个从没绑过的假号点「收款」、打「ยืนยัน」→
  点按钮的两次只回「请先绑定」、打字那次当绑定码试、回「码不对」；草稿原样、没有写账件、没有新记录；最后作废这张草稿。

LINE 那两种**不发任何 LINE 消息**：只在这个进程里把共用层的回复 / 推送 / 加载动画换成记录器、把发往 LINE 的 HTTP 挡掉，
LINE 后台任务（等结果、通知）不排；假 LINE 号以 ``Uacc`` 开头、跑完解绑。事件按 webhook 的同一套函数投
（`pearnly_core.line._claim_event` → `_dispatch_with_feedback` → `_finish_event`，验签那一步不在这里）。
防护同别的验收站点脚本：只在测试租户跑；这家公司由 CC 报上来的账套必须全部就是 ledger。
"""
import base64
import contextlib
import json
import secrets
import sys
import time
from unittest.mock import patch

import frappe

sys.argv = [base64.b64decode(a[4:]).decode("utf-8") if a.startswith("b64:") else a for a in sys.argv]
DONE = ("written", "failed", "withdrawn")
JOB = "Pearnly CC Job"


def same_path(left, right) -> bool:
    def norm(text):
        return str(text or "").strip().replace("/", "\\").rstrip("\\").casefold()
    return norm(left) == norm(right)


class Blocked:
    """发往 LINE 的 HTTP 一律不出这个进程（回复 / 推送已换成记录器；这是第二道）。"""

    def __getattr__(self, name):
        def refuse(*args, **kwargs):
            raise RuntimeError("acceptance: no LINE traffic from this script")
        return refuse


site, params = sys.argv[1], json.loads(sys.argv[2])
frappe.init(site=site, sites_path="/home/frappe/frappe-bench/sites")
frappe.connect()
result = {"ok": False}
bound_here = []
try:
    from pearnly_core.cc_desktop import books
    from pearnly_core.execution.replay import TEST_SITES
    from pearnly_erp_portal.collection import contracts, service

    if not (TEST_SITES.fullmatch(site) or site.endswith(".maclab.localhost")):
        raise SystemExit(f"refuse: {site} is not a test tenant")
    filters = {"tax_id": params["company_tax"], "is_active": 1}
    if params.get("branch"):
        filters["branch"] = params["branch"]
    company = None
    for name in frappe.get_all("ERP Company", filters=filters, pluck="name", order_by="name"):
        tax_id, branch = frappe.db.get_value("ERP Company", name, ["tax_id", "branch"])
        reported = [item.get("path") for item in books.for_company(tax_id, branch)]
        result.setdefault("reported", {})[name] = reported
        if reported and all(same_path(path, params["ledger"]) for path in reported):
            company = name
            break
    if not company:
        raise SystemExit("refuse: no company whose reported books are exactly the acceptance ledger")
    user, mode = params["user"], params["mode"]
    frappe.set_user(user)

    def jobs_of(name) -> int:
        return frappe.db.count(JOB, {"source_doctype": service.DOCTYPE, "source_name": name})

    def look(name) -> dict:
        frappe.db.rollback()
        view = service.status(name)
        row = frappe.db.get_value(service.DOCTYPE, name, ["job", "book_key", "consent_ref", "attempt"], as_dict=True)
        job = frappe.db.get_value(JOB, row.job, ["status", "verify_state", "reason", "stage"], as_dict=True) if row.job else None
        return {"status": view["status"], "docnum": view.get("docnum") or "", "returned": bool(view.get("returned")),
                "waiting": bool(service.express_waiting(name)), "job": job, "jobs": jobs_of(name)}

    def settle(name, wait=None):
        """等到这一笔有结果，回它此刻的样子（同 `erp_collection_post.py` 的记录形状）。"""
        deadline = time.monotonic() + int(wait or params.get("wait", 300))
        while True:
            frappe.db.rollback()
            view = service.status(name)
            if view["status"] in DONE or view.get("returned") or time.monotonic() > deadline:
                break
            time.sleep(3)
        row = frappe.db.get_value(service.DOCTYPE, name, ["job", "book_key", "consent_ref", "attempt"], as_dict=True)
        view["job"] = frappe.db.get_value(JOB, row.job, ["status", "verify_state", "reason"], as_dict=True) if row.job else None
        view["book_paths"] = [item.path for item in frappe.get_all(books.BOOK, filters={"book_key": row.book_key}, fields=["path"])]
        view["consent_ref"], view["attempt"], view["jobs"] = row.consent_ref, int(row.attempt or 0), jobs_of(name)
        return view

    if mode == "settle":
        result["records"] = [settle(name) for name in params["names"]]
        result.update(ok=True, company=company)
        raise SystemExit("")

    # ── 这张发票此刻可收（刚开的要等电脑读回）
    invoice, customer = params["invoice"], service.customer_code(company, str(params["customer"]))
    deadline, row = time.monotonic() + int(params.get("read_wait", 120)), None
    while True:
        found = service.open_invoices(company, customer)
        frappe.db.commit()
        row = next((item for item in found["items"] if item["docnum"] == invoice), None)
        if row or time.monotonic() > deadline:
            break
        time.sleep(3)
    if not row:
        raise SystemExit(f"invoice {invoice} never showed up as collectable")
    result["invoice"] = row
    if mode == "warm":
        # 电脑在线时先把这张发票读进未收快照（离线那格：停 CC 之前做；收款员平时看到的就是读回过的快照）
        result.update(ok=True, company=company)
        raise SystemExit("")
    token = secrets.token_hex(6)

    def record_for(entry, cash) -> str:
        invoices = [{"docnum": invoice, "amount": str(cash)}]
        if entry == "web":
            card = service.submit_web(company, customer, invoices, cash=str(cash), methods=[], request=f"acc-{token}-{entry}")
            frappe.db.commit()
            return card["name"]
        card = service.create_draft(company, customer, invoices, cash=str(cash), methods=[], source=entry)
        frappe.db.commit()
        released = service.release(card["name"], card["version"], f"{entry}:acc-{token}")
        frappe.db.commit()
        if released.get("status") != "released":
            raise SystemExit(f"release was not accepted: {released.get('status')} {released.get('code')}")
        return card["name"]

    if mode == "submit":
        names = [record_for(item["entry"], item["cash"]) for item in params["entries"]]
        seen = {name: [] for name in names}
        stop_at = time.monotonic() + int(params.get("watch", 90))
        while True:
            for name in names:
                seen[name].append(look(name))
            if time.monotonic() > stop_at:
                break
            time.sleep(3)
        result["records"] = [{"name": name, "entry": item["entry"], "cash": str(item["cash"]), "looks": len(seen[name]),
                              "written_seen": any(view["status"] == "written" or view["docnum"] for view in seen[name]),
                              "waiting_seen": any(view["waiting"] for view in seen[name]),
                              "leased_seen": any((view["job"] or {}).get("status") == "leased" for view in seen[name]),
                              "last": seen[name][-1]} for name, item in zip(names, params["entries"])]
        result.update(ok=True, company=company)
        raise SystemExit("")

    if mode not in ("line_redelivered", "line_unbound"):
        raise SystemExit(f"unknown mode {mode}")

    # ── LINE：只在这个进程里把发往 LINE 的都换成记录器
    from pearnly_core import line, line_binding, line_session
    from pearnly_erp_portal.access import guard
    from pearnly_erp_portal.integrations import line_collection, line_collection_cards, line_menu

    if not guard.has("integration.read", user=user) or not guard.has(line_collection.WRITE, user=user):
        raise SystemExit("refuse: the acceptance employee has no LINE / collection capability")
    sent = []

    def keep(kind):
        def record(*args, **kwargs):
            sent.append({"kind": kind, "to": str(args[0]) if args else "", "messages": args[1] if len(args) > 1 else kwargs.get("messages")})
            return True
        return record

    real_enqueue = frappe.enqueue

    def enqueue(method, *args, **kwargs):
        if str(method).startswith("pearnly_erp_portal.integrations.line_collection"):
            sent.append({"kind": "job-skipped", "to": str(method), "messages": None})
            return None
        return real_enqueue(method, *args, **kwargs)

    stack = contextlib.ExitStack()
    for target, attribute, value in ((line, "reply", keep("reply")), (line, "push", keep("push")), (line, "reply_or_push", keep("reply_or_push")),
                                     (line, "start_loading", lambda *a, **k: True), (line, "start_loading_background", lambda *a, **k: (lambda: None)),
                                     (line, "_feedback_begin", lambda event: None), (line, "user_language", lambda *a, **k: "zh"),
                                     (line, "requests", Blocked()), (line_menu, "ensure", lambda *a, **k: None),
                                     (line_binding, "_notify_changed", lambda *a, **k: None), (frappe, "enqueue", enqueue)):
        if hasattr(target, attribute):
            stack.enter_context(patch.object(target, attribute, value))

    with stack:
        def fake_id() -> str:
            return "Uacc" + secrets.token_hex(14)

        def bind(line_id):
            prior = frappe.db.sql("select line_user_id from `tabPearnly LINE Binding` where user=%s and status='active'", (user,))
            if prior:
                raise SystemExit("refuse: the acceptance employee already has an active LINE binding (not replacing it)")
            line_binding.bind(line_id, user, "acc")
            bound_here.append(line_id)

        def deliver(event):
            """按 webhook 的口径投一个事件：领事件号 → 交产品处理器 → 记结论。回（领到没有，这次回了什么）。"""
            before = len(sent)
            claimed = line._claim_event(event["webhookEventId"], source="line_webhook")
            handled = None
            if claimed:
                frappe.set_user("Guest")   # webhook 是 Guest，处理器按绑定的账号办事
                try:
                    handled = line._dispatch_with_feedback(event)
                    line._finish_event(event["webhookEventId"], "done" if handled else "ignored")
                finally:
                    frappe.set_user(user)
            frappe.db.commit()
            return {"event": event["webhookEventId"], "claimed": bool(claimed), "handled": handled,
                    "replies": [item for item in sent[before:] if item["kind"] != "job-skipped"]}

        def postback(line_id, event_id, data, redelivery=False):
            return {"type": "postback", "mode": "active", "webhookEventId": event_id, "timestamp": int(time.time() * 1000),
                    "deliveryContext": {"isRedelivery": redelivery}, "source": {"type": "user", "userId": line_id},
                    "replyToken": "acc-reply-" + secrets.token_hex(8), "postback": {"data": data}}

        def card_for(line_id, cash) -> dict:
            draft = service.create_draft(company, customer, [{"docnum": invoice, "amount": str(cash)}], cash=str(cash), methods=[],
                                         source="line")
            frappe.db.commit()
            line_collection._show_card(line_id, {}, service.card(draft["name"]), "acc-reply-card")
            frappe.db.commit()
            payload = (line_session.get_session(line_id) or {}).get("payload") or {}
            if payload.get("step") != "card" or payload.get("name") != draft["name"]:
                raise SystemExit(f"the confirm card was not issued (step {payload.get('step')})")
            data = line_collection_cards._data("ok", n=draft["name"], ver=payload["version"], k=payload["nonce"])
            return {"name": draft["name"], "version": payload["version"], "data": data}

        def collections_now() -> int:
            return frappe.db.count(service.DOCTYPE, {"company": company})

        if mode == "line_redelivered":
            line_id = fake_id()
            bind(line_id)
            card = card_for(line_id, params.get("cash") or "300")
            first_id, second_id = f"acc-{token}-1", f"acc-{token}-2"
            deliveries = [deliver(postback(line_id, first_id, card["data"])),                    # 首投
                          deliver(postback(line_id, first_id, card["data"], redelivery=True)),   # LINE 补发同一事件
                          deliver(postback(line_id, second_id, card["data"]))]                   # 连点：新事件号、同一按钮
            result.update(deliveries=deliveries, card={"name": card["name"], "version": card["version"]},
                          jobs_after_taps=jobs_of(card["name"]),
                          consent=frappe.db.get_value(service.DOCTYPE, card["name"], "consent_ref"))
            result["record"] = settle(card["name"])
        else:
            line_id = fake_id()
            bind(line_id)
            card = card_for(line_id, params.get("cash") or "300")
            before = collections_now()
            line_binding.unbind(line_id)
            frappe.db.commit()
            stranger = fake_id()
            taps = [deliver(postback(line_id, f"acc-{token}-old", card["data"])),          # 解绑后点旧卡的【确认】
                    deliver(postback(stranger, f"acc-{token}-start", "a=col&s=start")),     # 没绑过的号点「收款」
                    deliver({"type": "message", "mode": "active", "webhookEventId": f"acc-{token}-text", "timestamp": int(time.time() * 1000),
                             "source": {"type": "user", "userId": stranger}, "replyToken": "acc-reply-" + secrets.token_hex(8),
                             "message": {"type": "text", "id": token, "text": "ยืนยัน"}})]      # 没绑过的号打「确认」
            previous, frappe.local.lang = getattr(frappe.local, "lang", None), "zh"   # 回复用手机 LINE 的语言（这里记录器回 zh）
            expect = [frappe._("Bind this account first"), frappe._("Bind this account first"), frappe._("That code is not valid")]
            frappe.local.lang = previous
            for tap, words in zip(taps, expect):
                tap["texts"] = [message.get("text") for item in tap["replies"] for message in (item.get("messages") or [])
                                if isinstance(message, dict)]
                tap["expect"] = words
            frappe.db.rollback()
            draft = frappe.db.get_value(service.DOCTYPE, card["name"], ["status", "draft_version", "job", "docnum", "consent_ref"], as_dict=True)
            result.update(taps=taps, card={"name": card["name"], "version": card["version"]},
                          after={"status": draft.status, "version": int(draft.draft_version or 0), "job": draft.job or "",
                                 "docnum": draft.docnum or "", "consent": draft.consent_ref or "", "jobs": jobs_of(card["name"])},
                          collections={"before": before, "after": collections_now()},
                          stranger_session=bool(line_session.get_session(stranger)))
            service.discard_draft(card["name"])
            frappe.db.commit()
            result["record"] = service.status(card["name"])
    result.update(ok=True, company=company, mode=mode)
except SystemExit as stop:
    if str(stop):
        result["why"] = str(stop)
except Exception as error:
    frappe.db.rollback()
    code = getattr(error, "code", None)
    result["why"] = f"collection rule refused: {code} {getattr(error, 'detail', '')}" if code else frappe.get_traceback()[-1500:]
finally:
    for line_id in bound_here:   # 假 LINE 号跑完解绑（解绑过的再解一次无害）
        try:
            from pearnly_core import line_binding as _binding

            with patch.object(_binding, "_notify_changed", lambda *a, **k: None):
                _binding.unbind(line_id)
        except Exception:  # noqa: BLE001
            pass
    frappe.destroy()
print("ACC " + json.dumps(result, ensure_ascii=False, default=str))
