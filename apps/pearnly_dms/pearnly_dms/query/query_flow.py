# -*- coding: utf-8 -*-
"""查资料那一族的流程：选条件 → 现读 DMS → 出看板（受权限门控）。

照搬老产品 `services/line_dms/query_flow.py`（线上基准 `501589f0`，逻辑未改），
async → 同步。

## 权限是**二次校验**的（这条不能省）

每一步动作、以及**后台任务真正发结果之前**都会再查一次"这个人还有没有查询权"：

· 动作入口查一次 —— 没权限的人连菜单都进不去（回"还没有查询权限"）；
· 后台任务发结果**之前再查一次** —— 因为读 DMS 要几秒，这几秒里权限可能被收回。
  少了这一次，刚被收权的人还会收到一份数据。

**判据本身在 `dms_query_access`**（花名册那一栏），这里只负责在正确的位置调用它。

## 两步都丢后台

读 DMS 要重新登录（几秒到十几秒），所以 `_begin_records` / `_begin_top` 只做两件事：
把条件存进会话（`query_results`）→ 丢后台任务。webhook 立刻返回。
翻页、重读都是同一套：**条件进会话、任务读参数**，不靠内存里的状态。

新平台的两处映射：后台调度走 `dms_out.enqueue`；多 OA 的 `binding_guard` 去掉
（一个站点一条 OA，绑定随事件带进来）。
"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from pearnly_core import line_session as store
from pearnly_dms.line import cards as cards
from pearnly_dms.query import query_access as query_access
from pearnly_dms.query import query_cards as query_cards
from pearnly_dms.query import sales_readback as sales_readback
from pearnly_dms.line.out import enqueue as _enqueue
from pearnly_dms.line.out import push_text as _push
from pearnly_dms.line.out import reply_text as _reply
from pearnly_dms.line.out import send as _send
from pearnly_dms.line.out import start_loading as _start_loading

logger = logging.getLogger(__name__)

QUERY_ACTIONS = frozenset({cards.ACT_MENU_QUERY, *query_cards.QUERY_ACTION_NAMES})

# 需要用户**打一段字**的维度（其余维度是用按钮选的）
_TEXT_FIELDS = frozenset(
    {
        "advisor",
        "vehicle",
        "customer",
        "color",
        "booking_date",
        "delivery_date",
        "sales_doc_no",
        "engine_no",
    }
)
# 自定义期间：`01/08/2569-31/08/2569 15`（日期可带空格，末尾是条数）
_DATE_RE = re.compile(r"^(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})\s+(\d{1,2})$")


def _can_query(binding: dict) -> bool:
    return bool(query_access.can_query(binding))


def _denied(reply_token: str) -> None:
    _reply(reply_token, query_cards.TXT_DENIED)


# ── 入口 ─────────────────────────────────────────────────────────────────────
def open_query(binding: dict, line_user_id: str, reply_token: str) -> None:
    """进查询：先过权限门 → 落 `query_menu` 态 → 发"选类别"卡。"""
    if not _can_query(binding):
        _denied(reply_token)
        return
    store.set_session(line_user_id, "query_menu", {})
    _send(line_user_id, query_cards.query_type_message(), reply_token)


def handle_postback(
    binding: dict,
    line_user_id: str,
    reply_token: str,
    action: str,
    values: dict,
    sess: Optional[dict],
) -> None:
    if action == cards.ACT_MENU_QUERY:
        open_query(binding, line_user_id, reply_token)
        return
    # 除"进菜单"之外，每个动作都要再过一次权限门
    if not _can_query(binding):
        _denied(reply_token)
        return
    if action == query_cards.ACT_QUERY_TYPE:
        _pick_type(binding, line_user_id, reply_token, values.get("kind") or "")
    elif action == query_cards.ACT_QUERY_DIMENSION:
        _pick_dimension(binding, line_user_id, reply_token, values.get("dimension") or "")
    elif action == query_cards.ACT_QUERY_STATUS:
        _begin_records(
            binding, line_user_id, status=values.get("status") or "active", reply_token=reply_token
        )
    elif action == query_cards.ACT_QUERY_TOP_GROUP:
        _pick_top_group(binding, line_user_id, reply_token, values.get("group") or "model")
    elif action == query_cards.ACT_QUERY_TOP_METRIC:
        _pick_top_metric(binding, line_user_id, reply_token, sess, values.get("metric") or "quantity")
    elif action == query_cards.ACT_QUERY_TOP_PERIOD:
        _pick_top_period(binding, line_user_id, reply_token, sess, values)
    elif action == query_cards.ACT_QUERY_PAGE:
        _paginate(binding, line_user_id, reply_token, sess, values)


def handle_text(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], text: str
) -> bool:
    """只接管两个"等用户打字"的态；其它态返 False 交上层。"""
    state = (sess or {}).get("state")
    if state not in {"query_sales_input", "query_top_custom"}:
        return False
    if not _can_query(binding):
        _denied(reply_token)
        return True
    value = (text or "").strip()
    if state == "query_sales_input":
        field = str(((sess or {}).get("payload") or {}).get("field") or "")
        # 空输入 / 认不出的维度 → 原样重问（不拿空串去查一整页）
        if not value or field not in _TEXT_FIELDS:
            _send(line_user_id, query_cards.input_prompt(field), reply_token)
            return True
        _begin_records(
            binding,
            line_user_id,
            field=field,
            query=value,
            status="active",
            reply_token=reply_token,
        )
        return True

    match = _DATE_RE.match(value)
    if not match or not 1 <= int(match.group(3)) <= 30:
        _send(line_user_id, query_cards.top_custom_prompt(), reply_token)
        return True
    payload = (sess or {}).get("payload") or {}
    _begin_top(
        binding,
        line_user_id,
        group=str(payload.get("group") or "model"),
        metric=str(payload.get("metric") or "quantity"),
        date_from=match.group(1),
        date_to=match.group(2),
        limit=int(match.group(3)),
        reply_token=reply_token,
    )
    return True


# ── 一步步选条件 ─────────────────────────────────────────────────────────────
def _pick_type(binding: dict, line_user_id: str, reply_token: str, kind: str) -> None:
    """类别：目前只有"销售记录"能用（另两类是占位，回"这一版还没开"）。"""
    if kind != "sales":
        _reply(reply_token, query_cards.TXT_COMING_SOON)
        return
    store.set_session(line_user_id, "query_sales", {})
    _send(line_user_id, query_cards.sales_dimension_message(), reply_token)


def _pick_dimension(binding: dict, line_user_id: str, reply_token: str, dimension: str) -> None:
    """维度分四路：最新一览 / 要打字 / 要选状态 / 畅销榜。"""
    if dimension == "latest":
        _begin_records(
            binding, line_user_id, field="booking_date", status="active", reply_token=reply_token
        )
        return
    if dimension in _TEXT_FIELDS:
        store.set_session(line_user_id, "query_sales_input", {"field": dimension})
        _send(line_user_id, query_cards.input_prompt(dimension), reply_token)
        return
    if dimension in {"finance", "allocation", "contract"}:
        _send(line_user_id, query_cards.status_message(dimension), reply_token)
        return
    if dimension == "top":
        store.set_session(line_user_id, "query_top_group", {})
        _send(line_user_id, query_cards.top_group_message(), reply_token)


def _pick_top_group(binding: dict, line_user_id: str, reply_token: str, group: str) -> None:
    """分组：认不出的按"车型"（**fail 到默认值，不是报错** —— 按钮给的只可能是这三类）。"""
    if group not in sales_readback.TOP_GROUPS:
        group = "model"
    store.set_session(line_user_id, "query_top_metric", {"group": group})
    _send(line_user_id, query_cards.top_metric_message(), reply_token)


def _pick_top_metric(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], metric: str
) -> None:
    payload = (sess or {}).get("payload") or {}
    group = str(payload.get("group") or "model")
    if metric not in sales_readback.TOP_METRICS:
        metric = "quantity"
    store.set_session(line_user_id, "query_top_period", {"group": group, "metric": metric})
    _send(line_user_id, query_cards.top_period_message(), reply_token)


def _pick_top_period(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], values: dict
) -> None:
    """期间：本月 / 本年 → 直接查（**佛历**日期）；自定义 → 落态等用户打字。"""
    payload = (sess or {}).get("payload") or {}
    group = str(payload.get("group") or "model")
    metric = str(payload.get("metric") or "quantity")
    period = values.get("period") or "month"
    if period == "custom":
        store.set_session(line_user_id, "query_top_custom", {"group": group, "metric": metric})
        _send(line_user_id, query_cards.top_custom_prompt(), reply_token)
        return
    today = date.today()
    year_be = today.year + 543
    date_from = f"01/{today.month:02d}/{year_be}" if period == "month" else f"01/01/{year_be}"
    date_to = f"{today.day:02d}/{today.month:02d}/{year_be}"
    _begin_top(
        binding,
        line_user_id,
        group=group,
        metric=metric,
        date_from=date_from,
        date_to=date_to,
        limit=min(max(int(values.get("limit") or 10), 1), 30),
        reply_token=reply_token,
    )


# ── 两条"落地成任务"的路 ─────────────────────────────────────────────────────
def _begin_records(
    binding: dict,
    line_user_id: str,
    *,
    field: str = "booking_no",
    query: str = "",
    status: str = "active",
    limit: int = 10,
    page: int = 1,
    reply_token: str = "",
) -> None:
    """条件进会话 + 丢后台。⚠️ 记录的页大小上限是 **10**（看板才是 30）。"""
    params = {
        "query_kind": "records",
        "field": field,
        "query": query,
        "status": status,
        "limit": min(max(int(limit), 1), 10),
        "page": max(int(page), 1),
    }
    store.set_session(line_user_id, "query_results", params)
    _start_loading(line_user_id)
    _enqueue("records", _run_records, binding, line_user_id, params)


def _run_records(binding: dict, line_user_id: str, params: dict) -> None:
    """后台任务：读一页记录 → 出看板。**发结果前再查一次权限。**"""
    if not _can_query(binding):
        return
    result = sales_readback.fetch_sales_records(
        str(binding["user_id"]),
        field=params["field"],
        query=params["query"],
        status=params["status"],
        limit=params["limit"],
        page=params["page"],
    )
    if not _can_query(binding):
        _push(line_user_id, query_cards.TXT_DENIED)
        return
    if not result.get("ok"):
        _push(line_user_id, _failure_text(result))
        return
    _send(line_user_id, query_cards.sales_board(result))


def _begin_top(
    binding: dict,
    line_user_id: str,
    *,
    group: str,
    metric: str,
    date_from: str,
    date_to: str,
    limit: int,
    page: int = 1,
    reply_token: str = "",
) -> None:
    params = {
        "query_kind": "top",
        "group": group,
        "metric": metric,
        "date_from": date_from,
        "date_to": date_to,
        "limit": min(max(int(limit), 1), 30),
        "page": max(int(page), 1),
    }
    store.set_session(line_user_id, "query_results", params)
    _start_loading(line_user_id)
    _enqueue("top", _run_top, binding, line_user_id, params)


def _run_top(binding: dict, line_user_id: str, params: dict) -> None:
    """后台任务：读畅销榜 → 出看板。**发结果前再查一次权限。**"""
    if not _can_query(binding):
        return
    result = sales_readback.fetch_top_sales(
        str(binding["user_id"]),
        group=params["group"],
        metric=params["metric"],
        date_from=params["date_from"],
        date_to=params["date_to"],
        limit=params["limit"],
        page=params["page"],
    )
    if not _can_query(binding):
        _push(line_user_id, query_cards.TXT_DENIED)
        return
    if not result.get("ok"):
        _push(line_user_id, _failure_text(result))
        return
    _send(line_user_id, query_cards.top_sales_board(result))


def _failure_text(result: dict) -> str:
    """读失败怎么说：没账号就直说没账号，其余用 DMS 那句友好文案，兜底"读不到，请重试"。"""
    if result.get("error_code") == "ERR_NO_CREDS":
        return query_cards.TXT_NO_ENDPOINT
    return (result.get("error_friendly") or {}).get("th") or query_cards.TXT_QUERY_FAILED


# ── 翻页 / 重读 ──────────────────────────────────────────────────────────────
def _paginate(
    binding: dict, line_user_id: str, reply_token: str, sess: Optional[dict], values: dict
) -> None:
    """翻页：会话里那份条件就是唯一依据（过期/换过类别 → 让用户重来）。"""
    if (sess or {}).get("state") != "query_results":
        _reply(reply_token, query_cards.TXT_EXPIRED)
        return
    params = dict((sess or {}).get("payload") or {})
    # 卡片上的按钮带 kind：**必须和会话里那份对得上** —— 否则就是翻上一批结果的页
    if params.get("query_kind") != values.get("kind"):
        _reply(reply_token, query_cards.TXT_EXPIRED)
        return
    direction = values.get("direction")
    page = max(int(params.get("page") or 1), 1)
    params["page"] = page - 1 if direction == "prev" and page > 1 else page
    if direction == "next":
        params["page"] = page + 1
    if params["query_kind"] == "records":
        _begin_records(
            binding,
            line_user_id,
            reply_token=reply_token,
            **{key: params[key] for key in ("field", "query", "status", "limit", "page")},
        )
    else:
        _begin_top(
            binding,
            line_user_id,
            reply_token=reply_token,
            **{
                key: params[key]
                for key in ("group", "metric", "date_from", "date_to", "limit", "page")
            },
        )


__all__ = ["QUERY_ACTIONS", "handle_postback", "handle_text", "open_query"]
