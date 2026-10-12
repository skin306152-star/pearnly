"""推送成功的 Express 单据，取回读比对要的几项（2026-10 CC 闭环 L1，`readback` 域经 `push.application.service.pushed_express`
调用）。纯函数：只读推送记录里冻结的载荷（`mapper` / `sales_mapper` 的形状）与小助手回执，不连库、不依赖框架。

- 日期取载荷的 ``docdate_be``（佛历 YYMMDD，写进 Express 的单据日期）；
- 对方：载荷里的 supplier / customer，Express 里实际用的码以回执 ``meta.party_code`` 为准（新建的档载荷里没有码）；
- 存货单（回执 ``line_modes`` 有存货行）：小助手会另写重分类分录，比对只比表头，不比分录。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

DIRECTIONS = {"purchase": "supplier", "sales": "customer"}


def be_date(value: Any) -> Optional[dt.date]:
    """佛历 YYMMDD（'690515' = 2569-05-15 = 2026-05-15）→ 公历日期；读不出回 None。"""
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    try:
        return dt.date(2500 + int(text[:2]) - 543, int(text[2:4]), int(text[4:6]))
    except ValueError:
        return None


def _amount(value: Any) -> str:
    try:
        number = Decimal(str(value if value not in (None, "") else "0").replace(",", ""))
    except InvalidOperation:
        return ""
    return f"{number:.2f}" if number.is_finite() else ""


def project(name: str, history: Any, docnum: Any, payload: Any, response: Any) -> Optional[Dict[str, Any]]:
    """一条推送成功的记录 → {record, history, docnum, direction, doctype, date, ref_no, party_code, party_name,
    party_tax_id, base, vat, total, lines: [{acc, side, amount}], stock}；不是 Express 进 / 销项单的回 None。"""
    payload = payload if isinstance(payload, dict) else {}
    response = response if isinstance(response, dict) else {}
    direction = payload.get("direction")
    docnum = str(docnum or "").strip()
    if direction not in DIRECTIONS or not docnum:
        return None
    party = payload.get(DIRECTIONS[direction]) if isinstance(payload.get(DIRECTIONS[direction]), dict) else {}
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    day = be_date(payload.get("docdate_be"))
    modes = response.get("line_modes") if isinstance(response.get("line_modes"), list) else []
    lines = []
    for line in payload.get("lines") if isinstance(payload.get("lines"), list) else []:
        if isinstance(line, dict) and str(line.get("acc") or "").strip() and line.get("side") in ("D", "C"):
            lines.append({"acc": str(line["acc"]).strip(), "side": line["side"], "amount": _amount(line.get("amount"))})
    return {"record": name, "history": str(history or ""), "docnum": docnum, "direction": direction,
            "doctype": str(payload.get("doctype") or "").upper(), "date": day.isoformat() if day else "",
            "ref_no": str(payload.get("ref_no") or "").strip(),
            "party_code": str(meta.get("party_code") or party.get("code") or "").strip(),
            "party_name": str(party.get("name") or "").strip(), "party_tax_id": str(party.get("tax_id") or "").strip(),
            "base": _amount(payload.get("base_amount")), "vat": _amount(payload.get("vat_amount")),
            "total": _amount(payload.get("total_amount")), "lines": lines,
            "stock": any(isinstance(mode, dict) and str(mode.get("mode") or "").startswith("stock") for mode in modes)}


def in_window(item: Dict[str, Any], first: Optional[dt.date], last: Optional[dt.date]) -> bool:
    """单据日期落在 [first, last]；没给期间（只按单据号找）就都不算。"""
    if first is None or last is None:
        return False
    return bool(item.get("date")) and first.isoformat() <= item["date"] <= last.isoformat()
