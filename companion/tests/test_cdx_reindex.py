"""cdx_reindex 解析 + 编排单测(mock runner 输出,不跑真 Harbour/SMB)。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import cdx_reindex as C  # noqa: E402

_AD = Path(r"\\acc\X")


def _patch_run(monkeypatch, out: str):
    monkeypatch.setattr(C, "_run", lambda *a, **k: out)


def test_runner_path_dev_points_at_native_exe():
    p = C.runner_path()
    assert p.name == "cdx_reindex_runner.exe"
    assert p.parent.name == "native"


def test_reindex_ok_when_done_marker_present(monkeypatch):
    _patch_run(monkeypatch, "action=REINDEX\nused=.T. ordcount=4\nreindex_done=.T. ordcount=4\n")
    C.reindex(_AD, ["ISRUN"])  # 不抛即通过


def test_reindex_raises_on_error(monkeypatch):
    _patch_run(monkeypatch, "action=REINDEX\nerror=open failed\n")
    with pytest.raises(C.CdxReindexError) as e:
        C.reindex(_AD, ["ARTRN"])
    assert "ARTRN" in str(e.value) and "open failed" in str(e.value)


def test_reindex_raises_when_no_done_marker(monkeypatch):
    _patch_run(monkeypatch, "action=REINDEX\nused=.T. ordcount=0\n")
    with pytest.raises(C.CdxReindexError):
        C.reindex(_AD, ["STMAS"])


def test_seek_found_parses_fields(monkeypatch):
    _patch_run(
        monkeypatch,
        "action=SEEK table=STCRD tag=STCRD3\nused=.T. ordcount=15\n"
        "seek_found=.T. recno=12411 stkcod=PN00001 doc=IV690625-002 seq=1 stkdes=x\n",
    )
    r = C.seek(_AD, "STCRD", "STCRD3", "IV690625-002")
    assert r.found and r.recno == 12411 and r.docnum == "IV690625-002" and r.stkcod == "PN00001"


def test_seek_not_found(monkeypatch):
    _patch_run(monkeypatch, "action=SEEK\nused=.T. ordcount=16\nseek_found=.F.\n")
    assert C.seek(_AD, "ARTRN", "ARTRN1", "NOPE").found is False


def test_verify_indexed_ok(monkeypatch):
    _patch_run(monkeypatch, "seek_found=.T. recno=1743 doc=IV690625-002\n")
    C.verify_indexed(_AD, C.TAG_AR_DOCNUM, "IV690625-002")  # 不抛即通过


def test_verify_indexed_raises_when_missing(monkeypatch):
    _patch_run(monkeypatch, "seek_found=.F.\n")
    with pytest.raises(C.CdxReindexError):
        C.verify_indexed(_AD, C.TAG_AP_DOCNUM, "RR000000-001")


def test_reindex_busy_error_when_lock_token(monkeypatch):
    # 错误描述含占用特征 → CdxBusyError(可被调用方转 waiting_lock)。
    _patch_run(monkeypatch, "action=REINDEX\nerror=open error: sharing violation\n")
    with pytest.raises(C.CdxBusyError):
        C.reindex(_AD, ["ARTRN"])


def test_busy_is_subclass_of_reindex_error():
    assert issubclass(C.CdxBusyError, C.CdxReindexError)
