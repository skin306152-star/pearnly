# -*- coding: utf-8 -*-
"""供应商过账档案的规则层（纯逻辑，部署版口径）。

来源：ec2e5c957e6b

· ``routes/supplier_posting_routes.py``（98 行）：税号清洗（非法 422 ``purchase.tax_id_invalid``）、
  付款方式/品项类型两组值域的 422（``purchase.line_invalid`` + detail 机器码）、
  ``exclude_unset`` 的**部分更新**语义（只有给了的字段才动）；
· ``services/purchase/supplier_posting.py``：``upsert_profile`` 的 CASE/COALESCE 与
  "``user_rule`` 不被 ``correction`` 覆盖"、``delete_profile`` 的**幂等**返回值；
· 值域同源 ``services/ocr_history/posting_manual.py`` 的 ``_PAYMENT_VALUES`` /
  ``_ITEM_TYPE_VALUES``（旧侧路由直接 import 那里，不另抄一份；本仓同样只此一处）。

保留的部署版口径：

· 值域：付款方式 ``cash | credit``，品项类型 ``goods | expense``；**None = 这一次不动**。
  旧侧路由的 ``_PAYMENT_OR_UNSET`` 只把 None 当"未给"，空串在**用户入口**上非法（422），
  但内部纠错回流可以写空串 —— 见 ``input_error(..., allow_empty=)``；
· 税号：剥非数字后**恰好 13 位**，否则用户入口报错；删除路径**不校验**（幂等 no-op）；
· 首次建档：未给的字段落**空串**（旧 SQL 的 ``payment_val = default_payment if ... else ""``）；
· 已有档案：``user_rule`` 行不被 ``correction`` 覆盖（**整行不动**，连 source 都不改）；
  ``user_rule`` 可覆盖任意来源；其余只写本次给了值的字段，source 随本次来源；
· 删除幂等：没有这一行时返回 ``deleted=False``，不是失败。

这是纯模块：不碰 Frappe、数据库、时钟。存储层（``masters/push_inputs.py``）拿这里的判定去写
``Cowork Supplier Posting Profile``；差分测试把旧侧 ``upsert_profile`` 捕获到的 SQL 放进真实
SQL 引擎里跑，比对本模块算出来的每一列。
"""

from typing import Any, Dict, Optional

from pearnly_cowork.masters.field_clean import clean_tax_id

# 旧侧 routes/supplier_posting_routes.py 的 _PAYMENT_VALUES / _ITEM_TYPE_VALUES，
# 其值与其来源 services/ocr_history/posting_manual.py 逐字相同。
PAYMENT_VALUES = frozenset({"cash", "credit"})
ITEM_TYPE_VALUES = frozenset({"goods", "expense"})
SOURCES = ("user_rule", "correction")
DEFAULT_SOURCE = "correction"
FIELDS = ("default_payment", "default_item_type")
VALUE_DOMAINS = {"default_payment": PAYMENT_VALUES, "default_item_type": ITEM_TYPE_VALUES}

# 旧侧给调用方的机器码：税号非法与行值非法是两种错，细节靠 detail 区分。
ERR_TAX_ID = "purchase.tax_id_invalid"
ERR_LINE = "purchase.line_invalid"
ERR_SOURCE = "purchase.source_invalid"
DETAILS = {"default_payment": "default_payment_invalid",
    "default_item_type": "default_item_type_invalid"}

# 旧侧列表口的 Query(ge=1, le=500)。
MAX_LIMIT = 500


def clean_seller_tax_id(raw: Any) -> str:
    """档案锚（卖方税号）：合法返回 13 位数字串，否则空串。"""
    return clean_tax_id(raw)


def field_error(field: str, value: Any, *, allow_empty: bool = False) -> Optional[str]:
    """单个字段的值域判定：合法返回 None，否则给旧侧同款的 detail 机器码。"""
    if value is None:
        return None
    if allow_empty and value == "":
        return None
    if value not in VALUE_DOMAINS[field]:
        return DETAILS[field]
    return None


def input_error(source: str, incoming: Dict[str, Any], *, allow_empty: bool = False) -> Optional[str]:
    """用户入口的整体判定：值非法 → ``ERR_LINE``，来源非法 → ``ERR_SOURCE``，可以写 → None。

    旧侧路由先判值再写库（422 不带半截写入），本仓同样先判后写。
    """
    if source not in SOURCES:
        return ERR_SOURCE
    for field in FIELDS:
        if field in (incoming or {}) and field_error(field, incoming[field], allow_empty=allow_empty):
            return ERR_LINE
    return None


def merge(existing: Optional[Dict[str, Any]], incoming: Dict[str, Any], source: str) -> Dict[str, Any]:
    """既有档案 + 本次改动 → 该写什么（旧侧 SQL 的语义）。

    返回 ``{"preserved_user_rule": bool, "source": str, "fields": {字段: 值}}``：

    · ``existing`` 为 None（首次建档）：未给的字段落空串；
    · 已有 ``user_rule`` 行 + 本次 ``correction``：``preserved_user_rule=True``、``fields`` 为空
      （旧 SQL 的 ``WHERE ... OR EXCLUDED.source = 'user_rule'`` 让整行不更新，source 也不改）；
    · 其余：只写本次**非 None** 的字段，source 随本次来源。
    """
    values = {field: (incoming or {}).get(field) for field in FIELDS}
    if existing is None:
        return {"preserved_user_rule": False, "source": source, "fields":
            {field: (values[field] if values[field] is not None else "") for field in FIELDS}}
    if existing.get("source") == "user_rule" and source != "user_rule":
        return {"preserved_user_rule": True, "source": existing.get("source"), "fields": {}}
    return {"preserved_user_rule": False, "source": source,
        "fields": {field: values[field] for field in FIELDS if values[field] is not None}}


def has_effect(profile: Optional[Dict[str, Any]]) -> bool:
    """这条档案还有没有"默认过账"的效果（两个值都空 = 效果已经清掉）。"""
    if not profile:
        return False
    return any(str(profile.get(field) or "").strip() for field in FIELDS)


def cleared() -> Dict[str, str]:
    """清掉效果要写的值（本仓的档案行可审计、不可删，见 ``masters/push_inputs.py``）。"""
    return {field: "" for field in FIELDS}
