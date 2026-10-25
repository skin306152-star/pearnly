# -*- coding: utf-8 -*-
"""正式包缺 Express 索引重建程序时不写（10-25）：

- 用仓库源码离线往真 T 账套写单，CC 走了「只改 DBF、不重建 CDX」的回落，Express 开赊销单报 -330 后退出；
- 现在没有开发开关（PEARNLY_ALLOW_DBF_REINDEX=1）时整单拒写、回滚，回 REINDEX_UNAVAILABLE（转人工）；
- 心跳报重建程序的状态（ok / missing / broken）；批量延后重建主档索引失败时留到下一批，不丢。
"""

from __future__ import annotations

from pathlib import Path

from companion import cdx_reindex, worker
from companion import dbf_writer as W
from test_dbf_stock import _entry, _line, _write, book  # noqa: F401
from test_stock_location import _state, acct  # noqa: F401


def test_without_the_runner_and_without_the_dev_switch_nothing_is_written(book, monkeypatch):
    monkeypatch.delenv(cdx_reindex.DBF_FALLBACK_ENV, raising=False)
    before = _state(book)
    res = _write(book, _entry("OU", [_line("ICE01", 1)]), make_backup=True)
    assert not res.ok and res.error_code == W.ERR_REINDEX_UNAVAILABLE == "REINDEX_UNAVAILABLE"
    assert "cdx_reindex_runner missing" in res.error
    assert _state(book) == before
    assert worker._outcome_for(res.error_code) == "needs_review"


def test_the_dev_switch_keeps_the_dbf_only_fallback(book):
    assert cdx_reindex.dbf_fallback_allowed()
    assert _write(book, _entry("OU", [_line("ICE01", 1)])).ok


def test_the_runner_status_is_missing_without_the_exe_and_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(cdx_reindex, "_status_cache", {})
    monkeypatch.setattr(cdx_reindex, "runner_path", lambda: tmp_path / "nope.exe")
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: False)
    assert cdx_reindex.runner_status() == "missing"
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: True)
    assert cdx_reindex.runner_status() == "missing"  # 每次启动只试一次


def test_a_runner_that_does_not_answer_is_broken(monkeypatch):
    monkeypatch.setattr(cdx_reindex, "_status_cache", {})
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: True)
    monkeypatch.setattr(cdx_reindex, "_run", lambda *a, **k: "")
    assert cdx_reindex.runner_status() == "broken"
    monkeypatch.setattr(cdx_reindex, "_status_cache", {})
    # 表打不开时程序直接退出、只写第一行（10-25 测试机实测），不能当成能跑
    monkeypatch.setattr(cdx_reindex, "_run", lambda *a, **k: "action=TAGS table=PROBE tag=x\n")
    assert cdx_reindex.runner_status() == "broken"


def test_the_probe_opens_a_real_table_and_needs_it_opened(monkeypatch):
    seen = []

    def run(action, folder, table, *rest):
        seen.append((action, table, sorted(p.name for p in Path(folder).iterdir())))
        return "action=TAGS table=PROBE tag=x\nused=.T. ordcount=0 records=0\n"

    monkeypatch.setattr(cdx_reindex, "_status_cache", {})
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: True)
    monkeypatch.setattr(cdx_reindex, "_run", run)
    assert cdx_reindex.runner_status() == "ok"
    assert seen == [("TAGS", "PROBE", ["PROBE.DBF"])]


def test_a_failed_batch_reindex_is_kept_for_the_next_batch(monkeypatch):
    batch = cdx_reindex.IndexBatch()
    batch.add(r"C:\A", ("STMAS", "STCRD"))
    calls = []

    def boom(folder, tables):
        calls.append((str(folder), tables))
        raise cdx_reindex.CdxReindexError("busy")

    monkeypatch.setattr(cdx_reindex, "reindex", boom)
    batch.flush()
    assert batch.pending_dirs() == [r"C:\A"]
    monkeypatch.setattr(cdx_reindex, "reindex", lambda folder, tables: calls.append(("ok", tables)))
    batch.flush()
    assert batch.pending_dirs() == [] and calls[-1] == ("ok", ("STCRD", "STMAS"))


def test_tags_reads_the_record_count_and_tags(monkeypatch, tmp_path):
    out = ("action=TAGS table=STCRD tag=x\nused=.T. ordcount=2 records=17\n"
           "ord 1 name=STCRD1 key=STKCOD+DTOS(DOCDAT)\nord 2 name=STCRD3 key=DOCNUM+SEQNUM\n")
    monkeypatch.setattr(cdx_reindex, "_run", lambda *a, **k: out)
    assert cdx_reindex.tags(tmp_path, "STCRD") == (17, (("STCRD1", "STKCOD+DTOS(DOCDAT)"), ("STCRD3", "DOCNUM+SEQNUM")))
    monkeypatch.setattr(cdx_reindex, "_run", lambda *a, **k: "action=TAGS\nerror=Tag Name not Found\n")
    try:
        cdx_reindex.tags(tmp_path, "ISRUN")
    except cdx_reindex.CdxReindexError as error:
        assert "Tag Name not Found" in str(error)
    else:
        raise AssertionError("an unreadable index must raise")


def test_a_stock_entry_is_looked_up_by_the_express_detail_index(book, monkeypatch):
    seen = []
    monkeypatch.setattr(cdx_reindex, "runner_available", lambda: True)
    monkeypatch.setattr(cdx_reindex, "reindex", lambda account_dir, tables: None)
    monkeypatch.setattr(cdx_reindex, "verify_indexed", lambda account_dir, header, docnum: seen.append((header, docnum)))
    res = _write(book, _entry("OU", [_line("ICE01", 1)]))
    assert res.ok and seen == [(cdx_reindex.TAG_STCRD_DOC, res.docnum)]
