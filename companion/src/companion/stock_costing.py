# -*- coding: utf-8 -*-
"""CC 写进 Express 的每一行库存增减共用的一套取价与批次栏（K 系列第一项，11-22）：库存四单（``dbf_stock``）、调拨调出（``dbf_transfer``）、
销货（``stock_sale``）、进货（``stock_purchase``）都走这里，不各写一套。

规则来源见 docs/work/2026-11-18-stock-express-parity.md（四、八、九、十一、十二节）与 2026-11-22-stock-purchase-sale-chain.md：

- 平均成本账套（ISINFO.COSMTD ≠ F）：减少按主档批次链 MREMVAL / MREMBAL 取价，超出链上结存的部分按 0（原生 1-戊①）。
- 先进先出、不分仓：减少按这件存货最早仍有剩余的批取价（9.2）；出到链结存及以上时按链货值、多出的按 0（11-22 实录）。
- 减少行的明细批次栏一律照原生记写前链与当前批的指针（11-22 70EXP\test 实录：记写后、不带指针时，财务原生存同日进货后
  Express 从这一行重算当天，把链当成空，CC 的销货被算成 200、原生 1,260；照原生记则逐栏收敛）。覆盖 11-18 库存单「减少行记写后、
  不带指针、超出按均价」的口径（主会话 11-22 定）。
- 先进先出 / 平均 + 分仓计价（LOC_COST = Y，这个仓记着链）：按这个仓的链（``stock_chain``）；出超的部分按 0、链停在 0（十二节）；
  先进先出时排到当天已有行之前的行记开日状态（存单即重算当天，库存 C）。
- 增加：成为新的一批（LOT*），明细记写前结存（为负记 0）与当前批的指针；分仓计价的写后更新这个仓的链，主档抄它。

一张单建一个 ``Book``（同一张单前面的行已经动过的结存、批，后面的行接着用）；写完由调用方在 STLOC 结存记好后
``stock_chain.write(account_dir, *book.chain_writes())``。本模块不写明细行本身，只回这一行要记的栏，并改主档 STMAS。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_schema as S
from . import stock_chain
from . import stock_location

_CENT = Decimal("0.01")
RECEIPT_POSOPR = stock_chain.RECEIPT_POSOPR



def _q2(value) -> Decimal:
    return Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)


def _dec(value) -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def _open(account_dir, name: str, mode):
    path = stock_location._file(account_dir, name)
    if path is None:
        raise FileNotFoundError(name)
    table = dbf.Table(str(path), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    table.open(mode)
    return table


def is_fifo(account_dir) -> bool:
    """成本法先进先出（ISINFO.COSMTD = F）。"""
    return str((account_rules._first(account_dir, "ISINFO") or {}).get("COSMTD") or "").strip().upper() == "F"


def standard_cost(account_dir, stkcod: str) -> Decimal:
    table = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for rec in S.live_rows(table):
            if (rec.STKCOD or "").strip() == stkcod:
                return _dec(rec.STNPR)
        return Decimal(0)
    finally:
        table.close()


def average_cost(account_dir, item: Dict[str, Any]) -> Decimal:
    """移动均价（总结存为正时，TOTVAL / TOTBAL，= Express 的 UNITPR）→ 标准成本 → 0。"""
    if _dec(item["totbal"]) > 0:
        return _dec(item["totval"]) / _dec(item["totbal"])
    return standard_cost(account_dir, item["stkcod"])


def chain_cost(qty: Decimal, bal: Decimal, val: Decimal, average: Decimal) -> Tuple[Decimal, Decimal]:
    """平均成本账套减少一行 → (单价, 货值)：批次链结存为正按链（单价 = 结存金额 / 结存数量，与原生逐栏一致）；超出链上结存的部分、
    以及链结存 ≤ 0 时整行按 ``average``（原生是 0：之后财务原生存同日进货时 Express 照明细的写前链回填，11-22 实录）。"""
    if qty <= 0:
        return Decimal(0), Decimal(0)
    if bal <= 0:
        value = _q2(qty * average)
    elif qty > bal:
        value = _q2(max(val, Decimal(0)) + (qty - bal) * average)
    else:
        value = _q2(qty * val / bal)
    return (value / qty if value else Decimal(0)), value


def fifo_prices(account_dir, codes) -> Dict[str, List[Tuple[Decimal, Decimal]]]:
    """不分仓先进先出：各存货还有剩余的批 → ``[(剩余数量, 单价)]``（由早到晚）。所有进来的批按日期排，减去已经出去的总数
    （= 进来的合计 − 主档总结存），从最早仍有剩余的那一批起算（11-18 先进先出测试账套 4 件物料实录，差在第 4 位小数）。"""
    out: Dict[str, List[Tuple[Decimal, Decimal]]] = {}
    if not codes:
        return out
    received: Dict[str, List[Tuple[Any, str, str, Decimal, Decimal]]] = {}
    table = _open(account_dir, "STCRD", dbf.READ_ONLY)
    try:
        for rec in S.live_rows(table):
            code = (rec.STKCOD or "").strip()
            if code not in codes or (rec.POSOPR or "").strip() not in RECEIPT_POSOPR:
                continue
            qty = _dec(rec.XTRNQTY)
            if qty > 0 and rec.DOCDAT:
                received.setdefault(code, []).append((rec.DOCDAT, (rec.DOCNUM or "").strip(), (rec.SEQNUM or "").strip(),
                                                      qty, _dec(rec.XUNITPR)))
    finally:
        table.close()
    totals = {}
    master = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        for rec in S.live_rows(master):
            code = (rec.STKCOD or "").strip()
            if code in codes:
                totals[code] = _dec(rec.TOTBAL)
    finally:
        master.close()
    for code, rows in received.items():
        rows.sort(key=lambda row: (row[0], row[1], row[2]))
        used = sum((row[3] for row in rows), Decimal(0)) - totals.get(code, Decimal(0))
        left = []
        for _day, _doc, _seq, qty, price in rows:
            if used >= qty:
                used -= qty
                continue
            left.append((qty - max(used, Decimal(0)), price))
            used = Decimal(0)
        out[code] = left
    return out


def fifo_cost(lots: List[Tuple[Decimal, Decimal]], qty: Decimal, average: Decimal) -> Tuple[Decimal, Decimal]:
    """按最早仍有剩余的批依次摊 ``qty`` → (单价, 货值)；批不够的部分按 ``average``。"""
    value, need = Decimal(0), qty
    for remaining, price in lots:
        if need <= 0:
            break
        take = min(remaining, need)
        value += take * price
        need -= take
    if need > 0:
        value += need * average
    value = _q2(value)
    return (value / qty if qty else Decimal(0)), value


def fifo_take(lots: List[Tuple[Decimal, Decimal]], qty: Decimal) -> List[Tuple[Decimal, Decimal]]:
    """从最早的批起扣掉 ``qty``，回剩下的批。"""
    out, need = [], qty
    for remaining, price in lots:
        if need >= remaining:
            need -= remaining
            continue
        out.append((remaining - need, price))
        need = Decimal(0)
    return out


def lot_heads(account_dir, codes, marked=None) -> Dict[str, str]:
    """各存货主档上当前那一批（STMAS.MLOTNUM）：原生增加行的明细 MLOTNUM 指它。被标成负（首字符「-」）的还原成原样
    （「-/F」原样是「B/F」，日期打头的是「2」，11-18 实录），并记进 ``marked``。"""
    out: Dict[str, str] = {}
    if not codes:
        return out
    table = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        has = "MLOTNUM" in table.field_names
        for rec in S.live_rows(table):
            code = (rec.STKCOD or "").strip()
            if code in codes and has:
                raw, was_marked = stock_chain.unmark(str(rec.MLOTNUM or ""))
                if was_marked and marked is not None:
                    marked.add(code)
                out[code] = raw
    finally:
        table.close()
    return out


def lot_number(docdat, posopr: str, docnum: str, seq: int) -> str:
    """一批的批号：日期 + 动作类别 + 单号（12 位）+ 行号（原生）。"""
    return f"{docdat:%Y%m%d}{posopr}{docnum:<12}{S.seqnum_text(seq)}"


def _read_masters(account_dir, codes) -> Dict[str, Dict[str, Any]]:
    """各存货写前的主档快照（同 ``stock_lookup`` 的结存口径）；主档没有的按 0（本单随单新建的存货）。"""
    out = {code: {"stkcod": code, "totbal": Decimal(0), "totval": Decimal(0), "mrembal": Decimal(0), "mremval": Decimal(0),
                  "acccod": ""} for code in codes}
    if not codes:
        return out
    table = _open(account_dir, "STMAS", dbf.READ_ONLY)
    try:
        seen = set()
        for rec in S.live_rows(table):
            code = (rec.STKCOD or "").strip()
            if code in out and code not in seen:
                seen.add(code)
                out[code].update({"totbal": _dec(rec.TOTBAL), "totval": _dec(rec.TOTVAL), "mrembal": _dec(rec.MREMBAL),
                                  "mremval": _dec(rec.MREMVAL), "acccod": (rec.ACCCOD or "").strip()})
    finally:
        table.close()
    return out


@dataclass
class Line:
    """一行库存增减的结果：成本与这一行要记的批次栏（调用方写进明细）。"""

    unit_cost: Decimal  # 每个库存单位的成本
    value: Decimal  # 货值（正数）
    fields: Dict[str, Any] = field(default_factory=dict)  # MREMBAL / MREMVAL / MLOTNUM（+ 增加行的 LOTBAL / LOTVAL / LUNITPR）、XUNITPR


class Book:
    """一张单的取价与批次状态。``places`` = 这张单要动的 (存货码, 仓)；``masters`` = 各存货写前的主档快照
    （``stock_lookup`` 的结存：stkcod、totbal / totval、mrembal / mremval）。"""

    def __init__(self, account_dir, docnum: str, docdat, places: Iterable[Tuple[str, str]] = (),
                 masters: Optional[Dict[str, Dict[str, Any]]] = None, *, master_fields: Optional[Dict[str, Any]] = None):
        self.account_dir = account_dir
        self.docnum, self.docdat = docnum, docdat
        self.master_fields = dict(master_fields or {})  # 每一行都写进主档的栏（库存单：LASUPD）
        self.fifo = is_fifo(account_dir)
        self.by_place = stock_chain.applies(account_dir)
        self.masters: Dict[str, Dict[str, Any]] = {}
        self.marked: set = set()
        self.heads: Dict[str, str] = {}
        self.code_lots: Dict[str, List[Tuple[Decimal, Decimal]]] = {}
        self.chains: Dict[Tuple[str, str], stock_chain.Chain] = {}
        self.place_lots: Dict[Tuple[str, str], List[Tuple[Decimal, Decimal]]] = {}
        self.firsts: Dict[Tuple[str, str], stock_chain.DayFirst] = {}
        self.early_lots: Dict[Tuple[str, str], List[Tuple[Decimal, Decimal]]] = {}
        self.early_state: Dict[Tuple[str, str], Tuple[str, Decimal, Decimal]] = {}
        self.place_values: Dict[Tuple[str, str], Decimal] = {}
        self.balances: Dict[str, Tuple[Decimal, Decimal]] = {}
        self.totals: Dict[str, Tuple[Decimal, Decimal]] = {}
        self.extend(places, masters)

    def extend(self, places: Iterable[Tuple[str, str]], masters: Optional[Dict[str, Dict[str, Any]]] = None) -> None:
        """补进这张单要动的 (存货, 仓)：只读还没读过的（它们还没被本单动过，表里就是写前的状态）。销货、进货逐行认出存货，逐行补；
        混行单分几段写时同一个 ``Book`` 接着用。``masters`` 没给的存货从主档读写前快照。"""
        places = {place for place in places if place not in self.chains and (self.by_place or place[0] not in self.masters)}
        if not places:
            return
        codes = {code for code, _loc in places} - set(self.masters)
        given = masters or {}
        missing = {code for code in codes if code not in given}
        self.masters.update({code: given[code] for code in codes if code in given})
        self.masters.update(_read_masters(self.account_dir, missing))
        self.heads.update(lot_heads(self.account_dir, codes, self.marked))
        if self.fifo:
            self.code_lots.update(fifo_prices(self.account_dir, codes))
        if self.by_place:
            chains = stock_chain.read(self.account_dir, places)
            self.chains.update(chains)
            if self.fifo:
                self.place_lots.update(stock_chain.fifo_lots(self.account_dir, chains))
                self.firsts.update(stock_chain.day_firsts(self.account_dir, places, self.docdat))

    # —— 读状态 ——
    def kept(self, place) -> bool:
        """这个仓记着链、按仓走（老账套有的存货链在主档上）。"""
        return self.by_place and self.chains[place].kept

    def now(self, stkcod: str) -> Dict[str, Any]:
        """这件存货此刻（本单前面的行动过之后）的主档快照。"""
        match = self.masters[stkcod]
        tot_bal, tot_val = self.totals.get(stkcod, (_dec(match["totbal"]), _dec(match["totval"])))
        return {**match, "totbal": tot_bal, "totval": tot_val}

    def total_balance(self, stkcod: str) -> Decimal:
        return self.now(stkcod)["totbal"]

    def _early(self, place, posopr: str, seq: int) -> bool:
        if not (self.kept(place) and self.fifo):
            return False
        if place in self.early_lots:
            return True
        if not stock_chain.ahead(self.firsts.get(place), stock_chain.order_of(posopr, self.docnum, seq)):
            return False
        start = stock_chain.opening(self.firsts[place])
        self.early_lots[place] = stock_chain.fifo_lots(self.account_dir, {place: start}, before=self.docdat)[place]
        self.early_state[place] = (self.firsts[place].head, start.bal, start.val)
        return True

    def _before(self, stkcod, place, early) -> Tuple[Decimal, Decimal]:
        if early:
            return self.early_state[place][1], self.early_state[place][2]
        if self.kept(place):
            return self.chains[place].bal, self.chains[place].val
        match = self.masters[stkcod]
        return self.balances.get(stkcod, (_dec(match["mrembal"]), _dec(match["mremval"])))

    # —— 取价 ——
    def price_out(self, stkcod: str, place, qty: Decimal, *, posopr: str, seq: int) -> Tuple[Decimal, Decimal]:
        """只算一行减少 ``qty``（库存单位，正数）的 (单价, 货值)，不动状态（预估用；写行用 ``take``）。"""
        early = self._early(place, posopr, seq)
        bal, val = self._before(stkcod, place, early)
        return self._out_cost(stkcod, place, qty, bal, val, early, consume=False)

    def _out_cost(self, stkcod, place, qty, bal, val, early, *, consume: bool) -> Tuple[Decimal, Decimal]:
        here = self.kept(place)
        if not self.fifo:  # 平均成本：按链，链上结存不够的部分按 0（原生 1-戊①、11-22 实录）
            return chain_cost(qty, bal, val, Decimal(0))
        pool, slot = (self.early_lots, place) if early else (self.place_lots, place) if here else (self.code_lots, stkcod)
        lots = pool.get(slot, [])
        unit_cost, value = fifo_cost(lots, qty, Decimal(0))  # 批不够的部分按 0（原生）
        if not here and bal > 0 and qty >= bal:
            # 不分仓先进先出出到链结存及以上：原生按主档链货值（11-22 实录 1,200；按库存卡重建的批是 1,170），多出的按 0
            value = _q2(max(val, Decimal(0)))
            unit_cost = value / qty if qty else Decimal(0)
        if consume:
            pool[slot] = fifo_take(lots, qty)
        return unit_cost, value

    # —— 写一行 ——
    def take(self, stkcod: str, place, qty: Decimal, *, posopr: str, seq: int, cost: Optional[Decimal] = None) -> Line:
        """减少 ``qty``（库存单位，正数）。``cost`` 给了就按它（每库存单位）计，不取链 / 批的价（销货结存 ≤ 0 的标准成本 / 人填成本）。
        明细批次栏照原生记写前链与当前批的指针：按仓走的是这个仓的链（11-19 实录 OU2400001），否则是主档的链（11-22 实录 IV0000051）。"""
        early = self._early(place, posopr, seq)
        bal, val = self._before(stkcod, place, early)
        if cost is None:
            unit_cost, value = self._out_cost(stkcod, place, qty, bal, val, early, consume=True)
        else:
            value = _q2(qty * cost) if cost > 0 else Decimal(0)
            unit_cost = cost if value else Decimal(0)
            if self.fifo:
                pool, slot = (self.early_lots, place) if early else (self.place_lots, place) if self.kept(place) \
                    else (self.code_lots, stkcod)
                pool[slot] = fifo_take(pool.get(slot, []), qty)
        return self._apply(stkcod, place, -qty, -value, unit_cost, value, bal, val, early, posopr, seq)

    def add(self, stkcod: str, place, qty: Decimal, value: Decimal, *, posopr: str, seq: int,
            unit_cost: Optional[Decimal] = None) -> Line:
        """增加 ``qty``（库存单位，正数）、货值 ``value``：成为新的一批。``unit_cost``：每库存单位的单价（没给 = 货值 / 数量）。"""
        early = self._early(place, posopr, seq)
        bal, val = self._before(stkcod, place, early)
        value = _q2(value)
        if unit_cost is None:
            unit_cost = value / qty if qty else Decimal(0)
        if self.fifo:
            pool, slot = (self.early_lots, place) if early else (self.place_lots, place) if self.kept(place) \
                else (self.code_lots, stkcod)
            pool.setdefault(slot, []).append((qty, unit_cost))
        return self._apply(stkcod, place, qty, value, unit_cost, value, bal, val, early, posopr, seq)

    def _apply(self, stkcod, place, signed, signed_value, unit_cost, value, bal, val, early, posopr, seq) -> Line:
        from . import stock_sale  # 主档增减共用 ``stock_sale.move_stmas``（避免循环导入）

        here = self.kept(place)
        new_bal, new_val = bal + signed, _q2(val + signed_value)
        self.balances[stkcod] = (new_bal, new_val)
        now = self.now(stkcod)
        self.totals[stkcod] = (now["totbal"] + signed, _q2(now["totval"] + signed_value))
        increase = signed > 0
        fields: Dict[str, Any] = {
            # 批次链结存记写前（增加行写前为负记 0，11-18 实录 JU0000001；减少行照记，11-22 实录 IV0000051），指针指当前那一批
            "MREMBAL": float(bal if early else max(bal, Decimal(0)) if increase else bal),
            "MREMVAL": float(val),
            # 链上还没有批时原生记 B/F（11-24 NLTTEST 原生 JU 实录）
            "MLOTNUM": stock_chain.row_head(self.early_state[place][0] if early else self.chains[place].head if here
                                            else self.heads.get(stkcod, ""), bal),
            "XUNITPR": float(unit_cost.quantize(Decimal("0.0001"), ROUND_HALF_UP)) if unit_cost else 0.0,
        }
        if increase:
            fields.update({"LOTBAL": float(signed), "LOTVAL": float(value), "LUNITPR": float(_q2(unit_cost))})
        master = dict(self.master_fields)
        head_lot = self.heads.get(stkcod, "")
        new_head = lot_number(self.docdat, posopr, self.docnum, seq) if increase else ""
        if early:
            after = stock_chain.moved(stock_chain.opening(stock_chain.DayFirst((), self.early_state[place][0], bal, val)),
                                      signed, signed_value)
            self.early_state[place] = (stock_chain.stored_head(after), after.bal, after.val)
        if here:
            self.chains[place] = stock_chain.moved(self.chains[place], signed, signed_value, new_head)
            self.place_values[place] = self.place_values.get(place, Decimal(0)) + signed_value
            master["MLOTNUM"] = stock_chain.stored_head(self.chains[place])
            stock_sale.move_stmas(self.account_dir, stkcod, signed, signed_value, fields=master,
                                  lots=(self.chains[place].bal, self.chains[place].val))
            return Line(unit_cost, value, fields)
        if signed < 0 and new_bal < 0 and head_lot and stkcod not in self.marked:
            # 原生：批次链结存变成负数时，主档当前批次栏首字符改成「-」（11-18 实录 OU0000002）；明细上的指针仍写原样
            master["MLOTNUM"] = stock_chain.mark(head_lot)
            self.marked.add(stkcod)
        if increase:
            self.heads[stkcod] = master["MLOTNUM"] = new_head
        stock_sale.move_stmas(self.account_dir, stkcod, signed, signed_value, fields=master)
        return Line(unit_cost, value, fields)

    def chain_writes(self) -> Tuple[Dict[Tuple[str, str], stock_chain.Chain], Dict[Tuple[str, str], Decimal]]:
        """交 ``stock_chain.write``：写后各 (存货, 仓) 的链与本单货值。"""
        return {key: self.chains[key] for key in self.place_values}, dict(self.place_values)


__all__ = ["Book", "Line", "average_cost", "chain_cost", "fifo_cost", "fifo_prices", "fifo_take", "is_fifo",
           "lot_heads", "lot_number", "standard_cost"]
