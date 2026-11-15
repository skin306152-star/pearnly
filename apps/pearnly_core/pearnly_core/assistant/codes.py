"""CC 工具收到的 Express / MR.ERP 编码 → 主档里的原样编码（三产品 CC 工具共用，判法在纯契约 `express_rules.contracts.codes`）。

模型复述编码时空白会变（不换行空格变普通空格），库的排序规则又把两者当相等，所以工具入参里的编码一律先经 :func:`canonical`
换回主档原样，再查、再存、再写；绝不把模型给的串原样落进草稿或写入载荷。认不出（``several`` / ``none``）由工具回给模型
一个「请用户从候选里选」的结果（:func:`unmatched`），不让模型自己判「不存在」叫用户去 Express 建档。
"""
import frappe

from pearnly_core.express_rules.contracts import codes

CANDIDATES_MAX = 20
UNMATCHED = "code_not_matched"
UNMATCHED_NOTE = ("code_not_matched: this {kind} code does not match one {kind} on file exactly. Never say it is missing "
                  "from Express and never ask the user to create it there. Look it up again with the search tool, and if "
                  "more than one fits or none does, ask the user with ask_user to pick one of the options found (or stop). "
                  "Use codes exactly as the tool results give them.")


def canonical(doctype, field, given, filters=None) -> dict:
    """在 ``doctype`` 的 ``field`` 里认 ``given``（``filters`` 是调用方已有的范围：公司、账套、在用）。回
    `codes.resolve` 的结果（``status`` / ``code`` / ``matches``）。给的是空串回 ``none``。"""
    text = str(given or "")
    pattern = codes.like_pattern(text)
    if not pattern:
        return codes.resolve(text, ())
    found = frappe.get_all(doctype, filters={**(filters or {}), field: ["like", pattern]}, pluck=field,
                           order_by="creation asc", limit_page_length=CANDIDATES_MAX)
    return codes.resolve(text, found)


def among(given, candidates) -> dict:
    """候选已在手（快照行、读回的表）时直接判。"""
    return codes.resolve(given, candidates)


def unmatched(kind, given, resolved, **extra) -> dict:
    """给模型的「认不出」结果：原因码、几个像的候选（原样编码），说明里叫它问人选，不说「不存在」。"""
    return {"status": "not_saved", "reason": UNMATCHED, "kind": kind, "given": str(given or ""),
            "options": list(resolved.get("matches") or ())[:CANDIDATES_MAX], "note": UNMATCHED_NOTE.format(kind=kind),
            **extra}


__all__ = ["CANDIDATES_MAX", "UNMATCHED", "canonical", "among", "unmatched"]
