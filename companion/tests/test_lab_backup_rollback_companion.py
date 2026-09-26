# -*- coding: utf-8 -*-
r"""小助手侧的 `make_backup=True` 在**真靶场账套**上走一遍(交接单 §3.7)。

桥端那份在 `bridge/tests/test_lab_backup_rollback.py`。两侧各写了一份 `_backup`/`_restore`
(桥在 `writepath/snapshot.py`,小助手在 `companion/dbf_writer.py`),字节比对闸只防代码漂,
防不了「同一份代码在两台机器上跑出不同结果」——所以行为各自验一遍。

小助手没有四类新单据的实现(`ar_receipt`/`ap_payment`/`gl_journal`/`stock_adjust` 只在
`bridge/write_jobs.py` 的分发表里),所以这一侧验的是采购/销项那条在产写路的备份-回滚。
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from companion import cdx_reindex  # noqa: E402
from companion import dbf_writer  # noqa: E402
from companion import dbf_schema as S  # noqa: E402

from _lab_target import digest, requires_lab  # noqa: E402

pytestmark = requires_lab  # `lab_acct` 夹具在 conftest 注册

_REAL_RUNNER_AVAILABLE = cdx_reindex.runner_available
LOCK_FILE = "PEARNLY_WRITE.LCK"
DOCDATE = dt.date(2026, 5, 31)


@pytest.fixture(autouse=True)
def _use_the_real_cdx_runner(monkeypatch):
    """盖掉 conftest 的「禁 runner」—— 靶场是真 Express 账套,索引要走真的那条。"""
    monkeypatch.setattr(cdx_reindex, "runner_available", _REAL_RUNNER_AVAILABLE)


@pytest.fixture
def book(lab_acct):
    company = _read(lab_acct, "ISINFO", ("THINAM",))[0]["THINAM"]
    kw = dict(
        userid="BIT9",
        depcod="",
        allowed_sets=(lab_acct.name,),
        selected_dir=str(lab_acct),
        expected_company=company,
        make_backup=True,
    )
    return lab_acct, kw


def _read(acct: Path, table: str, fields):
    import dbf

    p = acct / f"{table}.DBF"
    if not p.is_file():
        return []
    t = dbf.Table(str(p), codepage=S.CODEPAGE)
    t.open(dbf.READ_ONLY)
    try:
        return [{f: _cell(r, f) for f in fields} for r in S.live_rows(t)]
    finally:
        t.close()


def _cell(rec, field):
    try:
        v = rec[field]
    except Exception:
        return ""
    return v.strip() if isinstance(v, str) else v


def _payload(acct: Path):
    supcod = _read(acct, "APMAS", ("SUPCOD",))[0]["SUPCOD"]
    return {
        "doctype": "RR",
        "account_set": acct.name,
        "docdate_be": "690531",
        "vat_period_be": "690501",
        "ref_no": "TIV69/9002",
        "supplier": {
            "code": supcod,
            "name": "",
            "tax_id": "",
            "prename": "",
            "supplier_new": False,
        },
        "vat_rate": 7.0,
        "base_amount": "1000.00",
        "vat_amount": "70.00",
        "total_amount": "1070.00",
        "lines": [
            {"acc": "53-03-06-00", "side": "D", "amount": "1000.00", "desc": "ซื้อ"},
            {"acc": "11-05-01-02", "side": "D", "amount": "70.00", "desc": "ภาษีซื้อ"},
            {
                "acc": "21-02-01-00",
                "side": "C",
                "amount": "1070.00",
                "desc": "เจ้าหนี้",
            },
        ],
    }


def _boom(*_a, **_kw):
    raise dbf_writer._WriteError("注入的读回失败(验回滚用)")


def test_backup_lands_beside_the_account_and_is_gone_after_success(book, monkeypatch):
    acct, kw = book
    seen = {}
    original = dbf_writer._discard_backup

    def spy(backup_dir):
        if backup_dir and Path(backup_dir).is_dir():
            seen["dir"] = Path(backup_dir)
            seen["files"] = digest(Path(backup_dir))
        original(backup_dir)

    monkeypatch.setattr(dbf_writer, "_discard_backup", spy)
    before = _backup_set_bytes(acct)

    res = dbf_writer.write_purchase(_payload(acct), acct, index_batch=None, **kw)
    assert res.ok, res.error

    made = seen["dir"]
    assert (
        made.parent == acct.parent
    ), "备份目录是账套的**兄弟**(当年靶场禁 True 就是为这个)"
    assert made.name.startswith(f"{acct.name}_pearnly_bak_")
    assert seen["files"] == before  # 装的是写前的原字节
    assert res.backup_dir is None
    assert not list(acct.parent.glob(f"{acct.name}_pearnly_bak_*"))


def test_a_failed_write_restores_the_lab_byte_for_byte(book, monkeypatch):
    acct, kw = book
    before = digest(acct)
    monkeypatch.setattr(dbf_writer, "_verify", _boom)

    res = dbf_writer.write_purchase(_payload(acct), acct, index_batch=None, **kw)
    assert not res.ok
    assert res.backup_dir, "回滚过的单要把快照路径留在结果里当标记"
    assert not Path(res.backup_dir).exists(), "回滚完快照就是死重,必须删掉"

    # 小助手不拿写互斥(那道闸只在桥端 · 见 bridge/write_jobs.py),所以这一侧还原后
    # 连 PEARNLY_WRITE.LCK 都不该多出来 —— 整目录逐文件全等。
    assert digest(acct) == before
    assert not (acct / LOCK_FILE).exists()
    assert not _read(acct, "APTRN", ("DOCNUM",))  # 靶场原本就是空的


def test_without_the_backup_the_same_failure_leaves_the_lab_dirty(book, monkeypatch):
    """反证:同一处失败、`make_backup=False` → 账套真的被改脏,半张采购票留在里面。"""
    acct, kw = book
    before = digest(acct)
    monkeypatch.setattr(dbf_writer, "_verify", _boom)

    res = dbf_writer.write_purchase(
        _payload(acct), acct, index_batch=None, **{**kw, "make_backup": False}
    )
    assert not res.ok
    assert res.backup_dir is None

    assert digest(acct) != before
    assert len(_read(acct, "APTRN", ("DOCNUM",))) == 1


def _backup_set_bytes(acct: Path) -> dict:
    """账套里属于备份集的那些表文件 {文件名: sha256}(备份目录该装的就是这些)。"""
    wanted = {t.upper() for t in S.BACKUP_TABLES}
    out = {}
    for name, sha in digest(acct).items():
        p = Path(name)
        if p.stem.upper() in wanted and p.suffix.upper() in dbf_writer._BACKUP_EXTS:
            out[p.name] = sha
    return out
