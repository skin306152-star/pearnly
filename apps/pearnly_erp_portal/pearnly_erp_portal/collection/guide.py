"""ERP 收款的引导式（CC 对话里点「收款」，总设计 P1）：声明 `collection.cc_guide.GUIDES` 点名的产品函数都在这里。

每个函数只转调收款的服务函数（`collection.service`，网页「新建收款」与 LINE 收款同一组），不另查库、不另写规则：

- 步骤与 LINE 收款同一口径：公司 → 收款日期（今天能录就不问，`service.day_options`）→ 账套（本对话账套 / 按日期定得下来就不问）
  → 客户 → 未收发票（按客户即时读回，逐张可收多少）→ 现金 / 转账 / 折扣分摊 → 每种转账一张凭证 → 复核 → 确定 → 结果。
- **写进账本的值都是这里给出的选项原值或用户在卡上填的数**：客户编码、发票号按原样比（不做空白归一），金额只来自卡上；
  模型不经手（用户 11-15：两个真机缺陷都出在值经过模型复述）。
- 每一步服务端都再核：选项此刻还在（客户停用、发票已被收）、金额不超此刻可收、合计对得上、日期在期间里；确定时
  `service.release` 再全判一遍，草稿在别处改过回 ``changed``，规则此刻不过退回对应那一步。
- 读回没到 / 电脑没连 / 要更新时，发票这一步照列上次读到的，题目下一行事实（同 LINE），列表为空时不说「没有未收发票」。

选项函数签名同引擎（`pearnly_core.assistant.guide.run`）：``options(ctx, txt, start, page_length, codes)``；``ctx`` 带本对话
``session``、开始时的 ``company`` / ``ledger``、前面各步的 ``answers``、草稿号 ``draft``。
"""
from __future__ import annotations

import contextlib

import frappe
from frappe import _
from frappe.utils import nowdate

from pearnly_erp_portal.collection import contracts, service, snapshot, texts

CASH = "cash"
TRANSFER = "transfer:"   # 分摊的各行码带种类前缀：声明里「有转账才问凭证」按前缀判（``when.starts``）
DISCOUNT = "discount:"
DRAFT_SEP = "@"          # 草稿号带复核时的版本：``COL…@3``（别处改过之后点确定回 changed、刷新复核卡）
SEVERAL_BOOKS = "express.account_set_several"
MASTERS_READING = "express.masters_reading"
MENU_SEP = "|"           # 账本是 MR.ERP、收款导入菜单要选一次时，账套那一步的选项是「账套 | 菜单」（选一次记在公司上）

# 业务拒绝的原因码 → 退回哪一步重问（其余答案保留）；没列的退回收款方式那一步（金额、方式、折扣类）
STEP_OF = {
    service.DAY_NEEDED: "day",
    service.LEDGER_NEEDED: "book", service.BOOK_NOT_CONVERSATION: "book", SEVERAL_BOOKS: "book",
    "express.account_set_missing": "book",
    service.NO_CUSTOMER: "customer",
    contracts.INVOICES_EMPTY: "invoices", contracts.INVOICES_TOO_MANY: "invoices", contracts.INVOICE_DUPLICATE: "invoices",
    contracts.INVOICE_NOT_FOUND: "invoices", contracts.OVER_COLLECTED: "invoices",
    contracts.VOUCHER_MISSING: "voucher", service.VOUCHER_INVALID: "voucher",
}
READ_FACTS = {snapshot.READING: "Reading from Express", "offline": "No computer connected",
              "outdated": "CC Desktop needs an update"}
RESULT = {contracts.WRITTEN: "entered", contracts.QUEUED: "queued", contracts.WRITING: "queued", contracts.FAILED: "failed",
          contracts.WITHDRAWN: "withdrawn"}


# ── 上下文 ───────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _conversation(ctx):
    """调收款服务时带上这段对话（本对话账套、对话里的附件都按它认），出来还原。"""
    before = getattr(frappe.local, "pearnly_assistant", None)
    frappe.local.pearnly_assistant = {**(before or {}), "session_id": str(ctx.get("session") or "")}
    try:
        yield
    finally:
        frappe.local.pearnly_assistant = before


def _answers(ctx) -> dict:
    return ctx.get("answers") or {}


def _company(ctx) -> str:
    return str(_answers(ctx).get("company") or ctx.get("company") or "")


def _day(ctx) -> str:
    return str(_answers(ctx).get("day") or nowdate())


def _row(company):
    return frappe.db.get_value(service.COMPANY, company, ["name", "company_name", "tax_id", "branch"], as_dict=True) or {}


def _page(rows, txt, start, page_length, codes, fields=("code", "name")) -> dict:
    """一张有界的选项表（公司、账套、方式、一位客户的发票，都已取全）上做按码取、搜索与翻页。"""
    if codes is not None:
        wanted = {str(code) for code in codes}
        return {"options": [row for row in rows if row["code"] in wanted], "more": False}
    text = str(txt or "").strip().casefold()
    if text:
        rows = [row for row in rows if any(text in str(row.get(key) or "").casefold() for key in fields)]
    start, size = max(0, int(start or 0)), max(1, int(page_length or 20))
    return {"options": rows[start:start + size], "more": len(rows) > start + size}


def _explained(error) -> list:
    return [texts.explain(error)] if isinstance(error, contracts.CollectionError) else []


# ── 公司 ─────────────────────────────────────────────────────────────────────


def _companies() -> list:
    return [{"code": row["name"], "name": row["company_name"]} for row in service.companies_for()]


def company_default(ctx):
    """对话挂的那家（能收款的）就不问。"""
    company = str(ctx.get("company") or "")
    found = next((row for row in _companies() if row["code"] == company), None) if company else None
    return {"value": found["code"], "value_label": found["name"], "source": "conversation"} if found else None


def company_options(ctx, txt="", start=0, page_length=20, codes=None):
    return _page(_companies(), txt, start, page_length, codes)


# ── 收款日期（`service.day_options`：今天能录就不问；不在任何已连接账套的期间里才问）──────────────────────


def _day_options(ctx, day=None):
    """``(day_options 的结果, 拒绝)``：这家没有账套 / 没有税号这类停下的原因照实交回（不当成「这天能录」）。"""
    with _conversation(ctx):
        try:
            return service.day_options(_company(ctx), day), None
        except contracts.CollectionError as error:
            return None, error


def day_default(ctx):
    found, refused = _day_options(ctx)
    if found is None and refused is None:
        today = nowdate()
        return {"value": today, "value_label": today, "source": "today"}
    return None


def day_check(ctx, value) -> str:
    day = service.parse_day(value)
    if not day:
        return service.DAY_NEEDED
    found, refused = _day_options(ctx, day)
    if refused is not None:
        return refused.code
    return "" if found is None else service.DAY_NEEDED


def day_facts(ctx) -> list:
    """可录的日期段（「会计期间：…」，`texts.periods_text`）：只在这一天录不了时给；这家停下的原因（没有账套等）照实说。"""
    found, refused = _day_options(ctx, _answers(ctx).get("day") or None)
    if refused is not None:
        return _explained(refused)
    return [texts.periods_text(found["periods"])] if found and found.get("periods") else []


# ── 账套（本对话账套；不在对话账套里的按日期定，同期两本、没选过才问）─────────────────────────────


def _pinned(ctx):
    from pearnly_erp_portal.express import conversation

    with _conversation(ctx):
        return conversation.pinned(_row(_company(ctx)))


def _menu_needed(ctx) -> dict | None:
    """账本是 MR.ERP、收款导入菜单不止一个已关联的、还没选过：``{"options": [...]}``；其余（含 Express 公司）None。"""
    with _conversation(ctx):
        try:
            return service.menu_options(_company(ctx), _day(ctx))
        except contracts.CollectionError:
            return None


def book_default(ctx):
    from pearnly_erp_portal.express import target

    with _conversation(ctx):
        try:
            book = service.book_for(_row(_company(ctx)), _day(ctx))
        except contracts.CollectionError:
            return None
    if _menu_needed(ctx):
        return None  # 收款导入菜单要选一次：账套这一步连同菜单问
    return {"value": book["key"], "value_label": target.option_name(book),
            "source": "conversation" if _pinned(ctx) else "date"}


def book_options(ctx, txt="", start=0, page_length=20, codes=None):
    from pearnly_erp_portal.express import target

    row = _row(_company(ctx))
    with _conversation(ctx):
        try:
            book = service.book_for(row, _day(ctx))
            rows = [{"code": book["key"], "name": target.option_name(book)}]
        except contracts.CollectionError as error:
            if error.code == service.LEDGER_NEEDED:
                rows = [{"code": item["code"], "name": item["name"]} for item in error.detail.get("options") or ()]
            elif error.code == SEVERAL_BOOKS:
                rows = [{"code": item["code"], "name": item["name"], "note": item.get("short") or ""}
                        for item in service._with_short(row, error.detail["options"])]
            else:
                return {"options": [], "more": False, "facts": _explained(error)}
    menu = _menu_needed(ctx)
    if menu:
        # 账本是 MR.ERP、收款导入菜单不止一个已关联的：每本账套 × 每个菜单一项，选一次记住（同 ERP 单据「问一次记住」）
        rows = [{"code": f"{row['code']}{MENU_SEP}{option['code']}", "name": f"{row['name']} · {option['name']}",
                 **({"note": row["note"]} if row.get("note") else {})} for row in rows for option in menu["options"]]
    return _page(rows, txt, start, page_length, codes)


def book_apply(ctx, value) -> None:
    """选了账套：对话里定账套的那家记进本对话（同右栏账套选择器，`assistant.ledger.choose(by="page")`）；不按对话账套的
    记成这家这一年的选择（同网页 / LINE，`service.choose_book`）。带了收款导入菜单（``账套|菜单``）的另记一次菜单。"""
    value, _sep, menu = str(value or "").partition(MENU_SEP)
    if menu:
        _apply_book(ctx, value)
        with _conversation(ctx):
            service.choose_menu(_company(ctx), menu, _day(ctx))
        return
    _apply_book(ctx, value)


def _apply_book(ctx, value) -> None:
    if _pinned(ctx) is not None:
        from pearnly_core.assistant import ledger

        chosen = ledger.choose(str(ctx.get("session") or ""), value, by="page")
        if chosen.get("status") != "set":
            frappe.throw(_("Invalid value"))
        return
    with _conversation(ctx):
        service.choose_book(_company(ctx), value, _day(ctx))


# ── 客户 ─────────────────────────────────────────────────────────────────────


def customer_options(ctx, txt="", start=0, page_length=20, codes=None):
    company = _company(ctx)
    with _conversation(ctx):
        if codes is not None:
            # 核答案：编码按原样对上账本里那一件（`customer_code` 回原样编码；只差空白的另一件不算）
            code = str(next(iter(codes), "") or "")  # 客户是单选：卡上只交回一位
            try:
                exact = service.customer_code(company, code) if code else ""
            except contracts.CollectionError:
                exact = ""
            if not exact or exact != code:
                return {"options": [], "more": False}
            return {"options": [{"code": code, "name": service._customer_name(company, code)}], "more": False}
        size = max(1, min(int(page_length or 20), service.SEARCH_MAX))
        found = service.customers(company, txt, start, size)
    rows = [{"code": row["code"], "name": row["name"], "note": row["code"]} for row in found["items"]]
    return {"options": rows, "more": int(start or 0) + len(rows) < int(found.get("total") or 0)}


# ── 未收发票（金额多选：每张可收多少 = 上限，默认全额，可改小）──────────────────────────────────


def invoice_options(ctx, txt="", start=0, page_length=20, codes=None):
    """这位客户此刻能收的发票：出卡时请电脑按这位客户即时读一次（核答案、搜索、翻页不重读）。读回没到 / 电脑没连 / 要更新：照列
    上次读到的，题目下一行事实；在读时页面过一会儿再取这张卡。"""
    customer = str(_answers(ctx).get("customer") or "")
    fresh = codes is None and not txt and not start
    with _conversation(ctx):
        try:
            found = service.open_invoices(_company(ctx), customer, _day(ctx), refresh=fresh)
        except contracts.CollectionError as error:
            return {"options": [], "more": False, "facts": _explained(error)}
    rows = [{"code": item["docnum"], "name": item["docnum"], "note": item["doc_date"], "amount": item["available"],
             "max": item["available"], "remaining": item["remaining"]} for item in found["items"]]
    out = _page(rows, txt, start, page_length, codes, fields=("code", "note"))
    state = found.get("refresh") or ""
    if state in READ_FACTS:
        # 「正在读取」按账本说（MR.ERP 的不说 Express，`service.reading_text`）
        out["facts"] = [service.reading_text(_company(ctx)) if state == snapshot.READING else _(READ_FACTS[state])]
        if state == snapshot.READING:
            out["wait"] = True
    elif fresh and not found["items"]:
        out["facts"] = [_("No open invoices")]  # 刚读回（ready）才这样说
    return out


def _invoice_total(values):
    return sum((contracts.money(row["amount"]) for row in values.get("invoices") or ()), contracts.ZERO)


# ── 收款方式（分摊：现金 / 各种转账 / 折扣，合计 = 所选发票收的合计）──────────────────────────────


def method_options(ctx, txt="", start=0, page_length=20, codes=None):
    with _conversation(ctx):
        try:
            found = service.methods(_company(ctx), _day(ctx))
        except contracts.CollectionError as error:
            out = {"options": [], "more": False, "facts": _explained(error)}
            if error.code in (MASTERS_READING, "collection.mrerp_reading"):
                out["wait"] = True
            return out
    rows = [{"code": CASH, "name": _("Cash"), "kind": "cash"}] if found["cash"] else []
    rows += [{"code": TRANSFER + item["prefix"], "name": item["label"], "kind": "transfer"} for item in found["transfers"]]
    if found["discount"]:
        rows.append({"code": DISCOUNT + found["discount"]["prefix"], "name": _("Discount"), "kind": "discount"})
    return _page(rows, txt, start, page_length, codes)  # MR.ERP 一笔只收一种钱：作答时 method_check 拒收


def method_check(ctx, value) -> str:
    paid = sum((contracts.money(row["amount"]) for row in value or ()), contracts.ZERO)
    if paid != _invoice_total(_answers(ctx)):
        return contracts.AMOUNT_MISMATCH
    money = [row for row in value or () if not str(row.get("code") or "").startswith(DISCOUNT)]
    if len(money) > 1:
        from pearnly_erp_portal.collection import mrerp as ledger

        if ledger.handles(_company(ctx)):
            return ledger.ONE_METHOD  # MR.ERP 收款单只有一格记收到的钱
    return ""


# ── 凭证（每种转账一张，按分摊卡上转账的先后对应）────────────────────────────────────────────


def _transfers(values) -> list:
    return [row for row in values.get("methods") or () if str(row["code"]).startswith(TRANSFER)]


def voucher_check(ctx, value) -> str:
    return "" if len(value or ()) == len(_transfers(_answers(ctx))) else contracts.VOUCHER_MISSING


# ── 草稿、复核、确定、结果 ───────────────────────────────────────────────────


def _draft_name(draft) -> tuple:
    name, _sep, version = str(draft or "").partition(DRAFT_SEP)
    return name, int(version) if version.isdigit() else 0


def _returned(error) -> dict:
    return {"returned": {STEP_OF.get(error.code, "methods"): {"reason": error.code, "text": texts.explain(error)}}}


def _methods(values) -> tuple:
    """分摊各行 → (现金, ``[{prefix, amount, voucher?}]``)：转账按先后配上卡上交的凭证（`assistant.files.attachment` 只认本对话
    本人的文件，存成收款的私有凭证 `service.store_voucher`）。"""
    from pearnly_core.assistant import files

    slips = list(values.get("voucher") or ())
    cash, out = "0", []
    for row in values.get("methods") or ():
        code = str(row["code"])
        if code == CASH:
            cash = str(row["amount"])
            continue
        item = {"prefix": code.split(":", 1)[1], "amount": str(row["amount"])}
        if code.startswith(TRANSFER) and slips:
            filename, content = files.attachment(slips.pop(0))  # loop-query: 每种转账一张凭证（最多 MAX_METHODS 种）
            item["voucher"] = service.store_voucher(filename, content)["file"]  # loop-query: 同上，每种转账一张
        out.append(item)
    return cash, out


def draft(ctx, values):
    """答完了：建一笔收款草稿（`service.create_draft`，规则全过），回 ``{"draft": "COL…@版本"}``；规则此刻不过回 ``returned``
    （哪一步、原因），引擎退回那一步重问。改了前面的答案再来一次：上一张草稿作废、建新的（同 LINE 出新卡）。"""
    previous, _version = _draft_name(ctx.get("draft"))
    with _conversation(ctx):
        if previous:
            with contextlib.suppress(contracts.CollectionError, frappe.PermissionError):
                service.discard_draft(previous)
        try:
            cash, methods = _methods(values)
            card = service.create_draft(
                _company(ctx), values["customer"],
                [{"docnum": row["code"], "amount": str(row["amount"])} for row in values.get("invoices") or ()],
                cash=cash, methods=methods, doc_date=values.get("day") or None, source="cc")
        except contracts.CollectionError as error:
            return _returned(error)
    return {"draft": f"{card['name']}{DRAFT_SEP}{card['version']}"}


def review(ctx, draft) -> dict:
    """复核卡：要办的全摆出来（公司·账套·客户·日期·各张发票收多少·方式·折扣·合计·销售员 / 部门），每行能点回那一步改。"""
    name, _version = _draft_name(draft)
    with _conversation(ctx):
        card = service.card(name)
    label = lambda key: f"guide.step.collection.{key}"  # noqa: E731
    methods = ([{"code": CASH, "name": _("Cash"), "amount": card["cash"]}] if contracts.money(card["cash"]) else []) + [
        {"code": row["prefix"], "name": row.get("label") or row["name"], "amount": row["amount"],
         **({"note": _("Discount")} if row.get("role") == contracts.DISCOUNT else {})} for row in card["methods"]]
    problem = card.get("problem") or ""
    return {
        "version": card["version"], "ready": bool(card["ready"]), "problem": problem,
        "problem_text": texts.explain(contracts.CollectionError(problem, card.get("detail"))) if problem else "",
        "lines": [
            {"key": "company", "label_key": label("company"), "value": card["company_name"], "step": "company"},
            {"key": "book", "label_key": label("book"), "value": card["book"], "step": "book"},
            {"key": "customer", "label_key": label("customer"), "value": f"{card['customer']} {card['customer_name']}",
             "step": "customer"},
            {"key": "day", "label_key": label("day"), "value": card["doc_date"], "step": "day"},
            *([{"key": "salesman", "label_key": "guide.review.salesman", "value": card["salesman"]}] if card["salesman"] else []),
            *([{"key": "department", "label_key": "guide.review.department", "value": card["department"]}]
              if card["department"] else []),
        ],
        "tables": [
            {"key": "invoices", "label_key": label("invoices"), "step": "invoices",
             "rows": [{"code": row["docnum"], "name": row["docnum"], "note": row["invoice_date"], "amount": row["amount"]}
                      for row in card["invoices"]]},
            {"key": "methods", "label_key": label("methods"), "step": "methods", "rows": methods},
        ],
        "total": card["total"],
    }


def _same_book(name) -> None:
    """草稿的账套仍是本对话账套（之后在右栏换了就不放行，同打字路径 `plan.view` 的 ``book_not_conversation``）。"""
    from pearnly_erp_portal.express import conversation

    doc = frappe.db.get_value(service.DOCTYPE, name, ["company", "book_key"], as_dict=True)
    if not doc:
        return
    pin = conversation.pinned(_row(doc.company))
    if pin is not None and pin.get("key") and pin["key"] != doc.book_key:
        raise contracts.CollectionError(service.BOOK_NOT_CONVERSATION)


def note(ctx, draft) -> str:
    """确定卡问句下面那行事实：这笔收款的公司与账套（同撤回允许卡 `cc_tools.note_withdraw` 的拼法）；还没有草稿回空串。"""
    from pearnly_erp_portal.collection import cc_tools

    name, _version = _draft_name(draft)
    return cc_tools.note_withdraw({"collection": name}) if name else ""


def confirm(ctx, values, draft, consent) -> dict:
    """点了「确定」：按复核时的版本放行（`service.release`，同意 = 页面上这一下 ``guide:<运行>:<版本>``）。草稿在别处改过回
    ``changed``（带新版本，复核卡刷新）；规则此刻不过（发票刚被别人收了、凭证不对）回 ``returned``；草稿的账套已不是本对话
    账套也退回账套那一步。放行后不在这里等结果：结果卡按 :func:`result` 现取。"""
    name, version = _draft_name(draft)
    with _conversation(ctx):
        try:
            _same_book(name)
        except contracts.CollectionError as error:
            return _returned(error)
        released = service.release(name, version, consent)
    if released["status"] == "changed":
        return {"outcome": "changed", "draft": f"{name}{DRAFT_SEP}{released['version']}"}
    if released["status"] == "invalid":
        return _returned(contracts.CollectionError(released["code"], released.get("detail")))
    return {"status": released["status"], "name": name}


def result(ctx, draft) -> dict:
    """结果卡的一行：已入账（RE 号）/ 排队（在等 CC 桌面版或 Express 关闭；页面过一会儿再取）/ 未入账（原因）/ 已撤回。"""
    name, _version = _draft_name(draft)
    with _conversation(ctx):
        view = service.status(name)
        waiting = view["status"] in (contracts.QUEUED, contracts.WRITING) and service.express_waiting(name)
    status = "failed" if view.get("returned") else RESULT.get(view["status"], view["status"])
    out = {"status": status, "name": name, "docnum": view["docnum"], "total": view["total"],
           "customer": f"{view['customer']} {view['customer_name']}",
           **({"ledger": "mrerp"} if view.get("ledger") == "mrerp" else {})}  # 账本是 MR.ERP：结果按 MR.ERP 说
    if status == "queued":
        out.update(wait=True, express_wait=bool(waiting))
    if status == "failed":
        out["reason_text"] = view.get("reason_text") or ""
    if view.get("mrerp_voided"):
        # 已写进 MR.ERP 后撤回：单号、在哪个菜单作废、冲过的发票，与要不要重录那一问（同收款页、CC 收款工具）
        out.update(docnum=view["mrerp_voided"]["docnum"], reason_text=view["mrerp_voided"]["text"],
                   voided=view["mrerp_voided"])
    return out
