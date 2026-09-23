# -*- coding: utf-8 -*-
"""商品单位的字段规则（纯逻辑）。

来源：ec2e5c957e6b ``services/products/units.py``（244 行）里不碰数据库的那部分：数值
列用 ``Decimal(str(v))`` 转换（避免浮点误差）、可写字段白名单、**建档与更新对"显式 None"
的不同处理**、以及"设新默认单位前要先清掉旧默认"这条单一默认规则。

旧侧的多单位是 POS 项目的形状（一个商品多个售卖单位，各自换算成基础单位、各自条码与售价），
本模块只搬字段判定；**表、行、外键与 RLS 属存储层**（下一批），所以这里返回"要写什么"，
由调用方决定怎么落。

口径（部署版逐条）：
· 可写字段 ``unit_name`` / ``factor_to_base`` / ``barcode`` / ``price`` / ``is_default_sell``；
· ``factor_to_base`` 与 ``price`` 是数值列：``Decimal(str(v))``，传了非数字会抛（旧侧同样抛）；
· **建档**只写入"值不是 None"的字段（显式 None = 这次不写）；
· **更新**写入"键存在且（值不是 None 或该字段可空）"的字段 —— 可空列（``barcode`` /
  ``price``）传显式 None 表示**清空**（箱码印错了要能删掉，不能只能改成别的码）；
· 更新时一个要写的字段都没有 → 返回 None，调用方照旧返回当前行（旧侧行为）；
· ``is_default_sell`` 为真时返回 ``clear_other_defaults=True``：调用方必须先把这个商品
  其它单位的默认标记清掉，保证单一默认（旧侧在插入/更新前调 ``_clear_default``）。
"""

from decimal import Decimal
from typing import Any, Dict, Optional

# 旧侧 `_UWRITABLE`
UNIT_WRITABLE = ("unit_name", "factor_to_base", "barcode", "price", "is_default_sell")
# 旧侧 `NULLABLE_UNIT_FIELDS`：显式 None 表示清空
NULLABLE_UNIT_FIELDS = {"barcode", "price"}
# 旧侧 `_NUMERIC`：以 Decimal(str(v)) 存
NUMERIC_UNIT_FIELDS = {"factor_to_base", "price"}


def coerce_number(value: Any) -> Optional[Decimal]:
    """数值列转换（旧侧 ``_num``）：None 就是 None，其余走 ``Decimal(str(v))``。"""
    return Decimal(str(value)) if value is not None else None


def _value(field: str, raw: Any) -> Any:
    return coerce_number(raw) if field in NUMERIC_UNIT_FIELDS else raw


def unit_insert_values(fields: Dict[str, Any]) -> Dict[str, Any]:
    """建档要写的列：白名单顺序、**跳过 None**。"""
    values: Dict[str, Any] = {}
    for field in UNIT_WRITABLE:
        if (fields or {}).get(field) is not None:
            values[field] = _value(field, fields[field])
    return values


def unit_update_values(fields: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """更新要写的列：给了键（含显式 None，仅限可空列）的字段；没有要写的返回 None。"""
    values: Dict[str, Any] = {}
    for field in UNIT_WRITABLE:
        if field not in (fields or {}):
            continue
        raw = fields[field]
        if raw is None and field not in NULLABLE_UNIT_FIELDS:
            continue
        values[field] = _value(field, raw) if raw is not None else None
    return values or None


def clears_other_defaults(fields: Dict[str, Any]) -> bool:
    """要不要先清掉这个商品其它单位的默认标记（单一默认）。"""
    return bool((fields or {}).get("is_default_sell"))
