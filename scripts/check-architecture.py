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


# 回放压测（不调谷歌、交回以前的真结果）只许在测试租户打开（``pearnly_core.execution.replay``）：
# 认的站点名写死在那个模块里，不许放宽；开关键也不许写进生产配置、部署脚本或别的产品代码 —— 正式站误开即使被运行时拒绝，
# 也不该有机会出现在发布物里。
REPLAY_KEY = "pearnly_ocr_replay"
REPLAY_SITES = r"test(?:cowork|erp|dms)[0-9]{0,2}\.pearnly\.com|[a-z0-9-]+\.localhost"
REPLAY_MODULE = "apps/pearnly_core/pearnly_core/execution/replay.py"
REPLAY_ALLOWED = {REPLAY_MODULE, "scripts/check-architecture.py", "scripts/ocr-replay.py"}


def check_ocr_replay(root):
    errors = []
    module = root / REPLAY_MODULE
    if not module.is_file():
        return errors
    sites = re.search(r'^TEST_SITES = re\.compile\(r"([^"]*)"\)', module.read_text(), re.MULTILINE)
    if not sites or sites.group(1) != REPLAY_SITES:
        errors.append(f"{REPLAY_MODULE}: OCR replay may only be enabled on the test tenants; do not widen TEST_SITES")
    for top in ("apps", "infrastructure", "scripts", ".github"):
        base = root / top
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            relative = path.relative_to(root).as_posix()
            if (not path.is_file() or relative in REPLAY_ALLOWED or "/test_" in relative or "/tests/" in relative
                    or any(part in {"node_modules", "__pycache__", "translations"} for part in path.parts)
                    or path.suffix in {".png", ".jpg", ".pdf", ".xlsx", ".woff2", ".csv", ".tsv"}):
                continue
            try:
                text = path.read_text()
            except (UnicodeDecodeError, OSError):
                continue
            if REPLAY_KEY in text:
                errors.append(f"{relative}: {REPLAY_KEY} may only be set on a test tenant through scripts/ocr-replay.py")
    return errors


# Cowork 识别的 Cloud Run 镜像只拷 Dockerfile 里列的那几个目录 / 文件（ADR-0015）：从服务入口出发，模块顶层导入
# 用到的本仓模块都要在拷贝清单里，否则镜像构建时导入就失败（2026-10-07：识别结构引用了 contracts，到部署才发现）。
CLOUD_RUN_DOCKERFILE = "infrastructure/cloud-run/cowork-ocr.Dockerfile"
CLOUD_RUN_ENTRY = "pearnly_cowork.ocr.remote.app"


def _module_file(root, module):
    package = module.split(".")[0]
    base = root / "apps" / package / Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_imports(tree, module, is_package):
    """模块导入时就会执行的导入（不进函数体）：返回完整模块名。"""
    found, stack = [], list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = module.split(".") if is_package else module.split(".")[:-1]
                parts = parts[:len(parts) - node.level + 1]
                base = ".".join(parts + ([node.module] if node.module else []))
            else:
                base = node.module or ""
            found.append(base)
            found.extend(f"{base}.{alias.name}" for alias in node.names)
        stack.extend(ast.iter_child_nodes(node))
    return found


def check_cloud_run_image(root):
    dockerfile = root / CLOUD_RUN_DOCKERFILE
    if not dockerfile.is_file() or _module_file(root, CLOUD_RUN_ENTRY) is None:
        return []
    copied = []
    for line in dockerfile.read_text().splitlines():
        words = line.split()
        if words[:1] == ["COPY"]:
            copied.extend(root / source for source in words[1:-1])
    errors, seen, queue = [], set(), [CLOUD_RUN_ENTRY]
    while queue:
        module = queue.pop()
        path = _module_file(root, module)
        if path is None or path in seen:
            continue
        seen.add(path)
        if not any(path == source or source in path.parents for source in copied):
            errors.append(f"{path.relative_to(root)}: imported by the Cloud Run OCR service ({CLOUD_RUN_ENTRY}) "
                          f"but not copied into {CLOUD_RUN_DOCKERFILE}")
            continue
        parts = module.split(".")
        queue.extend(".".join(parts[:i]) for i in range(1, len(parts)))
        tree = ast.parse(path.read_text(), filename=str(path))
        queue.extend(name for name in _top_level_imports(tree, module, path.name == "__init__.py")
                     if name.startswith("pearnly_"))
    return errors


# GitHub Actions 附件全账户只有 0.5 GB 存储额度，按每小时累计计费；一个小助手安装包约 150 MB。
# 2026-10-10 起每次推送都上传安装包并留 7 天，几天就把额度用满、上传被拒（用户 2026-10-11 定：一律只留 1 天）。
ARTIFACT_RETENTION_DAYS = "1"
_UPLOAD_ARTIFACT = re.compile(r"\buses:\s*actions/upload-artifact@")
_RETENTION = re.compile(r"^\s*retention-days:\s*['\"]?([^'\"\s#]+)")


def check_artifact_retention(root):
    """每个 upload-artifact 步骤都写明 retention-days: 1，不写就是 GitHub 默认的 90 天。"""
    errors = []
    base = root / ".github/workflows"
    if not base.is_dir():
        return errors
    for path in sorted([*base.glob("*.yml"), *base.glob("*.yaml")]):
        lines = path.read_text().splitlines()
        for index, line in enumerate(lines):
            if not _UPLOAD_ARTIFACT.search(line):
                continue
            start = next((i for i in range(index, -1, -1) if re.match(r"^\s*-\s", lines[i])), index)
            indent = len(lines[start]) - len(lines[start].lstrip())
            end = start + 1
            while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) > indent):
                end += 1
            values = [m.group(1) for m in map(_RETENTION.match, lines[start:end]) if m]
            if values != [ARTIFACT_RETENTION_DAYS]:
                errors.append(f"{path.relative_to(root).as_posix()}:{index + 1}: upload-artifact must set "
                              f"retention-days: {ARTIFACT_RETENTION_DAYS} (the account has 0.5 GB of Actions storage)")
    return errors


SITE_CONFIG_KEY = re.compile(r"""conf(?:\.get\(|\[)\s*["'](pearnly_[a-z0-9_]+)["']""")


def check_site_config_keys(root, policy):
    """站点配置键要登记用途（`architecture-policy.json` 的 ``site_config_keys``）。产品功能对所有租户直接生效，
    不加按租户的启用开关：文件夹自动录入、上传前缩照片都曾做成默认关的开关，新开户就没有（用户 2026-10-07）。"""
    registered = set(policy.get("site_config_keys", {})) - {"_rule"}
    errors = []
    base = root / "apps"
    if not base.is_dir():
        return errors
    for path in base.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if "/tests/" in relative or "/test_" in relative or any(part in {"node_modules", "__pycache__"} for part in path.parts):
            continue
        for key in sorted(set(SITE_CONFIG_KEY.findall(path.read_text()))):
            if key not in registered:
                errors.append(f"{relative}: site config key {key} is not registered in docs/development/architecture-policy.json "
                              "site_config_keys; features apply to every tenant, so do not add a per-tenant on/off switch")
    return errors


def self_calls(tree):
    """函数体（除文档字符串）只有一句 ``return 自己(...)``：一调用就无限递归（2026-10-03 DMS 编辑票据
    ``_state_store`` 机械替换时写成自调用，LIFF 编辑器全挂，测试都替换了这个函数所以没测出来）。"""
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = [b for b in node.body if not (isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant))]
        if (len(body) == 1 and isinstance(body[0], ast.Return) and isinstance(body[0].value, ast.Call)
                and isinstance(body[0].value.func, ast.Name) and body[0].value.func.id == node.name):
            found.append((node.lineno, node.name))
    return found


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
        errors.extend(f"{relative}:{line}: {name}() only calls itself and recurses forever"
                      for line, name in self_calls(tree))
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
                    if prefix not in {"__future__", "datetime", "dataclasses", "typing", "decimal", "re", "enum", "collections", "math", "html", "unicodedata", "pearnly_dms"} or (prefix == "pearnly_dms" and not dependency.startswith("pearnly_dms.contracts.")):
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
        # 三个都是 LINE 一次性票据页（编辑草稿 / 账号 / 扫身份证），不是产品 Web 的第二套外壳。
        if any(route not in {"/dms/editor", "/dms/account", "/dms/scan"} for route in routes):
            errors.append("frontend/src/router.ts: product Web routes must use native Desk; only scoped LINE editors are standalone")
    # ADR-019: React is allowed only for the assistant page (frontend/assistant), never as a general product shell.
    for path in (root / "frontend").rglob("package.json"):
        if "node_modules" in path.parts or path.relative_to(root).as_posix() == "frontend/assistant/package.json":
            continue
        declared = json.loads(path.read_text())
        if "react" in {**declared.get("dependencies", {}), **declared.get("devDependencies", {})}:
            errors.append(f"{path.relative_to(root)}: React is scoped to frontend/assistant (ADR-019)")
    for path in (root / "apps").glob("*/*/public/desk/*.js"):
        source = path.read_text()
        if re.search(r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\(", source):
            errors.append(f"{path.relative_to(root)}: use native frappe Dialog/confirm/msgprint")
        if re.search(r"<(?:input|select|textarea|table)\b", source):
            errors.append(f"{path.relative_to(root)}: use native Desk controls and Grid/DataTable")
    errors.extend(check_product_declarations(root))
    errors.extend(check_ocr_replay(root))
    errors.extend(check_site_config_keys(root, policy))
    errors.extend(check_cloud_run_image(root))
    errors.extend(check_artifact_retention(root))
    return errors


if __name__ == "__main__":
    failures = check()
    for failure in failures:
        print(failure)
    print(f"Architecture: {'FAIL' if failures else 'PASS'} ({len(failures)} violations)")
    raise SystemExit(bool(failures))
