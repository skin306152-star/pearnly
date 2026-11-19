# -*- coding: utf-8 -*-
"""代录引擎：在假 Express 上回放试点实测的录法（不花钱、不连 Express）。"""
from __future__ import annotations

from _fake_express import FakeBook, FakeExpress, FakeSite, samples

from companion.express_operator import recipe as R
from companion.express_operator.draft import Draft, Line
from companion.express_operator.engine import Engine, same
from companion.express_operator.helper import ComputerUseHelper

RECIPE = R.load()


def run_docs(drafts, *, site_kwargs=None, with_ai=True, **fake):
    sim = FakeExpress(RECIPE.data, **fake)
    site = FakeSite(sim, **(site_kwargs or {}))
    helper = ComputerUseHelper(site, sleep=lambda _s: None) if with_ai else None
    shots = []
    engine = Engine(sim, RECIPE, FakeBook(sim), helper=helper, evidence=lambda d, p: shots.append(d),
                    sleep=lambda _s: None)
    recs = []
    for d in drafts:
        site.draft = d
        recs.append(engine.run(d))
    return recs, sim, site, shots


def test_four_document_types_enter_with_keyboard_and_win32_only():
    recs, sim, site, shots = run_docs(samples())
    assert [r["outcome"] for r in recs] == ["saved"] * 4
    assert [r["docnum"] for r in recs] == ["RR0000001", "HP0000001", "IV0000001", "HS0000001"]
    assert all(r["readback"]["ok"] for r in recs)
    assert site.calls == []  # 流程定型后不调 AI
    assert all(r["checkpoints"] == 0 and not r["fallbacks"] for r in recs)
    assert shots == [d.doc_id for d in samples()]  # 每张存好后留一张存档截图
    assert sim.esc_pressed == 0 and sim.trap_saves == 0


def test_price_type_from_party_master_is_set_per_draft():
    d = samples()[0]
    recs, sim, _, _ = run_docs([d], inject={"incl_party:P001": True})
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"]


def test_duplicate_ref_and_no_history_are_reminders_only():
    a = samples()[0]
    b = Draft("S-RR-2", "RR", "2026-10-20", "P001", [Line("501780", "1", "10.00")], ref_no="INV-7781")
    recs, _, site, _ = run_docs([a, b], inject={"warn4108:P001": True})
    assert [r["outcome"] for r in recs] == ["saved", "saved"]
    assert {"reason": "duplicate_ref"} in recs[1]["reports"]  # 照录，汇报里列出
    assert site.calls == []


def test_typo_caught_by_cell_readback_goes_to_ai():
    d = samples()[1]
    recs, _, site, _ = run_docs([d], inject={"typo_qty:P002": "50"})
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"]
    assert any(r["reason"] == "ai_resolved" for r in recs[0]["reports"])
    assert site.calls[0].startswith("turn:claude-haiku")


def test_without_ai_a_mismatch_needs_a_human_and_the_half_document_is_voided():
    d = samples()[1]
    recs, sim, _, _ = run_docs([d], with_ai=False, inject={"typo_price:P002": "9"})
    rec = recs[0]
    assert rec["outcome"] == "need_human" and rec["reason"] == "unknown_screen"
    assert sim.esc_pressed == 0
    assert rec["voided"] or rec["placeholders"]  # 放弃即作废；作废不掉的空单头报给人


def test_party_missing_from_master_is_a_human_decision_not_a_guess():
    d = Draft("X-IV", "IV", "2026-10-22", "NOPE", [Line("501780", "1", "10.00")])
    recs, sim, site, _ = run_docs([d])
    assert recs[0]["outcome"] == "blocked" and recs[0]["reason"] == "party_missing"
    assert site.calls == [] and not sim.dialogs and not sim.editing  # 选择框按「ยกเลิก」关，不选第一项


def test_item_missing_from_master():
    d = Draft("X-RR", "RR", "2026-10-22", "P001", [Line("NOPE", "1", "10.00")], ref_no="Z1")
    recs, sim, _, _ = run_docs([d], items=("501780",))
    assert recs[0]["outcome"] == "blocked" and recs[0]["reason"] == "item_missing"


def test_unknown_dialog_escalates_from_haiku_to_opus():
    d = samples()[0]
    recs, _, site, _ = run_docs([d], inject={"party:P001": {"text": "ข้อความใหม่ 9999"}},
                                site_kwargs={"haiku_gives_up": True})
    assert recs[0]["outcome"] == "saved"
    assert any(c.startswith("turn:claude-opus") for c in site.calls)


def test_network_error_is_a_crash_and_nothing_is_clicked():
    d = samples()[2]
    recs, sim, _, _ = run_docs([d], inject={"crash_on_save:5001": True})
    assert recs[0]["outcome"] == "crash" and recs[0]["reason"] == "network_wait"
    assert sim.dialogs and sim.dialogs[-1]["name"] == "smb"  # -70 框原样留着，不点


def test_negative_stock_is_blocked_and_abandoned():
    d = samples()[0]
    recs, sim, _, _ = run_docs([d], inject={"block_item:501780": True})
    rec = recs[0]
    assert rec["outcome"] == "blocked" and rec["reason"] == "negative_stock"
    assert not sim.editing and sim.esc_pressed == 0


def test_date_outside_period_is_blocked():
    d = samples()[0]
    recs, _, _, _ = run_docs([d], inject={"period_outside": True})
    assert recs[0]["outcome"] == "blocked" and recs[0]["reason"] == "period_outside"


def test_readback_catches_what_screen_checks_missed():
    d = samples()[1]
    recs, _, _, _ = run_docs([d], readable="none", inject={"typo_qty:P002": "50"}, site_kwargs={"blind": True})
    rec = recs[0]
    assert rec["outcome"] == "need_human" and rec["reason"] == "readback_mismatch"
    assert not rec["readback"]["ok"]


def test_unsupported_document_type_is_not_attempted():
    d = Draft("X-RE", "RE", "2026-10-22", "5001", [Line("501780", "1", "10.00")])
    recs, sim, site, _ = run_docs([d])
    assert recs[0]["outcome"] == "need_human" and recs[0]["reason"] == "unsupported"
    assert sim.form is None and site.calls == []


def test_budget_from_the_site_stops():
    import pytest

    from companion.express_operator.helper import BudgetStop

    d = samples()[1]
    with pytest.raises(BudgetStop):
        run_docs([d], inject={"typo_qty:P002": "50"}, site_kwargs={"budget_after": 0})


def test_same_treats_numbers_and_nbsp_display():
    assert same("1000", "1,000.00")
    assert same("นาย\xa0ทดสอบ", "นาย•ทดสอบ")
    assert not same("P001", "P002")


def test_abandoning_never_voids_the_previous_saved_document():
    """10-26 实机：取消后单号格显示上一张已存的单，旧逻辑把它当成这一张作废了。"""
    good = samples()[1]
    bad = Draft("X-HP", "HP", "2026-10-21", "P002", [Line("501871", "1", "1.00")], ref_no="Z9")
    recs, sim, _, _ = run_docs([good, bad], with_ai=False, inject={"typo_qty:P002": "50"})
    first, second = recs
    assert first["outcome"] == "need_human"  # 第一张录错了，被它自己作废（这一张开录后出现的单号）
    sim2 = FakeExpress(RECIPE.data)
    engine = Engine(sim2, RECIPE, FakeBook(sim2), sleep=lambda _s: None)
    assert engine.run(good)["outcome"] == "saved"
    rec = engine.run(Draft("X-HP2", "HP", "2026-10-21", "NOPE", [Line("501871", "1", "1.00")], ref_no="Z8"))
    assert rec["outcome"] == "blocked" and rec["voided"] == []
    assert all(r["DOCSTAT"] != "C" for r in sim2.book["APTRN"])  # 上一张好单没被作废


def test_the_service_vat_basis_prompt_is_answered_on_save_and_while_abandoning():
    """10-29 实机（rec-laptop）：有服务品的赊购单存档途中 Express 问服务品进项税口径。录单时按配方答 เกณฑ์สิทธิ（同直写的
    SRV_VATTYP '1'）；放弃一张时「取消」就是存档，同样要答完、存好再作废，不能把半张单留在提问上。"""
    recs, sim, _, _ = run_docs([samples()[0]], with_ai=False, inject={"basis_on_save": True})
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"] and not sim.dialogs
    recs, sim, _, _ = run_docs([samples()[0]], with_ai=False, inject={"basis_on_save": True, "block_item:500310": True})
    rec = recs[0]  # 第二行被拦：第一行已完整，放弃时「取消」就把它存了
    assert rec["outcome"] == "blocked" and len(rec["voided"]) == 1 and not sim.dialogs
    assert any(r["DOCSTAT"] == "C" for r in sim.book["APTRN"] if r["DOCNUM"] == rec["voided"][0])


def test_the_service_output_vat_basis_prompt_on_a_credit_sale_is_answered_and_the_sale_is_saved_whole():
    """11-13 实机（测试机2 70EXP\\test，候选 75e785770）：有服务品的赊销单存档途中 Express 问服务品销项税口径；配方原来没有
    这一条，引擎认不出转人工，批次收尾退出 Express，留下半截单（表头金额 0、明细已写、分录与销项税没写）。按配方答 เกณฑ์สิทธิ
    （同直写默认的 SRV_VATTYP '1'），单完整存好、没有框留着。"""
    sale = samples()[2]
    assert sale.doc_type == "IV"
    recs, sim, _, _ = run_docs([sale], with_ai=False, inject={"sales_basis_on_save": True})
    rec = recs[0]
    assert rec["outcome"] == "saved" and rec["readback"]["ok"] and not sim.dialogs
    head = next(r for r in sim.book["ARTRN"] if r["DOCNUM"] == rec["docnum"])
    assert head["NETAMT"] == float(sale.totals()["total"]) and head["SRV_VATTYP"] == "1"


def test_a_sale_whose_payload_says_cash_basis_answers_the_output_vat_prompt_with_cash_basis():
    """载荷表头带 srv_vattyp = 2（收付实现，客户档 srv / 引导式问过一次）：销项税口径框按 เกณฑ์เงินสด 答，同直写写 '2'；
    带 1、不带或带别的值都按配方默认 เกณฑ์สิทธิ（不带键时行为不变）。"""
    from dataclasses import replace

    for choice, want in (("2", "2"), ("1", "1"), ("", "1")):
        sale = replace(samples()[2], srv_vattyp=choice)
        recs, sim, _, _ = run_docs([sale], with_ai=False, inject={"sales_basis_on_save": True})
        rec = recs[0]
        assert rec["outcome"] == "saved" and not sim.dialogs, choice
        head = next(r for r in sim.book["ARTRN"] if r["DOCNUM"] == rec["docnum"])
        assert head["SRV_VATTYP"] == want, choice


def test_a_purchase_answers_the_input_vat_prompt_with_the_basis_the_person_chose():
    """11-14（引导式对拍查出）：赊购单的服务品进项税框也按载荷 srv_vattyp 答——站点开始前问过一次、用户选了收付实现（2）就按
    เกณฑ์เงินสด，同直写；带 1、不带按 เกณฑ์สิทธิ（原来一律 เกณฑ์สิทธิ，用户选的收付实现被代录存成权责）。"""
    from dataclasses import replace

    for choice, want in (("2", "2"), ("1", "1"), ("", "1")):
        purchase = replace(samples()[0], srv_vattyp=choice)
        recs, sim, _, _ = run_docs([purchase], with_ai=False, inject={"basis_on_save": True})
        rec = recs[0]
        assert rec["outcome"] == "saved" and not sim.dialogs, choice
        head = next(r for r in sim.book["APTRN"] if r["DOCNUM"] == rec["docnum"])
        assert head["SRV_VATTYP"] == want, choice


def test_the_draft_takes_only_a_one_or_two_service_vat_basis_from_the_payload():
    from companion.express_operator.draft import from_payload

    base = {"doctype": "IV", "docdate_be": "691113", "customer": {"code": "ก026"},
            "items": [{"code": "PN00047", "qty": "1", "unit_price": "10"}]}
    assert from_payload("x", {**base, "srv_vattyp": "2"}).srv_vattyp == "2"
    assert from_payload("x", {**base, "srv_vattyp": 1}).srv_vattyp == "1"
    assert from_payload("x", {**base, "srv_vattyp": "3"}).srv_vattyp == ""
    assert from_payload("x", base).srv_vattyp == ""


def test_a_document_without_vat_is_entered_with_the_no_vat_price_type():
    """10-29 实机（rec-laptop 覆盖样本）：不收增值税的进货单（税额 0、明细不是免税品）原来按「2 - แยก VAT」录，Express 照 7% 算出税、
    合计对不上。税额为 0 的单表头价格类型选「0 - ไม่มี VAT」，同直写的 FLGVAT '0'。"""
    d = Draft("NV-HP", "HP", "2026-10-21", "P002", [Line("501871", "2", "900.00")], ref_no="NV1",
              expected={"net": "1800.00", "vat": "0.00", "total": "1800.00"})
    assert d.vat_mode == "0"
    recs, sim, _, _ = run_docs([d], with_ai=False)
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"]
    head = next(r for r in sim.book["APTRN"] if r["DOCNUM"] == recs[0]["docnum"])
    assert head["VATAMT"] == 0.0 and head["NETAMT"] == 1800.0


def test_an_exempt_document_keeps_its_vat_price_type():
    """10-29 语料（158 本）：整单免税的进货单原生是价格类型 1 / 2、明细 VATCOD '0'、零税额 AMTRAT0，不是「0 - ไม่มี VAT」；
    「无增值税」只给不收增值税、明细也没有免税行的单。"""
    exempt = Draft("EX-RR", "RR", "2026-10-21", "P001", [Line("501780", "2", "50.00", exempt=True)],
                   expected={"net": "100.00", "vat": "0.00", "total": "100.00"})
    plain = Draft("NV-RR", "RR", "2026-10-21", "P001", [Line("501780", "2", "50.00")],
                  expected={"net": "100.00", "vat": "0.00", "total": "100.00"})
    assert exempt.vat_mode == "2" and plain.vat_mode == "0"
    from companion.express_operator.draft import from_payload
    d = from_payload("X", {"doctype": "RR", "docdate_be": "691021", "supplier": {"code": "P001"},
                           "items": [{"code": "501780", "qty": "1", "unit_price": "10", "vat_exempt": True}]})
    assert d.lines[0].exempt is True


def _engine(sim, presence=None):
    return Engine(sim, RECIPE, FakeBook(sim), presence=presence, sleep=lambda _s: None)


def test_a_slow_form_open_is_waited_for_not_judged_at_once():
    # 11-01 录制机：另开着一个 Express 时第一次开表单比平常慢，只读一次就判成画面不对、整张转人工
    d = samples()[1]
    recs, sim, site, _ = run_docs([d], with_ai=False, inject={"slow_form": 80})
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"]
    assert recs[0]["checkpoints"] == 0 and not recs[0]["voided"]


def test_a_slow_save_is_waited_for_and_the_saved_document_is_not_voided():
    # 11-01 录制机：存档慢，原来读 8 次没读到正式单号就判画面不对，把存对了的单作废（HP0000118）
    d = samples()[1]
    recs, sim, _, _ = run_docs([d], with_ai=False, inject={"slow_save": 100})
    assert recs[0]["outcome"] == "saved" and recs[0]["docnum"] == "HP0000001"
    assert not recs[0]["voided"] and not any(e.startswith("void") for e in sim.log)


def test_a_person_touching_express_while_it_saves_restarts_the_wait_instead_of_failing():
    # 用户 10-30：被打断时先停、等人停手、再从断点接着做；人碰过 Express 的那一下不能当成画面不对作废
    d = samples()[1]
    sim = FakeExpress(RECIPE.data, inject={"slow_save": 300})
    paused = []

    def presence():
        if 200 <= sim.save_delay <= 205 and not paused:  # 存到一半，人在 Express 里点了一下：停下等人停手
            paused.append(sim.save_delay)
            return True
        return False

    rec = _engine(sim, presence).run(d)
    assert paused and rec["outcome"] == "saved" and rec["docnum"] == "HP0000001" and not rec["voided"]
    # 对照：同样慢、人没碰，画面宽限用完判不了——11-12 起保存键按下之后判不了先读账套：这张其实存好了，回单号、不作废
    #（以前交给人，站点再交来就从头重录，录出重复单）
    sim2 = FakeExpress(RECIPE.data, inject={"slow_save": 300})
    rec2 = _engine(sim2, lambda: False).run(samples()[1])
    assert rec2["outcome"] == "saved" and rec2["docnum"] == "HP0000001" and not rec2["voided"]


def test_a_person_touching_express_while_the_form_opens_is_waited_out():
    d = samples()[1]
    sim = FakeExpress(RECIPE.data, inject={"slow_form": 400})
    calls = []

    def presence():
        calls.append(sim.form_delay)
        if sim.form_delay and len(calls) > 5:  # 人动过、停手时表单已开好
            sim.form_delay = 0
            return True
        return False

    rec = _engine(sim, presence).run(d)
    assert rec["outcome"] == "saved" and rec["checkpoints"] == 0


def test_the_check_rereads_after_a_person_touched_express_before_judging():
    # 读画面判对错之前也先看有没有人动过：动过就等人停手、重读，读对了就算对，不截图、不转人工
    sim = FakeExpress(RECIPE.data)
    engine = _engine(sim, lambda: True)
    engine.begin(samples()[1])
    assert engine.checkpoint("after_save", "已存", {"saved": True}, reverify=lambda: "HP0000001") is True
    assert engine.rec["checkpoints"] == 0 and engine.rec["events"][-1]["after_pause"] is True


def test_abandoning_waits_for_a_slow_cancel_save_and_voids_the_half_document():
    # 11-01 录制机：第二行商品认不出要放弃，「取消」把录好的第一行存成了单；Express 慢、只读一次没读到就当没存，半截单留成有效单
    d = Draft("X-HP", "HP", "2026-10-22", "P002", [Line("501780", "1", "10.00"), Line("NOPE", "1", "10.00")], ref_no="Z9")
    sim = FakeExpress(RECIPE.data, inject={"slow_trap": 12})
    rec = _engine(sim).run(d)
    assert rec["outcome"] == "blocked" and rec["reason"] == "item_missing"
    assert rec["voided"] == ["HP0000001"] and any(e == "void HP0000001" for e in sim.log)
    assert not rec["placeholders"]


def test_a_half_document_left_saved_is_reported_not_ignored():
    # 作废没成（读不到、闸没过）时，金额对不上又没作废的单也交给人，不只报金额 0 的空单头
    from companion.express_operator import book as B

    d = Draft("X-HP", "HP", "2026-10-22", "P002", [Line("501780", "2", "10.00")], ref_no="Z9")
    head = {"DOCNUM": "HP0000123", "SUPCOD": "P002", "DOCDAT": "2026-10-22", "REFNUM": "Z9", "NETAMT": 7383.0, "DOCSTAT": "N"}
    voided = dict(head, DOCNUM="HP0000124", NETAMT=0.0, DOCSTAT="C")
    found = B.find_saved(d, {"APTRN": [head, voided], "STCRD": [], "ISVAT": []})
    assert found["docnum"] == "" and "HP0000123" in found["placeholders"]


def test_a_save_whose_number_cannot_be_read_is_confirmed_from_the_book_not_voided():
    # CC 里没接看图：「保存」已灰、单号格读不出字时，按账套里这一张（对方 + 日期 + 对方单号 + 合计）认，不作废存对了的单
    d = samples()[1]
    sim = FakeExpress(RECIPE.data, inject={"blank_docnum": True})
    rec = _engine(sim).run(d)
    assert rec["outcome"] == "saved" and rec["docnum"] == "HP0000001" and not rec["voided"]
    assert rec["readback"]["ok"]


def test_a_late_unit_dialog_is_waited_for_so_the_price_does_not_land_in_it():
    # Express 慢时单位框晚弹：不等就把单价打进单位框、整行错位（11-01 录制机怀疑的「认不出商品」）
    d = samples()[1]
    recs, sim, _, _ = run_docs([d], with_ai=False, inject={"late_unit": 6})
    assert recs[0]["outcome"] == "saved" and recs[0]["readback"]["ok"] and not recs[0]["voided"]


def test_a_document_handed_to_a_person_keeps_a_screenshot_of_that_moment():
    """2026-11-03：转人工的单在作废前截下当时的画面（原来只截存成的单）。1.12.15 起本机路径只在 rec["evidence"]（回执
    meta.evidence），说明里不再带路径（没接 AI 时也一样）。"""
    d = samples()[1]
    sim = FakeExpress(RECIPE.data, inject={"typo_price:P002": "9"})
    saved = []
    engine = Engine(sim, RECIPE, FakeBook(sim), helper=None, sleep=lambda _s: None,
                    evidence=lambda doc_id, png: saved.append(doc_id) or f"C:/evidence/{doc_id}.png")
    rec = engine.run(d)
    assert rec["outcome"] == "need_human"
    assert saved == [f"{d.doc_id}-need-human"]
    assert rec["evidence"] == f"C:/evidence/{d.doc_id}-need-human.png"
    assert "C:/evidence" not in rec["detail"] and rec["detail"].startswith("ai not connected · line1.price: ")


def test_a_site_without_the_ai_method_hands_the_document_to_a_person_instead_of_crashing():
    """新版 CC 桌面版先于服务端上线、或站点出错：AI 接手调不通就当判断不了，这张单转人工（原来会当崩溃）。"""
    d = samples()[1]
    sim = FakeExpress(RECIPE.data, inject={"typo_price:P002": "9"})
    engine = Engine(sim, RECIPE, FakeBook(sim), helper=ComputerUseHelper(lambda kind, request: {"error": "http_404"}),
                    evidence=lambda doc_id, png: f"C:/evidence/{doc_id}.png", sleep=lambda _s: None)
    rec = engine.run(d)
    assert rec["outcome"] == "need_human" and rec["reason"] == "unknown_screen"
    assert sim.esc_pressed == 0



def test_a_book_with_an_editable_line_number_column_still_enters_the_item_code_in_the_item_column():
    """11-19 测试机2：账套设了「允许插入明细行」（ISINFO.INSERTIT = Y，S 盘 760 本里 206 本）时，明细最左多一栏可编辑的行号；
    代录点进明细，焦点却停在行号栏，品码打进去只剩 3 个字（「000」「53-」），整张转人工。点进明细后焦点不在点的那一栏，
    就按回车往右走到那一栏再录；没有这一栏的账套照旧（不多按一下）。"""
    recs, sim, site, shots = run_docs(samples(), inject={"line_no_column": True})
    assert [r["outcome"] for r in recs] == ["saved"] * 4
    assert all(r["readback"]["ok"] for r in recs)
    assert site.calls == [] and all(not r["fallbacks"] for r in recs)
