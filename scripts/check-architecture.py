#!/usr/bin/env python3
"""Offline architecture guard: package boundaries and explicit compatibility APIs.

No Site, credentials, network or external writes. Existing root modules are a
reviewed baseline, not a pattern for new business features.
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs/development/architecture-policy.json"


def check(root=ROOT):
    policy = json.loads((root / POLICY.relative_to(ROOT)).read_text())
    mapping = json.loads((root / "docs/development/dms-module-map.json").read_text())
    errors = []
    for app in (root / "apps").iterdir():
        if not app.is_dir() or not (app / "pyproject.toml").exists():
            continue
        package = app / app.name
        allowed = set(policy["root_modules"].get(app.name, ["__init__.py", "hooks.py"]))
        for path in package.glob("*.py"):
            if path.name not in allowed:
                errors.append(f"{path.relative_to(root)}: new business code must belong to a domain package")
        for required in ("__init__.py", "hooks.py", "modules.txt", "patches.txt"):
            if not (package / required).is_file():
                errors.append(f"{package.relative_to(root)}: missing Frappe file {required}")
    legacy = set(mapping["modules"])
    domains = policy.get("dms_domain_dependencies", {})
    compat = mapping["compatibility"]
    for path in (root / "apps").rglob("*.py"):
        if any(part in {"node_modules", "__pycache__"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        relative = path.relative_to(root).as_posix()
        module = ".".join(path.with_suffix("").parts[path.parts.index("apps") + 2:])
        if module in compat:
            expected = compat[module]
            imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
            non_imports = [n for n in tree.body if not isinstance(n, ast.ImportFrom)
                          and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                                   and isinstance(n.value.value, str))]
            exports = {a.name for n in imports for a in n.names}
            if (non_imports or any(n.module != expected["module"] or n.level for n in imports)
                    or exports != set(expected["exports"])
                    or any(a.asname not in (None, a.name) for n in imports for a in n.names)):
                errors.append(f"{relative}: compatibility entry must only re-export its registered callables")
            continue
        for n in ast.walk(tree):
            imports = []
            if isinstance(n, ast.Import):
                imports = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                imports = [n.module or ""]
                if n.module == "pearnly_dms":
                    imports += [f"pearnly_dms.{a.name}" for a in n.names]
                if "/contracts/" in relative and n.level:
                    errors.append(f"{relative}:{n.lineno}: contracts use explicit imports to expose boundaries")
            for dependency in imports:
                if module.startswith("pearnly_dms.") and dependency.startswith("pearnly_dms."):
                    source_domain = module.split(".")[1]
                    target_domain = dependency.split(".")[1]
                    if (source_domain in domains and target_domain in domains
                            and source_domain != target_domain
                            and target_domain not in domains[source_domain]):
                        errors.append(f"{relative}:{n.lineno}: new domain dependency requires architecture review: {source_domain} -> {target_domain}")
                if dependency in legacy:
                    errors.append(f"{relative}:{n.lineno}: internal code must use domain API, not {dependency}")
                if "/pearnly_dms/contracts/" in relative:
                    prefix = dependency.split(".")[0]
                    if prefix not in {"__future__", "datetime", "dataclasses", "typing", "decimal", "re", "enum", "collections", "math", "pearnly_dms"} or (prefix == "pearnly_dms" and not dependency.startswith("pearnly_dms.contracts.")):
                        errors.append(f"{relative}:{n.lineno}: contracts must remain pure and independent: {dependency}")
    for old in compat:
        path = root / "apps/pearnly_dms" / (old.replace(".", "/") + ".py")
        if not path.is_file():
            errors.append(f"missing registered compatibility entry: {old}")
    for old, new in mapping["modules"].items():
        path = root / "apps/pearnly_dms" / (new.replace(".", "/") + ".py")
        if not path.is_file():
            errors.append(f"module mapping has no destination: {old} -> {new}")
    # General UI primitives belong to the pinned official component library.
    # Business layouts and the Dropdown's custom account trigger are intentional.
    for path in (root / "frontend/src").rglob("*.vue"):
        source = re.sub(r"<!--.*?-->", "", path.read_text(), flags=re.S)
        forbidden = re.search(r"<(?:input|select|textarea|table)\b|\b(?:window\.)?(?:alert|confirm|prompt)\(", source)
        if forbidden:
            errors.append(f"{path.relative_to(root)}: use official Frappe UI controls/dialogs/lists")
        if re.search(r"\bpn-(?:error|table|skeleton|card)\b", source):
            errors.append(f"{path.relative_to(root)}: replace custom generic UI with official components")
        if re.search(r"<button\b", source) and path.relative_to(root).as_posix() != "frontend/src/components/AccountMenu.vue":
            errors.append(f"{path.relative_to(root)}: use Frappe UI Button")
    # ADR-005: normal product routes must not grow a second Vue shell.
    router = root / "frontend/src/router.ts"
    if router.exists():
        routes = re.findall(r'path:\s*["\']([^"\']+)', router.read_text())
        if any(route != "/dms/editor" for route in routes):
            errors.append("frontend/src/router.ts: product Web routes must use native Desk; only LINE editor is standalone")
    for path in (root / "apps").glob("*/*/public/desk/*.js"):
        source = path.read_text()
        if re.search(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\(", source):
            errors.append(f"{path.relative_to(root)}: use native frappe Dialog/confirm/msgprint")
        if re.search(r"<(?:input|select|textarea|table)\b", source):
            errors.append(f"{path.relative_to(root)}: use native Desk controls and Grid/DataTable")
    return errors


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure)
    print(f"Architecture: {'FAIL' if failures else 'PASS'} ({len(failures)} violations)")
    raise SystemExit(bool(failures))
