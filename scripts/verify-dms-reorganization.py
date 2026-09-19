#!/usr/bin/env python3
"""One-time move audit against the recorded Git baseline; not a permanent test.

Compare complete Python ASTs after applying only the declared import/module-path
mapping. Any other behavior change requires separate review and separate evidence.
"""
import ast
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DATA = json.loads((ROOT / "docs/development/dms-module-map.json").read_text())
MAPPING = DATA["modules"]
PATTERN = re.compile(r"\b(" + "|".join(map(re.escape, sorted(MAPPING, key=len, reverse=True))) + r")\b")


class Normalize(ast.NodeTransformer):
    def visit_ImportFrom(self, node):
        if node.module == "pearnly_dms" and any("pearnly_dms." + a.name in MAPPING for a in node.names):
            result = []
            for alias in node.names:
                key = "pearnly_dms." + alias.name
                if key in MAPPING:
                    parent, name = MAPPING[key].rsplit(".", 1)
                    result.append(ast.ImportFrom(module=parent, names=[ast.alias(name=name, asname=alias.asname or alias.name)], level=0))
                else:
                    result.append(ast.ImportFrom(module=node.module, names=[alias], level=node.level))
            return result
        node.module = MAPPING.get(node.module, node.module)
        return node

    def visit_Constant(self, node):
        if isinstance(node.value, str):
            node.value = PATTERN.sub(lambda m: MAPPING[m[0]], node.value)
            for old, new in MAPPING.items():
                node.value = node.value.replace(old.replace(".", "/") + ".py", new.replace(".", "/") + ".py")
        return node


if __name__ == "__main__":
    failures = []
    for old, new in MAPPING.items():
        source = "apps/pearnly_dms/" + old.replace(".", "/") + ".py"
        original = subprocess.check_output(["git", "show", DATA["baseline"] + ":" + source], cwd=ROOT, text=True)
        dest = ROOT / "apps/pearnly_dms" / (new.replace(".", "/") + ".py")
        before = ast.dump(Normalize().visit(ast.parse(original)), include_attributes=False)
        after = ast.dump(ast.parse(dest.read_text()), include_attributes=False)
        if before != after:
            failures.append(new)
    print(f"Mechanical move audit: {len(MAPPING) - len(failures)}/{len(MAPPING)} identical after import/path normalization")
    for failure in failures: print("DIFFERENT", failure)
    raise SystemExit(bool(failures))
