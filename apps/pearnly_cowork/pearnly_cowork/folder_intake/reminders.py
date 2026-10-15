"""定时任务的运行前提醒（F2，2026-10-12）：开着提醒的任务，在负责人选的提醒时刻（运行日前 0–7 天 + 曼谷时间，最多 3 个；
没选过就是前一天 08:00 与 14:00，2026-10-13 起可自选）各提醒负责人一次把资料放进文件夹。

每 5 分钟由 `schedule.notices` 叫一次（口径见 `docs/work/2026-10-12-cc-closed-loop.md`）：

- 时段：每个提醒从它的时刻发到同一任务下一个更晚的提醒时刻（最后一个到运行时刻）为止（`contracts.reminder_slot`）；
  落在运行时刻之后的不发；打开提醒、换负责人、改运行日 / 时间、改提醒时刻、重新启用之前的时段不补发（`remind_since`）。
  部署停机跨过整点也不漏。
- 合并：同一负责人、同一运行时刻、同一提醒时刻、同一台读文件夹的电脑的任务合成一条，最多列 10 个，其余写「另有 N 项」。
  按电脑分开（2026-10-13）：提醒会被那台电脑同步取回弹出，不能带上别的电脑的任务名与文件夹路径。
- 只发一次：去重键 = 负责人 + 运行时刻 + 提醒时刻 + 电脑（`contracts.remind_key`）；先查键，已发的不再做权限复核。
- 负责人此刻不能用这条连接录入的任务不提醒（与后台推进同一判据 `machine.owner_ready`），整组都不能就不发。
- 文字在发出时按负责人的语言写好：任务名、开始时刻（今天 / 明天，两天及以后写日期）与剩余时间、完整文件夹路径；
  小助手近期同步过就带上待处理数（还没处理过、到点会处理的文件数；数到上限写「N+」；不知道或是 0 就不写，不用 0 冒充）。
- 送达交给共用层（`notify`）：挂在组内最近在 CC 里建 / 改 / 启停过、且仍是负责人自己的那段对话上，另有手机推送与电脑通知。
  一组一提交，一组出错只回滚这一组、记错误日志。不调模型、不扣费。
"""
import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from pearnly_cowork.folder_intake import agent, contracts, machine, notify, readers
from pearnly_cowork.folder_intake.access import RULE

FIELDS = ["name", "title", "rule_owner", "connection", "reader", "reader_device", "root_id", "folder", "run_day", "run_time",
          "remind_times", "remind_since", "modified", "cc_session"]


def run(now=None):
    """``now``：站点时区的时间字符串（测试环境手动触发用，见 `schedule.notices`），缺省是现在。"""
    if frappe.conf.get("pearnly_product") != "cowork":
        return
    current = notify.epoch(get_datetime(now) if now else now_datetime())
    due = []
    for rule in frappe.get_all(RULE, filters={"enabled": 1, "remind": 1}, fields=FIELDS, ignore_permissions=True):
        hit = _slot(rule, current)
        if hit:
            # 按读者（电脑|连接）分组：同一个池里几台电脑各读各的，提醒各回各的电脑（ADR-021 第 4 批）
            rule.update({"owner": rule.rule_owner, "run_at": hit[0], "label": hit[1], "reader": readers.ref(rule),
                         "member": readers.of(rule)[1]})
            due.append(rule)
    states = {}
    for group in contracts.reminder_groups(due):
        key = contracts.remind_key(group["owner"], group["run_at"], group["label"], group["reader"])
        # 部署前按连接算的键（读者那时只是连接）：同一时段部署前已发过的不再发一遍
        legacy = contracts.remind_key(group["owner"], group["run_at"], group["label"], group["rules"][0]["member"])
        try:
            if notify.sent(key) or (legacy != key and notify.sent(legacy)):
                continue
            _send(group, key, current, states)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Cowork folder reminder failed", message=frappe.get_traceback()[-1500:])


def _slot(rule, current):
    if not rule.rule_owner:
        return None
    try:
        day, at = contracts.schedule(rule.run_day, rule.run_time)
    except contracts.ContractError:
        return None
    since = rule.remind_since or rule.modified
    try:
        return contracts.reminder_slot(current, day, at, notify.epoch(since) if since else 0.0, rule.remind_times)
    except contracts.ContractError:
        return None


def _send(group, key, current, states):
    owner = group["owner"]
    rules = [rule for rule in group["rules"] if machine.owner_ready(rule)]
    if not rules:
        return
    with notify.in_lang(owner):
        title = _("cowork.folder.remind.title")
        text = _text(rules, group["run_at"], current, states)
    notify.send(owner, notify.KIND_REMIND, title, text, key, rules=[rule.name for rule in rules],
                conversation=notify.conversation(owner, rules), ttl=notify.REMIND_TTL)


def _text(rules, run_at, current, states):
    word = contracts.day_word(current, run_at)
    if word == "today":
        when = _("cowork.folder.remind.today").format(contracts.clock(run_at))
    elif word == "tomorrow":
        when = _("cowork.folder.remind.tomorrow").format(contracts.clock(run_at))
    else:
        when = _("cowork.folder.remind.date").format(*contracts.month_day(run_at), contracts.clock(run_at))
    left = _left(*contracts.time_left(current, run_at))
    if len(rules) == 1:
        rule = rules[0]
        lines = [_("cowork.folder.remind.one").format(rule.title, when, left, _path(rule, states))]
        count = _pending(rule, states)
        if count is not None:
            lines.append(_("cowork.folder.remind.count").format(count))
        return "\n".join(lines)
    lines = [_("cowork.folder.remind.many").format(len(rules), when, left)]
    for rule in rules[:contracts.LIST_MAX]:
        line = _("cowork.folder.line").format(rule.title, _path(rule, states))
        count = _pending(rule, states)
        if count is not None:
            line = _("cowork.folder.remind.lineCount").format(line, count)
        lines.append("· " + line)
    if len(rules) > contracts.LIST_MAX:
        lines.append(_("cowork.folder.more").format(len(rules) - contracts.LIST_MAX))
    return "\n".join(lines)


def _left(days, hours, minutes):
    if days:
        return _("cowork.folder.remind.leftDays").format(days, hours)
    if hours:
        return _("cowork.folder.remind.leftHours").format(hours, minutes)
    return _("cowork.folder.remind.leftMinutes").format(minutes)


def _state(rule, states):
    """读这条任务文件夹的小助手最近一次同步上报的本机情况（一轮里同一台只读一次）。"""
    reader = readers.ref(rule)
    if reader not in states:
        states[reader] = readers.state(*readers.of(rule))
    return states[reader]


def _root(state, root_id):
    return next((row for row in state.get("roots") or [] if isinstance(row, dict) and row.get("id") == root_id), {})


def _path(rule, states):
    """完整文件夹路径：根目录显示名（本机完整路径）+ 规则文件夹；小助手一周没同步、记不得根目录时只写规则文件夹。"""
    label = _root(_state(rule, states), rule.root_id).get("label") or ""
    return contracts.full_path(label, rule.folder or "") or _("cowork.folder.rootItself")


def _pending(rule, states):
    """待处理数（小助手上一轮算的、还没处理过、到点会处理的文件数，不是文件夹里现有几份）：近期同步过才有；
    数到上限写「N+」；0 不写（文件夹里可能留着处理过的文件，写「0 份」会让人以为是空的）。"""
    state = _state(rule, states)
    if not agent.fresh(state):
        return None
    count = (state.get("pending") or {}).get(rule.name)
    if type(count) is not int or count <= 0:
        return None
    return f"{count}+" if _root(state, rule.root_id).get("partial") else count
