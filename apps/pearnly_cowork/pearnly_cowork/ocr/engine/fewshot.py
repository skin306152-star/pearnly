"""Select source-compatible fewshots from an already-authorized, frozen snapshot."""
import re
from collections.abc import Mapping, Sequence

_TAX_ID = re.compile(r"\d{13}")
_MAX_EXAMPLES = 12


def build_prompt_block(examples: list) -> str:
    if not examples:
        return ""
    lines = ["已知该商户历史人工修正(同字段优先按修正后口径输出,除非本张确实不同):"]
    for example in examples[:_MAX_EXAMPLES]:
        ai_value = example.get("ai_value") or "(空)"
        corrected = example.get("corrected_value") or ""
        lines.append(f"- {example.get('field_name')}: AI 曾输出「{ai_value}」→ 正确「{corrected}」")
    return "\n".join(lines) + "\n\n"


def fewshot_for_text(text: str, examples_by_tax: Mapping[str, Sequence[dict]]) -> str:
    """First four distinct tax IDs, stable (field, AI value) dedup, first 12 examples.

    Scope, ordering, freshness and authorization belong to admission, not this
    function. Never accept this snapshot directly from an untrusted public API.
    """
    examples, seen = [], set()
    for tax in list(dict.fromkeys(_TAX_ID.findall(text)))[:4]:
        for example in examples_by_tax.get(tax, ()):
            key = (example.get("field_name"), example.get("ai_value"))
            if key not in seen:
                seen.add(key)
                examples.append(example)
    return build_prompt_block(examples)
