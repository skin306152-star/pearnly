# -*- coding: utf-8 -*-
"""采购建真库存(STKTYP=0):进货票 → 建/复用库存档 + 真入库 + 真成本。库存闭环的前半条。

「选库存」的采购行走这里(item_mode='stock_item')。此前采购只会写非库存行/直接科目行,库存
永远是 0,于是销售侧一卖就卡 STOCK_ITEM_NOT_FOUND —— 闭环缺的正是「进货把货和成本入进去」。
入库后销售侧 stock_sale ① 才有移动均价可结转 COGS。

成本口径:单位成本 = 行净额 ÷ 数量。净额 = 不含 VAT、已扣折扣;运费不摊进单价(运费另有科目,
摊进去会让同一批货在不同单据上成本不一致,期末对不上)。行货值(XTRNVAL/STMAS 货值)取净额本身,
单价只是它除以数量的展示值 —— 谁是权威不含糊。

数量/金额非法(qty≤0 或 amount<0)当场抛错交会计,绝不写负库存/负成本。

GL 不在这里写:本模块只落库存台账(STCRD 入库行 + STMAS 结存累加),返回「存货科目 → 入库额」
给调用方(dbf_writer)追加重分类分录(借 存货 / 贷 购货),与销售侧追加 COGS 分录同款机制。
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Tuple

import dbf

from . import dbf_detail
from . import dbf_schema as S
from . import product_master
from . import stock_sale
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
    """读库存主档现状 → (结存数量, 结存货值, ACCCOD 科目联动键)。

    按 STKCOD 读而不按品名读:同名可能有多行(历史脏数据/建档撞名),按名匹配会把货入到另一行,
    与随后写 STMAS 的那一行对不上。
    """
    t = dbf.Table(str(Path(account_dir) / "STMAS.DBF"), codepage=S.CODEPAGE)
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
    userid: str = "BIT9",
    acccod: str = "",
    ref_no: str = "",
) -> Tuple[List[Dict], Dict[str, Decimal]]:
    """写库存入库行 + 累加 STMAS 结存,返回 (逐行 mode 结果, {存货科目码: 入库金额合计})。

    items = item_mode=='stock_item' 的行(已在备份-回滚事务内)。逐行:建/复用 STKTYP=0 库存档
    → 按码读现结存 → 追加 STCRD 入库行 → STMAS 结存累加。

    只滚结存(MREMBAL/MREMVAL/TOTBAL/TOTVAL),**不碰期初列 OPNBAL/OPNVAL**:期初是会计的
    开账事实,进货是期中发生额,把进货写进期初会让客户的期初余额报表凭空长大。
    """
    line_modes: List[Dict] = []
    in_by_account: Dict[str, Decimal] = {}
    st = dbf.Table(str(Path(account_dir) / "STCRD.DBF"), codepage=S.CODEPAGE)
    st.open(dbf.READ_WRITE)
    try:
        for seq, it in enumerate(items, start=1):
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
            made = product_master.ensure_stock_item(
                account_dir, name, userid=userid, unit=unit, acccod=acccod
            )
            stkcod = made.stkcod
            # 别写回 acccod:那是「建新档用哪个科目组」的入参,逐行循环里覆盖它会让第二行
            # 起拿上一个商品的科目码去建档。这里要的是本商品档上实际挂的组。
            cur_bal, cur_val, item_acccod = _stmas_state(account_dir, stkcod)
            inv_acc, _cogs_acc = stock_sale.stock_accounts(account_dir, item_acccod)
            new_bal = cur_bal + qty
            new_val = _money(cur_val + amount)
            st.append(
                S.stcrd_stock_in_row(
                    docnum,
                    seq,
                    docdat,
                    stkcod,
                    party_code,
                    name,
                    float(qty),
                    float(_money(amount / qty)),
                    val=float(amount),
                    rem_bal=float(new_bal),
                    rem_val=float(new_val),
                    unit=unit,
                    depcod=depcod,
                    ref_no=ref_no,
                )
            )
            stock_sale.write_stmas_fields(
                account_dir,
                stkcod,
                {
                    "MREMBAL": float(new_bal),
                    "MREMVAL": float(new_val),
                    "TOTBAL": float(new_bal),
                    "TOTVAL": float(new_val),
                },
            )
            in_by_account[inv_acc] = in_by_account.get(inv_acc, Decimal("0")) + amount
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
