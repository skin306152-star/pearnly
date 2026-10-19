# -*- coding: utf-8 -*-
"""账套清单增量扫描（2026-10-18）：本机索引、重启后从索引上报、按文件指纹只重读变了的、服务器断开不卡、所选账套先读。

现象（办公室测试机 1.7.0 test3）：授权了 17 个数据根、1353 份账套，每次启动整套重扫约 5 分钟、21.8 秒 CPU，
扫完前心跳一直报上次的「101 个 · 科目 0」。这里用合成账套（真表同名同列）钉住新的做法。
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import dbf
import pytest

from companion import account_index, account_probe, express_locate, master_refresh, projection_refresh, scan_pace

_SCCOMP = "COMPNAM C(60); COMPCOD C(10); PATH C(150); GENDAT D; CANDEL C(1)"
_ISINFO = "THINAM C(40); TAXID C(15); ORGNUM N(5,0); ACCNUM11 C(15); ACCNUM06 C(15)"
_GLACC = "ACCNUM C(15); ACCNAM C(50); ACCNAM2 C(50); ACCTYP C(1); STATUS C(1); GROUP C(1)"


def _isprd(account: Path) -> None:
    spec = "; ".join(f"BEG{m}{slot} D; END{m}{slot} D" for slot in ("", "NY") for m in range(1, 13))
    table = dbf.Table(str(account / "ISPRD.DBF"), spec, codepage="cp874", dbf_type="vfp")
    table.open(dbf.READ_WRITE)
    row = {}
    for slot, year in (("", 2026), ("NY", 2027)):
        for m in range(1, 13):
            beg = dt.date(year, m, 1)
            row[f"BEG{m}{slot}"] = beg
            row[f"END{m}{slot}"] = dt.date(year + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
    table.append(row)
    table.close()


def _isinfo(account: Path, company: str) -> None:
    account.mkdir(parents=True, exist_ok=True)
    path = account / "ISINFO.DBF"
    if path.exists():
        path.unlink()
    table = dbf.Table(str(path), _ISINFO, codepage="cp874", dbf_type="vfp")
    table.open(dbf.READ_WRITE)
    table.append({"THINAM": company, "TAXID": "0105551234567", "ORGNUM": 0, "ACCNUM11": "41-01", "ACCNUM06": "11-06"})
    table.close()


def _bump(path: Path) -> None:
    """改完的文件修改时间往后推一秒（同一秒内改两次时文件系统的时间可能不变）。"""
    stat = path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))


def _glacc(account: Path, count: int = 3) -> None:
    table = dbf.Table(str(account / "GLACC.DBF"), _GLACC, codepage="cp874", dbf_type="db3")
    table.open(dbf.READ_WRITE)
    for index in range(count):
        table.append({"ACCNUM": f"11-{index:02d}", "ACCNAM": f"บัญชี {index}", "ACCTYP": "0", "STATUS": "A", "GROUP": "1"})
    table.close()


def _data_root(root: Path, names) -> Path:
    (root / "secure").mkdir(parents=True, exist_ok=True)
    table = dbf.Table(str(root / "secure" / "SCCOMP.DBF"), _SCCOMP, codepage="cp874", dbf_type="vfp")
    table.open(dbf.READ_WRITE)
    for position, name in enumerate(names):
        table.append({"COMPNAM": f"{position:02d}.{name}", "COMPCOD": name, "PATH": name, "CANDEL": "Y"})
        _isinfo(root / name, f"บริษัท {name}")
        _isprd(root / name)
    table.close()
    _glacc(root / names[0])
    return root


@pytest.fixture
def opened(monkeypatch):
    """数每张表被打开了几次（只读打开 DBF 是扫描里最花 CPU 与网络的一步）。"""
    counts: dict = {}
    real = dbf.Table.open

    def counting(self, *args, **kwargs):
        name = Path(str(self.filename)).name.upper()
        counts[name] = counts.get(name, 0) + 1
        return real(self, *args, **kwargs)

    monkeypatch.setattr(dbf.Table, "open", counting)
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: [])
    master_refresh._reset_for_tests()
    return counts


def _restart() -> None:
    """当作 CC 重启：进程里的扫描结果与索引都清空（本机索引文件还在）。"""
    master_refresh._reset_for_tests()
    account_index.INDEX._reset_for_tests()
    scan_pace.GATE._reset_for_tests()


def _complete(express_root, account_dir, **kwargs):
    master_refresh.request_master(str(express_root), str(account_dir), **kwargs)
    deadline = time.time() + 10
    while time.time() < deadline:
        payload, version = master_refresh.request_master(str(express_root), str(account_dir), **kwargs)
        if payload is not None and not payload.get("partial"):
            return payload, version
        time.sleep(0.01)
    raise AssertionError("scan did not finish")


def test_restart_reads_no_account_table_when_nothing_changed(tmp_path, opened):
    """首扫读一遍、存进本机索引；重启后同样的清单一份 DBF 都不开（原来每次启动 ISINFO 两次 + ISPRD 一次 × 每份账套）。"""
    root = _data_root(tmp_path / "70EXP", ["A", "B", "C", "D"])
    opened.clear()  # 建合成账套时的打开不算
    first, _ = _complete(root, root / "A")
    assert [row["company"] for row in first["account_sets"]] == ["บริษัท A", "บริษัท B", "บริษัท C", "บริษัท D"]
    assert len(first["accounts"]) == 3
    assert opened.get("ISINFO.DBF") == 4 and opened.get("ISPRD.DBF") == 4  # ISINFO 一份只开一次（原来两次）
    assert account_index.index_file().exists()

    _restart()
    opened.clear()
    second, _ = _complete(root, root / "A")
    assert second["account_sets"] == first["account_sets"] and second["accounts"] == first["accounts"]
    assert opened == {}  # 登记表、ISINFO、ISPRD、科目表都没变：一张都不开


def test_only_the_changed_account_is_read_again(tmp_path, opened):
    """按文件指纹（大小 + 修改时间）：改了的那份才重读；登记表变了才重读登记表。"""
    root = _data_root(tmp_path / "70EXP", ["A", "B", "C"])
    _complete(root, root / "A")
    _isinfo(root / "B", "บริษัท ใหม่")
    _bump(root / "B" / "ISINFO.DBF")

    _restart()
    opened.clear()
    payload, _ = _complete(root, root / "A")
    assert [row["company"] for row in payload["account_sets"]] == ["บริษัท A", "บริษัท ใหม่", "บริษัท C"]
    assert opened == {"ISINFO.DBF": 1, "ISPRD.DBF": 1}

    table = dbf.Table(str(root / "secure" / "SCCOMP.DBF"), codepage="cp874")
    table.open(dbf.READ_WRITE)
    table.append({"COMPNAM": "03.E", "COMPCOD": "E", "PATH": "E", "CANDEL": "Y"})
    table.close()
    _bump(root / "secure" / "SCCOMP.DBF")
    _isinfo(root / "E", "บริษัท E")
    _restart()
    opened.clear()
    payload, _ = _complete(root, root / "A")
    assert [row["code"] for row in payload["account_sets"]] == ["A", "B", "C", "E"]
    assert opened == {"SCCOMP.DBF": 1, "ISINFO.DBF": 1}  # 新登记的那份 E 才读（它没有 ISPRD）


def test_push_check_still_reads_the_registry_every_time(tmp_path, opened):
    """推送前核账套（worker._probe_task_account → probe_account_sets）照旧每次现读登记表，不吃索引里的行。"""
    root = _data_root(tmp_path / "70EXP", ["A"])
    _complete(root, root / "A")
    opened.clear()
    assert [item.code for item in account_probe.probe_account_sets(root)] == ["A"]
    assert opened.get("SCCOMP.DBF") == 1


def test_first_heartbeat_after_restart_reports_cached_sets_and_chart(tmp_path, monkeypatch, opened):
    """重启后第一次心跳就报上次的账套清单与科目表（原来先报「101 个 · 科目 0」，等整套扫完 5 分钟）；扫描在后台继续。"""
    from companion import worker
    from companion.config import CompanionConfig

    root = _data_root(tmp_path / "70EXP", ["A", "B"])
    first, _ = _complete(root, root / "A")
    state = {"reported_account_sets": first["account_sets"]}
    projection_refresh.save_account_sets(state, "p1")

    _restart()
    hold = threading.Event()
    real = account_probe.account_sets_payload
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda *a, **k: hold.wait(5) and real(*a, **k))
    sent = []

    class Queue:
        def health(self, **kwargs):
            sent.append(kwargs)
            return {}

    cfg = CompanionConfig(express_root=str(root), account_dir=str(root / "A"), account_set=str(root / "A"),
                          profile_id="p1", heartbeat_interval_s=60)
    try:
        worker._run_periodic(cfg, Queue(), {"last_heartbeat": 0.0})
    finally:
        hold.set()
    assert [row["code"] for row in sent[0]["account_sets"]] == ["A", "B"]
    assert sent[0]["accounts"] == first["accounts"] and len(sent[0]["accounts"]) == 3


def test_selected_root_is_read_first_and_reported_before_the_rest(tmp_path, monkeypatch, opened):
    """所选账套所在的数据根先读；扫得慢时先交「进行中」快照（带科目表），心跳把读完的数据根换成新的、其余沿用上次的。"""
    monkeypatch.setattr(master_refresh, "_PARTIAL_AFTER_S", 0.0)
    old = _data_root(tmp_path / "69EXP", ["X", "Y"])
    new = _data_root(tmp_path / "70EXP", ["A", "B"])
    order = []
    release = threading.Event()
    real = account_probe.account_sets_payload

    def slow(root, **kwargs):
        order.append(Path(root).name)
        if Path(root).name == "69EXP":
            release.wait(5)
        return real(root, **kwargs)

    monkeypatch.setattr(account_probe, "account_sets_payload", slow)
    kwargs = {"authorized_roots": [str(new)]}
    master_refresh.request_master(str(old), str(new / "A"), **kwargs)
    partial = None
    deadline = time.time() + 5
    while time.time() < deadline and partial is None:
        partial, version = master_refresh.request_master(str(old), str(new / "A"), **kwargs)
        time.sleep(0.01)
    try:
        assert order[0] == "70EXP"  # 所选账套 A 在 70EXP：先读它（主根 69EXP 排在后面）
        assert partial["partial"] is True and partial["scanned_roots"] == [str(new)]
        assert [row["code"] for row in partial["account_sets"]] == ["A", "B"]
        assert len(partial["accounts"]) == 3  # 科目表先读，进行中的快照就带上

        previous = [{"path": str(old / "X"), "root": str(old), "code": "X"},
                    {"path": str(new / "OLD"), "root": str(new), "code": "OLD"}]
        state = {"reported_account_sets": previous}
        projection_refresh.apply_endpoint_snapshot(state, partial, version)
        codes = [row["code"] for row in state["reported_account_sets"]]
        assert codes == ["A", "B", "X"]  # 70EXP 换成新的；69EXP 还没读，沿用上次的（不先缩水）
    finally:
        release.set()
    final, _ = _complete(old, new / "A", **kwargs)
    assert "partial" not in final
    assert [row["code"] for row in final["account_sets"]] == ["X", "Y", "A", "B"]  # 上报顺序照旧（主根在前）


def test_partial_snapshot_does_not_finish_a_web_refresh(tmp_path, monkeypatch):
    """网页「重新读取」要等完整快照才回「已完成」；进行中的快照照常上报，但不存本机清单。"""
    from companion import worker
    from companion.config import CompanionConfig

    partial = {"account_sets": [{"path": "C:/b/A", "root": "C:/b"}], "partial": True, "scanned_roots": ["C:/b"]}
    monkeypatch.setattr(projection_refresh, "collect_endpoint", lambda *a, **k: (partial, 5.0, ""))
    saved = []
    monkeypatch.setattr(projection_refresh, "save_account_sets", lambda state, pid: saved.append(pid))
    sent = []

    class Queue:
        def health(self, **kwargs):
            sent.append(kwargs)
            return {}

    state = {"last_heartbeat": 0.0, "master_refresh_request": {"request_id": "r1", "scope_kind": "endpoint"},
             "master_refresh_received_at": time.time()}
    cfg = CompanionConfig(express_root="C:/b", account_dir="C:/b/A", profile_id="p1")
    worker._run_periodic(cfg, Queue(), state)
    assert sent[0]["master_refresh_request_id"] is None
    assert [row["path"] for row in sent[0]["account_sets"]] == ["C:/b/A"]
    assert saved == []


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_unreachable_server_is_skipped_quickly_and_backed_off(tmp_path, monkeypatch, opened):
    """局域网服务器断开：问它在不在的那一下卡住也只等 probe_seconds；之后退避期内一次网络读都不做、不卡下一遍；
    本机的数据根照常读完上报。"""
    local = _data_root(tmp_path / "70EXP", ["A"])
    stuck = threading.Event()
    asked = []

    def probe(path):
        asked.append(path)
        stuck.wait(5)  # 共享盘断开：stat 一直不回
        return True

    clock = _Clock()
    gate = scan_pace.HostGate(probe, probe_seconds=0.2, clock=clock)
    monkeypatch.setattr(scan_pace, "GATE", gate)
    remote = r"\\server\account\69EXP"
    seen = []
    real = account_probe.account_sets_payload
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda root, **k: seen.append(root) or real(root, **k))
    try:
        started = time.monotonic()
        payload, _ = _complete(local, local / "A", authorized_roots=[remote, remote + r"\..\68EXP"])
        assert time.monotonic() - started < 2.0
        assert [row["code"] for row in payload["account_sets"]] == ["A"]
        assert seen == [str(local)]  # 服务器上的两个数据根都没去读
        assert len(asked) == 1  # 同一台服务器只问了一次

        master_refresh._reset_for_tests()  # 同一次运行里的下一遍整套扫描（服务器在不在的记录还在）
        clock.now += 30  # 还在退避期（第一次失败等 60 秒）
        started = time.monotonic()
        _complete(local, local / "A", authorized_roots=[remote])
        assert time.monotonic() - started < 1.0 and len(asked) == 1  # 卡着的那一下没回来前不再问
    finally:
        stuck.set()


def test_web_refresh_asks_a_backed_off_server_again(tmp_path, monkeypatch, opened):
    """服务器退避期内平时的扫描不去读它；网页「重新读取」（有人在等）马上再问一次，通了就照常读。"""
    local = _data_root(tmp_path / "70EXP", ["A"])
    answers = iter([False, True])
    gate = scan_pace.HostGate(lambda path: next(answers, True), probe_seconds=1, clock=_Clock())
    monkeypatch.setattr(scan_pace, "GATE", gate)
    remote = r"\\server\account\69EXP"
    seen = []
    real = account_probe.account_sets_payload
    monkeypatch.setattr(account_probe, "account_sets_payload", lambda root, **k: seen.append(root) or real(root, **k))
    _complete(local, local / "A", authorized_roots=[remote])
    master_refresh._reset_for_tests()
    _complete(local, local / "A", authorized_roots=[remote])
    assert remote not in seen  # 第一次问不通 → 退避期内不读
    _complete(local, local / "A", authorized_roots=[remote], force=True, account_sets_only=True)
    assert seen[-1] == remote  # 「重新读取」：又问了一次，通了就去读


def test_network_errors_mark_the_server_down_but_a_missing_folder_does_not(monkeypatch):
    """Windows 报网络类错误（如 53 找不到网络路径）→ 服务器不在、退避（60、120 秒……）；只是这个文件夹不在（3）→ 服务器是好的。"""
    clock = _Clock()
    calls = []

    class FakeOs:
        winerror = 53

        @classmethod
        def stat(cls, path):
            calls.append(path)
            exc = OSError(2, "unreachable")
            exc.winerror = cls.winerror
            raise exc

    monkeypatch.setattr(scan_pace, "os", FakeOs)
    gate = scan_pace.HostGate(probe_seconds=1, clock=clock)
    assert gate.reachable(r"\\nas\books\70EXP") is False
    assert gate.reachable(r"\\NAS\Books\69EXP") is False and len(calls) == 1  # 同一台：退避期内不读
    clock.now += 61
    assert gate.reachable(r"\\nas\books\69EXP") is False and len(calls) == 2  # 到点再问一次，还是不在
    clock.now += 61
    assert gate.reachable(r"\\nas\books\69EXP") is False and len(calls) == 2  # 第二次退避 120 秒
    assert gate.reachable(r"\\nas\books\69EXP", retry=True) is False and len(calls) == 3  # 「重新读取」：不等退避马上再问

    FakeOs.winerror = 3
    other = scan_pace.HostGate(probe_seconds=1, clock=clock)
    assert other.reachable(r"\\nas\books\70EXP") is True  # 只是这个文件夹不在：服务器是好的
    assert scan_pace.host_of(r"\\NAS\Books\70EXP") == r"\\nas\books" and scan_pace.host_of("\\\\nas\\books/A") == r"\\nas\books"
    assert scan_pace.host_of("S:\\70EXP", drive_type=lambda d: 4) == "s:"  # 网络盘符
    assert scan_pace.host_of("S:\\70EXP", drive_type=lambda d: 1) == "s:"  # 没有这个盘 / 未知：照网络盘
    assert scan_pace.host_of("C:\\PearnlyExpress\\70EXP", drive_type=lambda d: 3) == ""  # 本机固定盘：不设关卡
    assert scan_pace.host_of("70EXP") == ""
    probed = []
    local = scan_pace.HostGate(lambda path: probed.append(path) or False, drive_type=lambda d: 3)
    assert local.reachable("C:\\PearnlyExpress\\70EXP") is True and probed == []  # 本机盘不问、不退避


def test_server_that_stalls_mid_scan_is_dropped_for_this_pass(tmp_path, opened):
    """读到一半服务器没响应（某份账套光读文件就超过 slow_seconds）：这个数据根这一轮报空、这台服务器进退避，
    不再一份一份地等超时。"""
    root = _data_root(tmp_path / "70EXP", ["A", "B", "C"])
    opened.clear()  # 建合成账套时的打开不算
    on_server = scan_pace.HostGate(lambda path: True)
    on_server.host = lambda path: r"\\nas\books"  # 当作这个数据根在服务器上（合成账套只能建在本机临时目录）
    late = scan_pace.Pace(gate=on_server, slow_seconds=15, clock=lambda: time.monotonic() + 100, sleep=lambda s: None)
    assert account_probe.account_sets_payload(str(root), pace=late) == []
    assert opened.get("ISINFO.DBF") == 1  # 第一份读完就停：后面两份没再去读

    opened.clear()
    local = scan_pace.Pace(gate=scan_pace.HostGate(lambda path: True, drive_type=lambda d: 3), slow_seconds=15,
                           clock=lambda: time.monotonic() + 100, sleep=lambda s: None)
    rows = account_probe.account_sets_payload(str(root), pace=local)
    assert [row["code"] for row in rows] == ["A", "B", "C"]  # 本机盘读得慢只是慢：照常读完

    clock = _Clock()
    asked = []
    gate = scan_pace.HostGate(lambda path: asked.append(path) or True, clock=clock)
    pace = scan_pace.Pace(gate=gate, slow_seconds=15, clock=lambda: 100.0, cpu=lambda: 0.0, sleep=lambda s: None)
    with pytest.raises(scan_pace.HostStalled):
        pace.account(r"\\nas\books\70EXP", started=0.0)
    assert gate.reachable(r"\\nas\books\69EXP") is False and asked == []  # 同一台服务器的其它数据根：退避期内不读


def test_pace_keeps_cpu_under_the_duty_cycle():
    clock, cpu, slept = [0.0], [0.0], []

    def sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    pace = scan_pace.Pace(duty=0.25, clock=lambda: clock[0], cpu=lambda: cpu[0], sleep=sleep)
    for _ in range(8):
        cpu[0] += 0.016  # 一份账套 16 毫秒 CPU（测试机原来的平均数）
        clock[0] += 0.016
        pace.yield_cpu()
    assert cpu[0] / clock[0] <= 0.25 + 1e-9
    assert pace.slept == pytest.approx(sum(slept)) and pace.slept > 0


def test_restart_does_not_search_every_drive_again_within_a_day(tmp_path, opened, monkeypatch):
    """找新年度文件夹（整盘、共享盘深层查找）重启后不再马上做：距上次满 24 小时才做；网页「重新读取」照旧马上找。"""
    root = _data_root(tmp_path / "70EXP", ["A"])
    looked = []
    monkeypatch.setattr(express_locate, "candidate_roots", lambda: looked.append(True) or [])
    _complete(root, root / "A", discover=True)
    assert looked == [True]

    _restart()
    _complete(root, root / "A", discover=True)
    assert looked == [True]  # 重启后的第一遍：不整盘找

    _restart()
    _complete(root, root / "A", discover=True, force=True, account_sets_only=True)
    assert looked == [True, True]  # 网页「重新读取」：马上找


def test_index_file_survives_corruption_and_prunes_old_entries(tmp_path, monkeypatch):
    path = account_index.index_file()
    path.write_text("{not json", encoding="utf-8")
    index = account_index.AccountIndex()
    assert index.details("x", ((1, 1), None)) is None  # 坏文件 = 空索引，不报错

    index.remember_details("old", ((1, 1), None), ("A", "1", {}, {}))
    index.remember_details("new", ((2, 2), None), ("B", "2", {}, {}))
    index._details["old"][2] = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    for number in range(10):
        index.remember_chart(f"c{number}", (number, number), [{"code": str(number)}])
    assert index.save() is True
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert set(raw["details"]) == {"new"}  # 30 天没用到的删掉
    assert len(raw["charts"]) == account_index.KEEP_CHARTS and "c9" in raw["charts"] and "c0" not in raw["charts"]
    assert "token" not in path.read_text(encoding="utf-8").lower()

    again = account_index.AccountIndex()
    assert again.details("new", ((2, 2), None)) == ("B", "2", {}, {})
    assert again.details("new", ((2, 3), None)) is None  # 文件变了：当没有
    assert again.chart("c9") == [{"code": "9"}]


# ── 录入标记（第 3 期 E3，1.9.1）：GLJNL.DBF 表头的最后更新日期 / 记录数 ─────────────────────────────

_GLJNL = "VOUCHER C(12); VOUDAT D; AMOUNT N(14,2)"


def _journal(account: Path, rows: int) -> Path:
    account.mkdir(parents=True, exist_ok=True)
    path = account / "GLJNL.DBF"
    table = dbf.Table(str(path), _GLJNL, codepage="cp874", dbf_type="vfp") if not path.exists() \
        else dbf.Table(str(path), codepage="cp874")
    table.open(dbf.READ_WRITE)
    for index in range(rows):
        table.append({"VOUCHER": f"JV{index:04d}", "VOUDAT": dt.date(2026, 10, 1), "AMOUNT": 100})
    table.close()
    return path


def test_the_journal_header_is_read_without_opening_the_table(tmp_path):
    """只读表头前 8 个字节：第 1–3 字节是年 - 1900 / 月 / 日，第 4–7 字节记录数（小端）；不合法、太短、读不到回空串。"""
    path = tmp_path / "GLJNL.DBF"
    path.write_bytes(bytes([0x30, 126, 10, 19]) + (12345).to_bytes(4, "little") + b"\x00" * 24)
    assert account_probe.read_entry_header(path) == "2026-10-19/12345"
    path.write_bytes(bytes([0x03, 26, 10, 19]) + (7).to_bytes(4, "little"))   # 按两位年写的
    assert account_probe.read_entry_header(path) == "2026-10-19/7"
    for broken in (bytes([0x30, 126, 13, 1, 0, 0, 0, 0]), bytes([0x30, 126, 10])):
        path.write_bytes(broken)
        assert account_probe.read_entry_header(path) == ""
    assert account_probe.read_entry_header(tmp_path / "missing.DBF") == ""
    journal = _journal(tmp_path / "A", 3)
    assert account_probe.read_entry_header(journal).endswith("/3")


def test_entry_marks_ride_the_scan_and_a_restart_opens_no_journal(tmp_path, opened, monkeypatch):
    """每份账套另报 ``entry_mark``（服务端按它判两份同期时要不要重问）；GLJNL 没变就只 stat、不打开（同 ISINFO / ISPRD），
    重启后也一样；多录了单（记录数变）的那份才重读表头；没有 GLJNL 的不报这个键。"""
    root = _data_root(tmp_path / "70EXP", ["A", "B", "C"])
    _journal(root / "A", 3)
    _journal(root / "C", 5)
    reads = []
    real = account_probe.read_entry_header
    monkeypatch.setattr(account_probe, "read_entry_header", lambda path: reads.append(Path(path).parent.name) or real(path))
    opened.clear()
    first, _ = _complete(root, root / "A")
    marks = {row["code"]: row.get("entry_mark") for row in first["account_sets"]}
    assert marks["A"].endswith("/3") and marks["C"].endswith("/5") and marks["B"] is None
    assert sorted(reads) == ["A", "C"] and "GLJNL.DBF" not in opened   # 表头不经 dbf 库打开

    _restart()
    reads.clear()
    opened.clear()
    second, _ = _complete(root, root / "A")
    assert second["account_sets"] == first["account_sets"] and reads == [] and opened == {}

    _journal(root / "C", 1)
    _bump(root / "C" / "GLJNL.DBF")
    _restart()
    reads.clear()
    third, _ = _complete(root, root / "A")
    assert reads == ["C"] and {row["code"]: row.get("entry_mark") for row in third["account_sets"]}["C"].endswith("/6")
