"""推送成功的 Express 单据，取回读比对要的几项（2026-10 CC 闭环 L1，`readback` 域经 `push.application.service.pushed_express`
调用）。纯函数：只读推送记录里冻结的载荷（`mapper` / `sales_mapper` 的形状）与小助手回执，不连库、不依赖框架。

- 日期取载荷的 ``docdate_be``（佛历 YYMMDD，写进 Express 的单据日期）；
- 对方：载荷里的 supplier / customer，Express 里实际用的码以回执 ``meta.party_code`` 为准（新建的档载荷里没有码）；
- 存货单（回执 ``line_modes`` 有存货行）：小助手会另写重分类分录，比对只比表头，不比分录。
- 进项票号取写进 Express 时缩短后的（``fit_ref_no``）。
- POS 日汇总票（载荷 3 的 ``pos`` 块，现销 HS、单号由小助手自编）：同一次写入的付款方式转账凭证号在回执
  ``meta.pos_vouchers``（`common.sanitize_push_meta` 从小助手的 ``notes.pos_vouchers`` 留下），与 ``pos.transfers``
  逐张对应（同一顺序写）：``vouchers`` = [{voucher, acc, amount}]，回读据此不把它们算成直接录入、并比它们的分录。

另有 ``written``：同一份冻结载荷 → 现购 / 赊购、哪层定的、记的科目，交给 CC 照实告诉用户（验收 F19）。
"""
from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

DIRECTIONS = {"purchase": "supplier", "sales": "customer"}
REF_NO_BYTES = 15  # Express 的 REFNUM C(15)（APTRN / ISVAT），按账套编码 cp874 计


def fit_ref_no(value: Any, limit: int = REF_NO_BYTES) -> str:
    """进项票号写进 Express REFNUM 时 CC 桌面版的缩法（照 ``companion/src/companion/purchase_adapter.fit_ref_no``，
    `tests/test_companion_contract.py` 锁住两边一致）：放得下原样；放不下先去掉分隔符（- / . 空格），还放不下保留后面
    limit 字节。"""
    text = str(value or "").strip()

    def size(item: str) -> int:
        return len(item.encode("cp874", "replace"))

    if size(text) <= limit:
        return text
    compact = re.sub(r"[\s\-/.]", "", text)
    while size(compact) > limit:
        compact = compact[1:]
    return compact


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


def _vouchers(payload: Dict[str, Any], meta: Dict[str, Any]) -> List[Dict[str, str]]:
    """POS 日汇总票同一次写入的转账凭证：回执里的凭证号按顺序配 ``pos.transfers``（张数对不上时只留凭证号）。"""
    numbers = meta.get("pos_vouchers")
    if not isinstance(numbers, list):
        notes = meta.get("notes") if isinstance(meta.get("notes"), dict) else {}
        numbers = notes.get("pos_vouchers") if isinstance(notes.get("pos_vouchers"), list) else []
    numbers = [str(item).strip() for item in numbers if str(item or "").strip()]
    pos = payload.get("pos") if isinstance(payload.get("pos"), dict) else {}
    transfers = pos.get("transfers") if isinstance(pos.get("transfers"), list) else []
    paired = len(transfers) == len(numbers)
    out = []
    for index, number in enumerate(numbers):
        transfer = transfers[index] if paired and isinstance(transfers[index], dict) else {}
        out.append({"voucher": number, "acc": str(transfer.get("acc") or "").strip(),
                    "amount": _amount(transfer.get("amount")) if transfer else ""})
    return out


def project(name: str, history: Any, docnum: Any, payload: Any, response: Any) -> Optional[Dict[str, Any]]:
    """一条推送成功的记录 → {record, history, docnum, direction, doctype, date, ref_no, party_code, party_name,
    party_tax_id, base, vat, total, lines: [{acc, side, amount}], stock, vouchers: [{voucher, acc, amount}]}；
    不是 Express 进 / 销项单的回 None。"""
    payload = payload if isinstance(payload, dict) else {}
    response = response if isinstance(response, dict) else {}
    direction = payload.get("direction")
    docnum = str(docnum or "").strip()
    if direction not in DIRECTIONS or not docnum:
        return None
    party = payload.get(DIRECTIONS[direction]) if isinstance(payload.get(DIRECTIONS[direction]), dict) else {}
    meta = response.get("meta") if isinstance(response.get("meta"), dict) else {}
    day = be_date(payload.get("docdate_be"))
    ref = str(payload.get("ref_no") or "").strip()
    modes = response.get("line_modes") if isinstance(response.get("line_modes"), list) else []
    lines = []
    for line in payload.get("lines") if isinstance(payload.get("lines"), list) else []:
        if isinstance(line, dict) and str(line.get("acc") or "").strip() and line.get("side") in ("D", "C"):
            lines.append({"acc": str(line["acc"]).strip(), "side": line["side"], "amount": _amount(line.get("amount"))})
    return {"record": name, "history": str(history or ""), "docnum": docnum, "direction": direction,
            "doctype": str(payload.get("doctype") or "").upper(), "date": day.isoformat() if day else "",
            "ref_no": fit_ref_no(ref) if direction == "purchase" else ref,
            "party_code": str(meta.get("party_code") or party.get("code") or "").strip(),
            "party_name": str(party.get("name") or "").strip(), "party_tax_id": str(party.get("tax_id") or "").strip(),
            "base": _amount(payload.get("base_amount")), "vat": _amount(payload.get("vat_amount")),
            "total": _amount(payload.get("total_amount")), "lines": lines,
            "stock": any(isinstance(mode, dict) and str(mode.get("mode") or "").startswith("stock") for mode in modes),
            "vouchers": _vouchers(payload, meta) if direction == "sales" else []}


def in_window(item: Dict[str, Any], first: Optional[dt.date], last: Optional[dt.date]) -> bool:
    """单据日期落在 [first, last]；没给期间（只按单据号找）就都不算。"""
    if first is None or last is None:
        return False
    return bool(item.get("date")) and first.isoformat() <= item["date"] <= last.isoformat()


# 载荷单据类型 → 现 / 赊（Express：HP 现购记贷现金科目、RR 赊购记贷应付；HS 现销、IV 赊销）。
PAYMENT_OF = {("purchase", "HP"): "cash", ("purchase", "RR"): "credit", ("sales", "HS"): "cash", ("sales", "IV"): "credit"}
# 载荷 doctype_src（现 / 赊哪层定的，`payment.py` 六级漏斗与 `mapper` 的记过 / 任务默认）→ 给 CC 的说法。
# 没有这一项的旧记录、认不出的值不给 payment_from，不猜。
PAYMENT_FROM = {"manual": "chosen", "remembered": "remembered", "task_default": "task_default",
                "explicit": "document", "doc_type": "document_type", "profile": "supplier_profile",
                "bank": "bank", "config_default": "default"}


def written(payload: Any) -> Optional[Dict[str, str]]:
    """推送记录里冻结的 Express 载荷 → 这张单是怎么写进 Express 的几项事实（验收 F19：CC 只知道单号，答不出现购还是
    赊购，叫用户去 Express 核对）：{payment, payment_from?, expense_account | revenue_account, vat?}。
    不是 Express 进 / 销项载荷（MR.ERP 等）回 None。不带单号、对方、金额等别的东西，结果要小。"""
    payload = payload if isinstance(payload, dict) else {}
    direction = payload.get("direction")
    payment = PAYMENT_OF.get((direction, str(payload.get("doctype") or "").strip().upper()))
    if not payment:
        return None
    out = {"payment": payment}
    source = "pos_summary" if isinstance(payload.get("pos"), dict) else PAYMENT_FROM.get(
        str(payload.get("doctype_src") or "").strip())
    if source:
        out["payment_from"] = source
    account = str(payload.get("items_account") or "").strip()
    if account:
        out["expense_account" if direction == "purchase" else "revenue_account"] = account
    if direction == "purchase":
        vat = _amount(payload.get("vat_amount"))
        if payload.get("doc_lane") == "expense":
            capitalized = _amount(payload.get("vat_capitalized"))
            out["vat"] = "added_to_cost" if capitalized and Decimal(capitalized) > 0 else "none"
        elif vat:
            out["vat"] = "claimed" if Decimal(vat) > 0 else "none"
    return out
