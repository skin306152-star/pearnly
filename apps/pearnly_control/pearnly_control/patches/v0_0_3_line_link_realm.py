"""LINE 关联记录补上账号池（2026-10-04 改为每个 LINE 一个专用身份，按池计数）。

改造前写入的行没有池：按产品默认的池补（当时的关联就是按产品写进去的）。没配账号池的
环境（本机样板）跳过；之后站点重新同步时会按实际签发方改写。
"""
import frappe

from pearnly_control.account.member_provider import MemberError, MemberProvider


def execute():
    rows = frappe.get_all("Pearnly LINE Login Link", filters={"realm": ("is", "not set")},
                          fields=["name", "product"])
    realms = {}
    for row in rows:
        if row.product not in realms:
            try:
                realms[row.product] = MemberProvider(row.product).realm
            except MemberError:
                realms[row.product] = None
        if realms[row.product]:
            frappe.db.set_value("Pearnly LINE Login Link", row.name, "realm", realms[row.product],
                                update_modified=False)
