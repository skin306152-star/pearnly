"""Express 未收清发票的快照（``ERP Receivable Snapshot``）：只给收款流程读，Express 为准、ERP 不改发票。

经共用机器通道的按需请求 ``express_ar_open`` 请够得到这份账套的电脑读（共享种类：账套的公共资料，谁先来谁读；参数里带
``customer`` 时只读这一位客户），结果 ``{"read_at", "items": [{docnum, rectyp, docdat, customer, salesman, department, net,
received, remaining}]}`` 落进快照表（一本账套一行一张发票）。请求都带 ``offsets``（CC 桌面版 1.12.26 起另列抵减单：减项 RECTYP 5、
定金 RECTYP 0，同表同栏，`contracts.is_offset`）；够得到的电脑都还旧时退回不带的那种（只有发票，:func:`request`）。

什么时候读：
- 收款员选了客户（`service.open_invoices`）：按客户即时读一次（几十行），读好了就地落；
- Express 应收有变化（`express_marks` 结果里的 ``ar_mark`` 变了，:func:`marks_seen`，主档看标记那一轮叫）：近 30 天有人录过收款的
  账套整本读一次；
- 写进 Express 后：回执里每张发票写后的余额直接改快照（:func:`apply_balances`），再按客户读一次。

旧的不盖新的：每行记读取时刻（``read_at``），晚到的旧读取不覆盖、不删比它新的行（整本读与按客户读交错时）。删掉的发票（收清 / 作废）记一下
删的时刻（缓存 ``GONE``，两天），比它旧的读取不把它插回。发起的读取记在缓存里，
每分钟 :func:`advance` 把读好的落库（缓存丢了也不要紧：下一次同一请求号发起时，读好没取的那件先落）。
"""
import gzip
import hashlib
import json
from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from pearnly_core.cc_desktop import requests

from pearnly_erp_portal.collection import contracts

DOCTYPE = "ERP Receivable Snapshot"
COLLECTION = "ERP Collection"
KIND = "express_ar_open"
PENDING = "erp-ar-open-pending"   # 缓存：请求号 → {company, book_key, customer}
MARK = "erp-ar-mark:"            # 缓存：账套键 → 上次看到的 ar_mark
GONE = "erp-ar-gone:"            # 缓存：账套键 → {发票号: 读到它已不在（收清 / 作废）的时刻}
GONE_DAYS = 2                    # 记这么久：比它更旧的读取早就落完了
ROWS_MAX = 20000                 # 一本账套的未收清发票（几千行）
IN_USE_DAYS = 30

READY, READING, OFFLINE, OUTDATED, FAILED = "ready", "reading", "offline", "outdated", "failed"


LANDED = "erp-ar-landed:"          # 缓存：请求号 → 每分钟的 advance 刚把它落库（请求已删）；下一次同一请求号算读好了（只算一次）
LANDED_SECONDS = 120               # 落完到人点「刷新」这段时间够用；过了、或已用过一次，再点就重读
LOOKED = "erp-ar-looked:"          # 缓存：请求号 → 按单号查到的 {单号: {state, customer, remaining}}（docnums 那种读，10 分钟）
LOOKED_SECONDS = 600
DOCNUMS_MAX = 20
STATES = ("open", "settled", "cancelled")  # 电脑报的；读回里没有的单号 = 这本账套里没有这张（ABSENT）
ABSENT = "absent"


def _docnums(docnums):
    return sorted({str(value or "").strip()[:40] for value in docnums or () if str(value or "").strip()})[:DOCNUMS_MAX]


def request_key(book_key, customer="", docnums=()):
    """按客户读一次的请求号：客户编号可能是泰文、带空格（11-12 三星实测「นาย ทดสอบ」），共用通道的请求号只认字母数字，
    所以放编号的摘要；编号原样在请求参数里交给电脑（`_params`）。"""
    customer = str(customer or "").strip()
    key = "erp_ar_open:" + str(book_key)
    if customer:
        key += ":c" + hashlib.sha1(customer.encode("utf-8")).hexdigest()[:16]
    docnums = _docnums(docnums)
    if docnums:
        key += ":d" + hashlib.sha1("\n".join(docnums).encode("utf-8")).hexdigest()[:16]
    return key


def _params(book, customer="", docnums=(), offsets=True):
    params = {"account_set": book["path"], "account_root": book.get("root") or ""}
    if offsets:
        params["offsets"] = True
    if customer:
        params["customer"] = str(customer)
    if _docnums(docnums):
        params["docnums"] = _docnums(docnums)
    return params


def request(company, book, customer="", docnums=()):
    """请电脑读一次（同一请求号在读的不重发；读好了没落的就地落）。回 ``ready`` / ``reading`` / ``offline`` / ``outdated``。
    CC 桌面版还不认这种请求（服务端或电脑太旧）回 ``outdated``，不抛。``docnums``：同时按单号查这几张在账套里的状态
    （CC 桌面版 1.12.22 起；读好后 :func:`looked_up`）；够得到的电脑都还旧时回 ``outdated``，调用方改发不带单号的那种。"""
    key = request_key(book["key"], customer, docnums)
    params = _params(book, customer, docnums)
    try:
        made = requests.find(KIND, key)
        if made is not None and made["status"] == "done":
            land(company, book["key"], made, customer, key)
            return READY
        if made is not None:
            _remember(key, company, book["key"], customer)
            return READING
        if frappe.cache.get_value(LANDED + key):
            # 每分钟的 :func:`advance` 刚把这件落库并删了请求：这一次算读好了（11-20 线上缺陷⑦：没有未收发票的客户，每次「刷新」
            # 都新发一件、回「正在读取」，永远说不到「没有未收发票」）。只算一次：再问就重读，按单号核对刚开的发票不吃旧读取
            frappe.cache.delete_value(LANDED + key)
            return READY
        waiting = requests.waiting(KIND, book["key"], params=params)
        if waiting and waiting.get("reason") == "update_needed":
            # 够得到的电脑都还不认 ``offsets``（旧版 CC 桌面版）：照旧只读发票，不让收款流程卡住
            params = _params(book, customer, docnums, offsets=False)
            waiting = requests.waiting(KIND, book["key"], params=params)
        if waiting:
            return OUTDATED if waiting.get("reason") == "update_needed" else OFFLINE
        requests.create(KIND, key, params, book_key=book["key"], actor=frappe.session.user)
    except (frappe.ValidationError, KeyError, ValueError):
        # 共用通道还不认 ``express_ar_open``（另一批的改动没上）：当成要更新，不让收款流程报错
        return OUTDATED
    _remember(key, company, book["key"], customer)
    return READING


def looked_up(book_key, customer, docnums) -> dict:
    """带单号那次读回查到的状态：``{单号: {"state": open | settled | cancelled | absent, "customer", "remaining"}}``；
    还没读好（或读的电脑太旧、没按单号查）回空。"""
    found = frappe.cache.get_value(LOOKED + request_key(book_key, customer, docnums))
    if not isinstance(found, dict):
        return {}
    return {docnum: found.get(docnum) or {"state": ABSENT, "customer": "", "remaining": ""} for docnum in _docnums(docnums)}


def _looked(key, data):
    rows = data.get("looked_up")
    if not isinstance(rows, list):
        return
    found = {}
    for row in rows[:DOCNUMS_MAX]:
        if isinstance(row, dict) and str(row.get("state") or "") in STATES and str(row.get("docnum") or "").strip():
            found[str(row["docnum"]).strip()[:40]] = {"state": row["state"], "customer": str(row.get("customer") or "")[:40],
                                                      "remaining": str(row.get("remaining") or "")[:20]}
    frappe.cache.set_value(LOOKED + key, found, expires_in_sec=LOOKED_SECONDS)


def land_ready(company, book):
    """整本那次读回（不分客户）已经读好就落库，不新发请求（列发票前叫：LINE 选公司时预热的那次，`service.warm`）。"""
    try:
        made = requests.find(KIND, request_key(book["key"]))
        if made is not None and made["status"] == "done":
            land(company, book["key"], made)
    except (frappe.ValidationError, KeyError, ValueError):
        pass


def _remember(key, company, book_key, customer):
    frappe.cache.hset(PENDING, key, json.dumps({"company": company, "book_key": book_key, "customer": customer or ""}))


def _decode(content):
    raw = content or b""
    try:
        raw = gzip.decompress(raw)
    except OSError:
        pass
    data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("invalid express_ar_open result")
    return data


def land(company, book_key, made, customer="", key=None):
    """电脑读好的一份落进快照（取走即删）；带单号的那种另把查到的状态记进缓存（:func:`looked_up`）。回落了几行；
    结果坏了回 -1（记 Error Log）。``key``：这件的请求号（缺省是不带单号的那种）。"""
    key = key or request_key(book_key, customer)
    content, _meta = requests.result(made["request"])
    requests.drop(made["request"])
    frappe.cache.hdel(PENDING, key)
    try:
        data = _decode(content)
    except (ValueError, UnicodeDecodeError):
        frappe.log_error(title="ERP receivable snapshot result invalid", message=str(made.get("request")))
        return -1
    _looked(key, data)
    return replace(company, book_key, data["items"], data.get("read_at"), customer, offsets=bool(data.get("offsets")))


def _row_values(item):
    return {"docnum": str(item.get("docnum") or "").strip()[:40], "rectyp": str(item.get("rectyp") or "")[:4],
            "doc_date": str(item.get("docdat") or item.get("doc_date") or "")[:10] or None,
            "customer": str(item.get("customer") or "").strip()[:40],
            "salesman": str(item.get("salesman") or "").strip()[:20],
            "department": str(item.get("department") or "").strip()[:20],
            "net": contracts.money(item.get("net") or 0), "received": contracts.money(item.get("received") or 0),
            "remaining": contracts.money(item.get("remaining") or 0)}


def _moment(value):
    try:
        moment = get_datetime(value) if value else None
    except (TypeError, ValueError):
        moment = None
    if moment is not None and moment.tzinfo is not None:
        # 电脑报的是带时区的 UTC；站点的时刻栏与 now_datetime() 同一口径（站点时区、不带时区），换过来才比得动
        from datetime import timezone

        from frappe.utils import convert_utc_to_system_timezone

        moment = convert_utc_to_system_timezone(moment.astimezone(timezone.utc).replace(tzinfo=None)).replace(tzinfo=None)
    return moment or now_datetime()


def replace(company, book_key, items, read_at=None, customer="", offsets=True):
    """一份读取结果（整本或一位客户）对齐进快照：新的建、变的改、读到时已没有（收清 / 作废）的删；比这次读取新的行不动。
    ``offsets`` 为假（旧版 CC 桌面版只读了发票）：快照里已有的抵减单不当「已没有」删掉。"""
    at = _moment(read_at)
    filters = {"book_key": book_key, **({"customer": customer} if customer else {})}
    # 按客户读的一份只动这位客户的行：客户按原样比（库的排序规则把只差空白的两位客户当成一位，不能删了另一位的发票）
    current = {row.docnum: row for row in frappe.get_all(
        DOCTYPE, filters=filters, fields=["name", "docnum", "rectyp", "remaining", "received", "net", "read_at", "customer"],
        limit_page_length=ROWS_MAX) if not customer or row.customer == customer}
    seen, written = set(), 0
    gone = _gone(book_key)
    for item in items[:ROWS_MAX]:
        try:
            values = _row_values(item)
        except contracts.CollectionError:
            continue
        if not values["docnum"] or values["remaining"] <= 0 or (customer and values["customer"] != customer):
            continue
        seen.add(values["docnum"])
        row = current.get(values["docnum"])
        gone_at = gone.get(values["docnum"])
        if row is None and gone_at is not None and gone_at > at:
            continue  # 更新的读取已确认这张不在了（按客户读 / 写后回执删的）：旧的整本结果不插回（11-15）
        if row is None:
            frappe.get_doc({"doctype": DOCTYPE, "company": company, "book_key": book_key, "read_at": at,
                            **values}).insert(ignore_permissions=True)
            written += 1
        elif not row.read_at or get_datetime(row.read_at) <= at:
            frappe.db.set_value(DOCTYPE, row.name, {**values, "company": company, "read_at": at}, update_modified=False)
            written += 1
    for docnum, row in current.items():
        if not offsets and contracts.is_offset(row.get("rectyp")):
            continue  # 这份读回没列抵减单（旧版 CC）：不知道它在不在，留着
        if docnum not in seen and (not row.read_at or get_datetime(row.read_at) <= at):
            frappe.db.delete(DOCTYPE, {"name": row.name})
            _bury(book_key, docnum, at)
    return written


def _gone(book_key) -> dict:
    """这本账套近两天确认已不在的发票：``{发票号: 时刻}``；过期的顺手清掉。缓存丢了就是空（退回原样：CC 写前仍按
    already_collected 拒收）。"""
    out, oldest = {}, now_datetime() - timedelta(days=GONE_DAYS)
    for docnum, value in (frappe.cache.hgetall(GONE + str(book_key)) or {}).items():
        docnum = docnum.decode() if isinstance(docnum, bytes) else str(docnum)
        try:
            moment = get_datetime(value)
        except (TypeError, ValueError):
            moment = None
        if moment is None or moment < oldest:
            frappe.cache.hdel(GONE + str(book_key), docnum)
            continue
        out[docnum] = moment
    return out


def _bury(book_key, docnum, at):
    frappe.cache.hset(GONE + str(book_key), str(docnum), at.isoformat())


def apply_balances(book_key, found, at=None):
    """写进 Express 后（或电脑重读发现已被收）每张发票此刻的余额：改快照；收清的删。``found``：``{docnum: Decimal}``。"""
    at = _moment(at)
    for docnum, remaining in (found or {}).items():
        name = frappe.db.get_value(DOCTYPE, {"book_key": book_key, "docnum": docnum}, "name")
        if not name:
            continue
        if remaining <= 0:
            frappe.db.delete(DOCTYPE, {"name": name})
            _bury(book_key, docnum, at)
            continue
        net = contracts.money(frappe.db.get_value(DOCTYPE, name, "net") or 0)
        frappe.db.set_value(DOCTYPE, name, {"remaining": remaining, "received": max(contracts.ZERO, net - remaining),
                                            "read_at": at}, update_modified=False)


def rows(book_key, customer, limit=200, locked=False):
    """一位客户在这本账套此刻的未收清发票（快照），日期早的在前。客户按原样比：库的排序规则把不换行空格与空格当相等，
    只差空白的是 Express 里两位客户，不能把另一位的发票列进来。``locked``：加锁读（放行判余额，`service.release`）。"""
    fields = ["name", "customer", "docnum", "rectyp", "doc_date", "salesman", "department", "net", "received", "remaining", "read_at"]
    filters = {"book_key": book_key, "customer": customer}
    if locked:
        found = frappe.qb.get_query(DOCTYPE, fields=fields, filters=filters, order_by="doc_date asc, docnum asc", limit=limit,
                                    for_update=True).run(as_dict=True)
    else:
        found = frappe.get_all(DOCTYPE, filters=filters, fields=fields, order_by="doc_date asc, docnum asc",
                               limit_page_length=limit)
    return [row for row in found if row.customer == customer]


def lock_customer(book_key, customer):
    """放行先锁这位客户在这本账套的全部快照行（发票与抵减单，按 ``name`` 一个顺序），再锁收款记录、判余额与预留。
    两笔同一位客户的收款同时放行时在这里排队：先锁各自几张发票、再锁整位客户（或先锁自己的记录、再读别人记录的预留）
    两路各拿一半互等，实验栈同时起跑 10 轮 9 轮报 1213（11-28 退货减项 × B ②）。"""
    if not book_key or not customer:
        return
    frappe.db.sql(f"SELECT name FROM `tab{DOCTYPE}` WHERE book_key = %s AND customer = %s ORDER BY name FOR UPDATE",
                  [book_key, customer])


def lock(book_key, docnums):
    """放行时锁住这几张发票的快照行（两人同时放行同一张时排队判），回 ``{docnum: 行}``。"""
    if not docnums:
        return {}
    marks = ", ".join(["%s"] * len(docnums))
    found = frappe.db.sql(f"SELECT name, docnum, remaining FROM `tab{DOCTYPE}` WHERE book_key = %s AND docnum IN ({marks})"
                          " FOR UPDATE", [book_key, *docnums], as_dict=True)
    return {row["docnum"]: row for row in found}


def in_use(book_key) -> bool:
    """近 :data:`IN_USE_DAYS` 天这本账套有人录过收款（只给这样的账套整本刷新）。"""
    edge = now_datetime() - timedelta(days=IN_USE_DAYS)
    return bool(frappe.get_all(COLLECTION, filters={"book_key": book_key, "creation": [">=", edge]},  # bounded: 有没有一条
                               limit=1))


def marks_seen(company, book, data):
    """主档看标记那一轮（`express.masters.check_marks`）读到的结果：``ar_mark`` 和上次看到的不同、这本账套在用 → 整本刷新一次。
    旧版 CC 桌面版不报 ``ar_mark``：什么都不做。"""
    mark = str((data or {}).get("ar_mark") or "") if isinstance(data, dict) else ""
    if not mark:
        return False
    key = MARK + book["key"]
    last = frappe.cache.get_value(key)
    frappe.cache.set_value(key, mark)
    if last == mark or not in_use(book["key"]):
        return False
    request(company, book)
    return True


def advance():
    """（每分钟）发起过的读取：读好的落库，过期 / 失败的忘掉。没有发起过就一次缓存读。"""
    pending = frappe.cache.hgetall(PENDING) or {}
    for key, raw in pending.items():
        key = key.decode() if isinstance(key, bytes) else key
        try:
            item = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
        except (ValueError, AttributeError):
            frappe.cache.hdel(PENDING, key)
            continue
        made = requests.find(KIND, key)
        if made is None:
            frappe.cache.hdel(PENDING, key)
        elif made["status"] == "done":
            land(item["company"], item["book_key"], made, item.get("customer") or "", key)
            frappe.cache.set_value(LANDED + key, 1, expires_in_sec=LANDED_SECONDS)
            frappe.db.commit()
