# -*- coding: utf-8 -*-
"""小助手侧的重推场景钉(与 bridge/tests/test_purchase_reimport_scenarios 同一批场景)。

2026-07-27 Zihao 拍板:「不应该过度拦截,使用者自行会判断是否会重复,我们可以提示,但是不能
因此把推送流程拦在我们这边。」于是重投分两种处置 —— 系统自作主张的重投照挡,会计改了内容
主动再推则摆差异、由她定。桥侧同款断言;两侧同一载荷的结论一致性另由
bridge/tests/test_two_paths_agree 逐场景对拍。
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as W  # noqa: E402

from _synth_account import build, seed_fiscal_year  # noqa: E402

REF_NO = "TIV69/0640"


@pytest.fixture
def acct(tmp_path):
    d = build(tmp_path / "69EXP" / "test")
    seed_fiscal_year(d, 2026)
    return d


def _payload(base="1000.00", vat="70.00", total="1070.00", **over):
    p = {
        "doctype": "RR",
        "account_set": "test",
        "docdate_be": "690305",
        "vat_period_be": "690301",
        "ref_no": REF_NO,
        "supplier": {
            "code": "S001",
            "name": "บริษัท ปตท จำกัด",
            "tax_id": "0107544000108",
            "prename": "บริษัท",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": base,
        "vat_amount": vat,
        "total_amount": total,
        "lines": [
            {"acc": "51-03-00-00", "side": "D", "amount": base, "desc": "ซื้อ"},
            {"acc": "11-05-04-01", "side": "D", "amount": vat, "desc": "ภาษีซื้อ"},
            {"acc": "21-01-01-00", "side": "C", "amount": total, "desc": "เจ้าหนี้"},
        ],
    }
    p.update(over)
    return p


def _write(acct, **over):
    return W.write_purchase(
        _payload(**over),
        acct,
        allowed_sets=("test",),
        selected_dir=str(acct),
        make_backup=False,
    )


def _changed(**over):
    return dict(base="2000.00", vat="140.00", total="2140.00", **over)


def _aptrn_live(acct) -> int:
    t = dbf.Table(str(Path(acct) / "APTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return sum(1 for _ in S.live_rows(t))
    finally:
        t.close()


def test_system_replay_is_still_skipped_and_writes_nothing(acct):
    """网络重发/自动重试/连点两下 → 照挡。她没想推第二次,挡住是帮她。"""
    first = _write(acct)
    before = _aptrn_live(acct)

    again = _write(acct)
    assert again.ok and again.skipped and again.written is False
    assert again.docnum == first.docnum
    assert _aptrn_live(acct) == before
    assert first.docnum in (again.notes or {}).get("duplicate_of", "")


def test_changed_amount_is_laid_out_instead_of_silently_skipped(acct):
    """靶场 S3 的根治:1070 改 2140 → 零写入 + 结构化差异,不再报成功。"""
    first = _write(acct)
    before = _aptrn_live(acct)

    again = _write(acct, **_changed())
    assert not again.ok and again.error_code == W.ERR_DUPLICATE_DIFFERS
    assert again.written is False and _aptrn_live(acct) == before
    totals = next(f for f in again.duplicate["fields"] if f["field"] == "total_amount")
    assert (totals["in_erp"], totals["incoming"]) == ("1,070.00", "2,140.00")
    assert again.duplicate["docnum"] == first.docnum


def test_confirmed_repush_really_writes_the_second_document(acct):
    """「知道了,还是推」→ 真写新单。选哪个都执行。"""
    first = _write(acct)
    before = _aptrn_live(acct)

    again = _write(acct, **_changed(duplicate_confirmed=True))
    assert again.ok, again.error
    assert again.written is True and again.docnum != first.docnum
    assert _aptrn_live(acct) == before + 1
    assert first.docnum in (again.notes or {}).get("duplicate_confirmed", "")


def test_confirmed_replay_after_a_lost_ack_writes_nothing_more(acct):
    """确认落了第二张之后 ack 丢了 → 云端把同一份载荷(确认位还在)再投一遍。

    幂等钥匙下此刻有两张,取第一张就永远拿旧的那张 A 去比 —— 比出差异、确认位又还在,于是
    再落一张;丢几次 ack 就多几张,无上界。取最后一张才自愈:重投比的是刚写的那张,内容一致。
    桥侧同款断言 + 销项那一路在 bridge/tests/test_confirmed_replay_is_idempotent。
    """
    _write(acct)
    confirmed = _write(acct, **_changed(duplicate_confirmed=True))
    after_confirm = _aptrn_live(acct)

    for i in range(3):
        replay = _write(acct, **_changed(duplicate_confirmed=True))
        assert replay.ok and replay.skipped, f"第 {i + 1} 次重投没被幂等认出来"
        assert replay.written is False
        assert replay.docnum == confirmed.docnum  # 报的是刚写那张,不是被它盖过去的旧单
        assert _aptrn_live(acct) == after_confirm, f"第 {i + 1} 次重投多写了一张"


def test_replay_pointing_at_its_own_fresh_write_is_not_a_hard_failure(acct):
    """在产老 exe 的闸序(prior→幂等)在这里会返回 PRIOR_DOC_STILL_IN_ERP,
    指挥会计去删掉一张本来就该留着的好单 —— 闸序统一到桥之后不再发生。"""
    first = _write(acct)
    before = _aptrn_live(acct)

    again = _write(acct, prior_docnum=first.docnum)
    assert again.ok and again.skipped and again.written is False
    assert again.docnum == first.docnum
    assert _aptrn_live(acct) == before


def test_prior_doc_still_alive_is_refused_but_points_at_the_way_out(acct):
    first = _write(acct)
    before = _aptrn_live(acct)

    again = _write(acct, ref_no="TIV69/0641", prior_docnum=first.docnum)
    assert not again.ok and again.error_code == W.ERR_PRIOR_DOC_EXISTS
    assert again.written is False and _aptrn_live(acct) == before
    assert again.duplicate["confirm_param"] == "duplicate_confirmed"

    ok = _write(
        acct,
        ref_no="TIV69/0641",
        prior_docnum=first.docnum,
        duplicate_confirmed=True,
    )
    assert ok.ok and ok.written is True and ok.docnum != first.docnum


def test_money_in_the_diff_is_decimal_not_float(acct):
    _write(acct)
    res = _write(acct, **_changed())
    for f in res.duplicate["fields"]:
        for shown in (f["in_erp"], f["incoming"]):
            assert Decimal(shown.replace(",", "")).as_tuple().exponent == -2
