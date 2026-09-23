"""Hash-bound policy differential; no legacy imports, env, DB or provider IO."""
import argparse
import ast
import hashlib
from itertools import product
from pathlib import Path
from types import SimpleNamespace

from pearnly_cowork.ocr.policy import model_for, resolve_mode, vertex_location

HASHES = {
    "services/ocr/engine_policy.py": "f75c9a0a9b152249e5ea806bcbf112312177d90a3c1b307d0fa1e83fe19c088e",
    "services/ocr/gemini_models.py": "0bf7707e9bec7519ad724055c55689db22385713c54a467cca7fe5ede9d0ebbe",
    "services/ai_gateway/providers/vertex.py": "78473a9ba7956e5f6ec3f9c76afd91eafeef2f779bf83f9a7d8d9b6f985f64d7",
}


def oracle(root):
    selection = {
        "services/ocr/engine_policy.py": {"MODE_MODEL_MAPS", "MODE_UNSUPPORTED_TASKS", "_FAILSAFE_MODE", "resolve_mode"},
        "services/ocr/gemini_models.py": {"_override_for", "flash", "flash_lite", "fallback", "escalate"},
        "services/ai_gateway/providers/vertex.py": {"_GLOBAL_ONLY_PREFIXES", "_location", "_location_for_model"},
    }
    namespace = {"Dict": dict, "Any": object, "Optional": __import__("typing").Optional,
                 "os": SimpleNamespace(environ={}), "_OVERRIDE": SimpleNamespace(get=lambda: {})}
    for path, digest in HASHES.items():
        raw = (root / path).read_bytes()
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("Fixed source hash mismatch: " + path)
        nodes = []
        for node in ast.parse(raw).body:
            name = node.name if isinstance(node, ast.FunctionDef) else None
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
                name = node.targets[0].id
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                name = node.target.id
            if name in selection[path]:
                nodes.append(node)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "fixed:" + path, "exec"), namespace)
    return namespace


def verify(root):
    old = oracle(root)
    modes = ("", "economy", "enterprise", "direct35", "qwen", "selfhost", "auto", "invalid")
    count = 0
    for forced, global_mode, invoice, plan, exempt in product(modes, modes, modes, (None, "S", "M", "L", "unknown"), (False, True)):
        env = {"OCR_ENGINE_MODE": forced}
        cfg = {"mode": global_mode, "overrides_by_task": {"invoice": invoice},
               "defaults_by_plan": {"none": "direct35", "S": "enterprise", "M": "economy", "L": "qwen", "exempt": "selfhost"}}
        old["os"].environ = env
        assert old["resolve_mode"]("invoice", plan, exempt, cfg) == resolve_mode(env, cfg, plan_code=plan, is_exempt=exempt)
        count += 1
    model_count = 0
    for mode, blank, value in product(old["MODE_MODEL_MAPS"], (False, True), ("", "gemini-3.8-flash", "gemini-3.5-flash")):
        env = {} if blank else {key: value for key in ("OCR_FLASH_MODEL", "OCR_FLASHLITE_MODEL", "OCR_FALLBACK_MODEL", "OCR_ESCALATE_MODEL")}
        old["os"].environ = env
        old["_OVERRIDE"] = SimpleNamespace(get=lambda m=mode: old["MODE_MODEL_MAPS"][m])
        for tier in ("flash", "flash_lite", "fallback", "escalate"):
            assert old[tier]() == model_for(mode, tier, env)
            model_count += 1
    location_count = 0
    for model, local, global_location in product(("gemini-3.8-flash", "gemini-3.1-flash-lite", "gemini-2.5-flash", "gemini-3.5-flash", "other"),
                                                  ("", "asia-southeast1", "us-central1"), ("", "global", "us-central1")):
        env = {"VERTEX_LOCATION": local, "VERTEX_LOCATION_25": global_location}
        old["os"].environ = env
        assert old["_location_for_model"](model) == vertex_location(model, env)
        location_count += 1
    print(f"Policy parity PASS: {count} selections, {model_count} model tiers, {location_count} locations")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True, type=Path)
    verify(parser.parse_args().source)
