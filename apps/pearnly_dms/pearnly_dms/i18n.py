# -*- coding: utf-8 -*-
"""文案取用层：**代码里只留 key，文案进语言包**。

## 为什么要有它（而不是把文案写在代码里）

老产品把 LINE 卡片文案直接写死在 Python 里（`TXT_MENU_ITEM1 = "จัดทำข้อมูลลูกค้า"`），
后果是：改一个泰文错别字要动代码、发版；加一种语言要在每个文件里再写一套常量；
而网页端**另有一套**语言包（`static/dms/dms-i18n-*.js`）—— 同一句话两处维护，早晚不一致。

新平台用**框架原生的翻译机制**：`apps/<app>/translations/<lang>.csv`，一行 `key,文案`。
代码里只写 key，文案全在数据文件里。

## 为什么 msgid 用语义键而不是英文原文

Frappe 惯例是拿英文原句当 msgid（`_("Save")`）。这里**故意不那样做**，两个理由：

1. **旧包能逐字迁移**：老产品的包本来就是键式的（`'dx-title': 'บันทึก...'`）——
   键式 msgid 可以**直接搬过来，零翻译成本、零窜改风险**。换成英文原文当 msgid，
   得先人工把泰文倒着翻成英文，这一步本身就会引入错误。
2. **键稳定**：泰文改一个词，键不动，四份语言的对应关系不会错位。

代价是 `bench update-translations` 自动抽取用不上（它按源码里的英文句子抽）。
这个代价我们接受：文案是**照搬**来的，不是新写的，本来就不需要"抽取"。

## 两个入口（区别很重要）

- `t(key)` —— 按**当前会话**的语言（网页界面用）。
- `t(key, lang="th")` —— 按**收件人**的语言。

第二个不是多余的：LINE 的对方是泰国销售，而操作后台的人可能开着中文界面。
错误话术必须按**收件人**取，否则泰国销售会收到一句中文报错
（老产品为此专门把话术做成 `{码: {语言: 文案}}` 的字典，就是这个原因）。
"""

from __future__ import annotations

import logging

import frappe

logger = logging.getLogger(__name__)

APP = "pearnly_dms"
PREFIX = "dms."

# 本项目支持的语言（与 translations/ 下的文件名一一对应）。
#
# **四种**：中 / 英 / 泰 / 日 —— 与产品界面上的语言开关一致（老产品 `scripts/check_i18n.py`
# 写的就是这个集合）。
#
# ⚠️ **不要因为某个字典里出现了 `zh_TW` 就给它建一套包。** 老产品那份错误话术字典里
# 确实有 `zh_TW` 的条目，但它的正规做法是 **`zh_TW` 回落到 `zh`**（同一份 check_i18n.py
# 写明："zh_TW 通过 fallback 到 zh"）。这一档不是要维护的第五种语言；建包会让某个词
# 只在繁体里被单独改掉，两边慢慢就不一致了。回落逻辑见 `pack()`。
LANGUAGES = ("th", "en", "zh", "ja")
FALLBACK_LANG = "th"

_cache: dict[str, dict] = {}


def pack(lang: str) -> dict:
    """某语言的整包（key → 文案）。进程内缓存；读不到就当空包（上层有兜底）。"""
    lang = (lang or "").strip() or FALLBACK_LANG
    if lang in _cache:
        return _cache[lang]
    data: dict = {}
    try:
        path = frappe.get_app_path(APP, "translations", f"{lang}.csv")
        # 用框架自己的 CSV 读取器：它按约定处理 \\n 转义与三列（带上下文）的行
        from frappe.translate import get_translation_dict_from_file

        data = get_translation_dict_from_file(path, lang, APP) or {}
    except Exception:
        logger.warning("[i18n] 读不到语言包 %s（按空包处理）", lang, exc_info=True)
    # 带地区码的没包就回落基语言（`zh_TW` → `zh`）—— 不为此多维护几套译文。
    base = lang.split("_")[0].split("-")[0]
    if not data and base and base != lang:
        data = pack(base)
    _cache[lang] = data
    return data


def t(key: str, lang: str | None = None, **kwargs) -> str:
    """取一条文案。

    `lang=None` → 按当前会话语言（走框架的翻译字典）；
    给了 lang → 按那个语言（发给谁就用谁的语言）。
    **取不到就返回 key 本身** —— 界面会露出一个 key，比露出空白或英文异常好排查。
    """
    text = ""
    if lang:
        text = pack(lang).get(key, "")
    else:
        text = frappe._(key) or ""
        if text == key:
            text = ""
    if not text:
        text = pack(FALLBACK_LANG).get(key, "") or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def all_languages(key: str) -> dict:
    """一条 key 的全部语言（给"前端自己挑语言"这类调用方）。

    认不出的 key 回落到兜底那条，**不返回空字典** —— 空字典会让前端显示空白，
    而"显示一句兜底话术"至少还能让人知道出事了。
    """
    out = {}
    for lang in LANGUAGES:
        text = pack(lang).get(key, "")
        if text:
            out[lang] = text
    if not out:
        out = {lang: pack(lang).get(key, key) for lang in LANGUAGES}
    return out


def clear_cache() -> None:
    """换语言包之后清一次（开发/发布用；正常读走上面的缓存）。"""
    _cache.clear()
