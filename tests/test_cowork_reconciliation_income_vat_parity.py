"""Differential check of the migrated income (GL vs output-VAT) core.

CLI: --source <fixed-source-root>. Only selected pure definitions of the fixed
source are executed; no legacy application, database, file, network or model code
runs.
"""
import argparse
import ast
from collections import defaultdict
import hashlib
from dataclasses import dataclass, fields
from pathlib import Path
import random
import re
from typing import Any, Dict, List, Optional, Tuple
import unicodedata

from pearnly_cowork.reconciliation import income_vat as income

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {
    "services/recon/field_comparator.py":
        "109c0401587154d3f3860166b4f0c3812f568b03e2e452a880af866195eec037",
    "services/recon/gl_vat_types.py":
        "ee24b7dd0a7a24e67ac338f7c7deae73c71c36aca5b731ddc18b6e6af27a300d",
    "services/recon/gl_vat_reconcile.py":
        "05a5751d27c730819c53a94f13ccab5606677571f41839a056154d50647c13b8",
    "services/recon/gl_vat_parse_common.py":
        "ca59a5d1b313806e4358ad5a29b8abfa8b906b1dcc1d2f26128aee932eac5403",
}


# Intentional changes (2026-09-29, reproduced offline; each replacement must hit exactly once and
# everything else is still compared as printed): the VAT amount is compared with the voucher's net
# credit (credit − debit) instead of credits only / negated debits only, and a document number on
# several VAT lines is compared once — every line but the last takes its own VAT amount, the last
# takes the rest of the voucher.
INTENTIONAL = {
    "services/recon/gl_vat_reconcile.py": (
        ("""    for vat in vat_rows:
        raw_no = _vat_doc_no(vat)""",
         """    def _amount_of(v):
        try:
            return float(v.get("report_amount_pre_vat") or 0.0)
        except Exception:
            return 0.0

    _lines_of = defaultdict(list)
    for _index, _vat in enumerate(vat_rows):
        _lines_of[normalize_doc_no(_vat_doc_no(_vat))].append(_index)

    for index, vat in enumerate(vat_rows):
        raw_no = _vat_doc_no(vat)"""),
        ("""            is_credit_note = vat_amt < 0
            if is_credit_note:
                gl_amt = round(-sum(r.debit for r in gl_matches), 2)
            else:
                gl_amt = round(sum(r.credit for r in gl_matches), 2)""",
         """            gl_amt = round(sum(r.credit for r in gl_matches) - sum(r.debit for r in gl_matches), 2)
            siblings = _lines_of[norm_no]
            if len(siblings) > 1:
                if index != siblings[-1]:
                    gl_amt = round(vat_amt, 2)
                else:
                    gl_amt = round(gl_amt - sum(_amount_of(vat_rows[other]) for other in siblings[:-1]), 2)"""),
    ),
}


def extract(root, path, names, namespace, assignments=(), classes=()):
    """Execute only the named top-level definitions of a hash-pinned source file."""
    data = (root / path).read_bytes()
    if hashlib.sha256(data).hexdigest() != HASHES[path]:
        raise AssertionError(f"Source does not match {SOURCE_SHA}: {path}")
    text = data.decode("utf-8")
    for before, after in INTENTIONAL.get(path, ()):
        if text.count(before) != 1:
            raise AssertionError(("intentional change no longer applies", path, before))
        text = text.replace(before, after)
    tree = ast.parse(text)
    assignments, classes = set(assignments), set(classes)
    nodes = [node for node in tree.body
        if (isinstance(node, ast.FunctionDef) and node.name in names)
        or (isinstance(node, ast.ClassDef) and node.name in classes)
        or (isinstance(node, ast.Assign) and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name) and node.targets[0].id in assignments)]
    if {node.name for node in nodes if isinstance(node, ast.FunctionDef)} != set(names):
        raise AssertionError(f"Missing source definitions: {path}")
    if {node.name for node in nodes if isinstance(node, ast.ClassDef)} != classes:
        raise AssertionError(f"Missing source classes: {path}")
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(root / path), "exec"), namespace)
    return namespace


def load_legacy(root):
    normalizers = {"re": re, "unicodedata": unicodedata, "Optional": Optional}
    extract(root, "services/recon/field_comparator.py",
            {"normalize_str", "normalize_invoice_no"}, normalizers)
    parse_common = {"re": re, "List": List, "Dict": Dict, "Optional": Optional,
                    "normalize_invoice_no": normalizers["normalize_invoice_no"]}
    extract(root, "services/recon/gl_vat_parse_common.py", {"normalize_doc_no"}, parse_common)
    types = {"dataclass": dataclass, "List": List, "Dict": Dict, "Any": Any, "Optional": Optional}
    extract(root, "services/recon/gl_vat_types.py", set(), types,
            classes={"GlRow", "ReconRow", "GlVatSummary"})
    reconcile = {"defaultdict": defaultdict, "List": List, "Dict": Dict, "Any": Any,
                 "Tuple": Tuple, "normalize_doc_no": parse_common["normalize_doc_no"],
                 "GlRow": types["GlRow"], "ReconRow": types["ReconRow"],
                 "GlVatSummary": types["GlVatSummary"]}
    extract(root, "services/recon/gl_vat_reconcile.py", {"_vat_doc_no", "reconcile_gl_vat"},
            reconcile)
    return reconcile, parse_common, normalizers


def outcome(function, *arguments):
    """Compare success payloads and exception identity, never a partial outcome."""
    try:
        return "ok", function(*arguments)
    except Exception as error:
        return "error", type(error).__name__, str(error)


def describe(instance):
    return tuple((field.name, getattr(instance, field.name)) for field in fields(instance))


def gl_row(legacy, spec):
    return legacy["GlRow"](doc_no=spec["doc_no"], norm_doc_no=spec["norm_doc_no"],
                           date=spec["date"], account_code=spec["account_code"],
                           description=spec["description"], debit=spec["debit"], credit=spec["credit"])


def native_gl_row(spec):
    return income.GlRow(doc_no=spec["doc_no"], norm_doc_no=spec["norm_doc_no"], date=spec["date"],
                        account_code=spec["account_code"], description=spec["description"],
                        debit=spec["debit"], credit=spec["credit"])


def fixtures(rng):
    gl_doc_pool = ["GL-1", "gl-1", "GL/1", "", "IV001", "INV-1", "4110-01"]
    norm_pool = ["gl1", "iv001", "inv1", "411001", "", "gl"]
    date_pool = ["2026-06-01", "2026-06-02", "", None, "15/06/2569"]
    account_pool = ["4110-01", "4110", "", None]
    text_pool = ["sale", "", None, "ค่าบริการ"]
    amount_pool = [0.0, 5.0, 107.0, 1070.0, -3.0, 0.01, -0.01, None]
    vat_doc_pool = ["GL-1", "gl-1", "GL/1", "IV001", "", None, 4110]
    vat_amount_pool = [1070.0, "1070.00", -107.0, None, "", "abc", 0.0, 0.01, True]
    for _ in range(1200):
        yield {
            "gl_rows": [{"doc_no": rng.choice(gl_doc_pool), "norm_doc_no": rng.choice(norm_pool),
                         "date": rng.choice(date_pool), "account_code": rng.choice(account_pool),
                         "description": rng.choice(text_pool), "debit": rng.choice(amount_pool),
                         "credit": rng.choice(amount_pool)} for _ in range(rng.randint(0, 6))],
            "vat_rows": [{"report_ref_no": rng.choice(vat_doc_pool),
                          "report_reference_no": rng.choice(vat_doc_pool),
                          "report_invoice_no": rng.choice(vat_doc_pool),
                          "report_date": rng.choice(date_pool),
                          "report_buyer_name": rng.choice(text_pool),
                          "report_amount_pre_vat": rng.choice(vat_amount_pool)}
                         for _ in range(rng.randint(0, 6))],
            "amount_tolerance": rng.choice([0.01, 0.0, 1.0, 5.0, -1.0]),
        }
    # Fixed shapes: an exact pair, a credit note, a tolerance-suppressed difference,
    # a one-sided pair on each half of the summary and empty inputs.
    yield {"gl_rows": [{"doc_no": "GL-1", "norm_doc_no": "gl1", "date": "2026-06-01",
                        "account_code": "4110-01", "description": "sale", "debit": 0.0,
                        "credit": 1070.0}],
           "vat_rows": [{"report_ref_no": "GL-1", "report_date": "2026-06-01",
                         "report_buyer_name": "ACME", "report_amount_pre_vat": 1070.0}],
           "amount_tolerance": 0.01}
    yield {"gl_rows": [{"doc_no": "CN-1", "norm_doc_no": "cn1", "date": "2026-06-02",
                        "account_code": "4110-01", "description": "credit note", "debit": 107.0,
                        "credit": 0.0}],
           "vat_rows": [{"report_reference_no": "CN-1", "report_date": "2026-06-02",
                         "report_buyer_name": "ACME", "report_amount_pre_vat": -107.0}],
           "amount_tolerance": 0.01}
    yield {"gl_rows": [{"doc_no": "GL-2", "norm_doc_no": "gl2", "date": "2026-06-03",
                        "account_code": "4110-01", "description": "sale", "debit": 0.0,
                        "credit": 1069.99}],
           "vat_rows": [{"report_invoice_no": "GL-2", "report_date": "2026-06-03",
                         "report_buyer_name": "ACME", "report_amount_pre_vat": 1070.0}],
           "amount_tolerance": 0.01}
    yield {"gl_rows": [{"doc_no": "GL-3", "norm_doc_no": "gl3", "date": "2026-06-04",
                        "account_code": "4110-01", "description": "unmatched credit", "debit": 0.0,
                        "credit": 500.0},
                       {"doc_no": "GL-4", "norm_doc_no": "gl4", "date": "2026-06-05",
                        "account_code": "5000", "description": "unmatched debit", "debit": 200.0,
                        "credit": 0.0}],
           "vat_rows": [{"report_ref_no": "MISS-1", "report_date": "2026-06-06",
                         "report_buyer_name": "OTHER", "report_amount_pre_vat": 300.0},
                        {"report_ref_no": "MISS-2", "report_date": "2026-06-07",
                         "report_buyer_name": "OTHER", "report_amount_pre_vat": -50.0}],
           "amount_tolerance": 0.01}
    yield {"gl_rows": [], "vat_rows": [], "amount_tolerance": 0.01}


def run(root):
    legacy, parse_common, _ = load_legacy(root)
    doc_cases = 0
    for value in [None, "", " ", "GL-1", "gl 1", "GL/1", "ＧＬ-１", "INV-001", "IV001", "0110/2569",
                  "  4110-01  ", "0", 41, True, "ก.ค.-69"]:
        old = outcome(parse_common["normalize_doc_no"], value)
        new = outcome(income.normalize_doc_no, value)
        if old != new:
            raise AssertionError(("normalize_doc_no drifted", value, old, new))
        doc_cases += 1
    rng = random.Random(20261002)
    counts = {"batches": 0, "detail_rows": 0, "matched": 0, "credit_notes": 0,
              "tolerance_suppressed": 0, "gl_only_credit_items": 0, "gl_only_debit_items": 0,
              "vat_only_positive_items": 0, "vat_only_negative_items": 0, "empty": 0,
              "raised": 0}
    for case in fixtures(rng):
        old = outcome(legacy["reconcile_gl_vat"],
                      [gl_row(legacy, spec) for spec in case["gl_rows"]], case["vat_rows"],
                      case["amount_tolerance"])
        new = outcome(income.reconcile_gl_vat,
                      [native_gl_row(spec) for spec in case["gl_rows"]], case["vat_rows"],
                      case["amount_tolerance"])
        if old[0] != new[0]:
            raise AssertionError(("outcome kind drifted", case, old[0], new[0]))
        if old[0] == "error":
            if old != new:
                raise AssertionError(("exception drifted", case, old, new))
            counts["raised"] += 1
            counts["batches"] += 1
            continue
        old_detail, old_summary = old[1]
        new_detail, new_summary = new[1]
        if [describe(item) for item in old_detail] != [describe(item) for item in new_detail]:
            raise AssertionError(("detail drifted", case))
        if describe(old_summary) != describe(new_summary):
            raise AssertionError(("summary drifted", case))
        counts["batches"] += 1
        counts["detail_rows"] += len(new_detail)
        counts["matched"] += sum(1 for item in new_detail if item.gl_amount is not None)
        counts["credit_notes"] += sum(1 for item in new_detail if item.vat_amount < 0
                                      and item.gl_amount is not None)
        counts["tolerance_suppressed"] += sum(
            1 for item in new_detail if item.diff == 0.0 and item.gl_amount is not None
            and item.vat_amount != item.gl_amount)
        counts["gl_only_credit_items"] += len(new_summary.gl_only_credit_items)
        counts["gl_only_debit_items"] += len(new_summary.gl_only_debit_items)
        counts["vat_only_positive_items"] += len(new_summary.vat_only_positive_items)
        counts["vat_only_negative_items"] += len(new_summary.vat_only_negative_items)
        counts["empty"] += int(not case["gl_rows"] and not case["vat_rows"])
    for name, minimum in (("detail_rows", 500), ("matched", 100), ("credit_notes", 20),
                          ("tolerance_suppressed", 5), ("gl_only_credit_items", 50),
                          ("gl_only_debit_items", 50), ("vat_only_positive_items", 50),
                          ("vat_only_negative_items", 20), ("empty", 1), ("raised", 1)):
        if counts[name] < minimum:
            raise AssertionError((f"{name} barely exercised", counts[name]))
    print(f"PASS source={SOURCE_SHA} batches={counts['batches']} detail_rows={counts['detail_rows']} "
          f"matched={counts['matched']} credit_notes={counts['credit_notes']} "
          f"tolerance_suppressed={counts['tolerance_suppressed']} gl_only_credit={counts['gl_only_credit_items']} "
          f"gl_only_debit={counts['gl_only_debit_items']} vat_only_positive={counts['vat_only_positive_items']} "
          f"vat_only_negative={counts['vat_only_negative_items']} raised={counts['raised']} "
          f"doc_no_cases={doc_cases} differences=0")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    run(parser.parse_args().source)
