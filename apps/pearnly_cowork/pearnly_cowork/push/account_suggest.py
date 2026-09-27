"""识别落库后给「选了目标 ERP、没选账套」的票补上推送账套（用户 2026-09-27）。

LINE 里只选目标、进项 / 销项与入账方式（快捷回复一次最多 12 个选项，放不下几十上百份账套），
账套由识别结果自动挑（规则见 `targets/account_match.py`），挑不出来就空着，用户在编辑页选。
网页「新建录入」已经选了账套的票不经过这里。挂在 `cowork_ocr_recognized` 钩子上、排在 LINE 推卡片
之前（卡片上要显示挑到的账套）；识别落库已按入口授权，这里只读目录、只写这几张票的
`push_account_set`，不刷新目录、不推送。
"""
import json

import frappe

from pearnly_cowork.connections import directory
from pearnly_cowork.contracts.history import primary_page_fields
from pearnly_cowork.targets import account_match

HISTORY = "Cowork OCR History"
_RECENT = 50


def recent_accounts(connection):
    """这条连接最近推过的账套（新的在前、去重）：同一公司有几份账套时用来挑。"""
    rows = frappe.db.sql(
        f"""SELECT push_account_set FROM `tab{HISTORY}`
        WHERE push_connection=%s AND IFNULL(push_account_set, '')!=''
          AND last_push_status IN ('success', 'pending', 'manual')
        ORDER BY last_pushed_at DESC LIMIT {_RECENT}""", (connection,))
    seen = []
    for (key,) in rows:
        if key not in seen:
            seen.append(key)
    return seen


def fill(connection, histories, direction=None):
    """按识别结果给这几张票挑账套 → `{票名: {account_set_key, reason, candidates}}`。"""
    adapter, rows = directory.current(connection)
    if not rows:
        return {}
    recent = recent_accounts(connection)
    results = {}
    for name in histories:
        pages = json.loads(frappe.db.get_value(HISTORY, name, "pages") or "[]")
        fields = primary_page_fields(pages if isinstance(pages, list) else [])
        result = account_match.suggest(adapter, rows, direction=fields.get("direction") or direction,
                                       fields=fields, recent=recent)
        if result["account_set_key"]:
            frappe.db.set_value(HISTORY, name, "push_account_set", result["account_set_key"], update_modified=False)
        results[name] = result
    return results


def recognized(batch):
    """`cowork_ocr_recognized` 钩子：选了目标 ERP 却没选账套的这一批，按识别结果挑；挑账套出错不影响识别结果。"""
    if batch.get("target_account_set") or not batch.get("target_connection") \
            or batch.get("intake_target") not in ("express", "mrerp"):
        return
    records = batch.get("history_records") or "[]"
    names = json.loads(records) if isinstance(records, str) else records
    if not names:
        return
    try:
        fill(batch.target_connection, names, batch.get("direction"))
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.log_error(title="Cowork account suggestion failed", message=frappe.get_traceback()[-1500:])
