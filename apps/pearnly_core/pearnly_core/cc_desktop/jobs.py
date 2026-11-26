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
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _

from pearnly_core.cc_desktop import books, contracts, devices, documents, products, writer_rule
from pearnly_core.cc_desktop.wire import ack_rules, queue_contracts, route_contracts
from pearnly_core.express_rules.push import wire_location

JOB = documents.JOB
OPEN = ("queued", "leased")
DONE = ("success", "manual", "cancelled")
STATUSES = (*OPEN, *DONE)
HEAD_SCAN = 500
FIELDS = ("name", "request_key", "source_doctype", "source_name", "label", "actor", "book_key", "payload_version",
          "status", "route_device", "attempt", "fence", "lease_device", "lease_expires_at", "last_device", "docnum",
          "stage", "reason", "verify_state", "verify_json", "written_by_device", "written_at", "meta_json", "writer", "creation",
          "modified")
LOST = "cc_desktop.result_unknown"
VOIDED = "voided"  # 产品说这件不要了（来源单据已作废，`products.still_wanted`），领单前撤掉；同产品作废时撤的原因
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
    from pearnly_core.express_rules.push.common import written_codes

    # 直写回执在顶层；代录回执只在 meta.operator.masters 里报随单新建的（11-16 验收，已发出去的版本也认）
    codes = written_codes(meta)
    return {"job": row.get("name"), "request_key": row.get("request_key"), "source_doctype": row.get("source_doctype"),
            "source_name": row.get("source_name"), "actor": row.get("actor"), "book_key": row.get("book_key"),
            "status": row.get("status"), "docnum": row.get("docnum") or "", "stage": row.get("stage") or "",
            "reason": row.get("reason") or "", "verify_state": row.get("verify_state") or "", "verify": verify,
            "attempt": int(row.get("attempt") or 0), "device": row.get("written_by_device") or row.get("lease_device")
            or row.get("route_device") or "", "written_at": row.get("written_at"),
            # 电脑写这张单实际用的客户 / 供应商码（新建的、复用的都报）：产品据此把单上的对方档挂上 Express 码
            "party_code": codes["party_code"],
            # 逐行实际用的存货码（直写 {seq, name, code}；代录 {name, code}，按品名对行）：产品据此把随单新建的本地商品挂上 Express 码
            "line_codes": codes["line_codes"],
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


def writers(actor, book_key, eligible=None) -> dict:
    """``actor`` 交到 ``book_key`` 的单此刻有没有电脑写：``{state, devices}``（用户 11-17 定稿，两产品同一条）。能写的电脑 =
    他自己登录过 CC 的、加上别人的电脑里有一位登录人 ``eligible(用户)`` 为真的（产品判：此刻对这家公司有权限、签发有效；
    不给 = 只算他自己的），都要够得到这份账套。state（共用 ``route_contracts.writer_status``，换成原来的名字）：``ok``（有一台在线：
    推了就写）、``offline``（都关着：排队，谁先上线谁写）、``unreachable``（有电脑、没有一台能写这本）、``missing``（他自己和能替他写
    的人一台登录过 CC 的电脑都没有，界面写「CC 桌面版未连接」）。"""
    peers = devices.peers()
    check = eligible or (lambda user: False)
    mine = {name for name, peer in peers.items() if route_contracts.serves(peer.users, actor)}
    helpers = {name for name, peer in peers.items() if name not in mine
               and any(user != actor and check(user) for user in peer.users or ())}
    reach = set((books.reachers([book_key]) or {}).get(book_key) or ()) if (mine or helpers) else set()
    able = sorted(name for name in mine | helpers if name in reach)
    status = route_contracts.writer_status(online=any(peers[name].online for name in able), writers_known=bool(able),
                                           computers=bool(mine or helpers))
    return {"state": WRITER_STATES[status], "devices": able}


WRITER_STATES = {route_contracts.WRITER_CONNECTED: "ok", route_contracts.WRITER_OFFLINE: "offline",
                 route_contracts.WRITER_NO_WRITER: "unreachable", route_contracts.WRITER_MISSING: "missing",
                 route_contracts.WRITER_NEEDS_UPDATE: "ok"}


def waiting_computer(name, moment=None):
    """这件写账此刻在等哪一台电脑（只读，给各产品显示「等待电脑 · 名称」）：排队中、钉在一台电脑上（领走后那台失联、收回时
    钉回它，:func:`_recover`；定时任务钉在读文件夹的那台），而那台此刻不在线 → 回那台的名称（连接页上显示的那个，
    ``device_label``；没有名称回空串，页面只写「等待电脑」）；没钉、那台在线、那台已移除、查不到 → ``None``（不在等哪一台）。
    11-25 两台电脑实测：钉在关着的原电脑上时，同公司另一台在线，`writers` 报 ok，页面只写「录入中」，看不出在等那台。"""
    row = frappe.db.get_value(JOB, name, ["status", "route_device"], as_dict=True) if name else None
    if not row or row.status != "queued" or not row.route_device:
        return None
    device = frappe.db.get_value(documents.DEVICE, row.route_device,
                                 ["device_label", "last_seen_at", "token_digest", "retired_at"], as_dict=True)
    if not device or device.retired_at:
        return None
    if device.token_digest and contracts.online(device.last_seen_at, moment or _now()):
        return None
    return str(device.device_label or "")


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
    # 推的人在哪段对话里推的（只在他登录过这台电脑时给，CC 桌面版 1.12.23 ⑨：一批没录成的通知点开回到那段对话；
    # 记法同 ERP 推送工具 `push_followup.note_origin("erp", "<单据类型>:<单号>")`）
    # 只是顺带的可选信息：取不到只记日志、照常交单
    try:
        from pearnly_core.assistant import push_followup

        session = push_followup.origin_session("erp", f"{doc.source_doctype}:{doc.source_name}",
                                               (peer or route_contracts.Peer()).users) if doc.source_name else ""
    except Exception:
        frappe.log_error(title="Push origin lookup failed")
        session = ""
    return queue_contracts.job(doc.name, {"history": doc.source_name or "", "invoice_no": doc.label or None,
                                          "frozen_payload": payload}, attended, session=session or None)


def _busy(device, moment):
    return bool(frappe.db.sql(f"""SELECT name FROM `tab{JOB}` WHERE lease_device = %s AND status = 'leased'
        AND lease_expires_at > %s LIMIT 1""", (device, moment)))


def _heads(keys, device, peer, moment=None, since=None):
    """各份账套里**这台电脑能写的**排在最前的那件（按交进来的先后）；有一件正在写的账套不算（等它写完）。

    只在归这台写的单里取排头（录入人在这台电脑上登录过 CC，或钉在这台电脑上的单；`route_contracts.owns`），同 Cowork
    `companion.queue_heads`；``since``（站点时区、库里排队时刻的写法，:func:`_since`）不为空时还看那之后排的别人的单（替写）。验收 10-25：原来每份账套不分是谁的单只取最前一件，那件的录入人名下没有电脑时，同一份账套
    后面所有人的单都领不到（CC 一直在领、30 分钟 126 次全是空）。"""
    marks = ", ".join(["%s"] * len(keys))
    taken = {row[0] for row in frappe.db.sql(f"""SELECT DISTINCT book_key FROM `tab{JOB}`
        WHERE status = 'leased' AND book_key IN ({marks})""", tuple(keys))}
    users = sorted({user for user in peer.users or () if user})
    mine = f"(route_device = %s OR (IFNULL(route_device, '') = ''{' AND actor IN (' + ', '.join(['%s'] * len(users)) + ')' if users else ''}))"
    args = [*keys, device, *users]
    if since is not None:
        # 这台上有人能替别人写（用户 11-17 定稿）：再看切换时刻之后排的别人的单（旧件闸，`writer_rule`）；定时任务（钉在别的电脑上的）照旧不看。
        # 切换前排的别人的件不进排头，不挡这本账套后面的新件
        mine = f"({mine} OR (IFNULL(route_device, '') = '' AND creation >= %s))"
        args.append(since)
    rows = frappe.db.sql(f"""SELECT name, actor, book_key, payload_version, route_device, reason, lease_expires_at, source_doctype,
        source_name, creation FROM `tab{JOB}`
        WHERE status = 'queued' AND book_key IN ({marks}) AND {mine} ORDER BY creation, name LIMIT {HEAD_SCAN}""",
                         tuple(args), as_dict=True)
    heads, seen = [], set(taken)
    for row in rows:  # 每份账套取第一条（同 `route_contracts.heads`，这里的键叫 book_key）
        if row.book_key in seen:
            continue
        if not route_contracts.owns(device, peer, row.actor, row.route_device or "") and not (
                since is not None and not row.route_device and _open(row.creation)):
            continue
        seen.add(row.book_key)
        if (ack_rules.rests(row.reason) and moment is not None and row.lease_expires_at
                and row.lease_expires_at > moment):
            continue  # 这本账套的排头在等账套（Express 开着 / 别的电脑在写 / 暂时够不到）、还没歇够：整本先不派（后面的单也等它），看别的账套
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
    # 这台上登录过 CC 的人里可能有人替别人写（用户 11-17 定稿，产品声明 ``stand_in``）：排头再看切换时刻之后排的别人的单
    helping = bool(me.users) and products.stand_in_enabled()
    heads = _heads(sorted(reach), context.device, me, moment, since=_since() if helping else None)
    if not heads:
        return []
    reachers = books.reachers([head.book_key for head in heads])
    for head in heads:
        job = route_contracts.Job(head.name, head.book_key, head.actor, int(head.payload_version or 1),
                                  head.route_device or "")
        # 替这件写记谁：这台的登录人里此刻能替提交人写的（产品判），添加提交人的那位排前（Frappe 原生 User.owner，主会话 11-19）；
        # 旧件闸：切换时刻之前排的件不替（领单时再核一次）
        helper = _helper(me.users, head) if helping and head.actor not in (me.users or ()) and _open(head.creation) else ""  # loop-query: 每份账套一件排头（至多 MAX_HEADS），领到一件就返回
        decision = route_contracts.decide(context.device, job, reachers.get(head.book_key) or [], peers,
                                          route_contracts.Account(), moment, stand_in=helper)
        if not decision.take and decision.reason != route_contracts.NEEDS_UPDATE:
            continue
        try:
            doc = frappe.get_doc(JOB, head.name, for_update=True)
        except frappe.QueryDeadlockError:  # conflict-ok: 领单加锁读本身的 1020，输了空手回去、下一轮再领；这一段不保存，关隔离后读到最新
            # 两台电脑同时来领同一件（替写后同一件有几台能领）：排头是旧快照读到的，另一台刚领走并提交，这里加锁读撞「记录已变」
            # （MariaDB 1020）。库已把这次领单的整个事务回滚（保存点也不在了，流程 erp.writer-at-claim 实测）：这件归那台，
            # 这次空手回去、不报错，电脑下一轮再来领（报到时刻随下一轮再记）
            frappe.db.rollback()
            return []
        if doc.status != "queued":
            continue
        if not decision.take:
            # 归这张单、够得到它的电脑没有一台支持这个载荷版本：等不来，转人工（与 Cowork 同一个原因码）
            doc.update({"status": "manual", "reason": OUTDATED, "stage": "needs_review"})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        # 提交人此刻还能交单（签发有效）；替写的由替写人签（产品已核他有权限），提交人只要账号没停用（收银日汇总由系统建、
        # 收款员不在电脑上登录 CC，用户 11-17 定稿：任何成员的单都能写）
        if not (devices.issuer_valid(doc.actor) if decision.route != route_contracts.STAND_IN else _enabled(doc.actor)):  # loop-query: 同上，只对要领的这一件
            doc.update({"status": "manual", "reason": "cc_desktop.actor_not_allowed", "stage": "needs_review"})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        if not products.still_wanted(view(doc.as_dict())):
            # 领单前兜底（11-13）：来源单据排队后作废、撤写账件那一步没接上的路径，也不交给电脑写
            doc.update({"status": "cancelled", "reason": VOIDED, "route_device": None})
            documents.persist(doc)
            products.job_changed(view(doc.as_dict()))
            continue
        point = "pcc_lease_" + uuid.uuid4().hex
        frappe.db.savepoint(point)
        try:
            # 替写的记下是谁（只作记录与排查，单上的提交人 actor 不变）；本人的电脑写的清空
            doc.update({"status": "leased", "lease_device": context.device, "last_device": context.device,
                        "lease_expires_at": moment + timedelta(seconds=ack_rules.LEASE_SECONDS),
                        "writing_key": doc.book_key, "fence": int(doc.fence or 0) + 1,
                        "writer": helper if decision.route == route_contracts.STAND_IN else None})
            documents.persist(doc)
        except frappe.UniqueValidationError:
            # 同一份账套另一件刚被别的电脑领走（写者锁）：这份账套等它写完，看下一份
            frappe.db.rollback(save_point=point)
            continue
        products.job_changed(view(doc.as_dict()))
        return [_job(doc, reach[doc.book_key], job, peers.get(context.device))]
    return []


def _local_tz():
    return ZoneInfo(frappe.utils.get_system_timezone())


def _since():
    """旧件闸（`writer_rule`）换成库里排队时刻（``creation``，站点时区）的写法：没配回最早，配了读不出回最晚（一件都不替）。"""
    moment = datetime.strptime(route_contracts.db_time(writer_rule.current()), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=timezone.utc)
    if moment.year in (1970, 9999):
        return moment.replace(tzinfo=None)
    return moment.astimezone(_local_tz()).replace(tzinfo=None)


def _open(creation) -> bool:
    """这件（``creation`` 站点时区）能不能由别人的电脑替写（共用 ``route_contracts.stand_in_open``，按 UTC 比）。"""
    if not isinstance(creation, datetime):
        return False
    at = creation.replace(tzinfo=_local_tz()) if creation.tzinfo is None else creation
    return route_contracts.stand_in_open(at, writer_rule.current())


def _enabled(user) -> bool:
    return user == "Administrator" or bool(user and frappe.db.get_value("User", user, "enabled"))


def _helper(users, head) -> str:
    """这台电脑替 ``head`` 的提交人写时记谁（共用 ``route_contracts.pick_user``）：不是提交人、产品认可此刻能替这件写的登录人；
    添加提交人的那位排前（谁建的这个用户，Frappe 原生 ``User.owner``，主会话 11-19），取不到按用户名。只在几台都在线时排先后。"""
    inviter = (frappe.db.get_value("User", head.actor, "owner") or "") if head.actor else ""
    job = {"source_doctype": head.source_doctype, "source_name": head.source_name, "actor": head.actor, "book_key": head.book_key}
    return route_contracts.pick_user(users, lambda user: user != head.actor and products.stand_in(user, job), inviter)


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
    from pearnly_core.cc_desktop import operator_controls, request_wake, rescan

    # 网页要这台整份重扫账套（`rescan`，ERP「从连接添加」的刷新）：随领单回复带下去，同 Cowork 的 master_refresh；
    # 刚建了这台够得到的按需请求（`request_wake`）：带 requests_waiting，电脑马上去领；
    # 对话里对这台留着等人的代录单做的选择（`operator_controls`，1.12.23 起）：带下去由电脑核框后点
    return queue_contracts.lease_response(jobs, rescan.command(context.device), request_wake.take(context.device),
                                          operator_controls.take(context.device, devices.version_of(context.device)))


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
        if ack_rules.rests(transition.reason):
            # 这本账套此刻写不了（Express 开着 / 别的电脑在写 / 暂时够不到，`ack_rules.REST_CODES`）：歇一会儿再派（排队中的记录借
            # 租约到期栏记「这之前不派」，同一个时钟；`_heads` 据此跳过整本）。不歇的话 CC 桌面版处理过一件就立刻再领，约 1.5 秒一次
            values["lease_expires_at"] = _now() + timedelta(seconds=ack_rules.EXPRESS_OPEN_REST_SECONDS)
    else:
        values.update(status="manual", attempt=transition.attempt, reason=transition.reason or transition.stage,
                      route_device=None)
    doc.update(values)
    documents.persist(doc)
    products.job_changed(view(doc.as_dict()))
    return _reply(doc, transition.stage)
