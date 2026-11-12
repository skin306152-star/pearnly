"""LINE 里录收款（富菜单第 4 格「收款」，评估 docs/work/2026-11-11-collection-receipt-assessment.md §6、§12-1）。

一步一问（LINE 原生快捷回复 + Flex），每一步只认「当前正在问的那一步」，旧按钮点了不生效：

1. 选公司（只有一家时跳过）→ 2. 打客户编号或名称的一部分（最多列 12 个，多了请再打几个字）→ 3. 勾发票（Flex 列表可多选、
   显示合计；余额已扣别人预留）→ 4. 收款方式（现金 + 这本账套能收钱的转账类型）→ 5. 金额 → 6. 转账发一张凭证照片（每笔一张）
   → 不够时【再加一种方式】【折扣】【下次再收】（下次再收：选哪张留余额、或逐张输入）；超过未收的请重输 → 7. 确认卡
   （公司·账套·客户·各张本次收·方式·折扣·合计）带【确认】【编辑】【撤回】。
2. 放行：【确认】按钮或打「ยืนยัน / 确认 / ok」（大小写、空格不论）→ `collection.service.release`（卡片展示的版本 + 这次 LINE
   事件号作同意）；卡片之后改过回 ``changed`` 就重出卡片。【编辑】经 `line_login.entry_url` 打开网页原生弹窗（只改这张草稿）。
3. 结果：放行后在后台等写入最多 :data:`WAIT_SECONDS` 秒（显示三个点），写成用回复给 RE 号；没写成先回「已记录 · 待写入」，
   之后写成 / 失败由回执（`collection.push` → :func:`notify`）推送给录入的收款员本人。

规则、权限、金额全部在 `collection.service`（LINE 只调它，不另写一份）；这里只管问与记。身份是绑定的 ERP 账号。
"""
import contextlib
import re
from decimal import Decimal
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line, line_binding, line_session

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.collection import contracts

STATE = "erp_collection"
ACTION = "col"
TTL_MINUTES = 30
WRITE = "collection.write"
DOCTYPE = "ERP Collection"
CASH = "@cash"
DISCOUNT = "@discount"
OLDEST, EACH = "@oldest", "@each"
# 快捷回复一次最多 13 个按钮：选项最多 12 个，留一个给「取消」
MAX_CHOICES = 12
# Flex 发票列表：每张气泡 10 张，最多 5 张气泡（一笔收款最多 `contracts.MAX_INVOICES` 张）
PER_BUBBLE = 10
WAIT_SECONDS = 20
EDIT_ROUTE = "/desk/pearnly-my-collections/"
# 打字确认（用户手打的词不翻译；四种语言 + ok）。比较时去掉空白、不分大小写
CONFIRM_WORDS = frozenset(("ยืนยัน", "确认", "確認", "ok", "confirm"))
# 回执里这两种「不能收」带每张发票此刻的余额（`collection.push`）；没带余额的（如服务品现金制）照原因说
BALANCE_CODES = ("ALREADY_COLLECTED", "INVOICE_NOT_FOUND")
_WAIT_KEY = "pearnly:erp:collection:line-wait:"
_TOLD_KEY = "pearnly:erp:collection:line-told:"


def _service():
    from pearnly_erp_portal.collection import service

    return service


# ── 会话 ────────────────────────────────────────────────────────────────────


def payload_of(line_user_id) -> dict:
    found = line_session.get_session(line_user_id) or {}
    return dict(found.get("payload") or {}) if found.get("state") == STATE else {}


def active(line_user_id) -> bool:
    return bool(payload_of(line_user_id).get("step"))


def _save(line_user_id, payload):
    line_session.set_session(line_user_id, STATE, payload, ttl_minutes=TTL_MINUTES)


def _clear(line_user_id):
    line_session.clear_session(line_user_id)


@contextlib.contextmanager
def acting(user):
    """webhook 是 Guest：按绑定的这个账号调服务（权限、本人记录都按它判），做完还原。"""
    previous = frappe.session.user
    frappe.set_user(user)
    try:
        yield
    finally:
        frappe.set_user(previous)


# ── 消息 ────────────────────────────────────────────────────────────────────


def _data(step, value="", **extra) -> str:
    return urlencode({"a": ACTION, "s": step, **({"v": value} if value != "" else {}), **extra})


def _button(label, data):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data, "displayText": label}}


def _cancel():
    return _button(_("Cancel"), _data("cancel"))


def _send(reply_token, messages):
    """回给这个人。后台任务里（:func:`background`）攒到最后用回复令牌发一次，令牌过期了就推送。"""
    to = getattr(frappe.local, "pearnly_collection_to", "")
    if to:
        return line.reply_or_push(to, messages, reply_token)
    return line.reply(reply_token, messages)


@contextlib.contextmanager
def background(line_user_id, reply_token):
    """后台任务里的问答：同一套函数照常回复，消息攒到块结束一次发出（`line.batch`：先用回复令牌，不行再推送）。"""
    previous = getattr(frappe.local, "pearnly_collection_to", "")
    frappe.local.pearnly_collection_to = line_user_id
    try:
        with line.batch(line_user_id, reply_token):
            yield
    finally:
        frappe.local.pearnly_collection_to = previous


def _ask(reply_token, text, items=()):
    items = list(items)[:MAX_CHOICES] + [_cancel()]
    _send(reply_token, [{"type": "text", "text": text, "quickReply": {"items": items}}])


def _text(reply_token, text):
    _send(reply_token, [{"type": "text", "text": text}])


def money(value) -> str:
    from pearnly_erp_portal.collection import texts

    return texts.money(value)


def _amount(value) -> Decimal:
    return contracts.money(value)


def _explain(error) -> str:
    from pearnly_erp_portal.collection import texts

    return texts.explain(error)


def _refused(reply_token):
    _text(reply_token, _("This account cannot record collections"))


@contextlib.contextmanager
def _guarded(reply_token):
    """服务端拒了（规则不过、没权限）就照码回一句；其余异常照常抛（webhook 记失败）。"""
    try:
        yield
    except contracts.CollectionError as error:
        _text(reply_token, _explain(error))
    except frappe.PermissionError:
        _refused(reply_token)


# ── 入口 ────────────────────────────────────────────────────────────────────


def start(line_user_id, user, reply_token):
    """富菜单「收款」/「เมนู」里的收款按钮。"""
    if not guard.has(WRITE, user=user):
        return _refused(reply_token)
    with acting(user), _guarded(reply_token):
        rows = _service().companies_for()
        if not rows:
            return _text(reply_token, _("No company is available for this account"))
        payload = {"step": "company", "companies": {row["name"]: row["company_name"] for row in rows}}
        if len(rows) == 1:
            payload.update(company=rows[0]["name"], step="customer")
            _save(line_user_id, payload)
            return _ask_customer(reply_token)
        _save(line_user_id, payload)
        _ask_company(reply_token, payload)


def answer(line_user_id, user, data, reply_token, event_id=""):
    """``a=col`` 的按钮点回来。"""
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    step, value = params.get("s") or "", params.get("v", "")
    if step == "start":
        return start(line_user_id, user, reply_token)
    if step == "cancel":
        _clear(line_user_id)
        return _text(reply_token, _("Cancelled"))
    if not guard.has(WRITE, user=user):
        return _refused(reply_token)
    with acting(user), _guarded(reply_token):
        if step == "ok":  # 卡片上的【确认】：带着卡片展示的那个版本
            return _release(line_user_id, params.get("n") or "", params.get("ver") or "", reply_token, event_id)
        if step == "wd":
            return _withdraw(line_user_id, params.get("n") or "", reply_token)
        payload = payload_of(line_user_id)
        if not payload.get("step") or _STEP_OF.get(step, step) != payload["step"]:
            return _text(reply_token, _("This option is no longer current"))
        handler = _ANSWERS.get(step)
        if handler is None:
            return _text(reply_token, _("This option is no longer current"))
        handler(line_user_id, payload, value, reply_token)


def text(line_user_id, user, typed, reply_token, event_id="") -> bool:
    """会话停在收款里时收到的文字（客户关键字、金额、确认词）；不在收款里返回 False。"""
    payload = payload_of(line_user_id)
    step = payload.get("step")
    if not step:
        return False
    if not guard.has(WRITE, user=user):
        _refused(reply_token)
        return True
    typed = str(typed or "").strip()
    with acting(user), _guarded(reply_token):
        if step == "card":
            if is_confirm(typed):
                _release(line_user_id, payload.get("name") or "", payload.get("version"), reply_token, event_id)
            else:
                _show_card(line_user_id, payload, _service().card(payload["name"]), reply_token)
        elif step == "company":
            _type_company(line_user_id, payload, typed, reply_token)
        elif step == "customer":
            _type_customer(line_user_id, payload, typed, reply_token)
        elif step in ("amount", "each"):
            _type_amount(line_user_id, payload, typed, reply_token)
        elif step == "voucher":
            _ask_voucher(reply_token)
        else:
            _repeat(line_user_id, payload, reply_token)
    return True


def is_confirm(typed) -> bool:
    return re.sub(r"\s+", "", str(typed or "")).casefold() in CONFIRM_WORDS


# ── 公司 ────────────────────────────────────────────────────────────────────


def _ask_company(reply_token, payload, note=""):
    from pearnly_erp_portal.integrations.line_flow import short_name

    rows = list(payload["companies"].items())
    if len(rows) > MAX_CHOICES:
        return _ask(reply_token, "\n".join(filter(None, (note, _("Choose the company"),
                                                         _("Type a word from the company name to find it")))))
    lines = [note] if note else []
    lines += [_("Choose the company")] + [f"{index}. {label}" for index, (_key, label) in enumerate(rows, 1)]
    _ask(reply_token, "\n".join(lines), [_button(f"{index}. {short_name(label)}", _data("company", key))
                                         for index, (key, label) in enumerate(rows, 1)])


def _pick_company(line_user_id, payload, value, reply_token):
    if value not in payload.get("companies", {}):
        return _text(reply_token, _("This option is no longer current"))
    payload.update(company=value, step="customer")
    _save(line_user_id, payload)
    _ask_customer(reply_token)


def _type_company(line_user_id, payload, typed, reply_token):
    from pearnly_erp_portal.integrations.line_flow import _fold

    wanted = _fold(typed)
    found = [(key, label) for key, label in payload["companies"].items() if wanted and wanted in _fold(label)]
    if len(found) == 1:
        return _pick_company(line_user_id, payload, found[0][0], reply_token)
    if not found:
        return _ask_company(reply_token, payload, note=_("No match for “{0}”").format(typed[:40]))
    if len(found) > MAX_CHOICES:
        return _ask(reply_token, _("Too many matches; type a few more letters to narrow it down"))
    _ask(reply_token, "\n".join([_("Found {0}. Tap the number to choose:").format(len(found))]
                                + [f"{index}. {label}" for index, (_key, label) in enumerate(found, 1)]),
         [_button(f"{index}. {label}", _data("company", key)) for index, (key, label) in enumerate(found, 1)])


# ── 客户 ────────────────────────────────────────────────────────────────────


def _ask_customer(reply_token, note=""):
    _ask(reply_token, "\n".join(filter(None, (note, _("Type the customer code or part of the name")))))


def _type_customer(line_user_id, payload, typed, reply_token):
    if not typed:
        return _ask_customer(reply_token)
    found = _service().customers(payload["company"], typed, limit=MAX_CHOICES)
    items = found["items"]
    exact = [row for row in items if str(row["code"]).casefold() == typed.casefold()]
    if len(exact) == 1:
        return _pick_customer(line_user_id, payload, exact[0]["code"], reply_token)
    if not items:
        return _ask_customer(reply_token, note=_("No match for “{0}”").format(typed[:40]))
    if int(found.get("total") or 0) > MAX_CHOICES:
        return _ask(reply_token, _("Too many matches; type a few more letters to narrow it down"))
    payload["offered"] = {row["code"]: row["name"] for row in items}
    _save(line_user_id, payload)
    _ask(reply_token, "\n".join([_("Found {0}. Tap the number to choose:").format(len(items))]
                                + [f"{index}. {row['code']} {row['name']}" for index, row in enumerate(items, 1)]),
         [_button(f"{index}. {row['name']}", _data("customer", row["code"])) for index, row in enumerate(items, 1)])


def _pick_customer(line_user_id, payload, value, reply_token, note=""):
    service = _service()
    if value not in (payload.get("offered") or {}) and not service.customers(payload["company"], value, limit=1)["items"]:
        return _text(reply_token, _("This option is no longer current"))
    found = service.open_invoices(payload["company"], value)
    name = (payload.get("offered") or {}).get(value) or next(
        (row["name"] for row in service.customers(payload["company"], value, limit=MAX_CHOICES)["items"]
         if row["code"] == value), value)
    payload.pop("offered", None)
    payload.update(customer=value, customer_name=name, picked=[], cash="0", methods=[], allocation=None,
                   due={row["docnum"]: row["available"] for row in found["items"][:contracts.MAX_INVOICES]},
                   dates={row["docnum"]: row["doc_date"] for row in found["items"][:contracts.MAX_INVOICES]},
                   order=[row["docnum"] for row in found["items"][:contracts.MAX_INVOICES]])
    if not payload["order"]:
        payload["step"] = "customer"
        _save(line_user_id, payload)
        if found.get("refresh") == "reading":
            return _ask(reply_token, _("Reading from Express"), [_button(_("Refresh"), _data("customer", value))])
        if found.get("refresh") == "offline":  # 读不到不是「没有」：照实说电脑没连
            return _ask_customer(reply_token, note=_("CC Desktop not connected"))
        if found.get("refresh") == "outdated":
            return _ask_customer(reply_token, note=_("CC Desktop needs an update"))
        return _ask_customer(reply_token, note=_("No open invoices"))
    payload["step"] = "invoices"
    _save(line_user_id, payload)
    _show_invoices(payload, reply_token, note=note)


def _answer_customer(line_user_id, payload, value, reply_token):
    _pick_customer(line_user_id, payload, value, reply_token)


# ── 发票（多选）─────────────────────────────────────────────────────────────


def _picked_total(payload) -> Decimal:
    return sum((_amount(payload["due"][docnum]) for docnum in payload.get("picked") or []), contracts.ZERO)


def _show_invoices(payload, reply_token, note=""):
    from pearnly_erp_portal.integrations import line_collection_cards as cards

    messages = [cards.invoices(payload)]
    if note:
        messages.insert(0, {"type": "text", "text": note})
    _send(reply_token, messages)


def _answer_invoice(line_user_id, payload, value, reply_token):
    if value not in payload.get("due", {}):
        return _text(reply_token, _("This option is no longer current"))
    picked = list(payload.get("picked") or [])
    if value in picked:
        picked.remove(value)
    else:
        picked.append(value)
    payload["picked"] = [docnum for docnum in payload["order"] if docnum in picked]
    _save(line_user_id, payload)
    _show_invoices(payload, reply_token)


def _answer_invoices_done(line_user_id, payload, value, reply_token):
    if not payload.get("picked"):
        return _show_invoices(payload, reply_token, note=_("Choose at least one invoice"))
    payload.update(cash="0", methods=[], allocation=None)
    _ask_method(line_user_id, payload, reply_token)


# ── 收款方式与金额 ───────────────────────────────────────────────────────────


def _paid(payload) -> Decimal:
    return _amount(payload.get("cash") or 0) + sum((_amount(row["amount"]) for row in payload.get("methods") or []),
                                                   contracts.ZERO)


def _rest(payload) -> Decimal:
    return _picked_total(payload) - _paid(payload)


def _catalog(payload) -> dict:
    found = payload.get("catalog")
    if found is None:
        found = payload["catalog"] = _service().methods(payload["company"])
    return found


def _unused(payload) -> list:
    """还能加的方式：``[(值, 名称)]``（现金一次、每种转账一次）。"""
    found, used = _catalog(payload), {row["prefix"] for row in payload.get("methods") or []}
    out = [(CASH, _("Cash"))] if found.get("cash") and not _amount(payload.get("cash") or 0) else []
    return out + [(row["prefix"], row["name"]) for row in found.get("transfers") or [] if row["prefix"] not in used]


def _status_line(payload) -> str:
    head = _("{0} selected · total {1}").format(len(payload.get("picked") or []), money(_picked_total(payload)))
    if _paid(payload):
        head += "\n" + _("Received {0} · due {1}").format(money(_paid(payload)), money(_rest(payload)))
    return head


def _ask_method(line_user_id, payload, reply_token):
    options = _unused(payload)
    payload.update(step="method", offered=[key for key, _label in options])
    _save(line_user_id, payload)
    if not options:
        return _text(reply_token, _("This collection method is not available"))
    _ask(reply_token, _status_line(payload) + "\n" + _("Choose the collection method"),
         [_button(label, _data("method", key)) for key, label in options])


def _answer_method(line_user_id, payload, value, reply_token):
    if value not in (payload.get("offered") or []):
        return _text(reply_token, _("This option is no longer current"))
    payload.pop("offered", None)
    payload.update(step="amount", pending=value)
    _save(line_user_id, payload)
    _ask_amount(payload, reply_token)


def _pending_name(payload) -> str:
    pending = payload.get("pending")
    if pending == CASH:
        return _("Cash")
    if pending == DISCOUNT:
        return _("Discount")
    return next((row["name"] for row in _catalog(payload).get("transfers") or [] if row["prefix"] == pending), pending)


def _ask_amount(payload, reply_token, note=""):
    rest = money(_rest(payload))
    lines = [note] if note else [_status_line(payload)]
    if payload.get("pending") == DISCOUNT:
        lines.append(_("Enter the discount amount"))
    else:
        lines.append(_("Enter the amount for {0}").format(_pending_name(payload)))
    _ask(reply_token, "\n".join(lines), [_button(rest, _data("amount", rest.replace(",", "")))])


def _type_amount(line_user_id, payload, typed, reply_token):
    if payload["step"] == "each":
        return _each_amount(line_user_id, payload, typed, reply_token)
    try:
        amount = contracts.money(typed, allow_zero=False)
    except contracts.CollectionError:
        return _ask_amount(payload, reply_token, note=_("Enter a valid amount"))
    rest = _rest(payload)
    if amount > rest:
        return _ask_amount(payload, reply_token,
                           note=_("More than the amount due {0}. Enter the amount again").format(money(rest)))
    pending = payload.pop("pending", "")
    if pending == CASH:
        payload["cash"] = contracts.text(amount)
    elif pending == DISCOUNT:
        payload["methods"].append({"prefix": _catalog(payload)["discount"]["prefix"], "amount": contracts.text(amount),
                                   "role": contracts.DISCOUNT, "voucher": ""})
    else:
        payload["methods"].append({"prefix": pending, "amount": contracts.text(amount), "role": contracts.TRANSFER,
                                   "voucher": ""})
        payload.update(step="voucher", voucher_for=pending)
        _save(line_user_id, payload)
        return _ask_voucher(reply_token)
    _after_amounts(line_user_id, payload, reply_token)


def _answer_amount(line_user_id, payload, value, reply_token):
    _type_amount(line_user_id, payload, value, reply_token)


def _after_amounts(line_user_id, payload, reply_token):
    """收到的够了就建草稿出卡；不够问【再加一种方式】【折扣】【下次再收】。"""
    rest = _rest(payload)
    if rest == 0:
        payload["allocation"] = [{"docnum": docnum, "amount": payload["due"][docnum]} for docnum in payload["picked"]]
        return _draft(line_user_id, payload, reply_token)
    options = []
    if _unused(payload):
        options.append(_button(_("Add a method"), _data("more", "method")))
    if _catalog(payload).get("discount") and not any(row.get("role") == contracts.DISCOUNT
                                                       for row in payload["methods"]):
        options.append(_button(_("Discount"), _data("more", "discount")))
    options.append(_button(_("Collect later"), _data("more", "later")))
    payload["step"] = "more"
    _save(line_user_id, payload)
    _ask(reply_token, _status_line(payload), options)


def _answer_more(line_user_id, payload, value, reply_token):
    if value == "method":
        return _ask_method(line_user_id, payload, reply_token)
    if value == "discount" and _catalog(payload).get("discount"):
        payload.update(step="amount", pending=DISCOUNT)
        _save(line_user_id, payload)
        return _ask_amount(payload, reply_token)
    if value == "later":
        return _ask_split(line_user_id, payload, reply_token)
    _text(reply_token, _("This option is no longer current"))


# ── 部分收款：哪张留余额 ─────────────────────────────────────────────────────


def _ask_split(line_user_id, payload, reply_token):
    picked, paid = payload["picked"], _paid(payload)
    if len(picked) == 1:
        payload["allocation"] = [{"docnum": picked[0], "amount": contracts.text(paid)}]
        return _draft(line_user_id, payload, reply_token)
    total = _picked_total(payload)
    # 留余额的那张：其余收满后还要收到钱（> 0）
    keeps = [docnum for docnum in picked if total - _amount(payload["due"][docnum]) < paid]
    options = [_button(_("Oldest first"), _data("split", OLDEST))]
    if len(keeps) <= MAX_CHOICES - 2:
        options += [_button(docnum, _data("split", docnum)) for docnum in keeps]
    options.append(_button(_("Enter each"), _data("split", EACH)))
    payload.update(step="split", offered=[OLDEST, EACH, *keeps])
    _save(line_user_id, payload)
    _ask(reply_token, _status_line(payload) + "\n" + _("Which invoice keeps the balance?"), options)


def _answer_split(line_user_id, payload, value, reply_token):
    if value not in (payload.get("offered") or []):
        return _text(reply_token, _("This option is no longer current"))
    payload.pop("offered", None)
    picked, paid = payload["picked"], _paid(payload)
    if value == EACH:
        payload.update(step="each", each={}, at=0)
        _save(line_user_id, payload)
        return _ask_each(payload, reply_token)
    if value == OLDEST:
        rows = [{"docnum": docnum, "doc_date": payload["dates"].get(docnum) or "", "available": payload["due"][docnum]}
                for docnum in picked]
        allocation = contracts.allocate(rows, paid)
    else:
        others = [{"docnum": docnum, "amount": _amount(payload["due"][docnum])} for docnum in picked if docnum != value]
        allocation = [*others, {"docnum": value, "amount": paid - sum((row["amount"] for row in others), contracts.ZERO)}]
    payload["allocation"] = [{"docnum": row["docnum"], "amount": contracts.text(row["amount"])} for row in allocation]
    _draft(line_user_id, payload, reply_token)


def _ask_each(payload, reply_token, note=""):
    docnum = payload["picked"][payload["at"]]
    due = _amount(payload["due"][docnum])
    left = _paid(payload) - sum((_amount(value) for value in payload["each"].values()), contracts.ZERO)
    choices = [money(value) for value in sorted({due, max(contracts.ZERO, min(due, left)), contracts.ZERO}, reverse=True)]
    lines = [note] if note else [_("Received {0} · due {1}").format(money(_paid(payload)), money(_rest(payload)))]
    lines += [f"{docnum} · " + _("Due {0}").format(money(due)), _("Enter the amount for {0}").format(docnum)]
    _ask(reply_token, "\n".join(lines), [_button(value, _data("each", value.replace(",", ""))) for value in choices])


def _each_amount(line_user_id, payload, typed, reply_token):
    docnum = payload["picked"][payload["at"]]
    try:
        amount = contracts.money(typed)
    except contracts.CollectionError:
        return _ask_each(payload, reply_token, note=_("Enter a valid amount"))
    due = _amount(payload["due"][docnum])
    if amount > due:
        return _ask_each(payload, reply_token,
                         note=_("More than the amount due {0}. Enter the amount again").format(money(due)))
    payload["each"][docnum] = contracts.text(amount)
    payload["at"] += 1
    if payload["at"] < len(payload["picked"]):
        _save(line_user_id, payload)
        return _ask_each(payload, reply_token)
    entered = sum((_amount(value) for value in payload["each"].values()), contracts.ZERO)
    if entered != _paid(payload) or not entered:
        payload.update(each={}, at=0)
        _save(line_user_id, payload)
        return _ask_each(payload, reply_token, note=_("Total {0} does not match {1}").format(
            money(entered), money(_paid(payload))))
    payload["allocation"] = [{"docnum": key, "amount": value} for key, value in payload["each"].items()
                             if _amount(value) > 0]
    payload.pop("each", None)
    payload.pop("at", None)
    _draft(line_user_id, payload, reply_token)


def _answer_each(line_user_id, payload, value, reply_token):
    _each_amount(line_user_id, payload, value, reply_token)


# ── 凭证 ────────────────────────────────────────────────────────────────────


def _ask_voucher(reply_token):
    _ask(reply_token, _("Send a photo of the transfer slip"))


def wants_voucher(line_user_id) -> bool:
    return payload_of(line_user_id).get("step") == "voucher"


def receive_voucher(line_user_id, user, message, reply_token):
    """会话停在「发转账凭证」时收到的图片 / PDF：webhook 里不下载（LINE 取图慢，回调里同步下载会被 LINE 断开），
    排后台取图、存凭证，再用这次的回复令牌接着问下一步。"""
    if not guard.has(WRITE, user=user):
        return _refused(reply_token)
    payload = payload_of(line_user_id)
    kind = message.get("type") or ""
    filename = message.get("fileName") or f"line-{message.get('id') or 'voucher'}.{'jpg' if kind == 'image' else 'pdf'}"
    line.start_loading(line_user_id, 30)
    frappe.enqueue("pearnly_erp_portal.integrations.line_collection.store_voucher", queue="short",
                   enqueue_after_commit=True, line_user_id=line_user_id, user=user, message_id=message.get("id") or "",
                   filename=filename, prefix=payload.get("voucher_for") or "", reply_token=reply_token,
                   language=getattr(frappe.local, "lang", None) or "")


def store_voucher(line_user_id, user, message_id, filename, prefix, reply_token="", language=""):
    """（后台）从 LINE 取凭证、按绑定的账号存成私有凭证（`service.store_voucher`），挂到正在等凭证的那种转账上。"""
    if line_binding.user_of(line_user_id) != user:
        return
    previous_lang = getattr(frappe.local, "lang", None)
    if language:
        frappe.local.lang = language
    try:
        with acting(user):
            payload = payload_of(line_user_id)
            if payload.get("step") != "voucher" or payload.get("voucher_for") != prefix:
                return  # 等凭证那一步已经过去（取消了、换了一笔）
            try:
                content = line.download_message_content(message_id)
                stored = _service().store_voucher(filename, content) if content else None
            except Exception:
                frappe.db.rollback()
                frappe.log_error(title="ERP LINE collection voucher failed", message=frappe.get_traceback()[-1500:])
                stored = None
            if not stored:
                return line.reply_or_push(line_user_id, [{"type": "text", "text": _("Could not store this document")}],
                                          reply_token)
            with background(line_user_id, reply_token), _guarded(reply_token):
                if payload.get("name"):  # 卡片上缺凭证：补进草稿
                    found = _service().card(payload["name"])
                    card = _service().set_voucher(payload["name"], found["version"], prefix, stored["file"])
                    return _show_card(line_user_id, payload, card, reply_token)
                for row in payload["methods"]:
                    if row["prefix"] == prefix and not row.get("voucher"):
                        row["voucher"] = stored["file"]
                payload.pop("voucher_for", None)
                _after_amounts(line_user_id, payload, reply_token)
    finally:
        frappe.local.lang = previous_lang


# ── 草稿与确认卡 ─────────────────────────────────────────────────────────────


def _draft_methods(payload) -> list:
    return [{"prefix": row["prefix"], "amount": row["amount"], "voucher": row.get("voucher") or ""}
            for row in payload["methods"]]


def _draft(line_user_id, payload, reply_token):
    try:
        card = _service().create_draft(payload["company"], payload["customer"], payload["allocation"],
                                       cash=payload.get("cash") or 0, methods=_draft_methods(payload), source="line")
    except contracts.CollectionError as error:
        # 别人刚收了同一张、余额变了：从发票那一步按此刻的余额重来
        return _pick_customer(line_user_id, payload, payload["customer"], reply_token, note=_explain(error))
    _show_card(line_user_id, payload, card, reply_token)


def _show_card(line_user_id, payload, card, reply_token, note=""):
    from pearnly_erp_portal.integrations import line_collection_cards as cards

    state = {"step": "card", "company": card["company"], "customer": card["customer"], "name": card["name"],
             "version": card["version"]}
    messages = [cards.confirm(card, edit_url=edit_url(card["name"]))]
    if card.get("problem") == contracts.VOUCHER_MISSING:
        state.update(step="voucher", voucher_for=(card.get("detail") or {}).get("prefix") or "")
        messages.append({"type": "text", "text": _("Send a photo of the transfer slip"),
                         "quickReply": {"items": [_cancel()]}})
    elif card.get("problem"):
        messages.insert(0, {"type": "text", "text": _explain(contracts.CollectionError(card["problem"],
                                                                                         card.get("detail")))})
    if note:
        messages.insert(0, {"type": "text", "text": note})
    _save(line_user_id, state)
    _send(reply_token, messages)


def edit_url(name) -> str:
    from pearnly_core.account import line_login

    return line_login.entry_url(EDIT_ROUTE + name)


def _repeat(line_user_id, payload, reply_token):
    """停在按钮那几步时打了别的字：照这一步再问一次。"""
    step = payload["step"]
    if step == "invoices":
        return _show_invoices(payload, reply_token)
    if step == "method":
        return _ask_method(line_user_id, payload, reply_token)
    if step == "more":
        return _after_amounts(line_user_id, payload, reply_token)
    if step == "split":
        return _ask_split(line_user_id, payload, reply_token)
    _text(reply_token, _("This option is no longer current"))


# ── 放行、撤回、结果 ─────────────────────────────────────────────────────────


def _release(line_user_id, name, version, reply_token, event_id):
    result = _service().release(name, version, "line:" + str(event_id or ""))
    status = result["status"]
    if status == "changed":
        found = payload_of(line_user_id)
        return _show_card(line_user_id, found, _service().card(name), reply_token,
                          note=_("The collection changed. Check it again"))
    if status == "not_draft":
        return _text(reply_token, _status_text(result["card"]))
    if status == "invalid":
        card = _service().card(name)
        code, detail = result.get("code"), result.get("detail") or {}
        available = detail.get("available") if isinstance(detail, dict) else None
        if code == contracts.OVER_COLLECTED and isinstance(available, dict):
            # 11-12 三星实测：别的收款员刚收了同一张——照实说「已被收款」与每张此刻的余额，不说「请重新输入」
            note = "\n".join([_("Already collected. Current balance:")]
                             + [f"{docnum} {money(value)}" for docnum, value in available.items()])
        else:
            note = _explain(contracts.CollectionError(code, detail))
        if card.get("problem") == code:  # 同一个原因只说一遍
            if code == contracts.VOUCHER_MISSING:
                note = ""  # 缺凭证由卡片接着要照片（步骤跟着走），不另说
            else:
                card = {**card, "problem": ""}
        return _show_card(line_user_id, payload_of(line_user_id), card, reply_token, note=note)
    if payload_of(line_user_id).get("name") == name:
        _clear(line_user_id)
    _wait_on(name)
    line.start_loading(line_user_id, 30)
    frappe.enqueue("pearnly_erp_portal.integrations.line_collection.await_result", queue="short",
                   enqueue_after_commit=True, name=name, line_user_id=line_user_id, user=frappe.session.user,
                   reply_token=reply_token, language=getattr(frappe.local, "lang", None) or "")


def _withdraw(line_user_id, name, reply_token):
    view = _service().withdraw(name)
    if payload_of(line_user_id).get("name") == name:
        _clear(line_user_id)
    _text(reply_token, _status_text(view))


def _status_text(view) -> str:
    status = view.get("status") or ""
    if status == contracts.WRITTEN:
        return _("Written to Express: {0}").format(view.get("docnum") or "")
    if status == contracts.FAILED:
        return _failed_text(view)
    label = _(f"erp.collection.status.{status}") if status in contracts.STATUSES else status
    return f"{view.get('name') or ''} · {label}"


def _failed_text(view) -> str:
    from pearnly_core.cc_desktop.wire import ack_rules

    reason = str(view.get("reason") or "")
    if ack_rules.error_code(reason) in BALANCE_CODES and view.get("balances"):
        lines = [_("Already collected. Current balance:")]
        lines += [f"{docnum} {money(value)}" for docnum, value in (view.get("balances") or {}).items()]
        return "\n".join(lines)
    return _("Not written to Express: {0}").format(re.sub(r"^\[[A-Z_]+\]\s*", "", reason) or view.get("status"))


def _result_messages(view) -> list:
    status = view.get("status")
    head = f"{view.get('customer_name') or ''} · {money(view.get('total'))}"
    if status in (contracts.WRITTEN, contracts.FAILED, contracts.WITHDRAWN):
        return [{"type": "text", "text": _status_text(view) + "\n" + head}]
    # 还没写成：已记录、待写入；没被电脑领走前可撤回
    return [{"type": "text", "text": _("Recorded · pending write") + "\n" + head,
             "quickReply": {"items": [_button(_("Withdraw"), _data("wd", n=view["name"]))]}}]


FINAL = (contracts.WRITTEN, contracts.FAILED, contracts.WITHDRAWN)


def _state():
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _wait_on(name):
    store = _state()
    store.set(store.make_key(_WAIT_KEY + name), "1", ex=WAIT_SECONDS + 40)


def _wait_off(name):
    store = _state()
    store.delete(store.make_key(_WAIT_KEY + name))


def waiting(name) -> bool:
    store = _state()
    return bool(store.get(store.make_key(_WAIT_KEY + name)))


def claim(name, view) -> bool:
    """同一个结果只告诉一次（后台等结果的回复与回执后的推送谁先到谁发）。"""
    store = _state()
    key = store.make_key(f"{_TOLD_KEY}{name}:{view.get('status')}:{view.get('docnum') or ''}:{view.get('job') or ''}")
    return bool(store.set(key, "1", nx=True, ex=7 * 86400))


def _read(name) -> dict:
    frappe.db.commit()  # 结束本事务，读到回执刚提交的状态
    return _service().status(name)


def await_result(name, line_user_id, user, reply_token="", language=""):
    """（后台）放行后最多等 :data:`WAIT_SECONDS` 秒：写成 / 失败了用回复说结果；还没写成回「已记录 · 待写入」，结果由
    :func:`notify` 之后推送。"""
    import time

    previous_lang = getattr(frappe.local, "lang", None)
    if language:
        frappe.local.lang = language
    try:
        with acting(user):
            deadline = time.monotonic() + WAIT_SECONDS
            view = _read(name)
            while view["status"] not in FINAL and time.monotonic() < deadline:
                time.sleep(1)
                view = _read(name)
            if view["status"] not in FINAL:
                _wait_off(name)
                view = _read(name)  # 撤下等待之后再读一次：回执恰好在这之间到的，照样在这里回
            else:
                _wait_off(name)
            if view["status"] in FINAL and not claim(name, view):
                return
            line.reply_or_push(line_user_id, _result_messages(view), reply_token)
    finally:
        frappe.local.lang = previous_lang


def notify(name):
    """（回执提交之后）LINE 录的收款写成 / 失败：后台还在等就交给它回；否则推送给录入的收款员本人。"""
    doc = frappe.db.get_value(DOCTYPE, name, ["collector", "source", "status", "docnum", "job"], as_dict=True)
    if not doc or doc.source != "line" or doc.status not in (contracts.WRITTEN, contracts.FAILED):
        return
    if waiting(name):
        return
    bindings = frappe.get_all("Pearnly LINE Binding", filters={"user": doc.collector, "status": "active"},
                              pluck="line_user_id", limit_page_length=1)
    if not bindings:
        return
    previous_lang = getattr(frappe.local, "lang", None)
    # 推送用这个人手机上 LINE 的语言（同收单卡片）；取不到用站点语言（`intake.channel_language` 同一口径）
    frappe.local.lang = line.user_language(bindings[0], default=frappe.db.get_single_value("System Settings", "language")
                                           or "th")
    try:
        with acting(doc.collector):
            view = _service().status(name)
            if view["status"] in FINAL and claim(name, view):
                line.push(bindings[0], _result_messages(view))
    finally:
        frappe.local.lang = previous_lang


# 按钮的步名 → 会话停在的那一步（勾发票的每一下都停在「发票」这一步）
_STEP_OF = {"inv": "invoices"}

_ANSWERS = {
    "company": _pick_company,
    "customer": _answer_customer,
    "inv": _answer_invoice,
    "invoices": _answer_invoices_done,
    "method": _answer_method,
    "amount": _answer_amount,
    "more": _answer_more,
    "split": _answer_split,
    "each": _answer_each,
}
