"""Explicit fixed-source differential runner; never import the legacy application.

Run with --source ROOT. Only selected AST definitions are compiled; source ERP
context is fixed disabled for Cowork, and date.today is an injected fixed clock.
"""
import argparse
import ast
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
from pathlib import Path
import random
import re
import typing

from pydantic import BaseModel, Field, field_validator, model_validator
from pearnly_cowork.ocr.schema import ThaiInvoice as CurrentInvoice, PipelinePageResult as CurrentPageResult, validate_invoice

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
HASHES = {'schemas_invoice.py': 'c3c9f848cb587c725b519f7af7defdfc7fab2599b8f9beced482d04531f8c793', 'schemas_layer1.py': '13efcc77722040211507ecf6db80c71c674167eef54ba0e17f4fde45cf50a936', 'schemas_results.py': '6b3cc8b6abdcdd57d6e86c9655377c16751aa70151341876ed99b8062be5df16', 'sanity.py': '9fb8dbdc92df57e884f6daab705e076db84d25b4f23b71723bf75389f0ea1335', 'sanity_multi.py': 'c377359e93da3df5c8ab4073ec8038c48d91427a09eec3250175412e91292a3d', 'date_sanity.py': '1fcdff6570e6963b51b26cee49e879106251d68b32ec8435ae35d9fd0fffb406', 'validators.py': '0c2e1d93c23a8a9a4ecfa7597a97b0599deecf655a5bfa5defc58a39ac0ba821', 'triggers.py': '55644459b72dff709284ad54902dd962fa941488ea543aaea450648326c15fdf', 'confidence.py': '28fb8c466118f32104258a666bbc1558c3ea5eb7e54dd6fe459abec468325f7f', 'money.py': '48aacdd033dbf9bc5d77f8bcc64835c96394ba4114e3f9684ea9d615249d105c', 'page_runner.py': 'c4ef017901b6e62c86d9135ddfeba8c2326b5650ea7c8b902cb1bb0ff0cf620f'}


def read_sources(root):
    result = {}
    for name, expected in HASHES.items():
        path = Path(root) / "services" / "ocr" / name
        raw = path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise AssertionError(f"source mismatch: {name}: {actual}")
        result[name] = ast.parse(raw.decode(), filename=str(path))
    return result


def definitions(tree, names=None):
    result = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)):
            continue
        name = (node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                else node.target.id if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
                else node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                else None)
        if names is None or name in names:
            result.append(deepcopy(node))
    return result


def execute(nodes, namespace):
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    tree = ast.fix_missing_locations(ast.Module(body=[future, *nodes], type_ignores=[]))
    exec(compile(tree, "<hash-verified-source-definitions>", "exec"), namespace)


def legacy_models(sources, anchor_year=2026):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return date(anchor_year, 9, 21)

    namespace = {"__name__": __name__, "re": re, "date": FixedDate,
                 "BaseModel": BaseModel, "Field": Field,
                 "field_validator": field_validator, "model_validator": model_validator,
                 **{name: getattr(typing, name) for name in ("Any", "Dict", "List", "Literal", "Optional", "Tuple", "Union", "get_args")}}
    execute(definitions(sources["schemas_layer1.py"]), namespace)
    nodes = definitions(sources["schemas_invoice.py"])
    for node in nodes:
        if isinstance(node, ast.ClassDef) and node.name == "ThaiInvoice":
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == "_normalize_year":
                    # Only the Cowork branch: ERP enabled() is false in this product.
                    child.body = [part for part in child.body if not isinstance(part, ast.ImportFrom)
                                  and not (isinstance(part, ast.If) and isinstance(part.test, ast.Call)
                                           and isinstance(part.test.func, ast.Name) and part.test.func.id == "enabled")]
    execute(nodes, namespace)
    namespace["ThaiInvoice"].model_rebuild(_types_namespace=namespace, force=True)
    # Used solely to build source invoice/auto wrappers with document=None.
    namespace["NonInvoiceDocument"] = BaseModel
    execute(definitions(sources["schemas_results.py"]), namespace)
    for name in ("Layer2PageResult", "Layer2Result", "Layer3PageResult", "PipelinePageResult", "PipelineResult"):
        namespace[name].model_rebuild(_types_namespace=namespace, force=True)
    return namespace


def run_schema(sources):
    count = 0
    rng = random.Random(20260921)
    for year in (2000, 2025, 2026, 2040):
        legacy = legacy_models(sources, year)
        assert set(legacy["ThaiInvoice"].model_fields) == set(CurrentInvoice.model_fields)
        raws = ["", "21/09/69", "21/09/27", "21/09/28", "21/09/2569", "21/09/2026", "2026-09-21", "21/09/1999", "1/2/00"]
        dates = [None, "", "2020-12-31", "2020-1-2", "not-a-date"]
        for raw in raws:
            for value in dates:
                payload = {"date_raw": raw, "date": value, "additional_invoices": [{"date_raw": raw, "date": value}]}
                old = legacy["ThaiInvoice"].model_validate(payload)
                new = validate_invoice(payload, anchor_year=year)
                assert old.model_dump(mode="json") == new.model_dump(mode="json"), (year, payload)
                count += 1
        for _ in range(200):
            payload = {"invoice_number": rng.choice([None, "", 123, "INV-001"]),
                       "subtotal": rng.choice([None, "", 0, 15.25, "1,000.00"]),
                       "vat": rng.choice([None, "", True, "7.00"]),
                       "items": [{"name": None, "qty": rng.choice([True, 0, 1.2, "001"]), "price": 3}],
                       "document_type": rng.choice([None, "unknown", "tax_invoice", "receipt", "credit_note"]),
                       "source_refs": {"vat": None, "subtotal": [], "total_amount": {"value": "1,000", "source_page": None}},
                       "additional_invoices": [{"date_raw": "21/09/2569", "date": "2020-12-31"}]}
            snapshot = deepcopy(payload)
            old = legacy["ThaiInvoice"].model_validate(payload)
            new = validate_invoice(payload, anchor_year=year)
            assert old.model_dump(mode="json") == new.model_dump(mode="json"), payload
            assert payload == snapshot
            old_page = legacy["PipelinePageResult"](page_number=2, invoice=old)
            new_page = CurrentPageResult(page_number=2, invoice=new)
            assert old_page.model_dump(mode="json") == new_page.model_dump(mode="json")
            count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    count = run_schema(read_sources(args.source))
    print(f"PASS {SOURCE_SHA}: {count} schema/date/wrapper differential cases")
