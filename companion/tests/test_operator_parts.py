# -*- coding: utf-8 -*-
"""代录各部件：配方、草稿单、预检、锁屏 / 有人在用、自动登录与公司核对、读账套、存档截图、轻量（不代录零占用）。"""
from __future__ import annotations

import ast
import datetime as dt
import sys
from pathlib import Path

import dbf
import pytest
from _fake_express import FakeBook, FakeExpress, samples, tiny_png

from companion.express_operator import book as B
from companion.express_operator import precheck
from companion.express_operator import recipe as R
from companion.express_operator.draft import Draft, Line, from_payload
from companion.express_operator.engine import Engine, NeedHuman, Stopped
from companion.express_operator.presence import Presence
from companion.express_operator.probe import ProbeStore, probe_form
from companion.express_operator.session import Session

RECIPE = R.load()
PKG = Path(__file__).resolve().parents[1] / "src" / "companion" / "express_operator"


# ── 配方与草稿单 ────────────────────────────────────────────────────────────
def test_recipe_phase_zero_forms_are_verified_and_the_rest_are_registered():
    assert {t for t in RECIPE.forms if RECIPE.supported(t)} == {"RR", "HP", "IV", "HS"}
    assert RECIPE.phase("HS") == 0 and RECIPE.phase("RE") == 1 and RECIPE.phase("GL") == 2 and RECIPE.phase("RL") == 3
    assert not RECIPE.supported("RE")
    assert RECIPE.match_dialog("Message#. 4224 ยอดคงเหลือ")["reason"] == "negative_stock"
    assert RECIPE.match_dialog("Message#. 70 วันที่ไม่อยู่ในงวด")["reason"] == "period_outside"
    assert RECIPE.match_dialog(r"Error -70 (90621) Reading File")["outcome"] == "crash"
    assert R.versions() == ["1.0.1.94"]


def test_recipe_rejects_a_broken_form():
    data = dict(RECIPE.data, forms={"RR": {**RECIPE.data["forms"]["RR"], "header": {
        **RECIPE.data["forms"]["RR"]["header"], "values": {"วันที่": "price"}}}})
    with pytest.raises(R.RecipeError):
        R.Recipe(data)


def test_draft_from_the_direct_write_payload():
    payload = {"doctype": "RR", "docdate_be": "691020", "ref_no": "INV-1", "supplier": {"code": "P001"},
               "base_amount": "650.00", "vat_amount": "45.50", "total_amount": "695.50", "vat_rate": "7",
               "items": [{"code": "501780", "qty": "5", "amount": "600.00", "unit": "ชิ้น"},
                         {"code": "500310", "qty": "2", "unit_price": "25", "amount": "50.00", "loccod": "02"}]}
    d = from_payload("T1", payload)
    assert (d.doc_type, d.date, d.party_code, d.ref_no) == ("RR", "2026-10-20", "P001", "INV-1")
    assert [(l.item_code, l.qty, l.unit_price, l.loc) for l in d.lines] == [("501780", "5", "120", ""),
                                                                            ("500310", "2", "25", "02")]
    assert d.date_be == "20/10/69" and str(d.totals()["total"]) == "695.50" and not d.vat_included
    sale = from_payload("T2", {"doctype": "HS", "docdate_be": "691023", "customer": {"code": "CASH"},
                               "operator": {"vat_included": True}, "items": [{"code": "500310", "qty": "4",
                                                                               "unit_price": "53.50"}]})
    assert sale.party_code == "CASH" and sale.vat_included and str(sale.totals()["vat"]) == "14.00"
    # 供应商票号超过 REFNUM 的 15 字节：照直写的缩法（10-27 实机：Express 栏位只收前 15 字节，填完核对对不上）
    long = from_payload("T3", {**payload, "ref_no": "PDM-INV-0926-018-OP"})
    assert long.ref_no == "PDMINV0926018OP"


# ── 预检 ──────────────────────────────────────────────────────────────────
def test_precheck_lists_what_a_human_must_decide():
    sim = FakeExpress(RECIPE.data)
    book = FakeBook(sim)
    book.masters["APMAS"].append({"SUPCOD": "P009", "VATRAT": 0, "_del": False})
    sim.book["APTRN"].append({"_row": 0, "_del": False, "DOCNUM": "RR0000001", "SUPCOD": "P001", "REFNUM": "INV-7781",
                              "NETAMT": 1.0})
    drafts = samples() + [
        Draft("NEWP", "RR", "2026-10-20", "P777", [Line("501780", "1", "1")], ref_no="A"),
        Draft("NEWI", "RR", "2026-10-20", "P001", [Line("ZZZ", "1", "1")], ref_no="B"),
        Draft("TAX", "RR", "2026-10-20", "P009", [Line("501780", "1", "1")], ref_no="C"),
        Draft("LOCK", "RR", "2025-01-05", "P001", [Line("501780", "1", "1")], ref_no="D"),
        Draft("RE", "RE", "2026-10-20", "5001", [Line("501780", "1", "1")]),
    ]
    issues = precheck.check(drafts, book, RECIPE,
                            period=lambda day: "period_locked" if day < dt.date(2026, 1, 1) else None)
    reasons = {k: [i["reason"] for i in v] for k, v in issues.items()}
    assert reasons == {"S-RR-1": ["duplicate_ref"], "NEWP": ["party_missing"], "NEWI": ["item_missing"],
                       "TAX": ["tax_setting_mismatch"], "LOCK": ["period_locked"], "RE": ["unsupported"]}
    assert issues["RE"][0]["phase"] == 1


# ── 锁屏 / 有人在用：只在真冲突时停，留证据 ───────────────────────────────────
class Signals:
    def __init__(self):
        self.lock, self.last, self.now = False, 0, 0

    def locked(self):
        return self.lock

    def last_input_tick(self):
        return self.last

    def now_tick(self):
        return self.now


def _presence(sig, own=0, *, background=False, front="ExpressI.exe", **kw):
    events = []

    def sleep(seconds):
        sig.now += int(seconds * 1000)
        for when, change in list(kw.get("timeline", {}).items()):
            if sig.now >= when:
                change(sig)
                kw["timeline"].pop(when)

    p = Presence(sig, lambda: own, stop_requested=kw.get("stop", lambda: False),
                 on_pause=lambda r: events.append(("pause", r)), on_resume=lambda: events.append(("resume",)),
                 idle_s=30, poll_s=3, sleep=sleep, background=background,
                 foreground=lambda: {"exe": front, "title": "t"}, clock=lambda: sig.now / 1000)
    return p, events


def test_login_keystrokes_before_the_batch_never_count():
    """10-26 实机：用户只登录过，旧口径却把登录时的按键当成「有人在用」，每张白等 2 分钟。"""
    sig = Signals()
    sig.last, sig.now = 5_000, 60_000
    p, events = _presence(sig, own=0)
    p()
    p()
    assert events == [] and p.evidence == []


def test_new_input_while_the_program_is_idle_pauses_with_evidence():
    sig = Signals()
    sig.now = 10_000
    p, events = _presence(sig, own=9_000)
    p()  # 开局
    sig.last, sig.now = 12_000, 12_100
    p()
    assert events == [("pause", "user_active"), ("resume",)]
    ev = p.evidence[0]
    assert ev["reason"] == "user_active" and ev["input_tick"] == 12_000 and ev["own_tick"] == 9_000
    assert ev["foreground"] == "ExpressI.exe" and ev["paused_s"] >= 30
    events.clear()
    p()  # 这次输入已经处理过
    assert events == []


def test_locked_screen_pauses_the_keyboard_way_until_unlocked():
    sig = Signals()
    p, events = _presence(sig)
    p()
    sig.lock = True
    p.sleep = lambda s: (setattr(sig, "now", sig.now + int(s * 1000)),
                         sig.now >= 30_000 and setattr(sig, "lock", False))
    p()
    assert events[0] == ("pause", "screen_locked") and events[-1] == ("resume",) and sig.now >= 30_000


def test_background_way_ignores_other_programs_and_the_lock_screen():
    sig = Signals()
    p, events = _presence(sig, own=0, background=True, front="Notepad.exe")
    p()
    sig.lock = True
    sig.last, sig.now = 50_000, 50_100  # 有人在记事本里打字、又锁了屏
    p()
    assert events == [] and p.evidence == []


def test_background_way_pauses_only_when_someone_works_in_express():
    sig = Signals()
    p, events = _presence(sig, own=0, background=True, front="ExpressI.exe")
    p()
    sig.last, sig.now = 50_000, 50_100
    p()
    assert events[0] == ("pause", "user_active") and p.evidence[0]["foreground"] == "ExpressI.exe"


class HookSignals(Signals):
    """挂上了低级键鼠钩子：``last`` 不含 CC 自己发的输入（dwExtraInfo 带 CC 签名的钩子不记）。"""

    def __init__(self):
        super().__init__()
        self.hardware_only, self.started, self.stopped = True, 0, 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def test_hooks_go_on_at_the_first_check_and_off_when_the_batch_closes():
    sig = HookSignals()
    p, _ = _presence(sig, background=True)
    p()
    p()
    assert (sig.started, sig.stopped) == (1, 0)
    p.close()
    p()
    assert (sig.started, sig.stopped) == (2, 1)


def test_cc_own_input_never_pauses_while_hooks_are_on():
    """主会话 10-30：CC 自己发的键（带 CC 签名）不算有人在用；钩子不记它，last 不动。"""
    sig = HookSignals()
    p, events = _presence(sig, own=0, background=True, front="ExpressI.exe")
    p()
    sig.now = 50_000  # CC 在 Express 里发了一串键：钩子不记，系统最后输入时间也不看
    p()
    assert events == [] and p.evidence == []


def test_a_human_key_right_after_our_own_send_still_pauses_with_hooks():
    """钩子分得出 CC 自己发的：别的输入（本机键鼠、远程软件、别的程序）紧跟着 CC 发键也算，不再用「0.5 秒内不算」这道粗筛。"""
    sig = HookSignals()
    sig.now = 10_000
    p, events = _presence(sig, own=12_000, background=True, front="ExpressI.exe")
    p()
    sig.last, sig.now = 12_100, 12_200
    p()
    assert events[0] == ("pause", "user_active")
    assert p.evidence[0]["source"] == "hook" and p.evidence[0]["input_tick"] == 12_100


def test_without_hooks_the_old_filter_stays_as_fallback():
    sig = HookSignals()
    sig.hardware_only = False  # 钩子没挂上 / 被系统摘了：退回系统最后输入时间
    sig.now = 10_000
    p, events = _presence(sig, own=12_000, background=True, front="ExpressI.exe")
    p()
    sig.last, sig.now = 12_100, 12_200
    p()
    assert events == []
    sig.last, sig.now = 13_000, 13_100
    p()
    assert events[0] == ("pause", "user_active") and p.evidence[0]["source"] == "last_input"


def test_stop_while_waiting_raises_stopped():
    sig = Signals()
    p, _ = _presence(sig, stop=lambda: True)
    p()
    sig.lock = True
    with pytest.raises(Stopped):
        p()


# ── 开 Express、登录、公司核对、退出 ──────────────────────────────────────────
def test_session_uses_express_already_open_on_this_book_and_closes_it():
    sim = FakeExpress(RECIPE.data, company="บริษัท ทดสอบ จำกัด (/x/TEST) by BIT9")
    s = Session(sim, RECIPE, express_exe="", account_dir="/x/TEST", company_name="บริษัท ทดสอบ จำกัด",
                credential_target="t", account_open=lambda _d: True, sleep=lambda _s: None)
    s.ensure_open()
    s.close()
    assert not sim.alive


def test_session_refuses_another_company():
    sim = FakeExpress(RECIPE.data)
    s = Session(sim, RECIPE, express_exe="", account_dir="/x/TEST", company_name="บริษัท ทดสอบ จำกัด",
                credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None)
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "company_mismatch"


def test_session_recognizes_the_company_from_the_status_bar_and_never_closes_another_company():
    sim = FakeExpress(RECIPE.data, company=r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9")
    ok = Session(sim, RECIPE, express_exe="", account_dir=r"\\accserver\ACCOUNT\69EXP\test", company_name="บริษัท Pearnly จำกัด",
                 credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None)
    ok.ensure_open()  # 主菜单下 Express 不一定独占 ISRUN：认状态栏里的账套路径（10-26 实机）
    other = FakeExpress(RECIPE.data, company=r"บริษัท อื่น จำกัด (S:\2569\EXP69\6902ASC) by BIT9")
    s = Session(other, RECIPE, express_exe="", account_dir=r"\\accserver\ACCOUNT\69EXP\test", company_name="บริษัท Pearnly จำกัด",
                credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None)
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "company_mismatch" and "6902ASC" in e.value.detail
    s.close()
    assert other.alive  # 公司对不上：那是别人在用的 Express，不退


@pytest.mark.parametrize("company, shown", [
    ("บริษัท ทดสอบ (ไทยแลนด์) จำกัด", "บริษัท\xa0ทดสอบ\xa0(ไทยแลนด์)\xa0จำกัด"),  # 一对括号（11-18 实机复现的样子）
    ("บริษัท เอ (ไทยแลนด์) (สาขา 2) จำกัด", "บริษัท เอ (ไทยแลนด์) (สาขา 2) จำกัด"),            # 两对括号
    ("Made by Hand Co., Ltd.", "Made by Hand Co., Ltd."),                                          # 名字里含 by
    ("บริษัท Pearnly จำกัด", "บริษัท Pearnly จำกัด"),                                              # 没有括号
])
def test_the_status_bar_is_read_whatever_the_company_name_holds(company, shown):
    """状态栏「公司名 (账套路径) by 用户」的账套路径取最后一对括号：公司名带括号、两对括号、含 by、没有括号都认得出；
    名字对、路径换一个仍判不对。"""
    book = "\\\\fileserver\\ACCOUNT\\69EXP\\TBOOK"
    ok = Session(FakeExpress(RECIPE.data, company=f"{shown} ({book}) by BIT9"), RECIPE, express_exe="", account_dir=book,
                 company_name=company, credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None)
    ok.ensure_open()
    other = FakeExpress(RECIPE.data, company=f"{shown} ({book}2) by BIT9")
    s = Session(other, RECIPE, express_exe="", account_dir=book, company_name=company, credential_target="t",
                account_open=lambda _d: False, sleep=lambda _s: None)
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "company_mismatch"


def test_a_company_list_name_starting_with_an_initial_still_matches():
    """公司列表名去排序前缀时，「J.P. Trading」这类以缩写打头的名字原样也认（去前缀会去成「P. Trading」）。"""
    sim = FakeExpress(RECIPE.data, inject={"picked": r"J.P. Trading Co., Ltd. (\\fileserver\ACCOUNT\69EXP\TEST) by BIT9"})
    sim.dialog("companies", "", "ชื่อข้อมูล", buttons=COMPANIES)
    s = Session(sim, RECIPE, express_exe="", account_dir=r"\\fileserver\ACCOUNT\69EXP\test", company_name="",
                credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None,
                registry=lambda: (3, "J.P. Trading Co., Ltd."))
    s.ensure_open()
    assert s.attached


def _session(sim, **kw):
    return Session(sim, RECIPE, express_exe="", account_dir=r"\\accserver\ACCOUNT\69EXP\test", company_name="บริษัท Pearnly จำกัด",
                   credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None, **kw)


COMPANIES = ("ตกลง", "ยกเลิก", "ค้นหา", "เรียงใหม่ <Tab>")


def test_session_picks_the_company_by_registry_row_only_then_checks_the_status_bar():
    """主会话 10-27 / 10-30：公司列表上只按账套路径定的登记表行号选这家（不查名字：同前缀的公司会被查找跳过去），
    进去后状态栏公司名与账套路径逐字核对。"""
    sim = FakeExpress(RECIPE.data, inject={"picked": r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9"})
    sim.dialog("companies", "", "ชื่อข้อมูล", buttons=COMPANIES)
    s = _session(sim, registry=lambda: (35, "9.บริษัท Pearnly จำกัด"))
    s.ensure_open()
    assert sim.picks == [{"row": 35, "name": ""}] and s.attached


def test_a_wrong_company_after_picking_stops_the_task_and_closes_express():
    for status in (r"บริษัท อื่น จำกัด (S:\2569\EXP69\6902ASC) by BIT9",      # 进错了公司
                   r"บริษัท Pearnly จำกัด (S:\2569\EXP69\6902ASC) by BIT9",   # 名字对、路径不对
                   r"บริษัท อื่น จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9",  # 路径对、名字不对
                   "Express Accounting"):                                       # 状态栏读不出
        sim = FakeExpress(RECIPE.data, inject={"picked": status})
        sim.dialog("companies", "", "ชื่อข้อมูล", buttons=COMPANIES)
        s = _session(sim, registry=lambda: (34, "9.บริษัท Pearnly จำกัด"))
        s.opened_by_us = True
        with pytest.raises(NeedHuman) as e:
            s.ensure_open()
        assert e.value.reason == "company_mismatch", status
        s.close()
        assert not sim.alive, status  # 我们开的 Express 进错了：正常退出，什么都不写


def test_session_presses_ok_on_the_work_date_box_then_checks_the_company():
    sim = FakeExpress(RECIPE.data, company=r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9")
    sim.dialog("work_date", "", "วันที่ทำการ", buttons=("ตกลง",))
    s = _session(sim)
    assert s.stage() == "work_date"
    s.ensure_open()
    assert not sim.dialogs and s.attached


def test_session_stops_at_the_company_list_when_the_book_is_not_in_the_registry():
    sim = FakeExpress(RECIPE.data)
    sim.dialog("companies", "", "ชื่อข้อมูล", buttons=("ตกลง", "ยกเลิก", "ค้นหา", "เรียงใหม่ <Tab>"))
    s = _session(sim, registry=lambda: (0, ""))
    assert s.stage() == "company"
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "company_pick" and sim.dialogs and not getattr(sim, "picks", [])  # 不在默认行上按确定
    s.close()
    assert sim.alive  # Express 留在公司列表上给人选，不退出


class TwoExpress:
    """两个 Express 同时开着（两个代录会话并行，§15.1）：一个在别家公司，一个在试点账套。"""

    def __init__(self):
        self.sims = {11: FakeExpress(RECIPE.data, company=r"บริษัท อื่น จำกัด (S:\2569\EXP69\6902ASC) by BIT9"),
                     22: FakeExpress(RECIPE.data, company=r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9")}
        self.pid = None

    def bind(self, pid):
        self.pid = pid

    def instances(self):
        return [dict(sim.windows()[0], pid=pid) for pid, sim in self.sims.items()]

    def act(self, actions, windows=True):
        return self.sims[self.pid or 11].act(actions, windows)


def test_parallel_sessions_each_bind_their_own_express():
    from companion.express_operator import session as S

    desk = TwoExpress()
    s = _session(desk, own_pids=lambda: [11, 22])  # 两个都是 CC 自己起的
    s.ensure_open()
    assert desk.pid == 22 and 22 in S.claimed()  # 认的是状态栏写着这本账套的那一个，别家公司那个不碰
    s.close()
    assert not desk.sims[22].alive and desk.sims[11].alive and 22 not in S.claimed()


def test_an_express_the_person_opened_on_the_same_book_is_never_taken_over():
    """主会话 10-30：人自己开着同一账套的 Express 时，CC 另开自己的，绝不接管人的窗口（只认 CC 启动过的进程号）。"""
    desk = TwoExpress()  # 22 是人开的、停在试点账套
    s = _session(desk)
    s._bind_existing()
    assert desk.pid is None and s.pid is None and s.stage() == "none"  # 当作没开：下面去启动 CC 自己的
    launched = []
    s2 = _session(desk, launch=lambda exe: launched.append(exe) or 33)
    with pytest.raises(NeedHuman) as e:  # 假机器上没设自动登录：照旧请人设好，不去碰 22
        s2.ensure_open()
    assert e.value.reason == "login_missing" and desk.pid is None and desk.sims[22].alive


def test_session_counts_the_company_list_as_past_the_login_while_the_login_window_is_still_closing():
    sim = FakeExpress(RECIPE.data)
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    sim.dialog("companies", "", "ชื่อข้อมูล", buttons=("ตกลง", "ยกเลิก", "ค้นหา", "เรียงใหม่ <Tab>"))
    assert _session(sim).stage() == "company"  # 10-27 实机：两个窗口同时在的那一下，已经登录过了


class CompaniesAfterLogin:
    """10-27 实机的时序：按了 OK，公司列表弹出来，登录窗还要再在一下才关。"""

    def __init__(self, sim):
        self.sim, self.linger = sim, 0

    def act(self, actions, windows=True):
        reply = self.sim.act(actions, windows)
        if actions and actions[0].get("do") == "login_fill":
            self.sim.dialog("companies", "", "ชื่อข้อมูล", buttons=("ตกลง", "ยกเลิก", "ค้นหา", "เรียงใหม่ <Tab>"))
            self.linger = 2
        elif actions and actions[0].get("do") == "windows" and self.linger:
            self.linger -= 1
            reply["results"][0]["windows"].append({"hwnd": 9, "title": "เข้าสู่ระบบ/LogIn", "cls": "#32770",
                                                    "owned": True, "rect": [0, 0, 300, 200], "texts": ["&OK"]})
        return reply


def test_the_company_list_after_login_is_never_taken_for_a_login_error(monkeypatch):
    from companion.express_operator import credentials

    sim = FakeExpress(RECIPE.data)
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    monkeypatch.setattr(credentials, "read", lambda _t: ("BIT9", "secret"))
    s = _session(CompaniesAfterLogin(sim))
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert (e.value.reason, e.value.detail) == ("company_pick", "company list: pick by hand")  # 登录成功，停在公司列表请人选


def test_session_logs_in_from_the_stored_login_once(monkeypatch):
    from companion.express_operator import credentials

    sim = FakeExpress(RECIPE.data, company=r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9")
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    monkeypatch.setattr(credentials, "read", lambda _t: ("BIT9", "secret"))
    s = _session(sim)
    assert s.stage() == "login"
    s.ensure_open()
    assert sim.logged_in_as == "BIT9" and s.attached
    wrong = FakeExpress(RECIPE.data)
    wrong.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    monkeypatch.setattr(credentials, "read", lambda _t: ("BIT9", "changed"))
    with pytest.raises(NeedHuman) as e:
        _session(wrong).ensure_open()
    assert e.value.reason == "login_failed"  # 只试一次，不重试（防锁账号）


class Starting:
    """刚启动的 Express（10-27 实机）：头几次只有一个空的主框架（还没有状态栏字），之后才弹登录窗。"""

    def __init__(self, sim, empty_reads=3):
        self.sim, self.left = sim, empty_reads

    def act(self, actions, windows=True):
        reply = self.sim.act(actions, windows)
        if actions and actions[0].get("do") == "windows":
            found = reply["results"][0]["windows"]
            if self.left > 0:  # 头几次：登录窗还没弹
                self.left -= 1
                found = [w for w in found if not w["owned"]]
            if not getattr(self.sim, "logged_in_as", None):  # 还没登录进公司：主框架没有状态栏字
                found = [dict(w, texts=[]) if not w["owned"] else w for w in found]
            reply["results"][0]["windows"] = found
        return reply


def test_session_waits_while_express_is_still_starting_instead_of_calling_it_another_company(monkeypatch):
    from companion.express_operator import credentials

    sim = FakeExpress(RECIPE.data, company=r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9")
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    monkeypatch.setattr(credentials, "read", lambda _t: ("BIT9", "secret"))
    s = _session(Starting(sim))
    s.ensure_open()
    assert sim.logged_in_as == "BIT9" and s.attached


def test_session_stuck_on_an_empty_frame_reports_an_unknown_screen_not_another_company():
    sim = FakeExpress(RECIPE.data)
    s = _session(Starting(sim, empty_reads=10_000))
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "unknown_screen" and e.value.detail.startswith("main")


def test_operator_opens_the_express_program_of_the_documents_year(tmp_path):
    from types import SimpleNamespace

    from companion.express_operator.host import express_program

    year = tmp_path / "69EXP"
    year.mkdir()
    (year / "ExpressI.exe").write_bytes(b"")
    member = str(tmp_path / "68EXP" / "ExpressI.exe")
    assert express_program(SimpleNamespace(express_root=str(year), express_exe=member)) == str(year / "ExpressI.exe")
    assert express_program(SimpleNamespace(express_root=str(tmp_path / "70EXP"), express_exe=member)) == member


def test_session_reports_what_express_said_when_the_login_is_refused_and_never_retries(monkeypatch):
    from companion.express_operator import credentials

    sim = FakeExpress(RECIPE.data, inject={"password": "secret", "login_message": "รหัสผ่านไม่ถูกต้อง"})
    sim.dialog("login", "เข้าสู่ระบบ/LogIn", "รหัสผู้ใช้", buttons=("OK", "Cancel"))
    monkeypatch.setattr(credentials, "read", lambda _t: ("BIT9", "changed"))
    with pytest.raises(NeedHuman) as e:
        _session(sim).ensure_open()
    assert e.value.reason == "login_failed" and "รหัสผ่านไม่ถูกต้อง" in e.value.detail and "changed" not in e.value.detail
    assert sim.login_fills == 1  # 只试一次（防锁账号）


def test_session_without_login_set_up_needs_a_human():
    sim = FakeExpress(RECIPE.data)
    sim.alive = False
    s = Session(sim, RECIPE, express_exe="", account_dir="/x/TEST", company_name="", credential_target="t",
                sleep=lambda _s: None)
    with pytest.raises(NeedHuman) as e:
        s.ensure_open()
    assert e.value.reason == "login_missing"  # 登录窗口样子还没核过（login.verified=false）、这台也没存登录


# ── 探路 ──────────────────────────────────────────────────────────────────
def test_probe_walks_the_header_without_saving(tmp_path):
    sim = FakeExpress(RECIPE.data)
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    result = probe_form(engine, "RR")
    assert result["ok"] and "ประเภทราคา" in result["labels"]
    assert sim.book["APTRN"] == [] and not sim.editing  # 不保存任何单
    store = ProbeStore(tmp_path / "probe.json")
    store.put("uid1", RECIPE.version, "RR", result)
    assert store.get("uid1", RECIPE.version, "RR")["ok"]


def test_probe_reports_labels_this_book_does_not_have():
    data = dict(RECIPE.data)
    data["forms"] = dict(data["forms"])
    rr = dict(data["forms"]["RR"])
    rr["header"] = dict(rr["header"], labels=[x for x in rr["header"]["labels"] if x != "เลขที่บิล"])
    data["forms"]["RR"] = rr
    sim = FakeExpress(data)  # 这本账套的赊购单没有「เลขที่บิล」格
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    result = probe_form(engine, "RR")
    assert not result["ok"] and result["missing"] == ["เลขที่บิล"]


# ── 读账套 ──────────────────────────────────────────────────────────────────
def test_dbf_book_reads_only_new_rows_from_a_copy(tmp_path):
    folder = tmp_path / "TEST"
    folder.mkdir()
    table = dbf.Table(str(folder / "APTRN.DBF"), "DOCNUM C(12); DOCDAT D; SUPCOD C(10); REFNUM C(15); NETVAL N(12,2); "
                      "VATAMT N(12,2); NETAMT N(12,2)", codepage="cp874")
    table.open(dbf.READ_WRITE)
    table.append(("RR0000001", dt.date(2026, 10, 1), "P001", "OLD", 1, 0.07, 1.07))
    table.close()
    book = B.DbfBook(folder)
    before = book.counts(["APTRN"])
    assert before == {"APTRN": 1}
    table.open(dbf.READ_WRITE)
    table.append(("RR0000002", dt.date(2026, 10, 20), "P001", "INV-1", 100, 7, 107))
    table.close()
    rows = book.rows_since(before, ["APTRN"])
    assert [r["DOCNUM"] for r in rows["APTRN"]] == ["RR0000002"] and rows["APTRN"][0]["DOCDAT"] == "2026-10-20"
    d = Draft("x", "RR", "2026-10-20", "P001", [Line("1", "1", "100")], ref_no="INV-1")
    assert B.find_saved(d, rows)["docnum"] == "RR0000002"
    assert book.codes("APTRN", "SUPCOD") == {"P001"}
    raw = bytearray((folder / "APTRN.DBF").read_bytes())  # 把最后一行的 NETVAL 改成只有一个「.」（真账套见过）
    header_len = int.from_bytes(raw[8:10], "little")
    record_len = int.from_bytes(raw[10:12], "little")
    start = header_len + record_len + 1 + 12 + 8 + 10 + 15
    raw[start:start + 12] = b"           ."
    (folder / "APTRN.DBF").write_bytes(bytes(raw))
    rows = book.rows_since({}, ["APTRN"])["APTRN"]
    assert rows[1]["NETVAL"] is None and rows[1]["DOCNUM"] == "RR0000002"


# ── 存档截图：只存本机，满 30 天自动删 ─────────────────────────────────────────
DAY = 86400.0


def test_evidence_expires_by_the_batch_date_not_the_file_time(tmp_path):
    from companion.express_operator import evidence as E

    start = dt.datetime(2026, 10, 1, 9).timestamp()
    old = E.Evidence(tmp_path, "b-old", batch_date="2026-10-01", now=lambda: start)
    old_png = old.save("S/RR:1", tiny_png())
    new = E.Evidence(tmp_path, "b-new", batch_date="2026-10-25", now=lambda: start)
    new_png = new.save("S-HP-1", tiny_png())
    import os
    os.utime(new_png, (start - 90 * DAY, start - 90 * DAY))  # 文件时间很旧，批次是新的：不删
    later = start + 30 * DAY
    assert E.prune(tmp_path, now=lambda: later) == ["b-old"]
    assert not old_png.exists() and new_png.exists()
    assert E.Evidence(tmp_path, "b-old", now=lambda: later).state("S/RR:1") == E.EXPIRED  # 回看显示已过期，不报错
    assert E.Evidence(tmp_path, "b-old", now=lambda: later).state("never-saved") == E.NONE
    assert new.state("S-HP-1") == E.KEPT
    assert E.prune(tmp_path, now=lambda: later) == []  # 已清过的批次不再动


def test_evidence_only_removes_files_it_wrote(tmp_path):
    from companion.express_operator import evidence as E

    ev = E.Evidence(tmp_path, "b1", batch_date="2026-09-01")
    ev.save("d1", tiny_png())
    foreign = ev.dir / "someone-else.txt"
    foreign.write_text("keep", encoding="utf-8")
    stray = tmp_path / "no-manifest"
    stray.mkdir()
    (stray / "x.png").write_bytes(b"x")
    E.prune(tmp_path, now=lambda: dt.datetime(2026, 10, 26).timestamp())
    assert foreign.exists() and (stray / "x.png").exists()  # 不是自己写的、没有清单的目录都不碰
    assert not (ev.dir / "d1.png").exists()
    E.Evidence(tmp_path, "b2").save("d", tiny_png())
    E.Evidence(tmp_path, "b2").drop()
    assert not (tmp_path / "b2").exists()


def test_batch_tidies_screenshots_before_and_after_and_reports_expired(tmp_path):
    from companion.express_operator import evidence as E
    from companion.express_operator.batch import BatchState, BatchStore, Runner, no_lock

    sim = FakeExpress(RECIPE.data)
    book = FakeBook(sim)
    calls = []
    state = BatchState.new("x", "TEST", str(tmp_path), samples()[:1])
    ev = E.Evidence(tmp_path / "ev", state.id, batch_date="2026-09-01")

    class Session:
        def ensure_open(self):
            sim.alive = True

        def close(self):
            sim.alive = False

    engine = Engine(sim, RECIPE, book, evidence=ev.save, sleep=lambda _s: None)
    r = Runner(state, BatchStore(tmp_path / "b"), engine, Session(), book, lock=no_lock, sleep=lambda _s: None,
               housekeeping=lambda: calls.append(E.prune(tmp_path / "ev", now=lambda: dt.datetime(2026, 10, 26).timestamp())),
               evidence_state=ev.state)
    r.run()
    assert len(calls) == 2 and calls[1] == [state.id]  # 开始前一次（还没截图）、结束后一次（这批日期已满 30 天）
    assert r.snapshot()["docs"][0]["evidence"] == E.EXPIRED


# ── 轻量：不代录时零占用 ──────────────────────────────────────────────────────
def _imports(path: Path):
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            yield node.module or ""


def test_operator_package_is_only_loaded_on_demand_and_stays_light():
    heavy = {"PySide6", "httpx", "pywinauto", "comtypes", "cv2", "mss", "pyautogui", "numpy", "anthropic"}
    for path in PKG.glob("*.py"):
        top = {m.split(".")[0] for m in _imports(path)}
        allowed = {"PySide6"} if path.name == "login_dialog.py" else set()  # 授权登录小窗：托盘本来就有 Qt，点了才导入
        assert not (top & (heavy - allowed)), f"{path.name}: {top & heavy}"


def test_windows_desk_imports_everywhere_but_only_runs_on_windows():
    from companion.express_operator import win32_desk

    if sys.platform != "win32":
        with pytest.raises(RuntimeError):
            win32_desk.WindowsDesk()
    assert win32_desk.png(1, 1, [b"\0\0\0"]).startswith(b"\x89PNG")


# ── 多会话并行：按本机负载放行 ───────────────────────────────────────────────
def test_scheduler_lets_the_first_session_in_and_queues_when_the_computer_is_busy():
    from companion.express_operator.scheduler import Limits, Scheduler

    load = {"cpu": 0.3, "free": 4000}
    s = Scheduler(Limits(), cpu_busy=lambda: load["cpu"], free_mb=lambda: load["free"])
    assert s.room(0) and s.room(3)
    load["cpu"] = 0.9
    assert s.room(0) is True  # 一个都没在录：总要让人录得了
    assert not s.room(1) and s.last["reason"] == "cpu"
    load.update(cpu=0.2, free=700)
    assert not s.room(1) and s.last["reason"] == "memory"
    load["free"] = 4000
    s.report("b1", 3500)
    assert not s.room(1) and s.last["reason"] == "express_slow"
    s.forget("b1")
    assert s.room(1) and s.last["free_mb"] == 4000


def test_readback_takes_the_taxable_base_of_a_document_with_exempt_lines():
    """10-29 TEST2 实测（打开模式）：部分免税的进货单原生进项税记录 AMT01 = 税前 − 免税额、AMTRAT0 = 免税额（RR0000086 / 087
    和答案逐栏一致），读回原来按整张税前比，误判不符。"""
    import datetime as dt

    from companion.express_operator.book import compare
    from companion.express_operator.draft import Draft, Line
    d = Draft("EX", "RR", "2026-01-24", "P001", [Line("A", "1", "100"), Line("B", "1", "50", exempt=True)],
              expected={"net": "150.00", "vat": "7.00", "total": "157.00"})
    head = {"DOCNUM": "RR0000086", "SUPCOD": "P001", "DOCDAT": dt.date(2026, 1, 24), "NETVAL": 150.0, "VATAMT": 7.0,
            "NETAMT": 157.0}
    lines = [{"DOCNUM": "RR0000086", "SEQNUM": "1", "STKCOD": "A", "TRNQTY": 1.0, "UNITPR": 100.0, "TRNVAL": 100.0},
             {"DOCNUM": "RR0000086", "SEQNUM": "2", "STKCOD": "B", "TRNQTY": 1.0, "UNITPR": 50.0, "TRNVAL": 50.0}]
    native = {"APTRN": [head], "STCRD": lines,
              "ISVAT": [{"DOCNUM": "RR0000086", "AMT01": 100.0, "VAT01": 7.0, "AMTRAT0": 50.0}]}
    assert compare(d, native)["ok"], compare(d, native)["diffs"]
    whole_base = {**native, "ISVAT": [{"DOCNUM": "RR0000086", "AMT01": 150.0, "VAT01": 7.0, "AMTRAT0": 0.0}]}
    assert {x["field"] for x in compare(d, whole_base)["diffs"]} == {"isvat.amt", "isvat.exempt"}


def test_session_touches_no_company_list_of_another_express_program():
    """10-29 实机：桌面上开着的是另一套 70EXP 的 Express，公司列表是那套的；按 69EXP 登记表的行号选会进错账套。登录、选公司之前
    先核程序所在的年度文件夹，对不上一个键都不按。同一套程序走不同共享路径（d$ 与 account 共享）照常选。"""
    for exe, ok in ((r"\\accserver\d$\ACCOUNT\70EXP\ExpressI.exe", False),
                    (r"\\Accserver\d$\ACCOUNT\69EXP\ExpressI.exe", True)):
        sim = FakeExpress(RECIPE.data, exe=exe, inject={"picked": r"บริษัท Pearnly จำกัด (\\accserver\ACCOUNT\69EXP\TEST) by BIT9"})
        sim.dialog("companies", "", "ชื่อข้อมูล", buttons=COMPANIES)
        s = Session(sim, RECIPE, express_exe=r"\\accserver\account\69exp\ExpressI.exe",
                    account_dir=r"\\accserver\ACCOUNT\69EXP\test", company_name="บริษัท Pearnly จำกัด",
                    credential_target="t", account_open=lambda _d: False, sleep=lambda _s: None,
                    registry=lambda: (34, "9.บริษัท Pearnly จำกัด"))
        if ok:
            s.ensure_open()
            assert sim.picks and s.attached
            continue
        with pytest.raises(NeedHuman) as e:
            s.ensure_open()
        assert e.value.reason == "company_mismatch" and "70EXP" in e.value.detail
        assert not getattr(sim, "picks", None) and sim.dialogs  # 没在那套的公司列表上按任何键


def test_a_book_opened_through_a_mapped_drive_is_the_same_book():
    """10-29 实机（rec-laptop）：Express 从映射盘 P:（= \\\\accserver\\ACCOUNT）打开，状态栏写「P:\\69EXP\\TEST」，任务配的是网络路径；
    按本机映射换算后是同一本，照常接管。换出来是别处、或换不出来，仍算对不上。"""
    mapping = {"P:": r"\\accserver\ACCOUNT", "Q:": r"\\other\ACCOUNT"}

    def share(path):
        head = path[:2].upper()
        return mapping[head] + path[2:] if head in mapping else (path if path.startswith("\\\\") else "")

    for shown, ok in ((r"P:\69EXP\TEST", True), (r"Q:\69EXP\TEST", False), (r"Z:\69EXP\TEST", False)):
        sim = FakeExpress(RECIPE.data, company=f"บริษัท Pearnly จำกัด ({shown}) by BIT9")
        s = _session(sim, share=share)
        if ok:
            s.ensure_open()
            assert s.attached
            continue
        with pytest.raises(NeedHuman) as e:
            s.ensure_open()
        assert e.value.reason == "company_mismatch", shown


def test_background_way_ignores_another_express_than_the_one_this_batch_uses():
    """主会话 10-30：只有人在 CC 正在用的那个 Express 里操作才停；多批并行时切到别的 Express 不算这一批的冲突。"""
    sig = HookSignals()
    events = []
    p = Presence(sig, lambda: 0, stop_requested=lambda: False, on_pause=lambda r: events.append(r),
                 background=True, foreground=lambda: {"exe": "ExpressI.exe", "title": "t", "mine": False},
                 sleep=lambda _s: None, clock=lambda: 0)
    p()
    sig.last, sig.now = 50_000, 50_100
    p()
    assert events == [] and p.evidence == []
