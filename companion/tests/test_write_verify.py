# -*- coding: utf-8 -*-
"""写完顺手核对（数字会计员第 3 期第 6 条，`write_verify`）：只防写入走样与挂错对方，只报不回滚。

进项 / 销项 / POS 日汇总写完回 ``verify {state, fields, party}``，随回执 meta 交回、进 task_journal、重发照样带回；
写入行与载荷不一致（参考号、科目、日期、税记录分店）报 mismatch 但单子照样留着；读不出来报 ``unreadable``。
另：``account_history`` 结果版本 2 不算 Pearnly 写的单。"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import dbf
import pytest

from companion import account_history, dbf_schema as S, dbf_sales as WS, dbf_writer as W, pos_journal as PJ
from companion import task_journal, worker, write_verify

import account_rules_fixture as F
import test_dbf_writer as P
import test_dbf_writer_sales as SL
import test_pos_sales_writer as POS
from _synth_account import seed_fiscal_year

datat = P.datat  # 进项与销项共用同一套合成账套（两套表族都建了）


def _set(d, table, column, value, **match):
    t = dbf.Table(str(d / f"{table}.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    try:
        for r in t:
            if not dbf.is_deleted(r) and all((r[k] or "").strip() == v for k, v in match.items()):
                with r as rec:
                    rec[column] = value
    finally:
        t.close()


def _write_purchase(d, **over):
    return W.write_purchase(P._payload(**over), d, allowed_sets=("DATAT",), selected_dir=str(d), make_backup=False)


def _expected(d, res, payload):
    entry = W.build_purchase_entry(payload)
    return write_verify.Expected(
        side="purchase", docnum=res.docnum, party_code=res.party_code, docdat=S.be_yymmdd_to_greg(payload["docdate_be"]),
        rectyp=S.RECTYP[entry.doctype], refnum=entry.ref_no,
        lines=[((r.SEQIT or "").strip(), (r.ACCNUM or "").strip(), (r.TRNTYP or "").strip())
               for r in P._rows(d, "GLJNLIT", VOUCHER=res.docnum)],
        tax_id=entry.supplier_tax_id, branch=entry.supplier_branch, has_vat=True)


# ── 进项 ─────────────────────────────────────────────────────────────


def test_a_clean_purchase_reports_ok_and_the_party_it_was_hung_on(datat):
    F.seed(datat, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "บริษัท ปตท จำกัด", "TAXID": "0107544000108", "ORGNUM": 0}])
    res = _write_purchase(datat)
    assert res.ok, res.error
    assert res.verify == {"state": "ok", "fields": [], "party": {"tax_id": "0107544000108", "branch": "00000"}}


def test_the_party_is_what_the_master_row_says_not_what_the_payload_says(datat):
    # 载荷给的码挂到的档是另一家（税号不同）：写入照样成功，party 照实报那一档，服务端拿票面比
    F.seed(datat, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "อื่น", "TAXID": "0105555555555", "ORGNUM": 3}])
    res = _write_purchase(datat)
    assert res.ok and res.verify["party"] == {"tax_id": "0105555555555", "branch": "00003"}


def test_a_branch_on_the_tax_record_other_than_the_payload_is_a_mismatch(datat):
    F.seed(datat, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "บริษัท ปตท จำกัด", "TAXID": "0107544000108", "ORGNUM": 2}])
    payload = P._payload()
    payload["supplier"]["branch"] = "00005"
    res = W.write_purchase(payload, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok  # 只报不回滚
    assert res.verify["state"] == "mismatch" and "vat_branch" in res.verify["fields"]
    assert P._rows(datat, "APTRN", DOCNUM=res.docnum)  # 单子还在


def test_rows_that_drifted_from_the_payload_are_named(datat):
    F.seed(datat, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "บริษัท ปตท จำกัด", "TAXID": "0107544000108"}])
    payload = P._payload()
    res = W.write_purchase(payload, datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    want = _expected(datat, res, payload)
    assert write_verify.check(datat, want)["state"] == "ok"
    _set(datat, "APTRN", "REFNUM", "INV-0000", DOCNUM=res.docnum)
    _set(datat, "GLJNLIT", "ACCNUM", "99-99-99-99", VOUCHER=res.docnum, SEQIT=S.SEQIT["purchase"])
    _set(datat, "ISVAT", "DOCDAT", dt.date(2015, 12, 1), DOCNUM=res.docnum)
    _set(datat, "ISVAT", "TAXID", "0000000000000", DOCNUM=res.docnum)
    _set(datat, "APTRN", "SUPCOD", "S999", DOCNUM=res.docnum)
    found = write_verify.check(datat, want)
    assert found["state"] == "mismatch"
    assert found["fields"] == ["party_code", "party_master", "account", "date", "refnum", "vat_tax_id"]
    assert found["party"] == {}


def test_an_unreadable_account_set_is_reported_not_raised(tmp_path):
    want = write_verify.Expected(side="purchase", docnum="RR1", party_code="S1", docdat=dt.date(2026, 1, 1),
                                 rectyp="3", refnum="X")
    assert write_verify.check(tmp_path / "missing", want) == {"state": "mismatch", "fields": ["unreadable"], "party": {}}


def test_a_periodic_inventory_purchase_is_compared_after_the_writers_own_account_swap(datat):
    # 期末盘存制：借方换成账套的「购货」科目（写路自己的换法），核对按换完的比，不误报
    F.seed(datat, "APMAS", [{"SUPCOD": "S001", "SUPNAM": "บริษัท ปตท จำกัด", "TAXID": "0107544000108"}])
    F.isinfo(datat, ISPERPETUA="N", ACCNUM04="11-04-02-00", ACCNUM18="51-03-00-00")
    F.glacc(datat, ("51-03-00-00", "ซื้อ"), ("11-04-02-00", "สินค้าสำเร็จรูป"))
    res = _write_purchase(datat)
    assert res.ok and res.verify["state"] == "ok", res.verify
    debits = [(r.ACCNUM or "").strip() for r in P._rows(datat, "GLJNLIT", VOUCHER=res.docnum)
              if (r.TRNTYP or "").strip() == S.TRNTYP_DEBIT]
    assert debits[0] == "51-03-00-00"


def test_a_code_with_no_master_row_is_named(datat):
    # 写进单据的对方码在主档里找不到：挂到哪一档说不清，交给人看
    res = _write_purchase(datat)
    assert res.ok and res.verify == {"state": "mismatch", "fields": ["party_master"], "party": {}}


# ── 销项 / POS ──────────────────────────────────────────────────────────


def test_a_clean_sale_reports_ok(datat):
    F.seed(datat, "ARMAS", [{"CUSCOD": "C001", "CUSNAM": "ลูกค้า ทดสอบ", "TAXID": "0105551234567", "ORGNUM": 0}])
    res = WS.write_sale(SL._sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    assert res.ok, res.error
    assert res.verify == {"state": "ok", "fields": [], "party": {"tax_id": "0105551234567", "branch": "00000"}}


def test_a_resale_over_a_soft_deleted_one_compares_only_the_new_rows(datat):
    # 销项单号是票面号：删单重推时新旧同号，核对只看这次追加的行（同 _verify）
    F.seed(datat, "ARMAS", [{"CUSCOD": "C001", "CUSNAM": "ลูกค้า ทดสอบ", "TAXID": "0105551234567"}])
    first = WS.write_sale(SL._sale(), datat, allowed_sets=("DATAT",), selected_dir=str(datat), make_backup=False)
    t = dbf.Table(str(datat / "ARTRN.DBF"), codepage=S.CODEPAGE)
    t.open(dbf.READ_WRITE)
    for r in t:
        dbf.delete(r)
    t.close()
    again = WS.write_sale(SL._sale(docdate_be="581216"), datat, allowed_sets=("DATAT",), selected_dir=str(datat),
                          make_backup=False)
    assert first.docnum == again.docnum and again.verify["state"] == "ok", again.verify


@pytest.fixture
def pos_datat(tmp_path):
    """同 test_pos_sales_writer 的账套：现金 / 收款科目、现金客户档、真账套形状的单据簿。"""
    d = tmp_path / "70EXP" / "pos"
    d.mkdir(parents=True)
    for name, spec in S.TABLE_SPECS.items():
        t = dbf.Table(str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
        t.open(dbf.READ_WRITE)
        t.close()
    seed_fiscal_year(d, 2026)
    F.isinfo(d, ACCNUM00=POS.CASH, ACCNUM02=POS.RECEIVABLE)
    F.glacc(d, (POS.CASH, "เงินสด"), (POS.QR_BANK, "ธนาคาร QR"), (POS.CARD_BANK, "ธนาคาร บัตร"),
            (POS.REVENUE, "รายได้"), (POS.OUTPUT_VAT, "ภาษีขาย"), (POS.RECEIVABLE, "ลูกหนี้การค้า"))
    F.seed(d, "ARMAS", [{"CUSCOD": "เงินสด", "CUSNAM": "เงินสด", "ACCNUM": POS.RECEIVABLE}])
    POS._book(d, *POS.REAL_SHAPED_ISRUN)
    return d


def test_a_pos_day_reports_ok_and_a_voucher_dated_wrong_is_named(pos_datat):
    res = POS._write(pos_datat, POS._pos_sale())
    assert res.ok, res.error
    assert res.verify["state"] == "ok", res.verify
    since = {"GLJNL": 0, "GLJNLIT": 0}
    vouchers = [{"voucher": "JV691013-001", "acc": POS.QR_BANK, "amount": "535.00"}]
    day = S.be_yymmdd_to_greg("691013")
    assert PJ.verify(pos_datat, vouchers, POS.CASH, since, docdat=day) == []
    _set(pos_datat, "GLJNLIT", "VOUDAT", dt.date(2026, 10, 12), VOUCHER="JV691013-001")
    assert PJ.verify(pos_datat, vouchers, POS.CASH, since, docdat=day) == ["pos_date"]


# ── 回执 ───────────────────────────────────────────────────────────────


def test_the_receipt_carries_verify_and_a_redelivery_brings_it_back(monkeypatch, tmp_path):
    monkeypatch.setattr(task_journal, "FILE", tmp_path / "written.json")
    from companion.write_result import DbfWriteResult

    verify = {"state": "mismatch", "fields": ["refnum"], "party": {"tax_id": "0107544000108", "branch": "00000"}}
    writes = []

    def write_purchase(payload, account_dir, **kwargs):
        writes.append(1)
        return DbfWriteResult(True, docnum="RR-1", verify=verify)

    monkeypatch.setattr(W, "write_purchase", write_purchase)
    monkeypatch.setattr(worker, "_task_settings", lambda cfg, payload: SimpleNamespace(
        account_dir="C:/acct", account_set="A", account_company="", has_selection=lambda: True))
    acks = []

    class Queue:
        def ack_success(self, task_id, extracted_invoice_no=None, line_modes=None, meta=None):
            acks.append(meta)

    cfg = SimpleNamespace(default_depcod="", ui_lang="en")
    task = SimpleNamespace(id="CWP-9", payload={"direction": "purchase"}, invoice_no="X")
    worker._handle_dbf_task(cfg, Queue(), task)
    worker._handle_dbf_task(cfg, Queue(), task)
    assert writes == [1]
    assert acks[0]["verify"] == verify and acks[1]["verify"] == verify and acks[1]["duplicate_skipped"] is True


def test_a_failed_write_has_no_verify():
    from companion.write_result import DbfWriteResult

    meta = worker._push_meta(DbfWriteResult(False, error_code="X"), "purchase", "C:/a")
    assert "verify" not in meta


# ── account_history v2 起 ───────────────────────────────────────────────


def test_account_history_v2_leaves_out_what_pearnly_wrote(tmp_path):
    d = tmp_path / "acct"
    d.mkdir()
    for name, spec in S.TABLE_SPECS.items():
        if name in account_history.TABLES:
            t = dbf.Table(str(d / f"{name}.DBF"), spec, codepage=S.CODEPAGE, dbf_type=S.DBF_TYPE)
            t.open(dbf.READ_WRITE)
            t.close()
    F.seed(d, "APMAS", [{"SUPCOD": "S1", "SUPNAM": "ร้าน ก", "TAXID": "0105551234567"}])
    day = dt.date.today()
    rows = [("RR1", "", ""), ("RR2", "PEARNLY", ""), ("RR3", "", "PEARNLY")]
    F.seed(d, "APTRN", [{"DOCNUM": num, "SUPCOD": "S1", "DOCDAT": day, "USERID": user} for num, user, _ in rows])
    F.seed(d, "GLJNL", [{"VOUCHER": num, "CREBY": by} for num, _, by in rows])
    F.seed(d, "GLJNLIT", [{"VOUCHER": num, "ACCNUM": "53-01-00-00", "TRNTYP": "0", "AMOUNT": 100} for num, _, _ in rows])
    found = account_history.collect(d, day - dt.timedelta(days=30))
    assert found["version"] == account_history.RESULT_VERSION == 3
    assert found["documents"] == 1
    assert found["suppliers"][0]["accounts"] == [{"code": "53-01-00-00", "docs": 1, "last": day.isoformat()}]
