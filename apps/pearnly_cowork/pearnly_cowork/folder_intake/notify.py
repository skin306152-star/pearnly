"""定时任务的提醒与完成简报（2026-10-12）：本域只经这里调用共用层的对话提醒（`pearnly_core.assistant.notices`）。

共用层只管送达（对话卡片、手机推送、实时事件）；何时发、发给谁、写什么由本域算好（`reminders` / `briefs`）。提醒记录的
引用一律是定时任务（`reference_doctype` = 任务表，`reference_names` = 这条提醒涉及的全部任务），小助手同步按任务取回给
那台电脑弹系统通知（`for_rules`）。文字在发出时按收件人的语言写好（`in_lang`），之后不再重算。
"""
from contextlib import contextmanager
from zoneinfo import ZoneInfo

import frappe

from pearnly_cowork.folder_intake import contracts
from pearnly_cowork.folder_intake.access import RULE

KIND_REMIND = "cowork.folder.reminder"
KIND_BRIEF = "cowork.folder.brief"
# 手机推送在推送服务里留多久：提醒过了运行时刻就没用了，简报留一天
REMIND_TTL = 6 * 3600
BRIEF_TTL = 24 * 3600


def post(user, kind, text, dedup_key, *, title="", conversation="", rules=(), ttl=REMIND_TTL):
    """发一条提醒（同一去重键只发一次）：回 ``{"name", "id", "session_id", "created"}``。"""
    from pearnly_core.assistant import notices

    return notices.post(user, kind, text, dedup_key, title=title, conversation=conversation,
                        reference_doctype=RULE, reference_names=tuple(rules), ttl=ttl)


def send(user, kind, title, text, dedup_key, *, rules, conversation="", ttl=REMIND_TTL):
    """提醒 / 简报的发出口：``conversation`` 是候选对话（属于收件人、未归档、当前引擎才挂上，否则只走手机推送与
    电脑通知，由共用层判），提交后才推送；回 `post` 的结果（同键已发过时 ``created`` 为 False）。"""
    return post(user, kind, text, dedup_key, title=title, conversation=conversation, rules=rules, ttl=ttl)


def sent(dedup_key):
    """这个去重键已经发过（先查键再做权限复核与写文字，已发的每 5 分钟不重复做）。"""
    from pearnly_core.assistant import notices

    return notices.exists(dedup_key)


def conversation(owner, rules):
    """提醒挂在哪段 CC 对话上：这些任务在 CC 里建 / 改 / 启停过的对话（`cc_session`），按任务最近改过的先后，取第一段
    仍属于收件人 ``owner``、未归档、当前引擎的（共用层 `notices.owned_conversation` 判）；都不是就不挂（回空串）。"""
    found = sorted((rule for rule in rules if contracts.session_ref(rule.get("cc_session"))),
                   key=lambda rule: str(rule.get("modified") or ""), reverse=True)
    candidates = list(dict.fromkeys(rule.get("cc_session") for rule in found))
    if not candidates:
        return ""
    from pearnly_core.assistant import notices

    return notices.owned_conversation(owner, candidates) or ""


@contextmanager
def in_lang(user):
    """按收件人的网页语言写文字（只换语言，不换身份）；与 `machine._as` 同一做法，后台任务里标成已定，不被冲掉。"""
    from frappe.translate import get_user_lang

    previous = getattr(frappe.local, "lang", None)
    frappe.local.lang = get_user_lang(user)
    job = getattr(frappe.local, "job", None)
    if job is not None:
        job.lang_resolved = True
    try:
        yield
    finally:
        frappe.local.lang = previous


def epoch(value):
    """库里的站点时区裸时间（或同格式的字符串）→ epoch 秒（与 `machine._epoch` 同一写法）。"""
    from frappe.utils import get_datetime, get_system_timezone

    return get_datetime(value).replace(tzinfo=ZoneInfo(get_system_timezone())).timestamp()


def for_rules(rule_names, since_epoch, limit=20):
    """这些任务在 ``since_epoch`` 之后的提醒（按时间先后）：``[{"id", "title", "text", "created", "session", "refs"}]``，
    ``refs`` 是这条提醒涉及的全部任务（小助手同步只给涉及的任务全是本机读的那几条，见 `contracts.machine_notices`）。"""
    names = [name for name in rule_names or [] if name]
    if not names:
        return []
    from pearnly_core.assistant import notices

    return notices.for_references(RULE, names, since_epoch, limit)
