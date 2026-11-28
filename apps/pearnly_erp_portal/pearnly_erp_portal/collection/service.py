"""收款的服务函数：LINE（第 3 步）与 CC 工具（第 4 步）调同一组，权限、公司、账套、金额都在这里判（网页只读，见 `api`）。

身份一律是当前会话用户（LINE 里是绑定的 ERP 账号，CC 里是登录的人）。写入要 ``collection.write``（收款员岗位），只在本人能用的
公司里、只动本人的记录；看本公司全部要 ``collection.view``。

一笔收款的生命：建草稿（:func:`create_draft`，内容完整、规则全过；账套此刻按 `express.target.pick` 选定并冻结）→ 可改
（:func:`update_draft`，每改一次版本 +1）→ 出确认卡（:func:`card`，带版本号）→ 用户明确同意后放行（:func:`release`：只认卡片
展示的那个版本，对不上回 ``changed``）→ 交写账队列（`push`）→ 写入 / 失败；没被电脑领走前可撤回（:func:`withdraw`）。

规则不过抛 :class:`contracts.CollectionError`（``code`` / ``detail``），没权限抛 ``frappe.PermissionError``；调用方按码出文案。
"""
import secrets

import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate

from pearnly_core.cc_desktop import requests
from pearnly_core.express_rules.contracts import codes
from pearnly_erp_portal.access import guard
from pearnly_erp_portal.collection import contracts, snapshot, texts
from pearnly_erp_portal.collection import mrerp as ledger
from pearnly_erp_portal.express import choices, contracts as express_contracts, facts, target
from pearnly_erp_portal.masters import company as companies
from pearnly_erp_portal.masters import access as master_access

DOCTYPE = "ERP Collection"
INVOICE = "ERP Collection Invoice"
PAYMENT = "ERP Collection Payment"
COMPANY = "ERP Company"
CUSTOMER = "ERP Customer"
WRITE, VIEW = "collection.write", "collection.view"
SEARCH_MAX = 50
MINE_MAX = 100

# 规则以外、服务层自己的几个码
NO_COMPANY = "collection.company_not_linked"
NO_CUSTOMER = "collection.customer_not_found"
CC_UPDATE = "collection.cc_update_needed"
VOUCHER_INVALID = "collection.voucher_invalid"
LEDGER_NEEDED = "ledger_needed"  # CC 对话里本对话账套未定（同 `express.conversation.LEDGER_NEEDED`）
# 收款日期不在任何已连接账套的可录期间（或不在本对话账套的期间）：不拒，问收款日期（``detail.periods`` = 可选的各段，
# :func:`periods`；用户 11-15：「我只要改到账本期间就可以写帐，为何我们不可以」——同 Express 里人把工作日期改进期间再录）
DAY_NEEDED = "collection.date_needed"
BOOK_NOT_CONVERSATION = "collection.book_not_conversation"  # 草稿的账套不是本对话账套（之后在右栏换了，`plan.view`）
# 凭证原件的 id 前缀。任务书写的是 ``ERC-``，但共用原件层（`pearnly_core.originals.registered.NAME` / ``links.OBJECT`` /
# ``cloud_run.ORIGINAL``）与甲骨文上的原件服务只认 ``CWS-`` / ``ERO-``：用 ``ERC-`` 要同时改共用层并重新部署甲骨文
# claude-ocr，否则 Claude 租户的凭证存不进去。先用 ERP 原件已有的 ``ERO-``（交主会话定，换前缀只改这一行加共用层）。
VOUCHER_ID_PREFIX = "ERO-"


# ── 公司、账套、客户 ─────────────────────────────────────────────────────────


def _not_permitted():
    frappe.throw(_("Not permitted"), frappe.PermissionError)


def _company(company):
    """本人能用、接了 Express 或账本是 MR.ERP（`collection.mrerp.handles`，开关关着不查库）的公司（公司行）。"""
    from pearnly_erp_portal.express import push as express_push

    if not company or company not in companies.allowed_companies():
        _not_permitted()
    row = frappe.db.get_value(COMPANY, company, ["name", "company_name", "tax_id", "branch"], as_dict=True)
    if not row or not (express_push.linked(company) or ledger.handles(company)):
        raise contracts.CollectionError(NO_COMPANY)
    return row


def companies_for() -> list:
    """收款员能选的公司（本人可用、接了 Express、已连接账套报了会计期间）：``[{name, company_name}]``。只有一家时 LINE 跳过这一步。
    一本账套都没报期间的选了也录不了（问不出收款日期，11-15 线上「不在已连接账套的期间内」），不列出来。"""
    from pearnly_erp_portal.express import push as express_push

    guard.require(WRITE)
    out = []
    for name in companies.allowed_companies():
        if not express_push.linked(name) and not ledger.handles(name):  # loop-query: 本人可用的几家
            continue
        row = frappe.db.get_value(COMPANY, name, ["name", "company_name", "tax_id", "branch"], as_dict=True)  # loop-query: 本人可用的几家
        if row and periods(row):
            out.append({"name": name, "company_name": row.company_name or name})
    return out


def _picked(company_row, day):
    """这笔收款该写进哪本（与 ERP 单据同一条规则 `express.target.pick`）。CC 对话里（`express.conversation.wanted`）就是本对话账套，
    不按日期另判：列发票、列方式、建草稿三步同一本（A 线审计 A11，11-12）；网页、LINE 按单据日期选。"""
    from pearnly_erp_portal.express import conversation

    if ledger.handles(company_row.get("name")):
        return ledger.pick(company_row, day)  # 账本是 MR.ERP：按收款日期选这家的年度账套（同 ERP 单据）
    return target.pick(company_row, day, conversation.wanted(company_row))


def parse_day(value) -> str:
    """收款日期：``YYYY-MM-DD``，或泰国写法 ``D/M/YYYY``（年份大于 2400 当佛历减 543）；认不出回空串。"""
    import datetime as dt

    text = str(value or "").strip()
    try:
        if "/" in text:
            day, month, year = (int(part) for part in text.split("/"))
            return dt.date(year - 543 if year > 2400 else year, month, day).isoformat()
        return dt.date.fromisoformat(text[:10]).isoformat() if len(text) >= 10 else ""
    except ValueError:
        return ""


def periods(company_row, only=None) -> list:
    """这家公司已连接各本账套能录收款的日期段（本年起 ~ 次年止，与选账套、期间判据同一条 `account_match`），早的在前：
    ``[{key, label, short, start, end, last}]``；``last`` 是本年段最后一天（LINE 快捷回复给的那个日期）。``only``：只要这一本
    （CC 对话账套、草稿冻结的那本）。"""
    if ledger.handles(company_row.get("name")):
        return ledger.periods(company_row, only)
    every = [item for item in target.books_of(company_row) if only is None or item["key"] == only]
    rows = []
    for item in every:
        start = str(item.get("period_start") or "")[:10]
        end = str(item.get("next_end") or item.get("period_end") or "")[:10]
        if start and end:
            rows.append({"key": item["key"], "label": target.option_name(item), "start": start, "end": end,
                         "last": str(item.get("period_end") or end)[:10]})
    shorts = {row["code"]: row["short"] for row in _with_short(
        company_row, [{"code": row["key"], "name": row["label"]} for row in rows])}
    return sorted(({**row, "short": shorts.get(row["key"]) or row["label"]} for row in rows), key=lambda row: row["start"])


def _day_needed(company_row, day, only=None):
    found = periods(company_row, only)
    return contracts.CollectionError(DAY_NEEDED, {"company": company_row.get("name") or "", "day": str(day or "")[:10],
                                                  "periods": found}) if found else None


def book_for(company_row, day):
    """这笔收款写进哪本账套（:func:`_picked`）。没有税号 / 没有账套抛那个原因码；CC 对话里本对话账套未定抛
    :data:`LEDGER_NEEDED`（``detail`` 带可选的几本，问一次用 ``pearnly_conversation_ledger`` 设）；网页 / LINE 同期两份、没选过抛
    ``express.account_set_several``（``detail`` 带可选的几本，经 :func:`choose_book` 选一次）。"""
    from pearnly_erp_portal.express import conversation

    picked = _picked(company_row, day)
    if picked["reason"] == conversation.LEDGER_NEEDED:
        pin = conversation.pinned(company_row) or {}
        raise contracts.CollectionError(LEDGER_NEEDED, {"options": [
            {"code": item["id"], "name": item.get("label") or ""} for item in pin.get("options") or ()]})
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        raise contracts.CollectionError(picked["reason"], {"options": picked["options"]})
    if picked["reason"] in (express_contracts.NO_BOOK, express_contracts.PERIOD_OUTSIDE):
        # 这天不在可录期间：有已连接的账套就问收款日期（:data:`DAY_NEEDED`；CC 对话账套只给这一本的期间，换账套走对话的
        # 账套选择）；一本都没有才说没有账套（`texts.explain` → `target.no_book_text`）
        asked = _day_needed(company_row, day, (picked.get("book") or {}).get("key") if picked.get("book") else None)
        if asked is not None:
            raise asked
        raise contracts.CollectionError(express_contracts.NO_BOOK, {
            "company": company_row.get("name") or "", "day": str(day)[:10],
            **({"ledger": "mrerp"} if ledger.handles(company_row.get("name")) else {})})
    if picked["reason"] and picked["reason"] != express_contracts.NO_WRITER:
        raise contracts.CollectionError(picked["reason"])
    return picked["book"]  # 没有电脑能写也照样记（排队，电脑上线补写）


def day_options(company, day=None) -> dict | None:
    """开始一笔收款（网页 / LINE / 引导式）：这一天（默认今天）能录就回 None；不在任何已连接账套的期间里回
    ``{"day", "periods": [...]}``（:func:`periods`），问收款日期，选好的日期再调一次。``day`` 认不出也照这样回（再问）。
    其余停下的原因（没有账套、没有税号）照抛。"""
    guard.require(WRITE)
    row = _company(company)
    chosen = parse_day(day) if day else nowdate()
    try:
        if not chosen:
            raise _day_needed(row, day) or contracts.CollectionError(express_contracts.NO_BOOK, {
                "company": company, "day": ""})
        book_for(row, chosen)
    except contracts.CollectionError as error:
        if error.code == DAY_NEEDED:
            return {"day": chosen, "periods": error.detail["periods"]}
        if error.code != express_contracts.SEVERAL_BOOKS:
            raise
    return None


def book_options(company, day=None) -> dict | None:
    """网页 / LINE 开始一笔收款前：这家公司这一天的账套定得下来回 None；同期两份、没选过回 ``{"options": [{code, name, short}]}``
    （``short``：按钮上的短名，见 :func:`_with_short`），
    让收款员选一次（:func:`choose_book`）。其余停下的原因（没有账套、没有税号）照抛。"""
    guard.require(WRITE)
    row = _company(company)
    try:
        book_for(row, day or nowdate())
    except contracts.CollectionError as error:
        if error.code == express_contracts.SEVERAL_BOOKS:
            return {"options": _with_short(row, error.detail["options"])}
        raise
    return None


def _with_short(company_row, options) -> list:
    """可选的几本各带一个短名（按钮上用）：账套目录名（「TEST2」「TEST」）；目录名撞了再带会计年度；还撞用全名。"""
    from pearnly_core.cc_desktop import books

    tax = express_contracts.tax13(company_row.get("tax_id"))
    known = {item["key"]: item for item in (books.for_company(tax, company_row.get("branch")) if tax else [])}
    out = []
    for option in options:
        item = known.get(option["code"]) or {}
        path = str(item.get("path") or "").rstrip("\\/")
        out.append({**option, "short": path.replace("/", "\\").rsplit("\\", 1)[-1] or option["name"],
                    "year": str(item.get("period_start") or "")[:4]})
    dup = {row["short"] for row in out if [r["short"] for r in out].count(row["short"]) > 1}
    for row in out:
        if row["short"] in dup and row["year"]:
            row["short"] = f"{row['short']} {row['year']}"
    shorts = [row["short"] for row in out]
    for row in out:
        if shorts.count(row["short"]) > 1:
            row["short"] = row["name"]  # 同目录名同年度（副本与真账）：用全名（名称 · 位置），按钮上由 `line_ui.labels` 取不同的那段
    return [{key: value for key, value in row.items() if key != "year"} for row in out]


def choose_book(company, key, day=None) -> dict:
    """同期两份账套时选一份：按公司 × 会计年度记住，与 ERP 单据同一份记忆（`express.choices.ACCOUNT_SET`，网页单据上选的、
    CC 对话的默认账套都读它）。只认这一天可选的那几本；已经定下来的那一本再选一次照回。"""
    guard.require(WRITE)
    row = _company(company)
    day = str(day or nowdate())[:10]
    if ledger.handles(company):
        from pearnly_erp_portal.mrerp import catalog as mrerp_catalog

        picked = mrerp_catalog.pick(company, day)  # 同一份「同期两本选一次」记忆
    else:
        picked = target.pick(row, day)
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        found = next((item for item in picked["options"] if item["code"] == key), None)
        if found is None:
            frappe.throw(_("Invalid value"))
        choices.remember(company, choices.ACCOUNT_SET, picked["subject"], key, found["name"])
        return {"chosen": key}
    if not picked.get("book") or picked["book"]["key"] != key:
        frappe.throw(_("Invalid value"))
    return {"chosen": key}


def customers(company, q="", start=0, limit=12) -> dict:
    """找客户：这家公司从 Express 同步来的客户，按编号或名字的一部分（服务端分页，LINE 快捷回复最多 12 个）。
    ``{"items": [{code, name, tax_id}], "total"}``。收款员看所有客户（不按收款员过滤）。"""
    from pearnly_erp_portal.masters import link_search

    guard.require(WRITE)
    _company(company)
    text = str(q or "").strip()[:60]
    # 与选择框、LINE 选客户同一份查询（`masters.link_search`：名称、账本编码、税号、电话，不分大小写包含，几个词都要有，
    # 电话不论带不带横线），只要这家的账本客户
    limit = max(1, min(int(limit or 12), SEARCH_MAX))
    start = max(0, int(start or 0))
    total = link_search.count(CUSTOMER, company, text, filters={"is_active": 1}, ledger_only=True)
    if not total and text:
        return _customers_by_invoice(company, text, start, limit)
    found = link_search.rows(CUSTOMER, company, text, start=start, limit=limit, filters={"is_active": 1}, ledger_only=True)
    return {"items": [{"code": row.express_code, "name": row.title, "tax_id": row.tax_id or ""} for row in found],
            "total": total}


INVOICE_WORDS_MAX = 5


def _customers_by_invoice(company, text, start, limit) -> dict:
    """搜索字对不上任何客户时按发票号认（11-20：打字说「客户 C001 付了 IV001」，CC 开卡带的是发票号或「编号 + 发票号」）：搜索字里
    哪几个词是这家公司未收清发票的单号（快照，按单号精确对、最多几个词），回这些发票的客户。只认账本里在用的客户；单号与客户按原样比
    （库的排序规则把只差空白的当相等）。快照里没有（还没读过 / 已收清）就照旧是空。"""
    words = list(dict.fromkeys(text.split()))[:INVOICE_WORDS_MAX]
    owners = []
    for row in frappe.get_all(snapshot.DOCTYPE, filters={"company": company, "docnum": ["in", words]},
                              fields=["docnum", "customer"], order_by="doc_date asc, docnum asc", limit_page_length=SEARCH_MAX):
        if any(row.docnum.lower() == word.lower() for word in words) and row.customer and row.customer not in owners:
            owners.append(row.customer)
    if not owners:
        return {"items": [], "total": 0}
    rows = {row.express_code: row for row in frappe.get_all(
        CUSTOMER, filters={**master_access.ledger_filters(company), "is_active": 1, "express_code": ["in", owners]},
        fields=["express_code", "customer_name", "tax_id"], limit_page_length=SEARCH_MAX)}
    items = [{"code": code, "name": rows[code].customer_name, "tax_id": rows[code].tax_id or ""} for code in owners if code in rows]
    return {"items": items[start:start + limit], "total": len(items)}


def customer_code(company, given) -> str:
    """给的客户编号 → 这家公司账本客户的原样编号（`pearnly_core.assistant.codes`：CC 复述时空白会变，库的排序规则又把不换行
    空格与空格当相等，不换回原样就会把模型的串存进草稿、写 Express 时被拒）。认不出或几件只差空白的抛 :data:`NO_CUSTOMER`，
    ``detail.options`` 带像的那几件（交人选）。"""
    from pearnly_core.assistant import codes

    found = codes.canonical(CUSTOMER, "express_code", given, {**master_access.ledger_filters(company), "is_active": 1})
    if not found["code"]:
        raise contracts.CollectionError(NO_CUSTOMER, {"customer": str(given or ""), "options": found["matches"]})
    return found["code"]


def _customer_name(company, code):
    code = customer_code(company, code)
    name = frappe.db.get_value(CUSTOMER, {**master_access.ledger_filters(company), "express_code": code, "is_active": 1},
                               "customer_name")
    if name is None:
        raise contracts.CollectionError(NO_CUSTOMER, {"customer": code})
    return name


# ── 收款方式 ─────────────────────────────────────────────────────────────────


def _catalog(company, book):
    """这本账套的收款方式（`contracts.catalog`）。主档还没读过、或只有旧版读回（`facts.stale_for_collection`）就请电脑重读
    （抛 ``express.masters_reading``）；够得到这本的在线电脑都比带收款的版本旧才抛 :data:`CC_UPDATE`。账本是 MR.ERP 的按 MR.ERP
    主档列（`collection.mrerp.catalog`）。"""
    if ledger.handles(company):
        return ledger.catalog(company, book)
    data = facts.load(book["key"])
    if facts.stale_for_collection(data):
        if (requests.waiting(snapshot.KIND, book["key"]) or {}).get("reason") == "update_needed":
            raise contracts.CollectionError(CC_UPDATE)
        from pearnly_erp_portal.express import masters

        masters.ensure(company, book)  # 已经在读不重复建；读好了的在这里存成资料
        data = facts.load(book["key"])
    if facts.stale_for_collection(data):
        raise contracts.CollectionError(express_contracts.READING)
    return contracts.catalog(data["receipt_types"], data.get("cash_account") or "")


def methods(company, day=None, book=None, every=False) -> dict:
    """收款员能选的方式：``{"cash": bool, "transfers": [{prefix, code, name, label}], "discount": {…} | None}``（现金走现金那一格；
    支票、预扣、费用这类不列，规则见 `contracts`；``label`` 是银行简称 + 账号末 4 位，进同一户头的几种只列一种，`contracts.listed`）。``book``：已有的草稿冻结的那本（:func:`edit_choices`），不再按日期选。
    ``every``：进同一户头的几种都列（改草稿的弹窗要能显示草稿里已用的那种）。"""
    guard.require(WRITE)
    row = _company(company)
    found = _catalog(company, book or book_for(row, day or nowdate()))
    shape = lambda item: {"prefix": item["prefix"], "code": item["code"], "name": item["name"],  # noqa: E731
                          "label": item.get("label") or item["name"]}
    out = {"cash": found["cash"], "transfers": [shape(item) for item in (found["transfers"] if every else contracts.listed(found["transfers"]))],
           "discount": shape(found["discount"]) if found["discount"] else None}
    if ledger.handles(company):
        out["single"] = True  # MR.ERP 一笔只收一种钱（现金或一个银行），折扣另算：三处入口按它只给选一种
    return out


def menu_options(company, day=None) -> dict | None:
    """账本是 MR.ERP、收款导入菜单此刻要人选：``{"options": [{code, name}]}``（网页 / LINE / 引导问一次，:func:`choose_menu`）；
    Express 公司、不用选的回 None。"""
    guard.require(WRITE)
    if not ledger.handles(company):
        return None
    return ledger.menu_needed(company, book_for(_company(company), day or nowdate()))


def choose_menu(company, value, day=None) -> dict:
    """选一次收款导入菜单（记在公司这份账套名下，同 ERP 单据的「问一次记住」）。只认此刻可选的那几个。"""
    guard.require(WRITE)
    row = _company(company)
    if not ledger.handles(company) or not ledger.choose_menu(company, str(value or "").strip(), book_for(row, day or nowdate())):
        frappe.throw(_("Invalid value"))
    return {"chosen": str(value)}


def reading_topics(company, day=None, customer=None) -> list:
    """LINE 里「正在读取」等的那一件读好时叫醒的主题（`pearnly_core.line_ui.receipt.wake_topic`，用户 11-22）：``customer`` 给了是
    这位客户的未收清发票，否则是这本账套的主档（收款方式）。Express 经共用机器通道（``cc-request:<请求号>``，`requests.complete`
    叫醒），MR.ERP 是服务端的读取任务（`collection.mrerp.reading_topics`）。定不了账套回空（只等兜底）。"""
    try:
        book = book_for(_company(company), day or nowdate())
    except (contracts.CollectionError, frappe.ValidationError):
        return []
    if ledger.handles(company):
        return ledger.reading_topics(book, customer)
    if customer is not None:
        return ["cc-request:" + snapshot.request_key(book["key"], customer_code(company, customer))]
    return ["cc-request:" + express_contracts.MASTERS_REQUEST + book["key"]]


def reading_text(company) -> str:
    """「正在读取」按账本说：MR.ERP 的说 MR.ERP，其余照旧 Express。"""
    return _(ledger.READING_TEXT) if ledger.handles(company) else _("Reading from Express")


# ── 未收清发票与预留 ─────────────────────────────────────────────────────────


def _rows(doctype, filters, fields, limit, locked=False) -> list:
    """``locked``：加锁读（放行判预留用，:func:`release`）。关快照隔离后普通读走这笔事务早先建的读视图，看不到别人刚放行的那笔。"""
    if not locked:
        return frappe.get_all(doctype, filters=filters, fields=fields, limit_page_length=limit)
    return frappe.qb.get_query(doctype, fields=fields, filters=filters, limit=limit, for_update=True).run(as_dict=True)


def reserved(book_key, docnums, exclude=None, locked=False) -> dict:
    """别的收款记录占着的金额 ``{docnum: Decimal}``：已放行还没写完的（待写入、写入中），与已写入、但快照还没读到写后余额的
    （写入时刻晚于那张发票的读取时刻）。``exclude``：正在改的这一笔。``locked``：加锁读（:func:`_rows`）。"""
    if not docnums:
        return {}
    lines = _rows(INVOICE, {"parenttype": DOCTYPE, "docnum": ["in", list(docnums)]}, ["parent", "docnum", "amount"],
                  contracts.MAX_INVOICES * 200, locked)
    parents = {row.parent for row in lines if row.parent != exclude}
    if not parents:
        return {}
    heads = {row.name: row for row in _rows(
        DOCTYPE, {"name": ["in", list(parents)], "book_key": book_key, "status": ["in", [*contracts.RESERVING, contracts.WRITTEN]]},
        ["name", "status", "written_at"], len(parents), locked)}
    read = {row.docnum: row.read_at for row in _rows(
        snapshot.DOCTYPE, {"book_key": book_key, "docnum": ["in", list(docnums)]}, ["docnum", "read_at"], len(docnums), locked)}
    out = {}
    for row in lines:
        head = heads.get(row.parent)
        if head is None:
            continue
        if head.status == contracts.WRITTEN and (not head.written_at or not read.get(row.docnum)
                                                 or frappe.utils.get_datetime(head.written_at)
                                                 <= frappe.utils.get_datetime(read[row.docnum])):
            continue  # 快照已经是写后的余额
        out[row.docnum] = out.get(row.docnum, contracts.ZERO) + contracts.money(row.amount or 0)
    return out


def _open_rows(book_key, customer, exclude=None, locked=False) -> dict:
    found = snapshot.rows(book_key, customer, locked=locked)
    held = reserved(book_key, [row.docnum for row in found], exclude=exclude, locked=locked)
    out = {}
    for row in found:
        remaining = contracts.money(row.remaining or 0)
        out[row.docnum] = {"docnum": row.docnum, "rectyp": row.rectyp or "", "offset": contracts.is_offset(row.rectyp),
                           "doc_date": str(row.doc_date or ""), "salesman": row.salesman or "",
                           "department": row.department or "", "net": contracts.money(row.net or 0),
                           "received": contracts.money(row.received or 0), "remaining": remaining,
                           "reserved": held.get(row.docnum, contracts.ZERO),
                           "available": contracts.available(remaining, held.get(row.docnum)),
                           "read_at": row.read_at}
    return out


def warm(company, day=None) -> None:
    """预热（11-15 真机：首次读客户发票要点「刷新」，约 10 秒）：LINE 收款定下公司（和账套）时，请电脑把这本账套的未收发票整本读一次
    （已有的整本读回请求，同一请求号在读的不重发）；等人选好客户时多半已读好，列发票前落库（:func:`open_invoices`）。
    读不到照旧回「正在读取 + 刷新」。任何问题都不挡收款流程。"""
    try:
        if ledger.handles(company):
            return  # MR.ERP 没有整本读：选好客户时按客户读（`collection.mrerp.request`）
        book = book_for(_company(company), day or nowdate())
        # 只给还没有任何快照的账套整本读一次（新接的公司、第一次收款）；读过的平常由标记刷新与按客户读跟上（`snapshot.marks_seen`）
        if not frappe.db.exists(snapshot.DOCTYPE, {"book_key": book["key"]}):
            snapshot.request(company, book)
    except Exception:
        frappe.log_error(title="ERP collection warm-up skipped", message=frappe.get_traceback()[-1200:])


def open_invoices(company, customer, day=None, refresh=True, exclude=None, book=None, docnums=()) -> dict:
    """一位客户此刻能收的发票（快照减去别的记录的预留），日期早的在前：``{"items": [{docnum, doc_date, net, received,
    remaining, reserved, available, salesman, department}], "refresh": ready | reading | offline | outdated}``。
    ``refresh``：同时请电脑按这位客户即时读一次（读好前先给快照里的）；``book``：已有的草稿冻结的那本；``docnums``：这次读
    同时按单号查这几张在账套里的状态（CC 桌面版 1.12.22 起，结果多一项 ``looked_up``，见 `snapshot.looked_up`）。"""
    guard.require(WRITE)
    row = _company(company)
    customer = customer_code(company, customer)
    _customer_name(company, customer)
    book = book or book_for(row, day or nowdate())
    if ledger.handles(company):
        state = ledger.request(company, book, customer) if refresh else ""  # 后台登录 MR.ERP 按客户读
        docnums = ()  # 按单号查状态只有 CC 桌面版读 Express 时有
    else:
        # LINE 选公司时预热的整本读回（`warm`）读好了就先落，首次也能直接列出；这本账套一直在别处落（等不到串行锁）时
        # 这次就说「正在读取」，不再进 `snapshot.request` 再等一遍
        busy = snapshot.land_ready(company, book) == snapshot.READING if refresh else False
        state = (snapshot.READING if busy else snapshot.request(company, book, customer, docnums)) if refresh else ""
    rows = _open_rows(book["key"], customer, exclude=exclude)
    items = [{**value, **{key: contracts.text(value[key]) for key in ("net", "received", "remaining", "reserved",
                                                                       "available")}}
             for value in rows.values() if value["available"] > 0]
    items.sort(key=lambda item: item["offset"])  # 抵减单列在发票下面（主会话 11-28）
    for item in items:
        item.pop("read_at", None)
    found = {"items": items, "refresh": state, "book": book["key"], "customer": customer}
    if docnums:
        found["looked_up"] = snapshot.looked_up(book["key"], customer, docnums) if state == snapshot.READY else {}
    return found


# ── 草稿 ─────────────────────────────────────────────────────────────────────


def _new_name():
    for _attempt in range(8):
        name = "COL" + now_datetime().strftime("%y%m%d") + "-" + secrets.token_hex(2).upper()
        if not frappe.db.exists(DOCTYPE, name):
            return name
    frappe.throw(_("Try again"))


def _own(name, for_update=False):
    """有「收款」权限的人能动的一笔：本人能用的公司里的，不分是谁录的（用户 11-14）；别家公司的、不存在的一律当没权限。
    两个人同时改同一笔草稿由版本号拒后到的那个（:func:`update_draft` / :func:`release`）。``for_update``：先加锁、再做任何
    普通读（11-28 B ②），版本号判断按别人刚提交的值走。"""
    if for_update and name:
        frappe.db.sql(f"select name from `tab{DOCTYPE}` where name=%s for update", (name,))
    guard.require(WRITE)
    if not name or not frappe.db.exists(DOCTYPE, name):
        _not_permitted()
    doc = frappe.get_doc(DOCTYPE, name, for_update=for_update)
    if doc.company not in companies.allowed_companies():
        _not_permitted()
    return doc


def _vouchers_ok(methods_value, record=None):
    """方式里引用的凭证：本人上传的私有文件，还没挂别的单（或就挂在这一笔上）。"""
    for row in methods_value:
        file = str(row.get("voucher") or "").strip()
        if not file:
            continue
        found = frappe.db.get_value("File", file, ["owner", "attached_to_doctype", "attached_to_name", "is_private"],
                                    as_dict=True)
        if not found or found.owner != frappe.session.user or not found.is_private or (
                found.attached_to_doctype and (found.attached_to_doctype, found.attached_to_name) != (DOCTYPE, record)):
            raise contracts.CollectionError(VOUCHER_INVALID, {"voucher": file})


def _checked(company, book, customer, draft, record=None, *, require_vouchers=False, locked=False):
    open_rows = _open_rows(book["key"], customer, exclude=record, locked=locked)
    checked = contracts.check(draft, open_rows, _catalog(company, book), require_vouchers=require_vouchers)
    if ledger.handles(company):
        ledger.check_methods(checked)  # MR.ERP 收款单只有一格记收到的钱
    _vouchers_ok(checked["methods"], record)
    return checked


def _choices(book_key) -> dict:
    """这本账套能选的销售员（未停用）与部门、RE 单据簿的默认部门（主档读回的 ``salesmen`` / ``departments`` / ``receipt_run``）。
    旧版 CC 桌面版没报的项是 None（不核）。"""
    data = facts.load(book_key) or {}
    if facts.stale_for_collection(data):
        data = {}  # 旧读回的销售员 / 部门不作数（收款方式那一步已请电脑重读）
    salesmen = data.get("salesmen") if isinstance(data.get("salesmen"), list) else None
    departments = data.get("departments") if isinstance(data.get("departments"), list) else None
    run = data.get("receipt_run") if isinstance(data.get("receipt_run"), dict) else {}
    return {"salesmen": None if salesmen is None else [
                {"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                for row in salesmen if isinstance(row, dict) and row.get("code") and not row.get("inactive")],
            "departments": None if departments is None else [
                {"code": str(row.get("code") or ""), "name": str(row.get("name") or "")}
                for row in departments if isinstance(row, dict) and row.get("code")],
            "run_department": str(run.get("department") or "")}


def _staff(book_key, checked, salesman=None, department=None, previous=None) -> dict:
    """销售员与部门（`contracts.staff`）；收款员给的值要在这本账套里（停用的销售员、不存在的部门拒）。"""
    found = _choices(book_key)
    if salesman and found["salesmen"] is not None:
        salesman = codes.choose(salesman, [row["code"] for row in found["salesmen"]])
    if department and found["departments"] is not None:
        department = codes.choose(department, [row["code"] for row in found["departments"]])
    out = contracts.staff(checked["invoices"][0], found["run_department"], salesman, department, previous)
    if salesman and found["salesmen"] is not None and out["salesman"] not in {row["code"] for row in found["salesmen"]}:
        raise contracts.CollectionError(contracts.SALESMAN_INVALID, {"salesman": out["salesman"]})
    if department and found["departments"] is not None and \
            out["department"] not in {row["code"] for row in found["departments"]}:
        raise contracts.CollectionError(contracts.DEPARTMENT_INVALID, {"department": out["department"]})
    return out


def staff_choices(name) -> dict:
    """改草稿的弹窗里能选的销售员与部门（本人的记录）。"""
    doc = _own(name)
    found = _choices(doc.book_key)
    return {"salesmen": found["salesmen"] or [], "departments": found["departments"] or []}


def edit_choices(name) -> dict:
    """改草稿的弹窗（网页「收款」页）：各张此刻能收多少、能选的方式，都按这笔草稿冻结的那本账套（A11：不按草稿日期重新选，
    同期又报来一本也不变）。"""
    doc = _own(name)
    book = {"key": doc.book_key, **_book_row(doc)}
    found = open_invoices(doc.company, doc.customer, refresh=False, exclude=name, book=book)
    available = {row["docnum"]: row["available"] for row in found["items"]}
    row = frappe.db.get_value(COMPANY, doc.company, ["name", "tax_id", "branch"], as_dict=True) or {}
    return {"available": {row.docnum: available.get(row.docnum, "0.00") for row in doc.invoices},
            "methods": methods(doc.company, book=book, every=True), "periods": periods(row, doc.book_key)}


def _fill(doc, checked, people=None):
    first = checked["invoices"][0]  # 发票在前（`contracts.check`），抵减单不定销售员 / 部门
    people = people or {"salesman": first["salesman"], "department": first["department"]}
    doc.update({"salesman": people["salesman"], "department": people["department"], "cash": checked["cash"],
                "transfer": checked["transfer"], "discount": checked["discount"], "total": checked["total"],
                "voucher_count": sum(1 for row in checked["methods"] if row["voucher"]),
                **contracts.summaries(checked)})
    doc.set("invoices", [{"docnum": row["docnum"], "invoice_date": row["doc_date"] or None, "remaining": row["remaining"],
                          "amount": row["amount"], "salesman": row["salesman"], "rectyp": row.get("rectyp") or ""}
                         for row in checked["invoices"]])
    doc.set("payments", [{"prefix": row["prefix"], "code": row["code"], "type_name": row["name"], "role": row["role"],
                          "amount": row["amount"], "voucher": row["voucher"] or None} for row in checked["methods"]])


def _attach(name, checked):
    for row in checked["methods"]:
        if row["voucher"]:
            frappe.db.set_value("File", row["voucher"], {"attached_to_doctype": DOCTYPE, "attached_to_name": name},
                                update_modified=False)


def _draft_input(invoices, cash, methods_value, amount, open_rows):
    """``invoices`` 可以只给单号（``amount`` 是总数，按日期早的先收满，`contracts.allocate`），也可以逐张给金额。"""
    invoices = [row if isinstance(row, dict) else {"docnum": row} for row in invoices or []]
    # 复述时空白变了的单号换回快照里的原样（`pearnly_core.express_rules.contracts.codes`）
    invoices = [{**row, "docnum": codes.choose(row.get("docnum"), open_rows)} for row in invoices]
    if amount not in (None, "") and invoices and all(row.get("amount") in (None, "") for row in invoices):
        picked = [open_rows[row["docnum"]] for row in invoices if row.get("docnum") in open_rows]
        if len(picked) != len(invoices):
            missing = next(row.get("docnum") for row in invoices if row.get("docnum") not in open_rows)
            raise contracts.CollectionError(contracts.INVOICE_NOT_FOUND, {"docnum": missing})
        invoices = contracts.allocate(picked, amount)
    return {"invoices": invoices, "cash": cash or 0, "methods": list(methods_value or [])}


def create_draft(company, customer, invoices, cash=0, methods=None, amount=None, doc_date=None, source="line",
                 salesman=None, department=None) -> dict:
    """建一笔收款草稿（内容完整、规则全过，凭证可以之后补）。回 :func:`card`。

    ``invoices``：``[{docnum, amount}]``，或只给单号再给 ``amount``（总数，早的先收满）；``methods``：``[{prefix, amount,
    voucher?}]``（转账、折扣按前缀；``voucher`` 是 :func:`store_voucher` 回的文件）；``source``：``line`` / ``cc``；
    ``salesman`` / ``department``：不给就默认第一张发票的（`contracts.staff`）。"""
    guard.require(WRITE)
    row = _company(company)
    customer = customer_code(company, customer)
    customer_name = _customer_name(company, customer)
    day = (parse_day(doc_date) if doc_date else nowdate()) or ""
    if not day:
        raise _day_needed(row, doc_date) or contracts.CollectionError(DAY_NEEDED, {"day": "", "periods": []})
    book = book_for(row, day)
    if source not in contracts.SOURCES:
        frappe.throw(_("Invalid value"))
    draft = _draft_input(invoices, cash, methods, amount, _open_rows(book["key"], customer))
    checked = _checked(company, book, customer, draft)
    people = _staff(book["key"], checked, salesman, department)
    name = _new_name()
    doc = frappe.get_doc({"doctype": DOCTYPE, "name": name, "company": company, "doc_date": day,
                          "date_chosen": 1 if day != nowdate() else 0, "collector": frappe.session.user, "source": source, "customer": customer,
                          "customer_name": customer_name, "status": contracts.DRAFT, "draft_version": 1,
                          "book_key": book["key"], "book_label": target.option_name(book)[:140]})
    _fill(doc, checked, people)
    doc.flags.name_set = True
    doc.insert(ignore_permissions=True, set_name=name)
    _attach(name, checked)
    return card(name)


WEB_CONSENT = "web:"


def submit_web(company, customer, invoices, cash=0, methods=None, salesman=None, department=None, request="",
               doc_date=None) -> dict:
    """ERP 网页「新建」（11-14 用户：弹窗填好、提交后就写进 Express）：弹窗就是复核，提交 = 建草稿 + 放行一步，同一套规则
    （:func:`create_draft` / :func:`release`）。``request`` 是弹窗这一次打开时生成的号：同一个号再提交（连点、网络重发）回已建
    的那一笔，不建第二笔；放行的同意记为 ``web:<号>``。规则不过照码抛（同 LINE / CC），什么都不留。回放行后的 :func:`card`。"""
    guard.require(WRITE)
    request = str(request or "").strip()[:80]
    if not request:
        frappe.throw(_("Invalid request"))
    consent = WEB_CONSENT + request
    done = frappe.db.get_value(DOCTYPE, {"consent_ref": consent, "collector": frappe.session.user}, "name")
    if done:
        return card(done)
    made = create_draft(company, customer, invoices, cash=cash, methods=methods, source="web", doc_date=doc_date or None,
                        salesman=salesman or None, department=department or None)
    result = release(made["name"], made["version"], consent)
    if result["status"] == "invalid":
        raise contracts.CollectionError(result["code"], result.get("detail") or {})
    if result["status"] != "released":
        frappe.throw(_("Invalid request"))
    return card(made["name"])


def update_draft(name, version, invoices, cash=0, methods=None, amount=None, salesman=None, department=None,
                 doc_date=None) -> dict:
    """改草稿（整笔换成新内容，规则全过）：``version`` 要是本人看到的那个版本（别处刚改过回 ``changed``），改完版本 +1。
    ``salesman`` / ``department`` 不给：收款员改过的沿用，没改过的跟着第一张发票走（`contracts.staff`）。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        raise contracts.CollectionError(contracts.NOT_DRAFT, {"status": doc.status})
    if str(version) != str(int(doc.draft_version or 0)):
        raise contracts.CollectionError(contracts.CHANGED, {"version": int(doc.draft_version or 0)})
    book = {"key": doc.book_key}
    book.update(_book_row(doc))
    if doc_date:
        _redate(doc, book, doc_date)
    open_rows = _open_rows(doc.book_key, doc.customer, exclude=doc.name)
    draft = _draft_input(invoices, cash, methods, amount, open_rows)
    checked = _checked(doc.company, book, doc.customer, draft, record=doc.name)
    old_first = doc.invoices[0] if doc.invoices else None
    default = contracts.staff({"salesman": old_first.salesman if old_first else "",
                               "department": (open_rows.get(old_first.docnum) or {}).get("department", "") if old_first else ""},
                              _choices(doc.book_key)["run_department"])
    previous = {"salesman": doc.salesman or "", "department": doc.department or "",
                "default_salesman": default["salesman"], "default_department": default["department"]}
    _fill(doc, checked, _staff(doc.book_key, checked, salesman, department, previous))
    doc.draft_version = int(doc.draft_version or 0) + 1
    doc.save(ignore_permissions=True)
    _attach(doc.name, checked)
    return card(doc.name)


def _redate(doc, book, doc_date):
    """改草稿的收款日期（网页改草稿弹窗；LINE【编辑】打开的就是它）：只能在这笔冻结的那本账套的期间里（发票、方式都是那本的；
    要收别的账套的就重开一笔）。"""
    from pearnly_core.express_rules.targets import account_match

    day = parse_day(doc_date)
    if not day or account_match.conflict("express", {"attributes": book}, fields={"date": day}):
        row = frappe.db.get_value(COMPANY, doc.company, ["name", "tax_id", "branch"], as_dict=True) or {}
        raise _day_needed(row, doc_date, doc.book_key) or contracts.CollectionError(DAY_NEEDED, {"day": "", "periods": []})
    doc.doc_date, doc.date_chosen = day, 1 if day != nowdate() else 0


def _book_row(doc):
    """记录冻结的那本账套此刻的登记（位置、公司名）；电脑都不报了回只有键的一项（载荷准备时再停下）。"""
    from pearnly_core.cc_desktop import books

    if ledger.handles(doc.company):
        return ledger.book_row(doc.company, doc.book_key)  # MR.ERP 年度账套（连接、期间）
    row = frappe.db.get_value(COMPANY, doc.company, ["tax_id", "branch"], as_dict=True) or {}
    tax = express_contracts.tax13(row.get("tax_id"))
    for item in books.for_company(tax, row.get("branch")) if tax else []:
        if item["key"] == doc.book_key:
            return item
    return {"key": doc.book_key, "path": "", "root": "", "company": ""}


def store_voucher(filename, content) -> dict:
    """存一张转账凭证（私有、归本人、先不挂单；统一原件层配了就存原件层，id 前缀见 :data:`VOUCHER_ID_PREFIX`）：回 ``{"file", "file_url"}``，
    交给 :func:`create_draft` / :func:`set_voucher` 的 ``voucher``。"""
    import uuid

    from pearnly_core.originals import registered

    guard.require(WRITE)
    if not content:
        frappe.throw(_("This document has no content"))
    filename = str(filename or "voucher.jpg")[:140]
    stored = registered.put(VOUCHER_ID_PREFIX + (uuid.uuid4().hex + uuid.uuid4().hex)[:48], filename, content)
    if stored is None or isinstance(stored, dict):
        if isinstance(stored, dict):  # 原件层不收（坏图、超页数）
            frappe.throw(_("Invalid file"))
        stored = frappe.get_doc({"doctype": "File", "file_name": filename, "is_private": 1,
                                 "content": content}).insert(ignore_permissions=True)
    return {"file": stored.name, "file_url": stored.file_url}


def set_voucher(name, version, prefix, voucher) -> dict:
    """给草稿里一种转账补上凭证（版本 +1）。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        raise contracts.CollectionError(contracts.NOT_DRAFT, {"status": doc.status})
    prefix = codes.choose(prefix, [row.prefix for row in doc.payments])
    methods_value = [{"prefix": row.prefix, "amount": row.amount,
                      "voucher": voucher if row.prefix == prefix else row.voucher} for row in doc.payments]
    if not any(row.prefix == prefix for row in doc.payments):
        raise contracts.CollectionError(contracts.METHOD_INVALID, {"prefix": prefix})
    return update_draft(name, version, [{"docnum": row.docnum, "amount": row.amount} for row in doc.invoices],
                        doc.cash, methods_value)


# ── 确认卡、放行、撤回、状态 ─────────────────────────────────────────────────


def _view(doc) -> dict:
    return {"name": doc.name, "version": int(doc.draft_version or 0), "status": doc.status, "company": doc.company,
            "company_name": frappe.db.get_value(COMPANY, doc.company, "company_name") or doc.company,
            "book": doc.book_label or "", "customer": doc.customer, "customer_name": doc.customer_name,
            "doc_date": str(doc.doc_date or ""), "date_chosen": bool(doc.get("date_chosen")), "source": doc.source, "collector": doc.collector or "",
            "invoices": [{"docnum": row.docnum, "invoice_date": str(row.invoice_date or ""),
                          "remaining": contracts.text(row.remaining or 0), "amount": contracts.text(row.amount or 0),
                          "rectyp": row.get("rectyp") or "", "offset": contracts.is_offset(row.get("rectyp"))}
                         for row in doc.invoices],
            "cash": contracts.text(doc.cash or 0),
            "methods": [{"prefix": row.prefix, "name": row.type_name, "role": row.role,
                         "amount": contracts.text(row.amount or 0), "voucher": row.voucher or ""} for row in doc.payments],
            "transfer": contracts.text(doc.transfer or 0), "discount": contracts.text(doc.discount or 0),
            "total": contracts.text(doc.total or 0), "docnum": doc.docnum or "", "reason": doc.reason or "",
            "reason_text": texts.reason(doc.reason) if doc.reason else "",
            "result_unknown": contracts.result_unknown(doc.status, doc.reason),
            "retryable": contracts.retryable(doc.status, doc.reason) and not ledger.handles(doc.company),
            # 写进 MR.ERP 结果不明：人去 MR.ERP 核对后记下（「已在 MR.ERP 核对」，:func:`check_unknown`）
            "checkable": ledger.handles(doc.company) and ledger.unknown(doc),
            "salesman": doc.salesman or "", "department": doc.department or "",
            "balances": frappe.parse_json(doc.balances) if doc.balances else {},
            # 写前被电脑拒、退回草稿（带原因，改了再放行；`push.job_changed`）
            "returned": doc.status == contracts.DRAFT and bool(int(doc.get("attempt") or 0)) and bool(doc.reason),
            # 已写进 MR.ERP 后在 ERP 撤回（转人工）：在 MR.ERP 里作废要的事实（单号、收款菜单、冲过的发票与金额）
            **({"mrerp_voided": ledger.voided_facts(doc)} if ledger.voided(doc) else {}),
            **({"ledger": "mrerp"} if ledger.handles(doc.company) else {})}


def _draft_of(doc):
    return {"invoices": [{"docnum": row.docnum, "amount": row.amount} for row in doc.invoices], "cash": doc.cash,
            "methods": [{"prefix": row.prefix, "amount": row.amount, "voucher": row.voucher} for row in doc.payments]}


def card(name) -> dict:
    """确认卡要复述的一切（公司·账套·客户·各张发票收多少·方式·折扣·合计）与版本号；``ready`` 为假时 ``problem`` 说缺什么
    （例如转账还没凭证），这时不出【确认】。"""
    doc = _own(name)
    view = _view(doc)
    view.update(ready=False, problem="")
    _label_methods(doc, view)
    if doc.status == contracts.DRAFT:
        try:
            book = {"key": doc.book_key, **_book_row(doc)}
            _checked(doc.company, book, doc.customer, _draft_of(doc), record=doc.name, require_vouchers=True)
            view["ready"] = True
        except contracts.CollectionError as error:
            view.update(problem=error.code, detail=error.detail)
    return view


def _label_methods(doc, view):
    """确认卡上各转账方式写成选它时看到的名字（银行简称 + 账号末 4 位，`contracts.account_label`）；主档读不到的照旧方式名。"""
    data = facts.load(doc.book_key)
    if facts.stale_for_collection(data):
        return
    found = contracts.catalog(data["receipt_types"], data.get("cash_account") or "")
    labels = {item["prefix"]: contracts.account_label(item) for item in found["transfers"]}
    for row in view.get("methods") or []:
        if row.get("role") == contracts.TRANSFER and labels.get(row["prefix"]):
            row["label"] = labels[row["prefix"]]


def release(name, version, consent) -> dict:
    """放行（LINE【确认】/ 打「ยืนยัน / 确认 / ok」、CC「写入」共用这一条）：``version`` 是确认卡展示的版本，``consent`` 是那次
    明确同意的事件（LINE 回调的事件号、CC 的用户消息序号），存进记录备查。

    回 ``{"status": "released", "card"}``；卡片之后改过 ``{"status": "changed", "version"}``（重新出卡）；不是草稿
    ``{"status": "not_draft", "card"}``；规则此刻不过（别人刚收了同一张、凭证没补）``{"status": "invalid", "code", "detail"}``。"""
    head = frappe.db.get_value(DOCTYPE, name, ["book_key", "customer"], as_dict=True) if name else None
    if head:
        snapshot.lock_customer(head.book_key, head.customer)  # 锁的顺序：客户的快照行 → 这一笔 → 别的收款记录（预留）
    doc = _own(name, for_update=True)
    if head and (doc.book_key, doc.customer) != (head.book_key, head.customer):
        snapshot.lock_customer(doc.book_key, doc.customer)  # 读到锁之间草稿换了客户 / 账套（版本号随后会拒）
    code = contracts.release_check(doc.status, doc.draft_version, version, consent)
    if code == contracts.CHANGED:
        return {"status": "changed", "version": int(doc.draft_version or 0)}
    if code == contracts.NOT_DRAFT:
        return {"status": "not_draft", "card": _view(doc)}
    if code:
        return {"status": "invalid", "code": code, "detail": {}}
    snapshot.lock(doc.book_key, [row.docnum for row in doc.invoices])  # 两人同时放行同一张：排队判，后到的看到前一笔的预留
    book = {"key": doc.book_key, **_book_row(doc)}
    try:
        # 余额、预留按加锁读算（11-28 B ②）：读视图在拿发票锁之前就建了（核权限、读这一笔），普通读看不到前一笔刚放行的预留，
        # 两笔会把同一张发票收两遍
        checked = _checked(doc.company, book, doc.customer, _draft_of(doc), record=doc.name, require_vouchers=True, locked=True)
    except contracts.CollectionError as error:
        return {"status": "invalid", "code": error.code, "detail": error.detail}
    _fill(doc, checked, {"salesman": doc.salesman or "", "department": doc.department or contracts.staff(
        checked["invoices"][0], _choices(doc.book_key)["run_department"])["department"]})
    # 单据日期按放行当天（隔了一阵才确认、跨了日也记到确认那天，不是建草稿那天）；人选过的收款日期照选的（:data:`DAY_NEEDED`）
    doc.update({"status": contracts.QUEUED, "doc_date": doc.doc_date if doc.get("date_chosen") else nowdate(), "confirmed_at": now_datetime(),
                "consent_ref": str(consent)[:140], "reason": ""})
    doc.save(ignore_permissions=True)
    from pearnly_erp_portal.collection import push

    push.send(doc.name)
    return {"status": "released", "card": _view(frappe.get_doc(DOCTYPE, doc.name))}


BUSY = "busy"


def _retried(function, *args, busy_result=False, **kwargs):
    """LINE、CC、CC 对话引导改动收款（放行、改草稿、补凭证）用：撞上数据库冲突（1213 / 1020 / 保存前比对；同一位客户两笔同时放行、
    别笔草稿正在改时放行等）整笔回滚、退避重来（`transaction.conflicts.retried`，同网页入口 `api._fresh`）。被回滚的那次什么都
    没留下（写账件、导入任务、页面刷新通知都排在提交后），重来不重复。调用方保证此前这笔事务里没有没提交的写（重来会一起回滚）。
    用尽：``busy_result`` 为真回 ``{"status": "busy"}``（LINE 自己回话），否则抛「请稍后重试」（`conflicts.attempt` 同一句）。"""
    from pearnly_core.transaction import conflicts

    def once():
        frappe.flags.pop("pearnly_collection_live", None)  # 被回滚那次记下的「已通知刷新」不算（`push._live`）
        return function(*args, **kwargs)

    try:
        return conflicts.retried(once)
    except (conflicts.Exhausted, conflicts.RollbackFailed):
        try:
            frappe.db.rollback()
        except Exception:  # noqa: BLE001 — 回滚失败也只回同一句
            pass
        if busy_result:
            return {"status": BUSY}
        frappe.throw(_(conflicts.BUSY))


def release_retried(name, version, consent, busy_result=False) -> dict:
    """:func:`release` 经 :func:`_retried`（LINE【确认】、CC「写入」、CC 对话引导「确定」）。"""
    return _retried(release, name, version, consent, busy_result=busy_result)


def update_draft_retried(*args, **kwargs) -> dict:
    """:func:`update_draft` 经 :func:`_retried`（CC 改草稿）。"""
    return _retried(update_draft, *args, **kwargs)


def set_voucher_retried(name, version, prefix, voucher, busy_result=False) -> dict:
    """:func:`set_voucher` 经 :func:`_retried`（LINE 补凭证）。"""
    return _retried(set_voucher, name, version, prefix, voucher, busy_result=busy_result)


def withdraw(name) -> dict:
    """撤回（没被电脑领走前）：草稿、待写入、失败的可以；电脑正在写的回 ``busy``，已写入的不行（由财务在 Express 原生作废）。
    记录不删、凭证保留，标「已撤回」。"""
    doc = _own(name, for_update=True)
    if ledger.handles(doc.company) and ledger.withdraw(doc):
        return _view(doc)  # 已写进 MR.ERP：转人工、单号留着（写入中的抛 busy）
    if doc.status not in contracts.WITHDRAWABLE or contracts.retryable(doc.status, doc.reason):
        # 写进 Express 结果不明的不撤回（Express 里可能已有），只能「重试」（:func:`retry`）
        raise contracts.CollectionError(contracts.NOT_WITHDRAWABLE, {"status": doc.status})
    from pearnly_erp_portal.collection import push

    if doc.job and not push.cancellable(doc.job):
        raise contracts.CollectionError(contracts.BUSY)
    doc.update({"status": contracts.WITHDRAWN, "withdrawn_by": frappe.session.user, "withdrawn_at": now_datetime()})
    doc.save(ignore_permissions=True)
    if doc.job:
        push.cancel(doc.job)
    return _view(doc)


def retry(name) -> dict:
    """写进 Express 结果不明的再写一次（同 ERP 单据弹窗「重试」：共用写账队列 `cc_desktop.jobs.retry` 钉回原电脑再排，CC 桌面版
    写前按参照号查重，写过的回原号、一行不写；回执照常记回，`push.job_changed`）。别的状态、账本是 MR.ERP 的不行。"""
    from pearnly_erp_portal.collection import push

    doc = _own(name, for_update=True)
    if ledger.handles(doc.company) or not contracts.retryable(doc.status, doc.reason) or not push.retry(doc.job):
        raise contracts.CollectionError(contracts.NOT_RETRYABLE, {"status": doc.status})
    return _view(frappe.get_doc(DOCTYPE, name))  # 回执已在同一事务里记回（排队中）


def check_unknown(name, entered, docnum=None) -> dict:
    """写进 MR.ERP 结果不明的一笔：人在 MR.ERP 核对后记下（`collection.mrerp.resolve_unknown`）。权限同撤回；不是 MR.ERP、不是
    结果不明的拒。``entered`` 只收布尔。"""
    doc = _own(name, for_update=True)
    if not ledger.handles(doc.company) or not ledger.unknown(doc) or type(entered) is not bool:
        raise contracts.CollectionError(contracts.NOT_RETRYABLE, {"status": doc.status})
    ledger.resolve_unknown(doc, entered, docnum)
    return _view(frappe.get_doc(DOCTYPE, name))


def discard_draft(name) -> bool:
    """作废本人一张还没放行的草稿（LINE 里出了新卡、重开一笔、取消、卡片过期）：标「已撤回」，记录与凭证保留。不是草稿的不动。"""
    doc = _own(name, for_update=True)
    if doc.status != contracts.DRAFT:
        return False
    doc.update({"status": contracts.WITHDRAWN, "withdrawn_by": frappe.session.user, "withdrawn_at": now_datetime()})
    doc.save(ignore_permissions=True)
    return True


def status(name) -> dict:
    """一笔收款此刻的样子（本人的；能看收款的人看本公司的）。"""
    if not name or not frappe.db.exists(DOCTYPE, name):
        _not_permitted()
    doc = frappe.get_doc(DOCTYPE, name)
    if not can_read(doc):
        _not_permitted()
    return _view(doc)


def can_read(doc, user=None) -> bool:
    """本人能用的公司里、有「收款」权限（用户 11-14：看与录同一权限，不分谁录的）。"""
    user = user or frappe.session.user
    return doc.company in companies.allowed_companies(user) and guard.has(VIEW, user=user)


def _waiting_jobs(docs) -> set:
    """这几笔里待写入、写账件此刻在等 Express 关闭的那几件（共用通道一次查完，上次回报超过 90 秒不算；只读）。"""
    from pearnly_core.cc_desktop import jobs

    names = [doc.job for doc in docs if doc.status == contracts.QUEUED and doc.job]
    return set(jobs.express_wait_many(names)) if names else set()


def express_waiting(name) -> bool:
    """这笔待写入的收款此刻是不是在等 Express 关闭（它那本账套有写账件在等，`cc_desktop.jobs.book_express_wait`）。收款员放行后
    当场的回复用它带上现成那句 ``express.error.ACCOUNT_BUSY_LOCKED``。当天第一笔放行时电脑还没去试过，回 False。"""
    from pearnly_core.cc_desktop import jobs

    row = frappe.db.get_value(DOCTYPE, name, ["status", "book_key"], as_dict=True) if name else None
    if not row or row.status != contracts.QUEUED or not row.book_key:
        return False
    return bool(jobs.book_express_wait(row.book_key).get("waiting"))


def listed(company, start=0, limit=20, collector=None) -> dict:
    """这家公司最近的收款（新的在前，不分谁录的；``collector`` 给了只看那个人录的，用户 11-14）：``{"items": [view… +
    express_wait], "total"}``；``express_wait``：待写入、此刻在等 Express 关闭。CC 的 ``erp_collection_list`` 用（网页同一份数据，
    网页列表另走 `records` 的原生筛选）。"""
    guard.require(VIEW)
    if not company or company not in companies.allowed_companies():
        _not_permitted()
    filters = {"company": company, **({"collector": str(collector)} if collector else {})}
    names = frappe.get_all(DOCTYPE, filters=filters, pluck="name", order_by="creation desc",
                           limit_start=max(0, int(start or 0)), limit_page_length=max(1, min(int(limit or 20), MINE_MAX)))
    from pearnly_core.desk import list_filters

    docs = [frappe.get_doc(DOCTYPE, name) for name in names]
    waiting = _waiting_jobs(docs)
    return {"items": [{**_view(doc), "express_wait": bool(doc.job and doc.job in waiting)} for doc in docs],
            "total": list_filters.count(DOCTYPE, filters, ignore_permissions=True)}


__all__ = ["DAY_NEEDED", "book_for", "book_options", "can_read", "card", "choose_book", "companies_for", "create_draft", "customers", "day_options", "parse_day", "periods",
           "submit_web", "menu_options", "choose_menu", "reading_text",
           "discard_draft",
           "edit_choices", "express_waiting", "listed", "methods",
           "open_invoices", "release", "reserved", "set_voucher", "staff_choices", "status", "store_voucher", "update_draft",
           "withdraw"]
