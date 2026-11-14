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
          "stage", "reason", "verify_state", "verify_json", "written_by_device", "written_at", "meta_json", "creation", "modified")
LOST = "cc_desktop.result_unknown"
OUTDATED = "payload_version_outdated"


def _now():
    return devices.now()


# ── 给产品 ─────────────────────────────────────────────────────────────────────


def view(row) -> dict:
    """一件写账给产品看的样子（不含载荷）。"""
    if row is None:
        return None
    verify, meta = {}, {}
    try:
        verify = json.loads(row.get("verify_json") or "{}")
    except ValueError:
        pass
    try:
        meta = json.loads(row.get("meta_json") or "{}")
    except ValueError:
        pass
    return {"job": row.get("name"), "request_key": row.get("request_key"), "source_doctype": row.get("source_doctype"),
            "source_name": row.get("source_name"), "actor": row.get("actor"), "book_key": row.get("book_key"),
            "status": row.get("status"), "docnum": row.get("docnum") or "", "stage": row.get("stage") or "",
            "reason": row.get("reason") or "", "verify_state": row.get("verify_state") or "", "verify": verify,
            "attempt": int(row.get("attempt") or 0), "device": row.get("written_by_device") or row.get("lease_device")
            or row.get("route_device") or "", "written_at": row.get("written_at"),
            # 电脑写这张单实际用的客户 / 供应商码（新建的、复用的都报）：产品据此把单上的对方档挂上 Express 码
            "party_code": str((meta if isinstance(meta, dict) else {}).get("party_code") or "")[:40],
            # 逐行实际用的存货码（{seq, name, code}）：产品据此把随单新建的本地商品挂上 Express 码
            "line_codes": (meta.get("line_codes") if isinstance(meta, dict) else None) or [],
            # 收款单 RE（载荷 20）：电脑回报的单号与每张发票写后余额 {docnum, invoices: [{docnum, remaining_after}]}，原样带出；别的单为空
            "receipt": _receipt(meta)}


def _receipt(meta) -> dict:
    """回执 meta 里的 ``receipt``（CC 桌面版 ``dbf_receipt``）；不是对象或没有就是空字典。"""
    value = meta.get("receipt") if isinstance(meta, dict) else None
    return value if isinstance(value, dict) else {}


def find(request_key) -> dict:
    rows = frappe.get_all(JOB, filters={"request_key": request_key}, fields=list(FIELDS), limit=1)
    return view(rows[0]) if rows else None


def find_by_name(name) -> dict:
    rows = frappe.get_all(JOB, filters={"name": name}, fields=list(FIELDS), limit=1)
    return view(rows[0]) if rows else None


# ── 在等 Express 关闭（给各产品显示用，只读；11-12）──────────────────────────────────────────────
# 电脑回报「Express 开着这本账套」的任务：排队中、原因是 `ack_rules.EXPRESS_OPEN`、租约到期栏记着「上次回报 + 歇的秒数」
# （:func:`acknowledge`）。读的时候按上次回报到现在多久算（`ack_rules.waiting_for_express`），不另存标志：被领走、写成、
# 转人工后状态不再是排队中，电脑不再回报则到点过期。查不到的一律当作不在等。

_WAIT_FIELDS = ("name", "book_key", "status", "reason", "lease_expires_at")


def _reported_at(row):
    """这件上次回报「Express 开着」的时刻（没有回 None）。"""
    until = row.get("lease_expires_at")
    return until - timedelta(seconds=ack_rules.EXPRESS_OPEN_REST_SECONDS) if until else None


def _waits(rows, moment) -> dict:
    out = {}
    for row in rows:
        since = _reported_at(row)
        if ack_rules.waiting_for_express(row.get("status"), row.get("reason") or "", since, moment):
            out[row["name"]] = since
    return out


def _wait_view(since) -> dict:
    return {"waiting": since is not None, "since": since.isoformat() + "Z" if since is not None else None}


def express_wait(name, moment=None) -> dict:
    """这件写账此刻是不是在等 Express 关闭 → ``{"waiting": bool, "since": ISO UTC | None}``。"""
    return _wait_view(express_wait_many([name], moment).get(name))


def express_wait_many(names, moment=None) -> dict:
    """``{任务名: 上次回报的时刻}``，只含此刻在等 Express 关闭的那几件（列表用：一次查完，不逐笔查）。"""
    names = sorted({str(name) for name in names or () if name})
    if not names:
        return {}
    rows = frappe.get_all(JOB, filters={"name": ["in", names], "status": "queued", "reason": ack_rules.EXPRESS_OPEN},
                          fields=list(_WAIT_FIELDS), limit_page_length=len(names))
    return _waits(rows, moment or _now())


def book_express_wait(book_key, moment=None) -> dict:
    """这本账套此刻有没有任何一件在等 Express 关闭 → ``{"waiting": bool, "since": ISO UTC | None}``（取最近一次回报）。"""
    if not book_key:
        return _wait_view(None)
    rows = frappe.get_all(JOB, filters={"book_key": book_key, "status": "queued", "reason": ack_rules.EXPRESS_OPEN},
                          fields=list(_WAIT_FIELDS), order_by="lease_expires_at desc", limit_page_length=1)
    found = _waits(rows, moment or _now())
    return _wait_view(next(iter(found.values()), None))


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
    （他能写的电脑都关着，单子排队）、``not_yours``（他能写的电脑都关着，而别人的电脑在线、够得到这本——不派给别人，
    界面说「你的电脑离线」，不说成电脑离线，A 线 11-12 测试机1）、``unreachable``（他有电脑、都够不到这份账套）、
    ``missing``（他一台登录过 CC 的电脑都没有，界面写「CC 桌面版未连接」）。"""
    peers = devices.peers()
    mine = [name for name, peer in peers.items() if route_contracts.serves(peer.users, actor)]
    if not mine:
        return {"state": "missing", "devices": []}
    reach = set((books.reachers([book_key]) or {}).get(book_key) or ())
    able = [name for name in mine if name in reach]
    if not able:
        return {"state": "unreachable", "devices": []}
    if any(peers[name].online for name in able):
        return {"state": "ok", "devices": able}
    others = any(peer.online for name, peer in peers.items() if name in reach and name not in mine)
    return {"state": "not_yours" if others else "offline", "devices": able}


def waiting_on(actor, book_keys) -> list:
    """``actor`` 的活此刻在等的电脑名称：他自己登录过 CC、够得到这几本里任一本、此刻不在线的电脑（同事的不算，谁的活谁的电脑做）；
    他有在线的就回空（不在等）。代录排队时页面写「等待电脑 · 名称」（A 线 11-12）。"""
    keys = [key for key in book_keys or () if key]
    if not keys:
        return []
    peers = devices.peers()
    reach = {name for names in (books.reachers(keys) or {}).values() for name in names or ()}
    able = [name for name, peer in peers.items() if name in reach and route_contracts.serves(peer.users, actor)]
    if any(peers[name].online for name in able):
        return []
    return sorted(devices.label(name) for name in able)


# ── 电脑领单 ───────────────────────────────────────────────────────────────────


def _recover(moment, device):
    """领走后过了租约还没回报的：回到排队、钉在原来那台电脑上（它本机记着写没写过），计一次；到 3 次转人工。

    领它的电脑还在线（按需请求线程常来报到，与写账线分开跑）就续租约、不收回：代录一张单可以合法地超过租约（人用 Express 时
    暂停、开表单 / 保存等得久，ADR-021 11-02 修订）。来领的这台自己手上的在 :func:`_redeliver` 里处理。"""
    online = None
    for name in frappe.get_all(JOB, filters={"status": "leased", "lease_expires_at": ["<=", moment]}, pluck="name"):
        doc = frappe.get_doc(JOB, name, for_update=True)
        if doc.status != "leased" or not doc.lease_expires_at or doc.lease_expires_at > moment or doc.lease_device == device:
            continue
        online = devices.peers() if online is None else online
        if (online.get(doc.lease_device) or route_contracts.Peer()).online:
            doc.lease_expires_at = moment + timedelta(seconds=ack_rules.LEASE_SECONDS)
            documents.persist(doc)
            continue
        attempt = int(doc.attempt or 0) + 1
        values = {"lease_device": None, "lease_expires_at": None, "writing_key": None, "attempt": attempt,
                  "route_device": doc.lease_device, "reason": LOST}
        values["status"] = "manual" if attempt >= ack_rules.MAX_ATTEMPTS else "queued"
        doc.update(values)
        documents.persist(doc)
        products.job_changed(view(doc.as_dict()))


def _redeliver(context, moment):
    """这台电脑「领取中」的一件它又来领单：写账线单线程、做完才再领，来领就说明手上那件丢了（CC 被结束 / 重启、看门狗重启写账线、
    回执没送到）。照到期收回同样计一次（到 3 次转人工），不等租约到期，直接重发给它（新 fence、同一个编号，本机记着写没写过）；
    这份账套这会儿够不到就先不动。"""
    rows = frappe.get_all(JOB, filters={"status": "leased", "lease_device": context.device}, pluck="name",
                          order_by="creation asc", limit_page_length=5)
    if not rows:
        return []
    reach = books.writable(context.device)
    for name in rows:
        doc = frappe.get_doc(JOB, name, for_update=True)
        if doc.status != "leased" or doc.lease_device != context.device or doc.book_key not in reach:
            continue
        attempt = int(doc.attempt or 0) + 1
        if attempt >= ack_rules.MAX_ATTEMPTS:
            doc.update({"status": "manual", "lease_device": None, "lease_expires_at": None, "writing_key": None,
                        "attempt": attempt, "route_device": context.device, "reason": LOST})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        doc.update({"attempt": attempt, "fence": int(doc.fence or 0) + 1,
                    "lease_expires_at": moment + timedelta(seconds=ack_rules.LEASE_SECONDS)})
        documents.persist(doc)
        products.job_changed(view(doc.as_dict()))
        peers = devices.peers()
        return [_job(doc, reach[doc.book_key], route_contracts.Job(doc.name, doc.book_key, doc.actor,
                                                                    int(doc.payload_version or 1), doc.route_device or ""),
                     peers.get(context.device))]
    return []


def _job(doc, spelling, job, peer):
    payload = wire_location.localize(json.loads(doc.payload or "{}"), path=spelling.path, root=spelling.root,
                                     row=spelling.row, uid=spelling.uid)
    attended = route_contracts.attended(job, peer)
    return queue_contracts.job(doc.name, {"history": doc.source_name or "", "invoice_no": doc.label or None,
                                          "frozen_payload": payload}, attended)


def _busy(device, moment):
    return bool(frappe.db.sql(f"""SELECT name FROM `tab{JOB}` WHERE lease_device = %s AND status = 'leased'
        AND lease_expires_at > %s LIMIT 1""", (device, moment)))


def _heads(keys, device, peer, moment=None):
    """各份账套里**这台电脑能写的**排在最前的那件（按交进来的先后）；有一件正在写的账套不算（等它写完）。

    只在归这台写的单里取排头（录入人在这台电脑上登录过 CC，或钉在这台电脑上的单；`route_contracts.owns`），同 Cowork
    `companion.queue_heads`。验收 10-25：原来每份账套不分是谁的单只取最前一件，那件的录入人名下没有电脑时，同一份账套
    后面所有人的单都领不到（CC 一直在领、30 分钟 126 次全是空）。"""
    marks = ", ".join(["%s"] * len(keys))
    taken = {row[0] for row in frappe.db.sql(f"""SELECT DISTINCT book_key FROM `tab{JOB}`
        WHERE status = 'leased' AND book_key IN ({marks})""", tuple(keys))}
    users = sorted({user for user in peer.users or () if user})
    mine = f"(route_device = %s OR (IFNULL(route_device, '') = ''{' AND actor IN (' + ', '.join(['%s'] * len(users)) + ')' if users else ''}))"
    rows = frappe.db.sql(f"""SELECT name, actor, book_key, payload_version, route_device, reason, lease_expires_at FROM `tab{JOB}`
        WHERE status = 'queued' AND book_key IN ({marks}) AND {mine} ORDER BY creation, name LIMIT {HEAD_SCAN}""",
                         (*keys, device, *users), as_dict=True)
    heads, seen = [], set(taken)
    for row in rows:  # 每份账套取第一条（同 `route_contracts.heads`，这里的键叫 book_key）
        if row.book_key in seen or not route_contracts.owns(device, peer, row.actor, row.route_device or ""):
            continue
        seen.add(row.book_key)
        if (row.reason == ack_rules.EXPRESS_OPEN and moment is not None and row.lease_expires_at
                and row.lease_expires_at > moment):
            continue  # 这本账套的排头在等 Express 关闭、还没歇够：整本先不派（后面的单也等它），看别的账套
        heads.append(row)
        if len(heads) >= route_contracts.MAX_HEADS:
            break
    return heads


def _claim(context, request, moment):
    reach = books.writable(context.device)
    if not reach:
        return []
    peers = devices.peers()
    me = peers.get(context.device) or route_contracts.Peer()
    peers[context.device] = me._replace(online=True)
    heads = _heads(sorted(reach), context.device, me, moment)
    if not heads:
        return []
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
        return [_job(doc, reach[doc.book_key], job, peers.get(context.device))]
    return []


def lease(context, body) -> dict:
    request = queue_contracts.lease_request(body)
    del request
    context = devices.revalidate(context)
    moment = _now()
    jobs = _redeliver(context, moment)
    _recover(moment, context.device)
    devices.touch(context)
    if not jobs:
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


def _stored_meta(request):
    """回执的 meta 加上逐行回执里的存货码（CC 每行报 seq / name / stkcod，见 companion ``dbf_detail.line_mode``）。"""
    meta = dict(request["meta"] or {})
    codes = [{"seq": row["seq"], "name": str(row.get("name") or "")[:140], "code": str(row["stkcod"]).strip()[:40]}
             for row in request["line_modes"] or []
             if isinstance(row.get("seq"), int) and str(row.get("stkcod") or "").strip()]
    if codes:
        meta["line_codes"] = codes
    return meta


def _late_partial(doc, request, success) -> bool:
    """代录 held（11-14）：这件按「未完成 + 半截单号」收过回执，人在 Express 里答完框、单存完后，同一台电脑用同一件补报「已写入
    + 那个单号」。只收这一种（``common.late_partial_success``）。"""
    from pearnly_core.express_rules.push.common import late_partial_success

    try:
        stored = json.loads(doc.meta_json or "{}")
    except ValueError:
        return False
    return late_partial_success(success, doc.status, stored, request["express_docnum"])


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
        doc.status in ("queued", "manual") and doc.reason == LOST and doc.route_device == context.device) or (
        doc.last_device == context.device and _late_partial(doc, request, success))
    if not mine:
        # 不是这台电脑领的、已经交给别人的：这次回执不算（电脑丢掉它）
        raise frappe.AuthenticationError
    transition = ack_rules.acknowledgement(branch="legacy", attempt=int(doc.attempt or 0), success=success,
                                           outcome=request["outcome"], error=request["error"])
    values = {"lease_device": None, "lease_expires_at": None, "writing_key": None, "stage": transition.stage,
              "meta_json": json.dumps(_stored_meta(request), ensure_ascii=False, sort_keys=True)[:200000]}
    if transition.status == "success":
        state, verify = _verify(request["meta"])
        values.update(status="success", docnum=str(request["express_docnum"] or "")[:40], reason="",
                      verify_state=state, verify_json=verify, written_by_device=context.device,
                      written_at=_now(), route_device=None)
    elif transition.status == "pending":
        # 等账套 / 普通失败重试：没写进去（CC 桌面版失败整单回滚），谁的电脑能写都行
        values.update(status="queued", attempt=transition.attempt, reason=transition.reason or "", route_device=None)
        if transition.reason == ack_rules.EXPRESS_OPEN:
            # Express 开着这本账套：歇一会儿再派（排队中的记录借租约到期栏记「这之前不派」，同一个时钟；`_heads` 据此跳过整本）
            values["lease_expires_at"] = _now() + timedelta(seconds=ack_rules.EXPRESS_OPEN_REST_SECONDS)
    else:
        values.update(status="manual", attempt=transition.attempt, reason=transition.reason or transition.stage,
                      route_device=None)
    doc.update(values)
    documents.persist(doc)
    products.job_changed(view(doc.as_dict()))
    return _reply(doc, transition.stage)
