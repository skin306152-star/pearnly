"""对话直写的结论晚到时，在那段对话里补一句（多产品共用：Cowork 与 ERP 版 CC 同用；11-08 生产事故第 9 条）。

推送工具在工具里最多等 ``direct_push.PUSH_WAIT_SECONDS``，到点没结论就照实说「已排队」或「结果不明」，然后收尾。
几十秒后 CC 桌面版回报写成了，对话里却一直停在「已发出、未确认」。现在的做法：
- 工具收尾时（`remember`）记下这一张是本人在哪段对话里推的，存在缓存里，两天后过期。
- 产品收到回执（`tell`）时取出这条记录，往那段对话发一条提醒（`notices.post`，提交后才推送）：写成了写单号，没写成写原因。
- 提醒在对话里画出来，下一条消息也会带给 CC（`notices.uncarried`）。

缓存丢了只是少补这一句，不影响推送和账本。何时记、何时补、写进哪个账本由各产品定，这里只管记下与送达。
"""
from contextlib import contextmanager

import frappe
from frappe import _

KIND = "cc.push.result"
TTL = 2 * 24 * 3600  # 记多久：电脑离线的单可能隔天才写进去
NOTICE_TTL = 24 * 3600
LABEL_MAX = 140


def _key(product, record) -> str:
    return f"pearnly_push_followup:{product}:{record}"


def _cache():
    """缓存（拿不到就是 None：只是少补这一句）。"""
    return getattr(frappe, "cache", None)


def remember(product, record, label) -> bool:
    """这张推出去、到点还没结论：记下本人与这段对话（拿不到对话就不记）。回是否记了。"""
    context = getattr(frappe.local, "pearnly_assistant", None) or {}
    session = str(context.get("session_id") or "")
    user = str(getattr(frappe.session, "user", "") or "")
    cache = _cache()
    if not record or not session or not user or user == "Guest" or cache is None:
        return False
    cache.set_value(_key(product, record), {"user": user, "session": session, "label": str(label or "")[:LABEL_MAX]},
                           expires_in_sec=TTL)
    return True


def _origin_key(product, record) -> str:
    return f"pearnly_push_origin:{product}:{record}"


def note_origin(product, record) -> bool:
    """这张是本人在这段对话里推的（推送工具发出时就记，不论等不等得到结论）：领单时交给电脑，一批没录成的通知点开回到这段对话
    （CC 桌面版 1.12.23 ⑨）。拿不到对话、本人或缓存就不记。回是否记了。"""
    context = getattr(frappe.local, "pearnly_assistant", None) or {}
    session = str(context.get("session_id") or "")
    user = str(getattr(frappe.session, "user", "") or "")
    cache = _cache()
    if not record or not session or not user or user == "Guest" or cache is None:
        return False
    cache.set_value(_origin_key(product, record), {"user": user, "session": session}, expires_in_sec=TTL)
    return True


def origin_session(product, record, users) -> str:
    """这张推送来自的那段对话编号；只有推的人在这台电脑上登录过 CC（``users``）才给，别人的对话不交出去。没有回空。"""
    cache = _cache()
    found = cache.get_value(_origin_key(product, record)) if cache is not None and record else None
    if not isinstance(found, dict) or not found.get("session") or found.get("user") not in set(users or ()):
        return ""
    return str(found["session"])


@contextmanager
def _in_lang(user):
    """按收件人的网页语言写文字（回执请求是 CC 桌面版发的，不是本人的语言）。"""
    from frappe.translate import get_user_lang

    previous = getattr(frappe.local, "lang", None)
    frappe.local.lang = get_user_lang(user)
    try:
        yield
    finally:
        frappe.local.lang = previous


def tell(product, record, entered: bool, ledger: str, docnum="", reason="", explain=None) -> bool:
    """这张的结论到了：记过的就在那段对话里补一句（同一张只补一次），回是否发了。没记过（不是在对话里推的、
    工具里已经等到结论、缓存过期）不发。``explain``：产品把存的原因写成一句话的函数，按收件人的语言调用。"""
    key, cache = _key(product, record), _cache()
    found = cache.get_value(key) if cache is not None else None
    if not isinstance(found, dict) or not found.get("user") or not found.get("session"):
        return False
    cache.delete_value(key)
    from pearnly_core.assistant import notices

    with _in_lang(found["user"]):
        label = found.get("label") or _("Document")
        if reason and explain:
            reason = explain(reason) or reason
        if entered:
            text = _("{0} is entered in {1}: {2}").format(label, ledger, docnum) if docnum else \
                _("{0} is entered in {1}").format(label, ledger)
        else:
            text = _("{0} is not entered in {1}: {2}").format(label, ledger, reason) if reason else \
                _("{0} is not entered in {1}").format(label, ledger)
    try:
        notices.post(found["user"], KIND, text, f"push-result:{product}:{record}", conversation=found["session"],
                     ttl=NOTICE_TTL)
    except Exception:
        frappe.log_error(title="Push follow-up notice failed")
        return False
    return True
