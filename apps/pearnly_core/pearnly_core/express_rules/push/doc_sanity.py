# -*- coding: utf-8 -*-
"""Express 推送单据防呆体检(确定性纯函数 · 不连库 · 不调 LLM)。

preflight 在方向判定后、映射前调用。挡掉"不该当普通票直接入账"的单据,命中即返
规范 reason → preflight 落 manual 转人工(doc28 §8 deferred 的"转人工即可"):

  currency_not_thb        外币票(无币种核对会把 USD 当泰铢记 · 金额性质错 · 最严重)
                          currency 字段为准;字段空时票号/票面文本里的外币记号兜底
  credit_note             贷项/退货单(应走负向冲销,不当正向采购/销项)
  date_implausible        古董日期(y<2000 · POS 时钟没设的票 · 必假)
  date_future             未来日期(反造假 · 标可疑复核;Express 收不收没有实录,待测试机实测)

判定全确定性(币种归一 / 关键词 / 日期比较),只读票面字段,绝不写库。空信号
一律放行 —— 仅在票面"明确"出现外币/贷项/未来日期时拦,避免误伤正常票。

Express 与 MR.ERP 都不拦的不再拦(用户 2026-11-04):买卖方同税号(方向判不出照旧停下问进项还是销项)、
押金 / 定金收据、补开 / 倒签(ใบแทน)、对方税号不是 13 位。
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

# 票面 currency 字段归一小写后,视为"泰铢/未标注"的取值(放行)。其余一律视外币。
_THB_CURRENCY = {"", "thb", "บาท", "baht", "฿", "thai baht"}

# 贷项/退货单印记(document_type 兜底 · 票面措辞)。
_CREDIT_KW = ("ใบลดหนี้", "credit note", "creditnote")

# 外币记号(currency 字段空时的兜底)。token 词表保守只认无歧义币种码/币名,
# 符号只在贴着数字或 us$ 组合时算(泰铢票用 ฿,"$400" 必是外币)。
_FC_TOKEN_RE = re.compile(
    r"\b(usd|eur|gbp|jpy|cny|rmb|sgd|myr|hkd|krw|aud|dollar|dollars|euro|yen)\b"
)
_FC_SYMBOL_RE = re.compile(r"us\$|\$\s*\d|\d\s*\$|€|£|¥")
_FC_THAI_KW = ("ดอลลาร์", "ยูโร", "เยน")


def _foreign_currency_marker(
    fields: Dict[str, Any], history: Dict[str, Any], blob: str
) -> Optional[str]:
    """currency 字段空时,从票号 + 票面文本里找外币记号。返回记号(命中)或 None。"""
    probe = " ".join(
        (
            str(history.get("invoice_no") or ""),
            str(fields.get("invoice_number") or fields.get("invoice_no") or ""),
            blob,
        )
    ).lower()
    m = _FC_TOKEN_RE.search(probe)
    if m:
        return m.group(1)
    if _FC_SYMBOL_RE.search(probe):
        return "symbol"
    if any(k in probe for k in _FC_THAI_KW):
        return "th-marker"
    return None


def _text_blob(fields: Dict[str, Any]) -> str:
    """票面自由文本(备注 + 摘要 + 行项目名)归一小写,供关键词匹配。"""
    parts: List[str] = [str(fields.get("notes") or ""), str(fields.get("category") or "")]
    items = fields.get("items")
    if isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                parts.append(str(it.get("name") or ""))
    return " ".join(parts).lower()


def _parse_iso_date(raw: Any) -> Optional[date]:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(raw or "").strip())
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def check_document(
    fields: Dict[str, Any],
    history: Dict[str, Any],
    *,
    today: date,
) -> Optional[str]:
    """跑单据防呆,返回规范 manual reason(命中)或 None(放行)。按严重度从高到低。"""
    # 1 · 币种(最严重):非泰铢票不能当泰铢直接入账。
    currency = str(fields.get("currency") or "").strip().lower()
    if currency and currency not in _THB_CURRENCY:
        return f"currency_not_thb:{currency[:12]}"

    # 票面自由文本(关键词匹配用)。
    blob = _text_blob(fields)

    # 1b · 币种记号兜底:OCR 没抽出 currency,但票号/票面文本带外币记号(对抗票 15:
    # currency=None 而票号是 INV-USD-0007)→ 同样确认外币。
    if not currency:
        marker = _foreign_currency_marker(fields, history, blob)
        if marker:
            return f"currency_not_thb:{marker[:12]}"

    # 2 · 贷项/退货单:应走负向冲销。document_type 优先,措辞兜底。
    doc_type = str(fields.get("document_type") or "").strip().lower()
    if doc_type == "credit_note" or any(k in blob for k in _CREDIT_KW):
        return "credit_note"

    # 3 · 日期合理性:古董日期(POS 时钟没设印佛历 2513=1970 · 真机语料)/ 未来日期 → 标可疑复核。
    # 与录入侧 ocr_corrections 日期合理窗同口径(y<2000 必假)。
    invoice_date = _parse_iso_date(history.get("invoice_date") or fields.get("date"))
    if invoice_date is not None and invoice_date.year < 2000:
        return "date_implausible"
    if invoice_date is not None and invoice_date > today:
        return "date_future"
    return None
