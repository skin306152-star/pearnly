"""产品的四种界面语言在 Frappe 的语言表（Language）里都必须是启用的（多产品共用）。

为什么：锁定版本的 Frappe 自带语言表（``frappe/geo/languages.csv``）把 ``ja,日本語`` 标成停用，
网页「个人信息 → 语言」的下拉、``frappe.translate.get_all_languages`` 只列启用的语言，
于是每个站点都选不了日语（2026-11-09 三星真机：搜 th 有 ไทย，搜 ja 什么都没有）。
四种语言在 Frappe 里的代码与产品一致：th / zh / en / ja（中文是 ``zh``；``zh-CN`` 只是身份服务的写法，
见 ``preferences.LANGUAGES``）。

挂在 ``after_migrate``：已有站点随部署迁移启用，新开户收尾回跑 ``after_migrate``（``provisioning``），
两边落到同一状态。缺了哪种语言的记录就先按框架自带表补，补完仍缺就让迁移失败，不静默放过。
"""
import frappe

from pearnly_core.account import ui_language

# 产品支持的界面语言（对话页、Desk、LINE 都是这四种）
PRODUCT_LANGUAGES = ui_language.UI_LANGUAGES


def ensure_product_languages():
    missing = [code for code in PRODUCT_LANGUAGES if not frappe.db.exists("Language", code)]
    if missing:
        from frappe.core.doctype.language.language import sync_languages
        sync_languages()
        missing = [code for code in missing if not frappe.db.exists("Language", code)]
    if missing:
        # 只在迁移 / 开户收尾里出现，让它失败、日志里写明缺哪种（不是界面文案）
        raise RuntimeError("Language records missing: " + ", ".join(missing))
    for code in PRODUCT_LANGUAGES:
        if frappe.db.get_value("Language", code, "enabled"):
            continue
        doc = frappe.get_doc("Language", code)
        doc.enabled = 1
        doc.save(ignore_permissions=True)  # 走原生 on_update：清掉启用语言的缓存
