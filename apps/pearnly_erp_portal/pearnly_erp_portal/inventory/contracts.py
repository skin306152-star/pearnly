"""库存的纯规则：滚动加权平均成本、单位换算、差异判据。

不依赖 Frappe、IO 或上层流程。规则照旧线上 `services/stockcard/rolling.py` 与
`services/inventory/ledger.py` 的口径搬过来，逐条对齐：

- 结存恒记数量（含负库存）；**成本在从未确立之前诚实为 None**，不猜成 0；
- 购入按票面净单价重算加权平均；出库按当前结存单价计价、不重算均价；
- 退货入库按当前结存单价入账，均价因此不漂移；
- 成本未确立时的出入库只改数量，金额与单价都留空。
"""

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional
import hashlib

CENT = Decimal("0.01")


def position_name(prefix, product, warehouse, location=""):
    """Keep the historical unnamed-position key; named positions get a stable unique key."""
    location = str(location or "").strip()
    if not location:
        return f"{prefix}-{product}-{warehouse}"
    identity = "\0".join((product, warehouse, location))
    return f"{prefix}-{hashlib.sha256(identity.encode()).hexdigest()}"

# 流水类型（与旧线上 txn_type 对齐）。
OPENING = "opening"
PURCHASE_IN = "purchase_in"
SALE_OUT = "sale_out"
STOCK_IN = "stock_in"
STOCK_OUT = "stock_out"
ADJUST = "adjust"
COUNT = "count"
# 调拨（多仓闭环 P2）：同一张单一出一进，调入那一笔按调出那一笔的单价入（成本随货走）
TRANSFER_OUT = "transfer_out"
TRANSFER_IN = "transfer_in"

IN_TYPES = (OPENING, PURCHASE_IN, STOCK_IN, TRANSFER_IN)
OUT_TYPES = (SALE_OUT, STOCK_OUT, TRANSFER_OUT)

MOVEMENT_TYPES = IN_TYPES + OUT_TYPES + (ADJUST, COUNT)

# 出不去的方向：数量为负要按类型解释，不能让调用方随便塞。
SIGNED_TYPES = (ADJUST, COUNT)


# 库存页搜索框写的是「按名称或条码搜」：名称、编码、条码、二维码内容任一包含关键字就算命中，
# 与商品列表的搜索（`products.service._filters`）同一口径。扫码枪给的是完整的码，包含也就命中。
# 分类名称与特性/颜色（岳父 11-01）也算：`category_name` 由调用方按分类主档补到商品行上
SEARCH_FIELDS = ("product_name", "code", "internal_code", "express_code", "barcode", "qr_payload", "category_name", "attribute")


def product_matches(product, query) -> bool:
    """商品是否命中库存页的搜索关键字（不分大小写，空关键字恒命中）。"""
    wanted = str(query or "").strip().casefold()
    if not wanted:
        return True
    return any(wanted in str((product or {}).get(field) or "").casefold() for field in SEARCH_FIELDS)


def d(value) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("invalid number") from None
    if not result.is_finite():
        raise ValueError("invalid number")
    return result


def q2(value) -> Decimal:
    """分位量化，四舍五入（商户报表拍板口径）。"""
    return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def qty(value, *, field: str = "qty") -> Decimal:
    """数量：允许 0 但拒绝负（方向由流水类型决定，不用负数表达）。"""
    result = d(value)
    if result < 0:
        raise ValueError(f"{field}: negative")
    return result


def factor(value) -> Decimal:
    """单位换算系数：必须大于 0。"""
    result = d(value)
    if result <= 0:
        raise ValueError("factor: invalid")
    return result


def to_base(qty_value, factor_value) -> Decimal:
    return qty(qty_value) * factor(factor_value)


def direction_of(movement_type: str) -> str:
    """`in` / `out` / `signed`。未知类型一律拒绝（fail-closed）。"""
    if movement_type in IN_TYPES:
        return "in"
    if movement_type in OUT_TYPES:
        return "out"
    if movement_type in SIGNED_TYPES:
        return "signed"
    raise ValueError(f"unknown movement type: {movement_type}")


def signed_qty(movement_type: str, value) -> Decimal:
    """把"类型 + 数量"翻成带符号的基准单位变动。"""
    direction = direction_of(movement_type)
    if direction == "in":
        return qty(value)
    if direction == "out":
        return -qty(value)
    # 调整/盘点自带符号，因此**不能**走上面的非负校验：负数是"减少"的正当表达。
    return d(value)


def signed_base(movement_type: str, value, factor_value) -> Decimal:
    """落库并贯穿全链路的**唯一**数量：基准单位、带符号（入库为正、出库为负）。

    为什么只留一个数：第一版同时传"带符号数量"和"滚存幅度"，出库的符号被翻了两次，
    账直接反了；回放历史流水时又因为按类型再推一次方向，重算结果和实时结果对不上。
    现在方向只由符号表达，类型只用来决定"要不要按票面价计价"。
    """
    return signed_qty(movement_type, value) * factor(factor_value)


@dataclass(frozen=True)
class Balance:
    """滚存态。qty 恒记（含负库存）；成本未确立前 value / unit 诚实为 None。"""

    qty: Decimal
    value: Optional[Decimal]
    unit: Optional[Decimal]


ZERO_BALANCE = Balance(qty=Decimal("0"), value=None, unit=None)


def opening_balance(qty_value, unit_cost) -> Balance:
    """期初结转态。期初量为 0 视同未确立成本（与从未发生过流水等价）。"""
    quantity = d(qty_value)
    if quantity == 0:
        return ZERO_BALANCE
    value = q2(quantity * d(unit_cost))
    return Balance(qty=quantity, value=value, unit=q2(value / quantity))


def _receive(bal: Balance, amount: Decimal, price: Decimal):
    """入库通用分支：按 price 计入并按加权平均重算单价。"""
    total = q2(amount * price)
    new_qty = bal.qty + amount
    base_value = bal.value if bal.value is not None else Decimal("0.00")
    new_value = q2(base_value + total)
    new_unit = q2(new_value / new_qty) if new_qty != 0 else bal.unit
    return Balance(new_qty, new_value, new_unit), {"unit_price": price, "amount": total}


def _unknown_cost(bal: Balance, amount: Decimal, sign: int):
    """成本从未确立时的诚实分支：只改数量，不猜金额。"""
    return Balance(bal.qty + sign * amount, bal.value, bal.unit), {
        "unit_price": None, "amount": None,
    }


def receive_priced(bal: Balance, amount, price):
    return _receive(bal, d(amount), d(price))


def receive_at_cost(bal: Balance, amount):
    """退货入库：按当前结存单价入账，均价不因此漂移。"""
    quantity = d(amount)
    if bal.unit is None:
        return _unknown_cost(bal, quantity, sign=1)
    return _receive(bal, quantity, bal.unit)


def issue(bal: Balance, amount):
    """出库：按当前结存单价计价，不重算均价（均价只在入库时变动）。"""
    quantity = d(amount)
    if bal.unit is None:
        return _unknown_cost(bal, quantity, sign=-1)
    total = q2(quantity * bal.unit)
    return Balance(bal.qty - quantity, q2(bal.value - total), bal.unit), {
        "unit_price": bal.unit, "amount": total,
    }


def apply_move(bal: Balance, qty_base, *, unit_cost=None):
    """按**带符号**的基准数量推进结存。正数入库、负数出库、0 不动。

    入库时给了成本就按票面净单价重算加权平均；没给（退货入、盘点调增）就按当前结存
    单价。出库一律按当前结存单价。返回 (新结存, 金额明细)；明细里的 `unit_price` /
    `amount` 为 None 表示"成本未确立"——这是有意的，不是缺省值。
    """
    amount = d(qty_base)
    if amount > 0:
        if unit_cost in (None, ""):
            return receive_at_cost(bal, amount)
        return receive_priced(bal, amount, unit_cost)
    if amount < 0:
        return issue(bal, -amount)
    return bal, {"unit_price": None, "amount": None}


def roll(opening: Balance, movements):
    """按给定顺序逐笔滚存，返回 (期末结存, 逐笔明细)。排序是调用方的责任。

    每笔只用它的带符号 `qty_base`：方向由符号表达，类型不参与判断，因此同一份流水
    实时记一次、事后重算一次，结果必然一致。
    """
    bal = opening
    rows = []
    for movement in movements:
        bal, detail = apply_move(
            bal, movement["qty_base"],
            unit_cost=movement.get("unit_cost") if movement.get("cost_known") else None,
        )
        rows.append({**movement, **detail, "bal_qty": bal.qty,
                     "bal_unit_cost": bal.unit, "bal_value": bal.value})
    return bal, rows


def movements_before(movements, date_from, *, after: bool = True):
    """把流水按 `date_from` 切成"期初结转"与"期内"两段。

    `after=True`（报表默认）：`date_from` **当天**算期内，期初是"上期结转"。
    调用方传进来的日期必须已经归一成同一种格式，否则比较会出错。
    """
    before, period = [], []
    for movement in movements:
        date = movement.get("posting_date")
        if date is None or date_from is None:
            period.append(movement)
        elif date < date_from:
            before.append(movement)
        elif date == date_from and not after:
            before.append(movement)
        else:
            period.append(movement)
    return before, period


COUNT_ONLY = ("counted", "uncounted", "differences")


def count_rows(items, *, only="", warehouse="", location=""):
    """盘点行按「只看 / 仓库 / 库位」筛（差异报表、打印、导出同一口径，用户 10-29 批复）。空 = 不筛这一项。
    差异按实盘后数量不等算（四舍到 3 位，与盘点合计同一判据）。"""
    def differs(row):
        try:
            return bool(row.get("counted")) and round(float(row.get("difference") or 0), 3) != 0
        except (TypeError, ValueError):
            return False

    keep = {"counted": lambda row: bool(row.get("counted")), "uncounted": lambda row: not row.get("counted"),
            "differences": differs}.get(str(only or "").strip(), lambda row: True)
    warehouse, location = str(warehouse or "").strip(), str(location or "").strip()
    return [row for row in items if keep(row)
            and (not warehouse or row.get("warehouse") == warehouse)
            and (not location or (row.get("location") or "") == location)]


# 库存一览的两种看法（多仓闭环 P4）：按位置（商品 × 仓 × 库位，原来那样）/ 按商品（公司里各仓合计）
POSITION_VIEW, PRODUCT_VIEW = "position", "product"
VIEWS = (POSITION_VIEW, PRODUCT_VIEW)


def stock_status(qty_on_hand, company_qty, min_stock) -> str:
    """一行的库存状态：这一行没货是缺货；有货时低库存按这件商品在公司所有仓的合计比（最低库存是商品上的一个数，不分仓）。"""
    if d(qty_on_hand) <= 0:
        return "out"
    if d(min_stock) and d(company_qty) <= d(min_stock):
        return "low"
    return "ok"


def product_rows(rows, *, cost_visible) -> list:
    """按位置的行（带 ``company_qty``）→ 按商品合计，次序按每件商品第一次出现。数量、金额相加；有一处成本未确立就整件不确立
    （同一行的规矩：未确立留空、不是 0），没有成本权限的岗位同样留空；状态按公司合计判。"""
    merged = {}
    for row in rows:
        slot = merged.get(row["product"])
        if slot is None:
            slot = merged[row["product"]] = {
                "name": row["product"], "product": row["product"], "product_name": row.get("product_name"),
                "category_name": row.get("category_name") or "", "attribute": row.get("attribute") or "",
                "code": row.get("code"), "barcode": row.get("barcode"), "warehouse": None, "warehouse_name": None,
                "location": "", "unit": row.get("unit"), "min_stock": row.get("min_stock") or 0,
                "company_qty": row.get("company_qty"), "qty": d(0), "value": d(0), "known": True}
        slot["qty"] += d(row["qty_on_hand"])
        slot["value"] += d(row.get("value") or 0)
        slot["known"] = slot["known"] and bool(row.get("cost_known"))
    out = []
    for slot in merged.values():
        qty, value, known = slot.pop("qty"), slot.pop("value"), slot.pop("known")
        shown = cost_visible and known
        out.append({**slot, "qty_on_hand": float(qty), "cost_known": known,
                    "value": float(value) if shown else None,
                    "unit_cost": float(q2(value / qty)) if shown and qty > 0 else None,
                    "status": stock_status(slot["company_qty"], slot["company_qty"], slot["min_stock"])})
    return out
