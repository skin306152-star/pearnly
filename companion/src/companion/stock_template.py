# -*- coding: utf-8 -*-
"""自动建库存档时,克隆哪条既有库存品 —— 以及选中的存货科目认不认。

**为什么不能取第一条**:模板决定新品的 `ACCCOD`,而 `ACCCOD` 经 ISACC 决定这批货记进哪个
存货科目。按物理行序取第一条 = 存货科目由 DBF 的行顺序决定,与商品是什么货毫无关系。
70EXP 真账套里 162 个 STKTYP=0:146 个挂 ST10(→11-04-04-00 สินค้าสำเร็จรูป 成品),
行序第一条却是 ST01(→11-04-02-00 另一类成品)—— 自动建的品全落进少数派科目。故取**众数**:
账套里绝大多数真库存品用的那个科目,才是这套账"库存该记哪"的事实。

**为什么还要守门**:同一张 ISACC 里,ST02→52-08-00-00、ST03→53-05-02-00、ST05→53-03-02-00…
都是**费用**科目,ST06/ST07 是固定资产,ST00 是预付税。行序或众数一变,进货就把存货直接记成
费用/资产,而原先对 ACCCOD 只要求非空、零守门。判据建在真账套的 GLACC 上:
  ① `GLACC.GROUP` 必须是 '1'(资产)—— 费用是 '5'、收入 '4'、负债 '2'、权益 '3';
  ② 科目自身或其 PARENT 链上要出现存货类名(สินค้า/วัตถุดิบ/inventory/stock/goods…)。
两条都过才建档,否则 escalate 交会计 —— 宁可停下,也不静默把货记进费用。
70EXP 的 12 个 ST 码逐一验过此判据:只有 ST01/ST10 通过,ST00/ST02..ST09 全被挡下。

**会计为这个账套选过存货科目组时**(服务端「每个账套选一次」,用户 2026-09-28,随载荷
`stock_acccod` 传来):新建库存品一律挂所选的组,不再看众数 —— 科目规则由会计定一次、日常自动套用。
结构优先抄同组的真库存品;这组下还没有库存品就抄众数那一类的结构、只把 ACCCOD 换成所选。同样过守门。

**账里一条库存品都没有时**(新客户第一次卖库存):没有众数可取,回落抄一条服务品档
(STKTYP=3)拿结构,存货科目改用调用方传入的账套配置值,同样过守门。选不出存货科目 =
这套账压根没配存货科目组 —— 那是「选库存」剩下的唯一 escalate(StockAccGroupRequired)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import dbf

from . import dbf_schema as S
from . import stock_defaults
from . import stock_lookup

_MAX_PARENT_HOPS = 12  # 科目树深度上限(兼作坏数据成环的保险)
_ASSET_GROUP = "1"  # GLACC.GROUP:1=资产 2=负债 3=权益 4=收入 5=费用
# 回落模板取服务型:每套账都有(70EXP 有 81 条),且它是 Express 商品列表里唯一显示的非库存型。
_SERVICE_TYPE = "3"
_INVENTORY_WORDS = (
    "สินค้า",  # สินค้าคงเหลือ / สินค้าสำเร็จรูป
    "วัตถุดิบ",  # 原材料
    "inventory",
    "stock",
    "goods",
    "merchandise",
)


class InventoryAccountUnfit(Exception):
    """自动建库存档选中的存货科目不像存货科目 → 交会计,不静默建档。"""


class StockAccGroupRequired(InventoryAccountUnfit):
    """这套账既没有库存品可抄、调用方也没给存货科目组(ACCCOD)→ 建不出库存档。

    「选库存」剩下的唯一 escalate:存货科目组是账套配置,猜错就把货记进费用/收入,只能等
    会计配好。其余情形(有档没货、账里没这品)一律照卖,不再把用户堵在死胡同里。
    """


def pick(account_dir, clone_fields: Iterable[str], *, acccod: str = "") -> Dict:
    """取克隆模板:传入的存货科目组(会计为账套选的)优先,没传取账套里用得最多的那个组。

    账里一条库存品都没有 → 回落抄服务品档结构 + 传入的 `acccod`(见 _service_template)。
    选中的 ACCCOD 经 ISACC/GLACC 判不出是存货 → 抛 InventoryAccountUnfit。
    """
    rows = _stock_rows(account_dir, clone_fields)
    if not rows:
        return _service_template(account_dir, clone_fields, acccod)
    dominant = _dominant_acccod(rows)
    code = (acccod or "").strip() or dominant
    assert_inventory_account(account_dir, code)
    same = next((fields for c, fields in rows if c == code), None)
    if same is not None:
        return same
    # 所选的组下还没有库存品:结构抄众数那一类的真品,存货科目组换成所选(只借结构,同服务品回落)。
    fields = dict(next(f for c, f in rows if c == dominant))
    fields["ACCCOD"] = code
    return fields


def _service_template(account_dir, clone_fields: Iterable[str], acccod: str) -> Dict:
    """无库存品可抄时的回落:结构抄服务品档(STKTYP=3),存货科目取调用方传入值。

    只借结构 —— 服务品自己的 ACCCOD 指向收入/费用科目,照抄等于把存货记进费用,故必覆写。
    传入值照样过 assert_inventory_account:回落路不是守门的后门。
    连一条非库存档都没有的空账套 → 只带 ACCCOD 建,其余字段走 DBF 列默认(能建就别拦)。
    """
    code = (acccod or "").strip()
    if not code:
        raise StockAccGroupRequired(
            "这套账里没有任何库存品可抄,也没配存货科目组(ACCCOD)· "
            "请会计先在账套里配好存货科目组,再推这张销售票 · "
            "ยังไม่ได้ตั้งค่ากลุ่มบัญชีสินค้าคงเหลือในชุดบัญชีนี้ กรุณาให้ผู้ทำบัญชีตั้งค่าก่อน"
        )
    assert_inventory_account(account_dir, code)
    fields = _service_fields(account_dir, clone_fields)
    fields["ACCCOD"] = code
    return fields


def _service_fields(account_dir, clone_fields: Iterable[str]) -> Dict:
    """一条非库存档的结构字段(优先服务型 3,退而取任一非库存型)。一条都没有 → {}。"""
    fields = tuple(clone_fields)
    t = _open(account_dir, "STMAS")
    try:
        fallback: Dict = {}
        for r in t:
            if dbf.is_deleted(r):
                continue
            typ = str(_gv(r, "STKTYP"))
            if typ == _SERVICE_TYPE:
                return {f: _gv(r, f) for f in fields}
            if typ in stock_lookup.NONSTOCK_TYPES and not fallback:
                fallback = {f: _gv(r, f) for f in fields}
        return fallback
    finally:
        t.close()


def assert_inventory_account(account_dir, acccod: str) -> str:
    """ACCCOD → ISACC.ACCNUM01,并校验它确实是存货科目。返回该科目码;不合格抛。"""
    accnum = _isacc_inventory(account_dir, acccod)
    if not accnum:
        raise InventoryAccountUnfit(
            f"ISACC[{acccod}] 查不到存货科目(ACCNUM01 空)· 账套未配库存核算 · 交会计"
        )
    chain = _account_chain(account_dir, accnum)
    if not chain:
        raise InventoryAccountUnfit(f"存货科目 {accnum} 不在 GLACC 科目表里 · 交会计")
    group = str(chain[0][1] or "")
    if group != _ASSET_GROUP:
        raise InventoryAccountUnfit(
            f"ACCCOD {acccod} 的存货科目 {accnum}({chain[0][2]})在 GLACC 里属"
            f"第 {group} 类(非资产)· 自动建档会把存货记成费用/收入 · 交会计"
        )
    if not any(_looks_like_inventory(name) for _num, _grp, name in chain):
        raise InventoryAccountUnfit(
            f"ACCCOD {acccod} 的存货科目 {accnum}({chain[0][2]})及其上级都不像存货科目 · "
            f"交会计确认这批货该记哪个科目"
        )
    return accnum


def _dominant_acccod(rows: List[Tuple[str, Dict]]) -> str:
    """出现次数最多的 ACCCOD;并列时取物理行序靠前的那个(结果稳定可复现)。"""
    return stock_defaults.mode(code for code, _f in rows)


def _stock_rows(account_dir, clone_fields: Iterable[str]) -> List[Tuple[str, Dict]]:
    """全部带 ACCCOD 的 STKTYP=0 行 → [(ACCCOD, 克隆字段)],保持物理行序。"""
    fields = tuple(clone_fields)
    t = _open(account_dir, "STMAS")
    try:
        out: List[Tuple[str, Dict]] = []
        for r in t:
            if dbf.is_deleted(r) or str(_gv(r, "STKTYP")) != stock_lookup.STOCK_TYPE:
                continue
            code = _gv(r, "ACCCOD")
            if not code:  # 缺科目联动的档克隆出来也没法归 GL
                continue
            out.append((code, {f: _gv(r, f) for f in fields}))
        return out
    finally:
        t.close()


def _isacc_inventory(account_dir, acccod: str) -> str:
    t = _open(account_dir, "ISACC")
    try:
        for r in t:
            if not dbf.is_deleted(r) and _gv(r, "ACCCOD") == acccod:
                return _gv(r, "ACCNUM01")
        return ""
    finally:
        t.close()


def _account_chain(account_dir, accnum: str) -> List[Tuple[str, str, str]]:
    """科目 → [(科目码, GROUP, 名称)] 自身在前、逐级向上。科目不在表里返空。"""
    index = _glacc_index(account_dir)
    chain: List[Tuple[str, str, str]] = []
    cur = accnum
    seen = set()
    for _hop in range(_MAX_PARENT_HOPS):
        row = index.get(cur)
        if not row or cur in seen:
            break
        seen.add(cur)
        chain.append((cur, row["group"], row["name"]))
        cur = row["parent"]
        if not cur:
            break
    return chain


def _glacc_index(account_dir) -> Dict[str, Dict[str, str]]:
    path = Path(account_dir) / "GLACC.DBF"
    if not path.is_file():
        return {}
    t = dbf.Table(str(path), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        out: Dict[str, Dict[str, str]] = {}
        for r in t:
            if dbf.is_deleted(r):
                continue
            num = _gv(r, "ACCNUM")
            if num:
                out[num] = {
                    "group": str(_gv(r, "GROUP")),
                    "parent": _gv(r, "PARENT"),
                    # 两个名字列都看:泰文账套用 ACCNAM,英文科目表在 ACCNAM2
                    "name": " ".join(
                        x for x in (_gv(r, "ACCNAM"), _gv(r, "ACCNAM2")) if x
                    ),
                }
        return out
    finally:
        t.close()


def _looks_like_inventory(name: str) -> bool:
    text = (name or "").replace("\xa0", " ").lower()
    return any(w in text for w in _INVENTORY_WORDS)


def _open(account_dir, table: str):
    t = dbf.Table(str(Path(account_dir) / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    return t


def _gv(rec, field: str, default=""):
    try:
        v = rec[field]
    except Exception:
        return default
    return v.strip() if isinstance(v, str) else v
