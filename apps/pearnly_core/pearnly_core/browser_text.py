# -*- coding: utf-8 -*-
"""业务前端取文案：一次把一条键的**四种语言**都取回来。

## 为什么放在公共层（而不是某个产品里）

这个接口服务三个产品的前端页面（Cowork / ERP / DMS），而每个产品站点只装
「框架 + pearnly_core + **它自己那一个**产品 App」。接口住在某个产品里，
另外两个产品的前端就取不到文案 —— 2026-09-19 发现的原状正是如此：
它在 `pearnly_dms` 里，于是 Cowork 的录入页、ERP 的采购页根本调不到。

## 读谁的语言包：按**键的命名空间**判，不按"本站是什么产品"

`dms.*` 读 pearnly_dms 的包、`cowork.*` 读 pearnly_cowork 的包。
不按站点产品判有两个好处：

1. 同一个页面模板可以服务不同命名空间（语言开关切换时不用换接口）；
2. 控制面这种"没有产品"的站点也能取到（它给所有产品发号，界面上会用到产品术语）。

## 安全

这是 `allow_guest` 的接口，所以**只服务白名单里的命名空间** ——
不能当成"随便翻点什么"的翻译查询口（那等于把整张表开放出去）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

import frappe

logger = logging.getLogger(__name__)

# 命名空间前缀 → 语言包住在哪个 App。
# 加新命名空间就在这里加一行：**白名单是安全边界**，不是便利设施。
APP_BY_PREFIX = {
    "dms.": "pearnly_dms",
    "cowork.": "pearnly_cowork",
    "erp.": "pearnly_erp_portal",
    "nav.": "pearnly_core",
    "home.": "pearnly_core",
    "roster.": "pearnly_core",
    "common.": "pearnly_core",
    "intake.": "pearnly_core",
    "purchase.": "pearnly_core",
    # 商品页与库存页的文案在 ERP 那个 App（商品/库存域归它），不在 core。
    "product.": "pearnly_erp_portal",
    "stock.": "pearnly_erp_portal",
    "trade.": "pearnly_erp_portal",
    # 销售记录页的文案在 ERP 那个 App（销售域归它），与商品/库存同一口径。
    "sales.": "pearnly_erp_portal",
    # 供应商 / 客户主档页的文案也在 ERP（采购与销售共用的主数据域）。
    "supplier.": "pearnly_erp_portal",
    "client.": "pearnly_erp_portal",
    # 团队（员工账号与岗位）是 ERP 的功能，页面渲染也在 ERP。
    "team.": "pearnly_erp_portal",
    # 公司（本租户的主体资料：抬头/税号/地址那几栏）也是 ERP 的，页面渲染在 ERP。
    "company.": "pearnly_erp_portal",
    "connect.": "pearnly_core",
    "account.": "pearnly_core",
    "shell.": "pearnly_core",
}

# 四种语言与兜底：与各产品 `i18n.py` 保持一致（泰文是 LINE 通道的语言）。
LANGUAGES = ("th", "en", "zh", "ja")
FALLBACK_LANG = "th"

_cache: dict = {}


def app_of(key: str) -> str:
    """这条键的语言包在哪个 App。不在白名单里返回空串。"""
    for prefix, app in APP_BY_PREFIX.items():
        if key.startswith(prefix):
            return app
    return ""


def pack(app: str, lang: str) -> dict:
    """某 App 某语言的整包（key → 文案）。进程内缓存；读不到按空包处理。"""
    lang = (lang or "").strip() or FALLBACK_LANG
    cached = _cache.get((app, lang))
    if cached is not None:
        return cached
    data: dict = {}
    try:
        path = frappe.get_app_path(app, "translations", f"{lang}.csv")
        # 用框架自己的读取器：它按约定处理 `\\n` 转义与三列（带上下文）的行
        from frappe.translate import get_translation_dict_from_file

        data = get_translation_dict_from_file(path, lang, app) or {}
    except Exception:
        logger.warning("[browser-text] 读不到语言包 %s/%s（按空包处理）", app, lang, exc_info=True)
    _cache[(app, lang)] = data
    return data


def all_languages(key: str) -> Dict[str, str]:
    """一条键的全部语言。认不出的键回落到兜底那条，**不返回空字典**
    （空字典会让页面显示空白；显示兜底话术至少能让人知道出事了）。"""
    app = app_of(key)
    if not app:
        return {}
    out = {}
    for lang in LANGUAGES:
        text = pack(app, lang).get(key, "")
        if text:
            out[lang] = text
    if not out:
        out = {lang: pack(app, lang).get(key, key) for lang in LANGUAGES}
    return out


def clear_cache() -> None:
    """换语言包之后清一次（开发/发布用；正常读走上面的缓存）。"""
    _cache.clear()


def label(key: str, lang: str = "") -> str:
    """**服务端**按键取一条文案（导出件、模板表头、凭证抬头都用它）。

    为什么服务端也要这条路：界面是按 key 取词的（`t("trade.shiftNo")` → 班次编号），
    而服务端的 `_()` 认的是**英文原文**。同一句话只落 key 那一行时，界面是中文、
    导出件里却是英文 —— 用户 2026-09-23 口径："导出的报表术语表口径要统一"。

    取词的顺序：当前语言 → 英文 → key 本身（认不出的 key 直接回显，不返回空串）。
    语言取 `frappe.local.lang`（也就是当前会话界面语言），与页面显示的是同一种。
    """
    texts = all_languages(key)
    if not texts:
        return key
    want = (lang or frappe.local.lang or FALLBACK_LANG)[:2]
    return texts.get(want) or texts.get("en") or key


def _as_keys(wanted: Any) -> List[str]:
    if isinstance(wanted, str):
        try:
            wanted = json.loads(wanted)
        except ValueError:
            wanted = [part.strip() for part in wanted.split(",") if part.strip()]
    if not isinstance(wanted, (list, tuple)):
        return []
    return [str(k or "").strip() for k in wanted]


@frappe.whitelist(allow_guest=True)
def browser_text(keys: Any = None, prefix: str = "") -> dict:
    """页面要的文案：**四语一次给全**（页面有语言开关，切换时不该再跑一趟网络）。

    两种用法：

      · 给了 key 列表 → 只回这些键（每条都必须落在白名单命名空间里）；
      · **给空列表 + prefix** → 回这个命名空间下的整包（例如 `prefix="dms.edit."`）。
        页面因此不必自己维护"我要用哪些键"的清单：清单与语言包一旦不同步，
        页面上就会露出 key（真发生过）。
    """
    wanted = [k for k in _as_keys(keys) if app_of(k)]
    prefix = (prefix or "").strip()
    if not wanted and not prefix:
        return {}
    if prefix and not app_of(prefix):
        frappe.throw(frappe._("Namespace is not allowed: {0}").format(prefix))
    out: Dict[str, Dict[str, str]] = {}
    if not wanted:
        app = app_of(prefix)
        # 整包模式：以**兜底语言**的键集为准列键（缺哪一语的译，由 all_languages 决定）
        for key in pack(app, FALLBACK_LANG):
            if key.startswith(prefix):
                out[key] = all_languages(key)
        return out
    for key in wanted:
        out[key] = all_languages(key)
    return out
