"""「本公司是票上的哪一方」（卡片一类 ``own_party``，多产品共用，纯函数；评估 `docs/work/2026-11-10-cc-own-tax-once-评估.md`）。

所选公司、工作空间、账套目录都没有税号时，推送判不出进项还是销项（``direction_unknown``）。卡片问一次：这批票上出现过的
税号里哪个是本公司，答案记成公司的税号与分店，以后推送判方向用它，这家不再问（主会话 11-10 审定）：

- :func:`options`：一张票上可选的两方（卖方、买方里税号有效的），``{code: "<税号>:<分店>", name: "<名称> · <税号>"}``；
- :func:`merge`：一批票的选项合成一份，按出现张数排；每张都出现、且只有一个这样的税号时它是预选值；
- :func:`parse`：答案（选项编码，或「其他」里手输的 13 位税号，可带 ``/分店``）→ ``(税号, 分店)``，不合法回 None。

不依赖 Frappe、不读库。
"""
from __future__ import annotations

from collections import Counter
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from pearnly_core.express_rules.contracts.push import valid_tax_id  # 泰国税号 MOD-11（`tax_documents.amounts`）
from pearnly_core.express_rules.push.choices import branch5, digits

KIND = "own_party"
SIDES = ("seller", "buyer")
HEAD_OFFICE = "00000"


def tax_id(value: Any) -> str:
    """有效的 13 位税号；不是就空串。"""
    raw = digits(value)
    return raw if len(raw) == 13 and valid_tax_id(raw) else ""


def branch(value: Any) -> str:
    """分店号归一成 5 位（空 = 总公司 00000）。"""
    return branch5(value)


def code(tax: str, branch_no: str) -> str:
    return f"{tax}:{branch_no}"


def options(fields: Mapping[str, Any]) -> List[dict]:
    """一张票上可选的两方（税号无效的一方不列；两方同一税号同一分店只列一次）。"""
    out: List[dict] = []
    for side in SIDES:
        tax = tax_id(fields.get(f"{side}_tax") or fields.get(f"{side}_tax_id"))
        if not tax:
            continue
        branch_no = branch(fields.get(f"{side}_branch") or fields.get(f"{side}_branch_no"))
        name = " ".join(str(fields.get(f"{side}_name") or "").split())
        shown = " · ".join(part for part in (name, tax, "" if branch_no == HEAD_OFFICE else branch_no) if part)
        item = {"code": code(tax, branch_no), "name": shown[:200]}
        if all(existing["code"] != item["code"] for existing in out):
            out.append(item)
    return out


def merge(per_document: Sequence[Sequence[Mapping[str, Any]]]) -> Tuple[List[dict], str]:
    """一批票的选项 → ``(选项, 预选值)``：按出现张数排（同张数按先出现）；每张都有的税号只有一个时它是预选值，否则空串。"""
    counts: Counter = Counter()
    first: dict = {}
    for rows in per_document:
        for row in {str(r.get("code") or ""): r for r in rows if r.get("code")}.values():
            counts[row["code"]] += 1
            first.setdefault(row["code"], dict(row))
    ordered = sorted(first, key=lambda key: (-counts[key], list(first).index(key)))
    everywhere = [key for key in ordered if counts[key] == len(per_document)]
    return [first[key] for key in ordered], (everywhere[0] if len(per_document) and len(everywhere) == 1 else "")


def unify(items: Sequence[dict], proposed: str) -> None:
    """一批里「本公司是哪一方」按公司（归类键）合成一题，原地改：选项是这批票上出现过的税号（:func:`merge`），每张都有的那个
    预选（来源 ``proposed``，要人点开始才算数）。Cowork、ERP 的开始卡同用。"""
    by_company: dict = {}
    for item in items:
        if item.get("kind") == KIND:
            by_company.setdefault(item.get("subject"), []).append(item)
    for rows in by_company.values():
        options, value = merge([row.get("options") or [] for row in rows])
        for row in rows:
            row["options"] = [dict(option) for option in options]
            if value:
                row.update(value=value, source=proposed)


def parse(value: Any) -> Optional[Tuple[str, str]]:
    """答案 → ``(税号, 分店)``：选项编码 ``税号:分店``，或手输「13 位税号」可带分店（``0105555012345/00001``、空格横线随意）。"""
    text = str(value or "").strip().replace("/", ":")
    head, _sep, tail = text.partition(":")
    if not tail and len(digits(head)) == 18:  # 税号 + 分店连着打
        head, tail = digits(head)[:13], digits(head)[13:]
    tax = tax_id(head)
    if not tax or len(digits(head)) != 13 or (tail.strip() and not digits(tail)) or len(digits(tail)) > 5:
        return None
    return tax, branch(tail) if digits(tail) else HEAD_OFFICE


__all__ = ["HEAD_OFFICE", "KIND", "SIDES", "branch", "code", "merge", "options", "parse", "tax_id", "unify"]
