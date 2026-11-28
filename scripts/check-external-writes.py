#!/usr/bin/env python3
"""站外写入点必须登记、富菜单这类表面必须随部署同步（11-28，ris 富菜单 9 月后从没更新的事故）。

规则：
1. 生产代码（apps/ 非测试、infrastructure/production/、scripts/production/）里，模块提到 LINE / Keycloak / Cloudflare 接口、
   函数里又有写动词（"POST" / "PUT" / "PATCH" / "DELETE" 字面量，或 requests / session / http / api / client 的
   .post / .put / .patch / .delete 调用）的，算一个站外写入点，必须在 scripts/external-writes.toml 登记 kind 与 note；
   登记了但函数已不在 → 拦（登记表要跟着代码删）。
2. kind = deploy-sync 的函数所在模块，必须被某个产品 hooks.py 的 ``pearnly_line_surfaces`` 声明；
   声明的模块必须定义 SURFACE、CONFIG、ready、wanted、drift、publish、restore（pearnly_core.line_surfaces 的约定）。
3. kind = manual 的 note 要写原因（不能空）；kind 只能是登记表文件头列出的几种。

退出码：0 通过；1 有违规（逐条 FAIL 行）。
"""
import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "scripts" / "external-writes.toml"
KINDS = {"deploy-sync", "drift-check", "runtime", "token", "probe", "teardown", "manual"}
MARK = re.compile(r"api\.line\.me|api-data\.line\.me|richmenu|liff/v1|/v2/bot/|/admin/realms|api\.cloudflare\.com|identity-provider")
VERBS = {"POST", "PUT", "PATCH", "DELETE"}
CALLS = {"post", "put", "patch", "delete"}
CALLERS = {"requests", "session", "http", "api", "client"}
SURFACE_NAMES = {"SURFACE", "CONFIG", "ready", "wanted", "drift", "publish", "restore"}
HOOK = "pearnly_line_surfaces"


def production_files(root: Path):
    listed = subprocess.run(["git", "ls-files", "apps/*.py", "infrastructure/production/*.py", "scripts/production/*.py"],
                            cwd=root, capture_output=True, text=True, check=True).stdout.split()
    for rel in listed:
        name = rel.rsplit("/", 1)[-1]
        if "/tests/" in rel or name.startswith("test"):
            continue
        yield rel


def _writes(node) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and child.value in VERBS:
            return True
        if (isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute) and child.func.attr in CALLS
                and isinstance(child.func.value, ast.Name) and child.func.value.id in CALLERS):
            return True
    return False


def write_points(root: Path, files=None) -> set:
    found = set()
    for rel in files if files is not None else production_files(root):
        source = (root / rel).read_text(encoding="utf-8")
        if not MARK.search(source):
            continue
        for node in ast.walk(ast.parse(source, rel)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _writes(node):
                found.add(f"{rel}::{node.name}")
    return found


def declared_surfaces(root: Path) -> dict:
    """hooks.py 里 pearnly_line_surfaces 声明的 {模块路径: 产品}。"""
    out = {}
    for hooks in sorted(root.glob("apps/*/*/hooks.py")):
        for node in ast.parse(hooks.read_text(encoding="utf-8")).body:
            if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == HOOK for t in node.targets):
                for entry in ast.literal_eval(node.value):
                    out[entry["module"]] = entry["product"]
    return out


def module_file(root: Path, module: str) -> Path:
    head = module.split(".")[0]
    return root / "apps" / head / Path(*module.split(".")).with_suffix(".py")


def defined_names(path: Path) -> set:
    names = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def check(root: Path = ROOT, files=None) -> list:
    problems = []
    rows = tomllib.loads((root / "scripts" / "external-writes.toml").read_text(encoding="utf-8")).get("write", [])
    registered = {}
    for row in rows:
        target = row.get("target", "")
        if target in registered:
            problems.append(f"{target}: registered twice")
        registered[target] = row
        if row.get("kind") not in KINDS:
            problems.append(f"{target}: kind {row.get('kind')!r} is not one of {sorted(KINDS)}")
        if not str(row.get("note") or "").strip():
            problems.append(f"{target}: note is empty (say how it stays equal to the code, or why it cannot)")
    found = write_points(root, files)
    for target in sorted(found - set(registered)):
        problems.append(f"{target}: writes to LINE / Keycloak / Cloudflare but is not in scripts/external-writes.toml "
                        "(register it: deploy-sync via hooks pearnly_line_surfaces, or drift-check / runtime / manual with a reason)")
    if files is None:
        for target in sorted(set(registered) - found):
            problems.append(f"{target}: registered in scripts/external-writes.toml but no such external write any more (remove the row)")
    surfaces = declared_surfaces(root)
    for module in surfaces:
        path = module_file(root, module)
        if not path.is_file():
            problems.append(f"hooks {HOOK}: {module} does not exist")
            continue
        missing = SURFACE_NAMES - defined_names(path)
        if missing:
            problems.append(f"hooks {HOOK}: {module} lacks {', '.join(sorted(missing))}")
    for target, row in registered.items():
        if row.get("kind") != "deploy-sync":
            continue
        rel = target.split("::")[0]
        module = rel.removeprefix("apps/").split("/", 1)[1].removesuffix(".py").replace("/", ".")
        if module not in surfaces:
            problems.append(f"{target}: kind deploy-sync but {module} is not declared in any hooks {HOOK}")
    return problems


def main() -> int:
    problems = check()
    for line in problems:
        print("FAIL " + line)
    if problems:
        return 1
    print(f"external writes: all {len(write_points(ROOT))} registered; {len(declared_surfaces(ROOT))} surface modules declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
