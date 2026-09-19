# -*- coding: utf-8 -*-
"""订车推送**台账**写入（落到 `Pearnly Push Log`）。

## 台账是干什么的

每往 DMS 推一次单就留一行："谁、什么时候、推了什么、结果如何"。用途是对账与排障：
销售说"我昨天那单呢"能查到；推失败了能看出是哪一步；**顾问 id 与名字一起记**，
月底核提成归属时不用回查主档。

## 两条必须保持的规矩

1. **结果如实记**：成功记 `success`/200，失败记 `failed`/0 + 错误码 —— 台账不是宣传栏。
2. **台账失败不能影响业务结果**（用户那单已经推成功了）—— 写台账的异常一律吞掉并留日志。

## 与老产品的两处差异（有意，别照抄回去）

· `trigger` 填 **`line`**：新平台这张表的选项是 `manual / line / auto`，
  老产品那边这个位置写的是 `id_card`/`line_dms`（它自己的表）。照抄会直接校验失败
  —— 这个坑 `dms_flow._write_push_log` 里已经踩过并留了注释。
· 台账正文（`request_body`）里的 `trigger: "line_dms"` 保留为**来源标记**，
  与建档那条台账同一个写法，记录页才能按同一个口径筛。

照搬老产品 `services/line_dms/booking_ledger.py`（线上基准 `501589f0`）的内容，
落点改成新平台的推送台账。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from pearnly_dms.booking.conversation.qa_util import deposit_total

logger = logging.getLogger(__name__)

LOG = "Pearnly Push Log"


def booking_log_request_body(qa: Dict[str, Any]) -> Dict[str, Any]:
    """台账正文：推了什么。**qa 块只进摘要**（audit 那份太大，留在会话里）。"""
    qa = qa or {}
    answers = qa.get("answers") or {}
    adv = qa.get("advisor") or {}
    payments = qa.get("payments") or []
    return {
        "adapter": "mrerp_dms",
        "trigger": "line_dms",
        "mode": "booking",
        "customer_id": str((qa.get("customer") or {}).get("id") or ""),
        "car_id": str((answers.get("car") or {}).get("id") or ""),
        "paint_id": str((answers.get("paint") or {}).get("id") or ""),
        "advisor_id": str(adv.get("id") or ""),
        # 归属名与 id 同层：对账翻台账时不用回查主档，也不用跨层拼。
        "advisor_name": str(adv.get("name") or ""),
        "qa": {
            "place_id": str((answers.get("place") or {}).get("id") or ""),
            "term_id": str((answers.get("term") or {}).get("id") or ""),
            "regis_id": str((answers.get("regis") or {}).get("id") or ""),
            "regis_name": str(answers.get("regis_name") or ""),
            "payments": [
                {"channel": str(p.get("channel") or ""), "amount": str(p.get("amount") or "")}
                for p in payments
            ],
            "earnest_total": str(deposit_total(payments)),
            "slip_attached": bool((qa.get("files") or {}).get("slip_mid")),
            "master_snapshot_version": str((qa.get("master_snapshot") or {}).get("version") or ""),
            "master_snapshot_at": str((qa.get("master_snapshot") or {}).get("captured_at") or ""),
            "master_validated_at": str(
                (qa.get("master_validation") or {}).get("validated_at") or ""
            ),
        },
    }


def booking_log_response_body(result: Dict[str, Any]) -> Dict[str, Any]:
    """落库结果：DMS 回的单据 id/单号、附件挂了几件、失败原因。"""
    result = result or {}
    ok = bool(result.get("ok"))
    body: Dict[str, Any] = {
        **(result.get("response_body") or {}),
        "booking_id": result.get("booking_id", ""),
        "booking_no": result.get("booking_no", ""),
    }
    if "attach_ok" in result:
        body["attach_ok"] = result.get("attach_ok")
        body["attached"] = result.get("attached", 0)
        body["attach_failed"] = result.get("attach_failed") or []
    if not ok:
        body["raw_error"] = (result.get("response_body") or {}).get("raw_error", "")
    return body


def log_booking(
    *,
    endpoint: str,
    payload: Dict[str, Any],
    result: Dict[str, Any],
    actor: Optional[str] = None,
) -> Optional[str]:
    """按结果如实落一条订车台账；**写台账失败不影响业务结果**（吞掉并留日志）。

    返回台账行名；写失败返回 `None`。
    """
    result = result or {}
    ok = bool(result.get("ok"))
    qa = (payload or {}).get("qa") or {}
    try:
        import frappe

        doc = frappe.new_doc(LOG)
        doc.status = "success" if ok else "failed"
        doc.endpoint = endpoint or None
        # 触发方式取本平台台账的 Select 选项（manual/line/auto）—— 这条是 LINE 推的。
        doc.trigger = "line"
        doc.attempt = 1
        doc.business_key = str(result.get("booking_no") or "")
        doc.http_status = 200 if ok else 0
        doc.elapsed_ms = int(result.get("elapsed_ms") or 0)
        doc.request_body = json.dumps(booking_log_request_body(qa), ensure_ascii=False, default=str)
        doc.response_body = json.dumps(
            booking_log_response_body(result), ensure_ascii=False, default=str
        )
        doc.error_msg = (result.get("error_code") or "")[:2000]
        if actor:
            doc.actor = actor
        doc.insert(ignore_permissions=True)
        frappe.db.commit()
        return doc.name
    except Exception:
        logger.warning("[dms] 订车台账写入失败（不影响业务结果）", exc_info=True)
        return None
