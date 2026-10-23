"""CC 桌面版共用机器通道：写账队列（多产品共用，ADR-025）。

产品把一张单冻结成 Express 载荷交进来（:func:`enqueue`，同一个请求号只建一件），电脑来领（:func:`lease`）、写完回报
（:func:`acknowledge`），每次状态变了经产品声明的 ``job_changed`` 在同一事务里交回产品。线上形状与 Cowork 的领单 / 回执完全
相同（共用的 `wire.queue_contracts`），CC 桌面版写账的代码一行不分叉。

- **谁写**（用户 10-16「谁的活谁的电脑做」，共用纯规则 `wire.route_contracts`）：录入人登录过 CC 的电脑（一个登录记录都没有
  的电脑过渡期谁的都写），够得到这份账套、能写，CC 桌面版支持这张单的载荷版本；发起人的电脑都关着就排队，开机后接着写。
- **同一份账套同时只一件**（站点内写者锁）：领走时把账套键写进 ``writing_key``（唯一），另一台电脑同时领同一份账套的另一件
  会撞唯一约束、跳过；回报 / 收回时清掉。不同站点（Cowork 与 ERP 写同一份账套）之间的互斥在电脑那一侧（账套目录里的锁文件）。
- **每台电脑同时只一件**（CC 桌面版本来就单线程写）。
- **回执口径**与 Cowork 账本同一套（共用的 `wire.ack_rules`）：成功记单号与写后核对；等账套回到排队、不计次数；要人处理转人工；
  普通失败重试到 3 次转人工。
- **租约过期**（领走 120 秒没回报，电脑可能已经写了）：回到排队，但钉在原来那台电脑上，只有它能再领。线上的单据号就是这件的
  编号、每次领都一样，CC 桌面版本机记着「这件写过没有」（`task_journal`），写过的直接回上次的单号、不写第二张。过期也计一次，
  到 3 次转人工（结果不明）。迟到的回执（那台电脑后来才回报）照收。
"""
import json
import uuid
from datetime import timedelta

import frappe
from frappe import _

from pearnly_core.cc_desktop import books, contracts, devices, documents, products
from pearnly_core.cc_desktop.wire import ack_rules, queue_contracts, route_contracts
from pearnly_core.express_rules.push import wire_location

JOB = documents.JOB
OPEN = ("queued", "leased")
DONE = ("success", "manual", "cancelled")
STATUSES = (*OPEN, *DONE)
HEAD_SCAN = 500
FIELDS = ("name", "request_key", "source_doctype", "source_name", "label", "actor", "book_key", "payload_version",
          "status", "route_device", "attempt", "fence", "lease_device", "lease_expires_at", "last_device", "docnum",
          "stage", "reason", "verify_state", "verify_json", "written_by_device", "written_at", "creation", "modified")
LOST = "cc_desktop.result_unknown"
OUTDATED = "payload_version_outdated"


def _now():
    return devices.now()


# ── 给产品 ─────────────────────────────────────────────────────────────────────


def view(row) -> dict:
    """一件写账给产品看的样子（不含载荷）。"""
    if row is None:
        return None
    verify = {}
    try:
        verify = json.loads(row.get("verify_json") or "{}")
    except ValueError:
        pass
    return {"job": row.get("name"), "request_key": row.get("request_key"), "source_doctype": row.get("source_doctype"),
            "source_name": row.get("source_name"), "actor": row.get("actor"), "book_key": row.get("book_key"),
            "status": row.get("status"), "docnum": row.get("docnum") or "", "stage": row.get("stage") or "",
            "reason": row.get("reason") or "", "verify_state": row.get("verify_state") or "", "verify": verify,
            "attempt": int(row.get("attempt") or 0), "device": row.get("written_by_device") or row.get("lease_device")
            or row.get("route_device") or "", "written_at": row.get("written_at")}


def find(request_key) -> dict:
    rows = frappe.get_all(JOB, filters={"request_key": request_key}, fields=list(FIELDS), limit=1)
    return view(rows[0]) if rows else None


def find_by_name(name) -> dict:
    rows = frappe.get_all(JOB, filters={"name": name}, fields=list(FIELDS), limit=1)
    return view(rows[0]) if rows else None


def of_source(source_doctype, source_name) -> list:
    rows = frappe.get_all(JOB, filters={"source_doctype": source_doctype, "source_name": source_name},
                          fields=list(FIELDS), order_by="creation asc")
    return [view(row) for row in rows]


def enqueue(*, source_doctype, source_name, request_key, actor, book_key, payload, payload_version=1, label=""):
    """交一件写账（调用方的事务里）。同一个请求号已经交过就回那一件（不重建、不改载荷）。"""
    request_key = contracts.text(request_key, maximum=140)
    found = find(request_key)
    if found is not None:
        return found
    if not isinstance(payload, dict) or not book_key or not actor:
        frappe.throw(_("cc_desktop.job_invalid"))
    name = str(uuid.uuid4())
    documents.new(JOB, name, {
        "request_key": request_key, "source_doctype": source_doctype, "source_name": source_name,
        "label": str(label or "")[:140], "actor": actor, "book_key": book_key,
        "payload": json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        "payload_version": int(payload_version or 1), "status": "queued", "attempt": 0, "fence": 0})
    row = frappe.get_all(JOB, filters={"name": name}, fields=list(FIELDS))[0]
    products.job_changed(view(row))
    return view(row)


def cancel(name, reason="cancelled") -> dict:
    """撤掉一件还没写的（排队中、转人工的）：领走的不能撤（电脑正在写）。"""
    doc = frappe.get_doc(JOB, name, for_update=True)
    if doc.status not in ("queued", "manual"):
        frappe.throw(_("cc_desktop.job_busy"))
    doc.update({"status": "cancelled", "reason": reason, "route_device": None})
    documents.persist(doc)
    products.job_changed(view(doc.as_dict()))
    return view(doc.as_dict())


def retry(name) -> dict:
    """转人工的再排一次（人看过、处理好了）：从头计次数，结果不明的仍钉在原来那台电脑上。"""
    doc = frappe.get_doc(JOB, name, for_update=True)
    if doc.status != "manual":
        frappe.throw(_("cc_desktop.job_busy"))
    pinned = doc.route_device if doc.reason == LOST else None
    doc.update({"status": "queued", "attempt": 0, "reason": "", "stage": "", "route_device": pinned})
    documents.persist(doc)
    products.job_changed(view(doc.as_dict()))
    return view(doc.as_dict())


def writers(actor, book_key) -> dict:
    """``actor`` 交到 ``book_key`` 的单此刻有没有电脑写：``{state, devices}``。state：``ok``（有在线的）、``offline``
    （他能写的电脑都关着，单子排队）、``unreachable``（他有电脑、都够不到这份账套）、``missing``（他一台登录过 CC 的电脑都
    没有，界面写「CC 桌面版未连接」）。"""
    peers = devices.peers()
    mine = [name for name, peer in peers.items() if route_contracts.serves(peer.users, actor)]
    if not mine:
        return {"state": "missing", "devices": []}
    reach = set((books.reachers([book_key]) or {}).get(book_key) or ())
    able = [name for name in mine if name in reach]
    if not able:
        return {"state": "unreachable", "devices": []}
    return {"state": "ok" if any(peers[name].online for name in able) else "offline", "devices": able}


# ── 电脑领单 ───────────────────────────────────────────────────────────────────


def _recover(moment):
    """领走后过了租约还没回报的：回到排队、钉在原来那台电脑上（它本机记着写没写过），计一次；到 3 次转人工。"""
    for name in frappe.get_all(JOB, filters={"status": "leased", "lease_expires_at": ["<=", moment]}, pluck="name"):
        doc = frappe.get_doc(JOB, name, for_update=True)
        if doc.status != "leased" or not doc.lease_expires_at or doc.lease_expires_at > moment:
            continue
        attempt = int(doc.attempt or 0) + 1
        values = {"lease_device": None, "lease_expires_at": None, "writing_key": None, "attempt": attempt,
                  "route_device": doc.lease_device, "reason": LOST}
        values["status"] = "manual" if attempt >= ack_rules.MAX_ATTEMPTS else "queued"
        doc.update(values)
        documents.persist(doc)
        products.job_changed(view(doc.as_dict()))


def _busy(device, moment):
    return bool(frappe.db.sql(f"""SELECT name FROM `tab{JOB}` WHERE lease_device = %s AND status = 'leased'
        AND lease_expires_at > %s LIMIT 1""", (device, moment)))


def _heads(keys):
    """各份账套排在最前的那件（按交进来的先后）；有一件正在写的账套不算（等它写完）。"""
    marks = ", ".join(["%s"] * len(keys))
    taken = {row[0] for row in frappe.db.sql(f"""SELECT DISTINCT book_key FROM `tab{JOB}`
        WHERE status = 'leased' AND book_key IN ({marks})""", tuple(keys))}
    rows = frappe.db.sql(f"""SELECT name, actor, book_key, payload_version, route_device FROM `tab{JOB}`
        WHERE status = 'queued' AND book_key IN ({marks}) ORDER BY creation, name LIMIT {HEAD_SCAN}""",
                         tuple(keys), as_dict=True)
    heads, seen = [], set(taken)
    for row in rows:  # 每份账套取第一条（同 `route_contracts.heads`，这里的键叫 book_key）
        if row.book_key in seen:
            continue
        seen.add(row.book_key)
        heads.append(row)
        if len(heads) >= route_contracts.MAX_HEADS:
            break
    return heads


def _claim(context, request, moment):
    reach = books.writable(context.device)
    if not reach:
        return []
    heads = _heads(sorted(reach))
    if not heads:
        return []
    peers = devices.peers()
    me = peers.get(context.device) or route_contracts.Peer()
    peers[context.device] = me._replace(online=True)
    reachers = books.reachers([head.book_key for head in heads])
    for head in heads:
        job = route_contracts.Job(head.name, head.book_key, head.actor, int(head.payload_version or 1),
                                  head.route_device or "")
        decision = route_contracts.decide(context.device, job, reachers.get(head.book_key) or [], peers,
                                          route_contracts.Account(), moment)
        if not decision.take and decision.reason != route_contracts.NEEDS_UPDATE:
            continue
        doc = frappe.get_doc(JOB, head.name, for_update=True)
        if doc.status != "queued":
            continue
        if not decision.take:
            # 归这张单、够得到它的电脑没有一台支持这个载荷版本：等不来，转人工（与 Cowork 同一个原因码）
            doc.update({"status": "manual", "reason": OUTDATED, "stage": "needs_review"})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        if not devices.issuer_valid(doc.actor):
            doc.update({"status": "manual", "reason": "cc_desktop.actor_not_allowed", "stage": "needs_review"})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        point = "pcc_lease_" + uuid.uuid4().hex
        frappe.db.savepoint(point)
        try:
            doc.update({"status": "leased", "lease_device": context.device, "last_device": context.device,
                        "lease_expires_at": moment + timedelta(seconds=ack_rules.LEASE_SECONDS),
                        "writing_key": doc.book_key, "fence": int(doc.fence or 0) + 1})
            documents.persist(doc)
        except frappe.UniqueValidationError:
            # 同一份账套另一件刚被别的电脑领走（写者锁）：这份账套等它写完，看下一份
            frappe.db.rollback(save_point=point)
            continue
        products.job_changed(view(doc.as_dict()))
        spelling = reach[doc.book_key]
        payload = wire_location.localize(json.loads(doc.payload or "{}"), path=spelling.path, root=spelling.root,
                                         row=spelling.row, uid=spelling.uid)
        attended = route_contracts.attended(job, peers.get(context.device))
        return [queue_contracts.job(doc.name, {"history": doc.source_name or "", "invoice_no": doc.label or None,
                                               "frozen_payload": payload}, attended)]
    return []


def lease(context, body) -> dict:
    request = queue_contracts.lease_request(body)
    del request
    context = devices.revalidate(context)
    moment = _now()
    _recover(moment)
    devices.touch(context)
    jobs = [] if _busy(context.device, moment) else _claim(context, {}, moment)
    return queue_contracts.lease_response(jobs, None)


# ── 电脑回报 ───────────────────────────────────────────────────────────────────


def _verify(meta):
    verify = (meta or {}).get("verify") if isinstance(meta, dict) else None
    if not isinstance(verify, dict):
        return "", ""
    state = str(verify.get("state") or "")[:20]
    return state, json.dumps(verify, ensure_ascii=False, sort_keys=True)[:10000]


def _reply(doc, stage, replayed=False):
    status = {"queued": "pending", "leased": "pending"}.get(doc.status, doc.status)
    return queue_contracts.ack_response({
        "response_body": {"meta": {"stage": stage}}, "status": status, "replayed": replayed,
        "external_docnum": doc.docnum or None, "wire_attempt": max(1, int(doc.attempt or 0)),
        "result_unknown": False, "reason": doc.reason or ""})


def acknowledge(context, body) -> dict:
    request = queue_contracts.ack_request(body)
    context = devices.revalidate(context)
    found = frappe.db.sql(f"SELECT name FROM `tab{JOB}` WHERE name = %s FOR UPDATE", (request["log_id"],))
    if not found:
        raise frappe.AuthenticationError
    doc = frappe.get_doc(JOB, found[0][0])
    success = request["result"] == "success"
    if doc.status == "success":
        # 同一件已经记成功（回执重发）：照原样回，不再改
        if doc.written_by_device != context.device:
            raise frappe.AuthenticationError
        return _reply(doc, "success", replayed=True)
    mine = (doc.status == "leased" and doc.lease_device == context.device) or (
        doc.status in ("queued", "manual") and doc.reason == LOST and doc.route_device == context.device)
    if not mine:
        # 不是这台电脑领的、已经交给别人的：这次回执不算（电脑丢掉它）
        raise frappe.AuthenticationError
    transition = ack_rules.acknowledgement(branch="legacy", attempt=int(doc.attempt or 0), success=success,
                                           outcome=request["outcome"], error=request["error"])
    values = {"lease_device": None, "lease_expires_at": None, "writing_key": None, "stage": transition.stage,
              "meta_json": json.dumps(request["meta"] or {}, ensure_ascii=False, sort_keys=True)[:200000]}
    if transition.status == "success":
        state, verify = _verify(request["meta"])
        values.update(status="success", docnum=str(request["express_docnum"] or "")[:40], reason="",
                      verify_state=state, verify_json=verify, written_by_device=context.device,
                      written_at=_now(), route_device=None)
    elif transition.status == "pending":
        # 等账套 / 普通失败重试：没写进去（CC 桌面版失败整单回滚），谁的电脑能写都行
        values.update(status="queued", attempt=transition.attempt, reason=transition.reason or "", route_device=None)
    else:
        values.update(status="manual", attempt=transition.attempt, reason=transition.reason or transition.stage,
                      route_device=None)
    doc.update(values)
    documents.persist(doc)
    products.job_changed(view(doc.as_dict()))
    return _reply(doc, transition.stage)
