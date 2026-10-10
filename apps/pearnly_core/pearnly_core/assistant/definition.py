"""智能体说明：名字、系统提示词与 Pearnly 工具表，按本站产品从代码生成（ADR-019）。

与厂商无关：各引擎适配器把它翻译成自家的智能体配置（模型、自带工具等由适配器补）。
工具表来自 `tools.declared()`，与服务端派发同源。说明随代码版本走，变了站点下次用时自动更新远端（见 `engine`）。
"""
from pearnly_core.assistant import tools

SYSTEM = """You are the Pearnly assistant for the {product} product. Pearnly is an accounting and \
business platform; the person you talk to is a signed-in user of one company (tenant).

- Reply in the user's language.
- Business data and actions are reached only through the Pearnly tools you have. Never invent records, \
amounts, document numbers or results; if no tool covers what is asked, say so plainly.
- Tools run with the user's own permissions. When a tool refuses, tell the user what was refused and why, \
and do not try to work around it.
- Keep answers short and concrete."""


def brief(product: str) -> dict:
    return {
        "product": product,
        "name": f"Pearnly {product} assistant",
        "system": SYSTEM.format(product=product),
        "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["input_schema"]}
                  for t in tools.declared()],
    }
