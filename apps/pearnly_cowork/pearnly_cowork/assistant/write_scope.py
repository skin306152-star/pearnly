"""CC 只写对话选中的公司与账套（用户 11-10：「CC 或模型录进别的账套就是产品缺陷」；11-10 线上压测 S7 CC 说要录进卖方的真账套）。

凡是 CC 在对话里发起的账本写入（识别时带推送目标、推送、待选账套当场选、选现用账套、POS 导出、文件夹任务的去向），
写之前由服务端核「这份账套是本对话挂的那家公司的」：本对话公司 → `companies.service.work_sets`（各年度、各分店；对话指定了
分店只算该分店的，同给 CC 的公司说明）→ 允许的（连接, 账套键）。不是 → 拒，原因交回模型照实说明。不靠提示词。

代录（任务的公司明传 ``company``）与直写同一判据。不在 CC 对话里执行（网页待处理 / 复核页由人亲手选的、定时任务运行时）不限。
MR.ERP 的账套键（``comidyear:seldb``）同样在 `work_sets` 里，同一判定覆盖。

本对话账套（A 线 11-12，R2-5：右栏 T.8，CC 却按票据日期另判、自己填了 T.9 一类的账套）：对话 / 代录任务定了账套
（共用层 `pearnly_core.assistant.ledger`，Cowork 编号与电脑无关、同一本可能在几条连接上各有一处，A 线 11-12 账套身份跨电脑）时
只允许那一本的各处写法，别的同公司账套回
``not_this_conversation_book``，拒绝回包只列本对话账套；账套未定回 ``ledger_needed``（带可选的几本，问一次、用
``pearnly_conversation_ledger`` 设）。代录按任务会话的账套（``task``）。:func:`aim`：写入点不收模型另填的账套，没填就写本对话账套；
票上原本是别家公司的去向不改投（照拒 ``not_this_company_book``）。定时任务保存（``whole_company``）仍按公司判。
"""
import frappe

from pearnly_core.assistant import projects
from pearnly_core.express_rules.targets.identity import normalize_express_account_key

NOT_THIS_COMPANY = "not_this_company_book"
NOT_THIS_LEDGER = "not_this_conversation_book"
LEDGER_NEEDED = "ledger_needed"
NO_COMPANY = "no_company"
SHOWN_MAX = 10

# 给模型看的说明（只给模型，不经 _()）
NOTES = {
    NOT_THIS_COMPANY: ("not_this_company_book: this account set does not belong to the company this conversation is "
                       "filed under, so nothing was written to it. Only that company's account sets "
                       "(allowed_account_sets) can be written; another company's never. When neither side of the "
                       "document is this company, first say so in one sentence (which side is not this company); only "
                       "if the user then clearly agrees to enter it into this company's books anyway, push again with "
                       "allowed_account_sets (this conversation's account set when it has one)."),
    NOT_THIS_LEDGER: ("not_this_conversation_book: this conversation writes only into its own account set "
                      "(allowed_account_sets), so nothing was written to the one given. Do not pass another account "
                      "set: leave connection and account_set_key out and it goes into this conversation's account "
                      "set. Never choose an account set by the document date. Only if the user asked to switch, ask "
                      "with ask_user and set it with pearnly_conversation_ledger first."),
    LEDGER_NEEDED: ("ledger_needed: this conversation's account set is not decided yet, so nothing was written. Ask the "
                    "user once with ask_user which account set to write to (one question, each option label exactly "
                    "the label of one of allowed_account_sets), set it with pearnly_conversation_ledger (ledger = its "
                    "label), then do this again; do not ask again after that."),
    NO_COMPANY: ("no_company: this conversation is not filed under a company, so nothing was written. Ask the user "
                 "with ask_user which company to enter it into, offering the companies listed here (each option label "
                 "exactly the company's title), then file this conversation under the one they chose with "
                 "pearnly_file_conversation and push again."),
}
COMPANIES_SHOWN = 20


def key_of(value) -> str:
    """账套键比较用的写法（同 Express 键的归一：斜杠、结尾分隔符、大小写；MR.ERP 键只按大小写）。"""
    return normalize_express_account_key(value).casefold()


def _session() -> str:
    return str((getattr(frappe.local, "pearnly_assistant", None) or {}).get("session_id") or "")


def _company_sets(company, work_scope="") -> list:
    from pearnly_cowork.companies import service

    return service.work_sets(company, work_scope)


def pinned(company=None, task="") -> dict | None:
    """本对话（代录给了 ``company`` 时按 ``task`` 任务的会话）的账套：``{"status": "set" | "needed", "id", "label", "options",
    "connection", "key"}``；不在对话里、代录没给任务、产品没有、对话没挂公司、provider 出错回 None（照公司判）。"""
    from pearnly_core.assistant import ledger
    from pearnly_cowork.companies import provider

    if company:
        found = ledger.of_task(task) if task else None
    else:
        session = _session()
        found = ledger.of_session(session) if session else None
    if not found:
        return None
    book = _book_of(company, found["id"]) if found["status"] == ledger.SET else None
    if not book:
        return {**found, "connection": "", "key": "", "identity": "", "places": [], "status": ledger.NEEDED}
    places = _places(book)
    return {**found, "connection": places[0][0], "key": places[0][1], "identity": _identity(book), "places": places,
            "status": found["status"]}


def _places(item) -> list:
    """一本账套在各条连接上的（连接，账套键）：同一本经几台电脑（各配一条连接）报上来时有几处（账套身份跨电脑，A 线 11-12）。"""
    found = [(str(c or ""), str(k or "")) for c, k in item.get("places") or () if k]
    return found or [(str(item.get("connection") or ""), str(item.get("key") or ""))]


def _identity(item) -> str:
    from pearnly_cowork.companies import provider

    return str(item.get("identity") or provider.ledger_id(item.get("connection"), item.get("key")))


def _book_of(company, ledger_id):
    """本对话公司（代录给了 ``company``）里编号是 ``ledger_id`` 的那本：新编号，或 11-12 以前存下的「连接|键」（读时映射）。"""
    from pearnly_cowork.companies import provider

    found = allowed(company)
    connection, key = provider.ledger_parts(ledger_id)
    for item in (found or {}).get("sets") or ():
        if _identity(item) == ledger_id or (key and (connection, key_of(key)) in _keyed(item)):
            return item
    return None


def _keyed(item) -> set:
    return {(connection, key_of(key)) for connection, key in _places(item)}


def _is_pin(pin, connection, account_set_key) -> bool:
    return bool(pin and pin.get("key")) and (str(connection or ""), key_of(account_set_key)) in {
        (c, key_of(k)) for c, k in pin.get("places") or [(pin["connection"], pin["key"])]}


def _pool():
    from pearnly_cowork.connections import pool

    return pool


def _serving(places, user) -> tuple:
    """同一本账套有几处（几台电脑各配一条连接）时写进哪一处：替这件活干的电脑——这位用户自己登录 CC 的电脑
    （`connections.pool.owned`），加上公司里能替他写的别人的电脑（`connections.pool.stand_ins`，用户 11-17 定稿）——能写这本、
    此刻在线的那一处在前；都不在线取第一处有能写的电脑的（单子排在那条连接上，谁先上线谁写）；哪一处都没有回第一处
    （推送那边照旧回没有能写的电脑）。只读。"""
    pool, first = _pool(), None
    for connection, key in places:
        doc, peers = frappe.get_doc("Cowork ERP Connection", connection), pool.presence(connection)
        mine = pool.owned(peers, user)
        writers = set(pool.writers(doc, key, peers=mine) if mine else [])
        helpers = pool.stand_ins(doc, key, user, peers=peers)  # loop-query: 同一本账套的几处
        working = {**mine, **{name: peers[name] for name in helpers}}
        writers |= set(helpers)
        if any((working.get(device) or {}).get("online") for device in writers):
            return connection, key
        if writers and first is None:
            first = (connection, key)
    return first or tuple(places[0])


is_pin = _is_pin  # 这条连接与账套是不是本对话账套（:func:`pinned` 的结果）

def allowed(company=None) -> dict | None:
    """这次写入允许的账套：``{"company", "sets", "pairs"}``；对话没挂公司 ``{"company": "", "sets": [], "pairs": set()}``；
    没给 ``company`` 又不在 CC 对话里执行回 None（不限）。账套目录由 `companies.service` 的请求内缓存兜着，这里每次现算。"""
    if company:
        sets = _company_sets(str(company))
        return {"company": str(company), "sets": sets, "pairs": _pairs(sets)}
    session = _session()
    if not session:
        return None
    filed, _period, work_scope = projects.of_session(session)
    sets = _company_sets(filed, work_scope) if filed else []
    return {"company": filed or "", "sets": sets, "pairs": _pairs(sets)}


def _pairs(sets) -> set:
    return {pair for item in sets or () for pair in _keyed(item)}


def check(connection, account_set_key, *, company=None, day="", task="", whole_company=False) -> dict | None:
    """要写的（连接, 账套）在不在允许里：在、或不受限回 None；不在回 ``{"reason", "note", "company",
    "allowed_account_sets"}``（只给模型；``day`` 给了，可写的只列覆盖那天的，没有覆盖的才列全部；有本对话账套时只列它）。
    对话没挂公司另带 ``companies``（本人能用的公司，给提问卡用）。有本对话账套时只允许它（未定回 ``ledger_needed``）；
    ``whole_company``（定时任务保存）照公司判。账套没定（空）的不在这里判，由各写入点照旧停下要人选（或用 :func:`aim`）。"""
    found = allowed(company)
    if found is None:
        return None
    if not found["company"]:
        return {**refusal(found, NO_COMPANY), "companies": _companies()}
    pin = None if whole_company else pinned(company, task)
    return _judged(found, pin, connection, account_set_key, day)


def _judged(found, pin, connection, account_set_key, day="") -> dict | None:
    if pin and not pin.get("key"):
        return needed(found, pin)
    if not account_set_key or _is_pin(pin, connection, account_set_key):
        return None
    if (str(connection or ""), key_of(account_set_key)) not in found["pairs"]:
        return refusal(found, NOT_THIS_COMPANY, day, pin)
    return refusal(found, NOT_THIS_LEDGER, day, pin) if pin else None


def aim(connection, account_set_key, doc_connection="", doc_account_set="", *, company=None, task="",
        day="") -> dict | None:
    """CC 对话 / 代录里这张单写进哪：有本对话账套时回 ``{"connection", "key"}``（就是它）；模型另填了别的、账套未定、票上原本
    是别家公司的去向回拒绝（同 :func:`check` 的回包，不改投别家的票）；不受限、对话没挂公司、没有本对话账套回 None
    （调用方照旧取票上 / 模型给的，再过 :func:`check`）。"""
    found = allowed(company)
    if found is None or not found["company"]:
        return None
    pin = pinned(company, task)
    if not pin:
        return None
    if not pin.get("key"):
        return needed(found, pin)
    if connection or account_set_key:
        refused = _judged(found, pin, connection or pin["connection"], account_set_key or pin["key"], day)
        if refused:
            return refused
    elif doc_account_set and (str(doc_connection or ""), key_of(doc_account_set)) not in found["pairs"]:
        return refusal(found, NOT_THIS_COMPANY, day, pin)
    connection, key = place(pin)
    return {"connection": connection, "key": key, "label": pin.get("label") or pin["key"]}


def place(pin, connection=None) -> tuple:
    """本对话账套写在哪一处 (连接, 账套键)：给了 ``connection`` 且这本在那条连接上有一处就用那处的写法；只有一处就是它；
    几处（几台电脑各配一条连接）按这位用户自己能写、在线的那台挑（:func:`_serving`）。"""
    places = pin.get("places") or [(pin["connection"], pin["key"])]
    if connection is not None:
        here = next((item for item in places if item[0] == str(connection or "")), None)
        if here:
            return here
    return _serving(places, frappe.session.user) if len(places) > 1 else tuple(places[0])


def label_of(connection, account_set_key) -> str:
    """本对话公司这份账套的名称（给计划回包的 ``retargeted``）；不是这家的、认不出回账套键。"""
    found = allowed()
    for item in (found or {}).get("sets") or ():
        if (str(connection or ""), key_of(account_set_key)) in _keyed(item):
            return str(item.get("label") or account_set_key)
    return str(account_set_key or "")


def needed(found, pin) -> dict:
    from pearnly_cowork.companies import provider

    shown = []
    books = {_identity(book): book for book in found.get("sets") or ()}
    for item in (pin.get("options") or ())[:SHOWN_MAX]:
        book = books.get(item["id"])
        connection, key = _places(book)[0] if book else provider.ledger_parts(item["id"])
        # 不带本对话账套编号（内部编号）：CC 按名称设本对话账套（`ledger.choose` 认名称），A 线 11-15
        shown.append({"connection": connection, "key": key, "label": item.get("label"),
                      "covers": bool(item.get("covers"))})
    return {"reason": LEDGER_NEEDED, "note": NOTES[LEDGER_NEEDED], "company": found["company"],
            "allowed_account_sets": shown}


def covering(sets, day="") -> list:
    """本公司覆盖单据日期的账套（同公司说明的「覆盖这个月」）；没给日期或一份都不覆盖回全部。"""
    if not day:
        return list(sets or ())
    from pearnly_cowork.companies import identity

    return identity.covering(list(sets or ()), str(day)[:7]) or list(sets or ())


def refusal(found, reason, day="", pin=None) -> dict:
    if pin and pin.get("key"):
        shown = [{"connection": pin["connection"], "key": pin["key"], "label": pin.get("label") or pin["key"]}]
    else:
        shown = [{"connection": item.get("connection"), "key": item.get("key"), "label": item.get("label")}
                 for item in covering(found["sets"], day)[:SHOWN_MAX]]
    return {"reason": reason, "note": NOTES[reason], "company": found["company"], "allowed_account_sets": shown}


def _companies() -> list:
    from pearnly_cowork.companies import service

    try:
        rows = service.listing(None, 0, COMPANIES_SHOWN)
    except frappe.PermissionError:
        frappe.clear_messages()
        return []
    return [{"id": row.get("id"), "title": row.get("title"), "tax_id": row.get("tax_id")} for row in rows]


COMPANY_NEEDED = "needs_company"
COMPANY_NEEDED_NOTE = ("no_company: this conversation is not filed under a company and the user has several, so nothing "
                       "was done yet. Ask the user with ask_user which company this is for (one question, each option "
                       "label exactly a company title from companies; record_company is the company it belongs to), "
                       "file this conversation under the one they chose with pearnly_file_conversation, then call this "
                       "again. Do not refuse and do not choose for the user.")


def only_company() -> str:
    """没归公司的对话里改 / 删一样东西时按哪家办（主会话 11-16「要人定的事问用户、别拒绝」）：本人能用的公司只有一家就是那家；
    多家或没有回空串（多家由用户在提问卡上选、`pearnly_file_conversation` 归档后再来，:func:`company_needed`）。"""
    listed = _companies()
    return str(listed[0].get("id") or "") if len(listed) == 1 else ""


def company_needed(**fields) -> dict:
    """没归公司、用户又有多家公司：不拒，回给模型先问用户是哪家（现有的选公司做法：提问卡 + 归档对话），定了再调一次出卡片。"""
    return {"status": COMPANY_NEEDED, "reason": NO_COMPANY, **fields, "companies": _companies(),
            "note": COMPANY_NEEDED_NOTE}


def this_company_options(connection, day="") -> list | None:
    """停在「待选账套」的单在公司对话里的候选：本对话公司在这条连接上、覆盖单据日期的账套 ``[{key, label}]``
    （票的双方都不是本公司时，按票归属算出的候选全是别家的——CC 照实说明、用户同意后照样能写进本公司的账套，用户 11-10）。
    有本对话账套时只给它（不论票上是哪条连接）。不受限或对话没挂公司回 None。"""
    found = allowed()
    if found is None or not found["company"]:
        return None
    pin = pinned()
    here = str(connection or "")
    if pin and pin.get("key"):
        key = next((k for c, k in pin.get("places") or () if c == here), pin["key"])  # 这条连接上的那处写法
        return [{"key": key, "label": pin.get("label") or pin["key"]}]
    sets = [item for item in found["sets"] if any(c == here for c, _k in _places(item))]
    return [{"key": next(k for c, k in _places(item) if c == here), "label": item.get("label") or item.get("key")}
            for item in covering(sets, day)]


def keep(options, connection_of=None, company=None, task="") -> list | None:
    """候选账套里只留允许的（``options``：``[{key, ...}]``，连接取 ``connection_of(item)`` 或 item 的 ``connection``）；
    有本对话账套（对话里；代录给了 ``company`` 与 ``task``）时只留它（未定时照公司留，给提问卡列名称）。
    不受限、或对话没挂公司（写入照样由 `check` 拒，候选只是给人看的，不清空）回 None，调用方照旧用原候选。"""
    found = allowed(company)
    if found is None or not found["company"]:
        return None
    pick = connection_of or (lambda item: item.get("connection"))
    pin = pinned(company, task)
    if pin and pin.get("key"):
        return [item for item in options or () if _is_pin(pin, pick(item), item.get("key"))]
    return [item for item in options or () if (str(pick(item) or ""), key_of(item.get("key"))) in found["pairs"]]


__all__ = ["COMPANY_NEEDED", "LEDGER_NEEDED", "NOT_THIS_COMPANY", "NOT_THIS_LEDGER", "NO_COMPANY", "aim", "allowed",
           "check", "company_needed", "covering", "is_pin", "keep", "key_of", "label_of", "needed", "only_company",
           "pinned", "place", "refusal", "this_company_options"]
