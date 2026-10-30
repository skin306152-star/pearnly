"""一个人显示成什么（用户 10-30：个人信息加姓名，分清是哪个人）。三个产品与 CC 显示人名都走这里。

姓名就是原生 User 的「名 + 姓」拼出的全名（`full_name`，保存时由框架拼）；没填过姓名的老账号 `first_name` 是开户时写的
用户名，显示出来就还是用户名（存量不动，各人填了才变）。登录名另看 `username`，不在这里。
"""
import frappe
from frappe import _

FIRST_NAME_REQUIRED = "Enter the first name"


def clean(first_name, last_name) -> dict:
    """员工的名 / 姓（ERP、Cowork 团队管理建号与编辑共用）：名必填、姓可空，各最多 140 个字。"""
    first = str(first_name or "").strip()[:140]
    if not first:
        frappe.throw(_(FIRST_NAME_REQUIRED))
    return {"first_name": first, "last_name": str(last_name or "").strip()[:140]}


def display_name(user) -> str:
    if not user:
        return ""
    return frappe.utils.get_fullname(user) or str(user)
