"""启动信息里的整份翻译表按语言共享，不按用户各存一份（2026-10-02 m7 核实）。

Frappe `sessions.get` 把 `get_bootinfo()` 的结果按用户缓存在 redis-cache 的 `bootinfo` 里；其中 `__messages` 是当前语言的
**整份**翻译表（`frappe/boot.py` `load_translations`，单份约 1MB，占每人启动信息 1.13MB 的九成）。几十个用户就把 128mb 的
缓存挤满，表结构元信息被反复淘汰、各进程重算（累计淘汰 10230 个键）。

不改 Frappe：`get_bootinfo` 里翻译表先放进去、再跑 `boot_session` 钩子、然后才写缓存；`extend_bootinfo` 钩子在每次返回给
浏览器前跑（不进缓存）。所以在前者把它拿掉、在后者按语言放回——放回的是 Frappe 自己按语言缓存的那一份
（`frappe.translate.get_all_translations`），浏览器拿到的内容与原来相同。
"""
import frappe


def boot_session(bootinfo):
    """写进按用户缓存之前：去掉整份翻译表（每次返回前由 `extend_bootinfo` 按语言放回）。"""
    bootinfo.pop("__messages", None)


def extend_bootinfo(bootinfo):
    if "__messages" not in bootinfo:
        from frappe.translate import get_messages_for_boot

        bootinfo["__messages"] = get_messages_for_boot()
