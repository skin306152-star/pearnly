"""回读的账与 Pearnly 推送记录逐张比对（2026-10 CC 闭环 L1）：会计在 Express 里改过、作废、删掉了哪些推送过的单，以及
哪些单据 / 凭证是直接在 Express 里录的。纯函数：输入是 `contracts.parse` 的结果与 `push.express.pushed.project` 的行。

- 每张推送过的单按 Express 单号找 APTRN（进项）/ ARTRN（销项）：只剩软删行 = deleted；一行都没有 = missing；
  DOCSTAT 作废 = voided；否则逐项比日期、单别（RECTYP：进项 3 = RR / 1 = HP，销项 3 = IV / 1 = HS）、对方（码，推送时
  没有码比税号）、对方单号（进项 REFNUM、销项 YOUREF）、未税 / 税额 / 含税（NETVAL / VATAMT / NETAMT，差 0.01 以上）、
  税务记录（ISVAT 在不在、AMT01 / VAT01）、分录（按科目的净额；存货单另有重分类分录，不比）。
- 直接录入：当月的单据与凭证里，单号不在推送记录里的（作废的不列）；单据带对方与金额，只有凭证的（收付款、调整）带日记账。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Dict, List

from pearnly_cowork.readback import contracts

TABLES = {"purchase": "AP", "sales": "AR"}
VAT_SIDES = {"purchase": "P", "sales": "S"}
KINDS = {("AP", "3"): "RR", ("AP", "1"): "HP", ("AR", "3"): "IV", ("AR", "1"): "HS"}
TOLERANCE = Decimal("0.01")
FIELDS = ("date", "kind", "party", "ref_no", "before_vat", "vat", "total", "vat_record", "vat_before_vat",
          "vat_amount", "entries")
CHANGES = ("changed", "deleted", "missing", "voided")
SHOWN = 300


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


def _entries_text(entries: Dict[str, Decimal]) -> str:
    return "; ".join(f"{account} {value:+.2f}" for account, value in sorted(entries.items()))[:SHOWN]


def _party(code: str, name: str) -> str:
    return f"{code} {name}".strip()


def _fields(item: Dict[str, Any], doc: Dict[str, Any], vats, voucher) -> List[tuple]:
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
        pushed_entries = _entries((line["acc"], _money(line["amount"]) * (1 if line["side"] == "D" else -1))
                                  for line in item["lines"])
        now_entries = {}
        if voucher is not None and voucher["docstat"] != contracts.CANCELLED:
            now_entries = _entries((line["account"], contracts.signed(line["trntyp"], line["amount"]))
                                   for line in voucher["lines"]
                                   if contracts.signed(line["trntyp"], line["amount"]) is not None)
        accounts = set(pushed_entries) | set(now_entries)
        if any(_differs(pushed_entries.get(a, contracts.ZERO), now_entries.get(a, contracts.ZERO)) for a in accounts):
            out.append(("entries", _entries_text(pushed_entries), _entries_text(now_entries)))
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
    counts: Counter = Counter()
    changes: List[Dict[str, Any]] = []
    known = set()
    for item in sorted(pushed, key=lambda row: row["docnum"]):
        key = item["docnum"].upper()
        known.add(key)
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
            found = _fields(item, live[0], vats.get((VAT_SIDES[item["direction"]], key)) or [], vouchers.get(key))
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
