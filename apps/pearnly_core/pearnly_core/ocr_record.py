# -*- coding: utf-8 -*-
"""写识别记录的唯一入口（三套产品共用）。

为什么要有这个：首页的「识别记录」要按天/月/年筛、要能导出，都要有个**事实来源**。
老产品是 `ocr_history`；新平台没有，就补这一张。

规矩：

- **成败都记**。失败不记的话，用户看到的是"点了没反应"，我们也无从查起。
- 写记录**绝不能影响识别本身** —— 记不上只记日志，不把已经成功的识别说成失败。
"""

from __future__ import annotations

import logging

import frappe

logger = logging.getLogger(__name__)

DOCTYPE = "Pearnly OCR Record"

OK = "ok"
NEEDS_REVIEW = "needs_review"
FAILED = "failed"


def record(product: str, status: str, *, filename: str = "", page_count: int = 1,
           charged_thb: float | None = None, reference: str = "", detail: str = "",
           error: str = "", user: str | None = None) -> str | None:
    """落一条识别记录。返回记录名；失败返回 None（**不抛**）。"""
    try:
        doc = frappe.get_doc({
            "doctype": DOCTYPE,
            "product": product,
            "status": status,
            "user": user or frappe.session.user,
            "filename": (filename or "")[:140],
            "page_count": int(page_count or 1),
            "charged_thb": float(charged_thb) if charged_thb is not None else None,
            "reference": (reference or "")[:140],
            "detail": (detail or "")[:500],
            "error": (error or "")[:500],
        })
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name
    except Exception:  # noqa: BLE001
        logger.exception("写识别记录失败（不影响识别本身）")
        return None
