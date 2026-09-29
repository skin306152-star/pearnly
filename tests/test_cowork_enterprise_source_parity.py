"""The migrated Enterprise financial-document pipeline against the fixed legacy source ec2e5c957e6b.

CLI: --source <fixed-source-root>. Verbatim files are compared byte for byte; files whose imports
changed are compared by the AST of every function they keep; the frozen extraction request is
built by both sides and compared field by field. No legacy application, database, file, network or
model code runs (only pure definitions are executed, with the contract module injected).
"""
import argparse
import ast
import hashlib
from pathlib import Path

from pearnly_cowork.ocr.enterprise import adapter, contract, pages, pipeline, request

SOURCE_SHA = "ec2e5c957e6b4be7dee93ffdd2dae9fa83f69188"
NEW = Path(contract.__file__).parent
VERBATIM = {
    "services/ocr/enterprise_contract.py": "contract.py",
    "services/ocr/enterprise_local/__init__.py": "local/__init__.py",
    "services/ocr/enterprise_local/common.py": "local/common.py",
    "services/ocr/enterprise_local/geometry.py": "local/geometry.py",
    "services/ocr/enterprise_local/bank_parser.py": "local/bank_parser.py",
    "services/ocr/enterprise_local/gl_parser.py": "local/gl_parser.py",
    "services/ocr/enterprise_local/bank_chain.py": "local/bank_chain.py",
    "services/ocr/enterprise_local/result.py": "local/result.py",
}
# (legacy file, function, new module file): functions kept statement for statement.
SAME_FUNCTIONS = (
    ("services/ocr/enterprise_request.py", "build_request", "request.py"),
    ("services/ocr/enterprise_request.py", "contract_hash", "request.py"),
    ("services/ai_gateway/providers/enterprise.py", "request_body", "pages.py"),
    ("services/ocr/enterprise_pipeline.py", "_local_pages", "pipeline.py"),
)
# page_texts keeps its logic; only the deployed Layer1Error became this engine's ContentError.
RENAMED_ERRORS = (("services/ai_gateway/providers/enterprise.py", "page_texts", "pages.py"),
                  ("services/ocr/enterprise_adapter.py", "make_page", "adapter.py"))


def _function(path, name):
    tree = ast.parse(Path(path).read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant):
                node.body = node.body[1:]  # docstrings may differ
            return node
    raise AssertionError(("missing function", path, name))


def run(root):
    root = Path(root)
    counts = {"verbatim": 0, "functions": 0, "renamed_error_functions": 0, "requests": 0}
    for legacy, new in VERBATIM.items():
        old_bytes, new_bytes = (root / legacy).read_bytes(), (NEW / new).read_bytes()
        if hashlib.sha256(old_bytes).digest() != hashlib.sha256(new_bytes).digest():
            raise AssertionError(("verbatim file drifted", legacy, new))
        counts["verbatim"] += 1
    for legacy, name, new in SAME_FUNCTIONS:
        if ast.dump(_function(root / legacy, name)) != ast.dump(_function(NEW / new, name)):
            raise AssertionError(("function drifted", legacy, name))
        counts["functions"] += 1
    for legacy, name, new in RENAMED_ERRORS:
        old = ast.unparse(_function(root / legacy, name))
        new_text = ast.unparse(_function(NEW / new, name))
        old = old.replace('Layer1Error(\'Enterprise OCR returned an invalid page text anchor\')',
                          "ContentError('ocr.enterprise_invalid_anchor')")
        old = old.replace("Layer1Error('Enterprise OCR returned no pages')", "ContentError('ocr.enterprise_no_pages')")
        # make_page: the placeholder invoice is built through the platform's validate_invoice.
        old = old.replace("invoice=ThaiInvoice(is_not_invoice=True)", "invoice=_not_invoice()")
        if old != new_text:
            raise AssertionError(("function drifted beyond the error rename", legacy, name))
        counts["renamed_error_functions"] += 1
    # The pinned processor version and the frozen request, built by both sides.
    provider = (root / "services/ai_gateway/providers/enterprise.py").read_text()
    assert f'PROCESSOR_VERSION = "{pages.PROCESSOR_VERSION}"' in provider
    reader = (root / "services/ocr/enterprise_reader.py").read_text()
    assert f"USD_PER_PAGE = {pages.USD_PER_PAGE}" in reader
    namespace = {"contract": contract}
    tree = ast.parse((root / "services/ocr/enterprise_request.py").read_text())
    body = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.Import))
            or (isinstance(node, ast.ImportFrom) and node.module in ("__future__", "copy"))]
    exec(compile(ast.Module(body=body, type_ignores=[]), "legacy_request", "exec"), namespace)
    for category in ("bank", "gl", "vat", "invoice"):
        for transcript in ("", "line one\nline two ๑๒๓", "---"):
            for image_only in (False, True):
                old = namespace["build_request"](category, transcript, image_only=image_only)
                new = request.build_request(category, transcript, image_only=image_only)
                if old != new:
                    raise AssertionError(("request drifted", category, transcript, image_only))
                counts["requests"] += 1
    assert pipeline.ENGINE == "enterprise-2026-09-03-v1"
    assert adapter.DOCUMENT_TYPES == {"bank": "bank_statement", "gl": "general_ledger", "vat": "vat_report"}
    return {"source": SOURCE_SHA, "counts": counts, "differences": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    print(run(parser.parse_args().source))
