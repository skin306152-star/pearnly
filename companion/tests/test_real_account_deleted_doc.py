# -*- coding: utf-8 -*-
r"""对**真 Express 账套副本**跑的「删了还能重推」闸 —— 合成表照不出这条路的全貌。

为什么必须真表:合成账套里的软删行是我们自己 dbf.delete() 出来的,形状理想。真账套里躺着
会计**真的在 Express 里删掉**的票(本副本 ARTRN 48 行 / 40 行有效 / 8 行软删),票号、客户码、
RECTYP 全是真的,连号规则也是真的。2026-07-25 现场那张 IV690531-001 就死在这类行上:幂等闸
认下软删行,返回「已存在,跳过」,一个字节都没写,而云端看到的是"成功"。

怎么跑:
    set PEARNLY_REAL_ACCT=<某个账套只读副本目录>
    python -m pytest tests/test_real_account_deleted_doc.py -q
没配或目录不在 → 整个模块 skip(CI 上没有客户账套,不能因此变红)。
用例一律先 copytree 到 tmp_path 再动,绝不写源目录。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import cdx_reindex  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion import dbf_schema as S  # noqa: E402

_SRC = os.environ.get("PEARNLY_REAL_ACCT", "")
pytestmark = pytest.mark.skipif(
    not (_SRC and (Path(_SRC) / "ARTRN.DBF").is_file()),
    reason="需要真账套只读副本:设 PEARNLY_REAL_ACCT=<含 ARTRN.DBF 的目录>",
)

_MAX_DOCNUM = 12  # ARTRN.DOCNUM C(12):票号超宽会退回自编号,那就照不出「新旧同号」


@pytest.fixture
def acct(tmp_path):
    """真账套的一次性工作副本(源目录只读)。"""
    dst = tmp_path / "test"
    shutil.copytree(_SRC, dst)
    return dst


@pytest.fixture(autouse=True)
def no_cdx_runner(monkeypatch):
    """副本里没有 Harbour runner 可跑 —— 索引重建/回查按"不可用"走(与生产回落路一致)。"""
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)


def _artrn_rows(acct: Path, *, deleted: bool):
    t = dbf.Table(str(acct / "ARTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [
            {
                "DOCNUM": (r.DOCNUM or "").strip(),
                "YOUREF": (r.YOUREF or "").strip(),
                "CUSCOD": (r.CUSCOD or "").strip(),
                "RECTYP": (r.RECTYP or "").strip(),
            }
            for r in t
            if dbf.is_deleted(r) is deleted
        ]
    finally:
        t.close()


def _a_deleted_invoice(acct: Path) -> dict:
    """账套里一张**会计真删过**的销售票(票号短到能直接当凭证号,才照得出新旧同号)。"""
    sales = set(S.SALES_RECTYP.values())
    for row in _artrn_rows(acct, deleted=True):
        if not (row["YOUREF"] and row["CUSCOD"] and row["RECTYP"] in sales):
            continue
        if len(row["YOUREF"].encode(S.CODEPAGE, "replace")) <= _MAX_DOCNUM:
            return row
    pytest.skip("该副本里没有软删的销售票,这条路无从下手")


def _payload(ref_no: str, cuscod: str) -> dict:
    return {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": ref_no,
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
    }


def _push(acct: Path, ref_no: str, cuscod: str):
    return WS.write_sale(
        _payload(ref_no, cuscod),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )


def _express_delete(acct: Path, docnum: str) -> None:
    """会计在 Express 里删掉一张单 —— 单头/销项税/GL 凭证一起打删除标记。"""
    for table, field in (
        ("ARTRN", "DOCNUM"),
        ("ISVAT", "DOCNUM"),
        ("GLJNL", "VOUCHER"),
        ("GLJNLIT", "VOUCHER"),
    ):
        t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
        t.open(dbf.READ_WRITE)
        try:
            for r in t:
                if not dbf.is_deleted(r) and (r[field] or "").strip() == docnum:
                    dbf.delete(r)
        finally:
            t.close()


def _live_with_ref(acct: Path, ref_no: str, cuscod: str) -> int:
    return sum(
        1
        for r in _artrn_rows(acct, deleted=False)
        if r["YOUREF"] == ref_no and r["CUSCOD"] == cuscod
    )


def test_the_account_really_carries_soft_deleted_invoices(acct):
    """取证:这套账里确实躺着会计删掉的票 —— 后面几条用例的前提,别空跑变绿。"""
    ghosts = _artrn_rows(acct, deleted=True)
    assert ghosts, "副本里没有软删行,本模块照不出东西"
    assert _artrn_rows(acct, deleted=False)


def test_an_invoice_the_accountant_deleted_can_be_pushed_again(acct):
    """现场原样:拿账套里真删过的那张票重推,必须真写进去。

    修前:_find_existing 在那条软删行上命中 → ok=True / skipped=True / 有效行一条没加。
    """
    ghost = _a_deleted_invoice(acct)
    ref, cus = ghost["YOUREF"], ghost["CUSCOD"]
    assert _live_with_ref(acct, ref, cus) == 0  # Express 里已经看不见这张票

    res = _push(acct, ref, cus)
    assert res.ok, res.error
    assert not res.skipped, "软删行被当成了「已存在」→ 报成功但一个字节没写"
    assert _live_with_ref(acct, ref, cus) == 1


def test_full_delete_and_repush_cycle_on_the_real_account(acct):
    """完整循环:推 → 会计在 Express 里删 → 重推。

    第二张的凭证号与那条软删旧行**同号**(销项 DOCNUM 就是票面票号),四道闸里但凡有一道
    把软删行当真,票要么假成功进不去,要么读回判不平被回滚。
    """
    live = _artrn_rows(acct, deleted=False)
    cus = next(r["CUSCOD"] for r in live if r["CUSCOD"])
    ref = "IV690531-001"  # 现场那张票的号;先确认这套账里没有
    assert _live_with_ref(acct, ref, cus) == 0

    first = _push(acct, ref, cus)
    assert first.ok and not first.skipped and first.docnum == ref

    _express_delete(acct, ref)
    assert _live_with_ref(acct, ref, cus) == 0

    again = _push(acct, ref, cus)
    assert again.ok, again.error
    assert not again.skipped
    assert again.docnum == ref
    assert _live_with_ref(acct, ref, cus) == 1
    assert len([r for r in _artrn_rows(acct, deleted=True) if r["DOCNUM"] == ref]) == 1
