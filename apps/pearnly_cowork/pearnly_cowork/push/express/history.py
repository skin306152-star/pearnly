"""Legacy first-original-page selection; caller supplies a detached history."""
from typing import Any, Dict

def flatten_history_for_mrerp(history_record: Dict[str, Any]) -> Dict[str, Any]:
    """P1c · 把 OCR pages 顶层 fields 拍平,让 MRERPAdapter._extract_buyer 能直接
    在 history_flat['fields'] 找到 buyer_name(行为不变 · 从 push_mrerp_history 抽出)。
    选第一个非 duplicate/copy 的 page · 没有就退第一页。不写 tenant_id(caller 加)。"""
    pages = history_record.get("pages") or []
    primary = None
    for p in pages:
        if isinstance(p, dict) and not p.get("is_duplicate") and not p.get("is_copy"):
            primary = p
            break
    if primary is None and pages and isinstance(pages[0], dict):
        primary = pages[0]
    fields = (primary or {}).get("fields") if isinstance(primary, dict) else {}
    fields = fields if isinstance(fields, dict) else {}

    history_flat = dict(history_record)
    history_flat["fields"] = fields
    return history_flat
