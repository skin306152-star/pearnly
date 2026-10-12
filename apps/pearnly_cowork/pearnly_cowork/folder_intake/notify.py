"""定时任务的提醒与完成简报（2026-10-12）：本域只经这里调用共用层的对话提醒（`pearnly_core.assistant.notices`）。

共用层只管送达（对话卡片、手机推送、实时事件）；何时发、发给谁、写什么由本域算好。提醒记录的引用一律是
定时任务（`reference_doctype` = 任务表，`reference_names` = 这条提醒涉及的全部任务），小助手同步按任务取回给
那台电脑弹系统通知（`for_rules`）。
"""
from pearnly_cowork.folder_intake.access import RULE

REMIND_TTL = 6 * 3600
BRIEF_TTL = 24 * 3600


def post(user, kind, text, dedup_key, *, title="", conversation="", rules=(), ttl=REMIND_TTL):
    """发一条提醒（同一去重键只发一次）：回 ``{"name", "id", "session_id", "created"}``。"""
    from pearnly_core.assistant import notices

    return notices.post(user, kind, text, dedup_key, title=title, conversation=conversation,
                        reference_doctype=RULE, reference_names=tuple(rules), ttl=ttl)


def for_rules(rule_names, since_epoch, limit=20):
    """这些任务在 ``since_epoch`` 之后的提醒（按时间先后）：``[{"id", "title", "text", "created", "session"}]``。"""
    names = [name for name in rule_names or [] if name]
    if not names:
        return []
    from pearnly_core.assistant import notices

    return notices.for_references(RULE, names, since_epoch, limit)
