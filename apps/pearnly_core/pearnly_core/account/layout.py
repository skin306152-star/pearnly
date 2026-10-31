"""Product-only native User layout extensions; no parallel account form or widget."""
import json

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def ensure_profile_fields():
    if not frappe.conf.get("pearnly_product"):
        return
    create_custom_fields({"User": [
        {"fieldname": "pearnly_company", "label": "Company Name", "fieldtype": "Data",
         "read_only": 1, "is_virtual": 1, "insert_after": "username"},
        {"fieldname": "pearnly_domain", "label": "Your access domain", "fieldtype": "Data",
         "read_only": 1, "is_virtual": 1, "insert_after": "pearnly_company"},
        {"fieldname": "pearnly_change_password", "label": "account.changePassword", "fieldtype": "Button",
         "insert_after": "mute_sounds"},
    ]}, update=True)
    # Native Customize Form uses this DocType-level Property Setter for order.
    # Preserve every existing field, moving only mute/password into User Details.
    meta = frappe.get_meta("User", cached=False)
    moved = ["mute_sounds", "pearnly_change_password"]
    names = [df.fieldname for df in meta.fields if df.fieldname not in moved]
    anchor = names.index("time_zone") + 1
    names[anchor:anchor] = moved
    # 三栏各管一类（用户 10-31）：邮箱 / 名 / 姓 | 用户名 / 公司名 / 访问域名 | 语言 / 时区 / 静音 / 改密码。
    # 旧站点存下的顺序里姓还在原来隐藏时的位置、公司名和域名夹在名姓之间，这里按原生分栏重排。
    _move_after(names, "email", ["first_name", "middle_name", "last_name"])
    _move_after(names, "username", ["pearnly_company", "pearnly_domain"])
    make_property_setter("User", None, "field_order", json.dumps(names), "Text", for_doctype=True)
    # 姓名（用户 10-30：个人信息加姓名，分清是哪个人）：原生「名 / 姓」可改，显示人名的地方用它拼出的全名。
    # 原来 `first_name` 被改叫「用户名」并设成只读，那两条属性设定拿掉（已有站点迁移时删）。
    frappe.db.delete("Property Setter", {"doc_type": "User", "field_name": "first_name", "property": ["in", ["label", "read_only"]]})
    # 用户名（登录名）由后台发号、绑死这个租户，**界面上不能改**（用户 2026-09-23：
    # "可以用用户名登录，但是不能修改…用户只可以自己改密码"）：原生 `username` 一格只读显示。
    # 发号（新建账号）时仍是程序写入，不受影响。邮箱本来就不能改（见 account/profile.py 的守卫）。
    make_property_setter("User", "username", "read_only", "1", "Check")


def _move_after(names, anchor, fields):
    """把 fields（有的）按给定顺序挪到 anchor 后面，其余字段顺序不变。"""
    fields = [f for f in fields if f in names]
    rest = [f for f in names if f not in fields]
    at = rest.index(anchor) + 1
    names[:] = rest[:at] + fields + rest[at:]
