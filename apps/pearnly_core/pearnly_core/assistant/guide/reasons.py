"""引导卡上「没办成 / 留下」的原因文字（多产品共用：Cowork、ERP 的引导引擎与单据源都经这里出字）。纯查表，不碰库。

一张表：原因码 → 词条键（``guide.reason.<码>``，四语在 ``pearnly_core/i18n/messages.tsv``）。码来自引擎（确定时出错）、单据源
（复核挡住、写入计划跳过）与产品（`write_precheck` 的写不了原因、结果行）；产品已经给了 ``reason_text`` 的照用，不在这里重写。

确定时出错（`run._confirm_documents` / `run.confirm` 的兜底）：
- 产品用 ``frappe.throw`` 拒的（``ValidationError`` 一族、``PermissionError``），正式文案就是原因，原样用（去掉标签、截长）；
- 带码的按表；
- 都没有（程序出错）回一句通用的「处理出错」（``guide.reason.failed``）。错误编号只进运行记录（Error Log），不上卡片。
"""
from __future__ import annotations

import html
import re
from typing import Tuple

import frappe


PREFIX = "guide.reason."
FAILED = "failed"   # 兜底：说不出具体原因时的那一句
MAX = 300
_TAG = re.compile(r"<[^>]+>")


def text(code: str) -> str:
    """原因码 → 按本轮语言的一句；表里没有回空（页面只显示状态词，不按码造字）。"""
    code = str(code or "").strip()
    if not code:
        return ""
    key = PREFIX + code
    found = frappe._(key)
    return "" if found == key else str(found)


def _formal(exc: BaseException) -> str:
    """产品 ``frappe.throw`` 的正式文案（已按本轮语言翻好）；不是这类异常或没有文案回空。"""
    kinds = tuple(kind for kind in (getattr(frappe, "ValidationError", None), getattr(frappe, "PermissionError", None))
                  if isinstance(kind, type))
    if not kinds or not isinstance(exc, kinds):
        return ""
    message = html.unescape(_TAG.sub("", str(exc or ""))).strip()
    return message[:MAX]


def failure(exc: BaseException) -> Tuple[str, str]:
    """确定时出错 → ``(原因码, 原因文字)``：码同原来（异常的 ``code``，没有用类名）；文字按正式文案 → 表 → 通用一句。"""
    code = str(getattr(exc, "code", "") or type(exc).__name__)
    return code, _formal(exc) or text(code) or text(FAILED)


__all__ = ["FAILED", "PREFIX", "failure", "text"]
