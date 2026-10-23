"""已断开的电脑报过的账套名单删掉（2026-10-24）：之前 `devices.leave` 只作废令牌、名单留着，产品还会按它挑账套、建公司。
之后断开时一并删（`devices.leave`）。"""
import frappe


def execute():
    if not frappe.db.table_exists("Pearnly CC Book") or not frappe.db.table_exists("Pearnly CC Device"):
        return
    frappe.db.sql("""delete book from `tabPearnly CC Book` book join `tabPearnly CC Device` device on device.name = book.device
                     where ifnull(device.token_digest, '') = '' or device.retired_at is not null""")
