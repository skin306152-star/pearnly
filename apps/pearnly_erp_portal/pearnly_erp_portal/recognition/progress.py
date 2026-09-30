"""ERP 采购 / 销售网页识别按页的进度（用户 2026-09-30：「录入工作台按页分进度，不要按份，ERP 那边一样」）。

网页识别是一次同步调用；页面发起时带一个随机的进度号，识别每读完一页（多页 PDF 逐页读，见 `policy.read_all`）
就把「已读页数 / 总页数」写进缓存，页面等候期间按这个号轮询。只作展示：按当前用户隔离，缓存写不了不影响识别。
"""
import re

import frappe

PREFIX = "erp:recognise-pages:"
TOKEN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
EXPIRES = 3600


def _key(token):
    return f"{PREFIX}{frappe.session.user}:{token}"


def reporter(token):
    """发起识别时带的进度号 → ``(已读页数, 总页数)`` 的回报函数；没带或格式不对就不回报。"""
    if not isinstance(token, str) or not TOKEN.match(token):
        return None
    key = _key(token)

    def report(done, total):
        try:
            frappe.cache.set_value(key, {"done": int(done), "total": int(total)}, expires_in_sec=EXPIRES)
        except Exception:
            pass  # 进度只作展示
    return report


def read(token):
    if not isinstance(token, str) or not TOKEN.match(token):
        return {}
    value = frappe.cache.get_value(_key(token))
    return value if isinstance(value, dict) else {}
