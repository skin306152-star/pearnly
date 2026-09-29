"""Primary-page selection for the push; caller supplies a detached history."""
from typing import Any, Dict

from pearnly_cowork.contracts.history import primary_page


def flatten_history_for_mrerp(history_record: Dict[str, Any]) -> Dict[str, Any]:
    """P1c · 把 OCR pages 顶层 fields 拍平,让 MRERPAdapter._extract_buyer 能直接
    在 history_flat['fields'] 找到 buyer_name(从 push_mrerp_history 抽出)。不写 tenant_id(caller 加)。

    选哪一页与列表 / 复核页同一判定（`contracts.history.primary_page`）：旧线上取第一个非副本页，
    那页读空时推的不是复核页上改的那页（2026-09-29 有意改正）。"""
    pages = history_record.get("pages") or []
    primary = primary_page(pages)
    fields = (primary or {}).get("fields") if isinstance(primary, dict) else {}
    fields = fields if isinstance(fields, dict) else {}

    history_flat = dict(history_record)
    history_flat["fields"] = fields
    return history_flat
