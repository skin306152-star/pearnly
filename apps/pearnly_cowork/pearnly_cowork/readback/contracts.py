"""Express 回读（2026-10 CC 闭环 L1）的纯规则：CC 桌面端交回的读账结果怎么校验、怎么算期初、出总账与税报的行、给税务草稿的
进销项记录。不连库、不依赖框架。

- 交回的内容与字段名见小助手 ``companion/src/companion/express_read.py``（``RESULT_VERSION``）；``parse`` 逐项校验类型与
  长度，金额一律两位的 Decimal，日期为 date。
- 期初（与测试机上已和 Express 打印件逐笔对过的原型 ``xmake.py`` 同一口径）：
  ``GLBAL.BEGLY + Σ(DEBIT{k}LY − CREDIT{k}LY) + 本月之前所有没作废的凭证的 GLJNLIT 行（借加贷减）``。原型的「从账套首年起
  累计」取的是读到的最早一年，等于更早的全部行。
- 总账：每个科目先一行段头（期初），再按日期、凭证号、行号列本月的行（作废的凭证不列），余额借正贷负；只列有期初或有
  发生额的科目。行的说明空着就用凭证头的说明（同原型）。
- 税报：VATPRD 落在本月的行（没有 VATPRD 的按 VATDAT），软删的不列；作废的金额记 0（版式里名称后加作废字样）。
- 税务草稿用的记录（``vat_records``）：VATPRD、VATDAT 或 DOCDAT 落在本月的行，软删的照给并标 deleted。
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

RESULT_VERSION = 1
CENT = Decimal("0.01")
ZERO = Decimal("0.00")
DEBIT = ("0", "D")
CREDIT = ("1", "C")
CANCELLED = "C"
SIDES = ("P", "S")
# 回读请求点名的单据号（推送过的单）、总账只出的科目前缀
DOCNUMS_MAX = 2000
DOCNUM_MAX = 20
PREFIXES_MAX = 30
PATH_MAX = 500
TEXT_MAX = 300
ROWS_MAX = 2_000_000
_PERIOD = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
_PREFIX = re.compile(r"^[0-9A-Za-z.\-]{1,20}$")
_COLUMN = re.compile(r"^[A-Z0-9_]{1,20}$")


class ContractError(ValueError):
    def __init__(self, code: str = "cowork.readback.invalid"):
        self.code = code
        super().__init__(code)


# ── 请求 ────────────────────────────────────────────────────────────────────


def period(value: Any) -> str:
    found = _PERIOD.fullmatch(str(value or "").strip())
    if not found:
        raise ContractError("cowork.readback.period_invalid")
    return f"{int(found.group(1)):04d}-{found.group(2)}"


def month_range(value: Any) -> Tuple[dt.date, dt.date]:
    """'2026-05' → (2026-05-01, 2026-05-31)；跨年、闰年按日历。"""
    text = period(value)
    year, month = int(text[:4]), int(text[5:])
    first = dt.date(year, month, 1)
    return first, dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)


def prefixes(value: Any) -> Tuple[str, ...]:
    """总账只出这些科目（前缀）；空 = 全部科目。"""
    if value in (None, "", []):
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > PREFIXES_MAX:
        raise ContractError("cowork.readback.accounts_invalid")
    out = []
    for item in value:
        text = str(item or "").strip()
        if not isinstance(item, str) or not _PREFIX.fullmatch(text):
            raise ContractError("cowork.readback.accounts_invalid")
        if text not in out:
            out.append(text)
    return tuple(out)


def docnums(values) -> List[str]:
    """推送过的单据号（点名给电脑，不论日期都读）：去重、去空，超过上限只留前面的。"""
    out = []
    seen = set()
    for value in values or ():
        text = str(value or "").strip()
        if text and len(text) <= DOCNUM_MAX and text.upper() not in seen:
            seen.add(text.upper())
            out.append(text)
    return out[:DOCNUMS_MAX]


def read_params(account_set: Any, account_root: Any, when: Any, named=()) -> Dict[str, Any]:
    """交给电脑的读账参数（``express_read``）：账套文件夹、所在的 Express 根目录、月份、点名的单据号。"""
    account_set, account_root = str(account_set or "").strip(), str(account_root or "").strip()
    if not account_set or len(account_set) > PATH_MAX or len(account_root) > PATH_MAX:
        raise ContractError("cowork.readback.account_missing")
    return {"account_set": account_set, "account_root": account_root, "period": period(when),
            "docnums": docnums(named)}


# ── 交回的读账结果 ──────────────────────────────────────────────────────────


def amount(value: Any) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ContractError("cowork.readback.data_invalid")
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation as error:
        raise ContractError("cowork.readback.data_invalid") from error
    if not number.is_finite():
        raise ContractError("cowork.readback.data_invalid")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def _text(value: Any, limit: int = TEXT_MAX) -> str:
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > limit:
        raise ContractError("cowork.readback.data_invalid")
    return value.strip()


def _date(value: Any) -> Optional[dt.date]:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ContractError("cowork.readback.data_invalid")
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError as error:
        raise ContractError("cowork.readback.data_invalid") from error


def _list(value: Any, limit: int = ROWS_MAX) -> list:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > limit:
        raise ContractError("cowork.readback.data_invalid")
    return value


def _dict(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ContractError("cowork.readback.data_invalid")
    return value


def _month(value: Any) -> str:
    text = _text(value, 7)
    if not _PERIOD.fullmatch(text):
        raise ContractError("cowork.readback.data_invalid")
    return text


def parse(raw: Any, expected_period: str) -> Dict[str, Any]:
    """CC 桌面端交回的 JSON → 校验过的结构（金额 Decimal、日期 date）。版本、月份对不上或任何一项不合规都抛 ContractError。"""
    raw = _dict(raw)
    if raw.get("version") != RESULT_VERSION:
        raise ContractError("cowork.readback.version_unsupported")
    if raw.get("period") != period(expected_period):
        raise ContractError("cowork.readback.data_invalid")
    company = _dict(raw["company"]) if raw.get("company") is not None else {}
    out: Dict[str, Any] = {
        "period": raw["period"], "account_set": _text(raw.get("account_set")), "read_at": _text(raw.get("read_at"), 40),
        "company": {key: _text(company.get(key)) for key in ("name", "tax_id", "branch")},
        "calendar": [], "accounts": {}, "balances": {}, "movements": [], "journal": [], "vat": [], "documents": [],
        "diagnostics": raw.get("diagnostics") if isinstance(raw.get("diagnostics"), dict) else {},
    }
    for row in _list(raw.get("calendar"), 48):
        row = _dict(row)
        index = row.get("index")
        if type(index) is not int or not 1 <= index <= 12 or row.get("slot") not in ("", "NY"):
            raise ContractError("cowork.readback.data_invalid")
        out["calendar"].append({"index": index, "slot": row["slot"], "beg": _date(row.get("beg")),
                                "end": _date(row.get("end"))})
    for row in _list(raw.get("accounts")):
        row = _dict(row)
        code = _text(row.get("code"), 30)
        if code:
            out["accounts"][code] = {key: _text(row.get(key)) for key in ("name", "name2", "group", "level", "parent")}
    for row in _list(raw.get("balances")):
        row = _dict(row)
        code = _text(row.get("code"), 30)
        values = {}
        for name, value in _dict(row.get("values") or {}).items():
            if not isinstance(name, str) or not _COLUMN.fullmatch(name):
                raise ContractError("cowork.readback.data_invalid")
            values[name] = amount(value)
        if code:
            out["balances"][code] = values
    for row in _list(raw.get("movements")):
        row = _dict(row)
        out["movements"].append({"code": _text(row.get("code"), 30), "month": _month(row.get("month")),
                                 "trntyp": _text(row.get("trntyp"), 2).upper(), "amount": amount(row.get("amount"))})
    for voucher in _list(raw.get("journal")):
        voucher = _dict(voucher)
        lines = []
        for line in _list(voucher.get("lines")):
            line = _dict(line)
            lines.append({"seq": _text(line.get("seq"), 10), "account": _text(line.get("account"), 30),
                          "date": _date(line.get("date")), "descrp": _text(line.get("descrp")),
                          "trntyp": _text(line.get("trntyp"), 2).upper(), "amount": amount(line.get("amount")),
                          "dept": _text(line.get("dept"), 20)})
        out["journal"].append({"voucher": _text(voucher.get("voucher"), 30), "book": _text(voucher.get("book"), 4),
                               "date": _date(voucher.get("date")), "descrp": _text(voucher.get("descrp")),
                               "docstat": _text(voucher.get("docstat"), 2).upper(),
                               "creby": _text(voucher.get("creby"), 20), "lines": lines})
    for row in _list(raw.get("vat")):
        row = _dict(row)
        extra = {name: amount(value) for name, value in _dict(row.get("extra") or {}).items()
                 if isinstance(name, str) and _COLUMN.fullmatch(name)}
        out["vat"].append({
            "vatrec": _text(row.get("vatrec"), 2).upper(), "rectyp": _text(row.get("rectyp"), 2),
            "vatprd": _date(row.get("vatprd")), "vatdat": _date(row.get("vatdat")), "docdat": _date(row.get("docdat")),
            "docnum": _text(row.get("docnum"), 30), "refnum": _text(row.get("refnum"), 40),
            "descrp": _text(row.get("descrp")), "prenam": _text(row.get("prenam"), 40),
            "taxid": _text(row.get("taxid"), 20), "branch": _text(row.get("branch"), 10),
            "docstat": _text(row.get("docstat"), 2).upper(), "amt01": amount(row.get("amt01", "0")),
            "vat01": amount(row.get("vat01", "0")), "extra": extra, "deleted": row.get("deleted") is True})
    for row in _list(raw.get("documents")):
        row = _dict(row)
        if row.get("table") not in ("AP", "AR"):
            raise ContractError("cowork.readback.data_invalid")
        out["documents"].append({
            "table": row["table"], "rectyp": _text(row.get("rectyp"), 2), "docnum": _text(row.get("docnum"), 30),
            "docdat": _date(row.get("docdat")), "ref": _text(row.get("ref"), 60), "party": _text(row.get("party"), 30),
            "party_name": _text(row.get("party_name")), "party_taxid": _text(row.get("party_taxid"), 20),
            "flgvat": _text(row.get("flgvat"), 2), **{key: amount(row.get(key, "0"))
                                                       for key in ("amount", "netval", "vatamt", "netamt")},
            "docstat": _text(row.get("docstat"), 2).upper(), "userid": _text(row.get("userid"), 20),
            "deleted": row.get("deleted") is True})
    return out


# ── 期初与总账 ──────────────────────────────────────────────────────────────


def signed(trntyp: str, value: Decimal) -> Optional[Decimal]:
    """借为正、贷为负；认不出借贷的回 None（不算进余额，另报）。"""
    if trntyp in DEBIT:
        return value
    if trntyp in CREDIT:
        return -value
    return None


def opening(code: str, data: Dict[str, Any]) -> Decimal:
    """这个科目本月的期初：BEGLY + Σ(DEBIT{k}LY − CREDIT{k}LY) + 本月之前没作废的凭证行（借加贷减）。"""
    values = data["balances"].get(code) or {}
    total = values.get("BEGLY", ZERO)
    for k in range(1, 13):
        total += values.get(f"DEBIT{k}LY", ZERO) - values.get(f"CREDIT{k}LY", ZERO)
    for row in data["movements"]:
        if row["code"] == code and row["month"] < data["period"]:
            total += signed(row["trntyp"], row["amount"]) or ZERO
    return total


def _seq(value: str) -> tuple:
    return (int(value), value) if value.isdigit() else (0, value)


def month_lines(data: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    """本月每个科目的凭证行（作废的凭证、认不出借贷的行不算），按日期、凭证号、行号排。"""
    first, last = month_range(data["period"])
    found: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for voucher in data["journal"]:
        if voucher["docstat"] == CANCELLED:
            continue
        for line in voucher["lines"]:
            value = signed(line["trntyp"], line["amount"])
            if value is None or not line["date"] or not first <= line["date"] <= last:
                continue
            found[line["account"]].append({
                "date": line["date"], "book": voucher["book"], "voucher": voucher["voucher"], "seq": line["seq"],
                "descrp": line["descrp"] or voucher["descrp"], "debit": line["amount"] if value >= 0 else None,
                "credit": line["amount"] if value < 0 else None, "signed": value})
    for lines in found.values():
        lines.sort(key=lambda line: (line["date"], line["voucher"], _seq(line["seq"])))
    return found


def ledger(data: Dict[str, Any], only: Tuple[str, ...] = ()) -> List[Dict[str, Any]]:
    """总账：[{code, name, opening, lines: [{date, book, voucher, seq, descrp, debit, credit, balance}], debit, credit,
    closing}]，按科目号排；只列（前缀内）有期初或有发生额的科目。"""
    lines = month_lines(data)
    codes = set(data["balances"]) | {row["code"] for row in data["movements"]} | set(lines)
    out = []
    for code in sorted(code for code in codes if code and (not only or code.startswith(only))):
        start = opening(code, data)
        mine = lines.get(code) or []
        if not start and not mine:
            continue
        balance, debit, credit, rows = start, ZERO, ZERO, []
        for line in mine:
            balance += line["signed"]
            debit += line["debit"] or ZERO
            credit += line["credit"] or ZERO
            rows.append({key: line[key] for key in ("date", "book", "voucher", "seq", "descrp", "debit", "credit")}
                        | {"balance": balance})
        out.append({"code": code, "name": (data["accounts"].get(code) or {}).get("name", ""), "opening": start,
                    "lines": rows, "debit": debit, "credit": credit, "closing": balance})
    return out


# ── 税报与税务草稿记录 ──────────────────────────────────────────────────────


def _in(day: Optional[dt.date], first: dt.date, last: dt.date) -> bool:
    return bool(day) and first <= day <= last


def vat_rows(data: Dict[str, Any], side: str) -> List[Dict[str, Any]]:
    """税报的行（side P = 进项、S = 销项）：VATPRD 在本月（没有 VATPRD 按 VATDAT）、没软删；按税票日、单号排。
    invoice = 税票号（REFNUM，没有用单号）、ref = Express 单号（与总账凭证号相同，收入对账按它配）。"""
    if side not in SIDES:
        raise ContractError()
    first, last = month_range(data["period"])
    rows = [row for row in data["vat"] if row["vatrec"] == side and not row["deleted"]
            and _in(row["vatprd"] or row["vatdat"], first, last)]
    rows.sort(key=lambda row: (row["vatdat"] or row["docdat"] or first, row["docnum"]))
    out = []
    for row in rows:
        void = row["docstat"] == CANCELLED
        base, vat = (ZERO, ZERO) if void else (row["amt01"], row["vat01"])
        out.append({"date": row["vatdat"] or row["docdat"], "invoice": row["refnum"] or row["docnum"],
                    "ref": row["docnum"], "name": row["descrp"], "tax_id": row["taxid"], "branch": row["branch"],
                    "base": base, "vat": vat, "total": base + vat, "void": void})
    return out


def vat_records(data: Dict[str, Any], history: Optional[Dict[str, str]] = None) -> List[Dict[str, Any]]:
    """给税务草稿（ภ.พ.30 与进项核查）的 ISVAT 记录：VATPRD、VATDAT 或 DOCDAT 落在本月的行，软删的标 deleted；
    金额两位小数的字符串；推送过的单带上 Pearnly 的识别记录（history）。"""
    first, last = month_range(data["period"])
    known = {str(key).upper(): value for key, value in (history or {}).items() if value}
    out = []
    for row in data["vat"]:
        if row["vatrec"] not in SIDES or not any(_in(row[key], first, last) for key in ("vatprd", "vatdat", "docdat")):
            continue
        item = {"side": row["vatrec"], "rectyp": row["rectyp"],
                "period": row["vatprd"].strftime("%Y-%m") if row["vatprd"] else "",
                "tax_date": row["vatdat"].isoformat() if row["vatdat"] else "",
                "doc_date": row["docdat"].isoformat() if row["docdat"] else "",
                "docnum": row["docnum"], "refnum": row["refnum"], "name": row["descrp"], "prefix": row["prenam"],
                "tax_id": row["taxid"], "branch": row["branch"], "amount": f"{row['amt01']:.2f}",
                "vat": f"{row['vat01']:.2f}", "status": row["docstat"], "deleted": row["deleted"]}
        if known.get(row["docnum"].upper()):
            item["history"] = known[row["docnum"].upper()]
        out.append(item)
    out.sort(key=lambda item: (item["side"], item["tax_date"], item["docnum"]))
    return out


# ── 摘要 ────────────────────────────────────────────────────────────────────


def warnings(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """电脑读账时的异常与口径提示（不挡出表）：缺表、坏行、借贷不平、没有凭证头的行、认不出借贷的行、别的税率组有值。"""
    diag = data.get("diagnostics") or {}
    out = []
    missing = [name for name in diag.get("missing_tables") or [] if isinstance(name, str)]
    if missing:
        out.append({"code": "tables_missing", "tables": missing[:20]})
    bad = {name: count for name, count in (diag.get("bad_rows") or {}).items() if type(count) is int and count}
    if bad:
        out.append({"code": "unreadable_rows", "rows": bad})
    unbalanced = [item for item in diag.get("unbalanced") or [] if isinstance(item, str)]
    if unbalanced:
        out.append({"code": "unbalanced_vouchers", "vouchers": unbalanced[:20]})
    if type(diag.get("orphan_lines")) is int and diag["orphan_lines"]:
        out.append({"code": "lines_without_voucher", "count": diag["orphan_lines"]})
    unknown = sum(1 for voucher in data["journal"] for line in voucher["lines"] if signed(line["trntyp"], ZERO) is None)
    if unknown:
        out.append({"code": "unknown_debit_credit", "count": unknown})
    other = sorted({name for row in data["vat"] for name, value in row["extra"].items() if value})
    if other:
        out.append({"code": "other_vat_columns", "columns": other[:10]})
    return out


def totals(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    base = sum((row["base"] for row in rows), ZERO)
    vat = sum((row["vat"] for row in rows), ZERO)
    return {"count": len(rows), "before_vat": f"{base:.2f}", "vat": f"{vat:.2f}", "total": f"{base + vat:.2f}"}
