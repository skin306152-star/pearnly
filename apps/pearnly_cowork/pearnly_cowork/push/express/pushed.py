"""推送成功的 Express 单据，取回读比对要的几项（2026-10 CC 闭环 L1，`readback` 域经 `push.application.service.pushed_express`
调用）。纯函数：只读推送记录里冻结的载荷（`mapper` / `sales_mapper` 的形状）与小助手回执，不连库、不依赖框架。

- 日期取载荷的 ``docdate_be``（佛历 YYMMDD，写进 Express 的单据日期）；
- 对方：载荷里的 supplier / customer，Express 里实际用的码以回执 ``meta.party_code`` 为准（新建的档载荷里没有码）；
- 存货单（回执 ``line_modes`` 有存货行）：小助手会另写重分类分录，比对只比表头，不比分录。
- 进项票号取写进 Express 时缩短后的（``fit_ref_no``）。
- POS 日汇总票（载荷 3 的 ``pos`` 块，现销 HS、单号由小助手自编）：同一次写入的付款方式转账凭证号在回执
  ``meta.pos_vouchers``（`common.sanitize_push_meta` 从小助手的 ``notes.pos_vouchers`` 留下），与 ``pos.transfers``
  逐张对应（同一顺序写）：``vouchers`` = [{voucher, acc, amount}]，回读据此不把它们算成直接录入、并比它们的分录。

另有 ``written``：同一份冻结载荷 → 现购 / 赊购、哪层定的、记的科目及其来源，交给 CC 照实告诉用户（验收 F19）；第 2 期起
再带冻结目标里的 ``decided_by``（每一类按什么定的：账套记忆 / 公司规则 / Express 历史 / 任务默认）。
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
                "company_rule": "company_rule", "explicit": "document", "doc_type": "document_type",
                "profile": "supplier_profile", "bank": "bank", "config_default": "default"}
# 冻结目标 decided_by 里认的类别与来源（`push.express.company_choices`）；认不出的不给
DECIDED_KINDS = ("party", "item", "account", "payment", "vat", "department", "receipt")
DECIDED_SOURCES = ("task_default", "remembered", "company_rule", "express_history", "mixed")

# 载荷 account_source（记的科目哪层定的，`mapper` / `sales_mapper` 与推送服务 `_decisions`）→ 给 CC 的说法。
# 改版前的旧记录（只有笼统的 choice，或没有这一项）不给 account_from，不猜。
ACCOUNT_FROM = {"chosen": "chosen", "remembered": "remembered", "task_default": "task_default",
                "express_history": "express_history", "company_rule": "company_rule", "category_map": "matched",
                "config_default": "default"}


# 推前的「会怎么写」（`planned`）里，现 / 赊由这几层定时，票面本身指向的现 / 赊另给（票面没写 / 票种推不出来的不给）
CHOICE_LAYERS = ("remembered", "company_rule", "task_default")
# 票面这一侧的付款判据（`payment.payment_verdict` 的来源）→ 同 PAYMENT_FROM 一套词
DOCUMENT_FROM = {"explicit": "document", "doc_type": "document_type", "profile": "supplier_profile", "bank": "bank"}


def planned(payload: Any, decided_by: Any = None, company_rules: Any = None,
            document_verdict: Any = None) -> Optional[Dict[str, Any]]:
    """推之前这张单会怎么写进 Express（复核页「要选的项」与 CC 的 `cowork_push_choices`）：与入账后的 ``written`` 同一个函数、
    同一份载荷（准备推送那一步试算出的），所以推前说的与写进去的是同一个值。

    现 / 赊由账套记忆、公司规则或任务默认定（``payment_from`` 在 `CHOICE_LAYERS`），而票面自己指向另一个值时（``document_verdict``
    是不看这些选择时票面的判定 ``(是否已付, 来源)``），另给 ``document_payment`` ``{payment, from}``：CC 推前据此说清「按记住的
    现购写，不按票面的赊购」（2026-10-20 教程录制：推前说赊购，写进去是现购，事后才解释）。不是 Express 进 / 销项载荷回 None。"""
    out = written(payload, decided_by, company_rules)
    if not out:
        return None
    paid, source = document_verdict if isinstance(document_verdict, tuple) and len(document_verdict) == 2 else (None, "")
    if out.get("payment_from") in CHOICE_LAYERS and paid is not None and source in DOCUMENT_FROM:
        document = "cash" if paid else "credit"
        if document != out.get("payment"):
            out["document_payment"] = {"payment": document, "from": DOCUMENT_FROM[source]}
    return out


DOCNUM_BYTES = 12  # Express 单号 DOCNUM C(12)（ARTRN / APTRN / ISVAT / GLJNL），按账套编码 cp874 计


def numbers(payload: Any, docnum: Any) -> Dict[str, Any]:
    """票面号在 Express 里没有原样留下时给 CC 的说法（2026-10-20 教程录制：简易税票 ABB6909-00007 超出单号宽度，CC 桌面版改用
    自编号 HS690912-001，CC 只报了新单号）：

    - 销项（POS 日汇总除外，它本来就自编号）：单号就该是票面号，不同时给 ``printed_number``（票面号，Express 的参考号与销项税报
      里仍是它）；超出单号宽度再给 ``number_reason: too_long``；
    - 进项：单号本来就由账套自己排；票面号超出参考号宽度（REFNUM 15 字节）被缩写时给 ``printed_number`` 与
      ``reference_written``（写进 Express 的参考号）、``number_reason: too_long``。

    不是 Express 进 / 销项载荷、没有票号或单号的回空。"""
    payload = payload if isinstance(payload, dict) else {}
    direction = payload.get("direction")
    ref, docnum = str(payload.get("ref_no") or "").strip(), str(docnum or "").strip()
    if (direction, str(payload.get("doctype") or "").strip().upper()) not in PAYMENT_OF or not ref or not docnum:
        return {}
    size = len(ref.encode("cp874", "replace"))
    if direction == "sales":
        if isinstance(payload.get("pos"), dict) or ref.upper() == docnum.upper():
            return {}
        return {"printed_number": ref, **({"number_reason": "too_long"} if size > DOCNUM_BYTES else {})}
    written = fit_ref_no(ref)
    if written == ref:
        return {}
    return {"printed_number": ref, "reference_written": written, "number_reason": "too_long"}


def _rules_used(company_rules: Any, rules_now: Any) -> list:
    """冻结目标里用上的公司规则（`company_choices.used`）→ 给 CC 的 ``[{kind, for, tax_id?, value, value_name?, branch?,
    still_saved?}]``：``for`` 是这条规则的对象（卖方 / 买方名，存货是品名），``tax_id`` 取自归类键里的 13 位税号。
    ``rules_now`` ``{规则编号: 现在的值}`` 给了才说 ``still_saved``（编号在、值没变）；值改了另给 ``now_value``。"""
    out = []
    for item in company_rules if isinstance(company_rules, list) else ():
        if not isinstance(item, dict) or item.get("kind") not in DECIDED_KINDS or not str(item.get("value") or ""):
            continue
        parts = str(item.get("subject") or "").split("|")
        rule = {"kind": item["kind"], "for": str(item.get("label") or ""), "value": str(item["value"])}
        if len(parts) > 1 and len(parts[1]) == 13 and parts[1].isdigit():
            rule["tax_id"] = parts[1]
        if item.get("value_label") and item["value_label"] != item["value"]:
            rule["value_name"] = str(item["value_label"])
        rule["branch"] = str(item.get("branch") or "") or "whole company"
        if isinstance(rules_now, dict) and item.get("id"):
            now = rules_now.get(str(item["id"]))
            rule["still_saved"] = now == item["value"]
            if now is not None and now != item["value"]:
                rule["now_value"] = str(now)
        out.append(rule)
    return out


def written(payload: Any, decided_by: Any = None, company_rules: Any = None,
            rules_now: Any = None) -> Optional[Dict[str, Any]]:
    """推送记录里冻结的 Express 载荷 → 这张单是怎么写进 Express 的几项事实（验收 F19：CC 只知道单号，答不出现购还是
    赊购，叫用户去 Express 核对）：{payment, payment_from?, debit_account | revenue_account, account_from?, vat?,
    decided_by?, company_rules?}。``decided_by`` 是冻结目标里的「每一类按什么定的」（第 2 期），与 ``account_from`` 同一套词；
    ``company_rules`` 是冻结目标里用上的是哪几条公司规则（`_rules_used`，``rules_now`` 核现在还在不在）。
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
        # 进项叫 debit_account（费用或库存采购的借方都可能，期末盘存制账套 CC 桌面版还会把默认存货科目换成购货科目，
        # 复审 L3），销项是收入科目
        out["debit_account" if direction == "purchase" else "revenue_account"] = account
        account_from = ACCOUNT_FROM.get(str(payload.get("account_source") or "").strip())
        if account_from:
            out["account_from"] = account_from
    if direction == "purchase":
        vat = _amount(payload.get("vat_amount"))
        if payload.get("doc_lane") == "expense":
            capitalized = _amount(payload.get("vat_capitalized"))
            out["vat"] = "added_to_cost" if capitalized and Decimal(capitalized) > 0 else "none"
        elif vat:
            out["vat"] = "claimed" if Decimal(vat) > 0 else "none"
    decided = {kind: source for kind, source in (decided_by if isinstance(decided_by, dict) else {}).items()
               if kind in DECIDED_KINDS and source in DECIDED_SOURCES}
    if decided:
        out["decided_by"] = decided
    used = _rules_used(company_rules, rules_now)
    if used:
        out["company_rules"] = used
    return out
