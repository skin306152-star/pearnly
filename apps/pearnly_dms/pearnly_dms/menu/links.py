"""One tenant's LIFF entry contract, shared by Flex and persistent rich menus."""
from urllib.parse import urlencode

import frappe
from pearnly_core import line


def account_action(action, label):
    if action not in ("portal", "credentials") or not line.liff_id():
        return None
    query = urlencode({"action": action})
    desktop = frappe.utils.get_url().rstrip("/") + "/liff/dms-booking?" + query
    # **两个动作都走 LIFF（在 LINE 内打开）**。为什么门户不再用
    # `openExternalBrowser=1`：那样 LINE 会把**我们的 LIFF 页本身**丢进系统浏览器，
    # 页面拿 `liff.getIDToken()` 证明身份时什么都没有 → 401 →
    # 只显示一句"这个 OA 还没启用"（2026-09-21 线上实测）。
    # 正确地"出站"是**先拿票再出**：页面在 LINE 内验完身份、换到 60 秒票据后，
    # 自己 `liff.openWindow({external: true})` 把票据带去系统浏览器 ——
    # DMS 的 cookie 也就落在真浏览器里了（老产品 `dms-booking-edit.js` 的 portalMode 同款）。
    uri = f"https://liff.line.me/{line.liff_id()}?{query}"
    return {"type": "uri", "label": str(label), "uri": uri, "altUri": {"desktop": desktop}}
