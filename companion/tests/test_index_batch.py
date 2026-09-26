"""批量主档索引累积器(cdx_reindex.IndexBatch)单测。

逐票把主档表登记进来、队列空时一次性重建(一批 N 张只重建 1 次主档索引);重建失败只
记日志不抛(单据已记账,商品可见性延后兜底)。不依赖 runner —— 直接 monkeypatch reindex。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from companion import cdx_reindex as CR  # noqa: E402


class _RecordingReindex:
    def __init__(self):
        self.calls = []

    def __call__(self, account_dir, tables):
        self.calls.append((str(account_dir), tuple(tables)))


def test_accumulates_dedups_and_sorts(monkeypatch):
    rec = _RecordingReindex()
    monkeypatch.setattr(CR, "reindex", rec)
    b = CR.IndexBatch()
    b.add("/acc/a", ("STCRD", "STMAS"))
    b.add("/acc/a", ("STMAS", "ISACC"))  # 重复 STMAS 应去重
    b.add("/acc/a", ("",))  # 空表名忽略
    b.add("/acc/b", ("ARMAS",))
    b.flush()
    by_dir = dict(rec.calls)
    assert by_dir[str(Path("/acc/a"))] == ("ISACC", "STCRD", "STMAS")  # 排序后的并集
    assert by_dir[str(Path("/acc/b"))] == ("ARMAS",)


def test_flush_clears_no_double_rebuild(monkeypatch):
    rec = _RecordingReindex()
    monkeypatch.setattr(CR, "reindex", rec)
    b = CR.IndexBatch()
    b.add("/acc/a", ("STMAS",))
    b.flush()
    b.flush()  # 已冲洗 → 再 flush 不重复
    assert len(rec.calls) == 1


def test_empty_flush_is_noop(monkeypatch):
    rec = _RecordingReindex()
    monkeypatch.setattr(CR, "reindex", rec)
    CR.IndexBatch().flush()
    assert rec.calls == []


def test_flush_swallows_reindex_error(monkeypatch):
    def boom(account_dir, tables):
        raise CR.CdxReindexError("account busy")

    monkeypatch.setattr(CR, "reindex", boom)
    b = CR.IndexBatch()
    b.add("/acc/a", ("STMAS",))
    b.flush()  # 不得抛(只记日志)——已记账绝不因可见性回滚
    assert b._pending == {}  # 失败也清空,不无限累积(下批/夜间 PACK 兜)
