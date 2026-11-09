"""收发存导出的行模型（纯函数，不依赖 Frappe / IO）：照 MR.ERP「รายงานรับ-จ่ายสินค้า」三种报表的算法。

来源（只读参考 `~/mrerp-source/src`，对照见 docs/work/2026-11-08-stock-card-export-评估.md §A）：
合并全部仓 `report/50/all/57.php`、汇总 `report/51/all/59.php`、分仓 `report/52/all/198.php`。

- 收 / 发只看方向（我们是带符号数量：> 0 收、< 0 发；MR.ERP 是 statustran 1 / 2），单据类型不参与。
- 行上的单价、金额取我们成本引擎逐笔算出的值（MR.ERP 取它成本引擎写进库存卡的 ustd / amtstd）。
- 结余由报表自己滚：结余数量 ± 行数量、结余金额 ± 行金额；结余单价 = 数量为 0 时 0，否则 round(金额 / 数量, 2)。
- 汇总的收 / 发单价 = round(金额合计 / 数量合计, 2)，数量合计为 0 时 0。
- 小计、合计只合计数量与金额，单价格留空。
- 明细排序：日期 → 同一天先收后发 → 单号 → 录入先后。

我们多出的一条（评估 §C-3，主会话 11-08 定）：成本从未确立的流水金额为 None（MR.ERP 记 0）。行上留空；所在小计 / 合计的
金额只要有一笔未知就留空；结余金额在这个仓位成本确立之前留空（确立之后与成本引擎一样，未知那几笔按 0 计）。
没有成本权限的人（`costs=False`）单价、金额一律留空，数量照算。
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

ZERO = Decimal("0")
CENT = Decimal("0.01")


def d(value) -> Decimal:
    if value in (None, ""):
        return ZERO
    return value if isinstance(value, Decimal) else Decimal(str(value))


def q2(value) -> Decimal:
    return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def price(amount: Optional[Decimal], qty: Decimal) -> Optional[Decimal]:
    """MR.ERP 的单价：数量 0 → 0，否则 round(金额 / 数量, 2)；金额未知 → 未知。"""
    if amount is None:
        return None
    return ZERO if qty == 0 else q2(amount / qty) + 0   # + 0：负数量、金额 0 时不出「-0.00」


def add(total: Optional[Decimal], value: Optional[Decimal]) -> Optional[Decimal]:
    """合计：任一笔未知整格未知（不拿已知部分冒充）。"""
    return None if total is None or value is None else total + value


@dataclass
class Movement:
    """一笔流水（已由成本引擎算好金额）。`position` 是它所在的「仓 × 仓位」，合并报表跨仓位滚结余时分开记成本是否确立。"""

    date: str               # YYYY-MM-DD
    doc: str
    qty: Decimal            # 带符号：> 0 收、< 0 发
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    seq: int                # 录入先后（成本引擎的顺序）
    position: tuple = ()


@dataclass
class Opening:
    """一个仓位的上期结转（成本引擎算的，初始化已并入）。"""

    position: tuple
    qty: Decimal
    amount: Optional[Decimal]


@dataclass
class Line:
    date: str
    doc: str
    side: str               # "in" / "out"
    qty: Decimal            # 正数
    unit_price: Optional[Decimal]
    amount: Optional[Decimal]
    bal_qty: Decimal
    bal_price: Optional[Decimal]
    bal_amount: Optional[Decimal]


@dataclass
class Block:
    """一个物料（分仓版是一个仓里的一个物料）的明细块。"""

    opening_qty: Decimal
    opening_amount: Optional[Decimal]
    lines: list = field(default_factory=list)
    in_qty: Decimal = ZERO
    in_amount: Optional[Decimal] = ZERO
    out_qty: Decimal = ZERO
    out_amount: Optional[Decimal] = ZERO
    closing_qty: Decimal = ZERO
    closing_amount: Optional[Decimal] = ZERO

    @property
    def opening_price(self):
        return price(self.opening_amount, self.opening_qty)

    @property
    def in_price(self):
        return price(self.in_amount, self.in_qty)

    @property
    def out_price(self):
        return price(self.out_amount, self.out_qty)

    @property
    def closing_price(self):
        return price(self.closing_amount, self.closing_qty)

    def shown_in_detail(self) -> bool:
        """MR.ERP 明细：结转数量 ≠ 0 或 结转金额 ≠ 0 或 期内有行。"""
        return bool(self.opening_qty) or bool(self.opening_amount) or bool(self.lines)

    def shown_in_summary(self) -> bool:
        """MR.ERP 汇总：四块 × 数量 / 单价 / 金额 共 12 个数任一 ≠ 0（未知当 0 判）。"""
        values = (self.opening_qty, self.opening_price, self.opening_amount, self.in_qty, self.in_price, self.in_amount,
                  self.out_qty, self.out_price, self.out_amount, self.closing_qty, self.closing_price,
                  self.closing_amount)
        return any(value for value in values if value is not None)


def order_key(movement: Movement):
    return (movement.date, 0 if movement.qty >= 0 else 1, movement.doc or "", movement.seq)


@dataclass
class PoolMove:
    """算成本用的一笔流水。`cost` = 这笔收自带的单价（采购、入库单、期初、有价调整）；None = 没有自带单价
    （退货入、盘点调增、调拨入），按当时池里的均价入。发出不看 `cost`。"""

    date: str
    doc: str
    qty: Decimal            # 带符号：> 0 收、< 0 发
    seq: int
    cost: Optional[Decimal] = None


def pool_costs(moves) -> dict:
    """照 MR.ERP 成本引擎（component/php/stkcardpc.php，移动平均那一路）给每一笔定单价、金额：`{seq: (单价, 金额)}`。

    - 一个物料全公司一个池（所有仓、仓位合在一起），顺序同报表：日期 → 同一天先收后发 → 单号 → 录入先后；
    - 收：金额 = 数量 × 单价（自带单价，没有就用池里当时的结余单价）；结余数量、金额相加，结余单价 = round(金额 / 数量, 2)，
      数量 ≤ 0 时 0；
    - 发：发完结余 > 0 → 单价 = 发出前的结余单价、金额 = 单价 × 数量；发完结余 ≤ 0 → 金额 = 发出前剩下的全部金额、
      单价 = round(金额 / 数量, 2)，结余金额、单价归 0（之前没有结余就是 0 / 0）。
    永远有数，不留空（主会话 11-09：岳父的 MR.ERP 就是这样，不能出现空格）。
    """
    qty, amount, unit = ZERO, ZERO, ZERO
    out = {}
    for move in sorted(moves, key=order_key):
        quantity = abs(d(move.qty))
        if d(move.qty) >= 0:
            each = q2(move.cost) if move.cost is not None else unit
            value = q2(quantity * each)
            qty, amount = qty + quantity, amount + value
            unit = ZERO if qty <= 0 else q2(amount / qty)
            out[move.seq] = (each, value)
            continue
        left = qty - quantity
        if left <= 0:
            value = amount
            each = ZERO if quantity == 0 else q2(value / quantity)
            amount, unit = ZERO, ZERO
        else:
            each, value = unit, q2(unit * quantity)
            amount = amount - value
            unit = q2(amount / left)
        qty = left
        out[move.seq] = (each, value)
    return out


class _Running:
    """跨仓位的结余：数量、金额各自相加；金额能不能显示看每个仓位的成本确立了没有（仓位数量为 0 的不拖累）。"""

    def __init__(self, openings, costs):
        self.costs = costs
        self.qty = ZERO
        self.amount = ZERO
        self.positions: dict = {}
        for opening in openings:
            self.qty += opening.qty
            self.amount += d(opening.amount)
            state = self.positions.setdefault(opening.position, [ZERO, False])
            state[0] += opening.qty
            # 成本确立 = 成本引擎给了金额；数量为 0 的仓位不拖累合计（见 known）
            state[1] = state[1] or opening.amount is not None

    def known(self) -> bool:
        return self.costs and all(established or qty == 0 for qty, established in self.positions.values())

    def apply(self, movement: Movement):
        self.qty += movement.qty
        state = self.positions.setdefault(movement.position, [ZERO, False])
        state[0] += movement.qty
        if movement.amount is not None:
            self.amount += movement.amount if movement.qty >= 0 else -movement.amount
            state[1] = True

    def balance(self):
        amount = q2(self.amount) if self.known() else None
        return self.qty, (price(amount, self.qty) if amount is not None else None), amount


def block(openings, movements, *, costs=True) -> Block:
    """一个物料的明细块：上期结转（各仓位相加）+ 期内逐笔（MR.ERP 排序）+ 收发小计 + 期末。"""
    running = _Running(openings, costs)
    opening_qty, _price, opening_amount = running.balance()
    result = Block(opening_qty=opening_qty, opening_amount=opening_amount,
                   in_amount=ZERO if costs else None, out_amount=ZERO if costs else None)
    for movement in sorted(movements, key=order_key):
        side = "in" if movement.qty >= 0 else "out"
        quantity = abs(movement.qty)
        unit_price = movement.unit_price if costs else None
        amount = movement.amount if costs else None
        running.apply(movement)
        bal_qty, bal_price, bal_amount = running.balance()
        result.lines.append(Line(movement.date, movement.doc, side, quantity, unit_price, amount,
                                 bal_qty, bal_price, bal_amount))
        if side == "in":
            result.in_qty += quantity
            result.in_amount = add(result.in_amount, amount)
        else:
            result.out_qty += quantity
            result.out_amount = add(result.out_amount, amount)
    result.closing_qty, _price, result.closing_amount = running.balance()
    return result


@dataclass
class Totals:
    """小计 / 合计：只有数量与金额（MR.ERP 合计行单价格空）。"""

    blocks: int = 0
    lines: int = 0
    opening_qty: Decimal = ZERO
    opening_amount: Optional[Decimal] = ZERO
    in_qty: Decimal = ZERO
    in_amount: Optional[Decimal] = ZERO
    out_qty: Decimal = ZERO
    out_amount: Optional[Decimal] = ZERO
    closing_qty: Decimal = ZERO
    closing_amount: Optional[Decimal] = ZERO

    def take(self, item: Block):
        self.blocks += 1
        self.lines += len(item.lines)
        self.opening_qty += item.opening_qty
        self.opening_amount = add(self.opening_amount, item.opening_amount)
        self.in_qty += item.in_qty
        self.in_amount = add(self.in_amount, item.in_amount)
        self.out_qty += item.out_qty
        self.out_amount = add(self.out_amount, item.out_amount)
        self.closing_qty += item.closing_qty
        self.closing_amount = add(self.closing_amount, item.closing_amount)

    def merge(self, other: "Totals"):
        self.blocks += other.blocks
        self.lines += other.lines
        for name in ("opening", "in", "out", "closing"):
            setattr(self, f"{name}_qty", getattr(self, f"{name}_qty") + getattr(other, f"{name}_qty"))
            setattr(self, f"{name}_amount", add(getattr(self, f"{name}_amount"), getattr(other, f"{name}_amount")))

    def any(self) -> bool:
        """MR.ERP 表尾只在有物料或任一合计 ≠ 0 时印。"""
        values = (self.opening_qty, self.opening_amount, self.in_qty, self.in_amount, self.out_qty, self.out_amount,
                  self.closing_qty, self.closing_amount)
        return bool(self.blocks) or any(value for value in values if value is not None)


def totals(costs=True) -> Totals:
    empty = ZERO if costs else None
    return Totals(opening_amount=empty, in_amount=empty, out_amount=empty, closing_amount=empty)


def code_key(code) -> str:
    """物料 / 仓库代码的比较与排序口径（MR.ERP 是 MariaDB 不分大小写的 `BETWEEN` 与 `ORDER BY`）。"""
    return str(code or "").strip().casefold()


def in_range(code, start=None, end=None) -> bool:
    """代码区间（含两头）；哪头留空哪头就不限。"""
    key = code_key(code)
    if start not in (None, "") and key < code_key(start):
        return False
    if end not in (None, "") and key > code_key(end):
        return False
    return True
