# -*- coding: utf-8 -*-
"""推送失败现场证据包 —— 读回校验/明细行数/明细合计失败时,结构化现场随 ack meta.evidence
回云端,支持/会计不用再让客户把屏幕上的红字一个字一个字抄回来。

证据只在失败路径组装(成功路径 DbfWriteResult.evidence 恒 None,零开销)。错误消息文本
一字不改 —— 云端按文本 match 的既有断言(test_soft_deleted_rows 的 "GL not balanced" /
"line count 4")依赖它,evidence 只能是附加参数。
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import dbf_detail as DD  # noqa: E402
from companion import dbf_schema as S  # noqa: E402
from companion import dbf_writer as WP  # noqa: E402
from companion import dbf_sales as WS  # noqa: E402
from companion.worker import _push_meta  # noqa: E402
from companion.write_result import DbfWriteResult  # noqa: E402

from _synth_account import seed_fiscal_year  # noqa: E402


@pytest.fixture
def datat(tmp_path):
    """空 DATAT 合成账套(采购 + 销项两套表族)—— 照抄 test_soft_deleted_rows。"""
    d = tmp_path / "70EXP" / "test"
    d.mkdir(parents=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(
            str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE
        )
        t.open(dbf.READ_WRITE)
        t.close()
    seed_fiscal_year(d, 2026)
    return d


def _seed(acct: Path, table: str, row: dict, *, deleted: bool = False) -> None:
    t = dbf.Table(str(acct / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(row)
        if deleted:
            dbf.delete(t[-1])
    finally:
        t.close()


def _stcrd(datat, docnum: str, seq: str, netval: float, *, deleted: bool = False):
    _seed(
        datat,
        "STCRD",
        {
            "DOCNUM": docnum,
            "SEQNUM": seq,
            "DOCDAT": dt.date(2026, 5, 31),
            "TRNQTY": 1.0,
            "NETVAL": netval,
        },
        deleted=deleted,
    )


def _sale(**over):
    p = {
        "direction": "sales",
        "doctype": "IV",
        "account_set": "DATAT",
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": "IV690531-001",
        "customer": {
            "code": "C001",
            "name": "ลูกค้า ทดสอบ",
            "tax_id": "0105551234567",
            "prename": "บริษัท",
        },
        "vat_rate": 7.0,
        "base_amount": "9000.00",
        "vat_amount": "630.00",
        "total_amount": "9630.00",
        "lines": [
            {"acc": "11-03-01-00", "side": "D", "amount": "9630.00", "desc": "AR"},
            {"acc": "41-01-00-00", "side": "C", "amount": "9000.00", "desc": "รายได้"},
            {"acc": "11-05-04-02", "side": "C", "amount": "630.00", "desc": "ภาษีขาย"},
        ],
    }
    p.update(over)
    return p


def _sale_items(**over):
    return _sale(
        items=[
            {"name": "สินค้า A", "qty": "2", "unit_price": "3000", "amount": "6000.00"},
            {"name": "สินค้า B", "qty": "1", "unit_price": "3000", "amount": "3000.00"},
        ],
        items_status="ok",
        items_account="41-01-00-00",
        **over,
    )


def _push_sale(acct, payload=None):
    return WS.write_sale(
        payload or _sale(),
        acct,
        allowed_sets=("DATAT",),
        selected_dir=str(acct),
        make_backup=False,
    )


# ── ① verify_detail 行数不符 → evidence 完整 ─────────────────────────────────


def test_detail_line_count_mismatch_carries_full_evidence(datat):
    """6 活行 + 2 同号软删行,期望 8 行 → detail_mismatch 证据齐全(前 5 行摘要 + 软删数)。"""
    for seq, amt in (("1", 60.0), ("2", 40.0), ("3", 30.0), ("4", 25.0), ("5", 20.0), ("6", 15.0)):
        _stcrd(datat, "RR690531-001", seq, amt)
    _stcrd(datat, "RR690531-001", "7", 10.0, deleted=True)
    _stcrd(datat, "RR690531-001", "8", 5.0, deleted=True)

    with pytest.raises(DD.DetailVerifyError) as ei:
        DD.verify_detail(datat, "RR690531-001", Decimal("100.00"), 8)
    e = ei.value
    assert str(e) == "STCRD line count 6 != 8"  # 消息文本一字未改
    ev = e.evidence
    assert ev["kind"] == "detail_mismatch"
    assert ev["table"] == "STCRD"
    assert ev["docnum"] == "RR690531-001"
    assert ev["expected_lines"] == 8
    assert ev["found_lines"] == 6
    assert ev["deleted_count"] == 2
    assert len(ev["existing_rows"]) == 5  # 只带前 5 行,防 payload 爆肥
    assert ev["existing_rows"][0] == {
        "seq": "1",
        "date": "2026-05-31",
        "qty": 1.0,
        "amount": 60.0,
    }
    json.dumps(ev)  # 整包可 JSON 序列化(随 ack meta 上云)


def test_detail_sum_mismatch_carries_line_sum_and_base(datat):
    """行数对但合计不对 → detail_sum_mismatch,line_sum/base 两数。"""
    _stcrd(datat, "RR690531-001", "1", 60.0)
    _stcrd(datat, "RR690531-001", "2", 40.0)

    with pytest.raises(DD.DetailVerifyError) as ei:
        DD.verify_detail(datat, "RR690531-001", Decimal("150.00"), 2)
    ev = ei.value.evidence
    assert ev["kind"] == "detail_sum_mismatch"
    assert ev["line_sum"] == 100.0
    assert ev["base"] == 150.0
    assert ev["docnum"] == "RR690531-001"
    json.dumps(ev)


# ── ② _WriteError 双参/单参旧用法不回归 ────────────────────────────────────


def test_write_error_two_arg_usage_is_unchanged():
    e = WP._WriteError("APTRN row missing or NETAMT mismatch", WP.ERR_VERIFY)
    assert e.args == ("APTRN row missing or NETAMT mismatch", WP.ERR_VERIFY)
    assert e.evidence is None
    assert WP._error_code(e) == WP.ERR_VERIFY


def test_write_error_single_arg_still_maps_to_generic_code():
    """新增可选参不得挤占 args —— 单参旧用法仍落通用码 ERR_WRITE。"""
    e = WP._WriteError("boom")
    assert len(e.args) == 1
    assert str(e) == "boom"
    assert WP._error_code(e) == WP.ERR_WRITE


# ── ③ DbfWriteResult.evidence 缺省 None 且 JSON 可序列化 ─────────────────────


def test_dbf_write_result_evidence_defaults_none_and_is_json_serializable():
    r = DbfWriteResult(False, error_code="DBF_WRITE_FAILED", error="boom")
    assert r.evidence is None
    json.dumps(r.__dict__)  # 缺省 evidence 也要能整包进 JSON(云端序列化 ack meta)

    ev = {
        "kind": "detail_mismatch",
        "table": "STCRD",
        "docnum": "RR690531-001",
        "expected_lines": 8,
        "found_lines": 6,
        "deleted_count": 2,
        "existing_rows": [{"seq": "1", "date": "2026-05-31", "qty": 1.0, "amount": 60.0}],
    }
    r2 = DbfWriteResult(False, error_code="READBACK_VERIFY_FAILED", evidence=ev)
    assert json.loads(json.dumps(r2.__dict__))["evidence"] == ev


# ── ④ _push_meta 带 evidence ────────────────────────────────────────────────


def test_push_meta_carries_evidence():
    ev = {
        "kind": "detail_mismatch",
        "table": "STCRD",
        "docnum": "RR690531-001",
        "expected_lines": 8,
        "found_lines": 6,
        "deleted_count": 2,
        "existing_rows": [{"seq": "1", "date": "2026-05-31", "qty": 1.0, "amount": 60.0}],
    }
    res = DbfWriteResult(
        False,
        error_code="READBACK_VERIFY_FAILED",
        error="STCRD line count 6 != 8",
        evidence=ev,
    )
    m = _push_meta(res, "purchase", r"\\accserver\ACCOUNT\70EXP\TEST")
    assert m["evidence"] == ev


def test_push_meta_omits_evidence_when_absent():
    res = DbfWriteResult(False, error_code="DBF_WRITE_FAILED", error="boom")
    m = _push_meta(res, "purchase", r"\\accserver\ACCOUNT\70EXP\TEST")
    assert "evidence" not in m


# ── ⑤ 端到端:明细读回不符 → DbfWriteResult.evidence 带上现场 ─────────────────


def test_detail_mismatch_evidence_reaches_write_result(datat):
    """整条失败路径兜底:同号多出 2 条活明细 → 读回行数 4≠2 → write_sale 回 DbfWriteResult
    且 evidence 已装好(proof:证据不是只存在异常里,是进了对外返回的结果)。"""
    _stcrd(datat, "IV690531-002", "1", 60.0)
    _stcrd(datat, "IV690531-002", "2", 40.0)

    res = _push_sale(datat, _sale_items(ref_no="IV690531-002"))
    assert not res.ok
    assert res.error_code == "READBACK_VERIFY_FAILED"
    assert "line count 4" in res.error  # 错误消息文本一字未改
    ev = res.evidence
    assert ev["kind"] == "detail_mismatch"
    assert ev["table"] == "STCRD"
    assert ev["docnum"] == "IV690531-002"
    assert ev["expected_lines"] == 2
    assert ev["found_lines"] == 4
    assert ev["deleted_count"] == 0
    json.dumps(ev)
