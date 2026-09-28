"""catalog_probe 目录 + 记账指纹 + 存货科目组候选上报测试 · 合成 VFP 表 · 不依赖真账。

覆盖:商品 kind 派生(STKTYP=0→stock 其它→non_stock)+ stock_master_count / 客户三字段 /
指纹动库存行占比(MREMBAL 非零=moving)/ 缺表全空不抛 /
ISACC 候选(fit 判据与 stock_template 同一份 · 排序 · 固定资产组不列 · 只报 fit 且限量)。
"""

from __future__ import annotations

import sys
from pathlib import Path

import dbf
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import _synth_account  # noqa: E402

from companion import catalog_probe as CP  # noqa: E402
from companion import dbf_schema as S  # noqa: E402

# 取自 70EXP 真账套 ISACC:(ACCTYP, ACCCOD, ACCDES, 存货科目, 成本科目)。
# 前两条是真存货组,后两条是这套账里真实存在的"看着像库存、其实是费用/固定资产"。
_ISACC = (
    ("S", "ST01", "ST01", "11-04-02-00", "51-01-00-00"),
    ("S", "ST10", "สินค้าสำเร็จรูป\xa0น้ำมันเครื่อง", "11-04-04-00", "51-01-00-00"),
    ("S", "ST02", "ST02", "52-08-00-00", "51-01-00-00"),  # ค่าใช้จ่าย-ส่งเสริมการขาย
    ("S", "ST06", "ST06", "14-01-03-00", "51-01-00-00"),  # อุปกรณ์สำนักงาน
    ("F", "FA02", "FA02  อุปกรณ์สำนักงาน", "14-01-03-00", "53-03-05-02"),  # 固定资产组
)


@pytest.fixture
def acct(tmp_path):
    d = tmp_path / "acct"
    d.mkdir()
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
        t.open(dbf.READ_WRITE)
        t.close()
    return d


@pytest.fixture
def chart(tmp_path):
    """带真账套 GLACC 科目树的合成账套 + 70EXP 的 ISACC 组(候选判定要靠这两张)。"""
    d = _synth_account.build(tmp_path / "70EXP" / "test")
    for typ, code, des, inv, cogs in _ISACC:
        _add(
            d,
            "ISACC",
            ACCTYP=typ,
            ACCCOD=code,
            ACCDES=des,
            METHOD="A",
            ACCNUM01=inv,
            ACCNUM02=cogs,
        )
    return d


def _add(d, table, **fields):
    t = dbf.Table(str(d / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        t.append(fields)
    finally:
        t.close()


def test_products_kind_and_stock_count(acct):
    _add(acct, "STMAS", STKCOD="0001", STKDES="สินค้าคงคลัง", STKTYP="0")  # 库存
    _add(acct, "STMAS", STKCOD="PN001", STKDES="ค่าบริการ", STKTYP="3")  # 非库存
    prods, stock_n = CP.probe_products(acct)
    assert stock_n == 1
    kinds = {p["code"]: p["kind"] for p in prods}
    assert kinds["0001"] == "stock"
    assert kinds["PN001"] == "non_stock"


def test_customers(acct):
    _add(acct, "ARMAS", CUSCOD="C001", CUSNAM="บริษัท เอบีซี", TAXID="0105546015062")
    _add(
        acct,
        "ARMAS",
        CUSCOD="C002",
        CUSNAM="บริษัท เอบีซี (สาขา)",
        TAXID="0105546015062",
        ORGNUM=18,
    )
    custs = CP.probe_customers(acct)
    assert custs == [
        {
            "code": "C001",
            "name": "บริษัท เอบีซี",
            "tax_id": "0105546015062",
            "branch": "00000",
        },
        {
            "code": "C002",
            "name": "บริษัท เอบีซี (สาขา)",
            "tax_id": "0105546015062",
            "branch": "00018",
        },
    ]


def test_fingerprint_moving_vs_static(acct):
    _add(acct, "STCRD", STKCOD="0001", MREMBAL=5.0)  # 动库存(余额非零)
    _add(acct, "STCRD", STKCOD="PN001", MREMBAL=0)  # 不动库存
    lines, moving = CP.probe_stcrd_fingerprint(acct)
    assert lines == 2
    assert moving == 1


def test_catalog_payload_shape(acct):
    _add(acct, "STMAS", STKCOD="0001", STKDES="x", STKTYP="0")
    _add(acct, "STCRD", STKCOD="0001", MREMBAL=1.0)
    pay = CP.catalog_payload(acct)
    assert set(pay) == {"products", "customers", "suppliers", "departments", "fingerprint", "isacc_candidates"}
    assert pay["fingerprint"] == {
        "stock_master_count": 1,
        "stcrd_lines": 1,
        "stcrd_lines_moving_stock": 1,
    }
    assert pay["isacc_candidates"] == []  # 没配存货科目组 → 诚实报空,不编一个


def test_missing_tables_returns_empty(tmp_path):
    empty = tmp_path / "none"
    empty.mkdir()
    assert CP.probe_products(empty) == ([], 0)
    assert CP.probe_customers(empty) == []
    assert CP.probe_stcrd_fingerprint(empty) == (0, 0)
    assert CP.probe_isacc_candidates(empty) == []
    assert CP.probe_suppliers(empty) == []
    assert CP.probe_departments(empty) == []


def test_suppliers_and_departments_are_reported(acct):
    # 2026-09-28 压测:同税号多个供应商档要让会计选一次;部门(ISTAB TABTYP=50)账套设置里选一次
    _add(acct, "APMAS", SUPCOD="B004", SUPNAM="BANGCHAK", TAXID="0105536080112", ORGNUM=0)
    _add(acct, "ISTAB", TABTYP="50", TYPCOD="SUTI", TYPDES="สาขาที่ 1")
    _add(acct, "ISTAB", TABTYP="20", TYPCOD="ชด", TYPDES="ชุด")
    assert CP.probe_suppliers(acct) == [{"code": "B004", "name": "BANGCHAK", "tax_id": "0105536080112",
                                          "branch": "00000"}]
    assert CP.probe_departments(acct) == [{"code": "SUTI", "name": "สาขาที่ 1"}]


def test_isacc_candidates_fit_matches_real_chart_facts(chart):
    """70EXP 事实:ST01/ST10 是存货组,ST02(费用)/ST06(固定资产)不是。"""
    fit = {c["code"]: c["fit"] for c in CP.probe_isacc_candidates(chart)}
    assert fit == {"ST01": True, "ST10": True, "ST02": False, "ST06": False}


def test_isacc_candidates_carry_account_names_and_usage(chart):
    _stock(chart, "OIL01", "ST01")
    for i in range(3):
        _stock(chart, f"ENG0{i}", "ST10")
    _add(chart, "STMAS", STKCOD="SRV1", STKDES="ค่าบริการ", STKTYP="3", ACCCOD="ST10")
    top = CP.probe_isacc_candidates(chart)[0]
    assert top == {
        "code": "ST10",  # fit 优先 + used_by 最多 → 排头
        "desc": "สินค้าสำเร็จรูป น้ำมันเครื่อง",  # NBSP 归一成普通空格
        "inv_acc": "11-04-04-00",
        "inv_acc_name": "สินค้าสำเร็จรูป(น้ำมันเครื่อง)",
        "cogs_acc": "51-01-00-00",
        "cogs_acc_name": "ต้นทุนสินค้าเพื่อขาย",
        "used_by": 3,  # 只数 STKTYP=0,那条服务档不算
        "fit": True,
    }


def test_isacc_candidates_rank_fit_over_usage(chart):
    """在用的多但判据不过 → 仍排在没人用的合格组之后:不合格的组不该诱人选。"""
    for i in range(5):
        _stock(chart, f"PROMO{i}", "ST02")
    codes = [c["code"] for c in CP.probe_isacc_candidates(chart)]
    assert codes == ["ST01", "ST10", "ST02", "ST06"]  # 并列 fit 保持 ISACC 物理行序


def test_fixed_asset_groups_are_not_offered(chart):
    assert "FA02" not in {c["code"] for c in CP.probe_isacc_candidates(chart)}


def test_payload_reports_only_fit_candidates_capped(chart):
    for i in range(25):  # 25 个合格组,心跳只带得动 20 个
        _add(
            chart,
            "ISACC",
            ACCTYP="S",
            ACCCOD=f"S{i:03d}",
            ACCDES=f"S{i:03d}",
            METHOD="A",
            ACCNUM01="11-04-02-00",
            ACCNUM02="51-01-00-00",
        )
    got = CP.catalog_payload(chart)["isacc_candidates"]
    assert len(got) == CP._MAX_ISACC_CANDIDATES
    assert all(c["fit"] for c in got)


def _stock(acct, stkcod, acccod):
    _add(acct, "STMAS", STKCOD=stkcod, STKDES=stkcod, STKTYP="0", ACCCOD=acccod)


def test_account_refresh_finds_an_account_missing_from_the_cached_list(monkeypatch):
    # 2026-09-28:推送能写的 SINCTEST,按账套刷新却报「账套不可用」—— 缓存清单里没有它,改为直接读登录清单
    from types import SimpleNamespace

    from companion import account_probe, catalog_refresh, projection_refresh

    seen = {}
    monkeypatch.setattr(account_probe, "probe_account_sets", lambda root: [
        SimpleNamespace(path=r"\\Accserver\d$\ACCOUNT\70EXP\SINCTEST", writable=True)])
    monkeypatch.setattr(catalog_refresh, "request_target_catalog",
                        lambda path, newer_than: (seen.setdefault("path", path) and None, 0.0))
    monkeypatch.setattr(catalog_refresh, "target_refresh_error", lambda key, newer_than: "")
    state = {"master_refresh_received_at": 100.0}
    result = projection_refresh.collect_account(state, [], r"\\accserver\d$\account\70exp\sinctest", 101.0,
                                                roots=[r"\\Accserver\d$\ACCOUNT\70EXP"])
    assert seen["path"] == r"\\Accserver\d$\ACCOUNT\70EXP\SINCTEST"
    assert result[3] != "ERR_ACCOUNT_SET_UNAVAILABLE"
    missing = projection_refresh.collect_account(state, [], r"\\x\none", 101.0, roots=[r"\\Accserver\d$\ACCOUNT\70EXP"])
    assert missing[3] == "ERR_ACCOUNT_SET_UNAVAILABLE"


def test_an_empty_rescan_keeps_the_last_account_sets():
    # 2026-09-28 录制：登记表一时打不开，心跳连续报 0 个账套
    from src.companion import projection_refresh

    state = {"reported_account_sets": [{"path": r"S:\\EXP\\ACME", "writable": True}]}
    projection_refresh.apply_endpoint_snapshot(state, {"account_sets": [], "accounts": [{"code": "1"}]}, 2.0)
    assert state["reported_account_sets"] == [{"path": r"S:\\EXP\\ACME", "writable": True}]
    assert state["reported_accounts"] == [{"code": "1"}]
    projection_refresh.apply_endpoint_snapshot(state, {"account_sets": [{"path": "B"}], "accounts": []}, 3.0)
    assert state["reported_account_sets"] == [{"path": "B"}]
