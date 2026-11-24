# -*- coding: utf-8 -*-
"""没有明细行的买卖单补一行「编码 = 科目码」的服务品（直写与代录共用，11-24）。

为什么：Express 的进货 / 销货单金额由明细算出、界面里不能手填。直写以前给没有明细的票（OCR 没读到明细、明细对不上、人选了
「只记金额」）只写单头 + 凭证 + 税记录；这种单人在 Express 里「แก้ไข → 保存」，Express 按 0 行明细重算，金额、凭证、税记录全被
清零、单据仍有效（测试机2 69EXP\\TEST 11-24 实测 RR691124-001、IV0000034）。原生当年人录的单没有只有单头的（S 盘 2568 年
16 本）；人录服务 / 费用类单是明细一行「编码 = 科目码」的服务品（RR 4361、HP 2311 张）。

所以三处同一个口径（直写进货 ``dbf_writer.write_purchase``、直写销货 ``dbf_sales.write_sale``、代录 ``express_operator.masters``）：
补一行直接科目行——科目 = 载荷 ``items_account``（直写记分录的同一个科目）、数量 1、单价 = 税前（代录按含税价录时含税），
品名取科目名（读不到用科目码）。商品由直接科目行原有的 ``product_master.ensure_account_item`` 保证（有就沿用，没有就建；同码是
库存品抛 ``AccountItemIsStock`` 整单交人）。单头、凭证、税记录的写法不变，只多一行明细。

不补的：``items_status == ok`` 的（照明细写）、没有科目的、税前不是正数的、POS 日汇总（``pos`` 块另有写法）、不是 RR / HP / IV / HS 的。
``items_status`` 不是 ok 时载荷里带来的行（OCR 读了一部分、对不上）不可信、直写本来就不写，一律换成补的这一行。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Dict, Optional

DOC_TYPES = ("RR", "HP", "IV", "HS")
MARK = "amount_only_line"  # 补出来的这一行带这个标记（代录回执 line_codes 不把它当成票上的明细回报，``masters.written_codes``）


def line(payload: Dict, *, account_name: str = "", vat_included: bool = False) -> Optional[Dict]:
    """这张票要补的那一行；不用补回 None。``account_name``：科目名（品名用，调用方读账套）；``vat_included``：代录按含税价录。"""
    if str(payload.get("doctype") or "").strip().upper() not in DOC_TYPES or payload.get("pos"):
        return None
    if str(payload.get("items_status") or "").strip() == "ok":
        return None  # 明细可信：照明细写。不是 ok 的（读不到、不全、对不上、人选「只记金额」）带来的行不可信，直写本来就不写，换成补这一行
    account = str(payload.get("items_account") or "").strip()
    try:
        price = Decimal(str(payload.get("total_amount" if vat_included else "base_amount")).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None
    if not account or not price.is_finite() or price <= 0:
        return None
    amount = format(price.quantize(Decimal("0.01")), "f")
    return {"name": account_name.strip() or account, "qty": "1", "unit_price": amount, "amount": amount,
            "item_mode": "direct_account", "account": account, MARK: True}


def fill(entry, payload: Dict, account_dir) -> bool:
    """直写用：这张票要补行就把补的那一行放进 ``entry``（``items`` / ``items_status``），回是否补了。品名读这本账套的科目名。"""
    probe = line(payload)
    if probe is None:
        return False
    from pathlib import Path

    from .product_master import _account_name

    entry.items = [line(payload, account_name=_account_name(Path(account_dir), probe["account"]))]
    entry.items_status = "ok"
    return True
