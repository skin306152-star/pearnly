# -*- coding: utf-8 -*-
"""DMS 错误码 → 给用户看的话（多语言）。

**为什么光有错误码不够**：`ERR_DMS_CUSTOMER_SAVE` 这种码能让人去查日志，但不能拿给
操作员看。而且同一类失败要说清"是 DMS 拒了"还是"我们复核没查到"—— 那决定用户下一步
该干什么（改字段重试 / 找管理员 / 直接再试一次）。

照搬老产品 `services/erp/erp_dms_push.py` 里的 `_DMS_FRIENDLY`，**文案一字未改**：
这些话是给泰国车行操作员看的，改一个字都可能让他们做错事。五种语言全带（老产品支持
哪几种我们就要有哪几种，不能少）。
"""

from __future__ import annotations

from typing import Dict  # noqa: F401  旧调用方仍可能从本模块取类型

# 认不出的错误码回落到这一条（文案同样在语言包里）
_FALLBACK_CODE = "ERR_UNEXPECTED"


def friendly(code: str, lang: str | None = None):
    """错误码 → 给用户看的话。

    · `friendly(code)` → **全部语言**的字典（前端自己挑语言时用，老调用方不变）
    · `friendly(code, lang="th")` → 只要那一种语言的字符串（LINE/通知按收件人取）

    **认不出的码一律回"服务器内部错误"**，绝不把原始堆栈或英文异常丢给操作员看。

    文案本体在 `translations/<lang>.csv`（key = `dms.err.<码>`），代码里不留文案 ——
    改一个泰文错别字是改数据文件，不是改代码发版。
    """
    from pearnly_dms import i18n

    key = f"dms.err.{code}"
    if lang:
        text = i18n.t(key, lang=lang)
        # 认不出的码 → 兜底那条（同样按收件人语言）
        if text == key:
            text = i18n.t(f"dms.err.{_FALLBACK_CODE}", lang=lang)
        return text
    out = i18n.all_languages(key)
    if not out or all(v == key for v in out.values()):
        out = i18n.all_languages(f"dms.err.{_FALLBACK_CODE}")
    return out
