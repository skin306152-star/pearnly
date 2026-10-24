"""LINE 里录单前的几步选择（用户 2026-09-25 定稿，见 docs/work/2026-09-25-erp-line-intake.md）。

点菜单「ซื้อ / ขาย」（或手打 1 / 2 / ซื้อ / ขาย）之后，先问「上传识别 / 手动录入」：手动录入
直接在 LINE 里打开网页端原生的空白新建页（快捷回复的链接按钮，不再多发消息）；上传识别则依次用
**LINE 原生快捷回复按钮**问：公司 → 供应商（采购）/ 客户（销售）→ 已付 / 未付 →（已付）付款方式 →
服务 / 库存商品 →（库存商品且默认仓定不下来时）仓库 → 提示上传 PDF 或图片。每一步末尾都有「取消」。

公司、供应商 / 客户、仓库可能远多于一屏（快捷回复最多 13 个按钮，按账套自动建公司后一个租户几十家）：放得下就照旧列按钮；
放不下就请用户打名称（模糊匹配）或编号（税号、公司编号、Express 编码，必须准确命中），从匹配结果里点序号（用户 10-24：
「先让用户输入名称，然后系统模糊匹配，列出相匹配，或者输入编号必须命中」，同 Cowork 选账套 `connections/account_search`）。
供应商 / 客户本来就列不全：除了按票面、一般供应商 / 散客和最近用过的几个，随时可以打字找。

选择存在本人的 LINE 会话里（30 分钟），上传的每一张都按这组选择识别、各出一张预览卡。
这里只管问与记；识别、预览卡、入账在 `intake`。权限全部按绑定的这个账号在服务端判。
"""
import re
from urllib.parse import parse_qs, urlencode

import frappe
from frappe import _

from pearnly_core import line, line_session

from pearnly_erp_portal.access import guard

STATE = "erp_intake"
TTL_MINUTES = 30
FROM_DOCUMENT = "@doc"
# 兜底对方（用户 2026-09-25）：销售「散客」、采购「一般供应商」—— 不用票面上的对方信息。
FALLBACK = "@any"
FALLBACK_LABELS = {"purchase": "General supplier", "sales": "Walk-in customer"}
DIRECTIONS = ("purchase", "sales")
# 方向在 LINE 里的叫法（泰文照旧菜单「ซื้อ / ขาย」，其他语言跟手机语言翻译）。
DIRECTION_LABELS = {"purchase": "Buy", "sales": "Sell"}
# 快捷回复一次最多 13 个按钮：选项最多 12 个，留一个给「取消」。
MAX_CHOICES = 12
PURCHASE_METHODS = ("cash", "transfer", "promptpay", "bank", "cheque", "other")
SALES_METHODS = ("cash", "transfer", "promptpay", "other")
METHOD_LABELS = {"cash": "Cash", "transfer": "Transfer", "promptpay": "PromptPay",
                 "bank": "Bank", "cheque": "Cheque", "other": "Other"}
# 手打也能选方向（旧线上：1 / 2 与菜单等价）。用户手打的词不翻译。
DIRECTION_WORDS = {"1": "purchase", "ซื้อ": "purchase", "purchase": "purchase", "buy": "purchase",
                   "2": "sales", "ขาย": "sales", "sales": "sales", "sell": "sales"}
MENU_WORDS = ("เมนู", "menu", "菜单")
# LINE 按钮最多 20 字：几家都叫「บริษัท …」时截断后只剩「2. บริษัท เดโม่ เทร」，分不清（录制问题 #26）。
# 提问里列出带编号的全名；按钮上去掉这些通用的公司类型词，留下能区分的那段。
_GENERIC_NAME_WORDS = re.compile("|".join(re.escape(word) for word in sorted((
    "ห้างหุ้นส่วนจำกัด", "ห้างหุ้นส่วนสามัญ", "บริษัท", "จำกัด", "(มหาชน)", "มหาชน", "หจก.", "บจก.", "บมจ.",
    "Company Limited", "Co., Ltd.", "Co.,Ltd.", "Co., Ltd", "Limited", "Ltd."), key=len, reverse=True)), re.IGNORECASE)


# ── 会话 ────────────────────────────────────────────────────────────────────


def choices(line_user_id) -> dict:
    session = line_session.get_session(line_user_id) or {}
    return dict(session.get("payload") or {}) if session.get("state") == STATE else {}


def _save(line_user_id, payload):
    line_session.set_session(line_user_id, STATE, payload, ttl_minutes=TTL_MINUTES)


def keep(line_user_id, payload):
    """又发了一张：选择原样保留，30 分钟重新计时。"""
    _save(line_user_id, payload)


def cancel(line_user_id, reply_token):
    line_session.clear_session(line_user_id)
    _text(reply_token, _("Cancelled"))


# ── 消息 ────────────────────────────────────────────────────────────────────


def _button(label, data):
    label = str(label)[:20]
    return {"type": "action", "action": {"type": "postback", "label": label, "data": data,
                                         "displayText": label}}


def _ask(reply_token, text, items):
    items = items[:MAX_CHOICES] + [_button(_("Cancel"), "a=erp&s=cancel")]
    line.reply(reply_token, [{"type": "text", "text": text, "quickReply": {"items": items}}])


def _text(reply_token, text):
    line.reply(reply_token, [{"type": "text", "text": text}])


def menu(user, reply_token):
    """手打「เมนู」：按岗位给 ซื้อ / ขาย 两个原生按钮（与富菜单同一口径）。"""
    items = [_button(_(DIRECTION_LABELS[direction]), "a=mode:" + direction)
             for direction in DIRECTIONS
             if guard.has(direction + ".write", user=user)]
    if not items:
        return _text(reply_token, _("This account cannot send documents"))
    _ask(reply_token, _("Choose what to record"), items)


# ── 各步 ────────────────────────────────────────────────────────────────────


def start(line_user_id, user, direction, reply_token):
    if direction not in DIRECTIONS or not guard.has(direction + ".write", user=user):
        return _text(reply_token, _("This account cannot send documents"))
    from pearnly_erp_portal.masters import company

    payload = {"direction": direction}
    allowed = company.allowed_companies(user)
    if not allowed:
        return _text(reply_token, _("No company is available for this account"))
    if len(allowed) == 1:
        payload["company"] = allowed[0]
    _next(line_user_id, user, payload, reply_token)


def answer(line_user_id, user, data, reply_token):
    """快捷回复按钮点回来的一步。只接受「当前正在问的那一步」，旧按钮点了不生效。"""
    params = {key: values[0] for key, values in parse_qs(data).items()}
    step, value = params.get("s"), params.get("v", "")
    if step == "cancel":
        return cancel(line_user_id, reply_token)
    payload = choices(line_user_id)
    if not payload.get("direction"):
        return _text(reply_token, _("Choose purchase or sales first"))
    if step != _pending(payload):
        return _text(reply_token, _("This option is no longer current"))
    options = {key for key, _label in _options(step, payload, user)}
    offered = set((payload.get("offered") or {}).get(step) or [])  # 打字找出来点的
    if value not in options and value not in offered:
        return _text(reply_token, _("This option is no longer current"))
    payload.pop("offered", None)
    if step == "entry":
        payload["entry"] = value
    elif step == "paid":
        payload["is_paid"] = int(value == "1")
        if not payload["is_paid"]:
            payload["payment_method"] = ""
    elif step == "method":
        payload["payment_method"] = value
    else:
        payload[{"company": "company", "party": "party", "kind": "item_kind",
                 "warehouse": "warehouse"}[step]] = value
    _next(line_user_id, user, payload, reply_token)


def _pending(payload):
    """还差哪一步。仓库只在「库存商品 + 默认仓定不下来」时才问。"""
    if payload.get("entry") != "upload":
        return "entry"
    if not payload.get("company"):
        return "company"
    if "party" not in payload:
        return "party"
    if "is_paid" not in payload:
        return "paid"
    if payload["is_paid"] and not payload.get("payment_method"):
        return "method"
    if not payload.get("item_kind"):
        return "kind"
    if payload["item_kind"] == "goods" and not payload.get("warehouse"):
        return "warehouse"
    return "upload"


def _next(line_user_id, user, payload, reply_token):
    step = _pending(payload)
    if step == "warehouse":
        default = _default_warehouse(payload["company"])
        if default:
            payload["warehouse"] = default
            step = _pending(payload)
    _save(line_user_id, payload)
    if step == "upload":
        # 上传前的拍摄提示（用户 2026-09-26）：侧着的照片会读错商品名，不另花识别费去转正。
        return _text(reply_token, summary(payload) + "\n" + _("Please upload a PDF or image") + "\n"
                     + _("For the best reading, photograph the document upright and straight, with the text the right way up and the whole page in frame."))
    options = _options(step, payload, user)
    if step in SEARCHED and len(options) > MAX_CHOICES:
        return _ask_search(reply_token, step, payload, user)
    if not options:
        # 公司下没有启用的仓库之类：说清楚，而不是给一排空按钮。
        line_session.clear_session(line_user_id)
        return _text(reply_token, _("No warehouse is available for this company"))
    numbered = step in ("company", "party", "warehouse")
    if step == "entry":
        # 手动录入是链接按钮：点了直接打开网页端原生空白新建页，不回 webhook、不多发消息。
        # 与卡片「编辑」、菜单「盘点」同一个入口：LINE 里未登录时用绑定的 LINE 免密登录。
        from pearnly_core.account import line_login

        root = "pearnly-purchase" if payload["direction"] == "purchase" else "pearnly-sales"
        manual = {"type": "action", "action": {"type": "uri", "label": str(_("Enter manually"))[:20],
                  "uri": line_login.entry_url(f"/desk/{root}/new?entry=manual")}}
        return _ask(reply_token, _QUESTIONS[step](payload),
                    [_button(label, urlencode({"a": "erp", "s": step, "v": key})) for key, label in options]
                    + [manual])
    items = [_button(f"{index}. {short_name(label)}" if numbered else label, urlencode({"a": "erp", "s": step, "v": key}))
             for index, (key, label) in enumerate(options, 1)]
    question = _QUESTIONS[step](payload)
    if numbered:
        question += "\n" + "\n".join(f"{index}. {label}" for index, (_key, label) in enumerate(options, 1))
    if step == "party":
        question += "\n" + _SEARCH_PROMPTS["party"](payload)  # 列出的只是最近用过的几个：随时可以打字找
    _ask(reply_token, question, items)


# ── 打字找（选项放不下一屏时）────────────────────────────────────────────────────

SEARCHED = ("company", "party", "warehouse")
# 结果最多列 11 个（按钮是序号），留一个给「取消」（同 Cowork）。
MAX_RESULTS = MAX_CHOICES - 1
_SEARCH_PROMPTS = {
    "company": lambda p: _("Type the company name or number to find it"),
    "party": lambda p: (_("Type the supplier name or code to find it") if p["direction"] == "purchase"
                        else _("Type the customer name or code to find it")),
    "warehouse": lambda p: _("Type the warehouse name or code to find it"),
}


def _ask_search(reply_token, step, payload, user, note=""):
    """请用户打名称或编号；当前公司（选公司时）给一个按钮直接点。"""
    lines = [note] if note else []
    lines += [_QUESTIONS[step](payload), _SEARCH_PROMPTS[step](payload)]
    items = []
    if step == "company":
        from pearnly_erp_portal.masters import company

        try:
            current = company.active_company(user)
        except frappe.PermissionError:
            current = None
        if current:
            label = frappe.db.get_value("ERP Company", current, "company_name") or current
            items.append(_button(short_name(label), urlencode({"a": "erp", "s": step, "v": current})))
    _ask(reply_token, "\n".join(lines), items)


def search(line_user_id, user, text, reply_token) -> bool:
    """会话停在公司 / 供应商 / 客户 / 仓库这一步时收到的文字＝名称或编号：编号准确命中就直接选上；名称按模糊匹配列出，按序号点。
    不在这几步返回 False（交给菜单词等其余处理）。"""
    payload = choices(line_user_id)
    step = _pending(payload) if payload.get("direction") and payload.get("entry") == "upload" else ""
    if step not in SEARCHED or not str(text or "").strip():
        return False
    exact, found = find(step, payload, user, text)
    if exact:
        payload[{"company": "company", "party": "party", "warehouse": "warehouse"}[step]] = exact
        payload.pop("offered", None)
        _next(line_user_id, user, payload, reply_token)
        return True
    if not found:
        _save(line_user_id, payload)
        _ask_search(reply_token, step, payload, user, note=_("No match for “{0}”").format(str(text).strip()[:40]))
        return True
    shown = found[:MAX_RESULTS]
    payload["offered"] = {step: [key for key, _label in shown]}
    _save(line_user_id, payload)
    lines = [_("Found {0}. Tap the number to choose:").format(len(found))]
    lines += [f"{index}. {label}" for index, (_key, label) in enumerate(shown, 1)]
    if len(found) > len(shown):
        lines.append(_("Too many matches; type a few more letters to narrow it down"))
    items = [_button(f"{index}. {short_name(label)}", urlencode({"a": "erp", "s": step, "v": key}))
             for index, (key, label) in enumerate(shown, 1)]
    _ask(reply_token, "\n".join(lines), items)
    return True


def _fold(text) -> str:
    """比对用：去掉通用的公司类型词、空白与标点，统一大小写（泰文不分大小写，照原样）。"""
    return re.sub(r"[\s.,()\-_/]+", "", short_name(text)).casefold()


def find(step, payload, user, text):
    """``(准确命中的那一项或 None, [(值, 名称)] 模糊匹配)``。编号（税号、公司编号、Express 编码、仓库编码）要整串相同；
    名称按去掉公司类型词、空白后包含关键词（几个词时每个词都要有）算，名称相同 / 开头相同的排前面。"""
    wanted = str(text or "").strip()
    digits = re.sub(r"\D", "", wanted)
    rows = _searchable(step, payload, user, wanted)
    exact = [(key, label) for key, label, codes in rows
             if any(code and (code.casefold() == wanted.casefold() or (len(digits) == 13 and code == digits)) for code in codes)]
    if len(exact) == 1:
        return exact[0][0], []
    if exact:
        return None, exact  # 同一税号几家（不同分店）：列出来点
    words = [_fold(word) for word in wanted.split() if _fold(word)] or [_fold(wanted)]
    hits = []
    for key, label, _codes in rows:
        name = _fold(label)
        if all(word in name for word in words):
            hits.append(((name != _fold(wanted), not name.startswith(words[0]), len(name)), key, label))
    hits.sort(key=lambda item: item[0])
    return None, [(key, label) for _rank, key, label in hits]


def _searchable(step, payload, user, text):
    """``[(值, 名称, [编号…])]``：这一步能选的全部（供应商 / 客户按关键词先在库里筛一遍，主档几万条不全读）。"""
    if step == "company":
        from pearnly_erp_portal.masters import company

        rows = frappe.get_all("ERP Company", filters={"name": ["in", company.allowed_companies(user) or [""]]},
                              fields=["name", "company_name", "short_name", "tax_id", "branch"])
        same = {}
        for row in rows:
            same[row.company_name] = same.get(row.company_name, 0) + 1
        return [(row.name, (row.company_name or row.name) + (f" ({row.branch})" if same.get(row.company_name, 0) > 1
                 and row.branch else ""), [row.name, row.short_name or "", row.tax_id or ""]) for row in rows]
    if step == "warehouse":
        rows = frappe.get_all("ERP Warehouse", filters={"company": payload["company"], "is_active": 1},
                              fields=["name", "warehouse_name", "express_code"])
        return [(row.name, row.warehouse_name or row.name, [row.express_code or ""]) for row in rows]
    master, title = (("ERP Supplier", "supplier_name") if payload["direction"] == "purchase"
                     else ("ERP Customer", "customer_name"))
    like = "%" + "%".join(str(text or "").split()) + "%"
    digits = re.sub(r"\D", "", str(text or ""))
    rows = frappe.db.sql(f"""select name, `{title}` as label, express_code, tax_id from `tab{master}`
        where is_active = 1 and (express_company is null or express_company = '' or express_company = %(company)s)
          and (`{title}` like %(like)s or express_code = %(text)s or (%(digits)s != '' and tax_id = %(digits)s))
        order by length(`{title}`) limit 200""",
        {"company": payload["company"], "like": like, "text": str(text or "").strip(), "digits": digits if len(digits) == 13 else ""},
        as_dict=True)
    return [(row.name, row.label or row.name, [row.express_code or "", row.tax_id or ""]) for row in rows]


def short_name(label) -> str:
    """按钮上的名字：去掉通用的公司类型词（全名在提问里）；去完什么都不剩就用原名。"""
    text = " ".join(_GENERIC_NAME_WORDS.sub(" ", str(label)).split())
    return text or str(label)


_QUESTIONS = {
    "entry": lambda p: _("Upload a document or enter it manually?"),
    "company": lambda p: _("Choose the company"),
    "party": lambda p: _("Choose the supplier") if p["direction"] == "purchase" else _("Choose the customer"),
    "paid": lambda p: _("Paid or unpaid?"),
    "method": lambda p: _("Choose the payment method"),
    "kind": lambda p: _("Service or stock item?"),
    "warehouse": lambda p: _("Choose the warehouse"),
}


def _options(step, payload, user):
    from pearnly_erp_portal.masters import company

    if step == "entry":
        return [("upload", _("Upload and recognise"))]
    if step == "company":
        allowed = company.allowed_companies(user)
        rows = frappe.get_all("ERP Company", filters={"name": ["in", allowed or [""]]},
                              fields=["name", "company_name"], order_by="is_default desc, company_name asc")
        return [(row.name, row.company_name or row.name) for row in rows]
    if step == "party":
        return ([(FROM_DOCUMENT, _("From the document")),
                 (FALLBACK, _(FALLBACK_LABELS[payload["direction"]]))]
                + recent_parties(payload, MAX_CHOICES - 2))
    if step == "paid":
        return [("1", _("Paid")), ("0", _("Unpaid"))]
    if step == "method":
        methods = PURCHASE_METHODS if payload["direction"] == "purchase" else SALES_METHODS
        return [(key, _(METHOD_LABELS[key])) for key in methods]
    if step == "kind":
        return [("goods", _("Stock item")), ("service", _("Service"))]
    if step == "warehouse":
        rows = frappe.get_all("ERP Warehouse", filters={"company": payload["company"], "is_active": 1},
                              fields=["name", "warehouse_name"], order_by="warehouse_name asc")
        return [(row.name, row.warehouse_name or row.name) for row in rows]
    return []


def recent_parties(payload, limit):
    """这家公司最近用过的供应商 / 客户（按最近单据），不够再补启用的主档。"""
    if payload["direction"] == "purchase":
        doctype, field, master, title = "Purchase Document", "supplier", "ERP Supplier", "supplier_name"
    else:
        doctype, field, master, title = "ERP Sales Document", "customer", "ERP Customer", "customer_name"
    recent = frappe.db.sql(f"""select `{field}` from `tab{doctype}`
        where company=%s and status!='void' and ifnull(`{field}`, '')!=''
        group by `{field}` order by max(modified) desc limit %s""",
        (payload["company"], limit), pluck=True)
    company = payload["company"]
    usable = lambda name: (frappe.db.get_value(master, name, ["is_active", "express_company"], as_dict=True)  # noqa: E731
                           or frappe._dict())
    names = [name for name in recent if usable(name).is_active and (usable(name).express_company or company) == company]
    if len(names) < limit:
        # 不够再补这家公司该用的启用主档（本公司 Express 的 + 本地档；别家公司的不列，主档按公司隔离）
        names += frappe.db.sql(f"""select name from `tab{master}` where is_active = 1 and name not in %(names)s
            and (express_company is null or express_company = '' or express_company = %(company)s)
            order by modified desc limit %(limit)s""",
            {"names": tuple(names) or ("",), "company": company, "limit": limit - len(names)}, pluck=True)
    labels = dict(frappe.get_all(master, filters={"name": ["in", names or [""]]},
                                 fields=["name", title], as_list=True))
    return [(name, labels.get(name) or name) for name in names]


def _default_warehouse(company_name):
    """沿用网页端收发货的默认仓：收银台上的卖货仓 → 这家公司唯一的仓；定不下来返回空。"""
    from pearnly_erp_portal.inventory import warehouses
    from pearnly_erp_portal.masters import company

    shop = company.selling_warehouse(company_name)
    if shop and frappe.db.get_value("ERP Warehouse", shop, "is_active"):
        return shop
    return warehouses.sole(company_name)


def summary(payload) -> str:
    """一行说清楚这次按什么录：公司 · 供应商 · 已付（方式）· 类别（仓）。"""
    parts = [frappe.db.get_value("ERP Company", payload.get("company"), "company_name") or ""]
    party = payload.get("party")
    if party == FALLBACK:
        parts.append(_(FALLBACK_LABELS[payload["direction"]]))
    elif party and party != FROM_DOCUMENT:
        master = "ERP Supplier" if payload["direction"] == "purchase" else "ERP Customer"
        title = "supplier_name" if payload["direction"] == "purchase" else "customer_name"
        parts.append(frappe.db.get_value(master, party, title) or party)
    else:
        parts.append(_("From the document"))
    paid = _("Paid") if payload.get("is_paid") else _("Unpaid")
    if payload.get("is_paid") and payload.get("payment_method"):
        paid += f" ({_(METHOD_LABELS.get(payload['payment_method'], 'Other'))})"
    parts.append(paid)
    kind = _("Stock item") if payload.get("item_kind") == "goods" else _("Service")
    if payload.get("item_kind") == "goods" and payload.get("warehouse"):
        kind += " · " + (frappe.db.get_value("ERP Warehouse", payload["warehouse"], "warehouse_name")
                         or payload["warehouse"])
    parts.append(kind)
    head = _(DIRECTION_LABELS[payload["direction"]])
    return head + " · " + " · ".join(part for part in parts if part)


def ready(payload) -> bool:
    return bool(payload.get("direction")) and _pending(payload) == "upload"
