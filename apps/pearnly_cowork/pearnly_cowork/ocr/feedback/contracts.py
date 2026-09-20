"""Pure invoice correction/few-shot contracts from deployed ec2e5c957e6b.

The caller must supply examples already authorized for the server-bound actor
and tenant, ordered by use_count DESC, updated_at DESC within each tax ID.
This module does not grant access, fetch examples, read environment/context,
or decide which OCR route receives the prompt. Amounts are compared as the
source's trimmed text, never recalculated or rounded.
"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re


TRACKED_FIELDS = (
    "invoice_number", "invoice_date", "date", "seller_name", "seller_tax",
    "buyer_name", "buyer_tax", "total_amount", "subtotal", "vat", "wht", "category",
)
MAX_TAX_IDS = 4
MAX_EXAMPLES_PER_TAX = 20
MAX_PROMPT_EXAMPLES = 12
_TAX_ID = re.compile(r"\d{13}")


def _primary_fields(pages) -> dict:
    for page in pages or []:
        if not page.get("is_copy") and not page.get("is_duplicate"):
            return page.get("fields") or {}
    if pages:
        return pages[0].get("fields") or {}
    return {}


def _norm(value) -> str:
    return "" if value is None else str(value).strip()


def compute_field_corrections(ai_pages, corrected_pages) -> list[dict]:
    """Compare first OCR evidence with an edit; clearing a field is not learned.

    The first non-copy/non-duplicate page wins, even when its fields are empty.
    When all pages are copies, the first page wins. Internal fields, item rows,
    discounts and wht_amount stay outside the deployed learning allowlist.
    """
    ai = _primary_fields(ai_pages)
    current = _primary_fields(corrected_pages)
    corrections = []
    for field in TRACKED_FIELDS:
        before = _norm(ai.get(field))
        after = _norm(current.get(field))
        if after and after != before:
            corrections.append({"field_name": field, "ai_value": before or None, "corrected_value": after})
    return corrections


def primary_seller(pages) -> tuple[str | None, str | None]:
    """Return the primary page's printed seller tax/name, without a lookup."""
    fields = _primary_fields(pages)
    return _norm(fields.get("seller_tax")) or None, _norm(fields.get("seller_name")) or None


def tax_ids_for_text(text: str) -> list[str]:
    r"""First four distinct regex matches, retaining the source's Unicode \d.

    This is prompt lookup selection, not tax-ID validation. No checksum or
    digit boundaries are added; a long digit run can contain multiple matches.
    """
    return list(dict.fromkeys(_TAX_ID.findall(text)))[:MAX_TAX_IDS]


def select_authorized_examples(
    text: str, authorized_examples_by_tax: Mapping[str, Sequence[dict]],
) -> list[dict]:
    """Keep source order and the first (field_name, ai_value) across all taxes.

    The per-tax cap reproduces fetch_examples' SQL LIMIT 20. Do not globally
    re-sort, trim, normalize money, or deduplicate by corrected_value: each
    would change the actual prompt. Copies keep caller-owned evidence intact.
    """
    examples, seen = [], set()
    for tax in tax_ids_for_text(text):
        for example in authorized_examples_by_tax.get(tax, ())[:MAX_EXAMPLES_PER_TAX]:
            key = (example.get("field_name"), example.get("ai_value"))
            if key not in seen:
                seen.add(key)
                examples.append(deepcopy(example))
    return examples


def build_prompt_block(examples: list[dict]) -> str:
    """Preserve the deployed prompt bytes, ordering, cap and empty markers."""
    if not examples:
        return ""
    lines = ["已知该商户历史人工修正(同字段优先按修正后口径输出,除非本张确实不同):"]
    for example in examples[:MAX_PROMPT_EXAMPLES]:
        before = example.get("ai_value") or "(空)"
        after = example.get("corrected_value") or ""
        lines.append(f"- {example.get('field_name')}: AI 曾输出「{before}」→ 正确「{after}」")
    return "\n".join(lines) + "\n\n"


def prompt_for_text(
    text: str, authorized_examples_by_tax: Mapping[str, Sequence[dict]], *, enabled: bool,
) -> str:
    """Compose a hint from an explicit policy and previously authorized data.

    A disabled policy or absent/malformed examples produces no hint, as in the
    source. The caller handles lookup failures before calling this function;
    no request context is synthesized and no authorization is inferred here.
    """
    if type(enabled) is not bool:
        raise ValueError("few-shot enabled must be an explicit boolean")
    if not enabled or not text:
        return ""
    try:
        return build_prompt_block(select_authorized_examples(text, authorized_examples_by_tax))
    except Exception:
        return ""
