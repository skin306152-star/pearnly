#!/usr/bin/env python3
"""Offline architecture guard: package boundaries and explicit compatibility APIs.

No Site, credentials, network or external writes. Existing root modules are a
reviewed baseline, not a pattern for new business features.

Domain dependency directions are registered per App in the policy; DMS and Cowork
both have one. The framework module directory of an App (its DocType controllers)
is the orchestration layer and is not registered.

Product content is declared by each product (ADR-009): its sellable feature
catalog, its own menu and its own glossary live in its own directory, and the
shared layer or the control plane only aggregate them.
"""
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs/development/architecture-policy.json"

# 产品内容的声明归产品自己（ADR-009）。只在"这一份仓库确实带全部产品"时检查，
# 便于用最小目录树单测这条规则本身。
PRODUCT_APPS = {"pearnly_cowork": "cowork", "pearnly_erp_portal": "erp",
                "pearnly_dms": "dms"}


def check_product_declarations(root):
    """每个产品自己声明功能目录与菜单；共用层不许再存这些内容。"""
    errors = []
    if not (root / "apps/pearnly_control").is_dir():
        return errors
    for app, product in sorted(PRODUCT_APPS.items()):
        package = root / "apps" / app / app
        catalog = package / "features.json"
        if not catalog.is_file():
            errors.append(f"{catalog.relative_to(root)}: {product} must declare its sellable "
                          "feature catalog next to the product")
        else:
            try:
                declared = json.loads(catalog.read_text())
            except ValueError as error:
                errors.append(f"{catalog.relative_to(root)}: catalog is not valid JSON: {error}")
                declared = {}
            if declared.get("product") != product or not isinstance(declared.get("features"), list):
                errors.append(f"{catalog.relative_to(root)}: catalog must name product "
                              f"{product} and list its features")
        hooks = package / "hooks.py"
        if not re.search(r"^pearnly_navigation\s*=", hooks.read_text(), re.MULTILINE):
            errors.append(f"{hooks.relative_to(root)}: declare this product's menu as "
                          "pearnly_navigation")
        glossary = package / "i18n/messages.tsv"
        if not glossary.is_file():
            errors.append(f"{glossary.relative_to(root)}: each product keeps its own glossary")
    if (root / "scripts/i18n/messages.tsv").exists():
        errors.append("scripts/i18n/messages.tsv: the glossary is per product now "
                      "(apps/<app>/<app>/i18n/messages.tsv)")
    navigation = root / "apps/pearnly_core/pearnly_core/desk/navigation.py"
    if navigation.is_file() and re.search(r"pearnly-[a-z]", navigation.read_text()):
        errors.append(f"{navigation.relative_to(root)}: the shared layer must not hold product "
                      "menu entries; each product declares its own")
    return errors


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
    cowork = policy.get("cowork_domain_dependencies", {})
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
                if n.module == "pearnly_cowork":
                    imports += [f"pearnly_cowork.{a.name}" for a in n.names]
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
                if module.startswith("pearnly_cowork.") and dependency.startswith("pearnly_cowork."):
                    source_domain = module.split(".")[1]
                    target_domain = dependency.split(".")[1]
                    if (source_domain in cowork and target_domain in cowork
                            and source_domain != target_domain
                            and target_domain not in cowork[source_domain]):
                        errors.append(f"{relative}:{n.lineno}: new domain dependency requires architecture review: pearnly_cowork {source_domain} -> {target_domain}")
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
        if any(route not in {"/dms/editor", "/dms/account"} for route in routes):
            errors.append("frontend/src/router.ts: product Web routes must use native Desk; only scoped LINE editors are standalone")
    for path in (root / "apps").glob("*/*/public/desk/*.js"):
        source = path.read_text()
        if re.search(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\(", source):
            errors.append(f"{path.relative_to(root)}: use native frappe Dialog/confirm/msgprint")
        if re.search(r"<(?:input|select|textarea|table)\b", source):
            errors.append(f"{path.relative_to(root)}: use native Desk controls and Grid/DataTable")
    errors.extend(check_product_declarations(root))
    return errors


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure)
    print(f"Architecture: {'FAIL' if failures else 'PASS'} ({len(failures)} violations)")
    raise SystemExit(bool(failures))
