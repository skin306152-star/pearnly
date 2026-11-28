"""ERP 采购 / 销售单导入直写进 MR.ERP（评估 §13 第 6 步）：公司账本是 MR.ERP 的，提交时排上（:func:`queue`），提交后在后台
准备规范载荷、经共用层导入 5 步 + 列表回读（`pearnly_core.mrerp.push.execute`，与 Cowork 同一份），结论记回单据上的通用账本栏
（``ledger_state`` / ``ledger_docnum`` / ``ledger_reason`` / ``ledger_needed``，``ledger_target = mrerp``）。

- 只在 :data:`pearnly_erp_portal.mrerp.MRERP_LEDGER_ENABLED` 开着时生效（:func:`handles` 关着时直接回假，不查库）；Express 那条
  （`express.push`）在入口处先问 :func:`handles`，关着时一字不变。
- 哪些单：采购单（真进货 / 费用）与销售单（同 Express 的种类），已提交、在公司选 MR.ERP 当账本之后开始的。已付的采购按现购写
  （共用层第 9 列现金供应商、第 10 列真实卖家，同 Cowork）。出入库、收银日汇总、盘点 MR.ERP 没有对应的导入，不排。
- 写进哪本：一家公司可挂多本年度账套，按单据日期选（`mrerp.catalog.pick`，与 Express `target.pick` 同一条规则、同一个「同期两本选
  一次」记忆）；选中的那本记在单据上（``ledger_book``），导入、选项、写后核对都用它。没有覆盖单据日期的停下（``NO_BOOK``），同期两本
  没选过停下让人选一次（``choice_needed:mrerp`` + 账套那一项）。
- 要人选的导入菜单 / 主档默认值：读到过、此刻要人选的，导入前就停在单据上（``choice_needed:mrerp`` + ``ledger_needed``）；没读到过的
  照导，MR.ERP 那边按读到的核对，要选就停下（读到的选项存回连接）。选一次（:func:`answer`）记在公司上后再排。
- 同账号互斥（ADR-026 第 8 条）：导入拿共用层的账号短锁（`session_lock.account_lock`，导入 5 步里拿）；被 CC 桌面版代录占着
  （`operator_holds`）时不导、照旧排队（``mrerp.account_busy``），每分钟（:func:`poll`）再看，不算失败。
- 幂等：只从「排队中」领（行锁里改成「正在导入」并提交）；导入发出之后出错、受理了却查不到 = 结果不明（停下、不自动重推）；
  进程死掉的「正在导入」超过 15 分钟转结果不明。同一张单的 MR.ERP 单号固定（`contracts.history_id`），重推遇「单号已存在」回读认领。
- 推送不查重、不拦（用户 10-08）。
"""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe.utils import now_datetime

from pearnly_core.transaction import conflicts

from pearnly_erp_portal import mrerp
from pearnly_erp_portal.express import contracts as express_contracts, pending, printed
from pearnly_erp_portal.mrerp import choices, connection as connections, contracts

SALES = "ERP Sales Document"
PURCHASE = "Purchase Document"
STOCK = "ERP Stock Entry"  # 库存单（出库 / 入库 / 调拨 / 调整，`mrerp.stock`，11-19）
INVOICES = {SALES: "sales", PURCHASE: "purchase"}  # 发票类（CC 预选卡、对话直写按发票的判法）
DIRECTIONS = {**INVOICES, STOCK: "stock"}
KINDS = {SALES: express_contracts.SALES_KINDS, PURCHASE: express_contracts.PURCHASE_KINDS}
JOB_TIMEOUT_SECONDS = 600
POLL_MAX = 100


def handles(company) -> bool:
    """这家公司的账本是 MR.ERP（功能开着、公司选了 MR.ERP 账套）。关着时不查库。"""
    if not mrerp.enabled() or not company:
        return False
    row = frappe.db.get_value("ERP Company", company, ["mrerp_enabled", "express_enabled"], as_dict=True)
    return bool(row and row.mrerp_enabled and not row.express_enabled)


def _row(doctype, name, fields, for_update=False):
    """读单据的几栏；库存单没有 ``company`` / ``doc_date`` 两栏，按单头仓库的公司与过账日期补上（`mrerp.stock`）。"""
    own = list(dict.fromkeys(["name", *(field for field in fields
                                         if not (doctype == STOCK and field in ("company", "doc_date")))]))
    row = frappe.db.get_value(doctype, name, own, as_dict=True, for_update=for_update) if name else None
    if row and doctype == STOCK:
        from pearnly_erp_portal.mrerp import stock  # 库存那一侧在用到时导入（它经 `express.stock_entry` 用到 express 一侧）

        if "company" in fields:
            row["company"] = stock.company_of(name)
        if "doc_date" in fields:
            row["doc_date"] = stock.date_of(name)
    return row


def _eligible(doctype, doc) -> bool:
    if doctype == STOCK:
        from pearnly_erp_portal.mrerp import stock  # 同 :func:`_row`

        return stock.eligible(doc)
    if doctype not in DIRECTIONS or doc.get("status") != "submitted" or (doc.get("doc_kind") or "") not in KINDS[doctype]:
        return False
    since = frappe.db.get_value("ERP Company", doc.company, "mrerp_linked_at")
    created = frappe.db.get_value(doctype, doc.name, "creation")
    return bool(since and created and frappe.utils.get_datetime(created) >= frappe.utils.get_datetime(since))


def queue(doctype, doc):
    """（`express.push.queue` 交过来：提交事务里）该导入就标「排队中」、记账本 MR.ERP，排提交后的导入；不该什么都不做。"""
    if doc.ledger_state or not handles(doc.company) or not _eligible(doctype, doc):
        return
    _set(doctype, doc.name, {"ledger_state": express_contracts.QUEUED, "ledger_reason": "",
                             "ledger_target": contracts.LEDGER_MRERP})
    dispatch(doctype, doc.name)


def dispatch(doctype, name):
    # 任务入口在 `express.push.mrerp_run`：它把建单据的函数（`express.push.document`）交给 :func:`run`，本域不反向导入 express.push
    frappe.enqueue("pearnly_erp_portal.express.push.mrerp_run", queue="short", job_id=f"erp-mrerp-push::{doctype}::{name}",
                   deduplicate=True, enqueue_after_commit=True, timeout=JOB_TIMEOUT_SECONDS, doctype=doctype, name=name)


def _set(doctype, name, values):
    """写单据上的通用账本栏（不改修改时间）；改到状态或原因时单据进出「待处理」就推一次，LINE 上有人在等结果就叫醒（同
    `express.push._set`）。"""
    before = pending.ledger_of(doctype, name) if {"ledger_state", "ledger_reason"} & set(values) else None
    frappe.db.set_value(doctype, name, values, update_modified=False)
    if before is not None:
        pending.notify(doctype, name, before)
    pending.line_changed(doctype, name, values)


def _wanted(row):
    """交给 `catalog.pick` 的 ``wanted``（None 照单据日期选；账套键）。MR.ERP 的年度账套还没列进 CC 对话账套（公司接入第 3 批），
    MR.ERP 公司在对话里会回空串（「对话账套 0 本」= 未定）——列进去之前按 None：照单据日期选，不卡在「对话账套未定」。"""
    from pearnly_erp_portal.express import conversation  # 同 :func:`prepare`

    return conversation.wanted(row) or None


def prepare(doctype, name, document) -> dict:
    """准备一张：``{"payload", "company", "direction"}``，或 ``{"hold": 原因, "needed": [...]}`` / ``{"failed": 原因}``。不写单据。
    ``document``：建单据的函数（`express.push.document`，同 Express 交给写账规则的那个形状）。"""
    company = (_row(doctype, name, ["company"]) or {}).get("company")
    row = choices.company_row(company)
    if not row or not row.mrerp_enabled:
        return {"failed": contracts.NOT_CONFIGURED}
    if doctype == STOCK:
        from pearnly_erp_portal.mrerp import stock  # 同 :func:`_row`
    held = printed.hold(doctype, name) if doctype != STOCK else None  # 识别来的单票面合计没有 / 对不上：同 Express 直写停下
    if held:
        return {"hold": held}
    body = document(doctype, name)
    # 选账套与「同期两本选一次」的记忆在 express / 公司接入一侧（它们经 `express.push` 反向用到本模块）：用到时再导入
    from pearnly_erp_portal.express import choices as express_choices
    from pearnly_erp_portal.mrerp import catalog

    picked = catalog.pick(row.name, body["date"], _wanted(row))
    if picked["reason"] == express_contracts.SEVERAL_BOOKS:
        return {"hold": contracts.CHOICE, "needed": [{
            "kind": express_choices.ACCOUNT_SET, "subject": picked["subject"], "label": row.company_name or row.name,
            "options": picked["options"], "current": ""}]}
    if picked["reason"]:
        return {"hold": picked["reason"]}
    book = picked["book"]
    if doctype == STOCK:
        return stock.prepare(name, body, row, book)
    menu_view, master_view = choices.views(row, book)
    needed = contracts.needed(menu_view, master_view, body["direction"])
    if needed:
        return {"hold": contracts.CHOICE, "needed": needed, "book": book}
    party_field = "supplier" if body["direction"] == "purchase" else "customer"
    found = frappe.db.get_value(doctype, name, ["doc_kind", "payment_method", party_field], as_dict=True) or {}
    flat = contracts.history(body, company_name=row.company_name, doc_kind=found.get("doc_kind") or "",
                             payment_method=found.get("payment_method") or "", party_id=found.get(party_field) or "")
    menus, defaults = choices.chosen(row, book)
    return {"payload": contracts.payload(flat, own_tax_id=body["company"]["tax_id"], menus=menus, defaults=defaults,
                                         products=contracts.products(body, _ledger_codes(row.name, body))),
            "company": row, "direction": body["direction"], "book": book}


def _ledger_codes(company, body) -> set:
    """这张单明细里的代码中，属于这家公司账本是 MR.ERP 的物料（从 MR.ERP 读来的档）的那些；从 Express 同步来的旧档不算。"""
    codes = sorted({str(item.get("code") or "").strip() for item in body.get("items") or ()} - {""})
    if not codes:
        return set()
    from pearnly_erp_portal.masters import access  # 主档归属口径唯一的家

    filters = {**access.ledger_filters(company), "ledger": contracts.LEDGER_MRERP, "express_code": ["in", codes]}
    return set(frappe.get_all("ERP Product", filters=filters, pluck="express_code", limit_page_length=len(codes) * 4))


def picked(doctype, name) -> dict:
    """这张单写进哪本（CC 预选卡用，同 Express 预检 `_book_of` 的取法）：CC 对话 / 代录里是本对话账套；否则单据上记过的
    （``ledger_book``）；都没有按单据日期选。回 `mrerp.catalog.pick` 的结论（``{book, reason, options, subject}``）。"""
    row = _row(doctype, name, ["company", "doc_date", "ledger_book"]) if doctype in DIRECTIONS else None
    if not row:
        return {"book": None, "reason": contracts.NOT_CONFIGURED, "options": [], "subject": ""}
    return picked_for(row.company, str(row.doc_date or "")[:10], row.ledger_book)


def picked_for(company, day, ledger_book=None) -> dict:
    """同 :func:`picked`，给还没存成单据的（LINE 收单记录按票面日期，用户 11-19 出卡前问完）：本对话账套 → 记过的 → 按日期选。"""
    from pearnly_erp_portal.mrerp import catalog  # 同 :func:`prepare`

    # 都没有给 None（照单据日期选），不给空串（`catalog.pick` 把空串当「对话账套未定」）
    wanted = _wanted(choices.company_row(company)) or ledger_book or None
    return catalog.pick(company, day, wanted)


def send(doctype, name, manual=False):
    """（`express.push.send` 遇到账本是 MR.ERP 的单交过来，或「再写一次」）停下 / 失败的回到排队、排一次导入。回结论。

    ``manual``：人在明确的推送入口点的（ERP 的 CC 推送工具 `express.cc_tools`）：这个 MR.ERP 账号被踢过的标记先清掉、这次照常登录
    （:func:`release_kick`，11-09 风险 7）。其余（提交新单、每分钟巡检、选一次后再排、改票面合计放行）都不清，有标记时导入入口快速失败。

    加锁读现值再判再写（11-28 ④ code-review）：调用方这笔事务的读视图可能早于任务领取（:func:`_claim` 改成「正在导入」并提交）——
    连点两次「再写一次」、网页与 CC 同时点、选一次（:func:`answer`）之后再排；普通读看到「排队中 + 空原因」、把原因改回空，盖掉了
    「正在导入」，导入回来记结论时这张已不是本次领取，会转成结果不明。"""
    row = _row(doctype, name, ["name", "status", "company", "ledger_state", "ledger_reason"], for_update=True)
    if not row or row.status != "submitted" or not handles(row.company):
        return {"status": "not_ready"}
    if manual:
        release_kick(row.company)
    if row.ledger_state == express_contracts.QUEUED and row.ledger_reason == contracts.IMPORTING:
        return {"status": "already_queued"}
    if row.ledger_state in (express_contracts.WRITTEN, express_contracts.MISMATCH):
        return {"status": "already_queued"}
    if str(row.ledger_reason or "").startswith(contracts.UNKNOWN):
        return {"status": "held", "reason": contracts.UNKNOWN}  # 结果不明：不自动重推
    _set(doctype, name, {"ledger_state": express_contracts.QUEUED, "ledger_reason": "", "ledger_needed": None,
                         "ledger_target": contracts.LEDGER_MRERP})
    frappe.db.commit()
    dispatch(doctype, name)
    return {"status": "sent"}


def release_kick(company) -> bool:
    """清掉这家公司 MR.ERP 账号的「被踢」标记（`pearnly_core.mrerp.kick_mark`，与 Cowork 同一个标记）：只给人点的推送入口用。"""
    from pearnly_core.mrerp import kick_mark as account_kick
    from pearnly_core.mrerp.session import lock_key

    from pearnly_erp_portal.mrerp import catalog  # 同 :func:`prepare`

    row = choices.company_row(company)
    if not row:
        return False
    # 这家公司各年度账套所在的连接（一般是同一条）：哪本被选中都可能用到，各清一次
    names = dict.fromkeys([row.mrerp_connection] + [book["connection"] for book in catalog.books_of(company)])
    cleared = False
    for name in filter(None, names):
        lease = connections.lease(name)  # loop-query: 一家公司的 MR.ERP 连接，一般一条、至多几条
        if lease.get("username"):
            cleared = account_kick.clear(lock_key(lease["system_url"], lease["username"])) or cleared
    return cleared


def _claim(doctype, name):
    """行锁里从「排队中」领成「正在导入」并提交；领不到回 None。"""
    row = _row(doctype, name, ["name", "owner", "company", "status", "ledger_state", "ledger_reason"], for_update=True)
    if not row or row.status != "submitted" or row.ledger_state != express_contracts.QUEUED \
            or row.ledger_reason == contracts.IMPORTING or not handles(row.company):
        frappe.db.rollback()
        return None
    return row


def run(doctype, name, document):
    """（后台任务，经 `express.push.mrerp_run`）领一张、准备、导入（不持行锁）、记结论。重复排队无副作用。"""
    if not mrerp.enabled():
        return
    # 连网的共用层（requests / openpyxl / Redis）只在导入任务里载入：本模块在只有标准库的离线检查里也要能导入（Express 入口导入它）
    from pearnly_core.mrerp.push.execute import execute
    from pearnly_core.mrerp.session import lock_key
    from pearnly_core.session_lock import operator_holds

    row = _claim(doctype, name)
    if row is None:
        return
    prepared = prepare(doctype, name, document)
    book = prepared.get("book") or {}
    if "failed" in prepared or "hold" in prepared:
        _set(doctype, name, {"ledger_state": express_contracts.FAILED if "failed" in prepared else express_contracts.HELD,
                             "ledger_reason": prepared.get("failed") or prepared.get("hold"),
                             "ledger_book": book.get("key") or None,
                             "ledger_needed": json.dumps(prepared.get("needed") or [], ensure_ascii=False)
                             if "hold" in prepared else None})
        frappe.db.commit()
        return
    company = prepared["company"]
    lease = connections.lease(book["connection"])
    if not lease.get("username") or not lease.get("password"):
        _set(doctype, name, {"ledger_state": express_contracts.HELD, "ledger_reason": contracts.NO_LOGIN})
        frappe.db.commit()
        return
    if operator_holds(lock_key(lease["system_url"], lease["username"])):
        _set(doctype, name, {"ledger_reason": contracts.ACCOUNT_BUSY})  # 等账号：照旧排队，每分钟再看
        frappe.db.commit()
        return
    _set(doctype, name, {"ledger_reason": contracts.IMPORTING, "ledger_sent_at": now_datetime(), "ledger_book": book["key"]})
    frappe.db.commit()  # 领取落定、行锁释放，之后才对外请求

    today = datetime.now(ZoneInfo("Asia/Bangkok")).date()
    secrets = {"username": lease["username"], "password": lease["password"]}
    if prepared.get("stock"):
        from pearnly_core.mrerp.push import stock as stock_push  # 库存入库 / 出库导入（同上，只在任务里载入）

        outcome = stock_push.execute(prepared["payload"], {"account_set": book["key"]}, secrets, lease["system_url"])
    elif prepared.get("form"):
        from pearnly_core.mrerp.push import stock_form  # 调拨 / 调整：网页表单原生保存（同上）

        outcome = stock_form.execute(prepared["payload"], {"account_set": book["key"]}, secrets, lease["system_url"], today)
    else:
        outcome = execute(prepared["payload"], {"account_set": book["key"]}, secrets, lease["system_url"], today)
    try:
        # 只是给选择用的缓存：撞冲突整笔重来（只重来存选项这一段，不再导入），用尽 / 存不了都不影响导入结论
        conflicts.retried(_observe, book["connection"], book["key"], outcome.get("menus"), outcome.get("masters") or None)
    except Exception:  # noqa: BLE001 — 只是给选择用的缓存，存不了不影响导入结论
        frappe.db.rollback()
    # 记结论（④ A2 #16，11-28）：导入已经发出、结果在手里，这一段撞冲突（1213 / 保存前比对）只整笔重来这一段，不再导入。
    # 原来不在 `retried` 里：撞一次任务就失败，单据停在「正在导入」，15 分钟后被 :func:`poll` 转成结果不明，已写进 MR.ERP 的也要人工核。
    # 用尽照旧抛出（同修前：停在「正在导入」，由巡检转结果不明）。
    values = conflicts.retried(_conclude, doctype, name, contracts.settle(outcome), prepared, book, company.name)
    if values and values.get("ledger_state") in (express_contracts.WRITTEN, express_contracts.MISMATCH):
        # 写进了 MR.ERP（含写入核不上）：排一次主档整读（公司接入 `mrerp.masters.after_write`：去重、只排队、出错只记日志不抛）
        from pearnly_erp_portal.mrerp import masters  # 主档整读那一侧在写账之后才用到（同上，用到时导入）

        masters.after_write(company.name)


def _observe(connection, account_set, menus, masters):
    choices.observe(connection, account_set, menus, masters)
    frappe.db.commit()


def _conclude(doctype, name, values, prepared, book, company):
    """把导入结论记回单据并提交，回写下的值（没写回 None）。由 :func:`run` 经 `conflicts.retried` 调：重来只重来这一段。

    先加锁读这张的状态与账本栏（导入那几十秒本事务提交过，普通读的读视图可能比别人刚提交的旧）：
    - 导入期间在 ERP 作废了（11-13）：导入成的不记成干净的「已写入」，转人工、单号如实留着（同 Express 已领走的）；没导入成的清账本栏；
      结果不明的照旧交人核。作废会清掉账本栏（`express.push.void`），所以先于「还是不是本次领取」判。
    - 仍是本次领取（排队中 + 正在导入）：按导入结果写。
    - 已被别的路径改过（主会话 11-28 定的口径）：现值是结果不明（巡检转的）、导入其实写进了的，照实记已写入与单号（导入结果是事实，
      「结果不明」只是猜测）；导入写进了或结果不明（可能写进了）、而现值是别的意外状态的，转人工：停在结果不明（同巡检转的那种，
      `send` / 再写一次 / 巡检 / 选一次都不再导入，防 MR.ERP 里再多一张），写进了的单号记在单据上，另记一条错误日志带原状态与单号；
      确定没写进去的以别人的结论为准。"""
    row = _row(doctype, name, ["status", "ledger_state", "ledger_reason", "ledger_docnum"], for_update=True)
    if not row:
        return None
    values = dict(values)
    if values["ledger_reason"] == contracts.CHOICE:
        fresh = choices.company_row(company)
        if prepared.get("stock") or prepared.get("form"):
            from pearnly_erp_portal.mrerp import stock  # 同 :func:`_row`

            needed = stock.needed(fresh, book, prepared["menu_kind"])
        else:
            menu_view, master_view = choices.views(fresh, book)
            needed = contracts.needed(menu_view, master_view, prepared["direction"])
        values["ledger_needed"] = json.dumps(needed, ensure_ascii=False)
    else:
        values["ledger_needed"] = None
    written = values["ledger_state"] in (express_contracts.WRITTEN, express_contracts.MISMATCH)
    landed = written or values["ledger_reason"].startswith(contracts.UNKNOWN)  # 写进了，或可能写进了
    unknown_now = str(row.ledger_reason or "").startswith(contracts.UNKNOWN)
    if row.status == "void":
        # 写进了的（含写入核不上 mismatch：导入成了、只是合计核不上，原因是空的）一并按「写进去后作废」转人工、单号留着
        # （11-28 ④ code-review：原来只认 written，mismatch 落进下一支被清空账本栏、MR.ERP 单号丢了）
        if written:
            values.update(ledger_state=express_contracts.HELD, ledger_reason=express_contracts.VOIDED_AFTER_WRITE,
                          ledger_needed=None)
        elif not values["ledger_reason"].startswith(contracts.UNKNOWN):
            values = {"ledger_state": None, "ledger_reason": "", "ledger_needed": None}
    elif row.ledger_state == express_contracts.QUEUED and row.ledger_reason == contracts.IMPORTING:
        pass  # 仍是本次领取：照导入结果写
    elif written and unknown_now:
        pass  # 巡检转成结果不明、其实写进了：照实记已写入
    elif not landed or unknown_now:
        frappe.db.commit()  # 确定没写进去、或已经停在结果不明：别人的结论为准；这笔只加锁读过，提交只是放掉行锁
        return None
    else:
        frappe.log_error(title="ERP MR.ERP import landed after the document moved on",
                         message=f"{doctype} {name}: {row.ledger_state} {row.ledger_reason}; "
                                 f"MR.ERP {values.get('ledger_docnum') or values['ledger_reason']}")
        values = {"ledger_state": express_contracts.HELD, "ledger_reason": f"{contracts.UNKNOWN}: {contracts.MOVED_DURING_IMPORT}",
                  "ledger_needed": None, "ledger_docnum": (values.get("ledger_docnum") if written else row.ledger_docnum) or ""}
    _set(doctype, name, values)
    frappe.db.commit()
    return values


def resolve_unknown(doctype, name, entered, docnum=None) -> bool:
    """结果不明的单由人在 MR.ERP 核对后记下（ERP 单据弹窗「已在 MR.ERP 核对」、CC `erp_resolve_push_result`）：已入账记已写入 + 单号、
    排一次主档整读（同写成）；没有入账回到可再推（「重试」）。只收「账本是 MR.ERP + 停在结果不明」的单，别的回假。谁何时判了什么记在
    单据的评论里。权限由调用方核（同 :func:`answer`）。"""
    row = _row(doctype, name, ["company", "ledger_state", "ledger_reason"], for_update=True)
    if not row or not handles(row.company) or row.ledger_state != express_contracts.HELD \
            or not str(row.ledger_reason or "").startswith(contracts.UNKNOWN):
        return False
    values = contracts.checked(entered, docnum)
    _set(doctype, name, {**values, "ledger_needed": None})
    _audit(doctype, name, row.ledger_reason, values)
    frappe.db.commit()
    if entered:
        from pearnly_erp_portal.mrerp import masters  # 同 :func:`run`

        masters.after_write(row.company)
    return True


def _audit(doctype, name, before, values):
    """核对记录（谁、何时、判了什么、原来的原因）：单据评论，内部记录不经翻译。"""
    frappe.get_doc({"doctype": "Comment", "comment_type": "Info", "reference_doctype": doctype, "reference_name": name,
                    "content": f"MR.ERP check by {frappe.session.user}: "
                               + (f"entered {values.get('ledger_docnum') or ''}".strip() if values["ledger_state"] == "written"
                                  else "not entered") + f" (was {before})"}).insert(ignore_permissions=True)


def answer(doctype, name, answers) -> bool:
    """单据上选要人选的 MR.ERP 项（``[{kind, subject, value}]``，只认停在单据上的那几项、这一项能选的值）：记在公司上后再排。
    权限由调用方核（同 `express.api.answer`）。全部认得回真。"""
    row = _row(doctype, name, ["company", "ledger_state", "ledger_needed", "ledger_book"])
    if not row or row.ledger_state != express_contracts.HELD or not handles(row.company):
        return False
    try:
        needed = {(item["kind"], item["subject"]): item for item in json.loads(row.ledger_needed or "[]")}
    except (ValueError, KeyError, TypeError):
        return False
    from pearnly_erp_portal.express import choices as express_choices  # 同 :func:`prepare`
    from pearnly_erp_portal.mrerp import catalog

    # 菜单 / 默认值记在停下时选中的那本名下（单据上的 ``ledger_book``）；那本已不在这家名单里就不收
    book = catalog.pick(row.company, None, row.ledger_book)["book"] if row.ledger_book else None
    for given in answers or []:
        item = needed.get((given.get("kind"), given.get("subject"))) if isinstance(given, dict) else None
        value = str(given.get("value") or "").strip() if item else ""
        options = {option["code"]: option.get("name") or "" for option in (item or {}).get("options") or []}
        if not item or value not in options:
            return False
        if item["kind"] == express_choices.ACCOUNT_SET:
            # 同期两本选哪本：与 Express 同一个记忆（这家公司 × 这个会计年度），`catalog.pick` 下次照它选
            express_choices.remember(row.company, item["kind"], item["subject"], value,  # loop-query: 停在单据上的几项
                                     options[value] or item.get("label") or "")
            continue
        if (row.ledger_book and book is None) or choices.choose(row.company, item["kind"], item["subject"], value, book) is None:
            return False
    send(doctype, name)
    return True


def poll():
    """（每分钟，`express.push.poll` 末尾调）等账号的再排；「正在导入」超过 15 分钟的（进程死了）转结果不明。"""
    if not mrerp.enabled():
        return
    stale = now_datetime() - timedelta(minutes=contracts.IMPORTING_MINUTES)
    for doctype in DIRECTIONS:
        rows = frappe.get_all(doctype, filters={"ledger_target": contracts.LEDGER_MRERP,
                                                "ledger_state": express_contracts.QUEUED, "status": "submitted"},
                              fields=["name", "ledger_reason", "ledger_sent_at"], order_by="modified asc",
                              limit_page_length=POLL_MAX)
        for row in rows:
            if row.ledger_reason == contracts.IMPORTING:
                if row.ledger_sent_at and frappe.utils.get_datetime(row.ledger_sent_at) < stale:
                    _interrupted(doctype, row.name, stale)
            else:
                # 列出时的旧值只决定排不排：任务领取时在行锁里按现值再核一次（:func:`_claim`：仍排队中、不在导入、已提交），
                # 列出之后被转走的这一件领不到、什么都不改
                dispatch(doctype, row.name)
        frappe.db.commit()


def _interrupted(doctype, name, stale):
    """巡检把进程死掉的「正在导入」转成结果不明：加锁按现值再核一次仍是超时的那次领取才改（④，11-28）。普通读列出之后导入刚
    记成「已写入」、或这张已被重新领走的，按旧值改会把真结论盖成「结果不明」。"""
    row = _row(doctype, name, ["status", "ledger_target", "ledger_state", "ledger_reason", "ledger_sent_at"], for_update=True)
    if not row or row.status != "submitted" or row.ledger_target != contracts.LEDGER_MRERP \
            or row.ledger_state != express_contracts.QUEUED or row.ledger_reason != contracts.IMPORTING \
            or not row.ledger_sent_at or frappe.utils.get_datetime(row.ledger_sent_at) >= stale:
        return False
    _set(doctype, name, {"ledger_state": express_contracts.HELD, "ledger_reason": f"{contracts.UNKNOWN}: ERR_IMPORT_INTERRUPTED"})
    return True


__all__ = ["DIRECTIONS", "INVOICES", "STOCK", "answer", "dispatch", "handles", "picked", "picked_for", "poll", "prepare", "queue", "release_kick", "run", "send"]
