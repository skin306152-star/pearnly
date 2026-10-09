"""Narrow internal projection of the authoritative push ledger.

Caller holds Connection -> History -> writer -> Record locks. No authorization
upgrade, review edit, commit or external IO; old issued receipts survive revoke.
"""
import frappe
from frappe import _

FIELDS = ('last_push_status', 'last_pushed_at', 'last_push_record', 'last_push_reason')
EVENT = 'cowork_intake_changed'


def _changed(history, *users):
    """录入列表的推送状态变了：推给这张票的上传人和这次推送的发起人（原生实时推送，事务提交后才发，只带票号），
    他们开着的录入列表收到就重拉（``p.live``）；同事的列表最晚 60 秒兜底刷到。推不出去不影响推送本身。"""
    try:
        owner = frappe.db.get_value('Cowork OCR History', history, 'owner')
        for user in {owner, frappe.session.user, *users} - {None, '', 'Guest'}:
            frappe.publish_realtime(EVENT, {'history': history}, user=user, after_commit=True)
    except Exception:
        pass


def mirror(row, timestamp, *, select=False):
    if row.status not in {'pending', 'success', 'manual'}:
        return
    found = frappe.db.sql("""SELECT workspace,client,last_push_record,last_push_status
        FROM `tabCowork OCR History` WHERE name=%s FOR UPDATE""", (row.history,), as_dict=True)
    if not found or (found[0].workspace, found[0].client or '') != (row.workspace, row.client or ''):
        frappe.throw(_('Not Permitted'), frappe.PermissionError)
    previous = found[0]
    # A late result for another account/connection must not replace the push
    # the user selected more recently. Its own ledger receipt remains visible.
    if not select and previous.last_push_record not in (None, '', row.name):
        return
    if previous.last_push_record == row.name and previous.last_push_status == row.status:
        return
    # 转人工的原因跟着状态走（列表状态图标悬浮看原因，用户 2026-09-26）；其它状态清掉旧原因。
    frappe.db.set_value('Cowork OCR History', row.history, {
        'last_push_status': row.status, 'last_pushed_at': timestamp,
        'last_push_record': row.name,
        'last_push_reason': (row.reason or '')[:500] if row.status == 'manual' else ''}, update_modified=False)
    _changed(row.history, row.get('owner'))


_REVERSE = {}


def source_text(reason):
    """已按某种语言翻好的原因 → 词条原文（英文原句或词条键）：库里存原文，显示时按看的人的语言再翻
    （网页 `p.intake_push_reason`、结果表格 `export_facts.reason_text`）。10-09：后台推送按当时的语言存成了英文句子，
    泰文界面照样显示英文。认不出（带了单号等参数的句子、小助手回报的原文）的原样存。"""
    text = str(reason or "").strip()
    if not text:
        return text
    from frappe.translate import get_all_translations

    for lang in dict.fromkeys([getattr(frappe.local, "lang", None), "th", "zh", "ja"]):
        if not lang or lang == "en":
            continue
        table = get_all_translations(lang) or {}
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


def record_failure(history, reason):
    """还没进账本就失败（目标不可用、目录证据失效、推送未开启……）：只记原因，不改推送状态。

    推送状态只由账本投影；这里写的原因让列表状态图标能显示「失败 + 原因」，下次推成功时由 mirror 清掉。
    调用方已核过这张票的读取范围。
    """
    frappe.db.set_value('Cowork OCR History', history, 'last_push_reason', source_text(reason)[:500],
                        update_modified=False)
    _changed(history)
