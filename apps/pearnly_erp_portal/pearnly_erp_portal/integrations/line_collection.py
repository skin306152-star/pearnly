"""LINE 里录收款（富菜单第 4 格「收款」，评估 docs/work/2026-11-11-collection-receipt-assessment.md §6、§12-1）。

一步一问（LINE 原生快捷回复 + Flex），每一步只认「当前正在问的那一步」，旧按钮点了不生效：

1. 选公司（只有一家时跳过；这家同期两本账套、没选过时接着选一次，按公司 × 会计年度记住，与 ERP 单据同一份记忆，
   `collection.service.choose_book`）→ 2. 打客户编号或名称的一部分（最多列 12 个，多了请再打几个字）→ 3. 勾发票（Flex 列表可多选、
   显示合计；余额已扣别人预留）→ 4. 收款方式（现金 + 这本账套能收钱的转账类型）→ 5. 金额 → 6. 转账发一张凭证照片（每笔一张）
   → 不够时【再加一种方式】【折扣】【下次再收】（下次再收：选哪张留余额、或逐张输入）；超过未收的请重输 → 7. 确认卡
   （公司·账套·客户·各张本次收·方式·折扣·合计）带【确认】【编辑】【撤回】。
2. 放行：【确认】按钮或打「ยืนยัน / 确认 / ok」（大小写、空格不论）→ `collection.service.release`（卡片展示的版本 + 这次 LINE
   事件号作同意）；卡片之后改过回 ``changed`` 就重出卡片。【编辑】经 `line_login.entry_url` 打开网页原生弹窗（只改这张草稿）。
   卡片有效期照 DMS（共享笔记「卡片有效期规则」A 节）：每张卡一个一次性确认号（`line_session.consume_nonce`），【确认】【撤回】都要它；
   出新卡、重开一笔、取消时旧卡失效、没放行的草稿作废；卡自出卡起 :data:`CARD_MINUTES` 分钟有效；同一张卡再点不回话，其余失效回
   :data:`EXPIRED`。【取消】只认最新那条消息上的（步号 ``turn``）。单据日期按放行当天；今天不在这家任何一本账套的期间里时，选公司后先问收款日期（账套跟着日期走），照选的写。
3. 结果（用户 11-22，`pearnly_core.line_ui.receipt`）：放行后先显示三个点，收款回执落库（`collection.push` → :func:`notify`）时用这次的
   回复令牌回「成功 · RE 号」/「失败 · 原因」（写前被拒退回草稿的，原卡带原因还回）；等不到回「录入中」（带【查看结果】【撤回】）。
   之后只用回复：点【查看结果】或下次进「收款」时告诉（同一笔一次），不推送。选客户、勾完发票时电脑还在读（未收清发票、收款方式）
   同样先三个点，读好了接着回下一步；等不到回原来那句「正在读取」+【刷新】/【查看结果】。

规则、权限、金额全部在 `collection.service`（LINE 只调它，不另写一份）；这里只管问与记。身份是绑定的 ERP 账号。
"""
import contextlib
import re
import secrets
from decimal import Decimal
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line, line_binding, line_session
from pearnly_core.line_ui import receipt as line_receipt
from pearnly_core.line_ui.labels import choice_labels

from pearnly_erp_portal.access import guard
from pearnly_erp_portal.collection import contracts
from pearnly_erp_portal.integrations import line_out

STATE = "erp_collection"
ACTION = "col"
TTL_MINUTES = 30
# 确认卡（含卡上缺凭证、等补照片那一步）自出卡起的有效期，与 DMS 订车预览一致（`line_session._STATE_TTL_MINUTES`）
CARD_MINUTES = 120
# 卡片失效（被新卡替换、过期、会话没了）统一这一句，与 DMS 同一口径（`dms.card.TXT_EXPIRED`）
EXPIRED = "This item has expired. Please start again."
STALE = "This option is no longer current"
WRITE = "collection.write"
DOCTYPE = "ERP Collection"
CASH = "@cash"
DISCOUNT = "@discount"
OLDEST, EACH = "@oldest", "@each"
# 快捷回复一次最多 13 个按钮：选项最多 12 个，留一个给「取消」
MAX_CHOICES = 12
# Flex 发票列表：每张气泡 10 张，最多 5 张气泡（一笔收款最多 `contracts.MAX_INVOICES` 张）
PER_BUBBLE = 10
EDIT_ROUTE = "/desk/pearnly-collections/"  # 11-14 起「收款」一页（旧的 pearnly-my-collections/<记录> 仍转过来）
# 打字确认（用户手打的词不翻译；四种语言 + ok）。比较时去掉空白、不分大小写
CONFIRM_WORDS = frozenset(("ยืนยัน", "确认", "確認", "ok", "confirm"))
# 回执里这两种「不能收」带每张发票此刻的余额（`collection.push`）；没带余额的（如服务品现金制）照原因说
BALANCE_CODES = contracts.UNCOLLECTABLE  # CC 回的原样小写码（11-14 前写成大写、对不上）
_TOLD_KEY = "pearnly:erp:collection:line-told:"
_USED_KEY = "pearnly:erp:collection:line-used:"     # 已消费的确认号：同卡再点不回话
_PENDING_KEY = "pearnly:erp:collection:line-pending:"  # 「录入中」那条的撤回号（一笔一个）
USED_SECONDS = 86400


def _service():
    from pearnly_erp_portal.collection import service

    return service


# ── 会话 ────────────────────────────────────────────────────────────────────


def payload_of(line_user_id) -> dict:
    found = line_session.get_session(line_user_id) or {}
    payload = dict(found.get("payload") or {}) if found.get("state") == STATE else {}
    frappe.local.pearnly_collection_turn = payload.get("turn") or ""
    return payload


def active(line_user_id) -> bool:
    return bool(payload_of(line_user_id).get("step"))


def _save(line_user_id, payload, ttl_minutes=TTL_MINUTES):
    """存这一步。每存一次换一个步号（``turn``）：【取消】只认最新那条消息上的（:func:`_cancel`）。"""
    payload["turn"] = secrets.token_hex(4)
    frappe.local.pearnly_collection_turn = payload["turn"]
    line_session.set_session(line_user_id, STATE, payload, ttl_minutes=ttl_minutes)


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


@contextlib.contextmanager
def speaking(line_user_id, language=""):
    """后台任务里回给收款员的话用他的语言：webhook 那一刻定下的（`handlers.handle` 按手机上 LINE 的语言），没带就现取
    （`line.user_language`，取不到退回绑定账号的语言、站点语言）。在换身份（:func:`acting`）之后设：后台任务的默认语言是站点语言（泰文），
    不能让它漏到回复里。做完还原。

    锁定版 Frappe 的 ``_()`` 在后台任务里第一次调用时按排任务的会话用户定语言（``frappe.local.job.user``；webhook 排的任务是 Guest →
    站点语言），把这里设的盖掉（11-13 真机取证：凭证后那张卡变泰文）。设语言时同时告诉它这一任务的语言已定（``job.lang_resolved``）。"""
    from pearnly_core.execution import language as job_language

    with job_language.use(language or line.user_language(
            line_user_id, default=frappe.db.get_single_value("System Settings", "language") or "th")) as lang:
        yield lang


# ── 消息 ────────────────────────────────────────────────────────────────────


def _data(step, value="", **extra) -> str:
    return urlencode({"a": ACTION, "s": step, **({"v": value} if value != "" else {}), **extra})


def _button(label, data):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data, "displayText": label}}


def _cancel():
    return _button(_("Cancel"), _data("cancel", getattr(frappe.local, "pearnly_collection_turn", "") or ""))


def _send(reply_token, messages):
    """回给这个人。后台任务里（:func:`background`）攒到最后用回复令牌发一次，令牌过期了就推送。
    进「收款」时上次之后写成 / 失败的那几笔（:func:`_told_first`）放在这次回复的最前面。"""
    prefix = getattr(frappe.local, "pearnly_collection_prefix", None) or []
    if prefix:
        frappe.local.pearnly_collection_prefix = []
        messages = [*prefix, *messages]
    box = getattr(frappe.local, "pearnly_collection_capture", None)
    if box is not None:
        box.extend(messages)  # 拼「等结果」的回复（:func:`_captured`）：不发，交给 `line_receipt`
        return True
    to = getattr(frappe.local, "pearnly_collection_to", "")
    if to:
        return line_out.reply_or_push(to, messages, reply_token)
    return line_out.reply(reply_token, messages)


@contextlib.contextmanager
def _captured():
    """块里要回给这个人的消息只收下不发（:func:`_send`）：等结果的兜底与结果由 `line_receipt` 用回复令牌发。"""
    previous = getattr(frappe.local, "pearnly_collection_capture", None)
    box = frappe.local.pearnly_collection_capture = []
    try:
        yield box
    finally:
        frappe.local.pearnly_collection_capture = previous


def _resuming() -> bool:
    return bool(getattr(frappe.local, "pearnly_collection_resuming", False))


class _StillReading(Exception):
    """读好了接着回下一步（:func:`resume_step`）时还在读：这次不回，接着等。"""


def _read_key(line_user_id) -> str:
    return f"erp:col-read:{line_user_id}"


def _wait_read(line_user_id, reply_token, topics, step, value, fallback):
    """电脑还在读（未收清发票、收款方式）：先三个点，读好了接着回这一步的下一问；等不到回 ``fallback`` 原句（用户 11-22）。
    接着回的那一次（:func:`resume_step`）又碰到还在读：不再起一轮，抛 :class:`_StillReading` 接着等。"""
    if _resuming():
        raise _StillReading()
    with _captured() as messages:
        fallback()
    if not line_receipt.start(_read_key(line_user_id), line_user_id, reply_token, messages,
                              resume="pearnly_erp_portal.integrations.line_collection.resume_step",
                              args={"line_user_id": line_user_id, "step": step, "value": value}, topics=topics):
        _send(reply_token, messages)


def resume_step(line_user_id, step, value):
    """（`line_receipt` 的短任务，按收款员的身份与语言）读好了：接着走这一步，回它的消息；还在读、或会话已不在这一步回 None。"""
    payload = payload_of(line_user_id)
    wanted = {"customer": ("customer",), "invoices": ("invoices",)}.get(step) or ()
    if payload.get("step") not in wanted:
        return None
    previous = getattr(frappe.local, "pearnly_collection_resuming", False)
    frappe.local.pearnly_collection_resuming = True
    try:
        with _captured() as messages, _guarded(""):
            _ANSWERS[step](line_user_id, payload, value, "")
    except _StillReading:
        return None
    finally:
        frappe.local.pearnly_collection_resuming = previous
    return messages or None


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
    with acting(user), _guarded(reply_token), _told_first(user):
        _discard(payload_of(line_user_id).get("name"))  # 重开一笔：上一笔没放行的草稿作废，它的卡跟着失效
        rows = _service().companies_for()
        if not rows:
            return _text(reply_token, _("No company is available for this account"))
        payload = {"step": "company", "companies": {row["name"]: row["company_name"] for row in rows}}
        if len(rows) == 1:
            payload["company"] = rows[0]["name"]
            return _after_company(line_user_id, payload, reply_token)
        _save(line_user_id, payload)
        _ask_company(reply_token, payload)


def answer(line_user_id, user, data, reply_token, event_id=""):
    """``a=col`` 的按钮点回来。"""
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    step, value = params.get("s") or "", params.get("v", "")
    if step == "start":
        return start(line_user_id, user, reply_token)
    if not guard.has(WRITE, user=user):
        return _refused(reply_token)
    with acting(user), _guarded(reply_token):
        if step == "cancel":
            return _cancel_step(line_user_id, value, reply_token)
        if step == "ok":  # 卡片上的【确认】：只认当前这张卡的一次性确认号
            return _confirm_tap(line_user_id, params, reply_token, event_id)
        if step == "wd":
            return _withdraw_tap(line_user_id, params, reply_token)
        if step == "res":
            return _show_result(params.get("n") or "", reply_token, line_user_id)
        payload = payload_of(line_user_id)
        if not payload.get("step"):
            return _text(reply_token, _(EXPIRED))  # 会话过期 / 已结束：不是「旧步骤」
        handler = _ANSWERS.get(step)
        if handler is None or _STEP_OF.get(step, step) != payload["step"]:
            return _repeat(line_user_id, payload, reply_token)  # 旧步骤的按钮：吃下、照当前这一步再问（同 DMS）
        handler(line_user_id, payload, value, reply_token)


def _cancel_step(line_user_id, turn, reply_token):
    """【取消】只认最新那条消息上的（步号对得上）；取消时这一笔没放行的草稿一并作废。旧消息上的照当前这一步再问，不动正在录的。"""
    payload = payload_of(line_user_id)
    if not payload.get("step"):
        return _text(reply_token, _(EXPIRED))
    if not turn or turn != payload.get("turn"):
        return _repeat(line_user_id, payload, reply_token)
    _discard(payload.get("name"))
    _clear(line_user_id)
    _text(reply_token, _("Cancelled"))


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
                _confirm(line_user_id, payload.get("nonce") or "", payload.get("name") or "", reply_token, event_id)
            else:
                _show_card(line_user_id, payload, _service().card(payload["name"]), reply_token)
        elif step == "company":
            _type_company(line_user_id, payload, typed, reply_token)
        elif step == "day":
            _type_day(line_user_id, payload, typed, reply_token)
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
                                                         _("Type the company name, tax ID or account set to find it")))))
    lines = [note] if note else []
    lines += [_("Choose the company")] + [f"{index}. {label}" for index, (_key, label) in enumerate(rows, 1)]
    labels = choice_labels([short_name(label) for _key, label in rows])
    _ask(reply_token, "\n".join(lines), [_button(text, _data("company", key)) for text, (key, _label) in zip(labels, rows)])


def _pick_company(line_user_id, payload, value, reply_token, note=""):
    if value not in payload.get("companies", {}):
        return _text(reply_token, _("This option is no longer current"))
    payload["company"] = value
    _after_company(line_user_id, payload, reply_token, note=note)


def _after_company(line_user_id, payload, reply_token, note="", day_note=""):
    """公司定了：今天（或已选的收款日期）不在这家任何一本账套的期间里，先问收款日期（`service.day_options`，账套跟着日期走）；
    这一天的账套定得下来就去找客户；同期两本、没选过先选一次（A10，11-12）。"""
    # ``note``：回显刚选的（公司名），挂在下一步的问句上；``day_note``：只挂在「再问收款日期」上（打的日期认不出 / 在期间外）
    service = _service()
    asked_day = service.day_options(payload["company"], payload.get("day"))
    if asked_day:
        payload.update(step="day", periods=asked_day["periods"][:MAX_CHOICES])
        payload.pop("day", None)
        _save(line_user_id, payload)
        return _ask_day(reply_token, payload, day_note or note)
    payload.pop("periods", None)
    asked = service.book_options(payload["company"], payload.get("day"))
    if asked:
        rows = asked["options"][:MAX_CHOICES]
        payload.update(step="book", books={row["code"]: row["name"] for row in rows},
                       book_short={row["code"]: row.get("short") or row["name"] for row in rows})
        _save(line_user_id, payload)
        return _ask_book(reply_token, payload, note=note)
    payload.pop("books", None)
    payload.pop("book_short", None)
    menu = service.menu_options(payload["company"], payload.get("day"))
    if menu:
        # 账本是 MR.ERP、收款导入菜单不止一个已关联的：选一次记住（同账套那一步的按钮选法，`service.choose_menu`）
        rows = menu["options"][:MAX_CHOICES]
        payload.update(step="menu", menus={row["code"]: row["name"] for row in rows})
        _save(line_user_id, payload)
        return _ask_menu(reply_token, payload, note=note)
    payload.pop("menus", None)
    payload.update(step="customer")
    _save(line_user_id, payload)
    _ask_customer(reply_token, note=note)
    service.warm(payload["company"], payload.get("day"))  # 先回问句再预热：整本读回请求排给电脑，选好客户时多半已读好


# ── 收款日期（今天不在任何一本账套的期间里时）──────────────────────────────────


def _ask_day(reply_token, payload, note=""):
    """问收款日期：列出各本可录的期间；快捷回复是各本本年段的最后一天（日期 + 账套短名），也可以直接打日期。"""
    from pearnly_erp_portal.collection import texts
    from pearnly_erp_portal.integrations.line_cards import _be_date

    rows = payload.get("periods") or []
    lines = [note] if note else []
    lines += [_("Collection Date"), texts.periods_text(rows, _be_date)]  # 佛历日 / 月 / 年，同卡片；打字也认这种写法
    _ask(reply_token, "\n".join(lines),
         [_button(f"{_be_date(row['last'])} {row.get('short') or ''}".strip()[:20], _data("day", row["last"])) for row in rows])


def _answer_day(line_user_id, payload, value, reply_token):
    payload["day"] = value
    _after_company(line_user_id, payload, reply_token)


def _type_day(line_user_id, payload, typed, reply_token):
    day = _service().parse_day(typed)
    if not day:
        return _ask_day(reply_token, payload, note=_("No match for “{0}”").format(typed[:40]))
    payload["day"] = day
    _after_company(line_user_id, payload, reply_token, day_note=_("No match for “{0}”").format(typed[:40]))


# ── 账套（同期两本时）───────────────────────────────────────────────────────


def _ask_book(reply_token, payload, note=""):
    """同名的副本与真账只有位置不同：列表写全「名称 · 位置」，按钮放序号与短名（账套目录名，撞了带年度；`service._with_short`）。"""
    rows = list(payload["books"].items())
    short = payload.get("book_short") or {}
    labels = choice_labels([short.get(key) or label for key, label in rows])
    _ask(reply_token, "\n".join([note] * bool(note) + [_("Choose the account set")] + [f"{index}. {label}" for index, (_key, label)
                                                                  in enumerate(rows, 1)]),
         [_button(text, _data("book", key)) for text, (key, _label) in zip(labels, rows)])


def _answer_book(line_user_id, payload, value, reply_token):
    if value not in (payload.get("books") or {}):
        return _text(reply_token, _("This option is no longer current"))
    _service().choose_book(payload["company"], value, payload.get("day"))
    _after_company(line_user_id, payload, reply_token)


def _ask_menu(reply_token, payload, note=""):
    rows = list(payload["menus"].items())
    labels = choice_labels([label for _key, label in rows])
    _ask(reply_token, "\n".join([note] * bool(note) + [_("Choose the MR.ERP collection menu")]
                                + [f"{index}. {label}" for index, (_key, label) in enumerate(rows, 1)]),
         [_button(text, _data("menu", key)) for text, (key, _label) in zip(labels, rows)])


def _answer_menu(line_user_id, payload, value, reply_token):
    if value not in (payload.get("menus") or {}):
        return _text(reply_token, _("This option is no longer current"))
    _service().choose_menu(payload["company"], value, payload.get("day"))
    _after_company(line_user_id, payload, reply_token)


def _type_company(line_user_id, payload, typed, reply_token):
    from pearnly_erp_portal.integrations import line_flow

    # 与采购 / 销售选公司同一份（`line_flow.match`）：名称、税号、账套目录名等，不分大小写包含（11-15 真机：按税号找不到）
    exact, found = line_flow.match(line_flow.company_rows(payload["companies"]), typed)
    labels = payload["companies"]
    # 打字选中的（税号、账套名、名称唯一命中）：下一问的第一行是选中的那家，点按钮的有按钮字可看（11-15 真机）
    if exact:
        return _pick_company(line_user_id, payload, exact, reply_token, note=labels.get(exact) or "")
    found = [(key, labels.get(key) or label) for key, label in found]
    if len(found) == 1:
        return _pick_company(line_user_id, payload, found[0][0], reply_token, note=found[0][1])
    if not found:
        return _ask_company(reply_token, payload, note=_("No match for “{0}”").format(typed[:40]))
    if len(found) > MAX_CHOICES:
        return _ask(reply_token, _("Too many matches; type a few more letters to narrow it down"))
    _ask(reply_token, "\n".join([_("Found {0}. Tap the number to choose:").format(len(found))]
                                + [f"{index}. {label}" for index, (_key, label) in enumerate(found, 1)]),
         [_button(text, _data("company", key)) for text, (key, _label) in zip(choice_labels([label for _key, label in found]), found)])


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
         [_button(text, _data("customer", row["code"])) for text, row in zip(choice_labels([row["name"] for row in items]), items)])


def _pick_customer(line_user_id, payload, value, reply_token, note=""):
    service = _service()
    if value not in (payload.get("offered") or {}) and not service.customers(payload["company"], value, limit=1)["items"]:
        return _text(reply_token, _("This option is no longer current"))
    found = service.open_invoices(payload["company"], value, payload.get("day"))
    name = (payload.get("offered") or {}).get(value) or next(
        (row["name"] for row in service.customers(payload["company"], value, limit=MAX_CHOICES)["items"]
         if row["code"] == value), value)
    payload.pop("offered", None)
    payload.update(customer=value, customer_name=name, picked=[], cash="0", methods=[], allocation=None,
                   due={row["docnum"]: row["available"] for row in found["items"][:contracts.MAX_INVOICES]},
                   dates={row["docnum"]: row["doc_date"] for row in found["items"][:contracts.MAX_INVOICES]},
                   # 抵减单（减项 / 定金，从合计里扣，列在发票下面）：单号 → 类型
                   kinds={row["docnum"]: row["rectyp"] for row in found["items"][:contracts.MAX_INVOICES]
                          if contracts.is_offset(row.get("rectyp"))},
                   order=[row["docnum"] for row in found["items"][:contracts.MAX_INVOICES]])
    if not payload["order"]:
        payload["step"] = "customer"
        _save(line_user_id, payload)
        if found.get("refresh") == "reading":
            # 先三个点，读好了接着出发票列表；等不到回「正在读取」（按账本说 MR.ERP / Express）+【刷新】
            return _wait_read(line_user_id, reply_token, service.reading_topics(payload["company"], payload.get("day"), value),
                              "customer", value, lambda: _ask(reply_token, service.reading_text(payload["company"]),
                                                              [_button(_("Refresh"), _data("customer", value))]))
        if found.get("refresh") == "offline":  # 读不到不是「没有」：照实说电脑没连
            return _ask_customer(reply_token, note=_("No computer connected"))
        if found.get("refresh") == "outdated":
            return _ask_customer(reply_token, note=_("CC Desktop needs an update"))
        return _ask_customer(reply_token, note=_("No open invoices"))
    payload["step"] = "invoices"
    _save(line_user_id, payload)
    _show_invoices(payload, reply_token, note=note)


def _answer_customer(line_user_id, payload, value, reply_token):
    _pick_customer(line_user_id, payload, value, reply_token)


# ── 发票（多选）─────────────────────────────────────────────────────────────


def _is_offset(payload, docnum) -> bool:
    return docnum in (payload.get("kinds") or {})


def _signed(payload, docnum, value) -> Decimal:
    """抵减单记负数（合计 = 发票 − 抵减）。"""
    return -_amount(value) if _is_offset(payload, docnum) else _amount(value)


def _picked_total(payload) -> Decimal:
    return sum((_signed(payload, docnum, payload["due"][docnum]) for docnum in payload.get("picked") or []), contracts.ZERO)


def _picked_invoices(payload) -> list:
    return [docnum for docnum in payload.get("picked") or [] if not _is_offset(payload, docnum)]


def _full(payload, docnums) -> list:
    return [{"docnum": docnum, "amount": payload["due"][docnum]} for docnum in docnums]


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


READING_CODES = ("express.masters_reading", "collection.mrerp_reading")  # 主档还在读（Express / MR.ERP）


def _answer_invoices_done(line_user_id, payload, value, reply_token):
    if not _picked_invoices(payload):
        return _show_invoices(payload, reply_token, note=_("Choose at least one invoice"))
    if _picked_total(payload) < 0:
        return _show_invoices(payload, reply_token, note=_("Credit notes exceed the invoices"))
    payload.update(cash="0", methods=[], allocation=None)
    if _picked_total(payload) == 0:  # 抵减正好抵平：不收钱、不问方式，直接出确认卡（原生净额 0 的 RE）
        payload["allocation"] = _full(payload, payload["picked"])
        return _draft(line_user_id, payload, reply_token)
    try:
        _ask_method(line_user_id, payload, reply_token)
    except contracts.CollectionError as error:
        if error.code not in READING_CODES:
            raise
        # 收款方式要的主档电脑还在读：先三个点，读好了接着问收款方式；等不到回「正在读取」+【查看结果】（点了接着出这一步）
        _wait_read(line_user_id, reply_token, _service().reading_topics(payload["company"], payload.get("day")), "invoices", "",
                   lambda: _send(reply_token, [{"type": "text", "text": _explain(error), "quickReply": {"items": [
                       _button(_("View result"), _data("invoices")), _cancel()]}}]))


# ── 收款方式与金额 ───────────────────────────────────────────────────────────


def _paid(payload) -> Decimal:
    return _amount(payload.get("cash") or 0) + sum((_amount(row["amount"]) for row in payload.get("methods") or []),
                                                   contracts.ZERO)


def _rest(payload) -> Decimal:
    return _picked_total(payload) - _paid(payload)


def _catalog(payload) -> dict:
    found = payload.get("catalog")
    if found is None:
        found = payload["catalog"] = _service().methods(payload["company"], payload.get("day"))
    return found


def _unused(payload) -> list:
    """还能加的方式：``[(值, 名称)]``（现金一次、每种转账一次）。"""
    found, used = _catalog(payload), {row["prefix"] for row in payload.get("methods") or []}
    if found.get("single") and (_amount(payload.get("cash") or 0) or any(
            row.get("role") == contracts.TRANSFER for row in payload.get("methods") or [])):
        return []  # 账本是 MR.ERP：一笔只收一种钱（现金或一个银行），选过就不再加；折扣另算
    out = [(CASH, _("Cash"))] if found.get("cash") and not _amount(payload.get("cash") or 0) else []
    return out + [(row["prefix"], row.get("label") or row["name"]) for row in found.get("transfers") or []
                  if row["prefix"] not in used]


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
    lines = [_status_line(payload), _("Choose the collection method")]
    lines += [f"{index}. {label}" for index, (_key, label) in enumerate(options, 1)]
    _ask(reply_token, "\n".join(lines),
         [_button(text, _data("method", key)) for text, (key, _label) in zip(choice_labels([label for _key, label in options]), options)])


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
    return next((row.get("label") or row["name"] for row in _catalog(payload).get("transfers") or []
                 if row["prefix"] == pending), pending)


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
    picked, paid = _picked_invoices(payload), _paid(payload)
    offsets = [docnum for docnum in payload["picked"] if _is_offset(payload, docnum)]
    credit = -sum((_signed(payload, docnum, payload["due"][docnum]) for docnum in offsets), contracts.ZERO)
    if len(picked) == 1:  # 勾了的抵减单整张抵（主会话 11-28：自动分配不动抵减行），发票收「收到的 + 抵减」
        payload["allocation"] = [{"docnum": picked[0], "amount": contracts.text(paid + credit)}, *_full(payload, offsets)]
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
    picked, paid = _picked_invoices(payload), _paid(payload)
    offsets = [docnum for docnum in payload["picked"] if _is_offset(payload, docnum)]
    credit = -sum((_signed(payload, docnum, payload["due"][docnum]) for docnum in offsets), contracts.ZERO)
    if value == EACH:
        payload.update(step="each", each={}, at=0)
        _save(line_user_id, payload)
        return _ask_each(payload, reply_token)
    if value == OLDEST:
        rows = [{"docnum": docnum, "doc_date": payload["dates"].get(docnum) or "", "available": payload["due"][docnum],
                 "rectyp": (payload.get("kinds") or {}).get(docnum, "")} for docnum in payload["picked"]]
        allocation = contracts.allocate(rows, paid)
    else:
        others = [{"docnum": docnum, "amount": _amount(payload["due"][docnum])} for docnum in picked if docnum != value]
        allocation = [*others, *_full(payload, offsets),
                      {"docnum": value, "amount": paid + credit - sum((row["amount"] for row in others), contracts.ZERO)}]
    payload["allocation"] = [{"docnum": row["docnum"], "amount": contracts.text(row["amount"])} for row in allocation]
    _draft(line_user_id, payload, reply_token)


def _ask_each(payload, reply_token, note=""):
    docnum = payload["picked"][payload["at"]]
    due = _amount(payload["due"][docnum])
    left = _paid(payload) - sum((_signed(payload, key, value) for key, value in payload["each"].items()), contracts.ZERO)
    choices = [money(value) for value in sorted({due, max(contracts.ZERO, min(due, left)), contracts.ZERO}, reverse=True)]
    lines = [note] if note else [_("Received {0} · due {1}").format(money(_paid(payload)), money(_rest(payload)))]
    from pearnly_erp_portal.integrations.line_collection_cards import _kind

    kind = _kind((payload.get("kinds") or {}).get(docnum))  # 抵减单标类型，金额照填正数（从合计里扣）
    lines += [" · ".join(part for part in (docnum, kind, _("Due {0}").format(money(due))) if part),
              _("Enter the amount for {0}").format(docnum)]
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
    entered = sum((_signed(payload, key, value) for key, value in payload["each"].items()), contracts.ZERO)
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
    with acting(user), speaking(line_user_id, language) as language:
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
        frappe.local.lang = language  # 存原件的那一段不许把回复的语言带偏（11-13 真机：凭证后那条变泰文）
        if not stored:
            return line_out.reply_or_push(line_user_id, [{"type": "text", "text": _("Could not store this document")}],
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


# ── 草稿与确认卡 ─────────────────────────────────────────────────────────────


def _draft_methods(payload) -> list:
    return [{"prefix": row["prefix"], "amount": row["amount"], "voucher": row.get("voucher") or ""}
            for row in payload["methods"]]


def _draft(line_user_id, payload, reply_token):
    try:
        card = _service().create_draft(payload["company"], payload["customer"], payload["allocation"],
                                       cash=payload.get("cash") or 0, methods=_draft_methods(payload), source="line",
                                       doc_date=payload.get("day") or None)
    except contracts.CollectionError as error:
        # 别人刚收了同一张、余额变了：从发票那一步按此刻的余额重来
        return _pick_customer(line_user_id, payload, payload["customer"], reply_token, note=_explain(error))
    _show_card(line_user_id, payload, card, reply_token)


def _show_card(line_user_id, payload, card, reply_token, note=""):
    """出确认卡：每张卡一个新的一次性确认号（`line_session` 同 DMS 预览卡），旧卡当场失效；会话里原来那笔是另一张草稿时，那张草稿作废。"""
    from pearnly_erp_portal.integrations import line_collection_cards as cards

    _discard(payload_of(line_user_id).get("name"), keep=card["name"])
    state = {"step": "card", "company": card["company"], "customer": card["customer"], "name": card["name"],
             "version": card["version"], "nonce": secrets.token_urlsafe(9),
             "issued": frappe.utils.now_datetime().isoformat(sep=" ")}
    if card.get("problem") == contracts.VOUCHER_MISSING:
        state.update(step="voucher", voucher_for=(card.get("detail") or {}).get("prefix") or "")
    _save(line_user_id, state, ttl_minutes=CARD_MINUTES)
    messages = [cards.confirm(card, edit_url=edit_url(card["name"]), nonce=state["nonce"])]
    if state["step"] == "voucher":
        # 缺转账凭证：先只要照片，收到后（`store_voucher` 补进草稿）才出确认卡——卡发出后不再追问（用户 11-19「问完再出卡」）
        messages = [{"type": "text", "text": _("Send a photo of the transfer slip"), "quickReply": {"items": [_cancel()]}}]
    elif card.get("problem"):
        messages.insert(0, {"type": "text", "text": _explain(contracts.CollectionError(card["problem"],
                                                                                         card.get("detail")))})
    if note:
        messages.insert(0, {"type": "text", "text": note})
    _send(reply_token, messages)


def _discard(name, keep=None):
    """这一笔没放行的草稿作废（被新卡替换、重开一笔、取消、卡片过期）。已放行的、别人的、不存在的不动。"""
    if not name or name == keep:
        return
    with contextlib.suppress(contracts.CollectionError, frappe.PermissionError):
        _service().discard_draft(name)


def edit_url(name) -> str:
    from pearnly_core.account import line_login

    return line_login.entry_url(EDIT_ROUTE + name)


def _repeat(line_user_id, payload, reply_token):
    """照当前这一步再问一次（停在按钮那几步时打了别的字、点了旧步骤的按钮）。"""
    step = payload["step"]
    if step == "company":
        return _ask_company(reply_token, payload)
    if step == "customer":
        return _ask_customer(reply_token)
    if step in ("amount",) and payload.get("pending"):
        return _ask_amount(payload, reply_token)
    if step == "each":
        return _ask_each(payload, reply_token)
    if step == "voucher":
        return _ask_voucher(reply_token) if not payload.get("name") else _show_card(
            line_user_id, payload, _service().card(payload["name"]), reply_token)
    if step == "card":
        return _show_card(line_user_id, payload, _service().card(payload["name"]), reply_token)
    if step == "book":
        return _ask_book(reply_token, payload)
    if step == "menu":
        return _ask_menu(reply_token, payload)
    if step == "day":
        return _ask_day(reply_token, payload)
    if step == "invoices":
        return _show_invoices(payload, reply_token)
    if step == "method":
        return _ask_method(line_user_id, payload, reply_token)
    if step == "more":
        return _after_amounts(line_user_id, payload, reply_token)
    if step == "split":
        return _ask_split(line_user_id, payload, reply_token)
    _text(reply_token, _(STALE))


# ── 放行、撤回、结果 ─────────────────────────────────────────────────────────


def _store():
    return _state()


def _used(nonce) -> bool:
    store = _store()
    return bool(nonce and store.get(store.make_key(_USED_KEY + nonce)))


def _consume(line_user_id, nonce, name) -> dict | None:
    """消费当前卡的确认号（`line_session.consume_nonce`，原子；同一个号第二次一定对不上）。回卡片那一步的会话，对不上回 None。"""
    if not nonce:
        return None
    current = line_session.get_session(line_user_id)
    if not line_session.verify_nonce(current, nonce, STATE) or (current.get("payload") or {}).get("name") != name:
        return None  # 号对得上但单号对不上（伪造）：不消费，当前卡照样有效
    claimed = line_session.consume_nonce(line_user_id, STATE, nonce)
    if claimed is None:
        return None
    store = _store()
    store.set(store.make_key(_USED_KEY + nonce), "1", ex=USED_SECONDS)
    return claimed


def _expired_card(claimed) -> bool:
    issued = claimed.get("issued")
    if not issued:
        return True
    age = frappe.utils.now_datetime() - frappe.utils.get_datetime(issued)
    return age.total_seconds() > CARD_MINUTES * 60


def _stale_card(line_user_id, nonce, name, reply_token):
    """点的不是当前卡：同一张卡已经点过（处理中 / 已处理）不回话——但点过【取消】的回「单号 · 已取消」（11-19 真机：取消后再点
    【保存】没有任何回复，用户口径是保存要回「已取消」）；被新卡替换、过期、会话没了回「已过期」。
    会话里已经不是这一笔（过期了、或已换成别的）时，这张卡的草稿作废；会话正停在同一笔的新卡上则不动它。"""
    if _used(nonce):
        found = frappe.db.get_value(DOCTYPE, name, ["name", "status", "collector"], as_dict=True) if name else None
        if found and found.status == contracts.WITHDRAWN and found.collector == frappe.session.user:
            return _text(reply_token, _status_text(found))
        return None
    # 只作废自己录的：11-14 起本公司有收款权限的人都能改别人的草稿，但点的是自己 LINE 里没有的卡（别人的、伪造的），不动那一笔
    if payload_of(line_user_id).get("name") != name and frappe.db.get_value(DOCTYPE, name, "collector") == frappe.session.user:
        _discard(name)
    return _text(reply_token, _(EXPIRED))


def _confirm_tap(line_user_id, params, reply_token, event_id):
    _confirm(line_user_id, params.get("k") or "", params.get("n") or "", reply_token, event_id)


def _confirm(line_user_id, nonce, name, reply_token, event_id):
    claimed = _consume(line_user_id, nonce, name)
    if claimed is None:
        return _stale_card(line_user_id, nonce, name, reply_token)
    if claimed.get("step") != "card":
        return _text(reply_token, _(EXPIRED))
    if _expired_card(claimed):
        _discard(name)
        _clear(line_user_id)
        return _text(reply_token, _(EXPIRED))
    _release(line_user_id, name, claimed.get("version"), reply_token, event_id)


def _release(line_user_id, name, version, reply_token, event_id):
    result = _service().release(name, version, "line:" + str(event_id or ""))
    status = result["status"]
    if status == "changed":
        found = payload_of(line_user_id)
        return _show_card(line_user_id, found, _service().card(name), reply_token,
                          note=_("The collection changed. Check it again"))
    if status == "not_draft":
        if payload_of(line_user_id).get("name") == name:
            _clear(line_user_id)
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
    _pending_on(name)
    # 先三个点，收款回执落库时（:func:`notify`）回结果；等不到回「录入中」+【查看结果】【撤回】（用户 11-22）
    view = _service().status(name)
    if _settled(view):  # 放行时就有了结论（没有账套等）：当场回
        claim(name, view)
        return _send(reply_token, _result_messages(view))
    fallback = _result_messages(view)
    if not line_receipt.start(_receipt_key(name), line_user_id, reply_token, fallback,
                              resume="pearnly_erp_portal.integrations.line_collection.resume_release",
                              args={"name": name, "line_user_id": line_user_id}):
        _send(reply_token, fallback)


def _withdraw_tap(line_user_id, params, reply_token):
    """【撤回】只认当前卡（会话里的确认号），或这一笔「录入中」那条上的撤回号；别的旧卡回「已过期」，不动正在录的。"""
    name, nonce = params.get("n") or "", params.get("k") or ""
    if params.get("p"):
        if not nonce or nonce != _pending_of(name):
            return None if _used(nonce) else _text(reply_token, _(EXPIRED))
        _store().set(_store().make_key(_USED_KEY + nonce), "1", ex=USED_SECONDS)
        _pending_off(name)
        return _text(reply_token, _status_text(_service().withdraw(name)))
    claimed = _consume(line_user_id, nonce, name)
    if claimed is None:
        return _stale_card(line_user_id, nonce, name, reply_token)
    view = _service().withdraw(name)
    _clear(line_user_id)
    _text(reply_token, _status_text(view))


def _pending_on(name):
    store = _store()
    store.set(store.make_key(_PENDING_KEY + name), secrets.token_urlsafe(9), ex=7 * 86400)


def _pending_of(name) -> str:
    store = _store()
    found = store.get(store.make_key(_PENDING_KEY + name)) if name else None
    return found.decode() if isinstance(found, bytes) else str(found or "")


def _pending_off(name):
    store = _store()
    store.delete(store.make_key(_PENDING_KEY + name))


def _status_text(view) -> str:
    """一笔此刻的状态（状态词同网页「收款」页，`contracts.status_word`，用户 11-22）：成功 · RE 号、失败 · 原因，其余「记录号 · 状态」。"""
    status = view.get("status") or ""
    if status == contracts.WRITTEN:
        return f"{_(contracts.status_word(status))} · {view.get('docnum') or ''}"
    if contracts.result_unknown(status, view.get("reason")):
        # 结果不明（写没写进去不知道）：不说失败，原因是先去账本核对那句（同网页「收款」页，主会话 11-25）
        from pearnly_erp_portal.collection import texts

        return f"{_(contracts.RESULT_UNKNOWN)} · {texts.reason(view.get('reason'))}"
    if status == contracts.FAILED:
        return _failed_text(view)
    return f"{view.get('name') or ''} · {_(contracts.status_word(status))}"


def _failed_text(view) -> str:
    from pearnly_core.cc_desktop.wire import ack_rules

    reason = str(view.get("reason") or "")
    mrerp = view.get("ledger") == "mrerp"
    if reason == "express.account_set_missing" and view.get("company"):
        from pearnly_erp_portal.collection import texts

        if mrerp:
            from pearnly_erp_portal.collection import mrerp as ledger

            return _not_written(view, ledger.no_book_text(view["company"], view.get("doc_date")))
        return _not_written(view, texts.no_book(view["company"], view.get("doc_date")))
    if ack_rules.error_code(reason) in BALANCE_CODES and view.get("balances"):
        lines = [_("Already collected. Current balance:")]
        lines += [f"{docnum} {money(value)}" for docnum, value in (view.get("balances") or {}).items()]
        return _not_written(view, "\n".join(lines))
    from pearnly_erp_portal.collection import texts

    return _not_written(view, texts.reason(reason) or view.get("status"))


def _not_written(view, why) -> str:
    """没写成的那句：状态词「失败 · 原因」（Express、MR.ERP 同一句，用户 11-22）。"""
    return f"{_(contracts.status_word(contracts.FAILED))} · {why}"


def _result_messages(view) -> list:
    status = view.get("status")
    head = f"{view.get('customer_name') or ''} · {money(view.get('total'))}"
    if status in (contracts.WRITTEN, contracts.FAILED, contracts.WITHDRAWN):
        return [{"type": "text", "text": _status_text(view) + "\n" + head}]
    # 还没写成：录入中（同网页的状态词）；没被电脑领走前可撤回。这本账套此刻在等 Express 关闭就带上现成那句（只在这条回复里，
    # 不另推送；当天第一笔放行时电脑还没去试，不知道）
    waiting = view.get("status") == contracts.QUEUED and _service().express_waiting(view["name"])
    lines = [_("Entering"), *([_("express.error.ACCOUNT_BUSY_LOCKED")] if waiting else []), head]  # = status_word(QUEUED)
    items = [_button(_("View result"), _data("res", n=view["name"]))]
    token = _pending_of(view["name"])
    if token and view.get("status") in contracts.WITHDRAWABLE:
        items.append(_button(_("Withdraw"), _data("wd", n=view["name"], p=1, k=token)))
    return [{"type": "text", "text": "\n".join(lines), "quickReply": {"items": items}}]


FINAL = (contracts.WRITTEN, contracts.FAILED, contracts.WITHDRAWN)


def _state():
    from pearnly_core.execution import state as pearnly_state

    return pearnly_state.store()


def _receipt_key(name) -> str:
    return f"erp:col:{name}"


def claim(name, view) -> bool:
    """同一个结果只告诉一次（后台等结果的回复与回执后的推送谁先到谁发）。"""
    store = _state()
    key = f"{_TOLD_KEY}{name}:{view.get('status')}:{view.get('docnum') or ''}:{view.get('job') or ''}"
    if view.get("returned"):
        key += f":v{view.get('version')}"  # 退回草稿每次版本不同：第二次被拒照样还回
    key = store.make_key(key)
    return bool(store.set(key, "1", nx=True, ex=7 * 86400))


def _read(name) -> dict:
    frappe.db.commit()  # 结束本事务，读到回执刚提交的状态
    return _service().status(name)


def resume_release(name, line_user_id):
    """（`line_receipt` 的短任务，按收款员的身份与语言）收款回执到了：有结论就回结果（写前被拒的原卡带原因还回），记作告诉过；
    还没写成回 None 接着等。"""
    view = _read(name)
    if not _settled(view):
        return None
    claim(name, view)
    if view.get("returned"):
        with _captured() as messages:
            _return_card(line_user_id, view)
        return messages
    return _result_messages(view)


def _settled(view) -> bool:
    """有了结论：写成 / 失败 / 撤回，或写前被电脑拒、退回了草稿（:func:`_return_card`）。"""
    return view["status"] in FINAL or bool(view.get("returned"))


def _return_card(line_user_id, view, reply_token=""):
    """写前被电脑拒（一行没写）：原卡带原因还回（卡片有效期规则 A9）——新确认号、旧卡失效，同普通出卡（`_show_card`）。
    这个人正在 LINE 里录另一笔（会话停在别的步骤）：不打断，只说原因；这笔草稿在网页「收款」页、CC 对话里照样能改了再放行。"""
    name = view["name"]
    note = _failed_text(view)
    _pending_off(name)  # 「录入中」上的撤回号作废：这笔回到了草稿，撤回走新卡
    current = payload_of(line_user_id)
    previous = getattr(frappe.local, "pearnly_collection_to", "")
    frappe.local.pearnly_collection_to = line_user_id
    try:
        if current.get("step") and current.get("name") != name:
            return _send(reply_token, [{"type": "text", "text": note}])
        _show_card(line_user_id, {**current, "company": view["company"], "customer": view["customer"]},
                   _service().card(name), reply_token, note=note)
    finally:
        frappe.local.pearnly_collection_to = previous


def notify(name):
    """（回执提交之后的短任务，`collection.push._tell`）LINE 录的这笔有人在等结果（放行后三个点还在）：按收款员的身份与语言回结果
    （:func:`resume_release`，`line_receipt.resume`）。没人在等就什么都不做：不推送，收款员点【查看结果】或下次进「收款」时用回复
    告诉（:func:`_show_result`、:func:`_told_first`）。"""
    line_receipt.resume(_receipt_key(name))


def _show_result(name, reply_token, line_user_id=""):
    """「录入中」上的【查看结果】：用回复给此刻的状态 / RE 号（写前被拒的原卡带原因还回）；已了结的记作告诉过。"""
    view = _service().status(name)
    if view["status"] in FINAL or view.get("returned"):
        claim(name, view)
    if view.get("returned") and line_user_id:
        return _return_card(line_user_id, view, reply_token)
    _send(reply_token, _result_messages(view))


UNSEEN_DAYS = 7
UNSEEN_MAX = 10


def _unseen(user) -> list:
    """这位收款员 LINE 录的、放行后 :data:`UNSEEN_DAYS` 天内写成 / 失败、还没告诉过的几笔（新的在前），告诉一次就记下。"""
    since = frappe.utils.add_days(frappe.utils.now_datetime(), -UNSEEN_DAYS)
    names = frappe.get_all(DOCTYPE, filters={"collector": user, "source": "line",
                                             "status": ["in", [contracts.WRITTEN, contracts.FAILED]],
                                             "confirmed_at": [">", since]},
                           pluck="name", order_by="confirmed_at desc", limit_page_length=UNSEEN_MAX)
    out = []
    for name in names:
        view = _service().status(name)
        if view["status"] in (contracts.WRITTEN, contracts.FAILED) and claim(name, view):
            out.append(_status_text(view) + "\n" + f"{view.get('customer_name') or ''} · {money(view.get('total'))}")  # loop-query: 最多 UNSEEN_MAX 笔
    return [{"type": "text", "text": "\n\n".join(out)[:4900]}] if out else []


@contextlib.contextmanager
def _told_first(user):
    """进「收款」：上次之后写成 / 失败、还没告诉过的放进这次回复的最前面（不另推送）。"""
    frappe.local.pearnly_collection_prefix = _unseen(user)
    try:
        yield
    finally:
        frappe.local.pearnly_collection_prefix = []


# 按钮的步名 → 会话停在的那一步（勾发票的每一下都停在「发票」这一步）
_STEP_OF = {"inv": "invoices"}

_ANSWERS = {
    "company": _pick_company,
    "book": _answer_book,
    "menu": _answer_menu,
    "day": _answer_day,
    "customer": _answer_customer,
    "inv": _answer_invoice,
    "invoices": _answer_invoices_done,
    "method": _answer_method,
    "amount": _answer_amount,
    "more": _answer_more,
    "split": _answer_split,
    "each": _answer_each,
}
