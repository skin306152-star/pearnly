"""Narrow internal projection of the authoritative push ledger.

Caller holds Connection -> History -> writer -> Record locks. No authorization
upgrade, review edit, commit or external IO; old issued receipts survive revoke.
"""
import json

import frappe
from frappe import _

FIELDS = ('last_push_status', 'last_pushed_at', 'last_push_record', 'last_push_reason')
EVENT = 'cowork_intake_changed'
# 推送状态 / 原因变了要知道的入口（hooks.py；文件夹任务据此刷新那份文件的结果与结果表格）。history 域不导入它们。
HOOK = 'cowork_push_state_changed'
# 票的行在读的时候补上推送账本此刻才知道的只读标记（hooks.py；现在只有「在等 Express 关闭」→ ``push_waiting``）。
MARKS = 'cowork_history_push_marks'
FILTERS = 'cowork_history_push_filters'  # 列表「推送状态」筛选里要推送域判的那几档（结果不明）
# 票上记的推送原因最多多长：同写账回执规则 `ack_rules.OPERATOR_REASON_MAX`（代录回执带 AI 接手的完整结论，
# 测试守住两边一致）；history 域不导入回执规则
REASON_MAX = 2100


def _changed(history, *users):
    """录入列表的推送状态变了：推给这张票的上传人和这次推送的发起人（原生实时推送，事务提交后才发，只带票号），
    他们开着的录入列表收到就重拉（``p.live``）；同事的列表最晚 60 秒兜底刷到。推不出去不影响推送本身。"""
    try:
        owner = frappe.db.get_value('Cowork OCR History', history, 'owner')
        for user in {owner, frappe.session.user, *users} - {None, '', 'Guest'}:
            frappe.publish_realtime(EVENT, {'history': history}, user=user, after_commit=True)
    except Exception:
        pass


def marked(rows):
    """录入列表的一页、详情的一张（行带 ``last_push_status`` / ``last_push_record``）：交给登记了
    ``cowork_history_push_marks`` 的入口各补只读字段（一页一次查完）；票上存的四栏推送状态不动。history 域不导入它们。"""
    for path in frappe.get_hooks(MARKS) or []:
        frappe.get_attr(path)(rows)
    return rows


def state_filters(state):
    """录入列表「推送状态」筛选里要推送域判的那几档（``failed`` / ``unknown``）→ (且条件, 或条件)：交给登记了
    ``cowork_history_push_filters`` 的入口（推送域按推送记录判结果不明）；没有登记回两个空表。"""
    for path in frappe.get_hooks(FILTERS) or []:
        return frappe.get_attr(path)(state)
    return [], []


def _announce(history):
    """推送状态 / 原因变了：告诉登记了 ``cowork_push_state_changed`` 的入口（只读、排提交后的任务，不改推送本身）。
    哪一个出错只记日志，不影响推送。"""
    try:
        paths = frappe.get_hooks(HOOK) or []
    except Exception:
        return
    for path in paths:
        try:
            frappe.get_attr(path)(history)
        except Exception:
            try:
                frappe.log_error(title='Cowork push state hook failed', message=frappe.get_traceback()[-1500:])
            except Exception:
                pass


def mirror(row, timestamp, *, select=False):
    if row.status not in {'pending', 'success', 'manual'}:
        return
    found = frappe.db.sql("""SELECT workspace,client,last_push_record,last_push_status,last_push_reason,push_connection,push_account_set
        FROM `tabCowork OCR History` WHERE name=%s FOR UPDATE""", (row.history,), as_dict=True)
    if not found or (found[0].workspace, found[0].client or '') != (row.workspace, row.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    previous = found[0]
    # A late result for another account/connection must not replace the push
    # the user selected more recently. Its own ledger receipt remains visible.
    if not select and previous.last_push_record not in (None, '', row.name):
        return
    target = _target(previous, row, select)
    reason = (row.reason or '')[:REASON_MAX] if row.status == 'manual' else ''
    # 同一条记录、同一状态但原因变了也要抄（10-29 测试机：结果不明核对成「没有入账」状态都是 manual，原先不抄，
    # 票上一直是结果不明，再推被当作结果不明拦下）
    if previous.last_push_record == row.name and previous.last_push_status == row.status \
            and (previous.get('last_push_reason') or '') == reason and not target:
        return
    # 转人工的原因跟着状态走（列表状态图标悬浮看原因，用户 2026-09-26）；其它状态清掉旧原因。
    frappe.db.set_value('Cowork OCR History', row.history, {
        'last_push_status': row.status, 'last_pushed_at': timestamp,
        'last_push_record': row.name,
        'last_push_reason': reason, **target},
        update_modified=False)
    _changed(row.history, row.get('owner'))
    _announce(row.history)


def _target(previous, row, select) -> dict:
    """这次推往哪条连接、哪份账套抄到票上（还没记、或用户这次重新选了）：CC 对话里先「仅识别」再推的票，票上的推送
    目标原本是空的，按公司 / 按账套查单就漏掉它（2026-10-15 测试机：公司对话里已推的快递票查不到）。"""
    connection, account = str(row.get('connection') or ''), str(row.get('account_key') or '')
    if not connection or not account:
        return {}
    if previous.get('push_account_set') and not select:
        return {}
    if (previous.get('push_connection') or '', previous.get('push_account_set') or '') == (connection, account):
        return {}
    return {'push_connection': connection, 'push_account_set': account}


_REVERSE = {}


def source_text(reason):
    """已按某种语言翻好的原因 → 词条原文（英文原句或词条键）：库里存原文，显示时按看的人的语言再翻
    （网页 `p.intake_push_reason`、结果表格 `export_facts.reason_text`）。10-09：后台推送按当时的语言存成了英文句子，
    泰文界面照样显示英文。认不出（带了单号等参数的句子、小助手回报的原文）的原样存。"""
    text = str(reason or "").strip()
    if not text:
        return text
    from frappe.translate import get_all_translations

    # 英文也查：词条键（如 ``push.no_writer``）的英文译文是一句话，英文界面里确认失败时记下的就是那句，要还原成词条键
    # （11-19：英文界面的「没有可写入该账套的电脑」存成了英文句子，换语言照样显示英文）。英文原句当原文的词条译文与原文相同，不进反查
    for lang in dict.fromkeys([getattr(frappe.local, "lang", None), "en", "th", "zh", "ja"]):
        if not lang:
            continue
        try:
            table = get_all_translations(lang) or {}
        except Exception:
            # 读不到翻译表（缓存不可用等）就原样存：还原只是为了换语言好看，不能让记原因这一步失败。
            return text
        cached = _REVERSE.get(lang)
        if cached is None or cached[0] != len(table):
            reverse = {}
            for source, translated in table.items():
                if isinstance(translated, str) and translated.strip() and translated != source:
                    reverse.setdefault(translated.strip(), source)
            cached = _REVERSE[lang] = (len(table), reverse)
        if text in cached[1]:
            return cached[1][text]
    return text


def record_failure(history, reason, *, connection=None, account_set=None):
    """还没进账本就失败（目标不可用、目录证据失效、推送未开启……）：只记原因，不改推送状态。

    推送状态只由账本投影；这里写的原因让列表状态图标能显示「失败 + 原因」，下次推成功时由 mirror 清掉。
    给了 ``connection`` 与 ``account_set``（CC 对话、定时任务推之前停下，这次要推往哪）就一并抄到票上：先「仅识别」再推的票原本没有去向，
    待处理页没法当场选、按公司也归不上（2026-10-15 验收 F17）。调用方已核过这张票的读取范围。
    """
    values = {'last_push_reason': source_text(reason)[:500]}
    if connection and account_set:
        values.update(push_connection=str(connection), push_account_set=str(account_set))
    frappe.db.set_value('Cowork OCR History', history, values, update_modified=False)
    _changed(history)
    _announce(history)


def retarget(history, connection, account_set):
    """定时任务跟随公司当年账套（T1）：这次实际推往的连接与账套抄到票上，换掉识别时记下的任务账套 —— 账本投影
    （`mirror`）不覆盖票上已有的去向，不换的话票上、CC、待处理页都还显示任务存下时那一年的账套。只改去向，不动推送
    状态与原因；一样就不写。调用方已核过这张票的读取范围。"""
    if not connection or not account_set:
        return
    # 加锁读：「一样就不写」按别人刚提交的去向判（关快照隔离后普通读是旧值，11-28 B ③）
    found = frappe.db.get_value('Cowork OCR History', history, ['push_connection', 'push_account_set'], as_dict=True,
                                for_update=True)
    if not found or ((found.push_connection or ''), (found.push_account_set or '')) == (str(connection), str(account_set)):
        return
    frappe.db.set_value('Cowork OCR History', history, {'push_connection': str(connection),
                                                         'push_account_set': str(account_set)}, update_modified=False)


# 自动推送的判定记在票上（数字会计员第 3 期第 9 条）：类别、档位、原因码、提议，与判过的时刻（UTC，扫描据此不重判）
ASSESSMENT_FIELDS = ('auto_class', 'auto_tier', 'auto_reasons', 'auto_proposal', 'auto_checked_at')
REASONS_MAX = 140


def record_assessment(history, *, cls, tier, reasons='', proposal=None, checked_at):
    """记下这张票这次的判定（``cls`` A / B / C，``tier`` 判时这家的档位，``reasons`` 原因码简表，``proposal`` 冻结的提议，
    没有为 None）。只改这几列、不升 ``record_version``、不改 ``modified``（不是人改票，不作废别的准备）。调用方已核过这张票的
    读取范围。"""
    frappe.db.set_value('Cowork OCR History', history, {
        'auto_class': cls if cls in ('A', 'B', 'C') else '', 'auto_tier': tier if tier in ('L0', 'L1', 'L2') else '',
        'auto_reasons': str(reasons or '')[:REASONS_MAX],
        'auto_proposal': json.dumps(proposal, ensure_ascii=False, sort_keys=True, default=str) if proposal else None,
        'auto_checked_at': checked_at}, update_modified=False)


def withdrawn(row):
    """人撤回了排队中的推送（推送记录已转 ``cancelled``）：票上的推送状态还指着它就清回「没推」，可以重推。不升 ``record_version``。"""
    # 加锁读：另一路刚重推、已提交的新记录，普通读看不到——会把新那条的推送状态清掉（11-28 B ③，同下面 withdraw_automation）
    found = frappe.db.get_value('Cowork OCR History', row.history, 'last_push_record', for_update=True)
    if found != row.name:
        return
    frappe.db.set_value('Cowork OCR History', row.history, {
        'last_push_status': None, 'last_pushed_at': None, 'last_push_record': None, 'last_push_reason': ''},
        update_modified=False)
    _changed(row.history, row.get('owner'))
    _announce(row.history)


def withdraw_automation(row, reason, *, proposal=None):
    """自动推的单还没写就作废（领单时这家档位、规则或记忆变了，第 3 期第 5 条）：推送记录已转 ``cancelled``，票上的推送状态
    若还指着它就清回「没推」（改记成提议 ``proposal``，人在待处理里推），原因码并进 ``auto_reasons``。不升 ``record_version``。"""
    # 加锁读：原因码是在原值上追加的，按别人刚提交的值并（MariaDB 关了快照隔离，11-23）
    found = frappe.db.get_value('Cowork OCR History', row.history, ['last_push_record', 'auto_reasons'], as_dict=True,
                                for_update=True)
    if not found:
        return
    values = {'auto_reasons': ','.join(code for code in dict.fromkeys(
        [*(str(found.auto_reasons or '').split(',')), reason]) if code)[:REASONS_MAX]}
    if proposal:
        values['auto_proposal'] = json.dumps(proposal, ensure_ascii=False, sort_keys=True, default=str)
    if found.last_push_record == row.name:
        values.update(last_push_status=None, last_pushed_at=None, last_push_record=None, last_push_reason='')
    frappe.db.set_value('Cowork OCR History', row.history, values, update_modified=False)
    _changed(row.history, row.get('owner'))
    _announce(row.history)
