"""账本是 MR.ERP 的公司：收款写进 MR.ERP（服务端直写，评估 2026-11-16 收款直写；11-16 测试账套真写确认的导入口径）。

收款的单据、页面、权限（一页一权限 ``collection.write``）、预留、草稿、凭证、引导一律不动，只在三处按公司账本分流
（MR.ERP 判断同 ERP 单据 `mrerp.push.handles`：总开关 :data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 关着时一律回假、不查库，
Express 那条一字不变）：

1. 选账套：按收款日期在这家公司的 MR.ERP 年度账套里选（`mrerp.catalog.pick`，与 ERP 单据同一条、同一个「同期两本选一次」记忆）；
   CC 对话里照本对话账套（`express.conversation.wanted`，MR.ERP 年度账套还没列进对话账套前回空串 → 按日期选，同 `mrerp.push`）。
2. 未收发票：后台任务登录 MR.ERP 读这位客户的选单页（`pearnly_core.mrerp.receivables`），落进同一张快照表、同一套预留锁
   （`snapshot.replace`）；状态同 Express 读回（``reading`` / ``ready`` / ``failed``）。收款方式：现金（参考科目「เงินสด」）、转账
   （这份账套科目表里的「เงินฝาก…」存款科目，与 ``bank_account`` 默认值同源）、折扣（``discount_account`` 定得下来才给，同 Express
   「折扣类型多于一种不给」）。一笔只收一种钱：MR.ERP 收款单只有「เงินสด」一格记收到的钱（零用金一格填 0）。
3. 写账：放行后排一件服务端导入（:func:`run`，经共用层 `pearnly_core.mrerp.push.receipt`，不经 CC 桌面版）：领取（行锁里从「待写入」
   改成「写入中」）→ 导入 → 读回 farev 列表认单号 → 记 MR.ERP 单号（前缀 FR…）→ 按客户重读一次未收发票。受理了却查不到 =
   结果不明（不自动重推）。同一个 MR.ERP 账号的锁、被踢标记走共用入口，被代录占着时照旧排队、每分钟（:func:`poll`）再看。

作废：没写进去的照旧撤回；已写进 MR.ERP 的撤回时转人工、留单号（同 ERP 单据「作废后写进了」那条，`express.voided_after_write`）。
"""
import json

import frappe
from frappe.utils import now_datetime

from pearnly_erp_portal import mrerp
from pearnly_erp_portal.collection import contracts

DOCTYPE = "ERP Collection"
COMPANY = "ERP Company"
SUBJECT = "receipt"  # 收款导入菜单记在公司上的那一项（`mrerp.choices`：``mrerp_menu`` / ``receipt``）
JOB_TIMEOUT_SECONDS = 600
READ_TIMEOUT_SECONDS = 240
POLL_MAX = 100
IMPORTING_MINUTES = 15
READ_KEY = "erp-mrerp-ar-read:"  # 缓存：快照请求号 → 这次按客户读的状态
READ_SECONDS = 300

# 收款这一侧的原因码（四语在 i18n/messages.tsv）
READING = "collection.mrerp_reading"          # 这份账套的主档还没从 MR.ERP 读到（已排读取）
ONE_METHOD = "collection.mrerp_one_method"    # MR.ERP 一笔收款只记一种钱（现金或一种转账）
MENU_NEEDED = "collection.mrerp_menu_needed"  # 收款导入菜单不止一个已关联日记账、还没选过
# 已写进 MR.ERP 后在 ERP 撤回：给人看的两句（收款页、CC 收款工具 / 引导卡同一组）
VOIDED_TEXT = "Written to MR.ERP: {0}. Void it in MR.ERP under {1}"
VOIDED_QUESTION = "After voiding it in MR.ERP, enter this collection again in ERP?"
READING_TEXT = "Reading from MR.ERP"


def handles(company) -> bool:
    """这家公司的账本是 MR.ERP（同 ERP 单据）。开关关着不查库。"""
    if not mrerp.enabled():
        return False
    from pearnly_erp_portal.mrerp import push as mrerp_push

    return mrerp_push.handles(company)


def _name(company_row):
    return company_row.get("name") if isinstance(company_row, dict) else company_row


# ── 选账套 ───────────────────────────────────────────────────────────────────


def wanted(company_row):
    from pearnly_erp_portal.express import conversation

    return conversation.wanted(company_row) or None


def pick(company_row, day) -> dict:
    """同 `express.target.pick` 的形状（``{book, reason, options, subject}``）。"""
    from pearnly_erp_portal.mrerp import catalog

    return catalog.pick(_name(company_row), day, wanted(company_row))


def books(company_row) -> list:
    from pearnly_erp_portal.mrerp import catalog

    return catalog.books_of(_name(company_row))


def book_row(company, key) -> dict:
    """收款记录冻结的那本此刻的登记（连接、名字、期间）；不在这家名单里回只有键的一项。"""
    found = next((item for item in books(company) if item["key"] == key), None)
    return dict(found) if found else {"key": key, "connection": "", "label": "", "period_start": "", "period_end": ""}


def periods(company_row, only=None) -> list:
    """同 `service.periods` 的形状：``[{key, label, short, start, end, last}]``，早的在前。"""
    rows = []
    for item in books(company_row):
        if only is not None and item["key"] != only:
            continue
        start, end = str(item.get("period_start") or "")[:10], str(item.get("next_end") or item.get("period_end") or "")[:10]
        if start and end:
            label = item.get("label") or item["key"]
            rows.append({"key": item["key"], "label": label, "short": label, "start": start, "end": end,
                         "last": str(item.get("period_end") or end)[:10]})
    return sorted(rows, key=lambda row: row["start"])


def no_book_text(company, day) -> str:
    from pearnly_erp_portal.mrerp import catalog

    return catalog.no_book_text(company, day)


# ── 收款方式 ─────────────────────────────────────────────────────────────────


def _views(company, book):
    from pearnly_erp_portal.mrerp import choices

    row = choices.company_row(company)
    return row, choices.views(row, book)


def _queue_masters(book):
    """这份账套的主档还没读到：排一次读取（同管理员「读取」那件，同一连接同一账套只排一件；后台读不算人点的）。"""
    from pearnly_erp_portal.mrerp import choices

    if book.get("connection") and book.get("key"):
        frappe.enqueue("pearnly_erp_portal.mrerp.choices.run_read", queue="short",
                       job_id=f"erp-mrerp-menus::{book['connection']}::{book['key']}", deduplicate=True,
                       enqueue_after_commit=True, timeout=choices.JOB_TIMEOUT_SECONDS,
                       connection=book["connection"], account_set=book["key"], manual=False)


def catalog(company, book) -> dict:
    """这份账套给收款员的收款方式，形状同 `contracts.catalog`（``{cash, transfers, discount, excluded}``），每种转账 / 折扣的
    ``prefix`` 与 ``code`` 都是 MR.ERP 科目代码。收款导入菜单要人选（读到过、不止一个已关联）抛 :data:`MENU_NEEDED`；主档没读到
    排一次读取、抛 :data:`READING`。"""
    from pearnly_core.mrerp import masters as master_rules

    _row, (menu_view, master_view) = _views(company, book)
    receipt = ((menu_view or {}).get("kinds") or {}).get(SUBJECT) or {}
    if receipt.get("needed"):
        raise contracts.CollectionError(MENU_NEEDED, {"options": [
            {"code": str(o["idmenu"]), "name": o.get("label") or ""} for o in receipt.get("options") or ()]})
    if not (master_view or {}).get("known"):
        _queue_masters(book)
        raise contracts.CollectionError(READING)
    fields = master_view.get("fields") or {}
    bank = fields.get("bank_account") or {}
    chosen = bank.get("chosen") or ""

    def method(option):
        return {"code": option["code"], "prefix": option["code"], "name": option.get("name") or option["code"],
                "name_en": "", "account": option["code"], "bank_account": None, "uses": 0}

    transfers = [method(o) for o in bank.get("options") or ()
                 if str(o.get("name") or "").startswith(master_rules.DEPOSIT_ACCOUNT_NAME) or o["code"] == chosen]
    discount = fields.get("discount_account") or {}
    found = next((o for o in discount.get("options") or () if o["code"] == discount.get("chosen")), None)
    return {"cash": True, "transfers": transfers, "discount": method(found) if found else None, "excluded": []}


def menu_needed(company, book) -> dict | None:
    """收款导入菜单此刻要不要人选（读到过、不止一个已关联日记账、还没选过或选过的没了）：要选回 ``{"options": [{code, name}]}``，
    不用选回 None（唯一一个已关联的、选过还在的、还没读到过的——导入时 MR.ERP 那边再核）。"""
    _row, (menu_view, _master_view) = _views(company, book)
    receipt = ((menu_view or {}).get("kinds") or {}).get(SUBJECT) or {}
    if not receipt.get("needed"):
        return None
    return {"options": [{"code": str(o["idmenu"]), "name": o.get("label") or ""} for o in receipt.get("options") or ()]}


def choose_menu(company, value, book) -> bool:
    """收款入口里选一次收款导入菜单：记在这家公司这份账套名下（同 ERP 单据 ``mrerp_menu``，`mrerp.choices.choose`，只收最近读到、
    已关联日记账的）。权限由调用方核（``collection.write``）。"""
    from pearnly_erp_portal.mrerp import choices

    return choices.choose(company, choices.MENU, SUBJECT, value, book) is not None


def check_methods(checked) -> None:
    """MR.ERP 一笔收款只记一种钱（现金，或一种转账）：多于一种抛 :data:`ONE_METHOD`。折扣另算（进「其他 2」）。"""
    paid = (1 if checked["cash"] > 0 else 0) + sum(1 for row in checked["methods"] if row["role"] == contracts.TRANSFER)
    if paid > 1:
        raise contracts.CollectionError(ONE_METHOD)


# ── 未收发票 ─────────────────────────────────────────────────────────────────


def _read_key(book_key, customer):
    from pearnly_erp_portal.collection import snapshot

    return READ_KEY + snapshot.request_key(book_key, customer)


def request(company, book, customer) -> str:
    """按客户读一次（后台）：读好了回 ``ready``（只回一次，下一次再发新的读）、在读回 ``reading``、上一次读失败回 ``failed``。"""
    from pearnly_erp_portal.collection import snapshot

    key = _read_key(book["key"], customer)
    found = frappe.cache.get_value(key)
    state = found.get("state") if isinstance(found, dict) else ""
    if state in (snapshot.READY, snapshot.FAILED):
        frappe.cache.delete_value(key)
        return state
    if state == snapshot.READING:
        return snapshot.READING
    frappe.cache.set_value(key, {"state": snapshot.READING}, expires_in_sec=READ_SECONDS)
    frappe.enqueue("pearnly_erp_portal.collection.mrerp.run_read", queue="short", job_id=f"erp-mrerp-ar::{key}",
                   deduplicate=True, enqueue_after_commit=True, timeout=READ_TIMEOUT_SECONDS, company=company,
                   book_key=book["key"], customer=customer)
    return snapshot.READING


def run_read(company, book_key, customer):
    """（后台任务）登录 MR.ERP 读这位客户的未收单 → 落快照。永不抛到队列。"""
    from pearnly_erp_portal.collection import snapshot

    key = _read_key(book_key, customer)
    if not mrerp.enabled():
        return
    state = snapshot.FAILED
    try:
        state = _read(company, book_key, customer)
    except Exception:  # noqa: BLE001 — 读不到只记日志，收款流程照旧给上次的快照
        frappe.db.rollback()
        frappe.log_error(title="ERP MR.ERP receivables read failed")
    frappe.cache.set_value(key, {"state": state}, expires_in_sec=READ_SECONDS)


def _read(company, book_key, customer) -> str:
    from pearnly_core.mrerp import receivables
    from pearnly_core.mrerp.session import lock_key
    from pearnly_core.session_lock import operator_holds

    from pearnly_erp_portal.collection import snapshot
    from pearnly_erp_portal.mrerp import choices, connection as connections

    book = book_row(company, book_key)
    lease = connections.lease(book.get("connection"))
    frappe.db.commit()
    if not lease.get("username") or not lease.get("password") or \
            operator_holds(lock_key(lease["system_url"], lease["username"])):
        return snapshot.FAILED
    menu = choices.chosen(choices.company_row(company), book)[0].get(SUBJECT) or ""
    found = receivables.read(lease, book_key, customer, menu=menu)
    if found.get("menus"):
        choices.observe(book["connection"], book_key, found["menus"], None)
        frappe.db.commit()
    if found.get("items") is None:
        return snapshot.FAILED
    snapshot.replace(company, book_key, [{**item, "customer": customer} for item in found["items"]], None, customer)
    frappe.db.commit()
    return snapshot.READY


# ── 写账 ─────────────────────────────────────────────────────────────────────


def send(name):
    """（放行的事务里，`push.send` 交过来）排一件服务端导入（提交之后跑）。"""
    from pearnly_erp_portal.collection import push

    push._set(name, {"reason": ""})
    dispatch(name)
    return {"status": "sent"}


def dispatch(name):
    frappe.enqueue("pearnly_erp_portal.collection.mrerp.run", queue="short", job_id=f"erp-mrerp-receipt::{name}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, name=name)


def _claim(name):
    """行锁里从「待写入」领成「写入中」并提交（修改时刻记领取时刻，:func:`poll` 判进程死掉用）；领不到回 None。"""
    row = frappe.db.get_value(DOCTYPE, name, ["name", "status", "company", "job"], as_dict=True, for_update=True)
    if not row or row.status != contracts.QUEUED or row.job or not handles(row.company):
        frappe.db.rollback()
        return None
    from pearnly_erp_portal.mrerp import contracts as mrerp_contracts

    frappe.db.set_value(DOCTYPE, name, {"status": contracts.WRITING, "reason": mrerp_contracts.IMPORTING})
    frappe.db.commit()
    return row


def prepare(doc) -> dict:
    """一笔已放行的收款 → ``{"payload", "book"}``（`pearnly_core.mrerp.push.receipt` 读的形状），或 ``{"failed": 原因}``。不写记录。"""
    from pearnly_erp_portal.express import contracts as express_contracts
    from pearnly_erp_portal.mrerp import choices

    book = book_row(doc.company, doc.book_key)
    if not book.get("connection"):
        return {"failed": express_contracts.NO_BOOK}
    menus, defaults = choices.chosen(choices.company_row(doc.company), book)
    paid = [row for row in doc.payments if row.role == contracts.TRANSFER]
    cash = contracts.money(doc.cash or 0)
    if (cash > 0 and paid) or len(paid) > 1 or (cash <= 0 and not paid):
        return {"failed": ONE_METHOD}
    money = {"kind": "cash", "account": "", "amount": contracts.text(cash)} if cash > 0 else \
        {"kind": "transfer", "account": paid[0].code, "amount": contracts.text(paid[0].amount)}
    discount = next((row for row in doc.payments if row.role == contracts.DISCOUNT), None)
    payload = {"number": doc.name, "date": str(doc.doc_date or "")[:10], "customer": doc.customer,
               "salesman": doc.salesman or "", "department": doc.department or "", "money": money,
               "discount": {"amount": contracts.text(discount.amount) if discount else "0.00",
                            "account": discount.code if discount else ""},
               "bills": [{"docnum": row.docnum, "amount": contracts.text(row.amount)} for row in doc.invoices],
               # 第 20 列（备注 3）必须有值：记 ERP 收款记录号（同 Express RE 的 YOUREF）
               "notes": ["", "", doc.name], "menu": menus.get(SUBJECT) or "", "defaults": defaults}
    return {"payload": json.loads(json.dumps(payload, ensure_ascii=False, default=str)), "book": book}


MENU_CODES = ("ERR_MRERP_MENU_CHOICE_REQUIRED", "ERR_MRERP_MENU_CHANGED")


def settle(outcome) -> dict:
    """导入结论 → 收款记录的值：成功 = 已写入 + MR.ERP 单号（列表金额核不上照记已写入、原因记 ``ERR_MRERP_AMOUNT_MISMATCH``）；
    结果不明 = 失败 + ``mrerp.result_unknown: …``（不自动重推）；收款菜单要人选 = 失败 + :data:`MENU_NEEDED`；其余失败照原因。"""
    from pearnly_core.mrerp.push import readback

    from pearnly_erp_portal.mrerp import contracts as mrerp_contracts

    status, reason = outcome.get("status"), str(outcome.get("reason") or "")
    if status == "success":
        verify = ((outcome.get("meta") or {}).get("verify") or {}).get("state")
        docnum = str(outcome.get("docnum") or "")[:40]
        return {"status": contracts.WRITTEN, "docnum": docnum, "written_at": now_datetime(),
                "reason": f"{readback.AMOUNT_MISMATCH}:{docnum}" if verify == "mismatch" else ""}
    if status == "unknown":
        return {"status": contracts.FAILED, "reason": f"{mrerp_contracts.UNKNOWN}: {reason}"[:500]}
    if any(code in reason for code in MENU_CODES):
        return {"status": contracts.FAILED, "reason": MENU_NEEDED}
    return {"status": contracts.FAILED, "reason": reason[:500] or "ERR_UNKNOWN_UPLOAD_OUTCOME"}


def run(name):
    """（后台任务）领一笔、准备、导入（不持行锁）、记结论、写成后按客户重读未收发票、告诉录入的人。重复排队无副作用。"""
    if not mrerp.enabled():
        return
    from pearnly_core.mrerp.session import lock_key
    from pearnly_core.session_lock import operator_holds

    from pearnly_erp_portal.collection import push
    from pearnly_erp_portal.mrerp import choices, connection as connections, contracts as mrerp_contracts

    row = _claim(name)
    if row is None:
        return
    doc = frappe.get_doc(DOCTYPE, name)
    prepared = prepare(doc)
    if "failed" in prepared:
        push._set(name, {"status": contracts.FAILED, "reason": prepared["failed"]})
        frappe.db.commit()
        push._tell(doc)
        return
    book = prepared["book"]
    lease = connections.lease(book["connection"])
    if not lease.get("username") or not lease.get("password"):
        push._set(name, {"status": contracts.FAILED, "reason": mrerp_contracts.NO_LOGIN})
        frappe.db.commit()
        push._tell(doc)
        return
    if operator_holds(lock_key(lease["system_url"], lease["username"])):
        # 这个 MR.ERP 账号正被代录占着：照旧待写入，每分钟再看（不算失败）
        push._set(name, {"status": contracts.QUEUED, "reason": mrerp_contracts.ACCOUNT_BUSY})
        frappe.db.commit()
        return
    from pearnly_core.mrerp.push.receipt import execute  # 连网的共用层只在导入任务里载入（同 `mrerp.push.run`）

    outcome = execute(prepared["payload"], {"account_set": book["key"]},
                      {"username": lease["username"], "password": lease["password"]}, lease["system_url"])
    try:
        choices.observe(book["connection"], book["key"], outcome.get("menus"), outcome.get("masters") or None)
        frappe.db.commit()
    except Exception:  # noqa: BLE001 — 只是给选择用的缓存，存不了不影响导入结论
        frappe.db.rollback()
    values = settle(outcome)
    push._set(name, values)
    frappe.db.commit()
    if values["status"] == contracts.WRITTEN:
        request(doc.company, book, doc.customer)  # 写后余额：按这位客户再读一次未收发票
        frappe.db.commit()
    push._tell(doc)


def withdraw(doc) -> dict | None:
    """撤回的 MR.ERP 一半（`service.withdraw` 先问）：写入中的回 ``busy``（抛 `contracts.BUSY`）；已写进 MR.ERP 的不撤导入（MR.ERP
    没有删除导入），标「已撤回」、转人工、单号留着（``express.voided_after_write``）回 True；其余照通常的撤回回 None。"""
    from pearnly_erp_portal.express import contracts as express_contracts

    if doc.status == contracts.WRITING:
        raise contracts.CollectionError(contracts.BUSY)
    if doc.status == contracts.WRITTEN:
        doc.update({"status": contracts.WITHDRAWN, "reason": express_contracts.VOIDED_AFTER_WRITE,
                    "withdrawn_by": frappe.session.user, "withdrawn_at": now_datetime()})
        doc.save(ignore_permissions=True)
        return True
    return None


def voided(doc) -> bool:
    """这笔是不是「已写进 MR.ERP 后在 ERP 撤回」（:func:`withdraw` 记的；只有 MR.ERP 这条会记这个原因，不查库）。"""
    from pearnly_erp_portal.express import contracts as express_contracts

    return doc.get("status") == contracts.WITHDRAWN and doc.get("reason") == express_contracts.VOIDED_AFTER_WRITE \
        and bool(doc.get("docnum"))


def menu_label(company, book_key) -> str:
    """写这笔时用的收款导入菜单名：这份账套选过的那个，没选过就是读到的唯一一个已关联日记账的（导入时同样这么定，
    `pearnly_core.mrerp.push.receipt`）；都没有回空串。"""
    from pearnly_core.mrerp import menus as menu_rules

    _row, (menu_view, _master_view) = _views(company, book_row(company, book_key))
    receipt = ((menu_view or {}).get("kinds") or {}).get(SUBJECT) or {}
    chosen = str(receipt.get("chosen") or "")
    option = next((o for o in receipt.get("options") or () if str(o.get("idmenu")) == chosen), None)
    if option is None:
        choices_row = _row.get("mrerp_menus") if _row else None
        picked = (menu_rules.choices(choices_row).get(str(book_key)) or {}).get(SUBJECT) or {}
        return str(picked.get("label") or "")
    return str(option.get("label") or "")


def voided_facts(doc) -> dict:
    """在 MR.ERP 里作废这笔要的事实（收款页、CC 收款工具同一组）：``{docnum, menu, invoices: [{docnum, amount}], total}``——
    MR.ERP 单号（FR…）、所选收款导入菜单的名字、这笔冲过的各张发票与金额。"""
    try:
        menu = menu_label(doc.company, doc.book_key)
    except Exception:  # noqa: BLE001 — 菜单名读不到不挡页面
        menu = ""
    from frappe import _

    return {"docnum": doc.docnum or "", "menu": menu,
            "invoices": [{"docnum": row.docnum, "amount": contracts.text(row.amount or 0)} for row in doc.invoices],
            "total": contracts.text(doc.total or 0),
            "text": _(VOIDED_TEXT).format(doc.docnum or "", menu), "question": _(VOIDED_QUESTION)}


def poll():
    """（每分钟）等账号的、没排上的待写入再排；「写入中」超过 15 分钟的（进程死了）转结果不明。开关关着什么都不做。"""
    if not mrerp.enabled():
        return
    from datetime import timedelta

    from pearnly_erp_portal.collection import push
    from pearnly_erp_portal.mrerp import contracts as mrerp_contracts

    stale = now_datetime() - timedelta(minutes=IMPORTING_MINUTES)
    rows = frappe.get_all(DOCTYPE, filters={"status": ["in", [contracts.QUEUED, contracts.WRITING]],
                                            "job": ["is", "not set"]},
                          fields=["name", "status", "company", "modified"], order_by="modified asc",
                          limit_page_length=POLL_MAX)
    for row in rows:
        if not handles(row.company):  # loop-query: 每分钟至多 POLL_MAX 笔待写的收款，公司一般一两家
            continue
        if row.status == contracts.WRITING:
            if row.modified and frappe.utils.get_datetime(row.modified) < stale:
                push._set(row.name, {"status": contracts.FAILED,  # loop-query: 进程死掉的写入中，极少，至多 POLL_MAX 笔
                                     "reason": f"{mrerp_contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED"})
        else:
            dispatch(row.name)
    frappe.db.commit()


__all__ = ["MENU_NEEDED", "READING_TEXT", "VOIDED_QUESTION", "VOIDED_TEXT", "choose_menu", "menu_needed", "ONE_METHOD", "READING", "book_row", "books", "catalog", "check_methods", "dispatch",
           "handles", "no_book_text", "periods", "pick", "poll", "prepare", "request", "run", "run_read", "send",
           "settle", "voided", "voided_facts", "wanted", "withdraw"]
