"""页面 JS 报错回传：三个产品的 Desk 页面出错时记进本站 Error Log（共用件连带回归闸门，2026-10-25）。

起因：改了一个共用组件，别的页面黑屏 / 报错，没有人看见。浏览器端抓到的报错（`public/desk/errors.js`）发到这里，
记成 Error Log「Browser error」，部署后回读、冒烟都能按它查。零月费：只用本站 Error Log 与缓存。

- 只收已登录用户的（Desk 本来就要登录）；同一条（产品页 + 报错 + 文件 + 行）一小时只记一次；每人每小时最多 30 条、
  每站每小时最多 300 条，超出的丢掉。
- 不记个人信息：网址只留路径（去掉查询串与锚点）；报错与堆栈截短，邮箱、长串数字（电话、税号、卡号）换成占位。
"""
import hashlib
import json
import re

import frappe

TITLE = "Browser error"
WINDOW = 3600
PER_USER = 30
PER_SITE = 300
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_DIGITS = re.compile(r"\d{6,}")
_QUERY = re.compile(r"[?#][^\s)'\"]*")


def clean(text, limit):
    """去个人信息、截短。网址的查询串 / 锚点整段去掉（Desk 路由里可能带单号、搜索词）。"""
    text = _QUERY.sub("", str(text or ""))
    text = _EMAIL.sub("<email>", text)
    text = _DIGITS.sub("<digits>", text)
    return text[:limit]


def _count(key, limit):
    """这一小时的计数 +1，超过上限回 False。"""
    used = int(frappe.cache.get_value(key) or 0)
    if used >= limit:
        return False
    frappe.cache.set_value(key, used + 1, expires_in_sec=WINDOW)
    return True


@frappe.whitelist(methods=["POST"])
def report(message="", source="", line=0, column=0, stack="", route="", kind="error"):
    if frappe.session.user == "Guest":
        return {"ok": False}
    entry = {"kind": clean(kind, 20), "route": clean(route, 200), "message": clean(message, 500),
             "source": clean(source, 300), "line": int(line or 0) if str(line or "0").isdigit() else 0,
             "column": int(column or 0) if str(column or "0").isdigit() else 0, "stack": clean(stack, 3000)}
    if not entry["message"] and not entry["stack"]:
        return {"ok": False}
    digest = hashlib.sha256(json.dumps([entry["route"].split("/")[2:3], entry["message"], entry["source"], entry["line"]],
                                       ensure_ascii=False).encode()).hexdigest()[:24]
    seen = f"pearnly_client_error:{digest}"
    if frappe.cache.get_value(seen):
        return {"ok": True, "repeat": True}
    if not _count(f"pearnly_client_error_user:{frappe.session.user}", PER_USER) or not _count(
            "pearnly_client_error_site", PER_SITE):
        return {"ok": False, "limited": True}
    frappe.cache.set_value(seen, 1, expires_in_sec=WINDOW)
    frappe.log_error(title=f"{TITLE}: {entry['message'][:100]}", message=json.dumps(entry, ensure_ascii=False, indent=1))
    return {"ok": True}
