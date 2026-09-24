# -*- coding: utf-8 -*-
"""Employee-scoped menu-5 reads from complete local snapshots only.

Native parsers remain here; only ``query.snapshot_sync`` uses them to read DMS.
Interaction paths recheck local permissions and never log in, refresh, or enqueue
an external read. Custom ranking conditions are registered for the next sweep.
"""

from __future__ import annotations

import html
import hashlib
import json
import logging
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint
from pearnly_dms.query.query_access import can_query
from pearnly_dms.snapshots import store

logger = logging.getLogger(__name__)

LIST_PATH = "drfcbc/component/showdata.php"
TOP_SALES_PATH = "home/component/carmaxsellchart.php"

# DMS selcolsort controls ordering, not search scope. sd searches every column;
# named-field queries are filtered before our own pagination below.
SEARCH_COLUMNS = {
    "booking_no": "1",
    "booking_date": "2",
    "delivery_date": "3",
    "advisor": "4",
    "customer": "5",
    "vehicle": "6",
    "color": "7",
    "finance_status": "8",
    "allocation_status": "9",
    "sales_doc_no": "10",
    "engine_no": "11",
}

# 状态筛选（DMS 的 ftd）
STATUS_FILTERS = {
    "all": "1",
    "cancelled": "2",
    "active": "3",
    "draft": "4",
    "booking": "5",
    "cash": "6",
    "finance_pending": "7",
    "finance_approved": "8",
    "manager_pending": "9",
    "ready": "10",
    "unallocated": "11",
    "allocated_no_contract": "12",
    "contract_opened": "13",
    "contract_not_opened": "14",
    "delivery_overdue": "15",
}

TOP_GROUPS = {"model": "1", "type": "2", "subtype": "3"}
TOP_METRICS = {"quantity": "1", "amount": "2"}
SALES_DATASET = "sales_query"  # Retired scheduled dataset; no new reads.
CAPABILITIES_DATASET = "query.capabilities"
SNAPSHOT_VERSION = 2

_ROW_RE = re.compile(r'data-val="([^"]+)"', re.I)
_CELL_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)


def _text(raw: str) -> str:
    """单元格 → 纯文本：去标签、解实体、压空白；DMS 的空值占位 `-` 归一成空串。"""
    value = html.unescape(re.sub(r"<.*?>", "", raw or "")).strip()
    return "" if value == "-" else " ".join(value.split())


def parse_sales_rows(body: str) -> List[Dict[str, str]]:
    """解析 DMS 原生的订车/销售进度列表，**不改状态语义**。

    一行 = 一个 `data-val` 标记到下一个标记之间的一段；每段里的 `<p>` 是各列。
    认不出（列数不足、单号为空）则拒绝整次响应，不返回悄悄缺行的结果。
    """
    source = body or ""
    marks = list(_ROW_RE.finditer(source))
    rows: List[Dict[str, str]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(source)
        cells = [_text(value) for value in _CELL_RE.findall(source[mark.end() : end])]
        if len(cells) < 13 or not cells[2]:
            raise DmsClientError("DMS sales row shape changed", "ERR_DMS_TECHNICAL")
        rows.append(
            {
                "id": mark.group(1),
                "cancel_status": cells[0],
                "record_status": cells[1],
                "booking_no": cells[2],
                "booking_date": cells[3],
                "delivery_date": cells[4],
                "advisor": cells[5],
                "customer": cells[6],
                "vehicle": cells[7],
                "color": cells[8],
                "finance_status": cells[9],
                "allocation_status": cells[10],
                "sales_doc_no": cells[11],
                "engine_no": cells[12],
            }
        )
    return rows


def _sales_page(client, column, filter_code, query, size, page):
    body = client._post_text(LIST_PATH, {
        "sdtamt": str(size), "sdtpage": str(page), "sd": query, "ftd": filter_code,
        "selcolsort": column, "selcolsorttype": "2",
    })
    if (body or "").lstrip().startswith("err::"):
        raise DmsClientError("DMS rejected the sales query", "ERR_DMS_QUERY_SCOPE")
    rows = parse_sales_rows(body)
    if not rows and not str(body or "").lstrip().startswith("ndt::"):
        raise DmsClientError("DMS sales response shape changed", "ERR_DMS_TECHNICAL")
    return rows


def _field_page(client, field, column, filter_code, query, size, page):
    """Read the user's native results; filter before slicing, never truncate."""
    matches, seen = [], set()
    end = page * size
    for source_page in range(1, 21):
        rows = _sales_page(client, column, filter_code, query, 200, source_page)
        ids = {row["id"] for row in rows}
        if ids & seen or len(ids) != len(rows):
            raise DmsClientError("DMS sales pages repeat", "ERR_DMS_TECHNICAL")
        seen.update(ids)
        matches.extend(row for row in rows if query.casefold() in row[field].casefold())
        if len(matches) > end or len(rows) < 200:
            return matches[(page - 1) * size:end], len(matches) > end
    raise DmsClientError("DMS sales search exceeds the bounded scan", "ERR_DMS_TECHNICAL")


def _enabled_endpoint(user_id: str) -> Optional[dict]:
    """该操作员名下启用中的 `mrerp_dms` 端点。没有就 None（调用方回 `ERR_NO_CREDS`）。"""
    return resolve_dms_endpoint(user_id, None)


def _query_endpoint(user_id):
    if not can_query({"user_id": user_id}):
        raise DmsClientError("DMS query permission is disabled", "ERR_DMS_QUERY_FORBIDDEN")
    endpoint = _enabled_endpoint(user_id)
    if not endpoint:
        raise DmsClientError("No enabled employee endpoint", "ERR_NO_CREDS")
    return endpoint


def _metadata(snapshot):
    return {key: snapshot[key] for key in ("as_of", "completed_at", "generation", "stale")}


def query_parameters(kind="records", **params):
    """Only business conditions identify a cache; pages and binding do not."""
    if kind == "capabilities":
        return {"kind": kind}
    if kind == "records":
        return {"kind": kind,
                "field": params.get("field") if params.get("field") in SEARCH_COLUMNS else "booking_no",
                "status": params.get("status") if params.get("status") in STATUS_FILTERS else "active",
                "query": str(params.get("query") or "").strip()}
    if kind == "top":
        return {"kind": kind, **top_parameters(**{key: params.get(key, "")
                for key in ("group", "metric", "date_from", "date_to")})}
    raise DmsClientError("Unknown query kind", "ERR_DMS_TECHNICAL")


def query_dataset(params):
    conditions = query_parameters(**{key: value for key, value in params.items() if key != "_binding"})
    if conditions["kind"] == "capabilities":
        return CAPABILITIES_DATASET
    return "query." + hashlib.sha256(json.dumps(conditions, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()[:32]


def _read_query(endpoint, params):
    snapshot = store.read(endpoint, query_dataset(params), reader="sales")
    data = snapshot["data"]
    if not data.get("ok"):
        raise DmsClientError("DMS query snapshot is unavailable", data.get("error_code") or "ERR_DMS_SNAPSHOT_PENDING")
    if data.get("version") != SNAPSHOT_VERSION or data.get("params") != params:
        raise store.SnapshotUnavailable()
    return snapshot


def _failure(exc):
    if isinstance(exc, DmsClientError):
        return {"ok": False, "error_code": exc.error_code or "ERR_DMS_TECHNICAL"}
    logger.exception("DMS local query failed")
    return {"ok": False, "error_code": "ERR_UNEXPECTED"}


def fetch_sales_records(
    user_id: str,
    *,
    field: str = "booking_no",
    query: str = "",
    status: str = "active",
    limit: int = 10,
    page: int = 1,
) -> Dict[str, Any]:
    """Paginate one complete, explicitly requested native result without IO."""
    try:
        params = query_parameters("records", field=field, query=query, status=status)
        snapshot = _read_query(_query_endpoint(user_id), params)
        data = snapshot["data"]
        field, status, needle = params["field"], params["status"], params["query"]
        size = min(max(int(limit or 10), 1), 30)
        page_no = max(int(page or 1), 1)
        rows = data["rows"]
        end = page_no * size
        return {
            "ok": True,
            "kind": "sales_records",
            "source": "mrerp_dms_snapshot",
            "field": field,
            "query": needle,
            "status": status,
            "page": page_no,
            "limit": size,
            "has_more": len(rows) > end,
            "rows": rows[(page_no - 1) * size:end],
            **_metadata(snapshot),
        }
    except Exception as exc:
        return _failure(exc)


def _tag(source: str, tag: str, element_id: str) -> str:
    match = re.search(
        rf"<{tag}[^>]+id=[\"']{re.escape(element_id)}[\"'][^>]*>.*?</{tag}>",
        source,
        re.S | re.I,
    )
    return match.group(0) if match else ""


def _options(source: str, element_id: str) -> List[Dict[str, str]]:
    """某个 `<select>` 的选项。`selected` 原样带回，供看板回显当前筛选。"""
    block = _tag(source, "select", element_id)
    rows = []
    for value, attrs, label in re.findall(
        r"<option[^>]*value=[\"']([^\"']*)[\"']([^>]*)>(.*?)</option>",
        block,
        re.S | re.I,
    ):
        rows.append(
            {
                "id": value.strip(),
                "name": _text(label),
                "selected": "selected" in attrs.lower(),
            }
        )
    return rows


def _input_value(source: str, element_id: str) -> str:
    match = re.search(rf"<input[^>]+id=[\"']{re.escape(element_id)}[\"'][^>]*>", source, re.I)
    if not match:
        return ""
    value = re.search(r"value=[\"']([^\"']*)[\"']", match.group(0), re.I)
    return html.unescape(value.group(1)).strip() if value else ""


def _concrete_ids(rows: Iterable[Dict[str, str]]) -> str:
    """把选项 id 拼成 DMS 要的逗号串，**去掉占位的 `0`**（那是"全部"的哨兵）。"""
    values = [row["id"] for row in rows if row.get("id") and row.get("id") != "0"]
    return ",".join(dict.fromkeys(values))


def _dashboard_scope(home):
    user = _input_value(home, "idusers")
    if not user:
        raise DmsClientError("DMS dashboard session is unavailable", "ERR_DMS_AUTH")
    branches, teams = _options(home, "carmaxsellbranch"), _options(home, "carmaxsellteam")
    return user, branches, teams


def query_capabilities(user_id):
    """Only advertise dashboard scope captured with this employee's login."""
    try:
        snapshot = _read_query(_query_endpoint(user_id), {"kind": "capabilities"})
        return {"ok": True, "top_sales": bool(snapshot["data"]["capabilities"]["top_sales"]),
                **_metadata(snapshot)}
    except Exception as exc:
        return _failure(exc)


def _period_default() -> tuple:
    """默认期间：本月 1 号 → 今天，**佛历**（与 DMS 界面同一口径）。"""
    today = date.today()
    year_be = today.year + 543
    return f"01/{today.month:02d}/{year_be}", f"{today.day:02d}/{today.month:02d}/{year_be}"


def top_parameters(group="model", metric="quantity", date_from="", date_to=""):
    """Canonical native parameters; no page/limit in the snapshot identity."""
    default_from, default_to = _period_default()
    dates = []
    for raw in (date_from or default_from, date_to or default_to):
        try:
            day, month, year = [int(part) for part in str(raw).strip().split("/")]
            parsed = date(year - 543, month, day)
        except (ValueError, TypeError):
            raise DmsClientError("Invalid dashboard period", "ERR_DMS_TECHNICAL") from None
        dates.append(parsed)
    if dates[0] > dates[1]:
        raise DmsClientError("Dashboard period is reversed", "ERR_DMS_TECHNICAL")
    return {"group": group if group in TOP_GROUPS else "model",
            "metric": metric if metric in TOP_METRICS else "quantity",
            "date_from": f"{dates[0].day:02d}/{dates[0].month:02d}/{dates[0].year + 543}",
            "date_to": f"{dates[1].day:02d}/{dates[1].month:02d}/{dates[1].year + 543}"}


def top_dataset(params):
    return query_dataset({"kind": "top", **params})


def fetch_top_sales(
    user_id: str,
    *,
    group: str = "model",
    metric: str = "quantity",
    date_from: str = "",
    date_to: str = "",
    limit: int = 10,
    page: int = 1,
) -> Dict[str, Any]:
    """Read one explicitly requested native ranking; never request a refresh."""
    try:
        endpoint = _query_endpoint(user_id)
        params = top_parameters(group, metric, date_from, date_to)
        snapshot = _read_query(endpoint, {"kind": "top", **params})
        data = snapshot["data"]
        if not data.get("ok"):
            raise DmsClientError("DMS ranking snapshot unavailable", data.get("error_code") or "ERR_DMS_SNAPSHOT_PENDING")
        # A successful ranking captured its own employee dashboard scope. An
        # older capability cache must not veto that newer, authorized result.
        # Conversely, a later scope reduction invalidates an older ranking.
        try:
            sales = _read_query(endpoint, {"kind": "capabilities"})
        except store.SnapshotUnavailable as exc:
            if exc.error_code not in {"ERR_DMS_SNAPSHOT_PENDING", "ERR_DMS_SNAPSHOT_STALE"}:
                raise
            sales = None
        if sales and sales["as_of"] >= snapshot["as_of"]:
            if not sales["data"]["capabilities"]["top_sales"]:
                raise DmsClientError("This DMS login has no sales dashboard", "ERR_DMS_QUERY_SCOPE")
            if data.get("dashboard_scope") != sales["data"].get("dashboard_scope"):
                raise store.SnapshotUnavailable()
        size = min(max(int(limit or 10), 1), 30)
        page_no = max(int(page or 1), 1)
        end = page_no * size
        rows = data["rows"]
        return {
            "ok": True,
            "kind": "top_sales",
            "source": "mrerp_dms_snapshot",
            **params,
            "branches": data["branches"],
            "teams": data["teams"],
            "page": page_no,
            "limit": size,
            "total": len(rows),
            "has_more": len(rows) > end,
            "rows": [{"label": row["label"], "value": row["value"]} for row in rows[(page_no - 1) * size:end]],
            **_metadata(snapshot),
        }
    except Exception as exc:
        return _failure(exc)


__all__ = [
    "SEARCH_COLUMNS",
    "STATUS_FILTERS",
    "fetch_sales_records",
    "fetch_top_sales",
    "parse_sales_rows",
]
