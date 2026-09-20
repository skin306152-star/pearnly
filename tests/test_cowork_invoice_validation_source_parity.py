"""Hash-bound legacy decisions versus Decimal invoice validation, no legacy imports."""
import argparse
import ast
from datetime import date, timedelta
from decimal import Decimal
import random
import re

from pearnly_cowork.ocr import validation as current
from pearnly_cowork.ocr.schema import validate_invoice
from test_cowork_invoice_schema_source_parity import (
    SOURCE_SHA, definitions, execute, legacy_models, read_sources,
)
from test_cowork_invoice_validation import page


def legacy_validation(sources):
    namespace = legacy_models(sources)
    namespace["timedelta"] = timedelta
    execute(definitions(sources["money.py"]), namespace)
    namespace.update(_money=namespace["normalize_money"], _digits=namespace["normalize_id"],
                     _valid_tax_id=namespace["valid_thai_tax_id"])
    execute(definitions(sources["confidence.py"]), namespace)
    execute(definitions(sources["date_sanity.py"]), namespace)
    execute(definitions(sources["validators.py"], {
        "_DESCRIPTION_COLUMN_KEYWORDS", "_VOUCHER_COLUMN_KEYWORDS", "_ACCOUNT_CODE_COLUMN_KEYWORDS",
        "_norm_col", "_is_forbidden_amount_source", "validate_invoice",
    }), namespace)
    execute(definitions(sources["sanity.py"]), namespace)
    multi = definitions(sources["sanity_multi.py"])
    for node in multi:
        if isinstance(node, ast.FunctionDef):
            node.body = [part for part in node.body if not isinstance(part, ast.ImportFrom)]
    execute(multi, namespace)
    namespace.update(CONFIDENCE_THRESHOLD=.85, AMOUNT_TOLERANCE_THB=.5,
                     CONFIDENCE_AUTO_THRESHOLD=.98, CONFIDENCE_REVIEW_THRESHOLD=.9)
    execute(definitions(sources["triggers.py"], {
        "CRITICAL_FIELDS", "_aggregate_page_confidence", "_bucket_confidence", "_check_amount_math",
        "_count_invoice_no_candidates", "_evaluate_triggers", "_evaluate_soft_flags",
    }), namespace)
    execute(definitions(sources["page_runner.py"], {"_FIELD_CONF_ATTRS", "_field_confidences"}), namespace)
    return namespace


def reason_shapes(reasons):
    # Decimal preserves printed scale where float rewrote 100.00 -> 100.0.
    # Compare the full ordered prose with number tokens masked; decisions and
    # field names still must match. Separate assertions cover exact money.
    return [re.sub(r"-?\d+(?:\.\d+)?", "#", reason) for reason in reasons]


def run_validation(sources):
    old = legacy_validation(sources)
    rng = random.Random(20260921)
    totals = {"sanity": 0, "discount": 0, "math": 0, "sources_dates": 0, "confidence": 0}
    today = date(2026, 9, 21)
    for _ in range(2500):
        sub = rng.randrange(1, 1000) * 100
        discount = rng.choice((0, 100, 200))
        vat = (sub - discount) * 7 // 100
        total = sub - discount + vat + rng.choice((0, 0, 0, 10, -10))
        payload = {
            "subtotal": str(sub), "vat": rng.choice((str(vat), "")), "total_amount": str(total),
            "discount": str(discount), "document_type": rng.choice(("tax_invoice", "receipt", "credit_note")),
            "seller_tax": rng.choice(("", "0105556086859", "1111111111111")),
            "buyer_tax": rng.choice(("", "0105556086859")),
            "items": [{"subtotal": str(sub // 2)}, {"subtotal": str(sub // 2 + rng.choice((0, 100, -100)))}],
        }
        if rng.randrange(3) == 0:
            payload["additional_invoices"] = [{"subtotal": "100", "vat": "7", "total_amount": rng.choice(("107", "-5")),
                                                "items": [{"subtotal": "20"}, {"subtotal": "30"}]}]
        value = validate_invoice(payload, anchor_year=2026)
        left, right = old["evaluate_sanity"](value), current.evaluate_sanity(value)
        assert reason_shapes(left) == reason_shapes(right), (payload, left, right)
        assert old["_check_amount_math"](value) == current.check_amount_math(value), payload
        assert old["credit_note_review_reason"](value) == current.credit_note_review_reason(value)
        totals["sanity"] += 1
        totals["math"] += 1

    for sub in (100, 1000, 10000):
        for discount in (0, 1, 10, 50):
            vat = (Decimal(sub) - Decimal(discount)) * Decimal("0.07")
            total = Decimal(sub) - Decimal(discount) + vat
            value = validate_invoice({"subtotal": str(sub), "vat": str(vat), "total_amount": str(total)}, anchor_year=2026)
            previous, modified = value.model_copy(deep=True), value.model_copy(deep=True)
            a = old["infer_missing_discount"](previous)
            b = current.infer_missing_discount(modified)
            assert reason_shapes([a] if a else []) == reason_shapes([b] if b else []), (sub, discount, a, b)
            assert previous.discount == modified.discount
            totals["discount"] += 1

    for day in (-3000, -1826, -1825, -1, 0, 1, 2, 366):
        for column in ("", "Total", "Description:", "เลขที่", "รหัสบัญชี", "Details", "unknown"):
            value = validate_invoice({"date": (today + timedelta(days=day)).isoformat(),
                                      "source_refs": {"total_amount": {"value": "6091", "source_column": column}}}, anchor_year=2026)
            assert old["validate_invoice"](value, page()) == current.validate_invoice_sources(value, today=today)
            totals["sources_dates"] += 1

    class Pattern:
        def check_anomaly(self, seller, number):
            return "pattern changed"

    for _ in range(800):
        number = rng.choice(("", "IV69/00179", "ABCD", "179"))
        value = validate_invoice({"invoice_number": number, "total_amount": rng.choice(("", "1780.00", "1780")),
                                  "seller_tax": rng.choice(("", "1234", "0105556086859")),
                                  "document_type": rng.choice(("tax_invoice", "receipt")),
                                  "date_raw": rng.choice(("", "21/09/2569")),
                                  "is_not_invoice": rng.choice((False, False, False, True))}, anchor_year=2026)
        evidence = page(rng.choice(("IV69/00179 IV69/00189 1780.00 1234 21/09/2569", "unrelated")),
                        [("IV69/00179", rng.choice((.7, .85, .99))), ("1780.00", .8)],
                        confidence=rng.choice((0, .7, .9, .98, 1)))
        assert old["_evaluate_triggers"](evidence, value, Pattern()) == current.evaluate_triggers(evidence, value, pattern_anomaly="pattern changed")
        soft = old["_evaluate_soft_flags"](evidence, value)
        assert soft == current.evaluate_soft_flags(evidence, value)
        assert old["_field_confidences"](evidence, value) == current.field_confidences(evidence, value)
        for field in (number, value.total_amount, value.seller_tax):
            assert old["check_field_in_l1_text"](evidence, field) == current.check_field_in_l1_text(evidence, field)
            assert old["find_field_min_word_conf"](evidence, field) == current.find_field_min_word_conf(evidence, field)
        for review in (False, True):
            score = old["_aggregate_page_confidence"](evidence, value, None, ["t"], review, "invoice", soft)
            assert score == current.aggregate_page_confidence(evidence, ["t"], review, soft)
            assert old["_bucket_confidence"](score, review) == current.bucket_confidence(score, review)
        totals["confidence"] += 1

    # Explicit changes: float silently loses a one-baht difference above 2**53;
    # NaN/Infinity can pass old comparison gates. Decimal must report each case.
    for sub, total in (("9007199254740993", "9007199254740992"), ("NaN", "107"), ("Infinity", "Infinity")):
        value = validate_invoice({"subtotal": sub, "vat": "0", "total_amount": total}, anchor_year=2026)
        assert old["_check_amount_math"](value) is None
        assert current.check_amount_math(value) is not None
    totals["intentional_decimal_changes"] = 3
    return totals


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    print(f"PASS {SOURCE_SHA}: {run_validation(read_sources(args.source))}")
