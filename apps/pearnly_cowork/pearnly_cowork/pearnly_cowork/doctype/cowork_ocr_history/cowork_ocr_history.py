from frappe.model.document import Document
from pearnly_cowork.history.storage import validate_document, guard_write
import frappe
from frappe import _


class CoworkOCRHistory(Document):
    def before_insert(self):
        guard_write(self)

    def before_validate(self):
        guard_write(self)

    def autoname(self):
        self.name = "CWH-" + self.request_key[:48]

    def validate(self):
        validate_document(self)

    def on_trash(self):
        # 只有删除服务（`history/removal.py`）核过权限与推送后才放行；别的入口删不掉带原件的记录。
        from pearnly_cowork.history.removal import DELETE
        if self.source_file and self.flags.cowork_history_delete is not DELETE:
            frappe.throw(_("Original evidence and ownership cannot be changed."), frappe.PermissionError)


# 组合索引（2026-10-02 m4 压测：这张表没有 workspace 索引，1.1 万行时列表页每次整表扫 + 文件排序、同卖方单号记忆与重复检查沿
# creation 把整表走一遍，MariaDB 的 CPU 翻倍；数据越多越慢）。按「本工作区 → 条件 → 排序列」让查询直接定位、按序取前几条。
# 随 migrate 由 Frappe 原生的 on_doctype_update 建（已有同名索引就跳过）。
INDEXES = {
    "workspace_modified_index": ["workspace", "modified", "name"],              # 录入工作台列表（按修改时间倒序）与计数
    "workspace_seller_tax_index": ["workspace", "seller_tax", "creation"],      # 同卖方单号记忆、按税号找同方
    "workspace_date_seller_index": ["workspace", "invoice_date", "seller_name", "creation"],  # 重复检查（日期 + 卖方）
    "workspace_invoice_no_index": ["workspace", "invoice_no", "creation"],      # 重复检查（单号）
}


def on_doctype_update():
    for index_name, fields in INDEXES.items():
        frappe.db.add_index("Cowork OCR History", fields, index_name)

