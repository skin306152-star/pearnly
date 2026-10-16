"""定时任务跑完后的完成简报（F3，2026-10-12）：每 5 分钟由 `schedule.notices` 叫一次，看还没发简报的运行记录。

口径见 `docs/work/2026-10-12-cc-closed-loop.md`：

- 一份简报 = 同一负责人（处理这批文件的人，`run_owner`）+ 同一定时时刻 + 同一台读文件夹的电脑（运行的读者：
  `reader_device` + `connection`，ADR-021 第 4 批）；
  立即运行按批：同一负责人相隔 10 分钟之内的算一批，隔得久的各自一份（`brief_group`，见 `machine._brief_slot`）。
  按电脑分开是因为简报会被那台电脑同步取回弹出，不能带上别的电脑的任务（2026-10-13）。
- 何时发（`contracts.brief_groups`）：这些运行都扫描完、文件都走到头、最近一次运行开始与最后一份文件走完都满 10 分钟；
  最多等 13 小时（存货销售最多等进货 12 小时，再加 1 小时），到时带「处理中」照发；3 天前的不再发。没有新文件也发。
- 每个任务一行：已推送、待处理（为什么）、失败、不是发票、未上传、处理中（为什么），与任务列表、运行记录同一口径
  （`contracts.tally`）；有结果表格的再写一行位置：<根目录>\\<文件夹>\\<结果文件夹>\\<年-月>。
- 发给 run_owner，他此刻仍要能用这条连接录入（`machine.owner_ready`）；不能的那几个任务不写进简报、运行记成 skipped，
  整组都不能就不发。
- 只发一次：去重键 = 负责人 + 这批运行（`contracts.brief_key`）；发出、或共用层说这个键已发过，都把运行记成 sent。
  一组一提交，出错只回滚这一组，下一轮再试。不调模型、不扣费；上线前的运行由补丁记成 skipped，不补发。
"""
import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from pearnly_cowork.folder_intake import contracts, machine, notify, readers
from pearnly_cowork.folder_intake.access import FILE, RULE, RUN, read

FOLDER_GONE = ("root_missing", "folder_missing")


def run(now=None):
    """``now``：站点时区的时间字符串（测试环境手动触发用，见 `schedule.notices`），缺省是现在。"""
    if frappe.conf.get("pearnly_product") != "cowork":
        return
    moment = get_datetime(now) if now else now_datetime()
    runs = frappe.db.sql(f"""SELECT name, rule, connection, reader_device, run_owner, brief_group, slot_at, started_at,
        scanned_at, new_files, upload_failed, note FROM `tab{RUN}` WHERE brief_state = 'pending'""", as_dict=True)
    if not runs:
        return
    for row in runs:
        row["reader"] = contracts.reader_ref(row.get("reader_device"), row.connection)
    ready, stale = contracts.brief_groups(runs, _files(runs), moment)
    if stale:
        _mark(stale, "skipped")
        frappe.db.commit()
    by_name = {row.name: row for row in runs}
    for group in ready:
        try:
            _send(group, [by_name[name] for name in group["runs"]], moment)
            frappe.db.commit()
        except Exception:
            frappe.db.rollback()
            frappe.log_error(title="Cowork folder brief failed", message=frappe.get_traceback()[-1500:])


def _files(runs, full=False):
    """这些运行登记的文件：{运行名: [行]}（按任务名走索引，再按运行筛）。判发没发只要进度；写简报才取推送结果。"""
    fields = ("name, rule, run, state, last_code, outcome_json, registered_at" if full
              else "run, state, last_code, registered_at, finished_at")
    found = {}
    rows = frappe.db.sql(f"SELECT {fields} FROM `tab{FILE}` WHERE rule IN %s AND run IN %s",
                         (tuple(sorted({row.rule for row in runs})), tuple(sorted({row.name for row in runs}))),
                         as_dict=True)
    for row in rows:
        found.setdefault(row.run, []).append(row)
    return found


def _mark(names, state, moment=None, key=""):
    frappe.db.sql(f"UPDATE `tab{RUN}` SET brief_state = %s, brief_at = %s, brief_key = %s WHERE name IN %s",
                  (state, moment, key, tuple(names)))


def _owner_ready(rule, owner):
    """按处理这批文件的人复核（任务之后换了负责人，简报仍发给当时的人）：他此刻仍能用这条连接录入。"""
    return machine.owner_ready(frappe._dict(rule_owner=owner, connection=rule.connection))


def _send(group, members, moment):
    owner = group["owner"]
    tasks = {}
    for name in dict.fromkeys(row.rule for row in members):
        if frappe.db.exists(RULE, name):
            rule = read(RULE, name)
            if _owner_ready(rule, owner):
                tasks[name] = rule
    kept = [row for row in members if row.rule in tasks]
    dropped = [row.name for row in members if row.rule not in tasks]
    if dropped:
        _mark(dropped, "skipped")
    if not kept:
        return
    files = _files(kept, full=True)
    rules = sorted(tasks.values(), key=lambda rule: (rule.title or "", rule.name))
    with notify.in_lang(owner):
        title = _("cowork.folder.brief.title")
        text = _text(group, rules, kept, files, moment)
    key = contracts.brief_key(owner, [row.name for row in kept])
    # 共用层说这个键已发过（created 为 False）也照样记成 sent：发过了就不再发
    notify.send(owner, notify.KIND_BRIEF, title, text, key, rules=[rule.name for rule in rules],
                conversation=notify.conversation(owner, rules), ttl=notify.BRIEF_TTL)
    _mark([row.name for row in kept], "sent", moment, key)


def _text(group, rules, runs, files, moment):
    if group["slot_at"]:
        head = _("cowork.folder.brief.head").format(contracts.stamp(notify.epoch(group["slot_at"])), len(rules))
    else:
        head = _("cowork.folder.brief.headManual").format(len(rules))
    lines, states = [head], {}
    for rule in rules[:contracts.LIST_MAX]:
        mine = [row for row in runs if row.rule == rule.name]
        rows = [entry for row in mine for entry in files.get(row.name) or []]
        parts = _parts(contracts.tally(rows, moment), contracts.not_uploaded(mine),
                       any(row.note in FOLDER_GONE for row in mine))
        lines.append("· " + _("cowork.folder.line").format(rule.title, " · ".join(parts)))
        where = _results(rule, rows, states)
        if where:
            lines.append("  " + _("cowork.folder.brief.results").format(where))
    if len(rules) > contracts.LIST_MAX:
        lines.append(_("cowork.folder.more").format(len(rules) - contracts.LIST_MAX))
    if not group["complete"]:
        lines.append(_("cowork.folder.brief.stillMoving"))
    return "\n".join(lines)


def _parts(found, missing_uploads, folder_gone):
    parts = []
    if found["pushed"]:
        parts.append(_("cowork.folder.brief.pushed").format(found["pushed"]))
    if found["stopped"]:
        parts.append(_why(_("cowork.folder.brief.stopped").format(found["stopped"]), _stop_reasons(found)))
    if found["failed"]:
        parts.append(_("cowork.folder.brief.failed").format(found["failed"]))
    if found["not_invoice"]:
        parts.append(_("cowork.folder.brief.notInvoice").format(found["not_invoice"]))
    if missing_uploads:
        parts.append(_("cowork.folder.brief.notUploaded").format(missing_uploads))
    if found["moving"]:
        parts.append(_why(_("cowork.folder.moving").format(found["moving"]), _moving_reasons(found)))
    if folder_gone:
        parts.append(_("cowork.folder.folderMissing"))
    if not parts:
        # 有文件却一项都不计（票都在录入工作台删了）照实写 0，不写「没有新文件」
        parts.append(_("cowork.folder.brief.pushed").format(0) if found["files"] else _("cowork.folder.brief.nothing"))
    return parts


def _why(text, reasons):
    return _("cowork.folder.brief.reasons").format(text, " / ".join(reasons)) if reasons else text


def _kind(kind):
    """要人选的是哪一类（与录入的「选择一次」同一组名称；对方档另起不带 ERP 名称的说法）。"""
    if kind == "party":
        return _("cowork.folder.brief.why.party")
    return {"item": _("cowork.choice.item"), "account": _("cowork.choice.account"), "payment": _("cowork.choice.payment"),
            "vat": _("cowork.choice.vat"), "department": _("cowork.choice.department"),
            "receipt": _("cowork.choice.receipt")}.get(kind, kind)


def _stop_reasons(found):
    reasons = [_("cowork.folder.brief.why.choice").format(_kind(kind)) for kind in found["choice_kinds"]]
    if found["stop_reasons"].get("human"):
        reasons.append(_("cowork.folder.brief.why.human"))
    if found["stop_reasons"].get("task_off"):
        reasons.append(_("cowork.folder.brief.why.taskOff"))
    if found["stop_reasons"].get("review"):
        # 自动推前判成提议（CC 判断的 B 类在 L0 / L1）：待推送，人在待处理里点一下（数字会计员第 3 期第 4、14 条）
        reasons.append(_("cowork.folder.brief.why.review"))
    if found["stop_reasons"].get("checked"):
        # 没过自动推前核对的 C 类：不是现成的提议，同待处理里的说法「还没推送」（审查处理 10-16）
        reasons.append(_("cowork.company.pending.not_pushed"))
    if found["stop_reasons"].get("account_set"):
        # 跟随公司当年账套的任务这张票的账套定不下来（T1）：同「待选」的说法
        reasons.append(_("cowork.folder.brief.why.choice").format(_("intake.target.accountSet")))
    if found["stop_reasons"].get("pos_layout"):
        reasons.append(_("cowork.folder.brief.why.posLayout"))
    return reasons


def _moving_reasons(found):
    seen = found["moving_reasons"]
    labels = (("low_balance", _("cowork.folder.brief.why.lowBalance")), ("offline", _("cowork.folder.brief.why.offline")),
              ("waiting_purchases", _("cowork.folder.brief.why.purchasesFirst")),
              ("uploading", _("cowork.folder.state.registered")))
    return [label for reason, label in labels if seen.get(reason)]


def _results(rule, rows, states):
    """结果表格放在哪：<根目录>\\<文件夹>\\<结果文件夹>\\<年-月>。只有推送走完（done）的文件出表；月份取登记时的曼谷月份
    （小助手按上传那一刻的月份放），这批跨了月就只写到结果文件夹。小助手一周没同步、记不得根目录时从规则文件夹写起。"""
    done = [row for row in rows if row.state == "done" and row.registered_at]
    if not done:
        return ""
    reader = readers.ref(rule)
    if reader not in states:
        states[reader] = readers.state(*readers.of(rule))
    root = next((row for row in states[reader].get("roots") or []
                 if isinstance(row, dict) and row.get("id") == rule.root_id), {})
    months = {contracts.month_of(notify.epoch(row.registered_at)) for row in done}
    return contracts.full_path(root.get("label") or "", rule.folder or "", contracts.result_folder(rule.result_folder),
                               months.pop() if len(months) == 1 else "")
