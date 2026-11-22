# -*- coding: utf-8 -*-
"""采购建真库存(STKTYP=0):进货票 → 建/复用库存档 + 真入库 + 真成本。库存闭环的前半条。

「选库存」的采购行走这里(item_mode='stock_item')。此前采购只会写非库存行/直接科目行,库存
永远是 0,于是销售侧一卖就卡 STOCK_ITEM_NOT_FOUND —— 闭环缺的正是「进货把货和成本入进去」。
入库后销售侧 stock_sale ① 才有移动均价可结转 COGS。

成本口径:单位成本 = 行净额 ÷ 数量。净额 = 不含 VAT、已扣折扣;运费不摊进单价(运费另有科目,
摊进去会让同一批货在不同单据上成本不一致,期末对不上)。行货值(XTRNVAL/STMAS 货值)取净额本身,
单价只是它除以数量的展示值 —— 谁是权威不含糊。

数量/金额非法(qty≤0 或 amount<0)当场抛错交会计,绝不写负库存/负成本。

GL 不在这里写:本模块只落库存台账(STCRD 入库行 + STMAS 结存累加),返回「存货科目 → 入库额」,
``groups`` 另收「物料科目组 (ACCNUM01, ACCNUM02) → 入库额」交调用方照原生直接记科目组科目(``purchase_lines.stock_journal``)。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import dbf

from . import account_rules
from . import dbf_detail
from . import dbf_fields
from . import dbf_schema as S
from . import product_master
from . import stock_location
from . import stock_sale
from . import stock_units
from .dbf_detail import ITEM_MODE_STOCK  # item_mode 常量单一事实源在明细行路由那侧
from .logger import get_logger

log = get_logger(__name__)


class StockPurchaseError(Exception):
    """采购入库行不能自动落账(数量/金额非法)→ 交会计核对,绝不写负库存/负成本。"""


def _money(x) -> Decimal:
    """金额量化到分(钱走 decimal,不用 float 累加)。"""
    return Decimal(str(x)).quantize(Decimal("0.01"), ROUND_HALF_UP)


def _gv(rec, field: str, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v


def _stmas_state(account_dir: Path, stkcod: str) -> Tuple[Decimal, Decimal, str]:
    """读库存主档现状 → (批次链结存 MREMBAL, 批次链货值 MREMVAL, ACCCOD 科目联动键)。结存数只给 STCRD 行上的结存栏用
    (既有写法);主档总结存由 ``stock_sale.move_stmas`` 按增减改,不从这里推。

    按 STKCOD 读而不按品名读:同名可能有多行(历史脏数据/建档撞名),按名匹配会把货入到另一行,
    与随后写 STMAS 的那一行对不上。
    """
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    t.open(dbf.READ_ONLY)
    try:
        for r in t:
            if dbf.is_deleted(r) or _gv(r, "STKCOD") != stkcod:
                continue
            return (
                Decimal(str(_gv(r, "MREMBAL") or 0)),
                _money(_gv(r, "MREMVAL") or 0),
                _gv(r, "ACCCOD"),
            )
        raise StockPurchaseError(f"库存主档 {stkcod} 建档后读不回 · 交人工核对")
    finally:
        t.close()


def write_stock_in_lines(
    account_dir: Path,
    docnum: str,
    docdat,
    items: List[Dict],
    party_code: str,
    *,
    depcod: str = "",
    userid: str = S.PEARNLY_USERID,
    acccod: str = "",
    ref_no: str = "",
    locations: Optional[List[Optional[str]]] = None,
    moves: Optional[list] = None,
    seqs: Optional[List[int]] = None,
    groups: Optional[Dict[Tuple[str, str], Decimal]] = None,
    costing: Optional[Dict] = None,
) -> Tuple[List[Dict], Dict[str, Decimal]]:
    """写库存入库行 + 累加 STMAS 结存,返回 (逐行 mode 结果, {存货科目码: 入库金额合计})。
    ``groups``(可选):按物料科目组累计 {(存货科目 ACCNUM01, 购货 / 成本科目 ACCNUM02): 入库金额}。

    items = item_mode=='stock_item' 的行(已在备份-回滚事务内)。逐行:建/复用 STKTYP=0 库存档
    → 按码读现结存 → 追加 STCRD 入库行 → STMAS 结存累加。

    只滚结存(总结存 TOTBAL/TOTVAL 按增减、UNITPR 随之重算,批次链 MREMBAL/MREMVAL 同样增减,见 ``stock_sale.move_stmas``),
    **不碰期初列 OPNBAL/OPNVAL**:期初是会计的
    开账事实,进货是期中发生额,把进货写进期初会让客户的期初余额报表凭空长大。

    载荷 4(``stock_location``):``locations`` 是逐行仓库码(None = 主仓,同以前),``moves`` 收集各行在 STLOC 上的
    变动交调用方记账;两者都是 None 时与以前逐字节相同。
    ``seqs``:这几行在整张单里的行号(混行单分段写,`dbf_detail.runs`;``locations`` 照整张单的下标取);不给 = 从 1 起。
    ``costing``:同一张单几段共用的取价状态(``{"book": stock_costing.Book}``);调用方在 STLOC 结存记好后把分仓链写进去。

    每一行照原生成为新的一批(K 系列 11-22,与库存单增加行同一套 ``stock_costing``):LOTBAL / LOTVAL / LUNITPR、明细记写前结存
    (为负记 0)与当前批的指针,主档当前批改成这一批;分仓计价账套更新这个仓的批次链与 LOCVAL、主档抄它。
    """
    from . import stock_costing

    costing = costing if costing is not None else {}
    book = costing.get("book")
    if book is None:
        book = costing["book"] = stock_costing.Book(account_dir, docnum, docdat)
    main = account_rules.main_location(account_dir)
    line_modes: List[Dict] = []
    in_by_account: Dict[str, Decimal] = {}
    st = dbf.Table(str(Path(account_dir) / "STCRD.DBF"), codepage=S.CODEPAGE, unicode_errors=S.UNICODE_ERRORS)
    st.open(dbf.READ_WRITE)
    try:
        st_fields = {name.upper() for name in st.field_names}
        for seq, it in zip(dbf_detail.line_numbers(items, seqs), items):
            name = str(it.get("name") or "")
            qty = Decimal(str(it.get("qty") or "0"))
            amount = _money(it.get("amount") or "0")
            unit = str(it.get("unit") or "")
            if qty <= 0 or amount < 0:
                raise StockPurchaseError(
                    f"进货行「{name}」数量/金额非法(qty={qty} amount={amount})· "
                    f"入库会造负库存或负成本 · 交会计核对"
                )
            # 「新建」还是「复用」由建档自己回报(它本来就先查了一遍),不再多扫一遍 STMAS
            # 复核时选定了 Express 存货(`code`)就按码落,码不在就报错,不回头按品名找 / 新建
            # (2026-09-28 压测:票面品名与会计的存货名不同,按名找不到就新建了重复存货)。
            made = product_master.ensure_stock_item(
                account_dir,
                name,
                userid=userid,
                unit=unit,
                # 载荷 21 的行键（``guided``；版本 ≤ 20 的行上没有）：这一行自己的科目组盖过整单的，分类、允许负库存照行上给的
                acccod=str(it.get("acccod") or acccod),
                code=str(it.get("code") or ""),
                stkgrp=str(it.get("stkgrp") or ""),
                negallow=str(it.get("negallow") or ""),
            )
            stkcod = made.stkcod
            # 别写回 acccod:那是「建新档用哪个科目组」的入参,逐行循环里覆盖它会让第二行
            # 起拿上一个商品的科目码去建档。这里要的是本商品档上实际挂的组。
            _cur_bal, _cur_val, item_acccod = _stmas_state(account_dir, stkcod)
            inv_acc, _cogs_acc = stock_sale.stock_accounts(account_dir, item_acccod)
            # 包装单位按 Express 主档系数换成库存单位入库（stock_units；验收 10-25）
            units = stock_units.resolve(account_dir, stkcod, unit, qty, it.get("factor"))
            loc = stock_location.location_at(locations, seq - 1)
            book.extend([(stkcod, loc or main)])
            base_cost = _money(amount / units.base_qty)
            out = book.add(stkcod, (stkcod, loc or main), units.base_qty, amount, posopr=S.STCRD_POSOPR_PURCHASE, seq=seq,
                           unit_cost=base_cost)
            row = account_rules.localize_stcrd(account_dir, S.stcrd_stock_in_row(
                    docnum,
                    seq,
                    docdat,
                    stkcod,
                    party_code,
                    name,
                    float(units.qty),
                    float(_money(amount / units.qty)),
                    val=float(amount),
                    rem_bal=out.fields["MREMBAL"],
                    rem_val=out.fields["MREMVAL"],
                    unit=units.unit,
                    depcod=depcod,
                    ref_no=ref_no,
                    posopr=S.STCRD_POSOPR_PURCHASE,
                ), units.unit, loc)
            row = dbf_fields.priced_line(row, it, st_fields)  # 现购 HP 价含税：UNITPR / TRNVAL 记含税，成本照税前
            # 进货的库存行没有售价：原生 RR / HP 的库存行 XSALVAL 是 0（S 盘 95 本人录的 15.9 万行里 93%，11-07 取证），
            # 非库存行才记税前额（那条路不经这里）
            row["XSALVAL"] = 0.0
            row.update({name: value for name, value in out.fields.items() if name in st_fields and name != "XUNITPR"})
            S.append_stcrd(st, stock_units.stamp(row, units, base_cost=base_cost))
            if moves is not None:
                moves.append(stock_location.move(row, stock_location.KIND_PURCHASE))
            in_by_account[inv_acc] = in_by_account.get(inv_acc, Decimal("0")) + amount
            if groups is not None:
                groups[(inv_acc, _cogs_acc)] = groups.get((inv_acc, _cogs_acc), Decimal("0")) + amount
            line_modes.append(
                dbf_detail.line_mode(
                    seq,
                    name,
                    stkcod,
                    mode=ITEM_MODE_STOCK,
                    created=made.created,
                    unit_guessed=made.unit_guessed,
                    unit_code=made.unit_code,
                )
            )
    finally:
        st.close()
    log.info(
        "stock-in %s: %d 行 · 存货科目归集 %s", docnum, len(line_modes), in_by_account
    )
    return line_modes, in_by_account
