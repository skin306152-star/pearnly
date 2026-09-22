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

IN_TYPES = (OPENING, PURCHASE_IN, STOCK_IN)
OUT_TYPES = (SALE_OUT, STOCK_OUT)

MOVEMENT_TYPES = IN_TYPES + OUT_TYPES + (ADJUST, COUNT)

# 出不去的方向：数量为负要按类型解释，不能让调用方随便塞。
SIGNED_TYPES = (ADJUST, COUNT)


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
