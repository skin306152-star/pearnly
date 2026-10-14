"""已推送、但票上没记推送目标的识别记录补上目标（2026-10-15）。

CC 对话里先「仅识别」再推的票，推送账本记了连接和账套，票上的 ``push_connection`` / ``push_account_set`` 却是空的，
按公司、按账套查单时漏掉它们。从这张票最近一次选中的推送记录抄过来；已有目标的票不动。幂等。
"""
import frappe


def execute():
    frappe.db.sql("""UPDATE `tabCowork OCR History` h JOIN `tabCowork Push Record` r ON r.name = h.last_push_record
        SET h.push_connection = r.connection, h.push_account_set = r.account_key
        WHERE IFNULL(h.push_account_set, '') = '' AND IFNULL(r.connection, '') != '' AND IFNULL(r.account_key, '') != ''""")
