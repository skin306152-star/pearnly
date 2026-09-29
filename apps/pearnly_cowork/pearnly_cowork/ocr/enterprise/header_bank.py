"""Bank statement / passbook rows anchored on the printed column headers (added 2026-09-29).

The deployed local reconstruction (``local/``, kept byte-identical) places the amount and balance
columns at fixed fractions of the table width — the layout of the 3 September pilot statements — so
other layouts (SCB historical statements, KBank passbooks photographed on a desk, ...) never balance
and every page goes to the paid 3.8 schema extraction. This is a second attempt, used only when the deployed one does not balance:

- the columns come from the printed header labels (Thai / English / Chinese), not fixed fractions;
- every money value in the balance column below the header is one row, its amount and date are the
  values on the same (tilt-corrected) level in the amount and date columns;
- from there it is the deployed parser again: directions from adjacent printed balances, the deployed
  bounded balance-chain repair (audited and flagged on the row), the deployed document gate
  (``check_document``) and a count of printed date rows, so a balanced subset with missing rows cannot
  pass.

Dates must not go backwards (one misread between two equal dates takes that date, as the deployed
parser does). Newest-first statements, unreadable dates or a first page without a header return
``None`` and the file goes on to the schema extraction exactly as before.
"""

from __future__ import annotations

import datetime
import re
import statistics
import unicodedata
from decimal import Decimal

from pearnly_cowork.ocr.enterprise.local.bank_chain import _infer_bank_directions, _repair_bank
from pearnly_cowork.ocr.enterprise.local.common import TIME_RE, _line_direction, _loose_money, money, norm_text
from pearnly_cowork.ocr.enterprise.local.result import Reconstruction, check_document

LABELS = {
    "balance": ("คงเหลือ", "balance", "余额", "结存"),
    "withdrawal": ("ถอน", "หักบัญชี", "withdrawal", "withdraw", "debit", "取款", "支出"),
    "deposit": ("ฝาก", "เข้า", "deposit", "credit", "存款", "存入"),
}
CARRY = ("ยอดยกมา", "ยอดเงินคงเหลือยกมา", "balanceforward", "broughtforward", "bf")
THAI_MONTHS = {"มค": 1, "กพ": 2, "มีค": 3, "เมย": 4, "พค": 5, "มิย": 6, "กค": 7, "สค": 8, "กย": 9, "ตค": 10,
               "พย": 11, "ธค": 12}  # keys keep the vowel marks: ม.ค. is January, มี.ค. March
# Day, month, year with one repeated separator ("8 05/07/25" is row 8 on 5 July, not 8 May 2007).
# No look-behind: a passbook's row number is sometimes printed against the date ("705/07/25" is row 7).
DATE_RE = re.compile(r"(\d{1,2})\s*([/.-])\s*(\d{1,2})\s*\2\s*(\d{2,4})(?!\d)")
THAI_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\s*([\u0e01-\u0e4e][\u0e01-\u0e4e.\s]{1,6}?)\s*(\d{2,4})(?!\d)")
SMALL_RE = re.compile(r"\d{1,2}\.\d{2}")
HEADER_BAND = 0.03  # header labels printed in two or three languages stack within this height
BETAS = [step / 1000 for step in range(-40, 41)]  # photographed pages: y drifts with x


def _label(token):
    text = norm_text(token.text)
    return next((kind for kind, words in LABELS.items() if any(norm_text(word) in text for word in words)), None)


def _carry(text):
    compact = norm_text(text)
    return any(norm_text(word) in compact for word in CARRY)


def resolve_year(raw, reference_year):
    """A printed year → C.E.: Buddhist years (25xx) minus 543; two digits are C.E. unless that is in the future."""
    if raw >= 2400:
        return raw - 543
    if raw >= 1900:
        return raw
    if raw < 100:
        return 2000 + raw if 2000 + raw <= reference_year + 1 else 1957 + raw
    return None


def parse_date(text, reference_year):
    text = unicodedata.normalize("NFKC", str(text or ""))
    match = DATE_RE.search(text)
    if match:
        day, month, year = int(match.group(1)), int(match.group(3)), int(match.group(4))
    else:
        match = THAI_DATE_RE.search(text)
        month = THAI_MONTHS.get(re.sub(r"[.\s]", "", match.group(2))) if match else None
        if not month:
            return ""
        day, year = int(match.group(1)), int(match.group(3))
    year = resolve_year(year, reference_year)
    try:
        return datetime.date(year, month, day).isoformat() if year else ""
    except ValueError:
        return ""


def _header(tokens, lines):
    """The column centres (withdrawal, deposit, balance) and the header's lower edge, or None."""
    carry_tokens = {id(token) for line in lines if _carry(line.text) for token in line.tokens}
    labelled = [(kind, token) for token in tokens if id(token) not in carry_tokens and (kind := _label(token))]
    best = None
    for _kind, seed in labelled:
        band = [(kind, token) for kind, token in labelled if abs(token.yc - seed.yc) <= HEADER_BAND]
        columns = {}
        for kind in LABELS:
            members = [token for other, token in band if other == kind]
            if not members:
                break
            columns[kind] = sum(token.xc for token in members) / len(members)
        else:
            if abs(columns["withdrawal"] - columns["deposit"]) < 0.02:
                continue
            if columns["balance"] <= max(columns["withdrawal"], columns["deposit"]) + 0.02:
                continue
            spread = max(token.yc for _kind, token in band) - min(token.yc for _kind, token in band)
            bottom = max(token.y1 for _kind, token in band)
            if best is None or spread < best[0]:
                best = (spread, columns, bottom)
    return (best[1], best[2]) if best else None


def _zones(columns):
    low, high = sorted((columns["withdrawal"], columns["deposit"]))
    split = (high + columns["balance"]) / 2
    return {"amount": (low - 0.6 * (high - low), split), "balance": (split, 2 * columns["balance"] - split)}


def _money(tokens, zone, top):
    out = []
    for token in tokens:
        if token.yc <= top or not zone[0] <= token.xc < zone[1]:
            continue
        value = _loose_money(token.text)
        if value is None and SMALL_RE.fullmatch(token.text.strip()):
            value = Decimal(token.text.strip())  # "0.00" / "4.50": the deployed reader takes them for a time
        if value is not None:
            out.append((token, value))
    return out


def _fit(anchors, candidates, tolerance):
    """The tilt with the most anchor ↔ candidate pairs; returns (beta, {anchor index: [candidates]})."""
    best = None
    for beta in BETAS:
        pairs, residual = {}, 0.0
        for index, (anchor, _value) in enumerate(anchors):
            level = anchor.yc - beta * anchor.xc
            near = [(abs(token.yc - beta * token.xc - level), token, value) for token, value in candidates]
            near = [item for item in near if item[0] <= tolerance]
            if near:
                pairs[index] = [(token, value) for _distance, token, value in sorted(near, key=lambda item: item[0])]
                residual += min(item[0] for item in near)
        score = (len(pairs), -residual, -abs(beta))
        if best is None or score > best[0]:
            best = (score, beta, pairs)
    return best[1], best[2]


def _page_rows(page, tokens, lines, zones, top, reference_year):
    balances = sorted(_money(tokens, zones["balance"], top), key=lambda pair: pair[0].yc)
    if not balances:
        return None
    gaps = [b[0].yc - a[0].yc for a, b in zip(balances, balances[1:]) if b[0].yc > a[0].yc]
    pitch = statistics.median(gaps) if gaps else 0.02
    tolerance = min(0.45 * pitch, 0.008)
    amounts = _money(tokens, zones["amount"], top)
    beta, amount_pairs = _fit(balances, amounts, tolerance)
    left = zones["amount"][0]
    # The date column: tokens left of the amounts grouped by row level (a date and its year can be
    # separate OCR lines), each group read as one date.
    zone = sorted((token for token in tokens if token.yc > top and token.xc < left),
                  key=lambda token: token.yc - beta * token.x0)
    groups = []
    for token in zone:
        level = token.yc - beta * token.x0
        if groups and level - groups[-1]["last"] <= tolerance:
            groups[-1]["tokens"].append(token)
            groups[-1]["last"] = level
        else:
            groups.append({"tokens": [token], "first": level, "last": level})
    dated = []
    for group in groups:
        text = " ".join(token.text for token in sorted(group["tokens"], key=lambda token: token.x0))
        value = parse_date(text, reference_year)
        if value:
            dated.append(((group["first"] + group["last"]) / 2, value, text))
    # 另数一遍「同一高度上有钱数的日期行」：TTB 电子账单每页页脚印的打印日期时间、表头上方的「截至某日」不是流水
    # （2026-09-30 压测 B02），全数进去整份永远对不上、只能付费抽取。流水行漏读了金额也还有余额，照样数得到。
    moneyed = [pair[0].yc - beta * pair[0].xc for pair in _money(tokens, (zones["amount"][0], zones["balance"][1]), top)]
    money_dated = sum(1 for middle, _value, _text in dated if any(abs(level - middle) <= tolerance for level in moneyed))
    rows = []
    for index, (token, value) in enumerate(balances):
        level = token.yc - beta * token.xc
        near = sorted((abs(date_level - level), n) for n, (date_level, _date, _text) in enumerate(dated))
        date = dated[near[0][1]] if near and near[0][0] <= tolerance else None
        row_lines = [line for line in lines if abs(line.yc - beta * line.x0 - level) <= tolerance]
        rows.append({"page": page, "balance_token": token, "balance": value,
                     "amounts": amount_pairs.get(index, []),
                     "date": date[1] if date else "", "date_raw": date[2] if date else "",
                     "text": " ".join(line.text for line in sorted(row_lines, key=lambda line: line.x0))})
    date_rows = len(dated)
    return {"rows": rows, "date_rows": date_rows, "money_date_rows": money_dated, "beta": beta, "tolerance": tolerance}


def reconstruct(tokens_by_page, lines_by_page, pages, *, reference_year=None, trace=None):
    """``tokens_by_page`` / ``lines_by_page``: {page: [TokenBox]} / {page: [RowBox]} → Reconstruction or None."""
    reference_year = reference_year or datetime.date.today().year
    trace = trace if trace is not None else []

    def stop(reason, page=None, row=None):
        trace.append({"reason": reason, "page": page, "row": row})
        return None

    zones, audit, provenance, entries = None, [], [], []
    opening, previous, date_rows, money_date_rows, last_columns = None, None, 0, 0, None
    for page in range(1, pages + 1):
        tokens, lines = tokens_by_page.get(page) or [], lines_by_page.get(page) or []
        header = _header(tokens, lines)
        if header:
            zones, top = _zones(header[0]), header[1]
        elif zones is None:
            return stop("no_header", page)
        else:
            top = 0.0
        found = _page_rows(page, tokens, lines, zones, top, reference_year)
        if found is None:
            continue
        date_rows += found["date_rows"]
        money_date_rows += found["money_date_rows"]
        audit.append({"page": page, "field": "header_alignment", "value": {
            "beta": found["beta"], "tolerance": round(found["tolerance"], 6), "zones": {
                key: [round(edge, 4) for edge in value] for key, value in zones.items()}},
            "basis": "printed column headers and balance-anchored rows"})
        for row in found["rows"]:
            balance = row["balance"]
            if not row["amounts"] and not row["date"]:
                if opening is None and not entries:
                    opening = previous = balance  # printed brought-forward balance
                    continue
                if previous is not None and balance == previous:
                    continue  # a page's carried-forward line repeats the running balance
            columns = header[0] if header else last_columns
            amount, direction = _amount(row, previous, columns)
            entry = {"page": str(page), "transaction_date": row["date"], "transaction_date_raw": " ".join(
                part for part in (row["date_raw"], (TIME_RE.search(row["text"]) or [None, ""])[1]) if part),
                "description": row["text"][:240], "reference": "",
                "deposit": money(amount) if amount is not None and direction == "deposit" else "",
                "withdrawal": money(amount) if amount is not None and direction == "withdrawal" else "",
                "amount": money(amount) if amount is not None else "", "direction": direction,
                "balance": money(balance), "chain_repaired": False, "chain_amount_imputed": False,
                "review_required": not direction}
            entries.append(entry)
            provenance.append({"row": len(entries), "source": {"page": page, "text": row["text"][:240]},
                               "balance_bbox": [row["balance_token"].x0, row["balance_token"].y0,
                                                row["balance_token"].x1, row["balance_token"].y1]})
            previous = balance
        if header:
            last_columns = header[0]
    if not entries:
        return stop("no_rows")
    if not _dates_in_order(entries, audit):
        # Unreadable or out-of-order dates: newest-first statements and misreads go on.
        return stop("date_missing_or_out_of_order")
    document = {"document_type": "bank_statement", "bank_name": "", "bank_code": "",
                "opening_balance": money(opening) if opening is not None else "", "closing_balance": "",
                "entries": entries}
    # From here exactly as the deployed parse_bank: directions from adjacent printed balances, the
    # bounded balance-chain repair (audited, flagged on the row), then the deployed document gate.
    _infer_bank_directions(document, audit)
    document["closing_balance"] = entries[-1]["balance"]
    document, repair_audit = _repair_bank(document)
    audit.extend(repair_audit)
    issues = check_document(document, "bank", pages)
    # 两种数法有一种与行数相等即可（原来的数法照旧有效，只多认一种），见 ``_page_rows``。
    if len(entries) not in (date_rows, money_date_rows):
        issues.append("date_anchor_row_count_mismatch")
    return Reconstruction(document, audit, provenance, issues, pages)


def _dates_in_order(entries, audit):
    """Dates must never go backwards; one unreadable or out-of-order date between two rows of the same
    printed date takes that date (the deployed parser's adjacent-date rule), anything else fails."""
    dates = [entry["transaction_date"] for entry in entries]
    for index, value in enumerate(dates):
        before = dates[index - 1] if index else ""
        after = dates[index + 1] if index + 1 < len(dates) else ""
        if value and (not before or value >= before) and (not after or value <= after or not
                                                         (before and after and before <= after)):
            continue
        if before and before == after:
            dates[index] = before
            entries[index]["transaction_date"] = before
            audit.append({"row": index + 1, "field": "transaction_date", "from": value, "to": before,
                          "basis": "adjacent printed dates"})
    return all(dates) and all(left <= right for left, right in zip(dates, dates[1:]))


def _amount(row, previous, columns):
    """The row's printed amount and its direction: the balance change when it matches, else the column."""
    candidates = [value for _token, value in row["amounts"] if value]
    if not candidates:
        return None, ""
    if previous is not None:
        change = row["balance"] - previous
        for value in candidates:
            if change and abs(value) == abs(change):
                return abs(value), "deposit" if change > 0 else "withdrawal"
    token, value = next((token, value) for token, value in row["amounts"] if value)
    if columns and abs(columns["withdrawal"] - columns["deposit"]) >= 0.06:
        nearer = abs(token.xc - columns["withdrawal"]) < abs(token.xc - columns["deposit"])
        return abs(value), "withdrawal" if nearer else "deposit"
    return abs(value), _line_direction(row["text"])


def from_payloads(payloads, pages, *, reference_year=None, trace=None):
    """Document AI responses → Reconstruction or None (never raises on an unfamiliar layout)."""
    from pearnly_cowork.ocr.enterprise.local.geometry import rows_from_payloads

    _rows, lines, _tables, _meta = rows_from_payloads(payloads)
    tokens_by_page, lines_by_page = {}, {}
    for line in lines:
        lines_by_page.setdefault(line.page, []).append(line)
        tokens_by_page.setdefault(line.page, []).extend(line.tokens)
    try:
        return reconstruct(tokens_by_page, lines_by_page, pages, reference_year=reference_year, trace=trace)
    except (ValueError, ArithmeticError, LookupError) as error:
        if trace is not None:
            trace.append({"reason": type(error).__name__, "page": None, "row": None})
        return None
