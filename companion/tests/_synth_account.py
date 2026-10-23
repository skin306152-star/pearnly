# -*- coding: utf-8 -*-
"""合成账套骨架:按表规格建全部表 + 种上每套真 Express 账都有的两张基础表。

ISTAB(单位码)与 GLACC(科目表)在真账套里必然存在,自动建库存档要靠它们判「单位码合不合法」
「选中的存货科目是不是真存货科目」。合成账套漏建这两张,测的就不是生产上跑的那条路。
行取自 70EXP 真账套,只留判定用得上的几条(名称/GROUP/PARENT 一字未改)。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Sequence

import dbf

from companion import dbf_schema as S

# (ACCNUM, ACCNAM, GROUP, PARENT):存货两条 + 固定资产/费用/收入各一条,够守门正反两侧。
GLACC_ROWS = (
    ("10-00-00-00", "สินทรัพย์", "1", ""),
    ("11-00-00-00", "สินทรัพย์หมุนเวียน", "1", "10-00-00-00"),
    ("11-04-00-00", "สินค้าคงเหลือ", "1", "11-00-00-00"),
    ("11-04-02-00", "สินค้าสำเร็จรูป(น้ำมันใส)", "1", "11-04-00-00"),
    ("11-04-04-00", "สินค้าสำเร็จรูป(น้ำมันเครื่อง)", "1", "11-04-00-00"),
    ("14-00-00-00", "ที่ดิน\xa0อาคารและอุปกรณ์สุทธิ", "1", "10-00-00-00"),
    ("14-01-00-00", "ที่ดิน\xa0อาคาร\xa0ยานพาหนะและอุปกรณ์", "1", "14-00-00-00"),
    ("14-01-03-00", "อุปกรณ์สำนักงาน", "1", "14-01-00-00"),
    ("40-00-00-00", "รายได้", "4", ""),
    ("41-00-00-00", "รายได้จากการขายสินค้า-สุทธิ", "4", "40-00-00-00"),
    ("41-01-00-00", "รายได้จากการขาย", "4", "41-00-00-00"),
    ("50-00-00-00", "ค่าใช้จ่าย", "5", ""),
    ("51-00-00-00", "ต้นทุนขายสุทธิ", "5", "50-00-00-00"),
    ("51-01-00-00", "ต้นทุนสินค้าเพื่อขาย", "5", "51-00-00-00"),
    ("51-03-00-00", "ซื้อสุทธิ", "5", "51-00-00-00"),
    ("52-00-00-00", "ค่าใช้จ่ายในการขาย", "5", "50-00-00-00"),
    ("52-08-00-00", "ค่าใช้จ่าย-ส่งเสริมการขาย", "5", "52-00-00-00"),
)

# (TYPCOD, SHORTNAM, TYPDES):注意码 ≠ 单位名前两字(ถัง→ถง、ชิ้น→ชน),这正是 QUCOD 的坑。
ISTAB_UNITS = (
    ("ลต", "ลต", "ลิตร"),
    ("ถง", "ถง", "ถัง"),
    ("ชน", "ชิ้น", "ชิ้น"),
    ("กง", "กปง", "กระป๋อง"),
    ("กล", "กล", "กล่อง"),
    ("คง", "ครั้ง", "ครั้ง"),
    ("ขด", "ขวด", "ขวด"),
)


# 合成账套默认财年:CY = 2026 全年、NY = 2027 全年(与靶场 69EXP/test 真表同形)。
# 用别的年份/起始月的模块自行再调一次 seed_fiscal_year 覆盖(它替掉原行,不叠加)。
DEFAULT_CY_YEAR = 2026


def build(root: Path) -> Path:
    """在 root 下建一套空的合成账套(含 GLACC/ISTAB/ISPRD 基础数据),返回账套目录。"""
    root.mkdir(parents=True, exist_ok=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(
            str(root / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE
        )
        t.open(dbf.READ_WRITE)
        t.close()
    _seed(
        root,
        "GLACC",
        [
            {"ACCNUM": num, "ACCNAM": nam, "GROUP": grp, "PARENT": parent}
            for num, nam, grp, parent in GLACC_ROWS
        ],
    )
    _seed(
        root,
        "ISTAB",
        [
            {"TABTYP": "20", "TYPCOD": code, "SHORTNAM": short, "TYPDES": des}
            for code, short, des in ISTAB_UNITS
        ],
    )
    # 真账套必有会计期间表:不种它,余额表月桶就落不出列名(写路会按判不出跳过桶)。
    seed_fiscal_year(root, DEFAULT_CY_YEAR)
    return root


def seed_fiscal_year(
    acct: Path,
    cy_year: int,
    *,
    start_month: int = 1,
    locked_cy: Sequence[int] = (),
    locked_ny: Sequence[int] = (),
) -> Path:
    """种账套的会计期间表 ISPRD:24 期(CY 12 + NY 12)。

    `cy_year`/`start_month` = CY 第 1 期的年月。要复现非 1 月起账的账套(真账里 69ASIAMI/
    69ASIAR/68ASIAMI/68ASIAR 四本)传 `start_month=11`。`locked_*` 是关账期号列表 —— 小助手
    这条写路只对 ERP 的单(``respect_period_lock``)读锁,种它也为让合成表与真表同形(真账 432 格全是 'Y'/'N',没有空白)。
    重复调用覆盖原行,不叠加。
    """
    row = {}
    for slot, base_year, locked in (
        ("", cy_year, set(locked_cy)),
        ("NY", cy_year + 1, set(locked_ny)),
    ):
        for m in range(1, 13):
            beg = _add_months(dt.date(base_year, start_month, 1), m - 1)
            end = _add_months(beg, 1) - dt.timedelta(days=1)
            row[f"BEG{m}{slot}"] = beg
            row[f"END{m}{slot}"] = end
            row[f"LOCK{m}{slot}"] = "Y" if m in locked else "N"
    t = dbf.Table(str(acct / "ISPRD.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            dbf.delete(r)
        t.append(row)
    finally:
        t.close()
    return acct


def _add_months(d: dt.date, n: int) -> dt.date:
    total = (d.year * 12 + d.month - 1) + n
    return dt.date(total // 12, total % 12 + 1, 1)


def _seed(acct: Path, table: str, rows) -> None:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for row in rows:
            t.append(row)
    finally:
        t.close()
