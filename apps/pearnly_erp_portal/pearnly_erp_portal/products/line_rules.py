"""采购 / 销售每一行对物料与库存的预选（11-06 按行选物料与库存，评估 docs/work/2026-11-06-erp-line-item-stock-choice-评估.md 4.2）。

纯计算，不连库：调用方（`line_memory.suggest`）把这一行查到的记忆、认出的物料交进来，这里按固定顺序定一个结果。

| # | 条件 | 结果 |
|---|---|---|
| 1 | 人已经选了物料或定了入库与否 | 照人选的 |
| 2 | 识别补的「不可抵扣的增值税」行（采购） | 只记账 |
| 3 | 记忆命中（公司 × 方向 | 对方 | 品名），记住的物料还能用 | 记住的物料 + 入库与否 + 仓位 |
| 4 | 3 没中：这家公司这个方向下这个品名的全部记忆结论一致 | 用它 |
| 5 | 按条码 / 编码 / 品名认出已有物料，不是 Express 非库存品 | 这件物料 + 入库（销售出库） |
| 6 | 认出的是 Express 非库存品 | 这件物料 + 只记账 |
| 7 | 采购识别说这行是服务，或单据类型是「费用」 | 只记账 |
| 8 | 其余 | 新建并入库（**未定**：LINE 追问，网页照预选显示） |
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

GOODS, SERVICE = "goods", "service"
# 识别补的「不可抵扣的增值税」行的标记（同 `purchasing.recognition.NON_CLAIMABLE_VAT`，物料层不反向依赖采购域）
NON_CLAIMABLE_VAT = "non_claimable_vat"
USER, VAT, MEMORY, AGREED, MATCHED, EXPRESS_SERVICE, RECOGNISED, DEFAULT = (
    "user", "vat", "memory", "agreed", "matched", "express_service", "recognised", "default")


def _result(item_type, source, product=None, decided=True) -> dict:
    return {"item_type": item_type, "product": product or None, "source": source, "decided": decided}


def _remembered(row: Optional[Mapping[str, Any]], source) -> Optional[dict]:
    if not row or row.get("item_type") not in (GOODS, SERVICE):
        return None
    return _result(row["item_type"], source, row.get("product"))


def decide(*, chosen: Optional[Mapping[str, Any]] = None, non_claimable: bool = False,
           remembered: Optional[Mapping[str, Any]] = None, agreed: Optional[Mapping[str, Any]] = None,
           matched: Optional[Mapping[str, Any]] = None, recognised: Optional[str] = None,
           expense: bool = False) -> dict:
    """一行的预选 → ``{item_type, product, source, decided}``。

    ``chosen``：人选的 ``{product, item_type}``（有一项就算人选过）；``remembered`` / ``agreed``：调用方已核过物料还能用的记忆
    ``{product, item_type}``；``matched``：认出的物料 ``{product, express_non_stock}``；``recognised``：采购识别给的类型。"""
    if chosen and (chosen.get("product") or chosen.get("item_type") in (GOODS, SERVICE)):
        item_type = chosen.get("item_type") if chosen.get("item_type") in (GOODS, SERVICE) else GOODS
        return _result(item_type, USER, chosen.get("product"))
    if non_claimable:
        return _result(SERVICE, VAT)
    for row, source in ((remembered, MEMORY), (agreed, AGREED)):
        found = _remembered(row, source)
        if found:
            return found
    if matched and matched.get("product"):
        if matched.get("express_non_stock"):
            return _result(SERVICE, EXPRESS_SERVICE, matched["product"])
        return _result(GOODS, MATCHED, matched["product"])
    if recognised == SERVICE or expense:
        return _result(SERVICE, RECOGNISED)
    return _result(GOODS, DEFAULT, decided=False)


def tag(result: Mapping[str, Any], direction: str) -> str:
    """卡片上这一行的标签键：``in`` 入库 / ``out`` 出库 / ``none`` 只记账 / ``new_in`` 新建 · 入库 / ``new_out`` 新建 · 出库。"""
    if result.get("item_type") != GOODS:
        return "none"
    moved = "in" if direction == "purchase" else "out"
    return moved if result.get("product") else "new_" + moved


__all__ = ["GOODS", "SERVICE", "decide", "tag"]
