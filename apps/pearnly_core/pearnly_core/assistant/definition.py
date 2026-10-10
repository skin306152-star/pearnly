"""智能体的定义：模型、系统提示词与工具表，按本站产品从代码生成（ADR-019）。

定义随代码版本走；`fingerprint` 变了，站点下次用时自动更新对应智能体（见 `provision`）。
工具表来自 `tools.declared()`，与服务端派发同源。
"""
import hashlib
import json

from pearnly_core.assistant import tools

MODEL = {"id": "claude-opus-5-5", "effort": "medium"}
ENVIRONMENT_NAME = "pearnly-assistant"
MANAGED_BY = "pearnly-assistant"

SYSTEM = """You are the Pearnly assistant for the {product} product. Pearnly is an accounting and \
business platform; the person you talk to is a signed-in user of one company (tenant).

- Reply in the user's language.
- Business data and actions are reached only through the Pearnly tools you have. Never invent records, \
amounts, document numbers or results; if no tool covers what is asked, say so plainly.
- Tools run with the user's own permissions. When a tool refuses, tell the user what was refused and why, \
and do not try to work around it.
- Keep answers short and concrete."""


def spec(product: str) -> dict:
    custom = [{"type": "custom", "name": t["name"], "description": t["description"],
               "input_schema": t["input_schema"]} for t in tools.declared()]
    body = {
        "name": f"Pearnly {product} assistant",
        "model": MODEL,
        "system": SYSTEM.format(product=product),
        "tools": [
            {"type": "agent_toolset_20260401",
             "default_config": {"enabled": True, "permission_policy": {"type": "auto"}},
             "configs": [{"name": "web_fetch", "enabled": False}, {"name": "web_search", "enabled": False}]},
            *custom,
        ],
    }
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
    body["metadata"] = {"pearnly_managed": MANAGED_BY, "pearnly_product": product, "pearnly_spec": fingerprint}
    return body


ENVIRONMENT = {"name": ENVIRONMENT_NAME,
               "config": {"type": "cloud", "networking": {"type": "limited"}}}
