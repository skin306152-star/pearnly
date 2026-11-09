"""代录任务记第一句发出的时刻（``message_at``，11-09）：资料与开始前卡片锚在这一刻之后，没有这一刻、还在准备的任务不出这两张卡。
已有的任务都发出过第一句：补成建任务那一刻（同原来锚的时刻，排法不变）。幂等：只补空着的。"""
import frappe


def execute():
    frappe.db.sql("UPDATE `tabPearnly Express Operator Task` SET message_at = creation WHERE message_at IS NULL")
