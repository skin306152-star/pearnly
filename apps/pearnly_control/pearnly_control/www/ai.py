"""智能体入口 fatcc.com 的页面（ADR-019）：第一步填用户名或邮箱；多家公司时选一次；账号没有可进的公司时提示。

只在入口站点（站点配置 `pearnly_assistant_entry`）上存在，其余站点一律 404。页面本身由
scripts/build-ai-entry.py 生成（ai.html），这里只给模板变量。打开入口时若记得上次的账号池，先静默确认登录状态。
"""
import frappe
from frappe import _
from frappe.utils import escape_html

from pearnly_control.assistant_entry import entry

no_cache = 1
LANG_LABELS = (("th", "ไทย"), ("zh", "中文"), ("en", "English"))
PRODUCTS = {"cowork": "Cowork", "erp": "ERP", "dms": "DMS"}
SITE_PATH = {"th": "", "en": "/en", "zh": "/zh"}


def get_context(context):
    if not entry.enabled():
        raise frappe.PageDoesNotExistError
    lang = entry.lang()
    frappe.local.lang = lang
    args = frappe.form_dict
    if not (args.get("fresh") or args.get("pick") or args.get("none")):
        url = entry.resume_url()
        if url:
            frappe.local.flags.redirect_location = url
            raise frappe.Redirect
    site = "https://pearnly.com" + SITE_PATH.get(lang, "")
    terms = f'<a href="{site}/terms/" target="_blank" rel="noopener">{escape_html(_("Terms of Service"))}</a>'
    privacy = f'<a href="{site}/privacy/" target="_blank" rel="noopener">{escape_html(_("Privacy Policy"))}</a>'
    mode = "pick" if args.get("pick") else "none" if args.get("none") else "login"
    sites = entry.picks(args.get("pick")) if mode == "pick" else []
    if mode == "pick" and not sites:
        mode = "login"
    context.update({
        "lang": lang, "langs": LANG_LABELS, "products": PRODUCTS, "mode": mode, "sites": sites,
        "pick": args.get("pick") or "",
        "legal_html": escape_html(_("By logging in, you agree to Pearnly's {0} and {1}.")).format(terms, privacy),
        "csrf_token": frappe.sessions.get_csrf_token(),
        "no_breadcrumbs": True,
    })
    return context
