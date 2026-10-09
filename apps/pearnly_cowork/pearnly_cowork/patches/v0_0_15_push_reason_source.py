"""推送失败原因改存词条原文（2026-10-09，`history/push_state.source_text`）：以前按推送当时的语言存成句子，
换个语言看就是别的语言（泰文界面出现英文原因）。把已存成中 / 泰 / 日文句子的旧原因还原成原文；英文原句本来就是原文，
网页按看的人的语言再翻。认不出的不动。重跑只会得到同样的值。"""
import frappe

from pearnly_cowork.history.push_state import source_text


def execute():
    rows = frappe.db.sql("""SELECT name, last_push_reason FROM `tabCowork OCR History`
        WHERE IFNULL(last_push_reason, '') != ''""", as_dict=True)
    for row in rows:
        value = source_text(row.last_push_reason)
        if value and value != row.last_push_reason:
            frappe.db.set_value("Cowork OCR History", row.name, "last_push_reason", value[:500], update_modified=False)
