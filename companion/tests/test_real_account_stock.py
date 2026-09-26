# -*- coding: utf-8 -*-
r"""对**真 Express 账套副本**跑的库存建档回归闸 —— 这三条合成表照不出来。

为什么必须真表:合成表是我们自己按理想形状建的,列宽、单位码表、科目表全是"刚好对得上"的
版本。真账套里 `STMAS.STKDES` 是 C(50)(合成表曾按 C(40) 建)、单位码是 `ถง` 不是 `ถั`、
162 个库存品里 146 个挂 ST10 而行序第一条挂 ST01 —— 每一条缺陷都只在这些差异上现形。

怎么跑:
    set PEARNLY_REAL_ACCT=<某个账套只读副本目录>   # 目录里直接是 STMAS.DBF/ISACC.DBF/…
    python -m pytest tests/test_real_account_stock.py -q
没配或目录不在 → 整个模块 skip(CI 上没有客户账套,不能因此变红)。
用例一律先 copytree 到 tmp_path 再动,绝不写源目录。
"""

from __future__ import annotations

import datetime as dt
import os
import shutil
import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import cdx_reindex  # noqa: E402
from companion import dbf_meta  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import product_master as PM  # noqa: E402
from companion import stock_lookup as SL  # noqa: E402
from companion import stock_purchase as SP  # noqa: E402
from companion import stock_sale as SS  # noqa: E402
from companion import stock_template as ST  # noqa: E402
from companion import table_specs  # noqa: E402
from companion import unit_code  # noqa: E402

_SRC = os.environ.get("PEARNLY_REAL_ACCT", "")
pytestmark = pytest.mark.skipif(
    not (_SRC and (Path(_SRC) / "STMAS.DBF").is_file()),
    reason="需要真账套只读副本:设 PEARNLY_REAL_ACCT=<含 STMAS.DBF 的目录>",
)

_DATE = dt.date(2026, 1, 15)
_NEW_ITEM = "PEARNLY REGRESSION ITEM"  # 真账套里不存在的品名(用例开头会断言)
_UNIT = "ถัง"


@pytest.fixture
def acct(tmp_path):
    """真账套的一次性工作副本(源目录只读)。"""
    dst = tmp_path / "test"
    shutil.copytree(_SRC, dst)
    return dst


def _rows(acct, table, fields):
    """只取要用的列 —— 真表里有历史坏日期值,整行读会在无关列上炸(生产代码同样逐列容错取)。"""
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [{f: _cell(r, f) for f in fields} for r in t if not dbf.is_deleted(r)]
    finally:
        t.close()


def _cell(rec, field):
    try:
        v = rec[field]
    except Exception:
        return ""
    return v.strip() if isinstance(v, str) else v


_STMAS_FIELDS = (
    "STKCOD",
    "STKDES",
    "STKTYP",
    "ACCCOD",
    "STKGRP",
    "QUCOD",
    "MREMBAL",
    "MREMVAL",
)
_GL_FIELDS = ("VOUCHER", "SEQIT", "ACCNUM", "TRNTYP", "AMOUNT")


def _stock_rows(acct):
    rows = _rows(acct, "STMAS", _STMAS_FIELDS)
    return [r for r in rows if str(r["STKTYP"]) == "0"]


def _longest_named_empty_item(acct):
    """副本里品名最长、且零/负库存的库存品 —— 长名走建档路时最容易被截断误判的那一条。"""
    cands = [
        r for r in _stock_rows(acct) if r["STKDES"] and float(r["MREMBAL"] or 0) <= 0
    ]
    if not cands:
        pytest.skip("该副本没有零库存的库存品,长名用例无从下手")
    row = max(cands, key=lambda r: len(r["STKDES"]))
    if len(row["STKDES"]) <= 40:
        pytest.skip("该副本没有超过 40 字的库存品名,照不出截断口径缺陷")
    return row


def _buy(acct, name, qty=100, amount="1000.00", docnum="RRREG-001", unit=_UNIT):
    return SP.write_stock_in_lines(
        acct,
        docnum,
        _DATE,
        [{"name": name, "qty": str(qty), "amount": amount, "unit": unit}],
        "REGSUP",
    )


def _sell(acct, name, qty=50, amount="1500.00", docnum="IVREG-001"):
    return SS.write_stock_lines(
        acct,
        docnum,
        _DATE,
        [{"name": name, "qty": str(qty), "unit_price": "30", "amount": amount}],
        "REGCUS",
    )


# ── P0 品名截断口径 ────────────────────────────────────────────────────────


def test_long_name_purchase_lands_in_the_customers_own_item(acct):
    """>40 字的既有品进货 → 入到客户那条真档,不新建重复档。

    修前:建档侧按写死的 40 字截了再比,库里是 50 字原值,永远不等 → 判成新品。
    """
    target = _longest_named_empty_item(acct)
    before = len(_stock_rows(acct))
    modes, by_acc = _buy(acct, target["STKDES"])
    assert modes[0]["stkcod"] == target["STKCOD"]
    assert modes[0]["created"] is False
    assert len(_stock_rows(acct)) == before  # 一条新档都没建
    after = [r for r in _stock_rows(acct) if r["STKCOD"] == target["STKCOD"]][0]
    assert float(after["MREMBAL"]) == 100.0
    assert float(after["MREMVAL"]) == 1000.0
    assert sum(by_acc.values()) == 1000


def test_long_name_item_sells_with_real_cogs_after_purchase(acct):
    """闭环另一半:同一个长名随后能卖,按移动均价结转 COGS(修前这里 STOCK_ITEM_NOT_FOUND)。"""
    target = _longest_named_empty_item(acct)
    _buy(acct, target["STKDES"])
    modes, cogs_rows = _sell(acct, target["STKDES"])
    assert modes[0]["stkcod"] == target["STKCOD"]
    assert (
        cogs_rows
        and sum(a for _s, _acc, t, a in cogs_rows if t == S.TRNTYP_DEBIT) == 500
    )
    row = [r for r in _stock_rows(acct) if r["STKCOD"] == target["STKCOD"]][0]
    assert float(row["MREMBAL"]) == 50.0
    assert float(row["MREMVAL"]) == 500.0


def test_pre_fix_hardcoded_width_recreates_the_duplicate_shell(acct, monkeypatch):
    """变异:把列宽退回修前写死的 40 → 必须重现「货入进新建的重复空壳」。

    这是本用例咬得住的证据 —— 不把宽度从表头现取,同一份真表立刻回到错账。
    """
    target = _longest_named_empty_item(acct)
    monkeypatch.setattr(dbf_meta, "field_width", _pinned_width(40))
    before = len(_stock_rows(acct))
    modes, _ = _buy(acct, target["STKDES"])
    assert modes[0]["created"] is True
    assert modes[0]["stkcod"] != target["STKCOD"]
    assert len(_stock_rows(acct)) == before + 1  # 建出了重复档
    untouched = [r for r in _stock_rows(acct) if r["STKCOD"] == target["STKCOD"]][0]
    assert float(untouched["MREMBAL"]) == 0.0  # 客户那条真档一动没动


def test_pre_fix_split_lookup_keys_strand_the_goods_in_a_duplicate(acct, monkeypatch):
    """变异(分两段,照搬修前的两条代码):建档侧截 40、卖货侧不截 → 货和账分家。

    真账套验收当时的症状是销售死锁(STOCK_ITEM_NOT_FOUND);改判负库存放行后症状更隐蔽:
    进的货躺在重复空壳里(+100),卖的却是客户那条真档(−50),两边都不报错。口径分家
    仍然是错账,只是不再自己撞墙 —— 所以这条用例改咬「货在哪条档上」。
    """
    target = _longest_named_empty_item(acct)
    monkeypatch.setattr(dbf_meta, "field_width", _pinned_width(40))
    modes, _ = _buy(acct, target["STKDES"])  # 货进了重复空壳
    shell = modes[0]["stkcod"]
    assert shell != target["STKCOD"]
    monkeypatch.setattr(SL, "match_key", lambda _d, name: SL.norm(name))  # 卖货侧不截
    sold, _cogs = _sell(acct, target["STKDES"])
    assert sold[0]["stkcod"] == target["STKCOD"]  # 卖的是客户那条真档
    rows = {r["STKCOD"]: r for r in _stock_rows(acct)}
    assert float(rows[shell]["MREMBAL"]) == 100.0
    assert float(rows[target["STKCOD"]]["MREMBAL"]) == -50.0


def test_both_lookup_paths_agree_on_every_stock_item(acct):
    """全表不变式:建档查重与卖货查货,对同一个名字要么都命中要么都不命中。

    口径分家就是死锁的根,所以拿账套里全部库存品名逐个对一遍,不靠抽样。
    """
    seen = set()
    for row in _stock_rows(acct):
        name = row["STKDES"]
        if not name or name in seen:
            continue
        seen.add(name)
        by_code = PM.find_stock_item_code(acct, name)
        by_stock = SS.find_stock_item(acct, name)
        assert (by_code is None) == (by_stock is None), name
        if by_code:
            assert by_stock["stkcod"] == by_code, name
    assert len(seen) > 50  # 真副本才有的规模,防止空跑变绿


# ── P1 单位码 ─────────────────────────────────────────────────────────────


def test_unit_name_prefix_is_not_a_legal_code_in_this_account(acct):
    """取证:修前写的 `unit[:2]` 在这套账的合法单位码里根本不存在。"""
    legal = unit_code.legal_codes(acct)
    assert len(legal) > 10  # 真账套的单位表
    assert _UNIT[:2] not in legal
    assert unit_code.resolve(acct, _UNIT) in legal


def test_created_item_gets_a_legal_unit_code(acct):
    assert PM.find_stock_item_code(acct, _NEW_ITEM) is None
    modes, _ = _buy(acct, _NEW_ITEM)
    row = [r for r in _stock_rows(acct) if r["STKCOD"] == modes[0]["stkcod"]][0]
    assert row["QUCOD"] == unit_code.resolve(acct, _UNIT)
    assert row["QUCOD"] in unit_code.legal_codes(acct)
    assert row["QUCOD"] != _UNIT[:2]


def test_master_unit_matches_the_stock_card_unit(acct):
    """主档 QUCOD 与库存卡 TQUCOD 必须是同一个单位 —— 修前一个对一个错。"""
    modes, _ = _buy(acct, _NEW_ITEM)
    master = [r for r in _stock_rows(acct) if r["STKCOD"] == modes[0]["stkcod"]][0]
    cards = _rows(acct, "STCRD", ("DOCNUM", "TQUCOD"))
    card = [r for r in cards if r["DOCNUM"] == "RRREG-001"][0]
    assert master["QUCOD"] == card["TQUCOD"]


# ── 票面没给单位 / 类别 / 建档日:真机 69EXP 建出的"薄档"三件 ────────────────


def _account_mode(acct, field):
    """这套账 STKTYP=0 在某列上的众数(测试自己数一遍,不复用被测代码的答案)。"""
    counts = {}
    for r in _stock_rows(acct):
        v = r[field]
        if v:
            counts[v] = counts.get(v, 0) + 1
    return max(counts, key=lambda k: counts[k]) if counts else ""


def test_missing_invoice_unit_takes_this_accounts_most_common_unit(acct):
    """票面 unit 为空(真机 69EXP 就是这样)→ 单位不再留白,取本账套最常用的那个码。"""
    want = _account_mode(acct, "QUCOD")
    assert want and want in unit_code.legal_codes(acct)
    modes, _ = _buy(acct, _NEW_ITEM, unit="")
    row = [r for r in _stock_rows(acct) if r["STKCOD"] == modes[0]["stkcod"]][0]
    assert row["QUCOD"] == want
    # 猜出来的必须回传,否则会计在推送日志上看不见「这个单位是系统填的」
    assert modes[0]["unit_guessed"] is True
    assert modes[0]["unit_code"] == want


def test_created_item_group_takes_this_accounts_most_common_group(acct):
    want = _account_mode(acct, "STKGRP")
    assert want  # 真副本必有众数(70EXP 是 05 占 144/162)
    modes, _ = _buy(acct, _NEW_ITEM, unit="")
    row = [r for r in _stock_rows(acct) if r["STKCOD"] == modes[0]["stkcod"]][0]
    assert row["STKGRP"] == want


def test_four_unit_columns_agree_on_every_stock_item_including_the_new_one(acct):
    """真表不变式:单位四列永远同一个码。新建的品也得守,否则造出真账里不存在的形状。"""
    cols = ("QUCOD", "CQUCOD", "PQUCOD", "SQUCOD")
    before = [
        r for r in _rows(acct, "STMAS", ("STKTYP", *cols)) if str(r["STKTYP"]) == "0"
    ]
    assert len(before) > 50 and all(len({r[c] for c in cols}) == 1 for r in before)
    modes, _ = _buy(acct, _NEW_ITEM, unit="")
    after = [
        r
        for r in _rows(acct, "STMAS", ("STKCOD", *cols))
        if r["STKCOD"] == modes[0]["stkcod"]
    ][0]
    assert len({after[c] for c in cols}) == 1


def test_master_creation_date_is_today_not_the_document_date(acct):
    """主档审计列记真实建档日 —— 真机上这里写成了发票日 2026-05-31,查不出档是哪天建的。"""
    modes, _ = _buy(acct, _NEW_ITEM, unit="")
    row = _rows(acct, "STMAS", ("STKCOD", "CREDAT", "CHGDAT"))
    made = [r for r in row if r["STKCOD"] == modes[0]["stkcod"]][0]
    assert made["CREDAT"] == dt.date.today()
    assert made["CHGDAT"] == dt.date.today()
    assert made["CREDAT"] != _DATE  # _DATE 是单据日期


# ── P1 存货科目 ───────────────────────────────────────────────────────────


def test_template_takes_the_dominant_inventory_account(acct):
    """模板取众数科目,不取物理行序第一条。"""
    stock = [r for r in _stock_rows(acct) if r["ACCCOD"]]
    counts = {}
    for r in stock:
        counts[r["ACCCOD"]] = counts.get(r["ACCCOD"], 0) + 1
    dominant = max(counts, key=lambda c: counts[c])
    assert ST.pick(acct, ("ACCCOD", "STKGRP"))["ACCCOD"] == dominant
    if stock[0]["ACCCOD"] != dominant:  # 本副本正是这种形状(行序第一条是少数派)
        assert counts[stock[0]["ACCCOD"]] < counts[dominant]


def test_created_item_inventory_account_is_really_inventory(acct):
    """新建的品落到的存货科目,必须经 GLACC 判定确是存货(资产 + 存货类科目树)。"""
    modes, by_acc = _buy(acct, _NEW_ITEM)
    row = [r for r in _stock_rows(acct) if r["STKCOD"] == modes[0]["stkcod"]][0]
    inv = ST.assert_inventory_account(acct, row["ACCCOD"])
    assert list(by_acc) == [inv]
    chain = ST._account_chain(acct, inv)
    assert chain[0][1] == "1"  # GLACC.GROUP:资产
    assert any(ST._looks_like_inventory(name) for _n, _g, name in chain)


def test_expense_backed_acccods_are_rejected(acct):
    """账套里那些联动到费用科目的 ACCCOD,一个都不许当库存模板用。"""
    glacc = {r["ACCNUM"]: r for r in _rows(acct, "GLACC", ("ACCNUM", "GROUP"))}
    expense = [
        r["ACCCOD"]
        for r in _rows(acct, "ISACC", ("ACCCOD", "ACCNUM01"))
        if r["ACCCOD"] and str(glacc.get(r["ACCNUM01"], {}).get("GROUP", "")) == "5"
    ]
    if not expense:
        pytest.skip("该副本的 ISACC 没有联动到费用科目的项")
    for acccod in expense:
        with pytest.raises(ST.InventoryAccountUnfit):
            ST.assert_inventory_account(acct, acccod)


# ── 零库存账套:选了库存也要落得下去 ──────────────────────────────────────


@pytest.fixture
def zero_stock_acct(acct):
    """副本里把 STKTYP=0 全删 —— 「客户账里一件库存品都没有」的零起点。

    返回 (账套, 存货科目组):科目组在删档前从这套账自己的众数取,模拟云端载荷带来的账套配置。
    """
    acccod = ST.pick(acct, ("ACCCOD",))["ACCCOD"]
    t = dbf.Table(str(acct / "STMAS.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if not dbf.is_deleted(r) and str(_cell(r, "STKTYP")) == "0":
                dbf.delete(r)
    finally:
        t.close()
    assert not _stock_rows(acct)
    return acct, acccod


def _sale_payload(cuscod: str, name: str, acccod: str = ""):
    """acccod 走真载荷键 stock_acccod —— 别在 SalesEntry 上直接赋值,
    那样会把「载荷→entry」这段透传测没(字段名对不上就是静默失效)。"""
    return {
        "direction": "sales",
        "stock_acccod": acccod,
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690115",
        "vat_period_be": "690101",
        "ref_no": "IVREG-002",
        "customer": {"code": cuscod, "name": "", "tax_id": "", "prename": ""},
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "lines": [
            {"acc": "11-02-01-00", "side": "D", "amount": "1070.00", "desc": "ลูกหนี้"},
            {"acc": "41-01-00-00", "side": "C", "amount": "1000.00", "desc": "รายได้"},
            {"acc": "11-05-04-02", "side": "C", "amount": "70.00", "desc": "ภาษีขาย"},
        ],
        "items": [
            {
                "name": name,
                "qty": "5",
                "unit_price": "200",
                "amount": "1000.00",
                "unit": _UNIT,
                "item_mode": "stock_sale",
            }
        ],
        "items_status": "ok",
        "items_account": "41-01-00-00",
    }


def test_zero_stock_account_takes_the_sale_without_inventing_cost(
    zero_stock_acct, monkeypatch
):
    """一件库存品都没有的账套推销售票:建档 + 卖成负 + 不结转成本 + GL 仍平。

    这是「选了库存就一定能落」的下限场景 —— 修前它必卡 STOCK_ITEM_NOT_FOUND。
    """
    acct, acccod = zero_stock_acct
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)
    cuscod = _rows(acct, "ARMAS", ("CUSCOD",))[0]["CUSCOD"]

    res = WS.write_sale(
        _sale_payload(cuscod, _NEW_ITEM, acccod),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )
    assert res.ok, res.error

    # 商品出现在 STMAS(真库存型 · 挂账套配置的存货科目组)· 结存为负 · 货值 0
    created = [r for r in _stock_rows(acct) if r["STKDES"] == _NEW_ITEM]
    assert len(created) == 1
    assert created[0]["ACCCOD"] == acccod
    assert float(created[0]["MREMBAL"]) == -5.0
    assert float(created[0]["MREMVAL"] or 0) == 0.0
    assert res.line_modes[0]["created"] is True
    assert res.line_modes[0]["reason"] == SS.REASON_NEW_ITEM

    # 无 COGS 分录(没有成本基础,绝不拿售价 200 倒推)· GL 借贷仍平
    gl = [r for r in _rows(acct, "GLJNLIT", _GL_FIELDS) if r["VOUCHER"] == res.docnum]
    assert not [r for r in gl if r["SEQIT"] in (SS.COGS_SEQIT_DR, SS.COGS_SEQIT_CR)]
    dr = sum(Decimal(str(r["AMOUNT"])) for r in gl if r["TRNTYP"] == S.TRNTYP_DEBIT)
    cr = sum(Decimal(str(r["AMOUNT"])) for r in gl if r["TRNTYP"] == S.TRNTYP_CREDIT)
    assert dr == cr == Decimal("1070.00")


def test_zero_stock_account_without_account_group_escalates(
    zero_stock_acct, monkeypatch
):
    """同一场景但账套没配存货科目组 → 唯一剩下的 escalate,且不留半张单。"""
    acct, _acccod = zero_stock_acct
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)
    cuscod = _rows(acct, "ARMAS", ("CUSCOD",))[0]["CUSCOD"]

    res = WS.write_sale(
        _sale_payload(cuscod, _NEW_ITEM),  # 载荷不带 stock_acccod = 账套没配
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=True,
    )
    assert not res.ok
    assert "ACCCOD" in res.error
    assert not [r for r in _stock_rows(acct) if r["STKDES"] == _NEW_ITEM]


# ── 合成表 vs 真表 ────────────────────────────────────────────────────────


# 本批之前就存在的列宽漂移,逐条记在案(不是"忽略",是"另立条目"):
#   APMAS.ADDR03 真表 C(20) 而 master_create._ADDR_WIDTHS 按 30 切 —— 会写溢出,另派单修;
#   APTRN/ARTRN 的 DEPCOD/BILNUM 是单据头的列,不在本批(库存建档)范围内。
# 闸对**新增**漂移照咬,只是不让既有欠账把本批堵死。
_KNOWN_SPEC_DRIFT = {
    ("APTRN", "DEPCOD"),
    ("APTRN", "BILNUM"),
    ("ARTRN", "DEPCOD"),
    ("ARTRN", "BILNUM"),
    ("APMAS", "ADDR03"),
}


def test_synthetic_specs_do_not_drift_from_the_real_columns(acct):
    """合成表规格的列宽必须与真表一致 —— 漂移一次,整类截断缺陷就再也照不出来。

    这三条缺陷能活到真账套验收才暴露,根因就是合成表 STKDES 建成了 C(40) 而真表是 C(50)。
    """
    drift = []
    for table, spec in table_specs.TABLE_SPECS.items():
        if not (acct / f"{table}.DBF").is_file():
            continue
        for field, width in _declared(spec).items():
            if (table, field) in _KNOWN_SPEC_DRIFT:
                continue
            try:
                real = dbf_meta.field_width(acct, table, field)
            except Exception:
                continue  # 真表没这列(合成表只建写入器用得上的子集)
            if real != width:
                drift.append(f"{table}.{field}: 合成 {width} vs 真表 {real}")
    assert not drift, "; ".join(drift)


def test_known_spec_drift_list_stays_honest(acct):
    """记在案的漂移必须还真的漂着 —— 修好了就从清单里删,别让清单变成万能借口。"""
    stale = []
    for table, field in _KNOWN_SPEC_DRIFT:
        if not (acct / f"{table}.DBF").is_file():
            continue
        declared = _declared(table_specs.TABLE_SPECS[table]).get(field)
        if declared == dbf_meta.field_width(acct, table, field):
            stale.append(f"{table}.{field}")
    assert not stale, f"已对齐,请从 _KNOWN_SPEC_DRIFT 删除:{stale}"


def _declared(spec: str):
    """ "STKDES C(50); TRNQTY B" → {列: 宽度}(只挑定长字符列,数值/日期列宽度无意义)。"""
    out = {}
    for part in spec.split(";"):
        bits = part.strip().split()
        if len(bits) == 2 and bits[1].startswith("C(") and bits[1].endswith(")"):
            out[bits[0]] = int(bits[1][2:-1])
    return out


def _pinned_width(pinned: int):
    """只把 STKDES 宽度钉成修前那个常量,其余列仍读真表(变异要精准,别误伤别的列)。"""
    original = dbf_meta.field_width

    def width(account_dir, table, field):
        if (table, field) in (("STMAS", "STKDES"), ("STMAS", "STKDES2")):
            return pinned
        return original(account_dir, table, field)

    return width
