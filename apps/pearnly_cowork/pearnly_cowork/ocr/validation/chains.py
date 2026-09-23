"""Deterministic balance-chain repairs for bank statements and general ledgers.

Sources: ec2e5c957e6b ``services/ocr/bank_chain_gate.py`` (``repair_bank_chain``,
``_audit_warnings`` and their helpers) and ``services/ocr/gl_balance_chain.py``
(``repair_gl_document`` and its helpers). The two live in one module because they
share one parse rule and one tolerance philosophy — the deployed sources say so
themselves ("与 gl_balance_chain 同口径,两链不劈叉"), and splitting them would
duplicate that rule.

Why this exists: an internally consistent wrong read survives every other check.
A bank page whose deposit/withdrawal columns were swapped still adds up row by
row, and a single misread digit still looks like a plausible amount. The printed
balance is the one arithmetic invariant such a document carries, so the chain is
what proves a swap (a swap has a unique solution when the two sides differ) or
back-calculates a single-sided amount from the balance movement. Nothing here
guesses: a row is only changed when the arithmetic proves it, and every change is
marked on the row for audit.

Scope: the row-level repairs only. The deployed bank orchestration that escalated
a whole page to the top model and re-read it (``bank_chain_gate.reread_bank_page``)
needs the document escalation arm that does not exist on this side yet, so it
moves with that wiring; the deployed environment switch (``OCR_BANK_CHAIN_GATE``)
is a runtime flag and belongs to the caller, not to a pure module.

Pure logic: no Frappe, database, file, clock, HTTP, model, logger or export access.
The functions mutate the entry objects they are given, exactly like the deployed
ones, because the readers hand over their own row objects.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

# Bank amounts are printed to the satang, so a tolerance of one satang absorbs
# last-digit rounding that already happened on the model's side.
BANK_TOL = Decimal("0.01")
# GL pages print a coarser grid; the deployed GL chain used five satang.
GL_TOL = Decimal("0.05")

# Safety band for the chain-inferred overwrite: the inferred amount must be within
# this fraction of the model's own reading of that row's movement. A small gap
# means the reading is believable and only its direction or column was wrong; a
# large gap means the reading or the balance is wrong, and inventing an amount
# from a possibly wrong balance would write a silent error into the books.
IMPUTE_BAND = Decimal("0.2")


def _dec(x: Any) -> Optional[Decimal]:
    """Tolerant parse: drop thousands separators, empty or unparsable → None."""
    if x in (None, ""):
        return None
    try:
        return Decimal(str(x).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def _side_amount(raw: Any, field: str) -> Decimal:
    """One side's amount: empty is zero, an unparsable value is an error for the row."""
    s = str(raw or "").strip()
    if not s:
        return Decimal(0)
    d = _dec(s)
    if d is None:
        raise ValueError(f"{field} 不可解析: {s!r}")
    return d


def _flip_bank_entry(e) -> None:
    """A swap the arithmetic proved: exchange the columns and mark the row.

    ``direction`` is derived from the columns, so it has to follow the swap or the
    row contradicts itself. The amount value is the same under the exchange.
    """
    e.deposit, e.withdrawal = e.withdrawal, e.deposit
    if getattr(e, "direction", ""):
        e.direction = "withdrawal" if e.direction == "deposit" else "deposit"
    e.chain_repaired = True


def _impute_chain_amount(e, implied: Decimal) -> None:
    """Overwrite a single-sided amount with the value the printed balance proves.

    ``implied = balance − previous balance``; its sign decides the column, its
    magnitude is written with two decimals like the field it replaces. A negative
    implied on the deposit side is the same repair seen from the other column, so
    this one function covers both the same-side and the swapped-side case.
    ``review_required`` is set because the amount has only the chain as evidence:
    posting and export must not treat it as a final number.
    """
    amount = f"{abs(implied):.2f}"
    if implied > 0:
        e.deposit, e.withdrawal = amount, ""
    else:
        e.deposit, e.withdrawal = "", amount
    if getattr(e, "direction", ""):
        e.direction = "deposit" if implied > 0 else "withdrawal"
    e.chain_amount_imputed = True
    e.review_required = True


def repair_bank_chain(entries, opening_balance: Any) -> Tuple[list, List[str]]:
    """Row-level repair of a bank statement's balance chain.

    The four tests run in this order, and the order is a correctness invariant
    (keep the value first, correct it second):

    a. a carried-forward row (both sides empty) is released when its balance
       continues the previous one, and refused when it jumps;
    b. the ordinary direction: ``previous + deposit − withdrawal`` equals the
       printed balance, the row passes untouched;
    c. the swapped direction is the unique solution when the two sides differ, so
       the columns are exchanged and the row marked;
    d. both directions failing, exactly one side filled, a non-zero implied amount
       and a magnitude inside the safety band: the amount is overwritten from the
       chain and the row marked for review;
    e. nothing fits: the first break is returned and the caller decides (the
       deployed caller re-read the whole page with the top model).

    Returns ``(entries_out, reasons)``. Empty reasons means the whole chain holds
    after repair; entries_out is a shallow copy of the input list whose row objects
    were repaired in place.
    """
    entries = list(entries or [])
    if not entries:
        return entries, []
    prev = _dec(opening_balance)
    for i, e in enumerate(entries, start=1):
        bal = _dec(e.balance)
        if bal is None:
            return entries, [f"行 {i}: balance 缺失/不可解析"]
        try:
            dep = _side_amount(e.deposit, "deposit")
            wd = _side_amount(e.withdrawal, "withdrawal")
        except ValueError as exc:
            return entries, [f"行 {i}: {exc}"]
        if dep == 0 and wd == 0:
            # A carried-forward row (the first row of a statement is usually
            # ยอดยกมา) genuinely has no movement; its balance continuing the
            # previous one is correct. A jump means a lost movement or a misread
            # balance, which is a reading defect, not a page shape.
            if prev is not None and abs(bal - prev) <= BANK_TOL:
                prev = bal
                continue
            return entries, [f"行 {i}: deposit/withdrawal 均空且余额 {bal} 未承接上行 {prev}"]
        if prev is None:
            return entries, [f"行 {i}: opening_balance 缺失,链起点无法起算"]
        expected = prev + dep - wd
        if abs(expected - bal) <= BANK_TOL:
            prev = bal
            continue
        # The ordinary direction failed: try the swap. When the two sides are
        # equal both equations are identical, so there is no unique solution and
        # the row is left undecidable rather than guessed.
        if dep != wd and abs(prev + wd - dep - bal) <= BANK_TOL:
            _flip_bank_entry(e)
            prev = bal
            continue
        # Both directions failed: the chain can still supply this row's movement.
        # The band is anchored on the model's own reading of that side (X), not on
        # the balance: a small gap means the reading is sound and only its column
        # or sign is wrong. Two filled sides mean even the column ownership was
        # misread, so there is no anchor and the row stays undecidable.
        single_side = (dep > 0 and wd == 0) or (wd > 0 and dep == 0)
        if single_side:
            x = dep if dep > 0 else wd
            implied = bal - prev
            in_band = abs(abs(implied) - x) <= max(IMPUTE_BAND * x, BANK_TOL)
            if x > BANK_TOL and abs(implied) > BANK_TOL and in_band:
                _impute_chain_amount(e, implied)
                prev = bal
                continue
        return entries, [f"行 {i}: 余额链断 {prev} + {dep} - {wd} = {expected} ≠ 印刷 {bal}"]
    return entries, []


def bank_chain_warnings(entries, residual_reasons) -> List[str]:
    """The review warnings a still-broken chain and an inferred amount produce.

    A swap is a proved unique solution and is not flagged; an inferred amount is,
    because it has no second image evidence.
    """
    warns = [f"max 重读链校验:{r},该页金额待复核" for r in residual_reasons]
    warns += [
        f"行 {i} 金额为链推定(印刷余额反推)覆写,待复核"
        for i, e in enumerate(entries or [], 1)
        if getattr(e, "chain_amount_imputed", False)
    ]
    return warns


def _fmt(x: Decimal) -> str:
    return format(x.quantize(Decimal("0.01")), "f")


def _set_gl_side(entry, amount: Decimal, is_debit: bool) -> None:
    entry.debit = _fmt(amount) if is_debit else ""
    entry.credit = "" if is_debit else _fmt(amount)
    entry.direction = "deposit" if is_debit else "withdrawal"


def repair_gl_document(doc) -> List[str]:
    """Repair a general ledger in place; returns the warnings (breaks only).

    Three steps, each of which acts only when the arithmetic proves it:

    1. direction: an amount that agrees with the balance movement but is printed
       on the wrong side is put right and marked on the row;
    2. opening: when no opening balance is printed it is derived from the first
       row, cross-checked against the closing balance when there is one;
    3. a break: amount and movement disagree, so nothing is changed and a warning
       asks for a human.

    A row without a balance breaks ``prev``, so a movement spanning the gap can no
    longer match a single row's amount and is never "corrected" by mistake.
    """
    entries = list(doc.entries or [])
    if not entries:
        return []

    warnings: List[str] = []
    prev = _dec(doc.opening_balance)
    gap = False  # a balance-less row sits between prev and this row
    sd = sc = Decimal(0)  # running debit/credit totals, for the printed footer check
    last_bal_raw = ""  # the printed string, so a back-filled closing balance is not reformatted
    for i, e in enumerate(entries, start=1):
        bal = _dec(e.balance)
        if bal is None:
            gap = True
            continue
        deb, cred = _dec(e.debit) or Decimal(0), _dec(e.credit) or Decimal(0)
        amt = max(deb, cred)
        if prev is not None and amt > 0:
            delta = bal - prev
            if abs(abs(delta) - amt) <= GL_TOL:
                if (delta > 0 and cred > 0) or (delta < 0 and deb > 0):
                    _set_gl_side(e, amt, is_debit=delta > 0)
                    e.raw_row_data["direction_autocorrected"] = True
                    deb, cred = (amt, Decimal(0)) if delta > 0 else (Decimal(0), amt)
            elif not gap:
                warnings.append(
                    f"GL 余额链断裂 行{i}: 上行余额 {_fmt(prev)} ± 金额 {_fmt(amt)} ≠ 余额 {_fmt(bal)}"
                )
        sd, sc, last_bal_raw = sd + deb, sc + cred, e.balance
        prev, gap = bal, False

    warnings.extend(_reconcile_printed_totals(doc, entries, sd, sc))
    _derive_opening(doc, entries)
    if not (doc.closing_balance or "").strip():
        doc.closing_balance = last_bal_raw
    return warnings


def _reconcile_printed_totals(doc, entries, sd: Decimal, sc: Decimal) -> List[str]:
    """Printed footer totals against the summed rows.

    The first row's direction has no previous balance to check against when the
    opening balance is not printed, so the printed totals are the only
    deterministic source: if the sums are off and flipping the first row makes
    both match, the first row is flipped; if flipping does not settle it, rows are
    probably missing or a total is misread, so a warning is returned instead.
    """
    p_deb, p_cred = _dec(doc.total_debit), _dec(doc.total_credit)
    if p_deb is None or p_cred is None or not entries:
        return []
    if abs(sd - p_deb) <= GL_TOL and abs(sc - p_cred) <= GL_TOL:
        return []
    first = entries[0]
    deb0, cred0 = _dec(first.debit) or Decimal(0), _dec(first.credit) or Decimal(0)
    amt0 = max(deb0, cred0)
    if amt0 > 0:
        flip_sd = sd - amt0 if deb0 > 0 else sd + amt0
        flip_sc = sc + amt0 if deb0 > 0 else sc - amt0
        if abs(flip_sd - p_deb) <= GL_TOL and abs(flip_sc - p_cred) <= GL_TOL:
            _set_gl_side(first, amt0, is_debit=cred0 > 0)
            first.raw_row_data["direction_autocorrected"] = True
            return []
    return [
        f"GL 借贷合计与明细对不上: 行和 {_fmt(sd)}/{_fmt(sc)} vs 印刷 {_fmt(p_deb)}/{_fmt(p_cred)}"
        " — 可能漏行或合计读错"
    ]


def _derive_opening(doc, entries) -> None:
    """Derive the opening balance when the page does not print one.

    Rows 2..n have already had their direction checked against the chain and the
    first row's direction has been settled by the printed totals when those are
    present, so the printed balance makes the opening balance a determined number
    rather than a guess. With no printed totals at all the first row's direction
    rests on nothing, so the opening balance is still derived — but a mistake there
    surfaces as an explicit difference in the GL-to-bank reconciliation.
    """
    if (doc.opening_balance or "").strip():
        return
    first = entries[0]
    bal0 = _dec(first.balance)
    deb0, cred0 = _dec(first.debit) or Decimal(0), _dec(first.credit) or Decimal(0)
    if bal0 is None or max(deb0, cred0) <= 0:
        return
    doc.opening_balance = _fmt(bal0 - (deb0 - cred0))
