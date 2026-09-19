# -*- coding: utf-8 -*-
"""从操作员的 DMS 会话里**现读**销售/订车数据（列表 + 畅销看板）。

照搬老产品 `services/erp/mrerp_dms_sales_readback.py`（线上基准 `501589f0`，逻辑未改）。
它是"查资料"那一族的底座：解析 DMS **原生网页**返回的 HTML/JSON，不改状态语义。

两条要紧的性质：

· **每次调用都重新登录、读完就丢**：不留 cookie、不留结果 —— 查询是只读的一次性动作，
  留住会话只会让两个销售互相踢下线。
· **解析 fail closed**：认不出形状（`err::` 开头、行数不足、看板的 JSON 不是 JSON）
  一律**明确失败**，绝不回半份数据让人以为"就这么多"。

新平台的两处映射：
1. 端点解算走 `dms_ocr_service.resolve_dms_endpoint`（花名册里那个启用的 `mrerp_dms`），
   不再自己遍历老平台的端点表；
2. 首页那份 HTML 用 `adapter.transport().get(...)`（新适配器的 `transport()` 是公开方法）。
"""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import date
from typing import Any, Dict, Iterable, List, Optional

from pearnly_dms.contracts.client_base import DmsClientError
from pearnly_dms.intake.service import run_logged_in
from pearnly_dms.ocr.ocr_service import resolve_dms_endpoint

logger = logging.getLogger(__name__)

LIST_PATH = "drfcbc/component/showdata.php"
TOP_SALES_PATH = "home/component/carmaxsellchart.php"

# 搜索列（DMS 的 selcolsort）：值与 DMS 的表头顺序一一对应，**不能改**
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

_ROW_RE = re.compile(r'data-val="([^"]+)"', re.I)
_CELL_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)


def _text(raw: str) -> str:
    """单元格 → 纯文本：去标签、解实体、压空白；DMS 的空值占位 `-` 归一成空串。"""
    value = html.unescape(re.sub(r"<.*?>", "", raw or "")).strip()
    return "" if value == "-" else " ".join(value.split())


def parse_sales_rows(body: str) -> List[Dict[str, str]]:
    """解析 DMS 原生的订车/销售进度列表，**不改状态语义**。

    一行 = 一个 `data-val` 标记到下一个标记之间的一段；每段里的 `<p>` 是各列。
    认不出（列数不足、单号为空）就跳过这一行 —— 列表里出现半条比少一条更糟。
    """
    source = body or ""
    marks = list(_ROW_RE.finditer(source))
    rows: List[Dict[str, str]] = []
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(source)
        cells = [_text(value) for value in _CELL_RE.findall(source[mark.end() : end])]
        if len(cells) < 13 or not cells[2]:
            continue
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


def _enabled_endpoint(user_id: str) -> Optional[dict]:
    """该操作员名下启用中的 `mrerp_dms` 端点。没有就 None（调用方回 `ERR_NO_CREDS`）。"""
    return resolve_dms_endpoint(user_id, None)


def fetch_sales_records(
    user_id: str,
    *,
    field: str = "booking_no",
    query: str = "",
    status: str = "active",
    limit: int = 10,
    page: int = 1,
) -> Dict[str, Any]:
    """登录并读取**一页**实时数据。调用之间不留 cookie、不留结果。"""
    try:
        endpoint = _enabled_endpoint(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("DMS sales endpoint lookup failed")
        return {"ok": False, "error_code": "ERR_UNEXPECTED"}
    if not endpoint:
        return {"ok": False, "error_code": "ERR_NO_CREDS"}
    column = SEARCH_COLUMNS.get(field, SEARCH_COLUMNS["booking_no"])
    filter_code = STATUS_FILTERS.get(status, STATUS_FILTERS["active"])
    size = min(max(int(limit or 10), 1), 30)
    page_no = max(int(page or 1), 1)

    def _read(client, _adapter):
        body = client._post_text(
            LIST_PATH,
            {
                "sdtamt": str(size),
                "sdtpage": str(page_no),
                "sd": str(query or "").strip(),
                "ftd": filter_code,
                "selcolsort": column,
                "selcolsorttype": "2",
            },
        )
        if (body or "").lstrip().startswith("err::"):
            raise DmsClientError(body[:200], "ERR_DMS_TECHNICAL")
        rows = parse_sales_rows(body)
        return {
            "ok": True,
            "kind": "sales_records",
            "source": "mrerp_dms_live",
            "field": field,
            "query": str(query or "").strip(),
            "status": status,
            "page": page_no,
            "limit": size,
            # 拿满一页就当还有下一页（下一页可能为空，由调用方按空结果收尾）
            "has_more": len(rows) == size,
            "rows": rows,
        }

    return run_logged_in(endpoint, _read)


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


def _period_default() -> tuple:
    """默认期间：本月 1 号 → 今天，**佛历**（与 DMS 界面同一口径）。"""
    today = date.today()
    year_be = today.year + 543
    return f"01/{today.month:02d}/{year_be}", f"{today.day:02d}/{today.month:02d}/{year_be}"


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
    """读 DMS 原生的"卖得最好"看板（该登录能看见的所有分店/team）。"""
    try:
        endpoint = _enabled_endpoint(user_id)
    except Exception:  # noqa: BLE001
        logger.exception("DMS highest-sales endpoint lookup failed")
        return {"ok": False, "error_code": "ERR_UNEXPECTED"}
    if not endpoint:
        return {"ok": False, "error_code": "ERR_NO_CREDS"}
    group_code = TOP_GROUPS.get(group, TOP_GROUPS["model"])
    metric_code = TOP_METRICS.get(metric, TOP_METRICS["quantity"])
    size = min(max(int(limit or 10), 1), 30)
    page_no = max(int(page or 1), 1)
    default_from, default_to = _period_default()

    def _read(client, adapter):
        # 看板的筛选条件（用户 id、分店、team）只能从首页那份 HTML 里读回来
        home = adapter.transport().get(adapter.base_url + "home/home.php").text
        user_id_value = _input_value(home, "idusers")
        branches = _options(home, "carmaxsellbranch")
        teams = _options(home, "carmaxsellteam")
        branch_ids = _concrete_ids(branches)
        team_ids = _concrete_ids(teams)
        if not user_id_value or not branch_ids or not team_ids:
            raise DmsClientError("highest-sales dashboard filters unavailable")
        payload = {
            "idusers": user_id_value,
            "carmaxselltypedata": group_code,
            "carmaxsellbranch": branch_ids,
            "carmaxsellteam": team_ids,
            "txtcarmaxselldate_bg": date_from or default_from,
            "txtcarmaxselldate_to": date_to or default_to,
            "carmaxsellqnt": metric_code,
            "carmaxsellshow": str(size),
            "carmaxsellpage": str(page_no),
        }
        # 两次 POST：一次拿总数、一次拿本页（DMS 原生的分页方式）
        count_body = client._post_text(TOP_SALES_PATH, {**payload, "status": "all"})
        if (count_body or "").lstrip().startswith("err::"):
            raise DmsClientError(count_body[:200])
        page_body = client._post_text(TOP_SALES_PATH, {**payload, "status": "page"})
        try:
            labels, _ids, values = json.loads(page_body)
        except (TypeError, ValueError) as exc:
            raise DmsClientError("highest-sales response is not valid JSON") from exc
        total = int(str(count_body).strip() or 0)
        rows = [
            {"label": str(label), "value": value}
            for label, value in zip(labels or [], values or [])
        ]
        return {
            "ok": True,
            "kind": "top_sales",
            "source": "mrerp_dms_live",
            "group": group,
            "metric": metric,
            "date_from": payload["txtcarmaxselldate_bg"],
            "date_to": payload["txtcarmaxselldate_to"],
            "branches": [row["name"] for row in branches if row.get("id") != "0"],
            "teams": [row["name"] for row in teams if row.get("id") != "0"],
            "page": page_no,
            "limit": size,
            "total": total,
            "has_more": page_no * size < total,
            "rows": rows,
        }

    return run_logged_in(endpoint, _read)


__all__ = [
    "SEARCH_COLUMNS",
    "STATUS_FILTERS",
    "fetch_sales_records",
    "fetch_top_sales",
    "parse_sales_rows",
]
