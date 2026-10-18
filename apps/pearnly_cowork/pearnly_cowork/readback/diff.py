"""回读的账与 Pearnly 推送记录逐张比对（2026-10 CC 闭环 L1）：会计在 Express 里改过、作废、删掉了哪些推送过的单，以及
哪些单据 / 凭证是直接在 Express 里录的。纯函数：输入是 `contracts.parse` 的结果与 `push.express.pushed.project` 的行。

- 每张推送过的单（单据日在本月的，以及会计改到本月的）按 Express 单号找 APTRN（进项）/ ARTRN（销项）：只剩软删行 =
  deleted；一行都没有 = missing；DOCSTAT 作废 = voided；否则逐项比日期、单别（RECTYP：进项 3 = RR / 1 = HP，销项 3 = IV /
  1 = HS）、对方（码，推送时没有码比税号）、对方单号（进项 REFNUM、销项 YOUREF；进项按写入时缩短后的，见
  `push.express.pushed.fit_ref_no`）、未税 / 税额 / 含税（NETVAL / VATAMT / NETAMT，差 0.01 以上）、税务记录（ISVAT 在不在、
  AMT01 / VAT01）、分录（按科目的净额；存货单另有重分类分录，不比）。
- 分录按 CC 桌面版实际写进 Express 的科目比（``_written``）：收付款那一行（进项第一条贷方、销项第一条借方）现购 / 现销记
  账套的现金科目、赊购 / 赊销记对方档上的科目；期末盘存制账套的进项第一条借方是账套默认存货科目时记「购货」科目。这些换法
  CC 桌面版 2026-10-08 起才有，之前照载荷写，两种都认；换成别的科目照报改过。
- POS 日汇总票（现销 HS，单号由 CC 桌面版自编、参考号与销项税 REFNUM 是票号区间、借现金）照上面同样比；同一次写入的
  付款方式转账凭证（回执里的凭证号，`push.express.pushed` 的 ``vouchers``）算推送过的，不算直接录入，并按「借 收款科目 /
  贷 现金」比分录：被删、作废或改过科目金额都记在这张 HS 的分录一项下（前面带凭证号）。
- 直接录入：当月的单据与凭证里，单号不在推送记录里的（作废的不列）；单据带对方与金额，只有凭证的（收付款、调整）带日记账。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from pearnly_cowork.readback import contracts

TABLES = {"purchase": "AP", "sales": "AR"}
VAT_SIDES = {"purchase": "P", "sales": "S"}
KINDS = {("AP", "3"): "RR", ("AP", "1"): "HP", ("AR", "3"): "IV", ("AR", "1"): "HS"}
TOLERANCE = Decimal("0.01")
FIELDS = ("date", "kind", "party", "ref_no", "before_vat", "vat", "total", "vat_record", "vat_before_vat",
          "vat_amount", "entries")
CHANGES = ("changed", "deleted", "missing", "voided")
SHOWN = 300
# 差异的字段 → 自动推档位看的那一类（数字会计员第 3 期 R2，10-18）：分录 = 科目、单别 = 现 / 赊、对方 = 对方档；其余字段（日期、单号、
# 金额、税务记录）不算这三类。被删、作废、找不到的单不比字段，整张当三类都改（`ALL_KINDS`）
FIELD_KINDS = {"entries": "account", "kind": "payment", "party": "party"}
ALL_KINDS = ("account", "payment", "party")


def changed_kinds(items) -> Dict[str, List[str]]:
    """差异表（`compare` 的 ``changes``，或存下的那几行）→ ``{推送记录: [改了的类]}``：``changed`` 按字段换算（`FIELD_KINDS`，
    只改了日期 / 金额的为空表），``deleted`` / ``voided`` / ``missing`` 三类都算。没有推送记录号的行不列。"""
    out: Dict[str, List[str]] = {}
    for item in items or ():
        record = str((item or {}).get("record") or "")
        change = str((item or {}).get("change") or "")
        if not record or change not in CHANGES:
            continue
        kinds = out.setdefault(record, [])
        found = ALL_KINDS if change != "changed" else (FIELD_KINDS.get(str(item.get("field") or "")),)
        for kind in found:
            if kind and kind not in kinds:
                kinds.append(kind)
    return out


def kind(document: Dict[str, Any]) -> str:
    """单别：按 RECTYP；认不出的取单号开头的字母（PS、RE……）。"""
    found = KINDS.get((document["table"], document["rectyp"]))
    if found:
        return found
    letters = ""
    for char in document["docnum"]:
        if not char.isalpha():
            break
        letters += char
    return letters.upper()


def _money(value: Any) -> Decimal:
    try:
        return contracts.amount(str(value or "0"))
    except contracts.ContractError:
        return contracts.ZERO


def _differs(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) > TOLERANCE


def _same(left: Any, right: Any) -> bool:
    return str(left or "").strip().casefold() == str(right or "").strip().casefold()


def _entries(lines) -> Dict[str, Decimal]:
    net: Dict[str, Decimal] = defaultdict(Decimal)
    for account, value in lines:
        net[account] += value
    return {account: value for account, value in net.items() if value}


def _written(item: Dict[str, Any], doc: Dict[str, Any],
             settings: Dict[str, Any]) -> List[Tuple[Tuple[str, ...], Decimal]]:
    """推送载荷的分录 → CC 桌面版写进 Express 的行 [(可能的科目, 借正贷负)]，科目按 CC 桌面版的换法在前、载荷的在后
    （`dbf_writer._write_all` / `dbf_sales._write_all`；换不了时 CC 桌面版也照载荷写）。"""
    purchase = item["direction"] == "purchase"
    paying = "C" if purchase else "D"  # 收付款那一行：进项第一条贷方、销项第一条借方
    cash = item.get("doctype") in ("HP", "HS")
    out, paid, debited = [], False, False
    for line in item["lines"]:
        account, side = line["acc"], line["side"]
        options = [account]
        if side == paying and not paid:
            paid = True
            other = settings.get("cash") if cash else doc.get("party_account")
            if other and other != account:
                options.insert(0, other)
        elif (purchase and side == "D" and not debited and settings.get("periodic") and settings.get("purchases")
              and account == settings.get("stock")):
            options.insert(0, settings["purchases"])
        debited = debited or (purchase and side == "D")
        out.append((tuple(options), _money(line["amount"]) * (1 if side == "D" else -1)))
    return out


def _expected(written, now: Dict[str, Decimal]) -> Dict[str, Decimal]:
    """每行取 Express 里现在有的那个可能科目（都没有取第一个），按科目合计。"""
    return _entries((next((account for account in options if account in now), options[0]), value)
                    for options, value in written)


def _entries_text(entries: Dict[str, Decimal], voucher: str = "") -> str:
    text = "; ".join(f"{account} {value:+.2f}" for account, value in sorted(entries.items()))
    return (f"{voucher}: {text}" if voucher else text)[:SHOWN]


def _now_entries(voucher) -> Dict[str, Decimal]:
    """Express 里这张凭证现在的分录（按科目的净额）；没有或作废了是空的。"""
    if voucher is None or voucher["docstat"] == contracts.CANCELLED:
        return {}
    return _entries((line["account"], contracts.signed(line["trntyp"], line["amount"]))
                    for line in voucher["lines"] if contracts.signed(line["trntyp"], line["amount"]) is not None)


def _pos_vouchers(item: Dict[str, Any], journal: Dict[str, Any], settings: Dict[str, Any]) -> List[tuple]:
    """POS 日汇总票的转账凭证：每张「借 收款科目 / 贷 现金」（现金 = 这张 HS 借方实际记的科目：账套现金科目，
    没设时照载荷），金额 = 这种付款方式当天收的钱。"""
    debit = next((line["acc"] for line in item.get("lines") or [] if line["side"] == "D"), "")
    cash = tuple(dict.fromkeys(account for account in (settings.get("cash"), debit) if account))
    out = []
    for written in item.get("vouchers") or []:
        amount = _money(written.get("amount"))
        if not written.get("acc") or not amount or not cash:
            continue  # 回执的凭证号配不上转账（张数对不上）：只当推送过的，不比
        now = _now_entries(journal.get(written["voucher"].upper()))
        expected = _expected([((written["acc"],), amount), (cash, -amount)], now)
        if any(_differs(expected.get(a, contracts.ZERO), now.get(a, contracts.ZERO)) for a in set(expected) | set(now)):
            out.append(("entries", _entries_text(expected, written["voucher"]), _entries_text(now, written["voucher"])))
    return out


def _party(code: str, name: str) -> str:
    return f"{code} {name}".strip()


def _fields(item: Dict[str, Any], doc: Dict[str, Any], vats, voucher, settings: Dict[str, Any],
            journal: Dict[str, Any]) -> List[tuple]:
    out = []
    pushed_date = item.get("date") or ""
    now_date = doc["docdat"].isoformat() if doc["docdat"] else ""
    if pushed_date and pushed_date != now_date:
        out.append(("date", pushed_date, now_date))
    now_kind = KINDS.get((doc["table"], doc["rectyp"]))
    if item.get("doctype") and now_kind and item["doctype"] != now_kind:
        out.append(("kind", item["doctype"], now_kind))
    if item.get("party_code"):
        if doc["party"] and not _same(item["party_code"], doc["party"]):
            out.append(("party", _party(item["party_code"], item.get("party_name", "")),
                        _party(doc["party"], doc["party_name"])))
    elif item.get("party_tax_id") and doc["party_taxid"] and not _same(item["party_tax_id"], doc["party_taxid"]):
        out.append(("party", _party(item["party_tax_id"], item.get("party_name", "")),
                    _party(doc["party_taxid"], doc["party_name"])))
    if item.get("ref_no") and not _same(item["ref_no"], doc["ref"]):
        out.append(("ref_no", item["ref_no"], doc["ref"]))
    base, vat = _money(item.get("base")), _money(item.get("vat"))
    for field, key, now in (("before_vat", "base", doc["netval"]), ("vat", "vat", doc["vatamt"]),
                            ("total", "total", doc["netamt"])):
        pushed = _money(item.get(key))
        if item.get(key) not in (None, "") and _differs(pushed, now):
            out.append((field, f"{pushed:.2f}", f"{now:.2f}"))
    if vat:
        if not vats:
            out.append(("vat_record", f"{vat:.2f}", ""))
        else:
            row = vats[0]
            if _differs(base, row["amt01"]):
                out.append(("vat_before_vat", f"{base:.2f}", f"{row['amt01']:.2f}"))
            if _differs(vat, row["vat01"]):
                out.append(("vat_amount", f"{vat:.2f}", f"{row['vat01']:.2f}"))
    if not item.get("stock") and item.get("lines"):
        now_entries = _now_entries(voucher)
        pushed_entries = _expected(_written(item, doc, settings), now_entries)
        accounts = set(pushed_entries) | set(now_entries)
        if any(_differs(pushed_entries.get(a, contracts.ZERO), now_entries.get(a, contracts.ZERO)) for a in accounts):
            out.append(("entries", _entries_text(pushed_entries), _entries_text(now_entries)))
    out.extend(_pos_vouchers(item, journal, settings))
    return out


def compare(pushed: List[Dict[str, Any]], data: Dict[str, Any]) -> Dict[str, Any]:
    """{changes: [{docnum, direction, change, field, pushed, now, history, record}], direct: [...], counts: {...}}。"""
    first, last = contracts.month_range(data["period"])
    documents: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for doc in data["documents"]:
        documents[(doc["table"], doc["docnum"].upper())].append(doc)
    vats: Dict[tuple, List[Dict[str, Any]]] = defaultdict(list)
    for row in data["vat"]:
        if not row["deleted"]:
            vats[(row["vatrec"], row["docnum"].upper())].append(row)
    vouchers = {voucher["voucher"].upper(): voucher for voucher in data["journal"]}
    settings = data.get("settings") or {}
    moved_here = {number.upper() for number in month_numbers(data)}
    counts: Counter = Counter()
    changes: List[Dict[str, Any]] = []
    known = {item["docnum"].upper() for item in pushed}
    known |= {written["voucher"].upper() for item in pushed for written in item.get("vouchers") or []}
    for item in sorted(pushed, key=lambda row: row["docnum"]):
        key = item["docnum"].upper()
        if not (first.isoformat() <= (item.get("date") or "") <= last.isoformat() or key in moved_here):
            continue  # 只为税务记录取识别记录查来的（顺延抵扣的进项，单据在别的月份）：不比
        counts["pushed"] += 1
        base = {"docnum": item["docnum"], "direction": item["direction"], "history": item.get("history") or "",
                "record": item.get("record") or ""}
        rows = documents.get((TABLES[item["direction"]], key)) or []
        live = [row for row in rows if not row["deleted"]]
        if not live:
            change = "deleted" if rows else "missing"
        elif live[0]["docstat"] == contracts.CANCELLED:
            change = "voided"
        else:
            found = _fields(item, live[0], vats.get((VAT_SIDES[item["direction"]], key)) or [], vouchers.get(key),
                            settings, vouchers)
            counts["changed" if found else "unchanged"] += 1
            changes.extend({**base, "change": "changed", "field": field, "pushed": before, "now": after}
                           for field, before, after in found)
            continue
        counts[change] += 1
        changes.append({**base, "change": change, "field": "", "pushed": "", "now": ""})
    direct = []
    documented = set()
    for doc in sorted(data["documents"], key=lambda row: (row["docdat"] or first, row["docnum"])):
        key = doc["docnum"].upper()
        documented.add(key)
        if (doc["deleted"] or doc["docstat"] == contracts.CANCELLED or key in known
                or not (doc["docdat"] and first <= doc["docdat"] <= last)):
            continue
        direct.append({"docnum": doc["docnum"], "date": doc["docdat"].isoformat(), "kind": kind(doc),
                       "party": _party(doc["party"], doc["party_name"]), "before_vat": f"{doc['netval']:.2f}",
                       "vat": f"{doc['vatamt']:.2f}", "total": f"{doc['netamt']:.2f}", "entered_by": doc["userid"],
                       "book": ""})
    for voucher in sorted(data["journal"], key=lambda row: (row["date"] or first, row["voucher"])):
        key = voucher["voucher"].upper()
        if (voucher["docstat"] == contracts.CANCELLED or key in known or key in documented
                or not (voucher["date"] and first <= voucher["date"] <= last)):
            continue
        debit = sum((line["amount"] for line in voucher["lines"] if line["trntyp"] in contracts.DEBIT),
                    contracts.ZERO)
        direct.append({"docnum": voucher["voucher"], "date": voucher["date"].isoformat(), "kind": "", "party": "",
                       "before_vat": "", "vat": "", "total": f"{debit:.2f}", "entered_by": voucher["creby"],
                       "book": voucher["book"]})
    counts["direct"] = len(direct)
    return {"changes": changes, "direct": direct,
            "counts": {key: counts.get(key, 0) for key in ("pushed", "unchanged", *CHANGES, "direct")}}


def month_numbers(data: Dict[str, Any]) -> List[str]:
    """本月回读到的单据与凭证号（给推送记录再按单号查一遍：推送时是别的月份、会计改到本月的单）。"""
    first, last = contracts.month_range(data["period"])
    found = {doc["docnum"] for doc in data["documents"] if doc["docdat"] and first <= doc["docdat"] <= last}
    found |= {voucher["voucher"] for voucher in data["journal"]
              if voucher["date"] and first <= voucher["date"] <= last}
    return sorted(item for item in found if item)


def tax_numbers(data: Dict[str, Any]) -> List[str]:
    """本月税务记录（`contracts.tax_month`）的单号：顺延抵扣的进项单据日在别的月份，按单号才找得到它的推送记录，
    税务草稿的记录才带得上识别记录（history）。"""
    first, last = contracts.month_range(data["period"])
    return sorted({row["docnum"] for row in data["vat"] if row["docnum"] and contracts.tax_month(row, first, last)})
