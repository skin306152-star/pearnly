"""商品单的入账方式按公司选一次（数字会计员第 3 期，用户 2026-10-16「按公司问一次记住」）。纯函数，不连库、不依赖框架：
推送服务（准备、要选的项、自动推前的判定）与离线回放（`scripts/da-replay.py`）共用。

永续 / 混合库存账套（记账画像 `posting_profile` 判成 ``manual_review``）里没声明入账类型的商品单，映射器照「铁律」交人
（`posting_needs_review:*`，不静默按周期制落）。这家公司选过「走库存 / 记费用」后，同样情形的单按选定值当作声明了入账类型
（``stock`` / ``service``，与录入第一步每批的选择同一个值），之后照常判；没选过的照旧交人。

做法同转账凭条 / 预扣税凭证「选一次记住」（`contracts.document_policy`）：同一份存储（工作空间的 ``document_policy``，键
``goods_posting|<公司身份>``）、同一种问法（复核页推送原因 `posting_needs_review` 旁两个按钮，CC 同一个工具）。

- `blocked`：这张单推往这份账套时会不会因为「永续 / 混合库存、没声明」交人 —— 与 `mapper.build_express_payload` /
  `sales_mapper.build_express_sales_payload` 的判据逐条一致（进项只看商品车道：费用单不动库存，不受此闸约束；车道用映射器
  同一个 `field_rules.lane_verdict`，含这家选过的「小票 / 简易税票抵不抵进项」）；
- `resolve`：这次实际用的入账类型与来源（``declared`` 本票 / 本批声明的、``company_choice`` 这家公司选过的、空 = 没有）；
- `company_key`：账套目录一行 → 公司身份（同 `companies.identity.company_identity` 的 ``key``；push 域不导入 companies）。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Mapping, Optional, Tuple

from pearnly_core.tax_documents.amounts import valid_tax_id

from pearnly_cowork.contracts.document_policy import GOODS_POSTING_VALUES
from pearnly_core.express_rules.contracts.push import item_posting_kinds, normalize_posting_kind
from pearnly_core.express_rules.push.account_rules import stock_lane_enabled
from pearnly_core.express_rules.push.choices import digits
from pearnly_core.express_rules.push.field_rules import lane_verdict
from pearnly_core.express_rules.push.posting_profile import ESCALATE_REASON_PREFIX, profile_from_config
from pearnly_core.express_rules.targets.identity import account_set_company

DECLARED, COMPANY_CHOICE = "declared", "company_choice"
# 推送停在这一步的原因码前缀（映射器 `PostingProfile.escalate_reason`）：复核页据此出「走库存 / 记费用」两个按钮
NEEDED_PREFIX = ESCALATE_REASON_PREFIX
IDENTITY_MAX = 200


def company_key(connection: Any, row: Any) -> str:
    """Express 账套目录一行 → 公司身份：有 13 位有效税号 ``tax:{税号}``（各分店、各年度一家），否则
    ``name:express:{连接}:{名称键}``；都没有回空串（不记、照旧交人）。与 `companies.identity.company_identity` 同一规则。"""
    attributes = row.get("attributes") if isinstance(row, Mapping) else None
    attributes = attributes if isinstance(attributes, Mapping) else {}
    connection = " ".join(str(connection or "").split())
    if not connection:
        return ""
    tax = digits(attributes.get("tax_id"))
    if len(tax) == 13 and valid_tax_id(tax):
        return f"tax:{tax}"
    name_key = account_set_company(row)
    return f"name:express:{connection}:{name_key}"[:IDENTITY_MAX] if name_key else ""


def blocked(fields: Mapping[str, Any], *, direction: str, config: Mapping[str, Any], posting_kind: Any,
            today: dt.date, vat_choice: Any = None) -> bool:
    """没声明入账类型的商品单推往永续 / 混合库存账套会交人（映射器同一判据）：本票 / 本批声明了、明细行逐行声明了、
    画像不拦（非库存、指纹没报）、进项的费用单（不动库存）都不算。``vat_choice``：这家选过的小票 / 简易税票抵不抵进项
    （`choices.resolve` 的 ``applied["vat"]``；选了抵进项的小票走商品车道，映射器同样会交人）。"""
    fields = fields if isinstance(fields, Mapping) else {}
    if normalize_posting_kind(posting_kind) or item_posting_kinds(fields.get("items")) is not None:
        return False
    config = config if isinstance(config, Mapping) else {}
    if not profile_from_config(dict(config), stock_enabled=stock_lane_enabled(dict(config))).blocks_auto_posting():
        return False
    if direction == "sales":
        return True
    is_expense, _src = lane_verdict(dict(fields), today=today, vat_choice=vat_choice)
    return not is_expense


def resolve(fields: Mapping[str, Any], *, direction: str, config: Mapping[str, Any], posting_kind: Any,
            chosen: Any, today: dt.date, vat_choice: Any = None) -> Tuple[Optional[str], str]:
    """这次用的入账类型与来源：声明了的照声明（``declared``）；会交人、而这家公司选过的用选的值（``company_choice``）；
    其余 ``(None, "")``（照旧：不拦的按画像，拦的交人）。``vat_choice`` 同 `blocked`。"""
    explicit = normalize_posting_kind(posting_kind)
    if explicit:
        return explicit, DECLARED
    value = str(chosen or "").strip()
    if value in GOODS_POSTING_VALUES and blocked(fields, direction=direction, config=config, posting_kind=None,
                                                 today=today, vat_choice=vat_choice):
        return value, COMPANY_CHOICE
    return None, ""


__all__ = ["COMPANY_CHOICE", "DECLARED", "NEEDED_PREFIX", "blocked", "company_key", "resolve"]
