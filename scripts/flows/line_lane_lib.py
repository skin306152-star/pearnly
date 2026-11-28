"""实验栈实证（11-28，LINE 按用户一条道）：同一个 LINE 用户几乎同时发来多条事件时，每条都收下、都有结果、顺序稳定。

在本机实验栈（maclab，站点 ``*.maclab.localhost``）的 bench 容器里跑，一个进程模拟 LINE 平台：

- 每个 webhook 请求一个线程、各自的 Frappe 上下文与数据库连接（同 gunicorn gthread 的请求线程），签名验过之后调真实的
  ``pearnly_core.line.webhook``，回应之后跑请求的 ``after_response`` 回调（修后：在这里起道的排空线程，与线上同一段代码）；
- LINE 出站（回复 / 推送 / 三个点）与取图换成记录器，识别换成假引擎（睡 1–3 秒、记计费参考号，零费用），识别队列换成
  本进程里 2 路并发的线程（同线上 ocr 池的并发度），不排进栈里真正的 ocr worker；
- 只建、只删自己的测试数据（LINE 用户 ``Ulanelab…``、绑定、会话、收单记录、事件行）。

    docker exec -i <栈>-erp-bench-1 /home/frappe/frappe-bench/env/bin/python - erp erp.maclab.localhost <账号> <公司> [--images 10] < scripts/flows/line_lane_lib.py
    docker exec -i <栈>-cowork-bench-1 /home/frappe/frappe-bench/env/bin/python - cowork cowork.maclab.localhost <账号> [--images 10] < 同上

回归实验室里由流程 `erp.line.lane` / `cowork.line.lane`（`scripts/flows/line_lane_burst.py`）照同样的跑法调它。
修前（没有道的源码）与修后各跑一次。末行打印 ``RESULT {...}``，每格 ``ok`` 为真才算通过（退出码 0）。

格：① 同一个人同时发 10 张（ERP 途中点第一张卡上的「取消」）：10 条收单、10 个计费参考号各一次、10 张卡、收单按发送序、
没有「Could not store」、没有 failed 事件、推送 0；② 重投同一事件：不多一条、不多扣；③ 两个人同时各发 10 张：各自同 ①；
④（只在有道的源码上）线程死在半路：没回复过的那条推一次「请稍后重试」、回复过的不推，后面排着的由扫描接走、只处理一次。
"""
import json
import os
import random
import secrets
import sys
import threading
import time
import traceback
import types
from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import patch

import frappe

SITES = "/home/frappe/frappe-bench/sites"
SIGNATURE = "lab-signature"
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class Recorder:
    def __init__(self):
        self.lock = threading.Lock()
        self.out, self.billing, self.loading = [], [], 0

    def post(self, url, headers=None, json=None, timeout=None, **kwargs):
        with self.lock:
            if url.endswith("/chat/loading/start"):
                self.loading += 1
            else:
                kind = "reply" if url.endswith("/reply") else "push" if url.endswith("/push") else url
                self.out.append({"kind": kind, "at": time.time(), "to": (json or {}).get("to"),
                                 "token": (json or {}).get("replyToken"), "messages": (json or {}).get("messages") or []})
        return _Response()

    def get(self, *args, **kwargs):
        return _Response(content=PNG)

    def request(self, method, url, **kwargs):
        return self.post(url, **kwargs) if method.upper() == "POST" else self.get(url, **kwargs)


class _Response:
    status_code = 200

    def __init__(self, content=b"{}"):
        self.content, self.text, self.headers = content, content.decode("latin1"), {}

    def json(self):
        return {}

    def iter_content(self, size=None):
        yield self.content

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class AfterResponse(list):
    def add(self, fn):
        self.append(fn)


def connect(site):
    if not site.endswith((".maclab.localhost", ".regression.localhost")):
        raise SystemExit("lab sites only")
    os.chdir(SITES)
    frappe.init(site=site, sites_path=SITES, force=True)
    frappe.connect()


class Lab:
    def __init__(self, product, site, user, company):
        self.product, self.site, self.user, self.company = product, site, user, company
        self.rec = Recorder()
        self.ocr_slots = threading.BoundedSemaphore(2)
        self.ocr_threads, self.events_sent, self.reply_tokens = [], {}, {}

    # ── 替身 ────────────────────────────────────────────────────────────────
    def patches(self, stack):
        from pearnly_core import line
        from pearnly_core.execution import work

        fake = types.SimpleNamespace(post=self.rec.post, get=self.rec.get, request=self.rec.request,
                                     RequestException=line.requests.RequestException,
                                     exceptions=line.requests.exceptions, Session=line.requests.Session)
        stack.enter_context(patch.object(line, "requests", fake))
        stack.enter_context(patch.object(line, "verify_signature", lambda body, sig: sig == SIGNATURE))
        stack.enter_context(patch.object(line, "can_send", lambda: True))
        stack.enter_context(patch.object(line, "mirroring", lambda: False))
        stack.enter_context(patch.object(line, "_headers", lambda: {"Authorization": "Bearer lab"}))
        stack.enter_context(patch.object(line, "get_profile", lambda user_id: {}))
        stack.enter_context(patch.object(line, "user_language", lambda user_id, default=None: "th"))
        stack.enter_context(patch.object(line, "download_message_content", lambda message_id, **kw: PNG))
        stack.enter_context(patch.object(work, "submit", self.submit))
        if self.product == "erp":
            from pearnly_erp_portal.integrations import intake

            stack.enter_context(patch.object(intake, "_recogniser", lambda direction: self))
        else:
            from pearnly_cowork.files import service as originals
            from pearnly_cowork.line import cards, intake as cw_intake
            from pearnly_cowork.ocr.execution import service as recognition

            real_enqueue = frappe.enqueue

            def enqueue(method, **kwargs):
                if method != "pearnly_cowork.line.intake.store":
                    return real_enqueue(method, **kwargs)
                for key in ("queue", "enqueue_after_commit", "deduplicate", "job_id"):
                    kwargs.pop(key, None)
                self.background(lambda: cw_intake.store(**kwargs), "Guest")

            stack.enter_context(patch.object(cw_intake.frappe, "enqueue", enqueue))
            stack.enter_context(patch.object(originals, "create",
                                             lambda workspace, filename, content, key: {"name": "lanelab-src-" + key}))
            stack.enter_context(patch.object(recognition, "submit", self.cowork_submit))
            stack.enter_context(patch.object(cards, "previews", lambda records, card=None: [
                {"type": "flex", "altText": str(record), "contents": {}} for record in records]))

    def recognise(self, files, billing_reference="", **kwargs):
        """假识别：睡 1–3 秒、记一次计费参考号，回一张只有服务行的采购票（不触发按行追问）。"""
        time.sleep(random.uniform(1, 3))
        with self.rec.lock:
            self.rec.billing.append(billing_reference)
        tag = billing_reference.rsplit(":", 1)[-1]
        return {"doc_no": "LANE-" + tag[-6:], "doc_date": "2026-11-28", "doc_kind": "purchase_invoice", "has_vat": 1,
                "supplier_name": "lane lab supplier",
                "lines": [{"item_type": "service", "description": "lane lab service", "qty": 1, "unit_price": 10,
                           "vat_rate": 7}]}

    def background(self, fn, user):
        """后台任务替身：本进程里起线程跑（同 short 队列的任务，自建自关上下文）。"""
        def job():
            connect(self.site)
            try:
                frappe.set_user(user)
                fn()
                frappe.db.commit()
            except Exception:
                frappe.db.rollback()
                with self.rec.lock:
                    self.rec.out.append({"kind": "job-error", "error": traceback.format_exc()[-800:]})
            finally:
                frappe.destroy()

        thread = threading.Thread(target=job)
        thread.start()
        self.ocr_threads.append(thread)

    def cowork_submit(self, source, key, **kwargs):
        """Cowork 受理识别替身：记一次参考号；2 路并发睡 1–3 秒后按识别完成回调（同 `cowork_ocr_recognized`）。"""
        from pearnly_cowork.line import intake as cw_intake

        with self.rec.lock:
            self.rec.billing.append(key)

        def done():
            with self.ocr_slots:
                time.sleep(random.uniform(1, 3))
                cw_intake.announce(source, "saved", ["lanelab-record-" + key])

        self.background(done, frappe.session.user)
        return {"state": "queued"}

    def submit(self, kind, name, payload, user, **kwargs):
        """识别队列：本进程 2 路并发跑登记的识别入口（同线上 ocr 池），不进栈里真正的 ocr worker。"""
        from pearnly_core.execution import work

        def job():
            with self.ocr_slots:
                connect(self.site)
                try:
                    frappe.set_user(user)
                    frappe.get_attr(work.handler(kind))(**payload)
                    frappe.db.commit()
                except Exception:
                    frappe.db.rollback()
                    with self.rec.lock:
                        self.rec.out.append({"kind": "ocr-error", "error": traceback.format_exc()[-800:]})
                finally:
                    frappe.destroy()

        thread = threading.Thread(target=job)
        thread.start()
        self.ocr_threads.append(thread)
        return {"name": name}

    # ── 准备 / 清理 ─────────────────────────────────────────────────────────
    def prepare(self, line_user):
        from pearnly_core import line_binding, line_session

        connect(self.site)
        try:
            line_binding.bind(line_user, self.user, "lane lab")
            if self.product == "erp":
                from pearnly_erp_portal.integrations import line_flow

                payload = {"direction": "purchase", "company": self.company, "party": "@doc", "is_paid": 1,
                           "payment_method": "cash", "item_kind": "service"}
                frappe.set_user(self.user)
                if not line_flow.ready(payload):
                    raise SystemExit("ERP choices not ready, next step: " + str(line_flow._pending(payload)))
                line_session.set_session(line_user, line_flow.STATE, payload, ttl_minutes=30)
            else:
                from pearnly_cowork.line import flow as cw_flow

                payload = {"target": cw_flow.RECOGNIZE_ONLY, "direction": "purchase"}
                if not cw_flow.ready(payload):
                    raise SystemExit("Cowork choices not ready, next step: " + str(cw_flow._pending(payload)))
                line_session.set_session(line_user, cw_flow.STATE, payload, ttl_minutes=30)
            frappe.db.commit()
        finally:
            frappe.destroy()

    def cleanup(self, users):
        connect(self.site)
        try:
            for line_user in users:
                frappe.db.delete("Pearnly LINE Session", {"line_user_id": line_user})
                frappe.db.delete("Pearnly LINE Binding", {"line_user_id": line_user})
                if self.product == "erp":
                    frappe.db.delete("ERP LINE Intake", {"line_user_id": line_user})
                frappe.db.sql("delete from `tabPearnly LINE Event` where event_id like %s", ("lanelab-%",))
                if frappe.db.table_exists("Pearnly LINE Lane"):
                    frappe.db.delete("Pearnly LINE Lane", {"line_user_id": line_user})
            frappe.db.commit()
        finally:
            frappe.destroy()

    # ── 投递 ────────────────────────────────────────────────────────────────
    def deliver(self, events, start_gate=None):
        """一个 webhook 请求：签名验过 → 真实 webhook → 回应之后跑 after_response（修后在这里起道的线程）。"""
        from pearnly_core import line
        from werkzeug.wrappers import Request

        if start_gate:
            start_gate.wait()
        connect(self.site)
        try:
            frappe.set_user("Guest")
            body = json.dumps({"events": events}).encode()
            request = Request.from_values(method="POST", data=body, headers={"X-Line-Signature": SIGNATURE},
                                          content_type="application/json")
            request.after_response = AfterResponse()
            frappe.local.request = request
            frappe.local.response = frappe._dict()
            result = line.webhook()
            frappe.db.commit()
            for callback in request.after_response:
                callback()
            return result
        except Exception:
            frappe.db.rollback()
            with self.rec.lock:
                self.rec.out.append({"kind": "webhook-error", "error": traceback.format_exc()[-800:]})
        finally:
            frappe.destroy()

    def image(self, line_user, index, ts):
        mid = "%d%06d" % (int(ts), index)
        event = {"type": "message", "mode": "active", "timestamp": int(ts * 1000) + index,
                 "webhookEventId": "lanelab-" + secrets.token_hex(8), "replyToken": "rt-" + secrets.token_hex(8),
                 "source": {"type": "user", "userId": line_user},
                 "message": {"type": "image", "id": mid}, "deliveryContext": {"isRedelivery": False}}
        self.events_sent[event["webhookEventId"]] = (line_user, mid)
        self.reply_tokens[event["webhookEventId"]] = event["replyToken"]
        return event

    def burst(self, line_users, count):
        """每个用户同时发 ``count`` 张：每张一个 webhook 请求、同一时刻放行。"""
        gate = threading.Event()
        threads, ts = [], time.time()
        for line_user in line_users:
            for index in range(count):
                event = self.image(line_user, index, ts)
                threads.append(threading.Thread(target=self.deliver, args=([event], gate)))
        for thread in threads:
            thread.start()
        gate.set()
        for thread in threads:
            thread.join(120)

    def settle(self, timeout=150):
        """等道排空、识别跑完、卡片发完。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            for thread in list(self.ocr_threads):
                thread.join(0.2)
            alive = [t for t in threading.enumerate() if t.name == "pearnly-line-lane"] + \
                    [t for t in self.ocr_threads if t.is_alive()]
            if not alive:
                time.sleep(1)
                if not [t for t in threading.enumerate() if t.name == "pearnly-line-lane"] and \
                        not [t for t in self.ocr_threads if t.is_alive()]:
                    return True
            time.sleep(0.5)
        return False

    # ── 读结果 ──────────────────────────────────────────────────────────────
    def outcome(self, line_user, sent):
        connect(self.site)
        try:
            intakes = frappe.get_all("ERP LINE Intake", filters={"line_user_id": line_user},
                                     fields=["name", "line_message_id", "status", "creation"], order_by="creation asc",
                                     limit_page_length=100) if self.product == "erp" else []
            events = frappe.db.sql("select event_id, status, detail from `tabPearnly LINE Event` where event_id in %s",
                                   (tuple(e for e, (u, _m) in self.events_sent.items() if u == line_user) or ("-",),),
                                   as_dict=True)
        finally:
            frappe.destroy()
        texts, cards, pushes = [], 0, 0
        for out in self.rec.out:
            if out["kind"] not in ("reply", "push"):
                continue
            mine = out["kind"] == "push" and out["to"] == line_user or out["kind"] == "reply" and \
                out["token"] in self.tokens_of(line_user)
            if not mine:
                continue
            pushes += out["kind"] == "push"
            for message in out["messages"]:
                if message.get("type") == "flex":
                    cards += 1
                elif message.get("type") == "text":
                    texts.append(message.get("text"))
        if self.product == "erp":
            billing = [ref for ref in self.rec.billing if ref.rsplit(":", 1)[-1] in {i["name"] for i in intakes}]
            order = [i["line_message_id"] for i in intakes]
        else:
            mids = {m for _e, (u, m) in self.events_sent.items() if u == line_user}
            billing = [ref for ref in self.rec.billing if ref.split(":", 1)[-1] in mids]
            intakes = [None] * len(set(billing))
            order = []
        result = {
            "intakes": len(intakes), "billing": len(billing), "billing_unique": len(set(billing)), "cards": cards,
            "pushes": pushes, "order_by_send": order == sorted(order),
            "failed_events": [e["event_id"] for e in events if e["status"] in ("failed", "expired")],
            "could_not_store": sum("Could not store" in (t or "") or "ไม่สามารถ" in (t or "") for t in texts),
            "errors": [o.get("error") for o in self.rec.out if o["kind"].endswith("error")][:2],
            "texts": sorted(set(t for t in texts if t))[:6],
        }
        result["ok"] = (result["intakes"] == sent and result["billing_unique"] == sent == result["billing"]
                        and result["cards"] == sent and not result["failed_events"] and not result["could_not_store"]
                        and result["pushes"] == 0 and result["order_by_send"] and not result["errors"])
        return result

    def tokens_of(self, line_user):
        return {self.reply_tokens[e] for e, (u, _m) in self.events_sent.items() if u == line_user}


def _card_cancel(lab, line_user):
    """这个人收到的第一张卡上「取消」按钮的 postback 数据（等卡到，最多 60 秒）。"""
    deadline = time.time() + 60
    tokens = lab.tokens_of(line_user)
    while time.time() < deadline:
        with lab.rec.lock:
            outs = list(lab.rec.out)
        for out in outs:
            if out.get("kind") in ("reply", "push") and (out.get("token") in tokens or out.get("to") == line_user):
                for message in out["messages"]:
                    if message.get("type") != "flex":
                        continue
                    found = [d for d in _postbacks(message) if "cancel" in d.lower()]
                    if found:
                        return found[0]
        time.sleep(0.3)
    return ""


def _postbacks(node):
    if isinstance(node, dict):
        action = node.get("action") if isinstance(node.get("action"), dict) else None
        if action and action.get("type") == "postback" and action.get("data"):
            yield action["data"]
        for value in node.values():
            yield from _postbacks(value)
    elif isinstance(node, list):
        for value in node:
            yield from _postbacks(value)


def burst_with_tap(lab, line_user, count):
    """格 1：同一个人同时发 count 张；第一张卡到了以后，余下的一半与「点卡片上的取消」同时到。"""
    ts, half = time.time(), count // 2
    first = [lab.image(line_user, i, ts) for i in range(half)]
    gate = threading.Event()
    threads = [threading.Thread(target=lab.deliver, args=([event], gate)) for event in first]
    for thread in threads:
        thread.start()
    gate.set()
    data = _card_cancel(lab, line_user)
    second = [lab.image(line_user, i, ts) for i in range(half, count)]
    tap = {"type": "postback", "mode": "active", "timestamp": int(time.time() * 1000),
           "webhookEventId": "lanelab-" + secrets.token_hex(8), "replyToken": "rt-" + secrets.token_hex(8),
           "source": {"type": "user", "userId": line_user}, "postback": {"data": data}}
    lab.events_sent[tap["webhookEventId"]] = (line_user, "tap")
    lab.reply_tokens[tap["webhookEventId"]] = tap["replyToken"]
    gate2 = threading.Event()
    more = [threading.Thread(target=lab.deliver, args=([event], gate2)) for event in second + ([tap] if data else [])]
    for thread in more:
        thread.start()
    gate2.set()
    for thread in threads + more:
        thread.join(120)
    return {"tap_data": data, "tap_token": tap["replyToken"]}


def interrupted_cell(lab, line_user):
    """格 4（只在有道的源码上）：进程被回收、线程死在半路——没回复过的那条告诉本人一次，回复过的不打扰，
    后面排着的由扫描接走、只处理一次。"""
    from pearnly_core.line_lane import lane

    connect(lab.site)
    try:
        past = lane.utcnow() - timedelta(seconds=lane.LEASE_SECONDS + 30)
        frappe.get_doc({"doctype": lane.LANE, "line_user_id": line_user}).insert(ignore_permissions=True,
                                                                                 ignore_if_duplicate=True)
        frappe.db.set_value(lane.LANE, line_user, {"holder": "dead-worker", "lease_until": past})
        ts = time.time() - 40
        names = {}
        for key, status in (("silent", "processing"), ("answered", "processing"), ("waiting", "queued")):
            event = lab.image(line_user, len(names), ts + len(names))
            lane.store(event, line_user, lane.utcnow() - timedelta(seconds=40 - len(names)))
            if status == "processing":
                frappe.db.set_value(lane.EVENT, event["webhookEventId"],
                                    {"status": "processing", "lease_until": past, "attempts": 1})
            names[key] = event["webhookEventId"]
        frappe.db.commit()
        lane._mark_for(names["answered"])()
        lab.lane_runs = 0
        real_enqueue = frappe.enqueue

        def enqueue(method, **kwargs):
            if method != "pearnly_core.line_lane.lane.run":
                return real_enqueue(method, **kwargs)
            lab.lane_runs += 1
            lab.background(lambda: lane.run(kwargs["line_user_id"]), "Guest")

        with patch.object(lane.frappe, "enqueue", enqueue):
            lane.sweep()
            frappe.db.commit()
            lab.settle()
    finally:
        frappe.destroy()
    connect(lab.site)
    try:
        rows = {key: frappe.db.get_value(lane.EVENT, name, ["status", "replied", "attempts"], as_dict=True)
                for key, name in names.items()}
        intakes = frappe.db.count("ERP LINE Intake", {"line_user_id": line_user}) if lab.product == "erp" else None
    finally:
        frappe.destroy()
    pushes = [o for o in lab.rec.out if o.get("kind") == "push" and o.get("to") == line_user]
    result = {"rows": rows, "pushes": [m.get("text") for o in pushes for m in o["messages"]], "lane_runs": lab.lane_runs,
              "intakes": intakes}
    result["ok"] = (rows["silent"]["status"] == "failed" and not rows["silent"]["replied"]
                    and rows["answered"]["status"] == "failed" and rows["answered"]["replied"]
                    and rows["waiting"]["status"] == "done" and rows["waiting"]["attempts"] == 1
                    and len(pushes) == 1 and (intakes in (None, 1)))
    return result


def main(argv):
    results = run(argv)
    print("RESULT " + json.dumps(results, ensure_ascii=False, default=str))
    return 0 if all(v.get("ok") for v in results.values()) else 1


def run(argv) -> dict:
    product, site, user = argv[0], argv[1], argv[2]
    company = argv[3] if len(argv) > 3 and not argv[3].startswith("--") else ""
    count = int(argv[argv.index("--images") + 1]) if "--images" in argv else 10
    lab = Lab(product, site, user, company)
    results = {}
    express = None
    with ExitStack() as stack:
        lab.patches(stack)
        users = ["Ulanelab" + secrets.token_hex(12) for _ in range(4)]
        try:
            if product == "erp":
                # 识别后不去问 Express 的题（这一格只看收件与出卡）：临时关掉这家公司的接 Express，结束还原
                connect(site)
                try:
                    express = frappe.db.get_value("ERP Company", company, "express_enabled")
                    frappe.db.set_value("ERP Company", company, "express_enabled", 0, update_modified=False)
                    frappe.db.commit()
                finally:
                    frappe.destroy()
            for line_user in users:
                lab.prepare(line_user)
            # 格 1：同一个人同时发 count 张，途中点第一张卡上的「取消」
            tap = burst_with_tap(lab, users[0], count) if product == "erp" else None
            if tap is None:
                lab.burst([users[0]], count)
            lab.settle()
            one = lab.outcome(users[0], count)
            if tap is not None:
                answered = [m.get("text") for o in lab.rec.out if o.get("kind") == "reply"
                            and o.get("token") == tap["tap_token"] for m in o["messages"]]
                one["tap"] = {"data": tap["tap_data"], "answer": answered}
                one["ok"] = one["ok"] and bool(tap["tap_data"]) and bool(answered)
            results["same_user_burst"] = one
            # 格 2：重投同一事件（LINE 补发）：不多一条收单、不多扣一次
            first = next(e for e, (u, m) in lab.events_sent.items() if u == users[0] and m != "tap")
            replay = {"type": "message", "webhookEventId": first, "timestamp": int(time.time() * 1000),
                      "replyToken": "rt-replay", "source": {"type": "user", "userId": users[0]},
                      "message": {"type": "image", "id": lab.events_sent[first][1]},
                      "deliveryContext": {"isRedelivery": True}}
            lab.deliver([replay])
            lab.settle()
            again = lab.outcome(users[0], count)
            results["redelivery"] = {"intakes": again["intakes"], "billing": again["billing"],
                                     "ok": again["intakes"] == count and again["billing"] == count}
            # 格 3：两个不同的人同时连发，互不阻塞
            started = time.time()
            lab.burst(users[1:3], count)
            lab.settle()
            two = {u[-6:]: lab.outcome(u, count) for u in users[1:3]}
            results["two_users"] = {"seconds": round(time.time() - started, 1), "each": two,
                                    "ok": all(r["ok"] for r in two.values())}
            # 格 4：线程中断后扫描接走（修前的源码没有道，不跑）
            connect(site)
            try:
                has_lane = bool(frappe.db.table_exists("Pearnly LINE Lane"))
            finally:
                frappe.destroy()
            if has_lane:
                results["interrupted"] = interrupted_cell(lab, users[3])
        except Exception:
            results["crash"] = {"ok": False, "error": traceback.format_exc()[-1500:]}
        finally:
            lab.cleanup(users)
            if express is not None:
                connect(site)
                try:
                    frappe.db.set_value("ERP Company", company, "express_enabled", express, update_modified=False)
                    frappe.db.commit()
                finally:
                    frappe.destroy()
    return results


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
