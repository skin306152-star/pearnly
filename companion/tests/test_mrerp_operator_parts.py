# -*- coding: utf-8 -*-
"""MR.ERP 代录执行层的纯逻辑（不起网页内核，Windows CI 上照样跑）：配方、草稿、会话判定、找单口径、有人在用、
批次的目标钩子、宿主按开关装配、心跳能力、打包清单。整条网页流程见 ``test_mrerp_operator_replay.py``。"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from companion import payload_contract
from companion.express_operator import contract as K
from companion.express_operator import credentials
from companion.express_operator import host as host_module
from companion.express_operator.batch import BatchState, BatchStore, Runner, no_lock
from companion.express_operator.engine import NeedHuman, Stopped
from companion.mrerp_operator import book as B
from companion.mrerp_operator import draft as D
from companion.mrerp_operator import engine as E
from companion.mrerp_operator import recipe as R
from companion.mrerp_operator import session as S
from companion.mrerp_operator.presence import WebPresence
from companion.mrerp_operator.web_desk import DESK_FLAG, DeskGone, ProcessDesk, desk_command

COMPANION = Path(__file__).resolve().parents[1]
RECIPE = R.load()


def block(**over):
    data = {"url": "https://mrerp4sme.com/", "connection": "c9",
            "account_set": {"comidyear": "12", "seldb": "1", "name": "TEST2569", "company_name": "TEST Co"},
            "doc_type": "purchase", "menu": "363", "docnum": "INV-1", "date": "2026-10-20", "party_code": "S001",
            "ref_no": "IV-1", "ref_date": "2026-10-19", "credit_days": "30", "vat_rate": "7%", "discount": "0",
            "lines": [{"code": "P001", "qty": "2", "price": "100"}, {"code": "P002", "qty": "3", "price": "12.5"}]}
    data.update(over)
    return {"payload_version": 18, "entry_mode": "operator", "operator": {"target": "mrerp", "batch": {"id": "b"}},
            "mrerp": data}


# ── 配方 ──────────────────────────────────────────────────────────────────
def test_the_recipe_loads_and_every_form_waits_for_the_site_probe():
    assert R.versions() == [R.DEFAULT_VERSION]
    assert set(RECIPE.forms) == {"purchase", "purchase_expense", "sales_credit", "sales_cash"}
    for doc_type, form in RECIPE.forms.items():
        assert not form.verified  # 待探路核对：没在真站点核对过
        assert not RECIPE.supported(doc_type) and RECIPE.supported(doc_type, allow_unverified=True)
        ids = form.required_ids()
        assert {"txtdate", "txtdocnum", "btnsave", "txtnet", "txtcode1"} <= set(ids)
    assert RECIPE.form("sales_cash").module == "arse" and RECIPE.form("sales_cash").cash
    assert "txtcashpay" in RECIPE.form("sales_cash").required_ids()
    pending = RECIPE.unverified()
    assert "login.footer" in pending and "modules.aptran.header.txtdate" in pending and "dialogs.period" in pending
    assert "save" in pending and RECIPE.save["no_connection_reuse"] is True  # 存档不许被网页内核自动重发（默认禁用长连接复用）
    # 11-02 真站点实测过的：登录页三格、选账套页与链接、被踢弹框、账套库不存在的页面文字
    assert not {"login.page", "login.user_id", "login.password_id", "login.submit_id", "login.select_page",
                "login.account_link", "login.account_set_missing", "dialogs.kicked"} & set(pending)


def test_known_dialogs_are_recognised_by_their_mrerp_text():
    kicked = RECIPE.match_dialog('"User" ดังกล่าวมีผู้ Login ซ้ำ จะทำการออกจากระบบโดยอัตโนมัติ')
    assert kicked["outcome"] == "kicked"
    assert RECIPE.match_dialog("กรุณา Login เข้าสู่ระบบ")["outcome"] == "kicked"
    assert RECIPE.match_dialog("User หรือ Password ไม่ถูกต้อง")["outcome"] == "login_failed"
    assert RECIPE.match_dialog("วันที่ไม่อยู่ในรอบบัญชีที่กำหนด กรุณาป้อนวันที่ใหม่")["reason"] == "period_outside"
    assert RECIPE.match_dialog("กรุณาป้อนเลขที่บิล")["reason"] == "recipe_mismatch"
    assert RECIPE.match_dialog("Error : Duplicate entry")["reason"] == "unknown_screen"
    assert RECIPE.match_dialog("ระบบจะปิดปรับปรุง") is None


def test_a_broken_recipe_is_refused():
    data = json.loads((R.RECIPES / f"mrerp-{R.DEFAULT_VERSION}.json").read_text(encoding="utf-8"))
    data["modules"]["aptran"]["header"][0]["kind"] = "typewriter"
    with pytest.raises(R.RecipeError):
        R.Recipe(data)


# ── 草稿 ──────────────────────────────────────────────────────────────────
def test_a_payload_becomes_a_draft_with_page_style_totals_and_no_password():
    draft = D.from_payload("CWH-1", block())
    assert (draft.doc_type, draft.menu, draft.docnum, draft.party_code, draft.ref_no) == ("purchase", "363", "INV-1", "S001", "IV-1")
    assert draft.date_th == "20/10/2569" and draft.ref_date_th == "19/10/2569"
    assert draft.totals() == {"before_vat": Decimal("237.50"), "vat": Decimal("16.63"), "net": Decimal("254.13")}
    included = D.from_payload("x", block(vat_included=True, lines=[{"code": "P", "qty": "1", "price": "107"}]))
    assert included.totals() == {"before_vat": Decimal("100.00"), "vat": Decimal("7.00"), "net": Decimal("107.00")}
    given = D.from_payload("x", block(totals={"before_vat": "1", "vat": "2", "net": "3"}))
    assert given.totals()["net"] == Decimal("3.00")
    assert D.connection_of(block()) == {"url": "https://mrerp4sme.com", "connection": "c9", "comidyear": "12",
                                        "seldb": "1", "name": "TEST2569", "company_name": "TEST Co"}
    assert D.iso_date("20/10/2569") == "2026-10-20" and D.iso_date("x") == ""
    assert "password" not in json.dumps(block()).lower()


# ── 会话 ──────────────────────────────────────────────────────────────────
def test_the_local_login_is_one_per_connection_and_named_by_site_and_connection():
    assert S.credential_target("https://mrerp4sme.com", "c9") == "Pearnly CC/MR.ERP/mrerp4sme.com/c9"
    # 与第 10 步授权同名（credentials.mrerp_target）：主机不带端口；没连接号按 default；网址不合规回空串
    assert S.credential_target("http://127.0.0.1:8080/", "") == "Pearnly CC/MR.ERP/127.0.0.1/default"
    assert S.credential_target("https://www.MRERP4SME.com/", "CWEC-1") == credentials.mrerp_target("https://www.mrerp4sme.com", "CWEC-1")
    assert S.credential_target("", "c9") == ""


def test_the_footer_names_the_company_and_the_account_set():
    text = "Username : ccbot Status : user Company : TEST Trading Co., Ltd. Database Name : TEST2569"
    assert S.footer_facts(text) == {"company": "TEST Trading Co., Ltd.", "database": "TEST2569"}
    assert S.footer_facts("") == {"company": "", "database": ""}


def test_after_a_kick_or_a_refused_login_the_session_never_logs_in_again():
    session = S.MrerpSession(D.connection_of(block()), RECIPE, desk_factory=lambda: pytest.fail("no window"),
                             read_secret=lambda _t: pytest.fail("no password"))
    session.ensure_open()  # 按需登录：开批次时不开窗口、不取密码
    session.kicked = True
    with pytest.raises(NeedHuman) as kicked:
        session.ensure_open()
    assert kicked.value.reason == "login_failed" and "kicked" in kicked.value.detail
    session.kicked, session.refused = False, True
    with pytest.raises(NeedHuman):
        session.ensure_open()
    assert S.MrerpSession({"url": ""}, RECIPE).stage() == "login"
    with pytest.raises(NeedHuman):
        S.MrerpSession({"url": ""}, RECIPE).ensure_open()


def test_closing_after_a_kick_does_not_log_out_the_person_now_using_the_account():
    opened, closed = [], []
    page = SimpleNamespace(open=lambda url, **_k: opened.append(url), desk=SimpleNamespace(close=lambda: closed.append(1)))
    session = S.MrerpSession(D.connection_of(block()), RECIPE)
    session.page, session.ready, session.kicked = page, False, True
    session.close()
    assert opened == [] and closed == [1] and session.page is None
    session.page, session.ready, session.kicked = page, True, False
    session.close()
    assert opened == ["https://mrerp4sme.com/login/logout.php"] and closed == [1, 1]


def test_the_login_page_is_recognised_only_on_the_login_paths():
    session = S.MrerpSession(D.connection_of(block()), RECIPE)
    assert session.on_login_page("https://mrerp4sme.com/login/login.php")
    assert session.on_login_page("https://mrerp4sme.com/login/logoutkick.php")
    assert not session.on_login_page("https://mrerp4sme.com/aptran/allview.php?idmenu=363")


# ── 找单 ──────────────────────────────────────────────────────────────────
def test_the_list_header_row_is_not_a_document():
    page = ('<p><span>เลขที่เอกสาร</span><span>วันที่เอกสาร</span><span>ชื่อ</span><span>จำนวนเงิน</span></p>'
            '<p><span>PN1</span><span>20/10/2569</span><span>x</span><span>214.00</span>'
            '<span><a href="allform.php?id=5&status=vie"></a></span></p>')
    rows = [r for r in B.parse_listing(page) if r["docno"] != B.HEADER]
    assert rows == [{"docno": "PN1", "date": "20/10/2569", "name": "x", "amount": "214.00", "id": "5"}]
    assert B.same_amount("1,234.50", Decimal("1234.5")) and B.same_amount("0.00", Decimal(0))
    assert not B.same_amount("", "0") and not B.same_amount(None, "1")


def test_an_unsure_save_keeps_the_first_baseline_until_it_is_settled(tmp_path):
    store = B.UnsureStore(tmp_path / "u.json")
    session = SimpleNamespace(conn={"url": "https://m", "comidyear": "12"})
    book = B.MrerpBook(session, RECIPE, unsure=store)
    book.baselines["D1"] = {B.BASELINE: 1, "7": 1}
    book.mark_unsure("D1")
    book.baselines["D1"] = {B.BASELINE: 1, "7": 1, "9": 1}  # 第二次开录前清单已含存上的那张
    book.mark_unsure("D1")
    assert book.unsure_baseline("D1") == {B.BASELINE: 1, "7": 1}
    assert "D1" in (tmp_path / "u.json").read_text(encoding="utf-8")
    book.settle("D1")
    assert book.unsure_baseline("D1") is None and not (tmp_path / "u.json").exists()
    book.baselines["D2"] = {B.BASELINE: 0}
    book.mark_unsure("D2")  # 开录前清单没取到的不记（那时根本没往下录）
    assert book.unsure_baseline("D2") is None


def test_without_a_baseline_nothing_can_have_been_saved():
    book = B.MrerpBook(SimpleNamespace(conn={}), RECIPE)
    assert book.find_saved(D.from_payload("x", block()), {}) == {"docnum": "", "placeholders": []}
    assert book.find_saved(D.from_payload("x", block()), {B.BASELINE: 0})["docnum"] == ""


def test_an_unsupported_kind_opens_no_page_before_entering():
    book = B.MrerpBook(SimpleNamespace(conn={}, goto=lambda _p: pytest.fail("no page")), RECIPE,
                       supported=lambda _t: False)
    assert book.before(D.from_payload("x", block())) == {B.BASELINE: 0}


# ── 引擎（不开页面的几条）──────────────────────────────────────────────────
def test_the_engine_answers_unsupported_and_no_baseline_without_touching_the_page():
    session = SimpleNamespace(page=None, kicked=False, conn={})
    book = B.MrerpBook(session, RECIPE)
    engine = E.Engine(session, RECIPE, book)
    draft = D.from_payload("x", block())
    assert engine.run(draft)["reason"] == "unsupported"
    engine.allow_unverified = True
    rec = engine.run(draft)
    assert (rec["outcome"], rec["reason"]) == ("crash", "network_wait")  # 开录前清单没取到：不往下录


class _Page:
    def __init__(self, dialogs):
        self._dialogs = list(dialogs)

    def take_dialogs(self):
        found, self._dialogs = self._dialogs, []
        return found


@pytest.mark.parametrize("text,error,reason", [
    ('"User" ดังกล่าวมีผู้ Login ซ้ำ', S.Kicked, None),
    ("User หรือ Password ไม่ถูกต้อง", NeedHuman, "login_failed"),
    ("วันที่ไม่อยู่ในรอบบัญชีที่กำหนด", E.Blocked, "period_outside"),
    ("กรุณาป้อนแผนก", E.Blocked, "recipe_mismatch"),
    ("ข้อความที่ไม่รู้จัก", E.Blocked, "unknown_screen"),
])
def test_page_dialogs_are_handled_by_the_recipe_rules(text, error, reason):
    session = SimpleNamespace(page=_Page([{"kind": "alert", "text": text}]), kicked=False, ready=True, conn={})
    engine = E.Engine(session, RECIPE, B.MrerpBook(session, RECIPE))
    with pytest.raises(error) as raised:
        engine._dialogs("x")
    if reason:
        assert raised.value.reason == reason
    if error is S.Kicked:
        assert session.kicked and not session.ready


def test_an_unknown_dialog_can_be_judged_harmless_by_a_text_helper():
    session = SimpleNamespace(page=_Page([{"kind": "alert", "text": "บันทึกแล้ว"}]), kicked=False, conn={})
    engine = E.Engine(session, RECIPE, B.MrerpBook(session, RECIPE),
                      helper=SimpleNamespace(judge_text=lambda text, stage: "continue"))
    engine.rec = {"reports": [], "events": []}
    engine._dialogs("x")
    assert engine.rec["reports"] == [{"reason": "ai_resolved", "text": "บันทึกแล้ว"}]


# ── 有人在用 ──────────────────────────────────────────────────────────────
def test_typing_in_the_mrerp_window_pauses_until_it_has_been_quiet():
    state = {"human": 0, "human_idle_ms": None}
    paused, resumed, slept = [], [], []

    def sleep(_s):
        slept.append(1)
        state["human_idle_ms"] = 31000 if len(slept) >= 2 else 1000

    presence = WebPresence(lambda: dict(state), stop_requested=lambda: False, on_pause=paused.append,
                           on_resume=lambda: resumed.append(1), sleep=sleep, clock=lambda: 100.0)
    assert presence() is False  # 开局：之前的不算
    assert presence() is False
    state.update(human=2, human_idle_ms=10)
    assert presence() is True and paused == ["user_active"] and resumed == [1] and len(slept) == 2
    assert presence.evidence[0]["source"] == "mrerp_window" and presence.evidence[0]["inputs"] == 2
    assert presence() is False  # 恢复后只看新的


def test_stop_while_waiting_for_the_person_stops_the_document():
    state = {"human": 0, "human_idle_ms": 0}
    presence = WebPresence(lambda: dict(state), stop_requested=lambda: True, sleep=lambda _s: None)
    presence()
    state["human"] = 1
    with pytest.raises(Stopped):
        presence()


def test_resume_now_continues_at_once():
    state = {"human": 0, "human_idle_ms": 0}
    presence = WebPresence(lambda: dict(state), stop_requested=lambda: False, sleep=lambda _s: presence.resume_now())
    presence()
    state["human"] = 3
    assert presence() is True


# ── 批次的目标钩子 ────────────────────────────────────────────────────────
class _Engine:
    def __init__(self, outcomes):
        self.outcomes, self.rec, self.seen = list(outcomes), {}, []

    def run(self, draft):
        self.seen.append(draft)
        self.rec = self.outcomes.pop(0)
        return self.rec


class _Session:
    def __init__(self):
        self.opened = 0

    def ensure_open(self):
        self.opened += 1

    def close(self):
        pass


def test_the_runner_uses_the_targets_draft_baseline_and_saved_check(tmp_path):
    store = BatchStore(tmp_path)
    state = BatchState("b1", "TEST Co", "TEST2569", "https://m", [])
    engine = _Engine([{"outcome": "crash", "reason": "network_wait"}])
    befores, checks = [], []

    def before(draft):
        befores.append(draft.doc_id)
        return {B.BASELINE: 1, "7": 1}

    def find_saved(draft, baseline):
        checks.append((draft.doc_id, baseline))
        return {"docnum": "PNINV-1", "placeholders": []}

    runner = Runner(state, store, engine, _Session(), SimpleNamespace(counts=dict), lock=no_lock, sleep=lambda _s: None,
                    reachable=lambda: True, draft_type=D.Draft, before=before, find_saved=find_saved)
    draft = D.from_payload("D1", block())
    runner.add(draft)
    doc = runner.run_one("D1")
    assert (doc.status, doc.docnum) == (K.SAVED, "PNINV-1")  # 结果不明 → 续录前查到了 → 不重录
    assert befores == ["D1"] and checks == [("D1", {B.BASELINE: 1, "7": 1})]
    assert isinstance(engine.seen[0], D.Draft) and engine.seen[0].menu == "363"
    assert BatchStore(tmp_path).load("b1").docs[0].counts_before == {B.BASELINE: 1, "7": 1}


# ── 宿主与心跳 ────────────────────────────────────────────────────────────
def test_by_default_only_express_is_assembled_and_reported(monkeypatch):
    monkeypatch.delenv(payload_contract.MRERP_OPERATOR_ENV, raising=False)
    assert payload_contract.operator_targets() == ("express",) == payload_contract.OPERATOR_TARGETS
    assert tuple(host_module.enabled_assemblers()) == ("express",)
    assert tuple(host_module.Host(Path("/nonexistent")).assemblers) == ("express",)


def test_the_switch_adds_mrerp_to_the_heartbeat_and_the_host_together(monkeypatch):
    monkeypatch.setenv(payload_contract.MRERP_OPERATOR_ENV, "1")
    assert payload_contract.operator_targets() == ("express", "mrerp")
    assert host_module.enabled_assemblers()["mrerp"] is host_module.mrerp_factory
    from companion.mrerp_operator import host as mhost

    assert mhost.enabled() and mhost.ENV_ENABLED == payload_contract.MRERP_OPERATOR_ENV


def test_the_host_builds_the_mrerp_draft_and_settings_from_the_payload(tmp_path):
    seen = {}

    def assemble(state, store, settings, root, notify, member=None):
        seen["settings"], seen["state"], seen["member"] = settings, state, member
        runner = SimpleNamespace(state=state, add=lambda d, task_id=None: seen.setdefault("draft", d),
                                 run_one=lambda _id: SimpleNamespace(status=K.SAVED, docnum="PN1", voided=[], placeholders=[],
                                                                    reports=[], seconds=1.0, reason="", evidence=""),
                                 finish=lambda: None, snapshot=lambda: {})
        return runner, {}

    from companion.mrerp_operator import host as mhost

    assemble.settings_of, assemble.draft_of = mhost.settings_of, mhost.draft_of
    host = host_module.Host(tmp_path, assemblers={"mrerp": assemble})
    job = block()
    job["operator"]["batch"] = {"id": "b9", "index": 1, "total": 1, "doc": "CWH-9"}
    member = SimpleNamespace(site="testcowork.pearnly.com")
    out = host.handle("t1", job, None, member=member)
    assert out["status"] == "saved" and out["docnum"] == "PN1"
    assert seen["member"] is member  # 宿主与 Express 同一个调用形状：成员配置交给装配
    assert isinstance(seen["draft"], D.Draft) and seen["draft"].doc_id == "CWH-9"
    assert (seen["settings"].account_company, seen["settings"].account_set, seen["settings"].account_dir) == \
        ("TEST Co", "TEST2569", "https://mrerp4sme.com")
    assert seen["settings"].mrerp["connection"] == "c9"


def test_the_mrerp_factory_takes_the_member_like_express(monkeypatch):
    from companion.mrerp_operator import host as mhost

    seen = {}
    monkeypatch.setattr(mhost, "assemble", lambda *a, **kw: seen.update(args=a, kw=kw) or ("runner", {}))
    member = object()
    assert host_module.mrerp_factory("s", "st", None, Path("/x"), None, member=member) == ("runner", {})
    assert seen["kw"] == {"member": member} and seen["args"] == ("s", "st", None, Path("/x"), None)
    assert host_module.mrerp_factory("s", "st", None, Path("/x"), None) == ("runner", {})  # 收拾残留时 member=None


# ── 子进程与打包 ──────────────────────────────────────────────────────────
def test_the_desk_process_is_the_same_program_with_its_own_flag():
    assert desk_command(frozen=True, executable="C:/CC/companion.exe") == ["C:/CC/companion.exe", DESK_FLAG]
    assert desk_command(frozen=False, executable="py")[-2:] == ["companion.main", DESK_FLAG] or \
        desk_command(frozen=False, executable="py")[-1] == DESK_FLAG
    with pytest.raises(DeskGone):
        ProcessDesk(command=["x"]).call("state")


def test_the_package_lists_the_mrerp_modules_and_recipes_without_new_dependencies():
    spec = (COMPANION / "packaging" / "companion.spec").read_text(encoding="utf-8")
    modules = sorted(p.stem for p in (COMPANION / "src" / "companion" / "mrerp_operator").glob("*.py") if p.stem != "__init__")
    for name in modules:
        assert f'"{name}"' in spec, name
    assert "mrerp_operator/recipes/*.json" in spec
    main = (COMPANION / "src" / "companion" / "main.py").read_text(encoding="utf-8")
    assert "--mrerp-desk" in main and "child_main" in main


def test_the_window_asking_for_a_picture_does_not_wait_behind_a_long_page_step():
    desk = ProcessDesk(command=["x"])
    desk.process = SimpleNamespace(poll=lambda: None, pid=1)
    desk.conn = SimpleNamespace(send_bytes=lambda _b: pytest.fail("must not interleave"))
    desk._lock.acquire()  # 批次线程正在等一个页面动作
    try:
        assert desk.call("screenshot", wait=0.05) == {"ok": False, "error": "busy"}
    finally:
        desk._lock.release()
