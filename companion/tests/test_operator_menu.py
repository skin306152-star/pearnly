# -*- coding: utf-8 -*-
"""代录遇到定制菜单（1.12.15，11-05 测试机 T9MANAC7）：按编号 → 文字 → 子菜单里唯一的命令找；子菜单几个命令按载荷的选择
或付款方式定，定不下来只停这一张（menu_choice）；文字对不上交 AI 用 express_menu 选、选对记进本机；任何意外都收尾成明确状态。"""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from _fake_express import FakeBook, FakeExpress, FakeSite, express_settings, tiny_png

from companion.express_operator import contract as K
from companion.express_operator import menu as M
from companion.express_operator import recipe as R
from companion.express_operator.batch import BatchState, BatchStore, Runner, no_lock
from companion.express_operator.desk import MenuAmbiguous, Refused
from companion.express_operator.draft import Draft, Line, from_payload
from companion.express_operator.engine import Engine, NeedHuman
from companion.express_operator.helper import ComputerUseHelper
from companion.express_operator.host import Host
from companion.express_operator.probe import MenuMemory, ProbeStore, probe_form

RECIPE = R.load()
CASH_TEXT = "ซื้อเงินสด-ทั่วไป (ไม่รวม ปตท)"
TMB_TEXT = "ซื้อโดยโอนผ่าน TMB 2-01792-3"


def item(text, cmd=0, items=None, grey=False):
    return {"text": text, "cmd": cmd, "sub": items is not None, "grey": grey, "items": items or []}


def t9_menu(cash_items=None):
    """11-05 测试机 T9MANAC7 的「1.ซื้อ」：2 是子菜单（两套单号），4 是命令；另带一个普通的「2.ขาย」。"""
    buy = [item("1. ใบสั่งซื้อ", 50000),
           item("2. ซื้อเงินสด", items=cash_items if cash_items is not None
                else [item(f"1. {CASH_TEXT}", 50001), item(f"2. {TMB_TEXT}", 50002)]),
           item("4. ซื้อเงินเชื่อ", 50004)]
    sell = [item("2. ขายเงินสด", 60002), item("4. ขายเงินเชื่อ", 60004)]
    return [item("1.ซื้อ", items=buy), item("2.ขาย", items=sell)]


FORMS = {50001: "HP", 50002: "HP", 50003: "HP", 50004: "RR", 60002: "HS", 60004: "IV"}


def hp(doc_id="S-HP-1", **hint):
    return Draft(doc_id, "HP", "2026-10-21", "P002", [Line("500310", "10", "25.00")], ref_no="RC-0921", **hint)


def rr(doc_id="S-RR-1"):
    return Draft(doc_id, "RR", "2026-10-20", "P001", [Line("501780", "2", "1250.00")], ref_no="INV-7781")


class Shots:
    def __init__(self, tmp_path):
        self.root, self.saved = tmp_path / "evidence", []

    def __call__(self, doc_id, png):
        self.saved.append(doc_id)
        return self.root / f"{doc_id}.png"


def engine_for(tmp_path, menus, *, transport=None, store=None, titles=None, evidence=None):
    sim = FakeExpress(RECIPE.data, menus=menus, menu_forms=FORMS, menu_titles=titles)
    helper = ComputerUseHelper(transport, sleep=lambda _s: None) if transport else None
    memory = MenuMemory(store or ProbeStore(tmp_path / "probe.json"), "c:\\book", RECIPE.version)
    engine = Engine(sim, RECIPE, FakeBook(sim), helper=helper, sleep=lambda _s: None, menus=memory,
                    evidence=evidence or Shots(tmp_path))
    return engine, sim


class Scripted:
    """假模型站点：按顺序回一串工具调用（每轮一组），记下请求。"""

    def __init__(self, turns):
        self.turns, self.calls, self.requests = list(turns), [], []

    def __call__(self, kind, request):
        self.calls.append(kind)
        self.requests.append(copy.deepcopy(request))
        uses = self.turns.pop(0) if self.turns else [{"name": "finish", "input": {"status": "need_human", "note": "?"}}]
        n = len(self.calls)
        return {"content": [{"type": "tool_use", "id": f"tu_{n}_{i}", "name": u["name"], "input": u["input"]}
                            for i, u in enumerate(uses)], "stop_reason": "tool_use"}


# ── 纯函数 ──────────────────────────────────────────────────────────────────
def test_resolve_by_number_text_name_and_the_single_command_of_a_submenu():
    tree = t9_menu()
    assert M.resolve(tree, ["2", "4"], "ขายเงินเชื่อ") == (60004, True)  # 编号原样对上
    assert M.resolve(tree, ["1", "4", "1"], "ซื้อเงินเชื่อ") == (50004, False)  # 这家 4 是命令：按单据名认
    assert M.resolve(tree, ["1", "ซื้อเงินเชื่อ"]) == (50004, False)  # 按文字
    assert M.resolve(tree, ["1", "9"], "ซื้อเงินเชื่อ") == (50004, False)  # 编号找不到按单据名
    with pytest.raises(MenuAmbiguous) as caught:
        M.resolve(tree, ["1", "2"], "ซื้อเงินสด")
    assert caught.value.options == [CASH_TEXT, TMB_TEXT] and caught.value.path == ["1", "2"]
    one = t9_menu([item(f"1. {CASH_TEXT}", 50001), item("2. ปิด", 50009, grey=True)])
    assert M.resolve(one, ["1", "2"], "ซื้อเงินสด") == (50001, False)  # 灰掉的不算
    with pytest.raises(Refused):
        M.resolve(tree, ["1", "4", "1"], "อื่น")  # 是命令、路径还没完、名字也对不上：交 AI
    with pytest.raises(Refused):
        M.resolve(tree, ["3"], "")


def test_pick_by_remembered_text_then_by_payment_route():
    options = [CASH_TEXT, TMB_TEXT]
    assert M.pick(options, pay_route="cash") == CASH_TEXT
    assert M.pick(options, pay_route="transfer") == TMB_TEXT
    assert M.pick(options, form_choice=f"2. {TMB_TEXT}") == TMB_TEXT
    assert M.pick(options, form_choice="ซื้อโดยโอนผ่าน TMB", pay_route="cash") == TMB_TEXT  # 记住的优先
    assert M.pick(options) == ""
    assert M.pick(["ซื้อเงินสด สาขา 1", "ซื้อเงินสด สาขา 2"], pay_route="cash") == ""  # 不唯一不猜
    assert M.pick(["ซื้อผ่าน KBANK", "ซื้อผ่าน SCB"], pay_route="transfer") == ""
    assert M.pick(["ซื้อเงินสด", "ซื้อผ่าน kbank"], pay_route="transfer") == "ซื้อผ่าน kbank"


def test_payload_brings_the_two_menu_choices_and_old_payloads_leave_them_empty():
    job = {"doctype": "HP", "docdate_be": "691021", "supplier": {"code": "P002"},
           "items": [{"code": "500310", "qty": "1", "unit_price": "1"}],
           "operator": {"form_choice": TMB_TEXT, "pay_route": "Transfer"}}
    d = from_payload("x", job)
    assert (d.form_choice, d.pay_route) == (TMB_TEXT, "transfer")
    job["operator"] = {"pay_route": "card"}
    d = from_payload("x", job)
    assert (d.form_choice, d.pay_route) == ("", "")
    assert K.level("menu_choice") == K.HUMAN and "menu_choice" not in K.BATCH_STOPPERS


# ── 引擎 ────────────────────────────────────────────────────────────────────
def test_cash_route_picks_the_general_cash_command_and_the_form_opens(tmp_path):
    site = Scripted([])
    engine, sim = engine_for(tmp_path, t9_menu(), transport=site)
    rec = engine.run(hp(pay_route="cash"))
    assert rec["outcome"] == "saved" and rec["readback"]["ok"]
    assert sim.menu_sent == [50001] and site.calls == []
    assert not (tmp_path / "probe.json").exists()  # 业务选择不记在本机


def test_two_commands_and_no_choice_holds_this_document_without_ai(tmp_path):
    site = Scripted([])
    engine, sim = engine_for(tmp_path, t9_menu(), transport=site)
    rec = engine.run(hp())
    assert rec["outcome"] == "need_human" and rec["reason"] == "menu_choice"
    assert rec["menu_options"] == [CASH_TEXT, TMB_TEXT]
    assert site.calls == [] and sim.form is None and sim.menu_sent == []


def test_remembered_text_opens_the_transfer_series_with_its_own_window_name(tmp_path):
    engine, sim = engine_for(tmp_path, t9_menu(), titles={50002: "HP - ซื้อโดยโอนผ่าน TMB"})
    rec = engine.run(hp(form_choice=TMB_TEXT))
    assert rec["outcome"] == "saved" and sim.menu_sent == [50002]
    # 同一种单换了另一套：不沿用开着的窗口，关掉按这张的选择重开
    rec = engine.run(hp("S-HP-2", pay_route="cash"))
    assert rec["outcome"] == "saved" and sim.menu_sent == [50002, 50001]


def test_a_submenu_with_one_command_and_a_command_where_the_recipe_has_a_submenu_need_no_ai(tmp_path):
    site = Scripted([])
    engine, sim = engine_for(tmp_path, t9_menu([item(f"1. {CASH_TEXT}", 50001)]), transport=site)
    assert engine.run(hp())["outcome"] == "saved"
    assert engine.run(rr())["outcome"] == "saved"  # 配方 1→4→1，这家 4 就是命令
    assert sim.menu_sent == [50001, 50004] and site.calls == []


def renamed_menu():
    """这家把现购改名、换了编号（文字对不上配方）。"""
    return [item("1.ซื้อ", items=[item("3. ซื้อสด", 50003), item("4. ซื้อเงินเชื่อ", 50004)])]


def test_menu_text_mismatch_goes_to_ai_which_picks_with_express_menu_and_the_path_is_remembered(tmp_path):
    site = Scripted([[{"name": "express_menu", "input": {"read": True}}],
                     [{"name": "express_menu", "input": {"path": ["1", "ซื้อสด"]}}],
                     [{"name": "finish", "input": {"status": "resolved", "note": "opened"}}]])
    store = ProbeStore(tmp_path / "probe.json")
    engine, sim = engine_for(tmp_path, renamed_menu(), transport=site, store=store)
    rec = engine.run(hp())
    assert rec["outcome"] == "saved" and sim.menu_sent == [50003]
    assert {"reason": "ai_resolved", "where": "form_menu"} in rec["reports"]
    first = site.requests[0]
    assert any(t.get("name") == "express_menu" for t in first["tools"])
    assert "ซื้อสด" in first["messages"][0]["content"][0]["text"]  # 目标里带菜单树
    results = [b for b in site.requests[1]["messages"][-1]["content"] if b.get("type") == "tool_result"]
    assert "toolset_name" not in results[0] and "3. ซื้อสด" in json.dumps(results[0], ensure_ascii=False)
    saved = json.loads((tmp_path / "probe.json").read_text(encoding="utf-8"))
    assert saved[f"c:\\book|{RECIPE.version}|HP|menu"]["path"] == ["1", "ซื้อสด"]
    # 同一本账套下次：直接用记住的路径，不再调 AI
    again = Scripted([])
    engine2, sim2 = engine_for(tmp_path, renamed_menu(), transport=again, store=store)
    assert engine2.run(hp("S-HP-2"))["outcome"] == "saved"
    assert again.calls == [] and sim2.menu_sent == [50003]


def test_ai_reaching_a_submenu_with_several_commands_does_not_guess(tmp_path):
    tree = [item("1.ซื้อ", items=[item("7. สด", items=[item(f"1. {CASH_TEXT}", 50001), item(f"2. {TMB_TEXT}", 50002)])])]
    site = Scripted([[{"name": "express_menu", "input": {"path": ["1", "7"]}}]] * 2)
    engine, sim = engine_for(tmp_path, tree, transport=site)
    rec = engine.run(hp())
    assert rec["reason"] == "menu_choice" and rec["menu_options"] == [CASH_TEXT, TMB_TEXT]
    assert not (tmp_path / "probe.json").exists()  # 窗口没开成：AI 走的路径不记
    rec = engine.run(hp("S-HP-2", pay_route="transfer"))
    assert rec["outcome"] == "saved" and sim.menu_sent == [50002]
    calls = len(site.calls)
    rec = engine.run(hp("S-HP-3", pay_route="cash"))  # 开成过：记住到子菜单的路径，之后不再调 AI
    assert rec["outcome"] == "saved" and sim.menu_sent == [50002, 50001] and len(site.calls) == calls


def test_ai_that_cannot_open_the_form_hands_over_with_a_screenshot(tmp_path):
    site = Scripted([[{"name": "finish", "input": {"status": "need_human", "note": "no such menu"}}]] * 2)
    shots = Shots(tmp_path)
    engine, sim = engine_for(tmp_path, renamed_menu(), transport=site, evidence=shots)
    rec = engine.run(hp())
    assert rec["outcome"] == "need_human" and rec["reason"] == "unknown_screen"
    assert rec["evidence"].endswith("S-HP-1-need-human.png") and rec["detail"].startswith(rec["evidence"])
    assert "S-HP-1-need-human" in shots.saved
    assert not (tmp_path / "probe.json").exists()


# ── 批次 / 宿主 ────────────────────────────────────────────────────────────
class Session:
    def __init__(self, sim, fail=None):
        self.sim, self.fail, self.closed = sim, fail, 0

    def ensure_open(self):
        if self.fail:
            raise self.fail
        self.sim.alive = True

    def close(self):
        self.sim.alive = False
        self.closed += 1


def runner_for(tmp_path, drafts, menus, *, transport=None, probe_error=None, session_error=None):
    engine, sim = engine_for(tmp_path, menus, transport=transport)
    book = engine.book
    state = BatchState.new("x", "TEST", str(tmp_path / "book"), drafts)

    def probe(doc_type):
        if probe_error:
            raise probe_error
        return probe_form(engine, doc_type)

    session = Session(sim, session_error)
    r = Runner(state, BatchStore(tmp_path / "batches"), engine, session, book, probe=probe, lock=no_lock,
               sleep=lambda _s: None)
    return r, sim, session


def test_probe_with_two_commands_holds_only_this_document_and_the_next_one_uses_its_route(tmp_path):
    site = Scripted([])
    r, sim, _ = runner_for(tmp_path, [hp("A"), hp("B", pay_route="cash")], t9_menu(), transport=site)
    a = r.run_one("A")
    assert (a.status, a.reason, a.menu_options) == (K.WAITING, "menu_choice", [CASH_TEXT, TMB_TEXT])
    assert r.state.status != K.BATCH_STOPPED and site.calls == []
    b = r.run_one("B")
    assert b.status == K.SAVED and sim.menu_sent == [50001]  # 探路按现金那项开，录单沿用开着的同一个窗口


def test_probe_raising_anything_stops_the_batch_with_a_clear_state(tmp_path):
    r, sim, _ = runner_for(tmp_path, [hp("A"), hp("B")], t9_menu(), probe_error=RuntimeError("boom"))
    d = r.run_one("A")
    assert (d.status, d.reason) == (K.WAITING, "unknown_screen") and "RuntimeError: boom" in d.detail
    assert "A-need-human.png" in d.detail
    saved = BatchStore(tmp_path / "batches").load(r.state.id)
    assert saved.status == K.BATCH_STOPPED and saved.reason == "unknown_screen" and saved.current == ""


def test_opening_express_raising_anything_is_settled_the_same_way(tmp_path):
    r, *_ = runner_for(tmp_path, [hp("A")], t9_menu(), session_error=OSError("pipe"))
    d = r.run_one("A")
    assert (d.status, d.reason) == (K.WAITING, "unknown_screen")
    assert r.state.status == K.BATCH_STOPPED


def host_factory(tmp_path, menus, sims, *, transport=None, crash=None):
    def factory(state, store, settings, root, notify, member=None):
        engine, sim = engine_for(tmp_path, menus, transport=transport)
        sims.append(sim)
        session = Session(sim)
        runner = Runner(state, store, engine, session, engine.book, probe=lambda t: probe_form(engine, t),
                        lock=no_lock, sleep=lambda _s: None, on_change=notify)
        if crash:
            runner.run_one = lambda _doc_id: (_ for _ in ()).throw(crash)
        return runner, {"desk": sim}
    return factory


def job(index=1, total=1, **operator):
    return {"doctype": "HP", "docdate_be": "691021", "ref_no": "RC-1", "supplier": {"code": "P002"},
            "items": [{"code": "500310", "qty": "10", "unit_price": "25.00"}], "payload_version": 16,
            "entry_mode": "operator", "operator": {"batch": {"id": "b1", "index": index, "total": total}, **operator}}


SETTINGS = express_settings("c:\\book")


def test_host_returns_menu_choice_with_the_options_and_the_batch_is_settled(tmp_path):
    sims, site = [], Scripted([])
    host = Host(tmp_path / "op", factory=host_factory(tmp_path, t9_menu(), sims, transport=site))
    out = host.handle("t1", job(), SETTINGS)
    assert out["status"] == "human" and out["reason"] == "menu_choice"
    assert out["meta"]["menu_options"] == [CASH_TEXT, TMB_TEXT] and out["meta"]["doc_type"] == "HP"
    assert site.calls == [] and not sims[0].alive
    assert host.snapshots()[0]["status"] != K.BATCH_RUNNING
    # 站点问过、记住后带 form_choice 再交（新的写账件号）：录成
    again = host.handle("t2", job(form_choice=TMB_TEXT), SETTINGS)
    assert again["status"] == "saved" and sims[-1].menu_sent[-1] == 50002


def test_host_turns_any_exception_into_a_human_unknown_screen_and_finishes_the_runner(tmp_path):
    sims = []
    host = Host(tmp_path / "op", factory=host_factory(tmp_path, t9_menu(), sims, crash=KeyError("x")))
    out = host.handle("t1", job(index=1, total=3), SETTINGS)
    assert out["status"] == "human" and out["reason"] == "unknown_screen" and "KeyError" in out["detail"]
    saved = BatchStore(tmp_path / "op" / "batches").load("b1")
    assert saved.status == K.BATCH_STOPPED and saved.reason == "unknown_screen"
    assert saved.docs[0].status == K.WAITING


def test_host_probe_whose_ai_gives_up_is_unknown_screen_with_a_screenshot(tmp_path):
    sims = []
    site = Scripted([[{"name": "finish", "input": {"status": "need_human", "note": "?"}}]] * 2)
    host = Host(tmp_path / "op", factory=host_factory(tmp_path, renamed_menu(), sims, transport=site))
    out = host.handle("t1", job(), SETTINGS)
    assert out["status"] == "human" and out["reason"] == "unknown_screen"
    assert "need-human.png" in out["detail"] and not sims[0].alive
    assert BatchStore(tmp_path / "op" / "batches").load("b1").status == K.BATCH_STOPPED


def test_the_usual_menu_still_opens_with_the_recipe_path(tmp_path):
    sim = FakeExpress(RECIPE.data)  # 没给菜单树：按配方编号按键（1→2 是命令的公司）
    engine = Engine(sim, RECIPE, FakeBook(sim), sleep=lambda _s: None)
    assert engine.run(hp())["outcome"] == "saved"
    assert FakeSite and tiny_png  # 同一套假件
