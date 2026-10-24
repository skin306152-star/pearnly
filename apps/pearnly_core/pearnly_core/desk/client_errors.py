"""页面 JS 报错回传：三个产品的 Desk 页面与前端单页（LIFF 票据页、手机盘点页、CC 对话页）出错时记进本站 Error Log（共用件连带回归闸门，2026-10-25）。

起因：改了一个共用组件，别的页面黑屏 / 报错，没有人看见。浏览器端抓到的报错（`public/desk/errors.js`）发到这里，
记成 Error Log「Browser error」，部署后回读、冒烟都能按它查。零月费：只用本站 Error Log 与缓存。

- 同一条（产品页 + 报错 + 文件 + 行）一小时只记一次；每人每小时最多 30 条、每站每小时最多 300 条，超出的丢掉。
- LINE 里打开的票据页是未登录访客：只收这几个单页路径（`GUEST_ROUTES`）的，按来源 IP 每小时最多 10 条，访客合计每站每小时
  最多 100 条（不挤掉登录用户的额度）。
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
PER_GUEST_IP = 10
PER_GUEST_SITE = 100
GUEST_ROUTES = ("/pearnly-app", "/liff/", "/stocktake")
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


def _within_limits():
    user = frappe.session.user
    if user != "Guest":
        return _count(f"pearnly_client_error_user:{user}", PER_USER) and _count("pearnly_client_error_site", PER_SITE)
    ip = str(getattr(frappe.local, "request_ip", "") or "")
    return bool(ip) and _count(f"pearnly_client_error_ip:{ip}", PER_GUEST_IP) and _count(
        "pearnly_client_error_guests", PER_GUEST_SITE)


@frappe.whitelist(methods=["POST"], allow_guest=True)
def report(message="", source="", line=0, column=0, stack="", route="", kind="error"):
    if frappe.session.user == "Guest" and not str(route or "").startswith(GUEST_ROUTES):
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
    if not _within_limits():
        return {"ok": False, "limited": True}
    frappe.cache.set_value(seen, 1, expires_in_sec=WINDOW)
    frappe.log_error(title=f"{TITLE}: {entry['message'][:100]}", message=json.dumps(entry, ensure_ascii=False, indent=1))
    return {"ok": True}
