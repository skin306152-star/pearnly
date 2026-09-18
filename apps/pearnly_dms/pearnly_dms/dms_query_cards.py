# -*- coding: utf-8 -*-
"""查资料那一族的 LINE 消息与看板卡片（纯函数，零副作用）。

照搬老产品 `services/line_dms/query_cards.py`（线上基准 `501589f0`，布局与配色未改）。

## 文案在语言包里

一个泰文字符都不留在这里（键 = `dms.card.query.*`）。老产品这份文件里**内联字面量比
常量还多**（选项标签、提示语、看板标题几十条），只按顶层常量搬会静默漏掉大半 ——
所以这份的包是**用 AST 从线上源码逐字抽出来**的（见 `docs/p4/48-query-family.md`）。

`ACT_*` 是 postback 载荷，**不是文案**，留在代码里且不能翻译。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from pearnly_dms import i18n

ACT_QUERY_TYPE = "query_type"
ACT_QUERY_DIMENSION = "query_dimension"
ACT_QUERY_STATUS = "query_status"
ACT_QUERY_TOP_GROUP = "query_top_group"
ACT_QUERY_TOP_METRIC = "query_top_metric"
ACT_QUERY_TOP_PERIOD = "query_top_period"
ACT_QUERY_PAGE = "query_page"

# 这一族的动作码（流程层用它识别 postback 归谁）
QUERY_ACTION_NAMES = (
    ACT_QUERY_TYPE,
    ACT_QUERY_DIMENSION,
    ACT_QUERY_STATUS,
    ACT_QUERY_TOP_GROUP,
    ACT_QUERY_TOP_METRIC,
    ACT_QUERY_TOP_PERIOD,
    ACT_QUERY_PAGE,
)


def __getattr__(name: str) -> Any:
    """让外部用 `query_cards.TXT_XXX` 取词（流程层要 TXT_DENIED 那几条）。"""
    if name.startswith(("TXT_", "LBL_", "NAV_", "UNIT_")):
        return _t(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _t(key: str, **kw) -> str:
    """取一条查资料文案。默认泰文（LINE 通道的语言）。"""
    return i18n.t(f"dms.card.query.{key}", lang="th", **kw)


def _data(action: str, **values: str) -> str:
    """postback 载荷：动作 + 非空参数（空值不写进去，免得把默认值当成选择）。"""
    return urlencode({"action": action, **{key: value for key, value in values.items() if value}})


def _item(label: str, action: str, **values: str) -> Dict[str, Any]:
    """quick reply 的一项。标签**截到 20 字**（LINE 的硬限），完整文本放 displayText。"""
    return {
        "type": "action",
        "action": {
            "type": "postback",
            "label": label[:20],
            "displayText": label,
            "data": _data(action, **values),
        },
    }


def _quick(text: str, items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {"type": "text", "text": text, "quickReply": {"items": list(items)}}


# ── 选类别 / 选维度 / 选状态 / 看板条件 ─────────────────────────────────────
def query_type_message() -> Dict[str, Any]:
    return _quick(
        _t("TXT_PICK_TYPE"),
        [
            _item(_t("TYPE_SALES"), ACT_QUERY_TYPE, kind="sales"),
            _item(_t("TYPE_INVENTORY"), ACT_QUERY_TYPE, kind="inventory"),
            _item(_t("TYPE_OTHER"), ACT_QUERY_TYPE, kind="other"),
        ],
    )


# 维度 → 文案键（顺序 = 按钮顺序，改顺序就是改产品）
_DIMENSIONS = (
    ("DIM_LATEST", "latest"),
    ("DIM_ADVISOR", "advisor"),
    ("DIM_VEHICLE", "vehicle"),
    ("DIM_CUSTOMER", "customer"),
    ("DIM_COLOR", "color"),
    ("DIM_BOOKING_DATE", "booking_date"),
    ("DIM_DELIVERY_DATE", "delivery_date"),
    ("DIM_FINANCE", "finance"),
    ("DIM_ALLOCATION", "allocation"),
    ("DIM_CONTRACT", "contract"),
    ("DIM_SALES_DOC_NO", "sales_doc_no"),
    ("DIM_ENGINE_NO", "engine_no"),
    ("DIM_TOP", "top"),
)


def sales_dimension_message() -> Dict[str, Any]:
    return _quick(
        _t("TXT_PICK_SALES"),
        [_item(_t(key), ACT_QUERY_DIMENSION, dimension=value) for key, value in _DIMENSIONS],
    )


# 要打字的维度 → 提示语键（其余走 ASK_ANY 兜底）
_PROMPTS = {
    "advisor": "ASK_ADVISOR",
    "vehicle": "ASK_VEHICLE",
    "customer": "ASK_CUSTOMER",
    "color": "ASK_COLOR",
    "booking_date": "ASK_BOOKING_DATE",
    "delivery_date": "ASK_DELIVERY_DATE",
    "sales_doc_no": "ASK_SALES_DOC_NO",
    "engine_no": "ASK_ENGINE_NO",
}


def input_prompt(field: str) -> Dict[str, Any]:
    return {"type": "text", "text": _t(_PROMPTS.get(field, "ASK_ANY"))}


# 状态维度 → (文案键, 状态码)；顺序 = 按钮顺序
_STATUS_CHOICES = {
    "finance": (
        ("ST_CASH", "cash"),
        ("ST_FINANCE_PENDING", "finance_pending"),
        ("ST_FINANCE_APPROVED", "finance_approved"),
        ("ST_MANAGER_PENDING", "manager_pending"),
    ),
    "allocation": (
        ("ST_UNALLOCATED", "unallocated"),
        ("ST_ALLOCATED", "allocated_no_contract"),
        ("ST_READY", "ready"),
    ),
    "contract": (
        ("ST_CONTRACT_OPENED", "contract_opened"),
        ("ST_CONTRACT_NOT_OPENED", "contract_not_opened"),
        ("ST_BOOKING", "booking"),
        ("ST_DRAFT", "draft"),
    ),
}


def status_message(kind: str) -> Dict[str, Any]:
    return _quick(
        _t("PICK_STATUS"),
        [
            _item(_t(key), ACT_QUERY_STATUS, status=value)
            for key, value in _STATUS_CHOICES.get(kind, ())
        ],
    )


def top_group_message() -> Dict[str, Any]:
    return _quick(
        _t("ASK_TOP_GROUP"),
        [
            _item(_t("GRP_MODEL"), ACT_QUERY_TOP_GROUP, group="model"),
            _item(_t("GRP_TYPE"), ACT_QUERY_TOP_GROUP, group="type"),
            _item(_t("GRP_SUBTYPE"), ACT_QUERY_TOP_GROUP, group="subtype"),
        ],
    )


def top_metric_message() -> Dict[str, Any]:
    return _quick(
        _t("ASK_TOP_METRIC"),
        [
            _item(_t("MET_QTY"), ACT_QUERY_TOP_METRIC, metric="quantity"),
            _item(_t("MET_AMOUNT"), ACT_QUERY_TOP_METRIC, metric="amount"),
        ],
    )


def top_period_message() -> Dict[str, Any]:
    """看板的期间 + 条数（**一次选完**，不逐步问 —— 这是原设计）。"""
    return _quick(
        _t("ASK_TOP_PERIOD"),
        [
            _item(_t("PERIOD_M_5"), ACT_QUERY_TOP_PERIOD, period="month", limit="5"),
            _item(_t("PERIOD_M_10"), ACT_QUERY_TOP_PERIOD, period="month", limit="10"),
            _item(_t("PERIOD_M_20"), ACT_QUERY_TOP_PERIOD, period="month", limit="20"),
            _item(_t("PERIOD_M_30"), ACT_QUERY_TOP_PERIOD, period="month", limit="30"),
            _item(_t("PERIOD_Y_10"), ACT_QUERY_TOP_PERIOD, period="year", limit="10"),
            _item(_t("PERIOD_CUSTOM"), ACT_QUERY_TOP_PERIOD, period="custom"),
        ],
    )


def top_custom_prompt() -> Dict[str, Any]:
    return {"type": "text", "text": _t("ASK_TOP_CUSTOM")}


# ── 看板卡片 ─────────────────────────────────────────────────────────────────
def _now_label() -> str:
    """"读到几点"的标签：**曼谷时间**的 `dd/mm/YYYY HH:MM`。

    ⚠️ 这里用公历年（`%Y`），不是佛历 —— 线上就是 `strftime("%d/%m/%Y %H:%M")`。
    别顺手改成佛历：同一张卡上"数据时间"和"期间"（佛历）本来就是两套口径。
    """
    return datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%d/%m/%Y %H:%M")


def _text_row(label: str, value: Any) -> Dict[str, Any]:
    return {
        "type": "box",
        "layout": "baseline",
        "spacing": "sm",
        "contents": [
            {"type": "text", "text": label, "size": "xxs", "color": "#8a8a8a", "flex": 2},
            {
                "type": "text",
                "text": str(value or "—"),
                "size": "xs",
                "color": "#222238",
                "wrap": True,
                "flex": 5,
            },
        ],
    }


def _record_bubble(row: dict, position: int) -> Dict[str, Any]:
    """一条销售记录一张小卡：单号 + 状态 + 六到七行明细。"""
    status = row.get("record_status") or _t("STATUS_UNKNOWN")
    rows = [
        _text_row(_t("LBL_CUSTOMER"), row.get("customer")),
        _text_row(_t("LBL_CAR"), " · ".join(filter(None, [row.get("vehicle"), row.get("color")]))),
        _text_row(_t("LBL_ADVISOR"), row.get("advisor")),
        _text_row(_t("LBL_DATE"), row.get("booking_date")),
        _text_row(_t("LBL_DELIVERY"), row.get("delivery_date")),
        _text_row(_t("LBL_FINANCE"), row.get("finance_status")),
        _text_row(_t("LBL_ALLOCATION"), row.get("allocation_status")),
    ]
    # 单号/机号都没有时**整行不出**（不留空槽）
    if row.get("sales_doc_no") or row.get("engine_no"):
        rows.append(
            _text_row(
                _t("LBL_DOCS"),
                " · ".join(filter(None, [row.get("sales_doc_no"), row.get("engine_no")])),
            )
        )
    return {
        "type": "bubble",
        "size": "micro",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "14px",
            "contents": [
                {
                    "type": "text",
                    "text": f"{position}. {row.get('booking_no') or '—'}",
                    "weight": "bold",
                    "size": "sm",
                    "wrap": True,
                    "color": "#392e66",
                },
                {
                    "type": "text",
                    "text": status,
                    "size": "xxs",
                    "color": "#7656d6",
                    "margin": "xs",
                    "wrap": True,
                },
                {"type": "separator", "margin": "md", "color": "#eeeaf8"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "spacing": "sm",
                    "margin": "md",
                    "contents": rows,
                },
            ],
        },
    }


def _navigation(result: dict, kind: str) -> List[Dict[str, Any]]:
    """翻页按钮：首页不出"上一页"；没有下一页就不出"下一页"；"重新读"永远有。"""
    actions = []
    page = int(result.get("page") or 1)
    if page > 1:
        actions.append(_item(_t("NAV_PREV"), ACT_QUERY_PAGE, kind=kind, direction="prev"))
    actions.append(_item(_t("NAV_REFRESH"), ACT_QUERY_PAGE, kind=kind, direction="refresh"))
    if result.get("has_more"):
        actions.append(_item(_t("NAV_NEXT"), ACT_QUERY_PAGE, kind=kind, direction="next"))
    return actions


def _button(item: Dict[str, Any]) -> Dict[str, Any]:
    return {"type": "button", "style": "link", "height": "sm", "action": item["action"]}


def sales_board(result: dict) -> Dict[str, Any]:
    """销售记录看板：一张汇总卡 + 每行一张小卡（LINE 轮播最多 12 张，所以只放前 11 行）。"""
    rows = result.get("rows") or []
    if not rows:
        # ⚠️ 前缀末尾那个空格**必须写在代码里**：线上那句以空格结尾（"…读完于 "），
        # 而语言包读取器对每条值做 `strip()`，行尾空格存不进 CSV —— 少了空格，
        # 前缀的最后一个词会和时间粘成一个词。既有守卫（语言包首尾空白）会直接报这个错。
        return _quick(_t("EMPTY_RECORDS_PREFIX") + " " + _now_label(),
                      _navigation(result, "records"))
    page = int(result.get("page") or 1)
    summary = {
        "type": "bubble",
        "size": "micro",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "16px",
            "backgroundColor": "#f5f1ff",
            "contents": [
                {"type": "text", "text": _t("CARD_TITLE"), "weight": "bold", "size": "md"},
                {
                    "type": "text",
                    "text": _t("CARD_NOTE"),
                    "size": "xxs",
                    "wrap": True,
                    "color": "#6d6680",
                    "margin": "sm",
                },
                _text_row(_t("LBL_PAGE"), page),
                _text_row(_t("LBL_COUNT"), len(rows)),
                _text_row(_t("LBL_READ_AT"), _now_label()),
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [_button(item) for item in _navigation(result, "records")],
        },
    }
    bubbles = [summary]
    offset = (page - 1) * int(result.get("limit") or 10)
    bubbles.extend(_record_bubble(row, offset + index) for index, row in enumerate(rows, 1))
    return {
        "type": "flex",
        "altText": _t("ALT_RECORDS", n=len(rows)),
        "contents": {"type": "carousel", "contents": bubbles[:12]},
    }


def top_sales_board(result: dict) -> Dict[str, Any]:
    """"卖得最好"看板：把 `label/value` 排成排行文本。金额按两位小数带千分位。"""
    metric = result.get("metric")
    unit = _t("UNIT_AMOUNT") if metric == "amount" else _t("UNIT_QTY")
    lines = []
    for index, row in enumerate(result.get("rows") or [], 1):
        value = row.get("value")
        if metric == "amount":
            try:
                value = f"{float(value):,.2f}"
            except (TypeError, ValueError):
                value = str(value or 0)
        lines.append(f"{index}. {row.get('label') or '—'}  {value} {unit}")
    text = "\n".join(lines) if lines else _t("EMPTY_TOP")
    navigation = _navigation(result, "top")
    bubble = {
        "type": "bubble",
        "body": {
            "type": "box",
            "layout": "vertical",
            "paddingAll": "18px",
            "contents": [
                {"type": "text", "text": _t("TITLE_TOP"), "weight": "bold", "size": "lg"},
                {
                    "type": "text",
                    "text": f"{result.get('date_from')} – {result.get('date_to')}",
                    "size": "xs",
                    "color": "#7656d6",
                    "margin": "xs",
                },
                {
                    "type": "text",
                    "text": text,
                    "size": "sm",
                    "wrap": True,
                    "margin": "lg",
                    "lineSpacing": "6px",
                },
                {
                    "type": "text",
                    # 同上：分隔空格放代码里
                    "text": _t("TOP_NOTE_PREFIX") + " " + _now_label(),
                    "size": "xxs",
                    "wrap": True,
                    "color": "#8a8a8a",
                    "margin": "lg",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "contents": [_button(item) for item in navigation],
        },
    }
    return {"type": "flex", "altText": _t("ALT_TOP"), "contents": bubble}
