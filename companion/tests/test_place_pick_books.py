# -*- coding: utf-8 -*-
"""「Express 位置」能只选年度里的某几本账套（11-14）：69EXP 里混着真实客户账套，不能整个目录给（11-13 两次差点把真实账套
报成可写）。选的是账套目录时：读它所在的年度，但上报范围严格等于所选的这几本；选盘 / 上层 / 年度的照旧整片报。"""
from __future__ import annotations

import types

from companion import channel, express_machine, worker
from companion.express_places import reaches, row_in

YEAR = r"P:\69EXP"
TEST, TEST2, REAL = YEAR + r"\TEST", YEAR + r"\TEST2", YEAR + r"\69MANAC"
OTHER = r"P:\70EXP"


def _rows():
    return [{"root": YEAR, "path": path, "code": path.rsplit("\\", 1)[-1]} for path in (TEST, TEST2, REAL)] + \
           [{"root": OTHER, "path": OTHER + r"\TEST", "code": "TEST"}]


def _cfg(*places):
    return types.SimpleNamespace(channel=channel.CORE, express_places=tuple(places),
                                 place_channels=tuple((p, ("core",)) for p in places))


def test_a_year_is_read_when_a_book_inside_it_is_picked_but_not_a_sibling_year():
    assert reaches(YEAR, [TEST]) and reaches(YEAR, [YEAR]) and reaches(YEAR, ["P:\\"])
    assert not reaches(OTHER, [TEST])


def test_only_the_picked_books_are_reported():
    cfg = _cfg(TEST, TEST2)
    assert [r["path"] for r in worker.places_scope(cfg, _rows())] == [TEST, TEST2]  # 69MANAC（真账套）不报
    assert worker.scan_roots(cfg, [YEAR, OTHER]) == [YEAR]


def test_a_whole_year_or_drive_still_reports_every_book_in_it():
    assert [r["path"] for r in worker.places_scope(_cfg(YEAR), _rows())] == [TEST, TEST2, REAL]
    assert len(worker.places_scope(_cfg("P:\\"), _rows())) == 4


def test_the_startup_cache_of_an_older_wider_list_is_cut_to_the_picked_books():
    """本机缓存（account_sets_<档案>.json）存着换位置前扫到的 39 本：启动时先用它，上报照样只剩所选（11-13 测试机2 旁证）。"""
    stale = _rows() + [{"root": r"S:\2569\EXP69", "path": r"S:\2569\EXP69\69MANAC1", "code": "69MANAC1"}]
    assert [r["path"] for r in worker.places_scope(_cfg(TEST), stale)] == [TEST]


def test_the_selected_book_is_named_only_when_it_is_one_of_the_picked_books():
    cfg = _cfg(TEST)
    assert worker._in_member_places(cfg, TEST) is True
    assert worker._in_member_places(cfg, REAL) is False


def test_picking_a_book_folder_finds_its_year(monkeypatch):
    counts = {YEAR: 12}
    monkeypatch.setattr("companion.express_locate.sccomp_company_count", lambda p: counts.get(p.rstrip("\\/"), 0))
    monkeypatch.setattr(express_machine, "sccomp_company_count", lambda p: counts.get(p.rstrip("\\/"), 0), raising=False)
    from companion.scan_pace import GATE

    monkeypatch.setattr(GATE, "reachable", lambda _p: True)
    monkeypatch.setattr(express_machine, "_scan_exp_roots", lambda _d: [])
    monkeypatch.setattr(express_machine, "_deep_scan", lambda *_a, **_k: [])
    monkeypatch.setattr(express_machine, "rank_roots", lambda roots: [(r, 12) for r in roots])
    monkeypatch.setattr(express_machine, "pick_default", lambda ranked: ranked[0][0])
    assert express_machine.places_roots([TEST]) == [YEAR]
    assert express_machine.places_roots([TEST, TEST2]) == [YEAR]  # 同一年度只读一次
    assert row_in({"root": YEAR, "path": TEST}, [TEST]) and not row_in({"root": YEAR, "path": REAL}, [TEST])
