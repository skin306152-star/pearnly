"""整份翻译表不跟着每次页面走：按语言 + 内容指纹单独取、浏览器缓存一年。

## 1. 不按用户各存一份（2026-10-02 m7 核实）

Frappe `sessions.get` 把 `get_bootinfo()` 的结果按用户缓存在 redis-cache 的 `bootinfo` 里；其中 `__messages` 是当前语言的
**整份**翻译表（`frappe/boot.py` `load_translations`，单份约 1MB，占每人启动信息 1.13MB 的九成）。几十个用户就把 128mb 的
缓存挤满。`get_bootinfo` 里翻译表先放进去、再跑 `boot_session` 钩子、然后才写缓存，所以在 `boot_session` 拿掉。

## 2. 不随每次打开页面重下（2026-11-13 线上访问速度）

Frappe 原生在 `www/desk.html` 里把整份 `frappe.boot`（含 `__messages`）内联进页面，页面是 `no-store`。泰语 11404 条，
压缩后约 289KB，占每次打开页面 296KB 的九成；晚高峰泰国到服务器只有 30–90KB/s，光它就是 4–6 秒。

改法：`extend_bootinfo`（每次返回前跑、不进缓存）不再放回整表，只给 `pearnly_messages = {lang, v}`，`v` 是这份表的内容指纹；
`desk/messages.js`（本 App 最先加载的脚本）在页面解析阶段同步插入 `messages?lang=…&v=…`，它返回给 `frappe._messages` 赋值的
脚本，指纹对得上就 `public, max-age=31536000, immutable`。翻译一变指纹就变，地址跟着变，浏览器自然取新的。

Frappe 自己的打包脚本在本 App 脚本之前执行；界面在 `DOMContentLoaded` 之后才启动（`frappe.start_app`），那时词表已就位。
例外是 Frappe 打包脚本**加载时**就调用 `__()` 的常量（列表排序的「创建于 / 最后更新日期」、标准字段名、键盘快捷键说明、
数字缩写等）：它们在词表到之前就定了值，只能随页面先给。`EARLY` 是这批原文，启动信息里只带它们的译文（约 30 条，不到 2KB）。
清单来自实验室实测（2026-11-14，Frappe 16.34：在打包脚本执行前挂钩 `__()`，四种语言、ERP / Cowork / DMS 各页、60KB/s 慢网
都是同样 29 条），流程 `desk.boot-messages` 在每次改到这里时重测、多出来就拦；升级 Frappe 时同样重跑。
"""
import hashlib

import frappe

MAX_AGE = 31536000
# Frappe 打包脚本加载时就翻译的原文（见文件头）：译文随启动信息先到，其余整表单独取
EARLY = (
    # 键盘快捷键说明（frappe/ui/keyboard.js）
    "Trigger primary action", "Open Awesomebar", "Open Global Search", "Show keyboard shortcuts", "Clear cache and reload",
    "Open console",
    # 数字缩写（千 / 十亿 / 百万 / 千万 / 十万 等，frappe/utils/number_format.js）
    "T", "B", "M", "K", "Cr", "L", "Kh", "Ar",
    # 相机控件
    "Camera", "Unable to load camera.",
    # 标准字段名（frappe.model.std_fields：列表排序、筛选、报表列）
    "ID", "Created By", "Index", "Created On", "Last Updated On", "Last Updated By", "Tags", "Liked By", "Comments",
    "Assigned To", "Document Status", "Parent",
    "Search",
)


def boot_session(bootinfo):
    """写进按用户缓存之前：去掉整份翻译表。"""
    bootinfo.pop("__messages", None)


def _fingerprint(messages: dict) -> str:
    body = frappe.as_json(messages, indent=None, separators=(",", ":"))
    return hashlib.sha1(body.encode()).hexdigest()[:16]


def extend_bootinfo(bootinfo):
    """每次返回前：只给加载时就要用的那几条译文与指纹，整表由 `messages` 单独取（可缓存）。"""
    if bootinfo.get("pearnly_messages"):
        return
    from frappe.translate import get_messages_for_boot

    lang = str(frappe.local.lang or "en")
    table = get_messages_for_boot()
    bootinfo["__messages"] = {text: table[text] for text in EARLY if text in table}
    bootinfo["pearnly_messages"] = {"lang": lang, "v": _fingerprint(table)}


def _known_lang(lang: str) -> bool:
    from frappe.translate import get_all_languages

    return lang in set(get_all_languages())


@frappe.whitelist(allow_guest=True, methods=["GET"])
def messages(lang=None, v=None):
    """一种语言的整份翻译表，作为脚本返回（给 `frappe._messages` 赋值）。翻译不是秘密：Frappe 与各 App 随源码发布。"""
    from werkzeug.wrappers import Response

    from frappe.translate import get_all_translations

    lang = str(lang or "")
    if not _known_lang(lang):
        return Response("", status=404)
    table = get_all_translations(lang)
    current = _fingerprint(table)
    body = "frappe._messages = Object.assign(frappe._messages || {}, " + frappe.as_json(table, indent=None, separators=(",", ":")) + ");"
    # 指纹对不上（页面是旧的、或翻译刚变）：内容照给最新的，但不许缓存，免得旧地址存住新内容。
    cache = f"public, max-age={MAX_AGE}, immutable" if v == current else "no-cache"
    return Response(body, mimetype="application/javascript", headers={"Cache-Control": cache})
