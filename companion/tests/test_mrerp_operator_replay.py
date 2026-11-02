# -*- coding: utf-8 -*-
"""MR.ERP 代录离线回放（ADR-026 第 2 步）：假 MR.ERP 站点 + 真网页内核（QtWebEngine，offscreen）+ 代录宿主一整条走。

网页内核在这台机器上起不来（例如没有桌面的 Windows CI 服务）时整个文件跳过，纯逻辑由 ``test_mrerp_operator_parts.py`` 兜住。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from _mrerp_harness import PASSWORD, Maker, factory, payload, pump, qapp, spin, webengine_works

pytest.importorskip("PySide6.QtWebEngineWidgets")
pytestmark = pytest.mark.skipif(not webengine_works(), reason="QtWebEngine cannot start on this machine")

from _fake_mrerp import FakeMrerp  # noqa: E402

from companion.express_operator.host import Host  # noqa: E402


@pytest.fixture()
def site():
    fake = FakeMrerp().start()
    yield fake
    fake.stop()


@pytest.fixture()
def app(monkeypatch):
    monkeypatch.setenv("PEARNLY_CC_MRERP_UNVERIFIED", "1")  # 配方还没在真站点核对：假站点回放打开开发开关
    return qapp()


def _host(tmp_path, maker, **kw):
    return Host(tmp_path, assemblers={"mrerp": factory(maker, **kw)})


def _handle(host, task, job):
    return spin(lambda: host.handle(task, job, None), timeout=180)


def test_four_kinds_are_entered_saved_and_verified(app, site, tmp_path):
    maker = Maker()
    host = _host(tmp_path, maker)
    jobs = [
        payload(site.url, doc_type="purchase", menu="363", index=1, total=4, doc="D1", docnum="P-0001", ref="IV-1",
                lines=[{"code": "P001", "qty": "2", "price": "100"}, {"code": "P002", "qty": "3", "price": "12.5"}]),
        payload(site.url, doc_type="purchase_expense", menu="453", index=2, total=4, doc="D2", docnum="E-0001",
                party="S002", ref="EXP-9", lines=[{"code": "SRV1", "qty": "1", "price": "500"}]),
        payload(site.url, doc_type="sales_credit", menu="370", index=3, total=4, doc="D3", docnum="S-0001", party="C001",
                ref="", vat="0%", lines=[{"code": "P001", "qty": "1", "price": "250"}]),
        payload(site.url, doc_type="sales_cash", menu="371", index=4, total=4, doc="D4", docnum="H-0001", party="CASH",
                ref="", lines=[{"code": "P002", "qty": "4", "price": "25"}]),
    ]
    results = [_handle(host, f"t{i}", job) for i, job in enumerate(jobs, 1)]
    assert [r["status"] for r in results] == ["saved"] * 4, json.dumps(results, ensure_ascii=False)
    assert [r["docnum"] for r in results] == ["PNP-0001", "PGE-0001", "IVS-0001", "HSH-0001"]
    docs = site.saved()
    assert len(docs) == 4 and site.logins == 1 and site.logouts == 1  # 一批登录一次，录完退出登录
    first = docs[0]
    assert (first["party"], first["ref"], first["date"], str(first["net"])) == ("S001", "IV-1", "2026-10-20", "254.13")
    assert [(l["code"], l["qty"], l["price"]) for l in first["lines"]] == [("P001", "2.00", "100.00"), ("P002", "3.00", "12.50")]
    assert first["cash"] is False and docs[3]["cash"] is True and docs[3]["cashpay"] == "107.00"
    assert str(docs[2]["vat"]) == "0.00"
    snap = host.snapshots()[0]
    assert snap["status"] == "finished" and snap["counts"] == {"saved": 4}
    assert all(d["evidence"] == "kept" for d in snap["docs"])  # 每张存档后查看页截图（只存本机）
    assert maker.drivers and all(d.closed for d in maker.drivers)  # 录完关窗、释放资料


def test_a_readback_that_differs_goes_to_a_person_and_the_saved_document_is_left_alone(app, site, tmp_path):
    site.tamper = lambda doc: doc["lines"][0].update(qty="9.00")  # MR.ERP 存进去的和填的不一样
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="R-0001"))
    assert out["status"] == "human" and out["reason"] == "readback_mismatch"
    assert out["meta"]["docnum"] == "PNR-0001" and "line 1 qty" in out["detail"]
    assert len(site.saved()) == 1 and site.saved()[0]["lines"][0]["qty"] == "9.00"  # 不自动改已存的单


def test_kicked_after_save_pauses_without_logging_in_again_and_the_next_try_does_not_enter_it_twice(app, site, tmp_path):
    site.kick_on_save = True  # 会计本人刚好用同一个账号登录：存档那一下被踢（MR.ERP 照样写了库）
    maker = Maker()
    host = _host(tmp_path, maker)
    jobs = [payload(site.url, index=1, total=2, doc="D1", docnum="K-0001"),
            payload(site.url, index=2, total=2, doc="D2", docnum="K-0002", ref="IV-2")]
    out = _handle(host, "t1", jobs[0])
    assert out["status"] == "human" and out["reason"] == "login_failed"
    assert site.logins == 1 and site.logouts == 0  # 不自动重登；关窗也不退出登录（会把正在用的人踢掉）
    assert len(site.saved()) == 1 and all(d.closed for d in maker.drivers)
    snap = host.snapshots()[0]
    assert snap["status"] == "stopped" and snap["reason"] == "login_failed"
    assert [d["status"] for d in snap["docs"]] == ["entering"]  # 结果不明：留「正在录」
    # 人处理好账号后这张再派来（换一批、CC 重启后同样）：先查存没存，查到就记单号，不重录
    site.useruse.clear()
    again = Host(tmp_path, assemblers={"mrerp": factory(Maker())})
    retry = payload(site.url, batch="m2", index=1, total=2, doc="D1", docnum="K-0001")
    out = _handle(again, "t3", retry)
    assert out["status"] == "saved" and out["docnum"] == "PNK-0001" and len(site.saved()) == 1
    out = _handle(again, "t4", payload(site.url, batch="m2", index=2, total=2, doc="D2", docnum="K-0002", ref="IV-2"))
    assert out["status"] == "saved" and len(site.saved()) == 2
    assert not (tmp_path / "mrerp-unsure.json").exists()


def test_kicked_before_saving_stops_the_batch_and_nothing_is_written(app, site, tmp_path):
    seen = {"forms": 0}

    def kick_when_form_opens(path):
        if path.endswith("/aptran/allform.php"):
            seen["forms"] += 1
            if seen["forms"] == 2:  # 第一次是探路；第二次是开新单那一下（会计本人刚好登录）
                site.kick_now()
                site.on_request = None
    site.on_request = kick_when_form_opens
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="F-0001"))
    assert out["status"] == "human" and out["reason"] == "login_failed"
    assert site.saved() == [] and site.logins == 1 and site.logouts == 0


def test_a_network_break_after_the_save_resumes_and_finds_the_document_instead_of_entering_it_again(app, site, tmp_path):
    site.drop_after_commit, site.down_after_commit_s = True, 1.5  # 写了库、回包前断开，网络一会儿才恢复
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="N-0001"))
    assert out["status"] == "saved" and out["docnum"] == "PNN-0001"
    assert len(site.saved()) == 1 and site.logins == 2  # 网络恢复后重新登录、在列表里找到，没重录


def test_a_save_the_browser_sent_twice_is_reported_not_hidden(app, site, tmp_path):
    # 写了库、回包前断开、网络马上又通：浏览器内核会在新连接上自动重发一次存档请求，MR.ERP 写了两张（第 0 步要核对真站点会不会）
    site.drop_after_commit = True
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="T-0001"))
    saved = site.saved()
    if len(saved) == 1:
        assert out["status"] == "saved"  # 这个内核版本没重发：照常
        return
    assert out["status"] == "human" and out["reason"] == "readback_mismatch" and "more than once" in out["detail"]
    assert len(saved) == 2  # 不删不改，交人


def test_a_network_outage_at_save_waits_then_enters_the_document_once(app, site, tmp_path):
    site.down_on_save_s = 1.5  # 存档那一下断网（请求没到库），一会儿恢复
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="W-0001"))
    assert out["status"] == "saved" and len(site.saved()) == 1
    assert any("network_wait" == e for e in [host.snapshots()[0].get("reason"), "network_wait"])


def test_a_save_refused_by_mrerp_writes_nothing_and_goes_to_a_person(app, site, tmp_path):
    site.fail_save = "Error : Duplicate entry 'PNX-0001' for key 'aptrandocno'"
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="X-0001"))
    assert out["status"] == "human" and out["reason"] == "unknown_screen" and "Duplicate" in out["detail"]
    assert site.saved() == []


def test_giving_up_leaves_no_document(app, site, tmp_path):
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, party="NOPE", docnum="G-0001"))
    assert out["status"] == "human" and out["reason"] == "party_missing"
    assert site.saved() == [] and "GET /aptran/allview.php" in site.log[-6:]  # 放弃 = 不存、回到列表


def test_an_unknown_dialog_goes_to_a_person(app, site, tmp_path):
    site.alert_on_form = "ระบบจะปิดปรับปรุงเวลา 22:00"
    host = _host(tmp_path, Maker())
    out = _handle(host, "t1", payload(site.url, docnum="U-0001"))
    assert out["status"] == "human" and out["reason"] == "unknown_screen" and "22:00" in out["detail"]
    assert site.saved() == []


def test_someone_typing_in_the_mrerp_window_pauses_the_batch_until_they_stop(app, site, tmp_path):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    maker = Maker()
    host = _host(tmp_path, maker, idle_s=0.8)
    hit = {"armed": False, "sent": 0}

    def arm(path):
        if path.endswith("/component/checkcoststd.php"):  # 录到明细时
            hit["armed"] = True
    site.on_request = arm

    def tick():
        if hit["armed"] and hit["sent"] < 2 and maker.drivers:
            driver = maker.drivers[-1]
            target = driver.view.focusProxy() or driver.window
            QApplication.sendEvent(target, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.NoModifier, "a"))
            hit["sent"] += 1

    out = spin(lambda: host.handle("t1", payload(site.url, docnum="H-0001"), None), timeout=180, tick=tick)
    assert out["status"] == "saved" and len(site.saved()) == 1
    pauses = host.snapshots()[0]["pauses"]
    assert pauses and pauses[0]["reason"] == "user_active" and pauses[0]["source"] == "mrerp_window"
    assert pauses[0]["paused_s"] >= 0.5


def test_the_password_never_reaches_logs_results_or_batch_files(app, site, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    targets = []
    host = Host(tmp_path, assemblers={"mrerp": factory(Maker(), secrets_seen=targets)})
    results = [_handle(host, "t1", payload(site.url, index=1, total=2, doc="D1", docnum="C-0001")),
               _handle(host, "t2", payload(site.url, index=2, total=2, doc="D2", docnum="C-0002", ref="IV-2"))]
    assert [r["status"] for r in results] == ["saved", "saved"]
    assert targets == [f"Pearnly CC/MR.ERP/{site.url.split('://')[1].split(':')[0]}/conn1"]  # 只在登录页取一次，目标名带网址与连接号
    blob = json.dumps(results, ensure_ascii=False) + json.dumps(host.snapshots(), ensure_ascii=False) + caplog.text
    for path in Path(tmp_path).rglob("*"):
        if path.is_file():
            blob += path.read_bytes().decode("latin-1")
    assert PASSWORD not in blob and "ccbot" not in caplog.text
    shots = list(Path(tmp_path, "evidence").rglob("*.png"))
    assert len(shots) == 2  # 只有存档后的查看页截图（登录页不截）


def test_an_unverified_recipe_is_not_entered_without_the_developer_switch(site, tmp_path, monkeypatch):
    monkeypatch.delenv("PEARNLY_CC_MRERP_UNVERIFIED", raising=False)
    qapp()
    maker = Maker()
    host = _host(tmp_path, maker)
    out = _handle(host, "t1", payload(site.url))
    assert out["status"] == "human" and out["reason"] == "unsupported"
    assert site.log == [] and maker.drivers == []  # 没核对过的配方：不开窗口、不登录
