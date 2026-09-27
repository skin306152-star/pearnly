"""LINE 里「发单据进 ERP」上传前的几步选择（用户 2026-09-26：照 ERP 的新做法）。

点菜单「ส่งเอกสารเข้า ERP」（旧线上与新菜单都是 postback `action=cowork_erp_start`）之后，用 LINE
原生快捷回复按钮依次问：目标 ERP → 账套 → 进项 / 销项 → 入账方式 → 提示发 PDF 或图片。
口径与网页「新建录入」弹窗同一套（`public/desk/intake-choices.js`）：
  · 目标：MR.ERP 要有可用连接，Express 要助手在线，「识别后导出」不要连接；不能选的在问句里列出原因
    （旧线上 `erp_picker_card` 的状态行）；
  · **账套上传前由用户定**（用户 2026-09-27：系统判断不了的由用户选，按用户的选择执行）：快捷回复一次
    最多 12 个选项，放不下几十上百份账套，所以请用户打几个字（公司名、账套名、年度文件夹、税号都认），
    从匹配结果里点序号（`connections/account_search.py`）；另给「上次用的」一键选；目录里只有一份就直接选上；
  · 入账方式以第三方为准：MR.ERP 选付款（进项只有赊账），Express 选过账类型（服务 / 存货）；
  · 识别后导出只选进项 / 销项。
每一步末尾都有「取消」。选择存在本人的 LINE 会话里（30 分钟），之后发的每一张都按这组选择识别。
按钮只带序号，值存在会话里（账套键可能很长，postback 有 300 字上限）；只接受当前在问的那一步。
"""
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line, line_session

from pearnly_cowork.access import scope

STATE = "cowork_intake"
TTL_MINUTES = 30
START = "action=cowork_erp_start"
RECOGNIZE_ONLY = "none"
TARGETS = (("mrerp", "MR.ERP"), ("express", "Express"))
DIRECTIONS = ("purchase", "sales")
MODE = {"mrerp": "payment", "express": "posting_kind"}
# 快捷回复一次最多 13 个按钮：选项最多 12 个，留一个给「取消」。
MAX_CHOICES = 12
MENU_WORDS = ("เมนู", "menu", "菜单", "菜單", "メニュー")


# ── 会话 ────────────────────────────────────────────────────────────────────


def choices(line_user_id) -> dict:
    session = line_session.get_session(line_user_id) or {}
    return dict(session.get("payload") or {}) if session.get("state") == STATE else {}


def _save(line_user_id, payload):
    line_session.set_session(line_user_id, STATE, payload, ttl_minutes=TTL_MINUTES)


def keep(line_user_id, payload):
    """又发了一张：选择原样保留，30 分钟重新计时。"""
    _save(line_user_id, payload)


def ready(payload) -> bool:
    return bool(payload.get("target")) and _pending(payload) == "upload"


# ── 消息 ────────────────────────────────────────────────────────────────────


def _button(label, data):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data,
                                         "displayText": label}}


def _text(reply_token, text):
    line.reply(reply_token, [{"type": "text", "text": text}])


def _ask(reply_token, text, items):
    items = items[:MAX_CHOICES] + [_button(_("Cancel"), "a=cowork&s=cancel")]
    line.reply(reply_token, [{"type": "text", "text": text, "quickReply": {"items": items}}])


def menu(reply_token):
    """手打「เมนู」：给「发单据进 ERP」一个原生按钮（与富菜单同一个 postback）。"""
    _ask(reply_token, _("Choose what to do"), [_button(_("Send documents to ERP"), START)])


# ── 各步 ────────────────────────────────────────────────────────────────────


def start(line_user_id, reply_token):
    """从头问起（旧会话作废）。调用方已把会话用户切成绑定的这个账号。"""
    _next(line_user_id, {}, reply_token)


def cancel(line_user_id, reply_token):
    line_session.clear_session(line_user_id)
    _text(reply_token, _("Cancelled"))


def answer(line_user_id, data, reply_token):
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    step = params.get("s") or ""
    if step == "cancel":
        return cancel(line_user_id, reply_token)
    payload = choices(line_user_id)
    offered = (payload.get("offered") or {}).get(step) or []
    try:
        value = offered[int(params.get("v", ""))]
    except (ValueError, IndexError):
        value = None
    if not payload.get("offered") or step != _pending(payload) or value is None:
        return _text(reply_token, _("This option is no longer current"))
    if step == "target":
        payload = {"target": value}
        connection = _connections().get(value) if value != RECOGNIZE_ONLY else None
        if connection:
            payload["connection"] = connection["name"]
    elif step == "account":
        labels = (payload.get("offered_labels") or {}).get("account") or []
        index = int(params.get("v"))
        payload.update(account_set_key=value, account_label=labels[index] if index < len(labels) else "")
        payload.pop("offered_labels", None)
    else:
        payload[step] = value
    _next(line_user_id, payload, reply_token)


def _pending(payload):
    target = payload.get("target")
    if not target:
        return "target"
    if target != RECOGNIZE_ONLY and not payload.get("account_set_key"):
        return "account"
    if not payload.get("direction"):
        return "direction"
    mode = MODE.get(target)
    if mode and not payload.get(mode):
        return mode
    return "upload"


def _next(line_user_id, payload, reply_token):
    while True:
        step = _pending(payload)
        if step == "upload":
            payload.pop("offered", None)
            payload.pop("offered_labels", None)
            _save(line_user_id, payload)
            # 上传前的拍摄提示照 ERP（用户 2026-09-26）：侧着的照片会读错，不另花识别费去转正。
            return _text(reply_token, summary(payload) + "\n" + _("Please upload a PDF or image") + "\n"
                         + _("For the best reading, photograph the document upright and straight, with the text the right way up and the whole page in frame."))
        if step == "account":
            if _ask_account(line_user_id, payload, reply_token):
                return
            continue  # 目录里只有一份：已直接选上
        if step == "target":
            options, note = _targets()
        else:
            options, note = _options(step, payload), ""
        if not options:
            line_session.clear_session(line_user_id)
            return _text(reply_token, _empty(step, payload))
        # 只有一个可选就直接选上（MR.ERP 进项只有赊账；网页弹窗同一口径）。
        if len(options) == 1 and step == "payment":
            payload[step] = options[0][0]
            continue
        break
    payload["offered"] = {step: [key for key, _label in options]}
    _save(line_user_id, payload)
    items = [_button(label, urlencode({"a": "cowork", "s": step, "v": index}))
             for index, (_key, label) in enumerate(options[:MAX_CHOICES])]
    question = _QUESTIONS[step]()
    _ask(reply_token, question + ("\n" + note if note else ""), items)


_QUESTIONS = {
    "target": lambda: _("Choose the destination for these documents"),
    "direction": lambda: _("Purchase or sales?"),
    "payment": lambda: _("Choose the payment method"),
    "posting_kind": lambda: _("Service or stock?"),
}


def _empty(step, payload):
    return _("No ERP is ready to use")


# ── 账套：打字找 ────────────────────────────────────────────────────────────

# 结果最多列 11 个（按钮是序号），留一个给「取消」。
MAX_RESULTS = MAX_CHOICES - 1


def _account_rows(payload):
    """这条连接能选的账套（服务端存着的目录，按本人权限读）。"""
    from pearnly_cowork.connections import service
    from pearnly_cowork.connections import account_search

    data = service.targets(payload["connection"])
    return account_search.usable(payload["target"], data.get("account_sets") or [])


def _last_used(payload, rows):
    """本人在这条连接上最近一次用的账套（还在目录里、还能选才算）。"""
    keys = {str(row.get("source_id")) for row in rows}
    for key in frappe.get_all("Cowork OCR History", filters={
            "owner": frappe.session.user, "push_connection": payload["connection"], "push_account_set": ["!=", ""]},
            pluck="push_account_set", order_by="creation desc", limit=20):
        if key in keys:
            return key
    return ""


def _offer(line_user_id, payload, keys, labels):
    payload["offered"] = {"account": keys}
    payload["offered_labels"] = {"account": labels}
    _save(line_user_id, payload)


def _ask_account(line_user_id, payload, reply_token, note="") -> bool:
    """请用户打字找账套；只有一份就直接选上（返回 False，接着问下一步）。"""
    from pearnly_cowork.connections import account_search

    rows = _account_rows(payload)
    if not rows:
        line_session.clear_session(line_user_id)
        _request_directory(payload)
        _text(reply_token, _("Reading the company list from the ERP. Tap the menu again in a moment."))
        return True
    if len(rows) == 1:
        payload.update(account_set_key=str(rows[0]["source_id"]), account_label=account_search.label(payload["target"], rows[0]))
        return False
    last = _last_used(payload, rows)
    last_label = next((account_search.label(payload["target"], row) for row in rows
                       if str(row.get("source_id")) == last), "")
    _offer(line_user_id, payload, [last] if last else [], [last_label] if last else [])
    lines = [note] if note else []
    lines.append(_("Type a few letters of the company or account set name to find it"))
    if last:
        lines.append(f"{_('Last used')}: {last_label}")
    items = [_button(_("Last used"), urlencode({"a": "cowork", "s": "account", "v": 0}))] if last else []
    _ask(reply_token, "\n".join(lines), items)
    return True


def search(line_user_id, text, reply_token) -> bool:
    """在「账套」这一步收到的文字＝关键词：列出匹配的账套，按序号点选。不在这一步返回 False。"""
    from pearnly_cowork.connections import account_search

    payload = choices(line_user_id)
    if not payload.get("target") or _pending(payload) != "account":
        return False
    rows = _account_rows(payload)
    found = account_search.search(payload["target"], rows, text)
    if not found:
        _ask_account(line_user_id, payload, reply_token, note=_("No account set matches “{0}”").format(text[:40]))
        return True
    shown = found[:MAX_RESULTS]
    _offer(line_user_id, payload, [row["value"] for row in shown], [row["label"] for row in shown])
    lines = [_("Found {0}. Tap the number to choose:").format(len(found))]
    lines += [f"{index + 1}. {row['label']}" for index, row in enumerate(shown)]
    if len(found) > len(shown):
        lines.append(_("Too many matches; type a few more letters to narrow it down"))
    items = [_button(str(index + 1), urlencode({"a": "cowork", "s": "account", "v": index}))
             for index in range(len(shown))]
    _ask(reply_token, "\n".join(lines), items)
    return True


def _request_directory(payload):
    """服务端还没有这家 ERP 的账套目录：申请读一次（与网页同一个刷新），读到后再点菜单就有了。"""
    from uuid import uuid4

    from pearnly_cowork.connections import service

    try:
        service.request_refresh(payload["connection"], str(uuid4()))
    except frappe.ValidationError:
        pass


# ── 选项 ────────────────────────────────────────────────────────────────────


def _connections():
    """本人能用的连接（与网页同一个接口，按当前会话用户判），每种目标取第一条。"""
    from pearnly_cowork.connections import service

    found = {}
    for row in service.records(None, limit=100) or []:
        found.setdefault(row.get("adapter"), row)
    return found


def _state(adapter, connection):
    """这条通道现在能不能选、为什么不能（判据与网页 `targets.js` 的 `ready` / `describe` 相同）。"""
    if not connection:
        return False, _("connect.state.notConnected")
    if not connection.get("enabled"):
        return False, _("connect.state.disabled")
    if adapter == "express":
        from pearnly_cowork.companion import credentials

        try:
            online = credentials.online(connection["name"])
        except frappe.PermissionError:
            online = False
        if not online:
            return False, _("intake.agent.state.missing")
    return True, ""


def _targets():
    connections = _connections()
    options, lines = [], []
    for adapter, label in TARGETS:
        ok, reason = _state(adapter, connections.get(adapter))
        if ok:
            options.append((adapter, label))
        else:
            lines.append(f"• {label}: {reason}")
    options.append((RECOGNIZE_ONLY, _("intake.target.recognizeOnly")))
    return options, "\n".join(lines)


def _options(step, payload):
    if step == "direction":
        return [("purchase", _("intake.dir.purchase")), ("sales", _("intake.dir.sales"))]
    if step == "payment":
        # 以第三方为准：MR.ERP 进项只有赊账（旧线上 `mode_options`）。
        return ([] if payload.get("direction") == "purchase" else [("cash", _("Cash"))]) + [("credit", _("Credit"))]
    if step == "posting_kind":
        return [("service", _("Service")), ("stock", _("Stock"))]
    return []


def target_name(payload) -> str:
    """只是目标 ERP 的名字（卡片上账套另起一行，取票上记的）。"""
    return dict(TARGETS).get(payload.get("target")) or _("intake.target.recognizeOnly")


def target_label(payload) -> str:
    """目标 ERP · 账套（识别后导出只有目标）。"""
    parts = [dict(TARGETS).get(payload.get("target")) or _("intake.target.recognizeOnly")]
    if payload.get("target") != RECOGNIZE_ONLY and payload.get("account_label"):
        parts.append(payload["account_label"])
    return " · ".join(parts)


def mode_label(payload) -> str:
    labels = {"cash": _("Cash"), "credit": _("Credit"), "service": _("Service"), "stock": _("Stock")}
    value = payload.get(MODE.get(payload.get("target")) or "")
    return labels.get(value, value or "")


def summary(payload) -> str:
    """一行说清楚这次按什么录：目标 · 账套 · 进项 / 销项 · 入账方式。"""
    parts = [target_label(payload), dict(_options("direction", payload)).get(payload.get("direction"), ""),
             mode_label(payload)]
    return " · ".join(part for part in parts if part)


def is_start(data) -> bool:
    params = {key: values[0] for key, values in parse_qs(data or "").items()}
    return params.get("action") == "cowork_erp_start"


def is_step(data) -> bool:
    return (parse_qs(data or "").get("a") or [""])[0] == "cowork"


def allowed(user) -> bool:
    return scope.allowed("intake", user)
