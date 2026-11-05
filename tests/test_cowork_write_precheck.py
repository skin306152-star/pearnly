"""写账前置判定的 Cowork 一侧（`assistant.precheck_source`，W2；评估 `docs/work/2026-11-04-precheck-card.md` 第 3、4 节）。
不连库、不依赖 Frappe：判定模块按真文件加载，推送服务、单据、记法的存取换成替身。

- 一次收齐：录不录没选、商品单入账方式没选、写账规则要选的项同时出（以前 `prepare` 在第一道就退出，人要被问好几轮）；
  代录预检原来的回包（``legacy``）不变：录不录没选照旧停在那一步。
- 预选：对方档按同税号同分店、存货「新建」、费用科目取第一个建议、现 / 赊按票种、小票税额「不抵」、录不录「不录」、
  入账方式「走库存」；记住的 / 公司规则 / Express 历史是已定、可改；定时任务默认处理定下的不列。
- 记法分派：写账规则的项照原样交 `service.save_choices`（同一份账套同一项只记一次），录不录、入账方式、分组、待选账套各走现有那条路。
- 自动推因要选的项停下（C 类）记 ``choice_needed:<类>``；共用接口作答整批核过才记，回照写与留下的单。

    python3 tests/test_cowork_write_precheck.py
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_core.write_precheck import contracts as card  # noqa: E402

ns = types.SimpleNamespace
PATH = ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/precheck_source.py"
BOOK = "P:\\69EXP\\TEST"


class Stop(Exception):
    pass


def frappe_stub(calls):
    def throw(message, exc=None):
        raise Stop(message)

    module = types.ModuleType("frappe")
    module.__dict__.update(
        _=lambda text, *a, **k: text, throw=throw, ValidationError=Stop, PermissionError=PermissionError,
        DoesNotExistError=LookupError, clear_messages=lambda: None,
        db=ns(rollback=lambda: calls.append(("rollback",)), commit=lambda: calls.append(("commit",)),
              get_value=lambda *a, **k: "ABC Co"))
    return module


class Doc(ns):
    def as_dict(self):
        return dict(vars(self))


def document(**extra):
    fields = {"direction": "purchase", "seller_name": "ร้าน A", "seller_tax": "0105555000011", "seller_branch": "00000",
              "document_type": "receipt"}
    values = {"name": "h1", "push_connection": "C1", "push_account_set": BOOK, "invoice_no": "INV-1",
              "seller_name": "ร้าน A", "buyer_name": "", "last_push_status": "", "last_push_reason": "",
              "last_push_record": "", "pages": json.dumps([{"fields": fields}]), "workspace": "W1", "client": ""}
    values.update(extra)
    return Doc(**values)


def load(doc=None, *, block=None, needed=(), decided=None, goods=False, stop_book=None, direction="purchase",
         mismatch=None, config=None, accounts=None):
    """真加载判定模块；``needed``：写账规则要选的项；``block``：录不录的原因码；``goods``：商品单要选入账方式。"""
    calls = []
    doc = doc or document()
    fields = json.loads(doc.pages)[0]["fields"]
    target = {"account_set": BOOK, "account_set_title": "TEST 2026", "connection": "C1"}
    found = {"target": ns(account_set=BOOK), "fields": fields, "direction": direction, "invoice_date": None,
             "selected": {"config": dict(config or {})}, "flat": {}, "preparation": {}}

    def choices(_doc, _loaded, _found, posting_kind, **kw):
        calls.append(("choices", posting_kind))
        view = {"needed": [dict(item) for item in needed],
                "options": {"accounts": accounts or [{"code": "5100", "name": "ค่าใช้จ่าย"}]}}
        return view, {"needed": view["needed"], "applied": dict((decided or {}).get("applied") or {}),
                      "decided_by": dict((decided or {}).get("decided_by") or {})}, (None, "", None)

    def matching(*a):
        if mismatch:
            raise Stop(mismatch)

    service = ns(
        RESULT_UNKNOWN="push.result_unknown", _document_block=lambda d: block, _express_context=lambda *a: dict(found),
        _operator_target=lambda *a: dict(target), _require_matching_account=matching, _choices=choices,
        _fixed=lambda *a: None,
        _account_row=lambda *a: {"attributes": {"tax_id": "0105555000099"}}, operator_owned=lambda d: False,
        save_choices=lambda *args: calls.append(("save",) + args) or {"saved": len(json.loads(args[3]))})
    policy = ns(SKIPPED_PREFIX="document_policy_skip_", VALUES=("enter", "skip"),
                document_class=lambda fields: "payment_evidence" if block else "")
    goods_posting = ns(blocked=lambda *a, **k: goods, company_key=lambda *a: "tax:0105555000099")
    pending = ns(account_set_stop=lambda row: bool(stop_book),
                 account_set_options=lambda row: stop_book or {"account_sets": [], "current": ""},
                 account_set_pick=lambda row, key: {"key": key} if key in ("K1", "K2") else None,
                 current_book_choice=lambda row, key: {"workspace": "W1", "key": "cb", "value": key})
    storage = ns(read=lambda name: doc)
    modules = {
        "frappe": frappe_stub(calls),
        "pearnly_cowork": ns(), "pearnly_cowork.access": ns(scope=ns(require=lambda *a: None)),
        "pearnly_cowork.access.scope": ns(require=lambda *a: None),
        "pearnly_cowork.push": ns(lifecycle=ns(result_unknown=lambda status, reason: reason == "UNKNOWN")),
        "pearnly_cowork.push.lifecycle": ns(result_unknown=lambda status, reason: reason == "UNKNOWN"),
        "pearnly_cowork.push.application": ns(service=service, goods_posting=ns(
            choose=lambda name, value: calls.append(("goods", name, value)))),
        "pearnly_cowork.push.application.service": service,
        "pearnly_cowork.push.application.goods_posting": ns(choose=lambda name, value: calls.append(("goods", name, value))),
        "pearnly_cowork.push.application.provenance": ns(original_history=lambda d: None),
        "pearnly_cowork.push.express": ns(goods_posting=goods_posting),
        "pearnly_cowork.push.express.goods_posting": goods_posting,
        "pearnly_cowork.history": ns(storage=storage, document_policy=ns(
            choose=lambda name, value: calls.append(("policy", name, value))),
            push_state=ns(retarget=lambda *a: calls.append(("retarget",) + a))),
        "pearnly_cowork.history.storage": storage,
        "pearnly_cowork.history.document_policy": ns(choose=lambda name, value: calls.append(("policy", name, value))),
        "pearnly_cowork.history.push_state": ns(retarget=lambda *a: calls.append(("retarget",) + a)),
        "pearnly_cowork.connections": ns(service=ns(read_for_push=lambda *a, **k: ns(
            endpoint={"adapter": "express"}, doc=ns(name="C1")))),
        "pearnly_cowork.connections.service": ns(read_for_push=lambda *a, **k: ns(
            endpoint={"adapter": "express"}, doc=ns(name="C1"))),
        "pearnly_cowork.contracts": ns(document_policy=policy), "pearnly_cowork.contracts.document_policy": policy,
        "pearnly_cowork.companies": ns(pending=pending), "pearnly_cowork.companies.pending": pending,
        "pearnly_cowork.assistant": ns(
            operator_source=ns(regroup=lambda company, name, value: calls.append(("regroup", company, name, value)),
                               party_key=lambda fields, direction: ("party|0105555000011|0", "ร้าน A")),
            pending_api=ns(_remember=lambda found: calls.append(("current_book", found["value"])))),
        "pearnly_cowork.assistant.operator_source": None, "pearnly_cowork.assistant.pending_api": None,
    }
    modules["pearnly_cowork.assistant.operator_source"] = modules["pearnly_cowork.assistant"].operator_source
    modules["pearnly_cowork.assistant.pending_api"] = modules["pearnly_cowork.assistant"].pending_api
    patcher = mock.patch.dict(sys.modules, modules)
    patcher.start()
    spec = importlib.util.spec_from_file_location("cowork_write_precheck_under_test", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, calls, patcher


PARTY = {"kind": "party", "subject": "purchase|0105555000011|00000|ร้าน a", "label": "ร้าน A",
         "options": [{"code": "S1", "name": "ร้าน A", "branch": "00000"}, {"code": "S2", "name": "ร้าน A สาขา 2",
                                                                         "branch": "00002"}], "current": ""}
ITEM = {"kind": "item", "subject": "0105555000011|led 9w", "label": "LED 9W", "current": ""}
ACCOUNT = {"kind": "account", "subject": "purchase|0105555000011", "label": "ร้าน A", "current": "",
           "suggested": [{"code": "5100", "name": "ค่าใช้จ่าย"}]}
PAYMENT = {"kind": "payment", "subject": "purchase|0105555000011", "label": "ร้าน A",
           "options": [{"code": "cash"}, {"code": "credit"}], "current": ""}
VAT = {"kind": "vat", "subject": "purchase|0105555000011", "label": "ร้าน A",
       "options": [{"code": "claim"}, {"code": "expense"}], "current": ""}


class Collect(unittest.TestCase):
    def run_document(self, *args, **kwargs):
        module, calls, patcher = load(*args, **kwargs)
        self.addCleanup(patcher.stop)
        return module.document("h1"), calls, module

    def by_kind(self, found):
        return {item["kind"]: item for item in found["items"]}

    def test_everything_to_decide_comes_at_once(self):
        found, calls, _m = self.run_document(block="document_policy_needed_payment_evidence", goods=True,
                                             needed=(PARTY, ITEM, ACCOUNT, PAYMENT, VAT))
        self.assertEqual(found["state"], card.CHECK)
        self.assertEqual(set(self.by_kind(found)), {"document_policy", "goods_posting", "party", "item", "account",
                                                    "payment", "vat"})
        # 代录预检原来的回包照旧停在录不录那一步（U1 / U2 调用方不用改）
        self.assertEqual(found["legacy"], {"state": "blocked", "message": "document_policy_needed_payment_evidence"})
        # 入账方式预选走库存后按它再判一次，存货对照这些一并列出
        self.assertEqual([c[1] for c in calls if c[0] == "choices"], [None, "stock"])

    def test_preselects_follow_the_shared_rules(self):
        found, _calls, _m = self.run_document(block="document_policy_needed_payment_evidence", goods=True,
                                              needed=(PARTY, ITEM, ACCOUNT, PAYMENT, VAT))
        items = self.by_kind(found)
        expected = {"document_policy": ("skip", card.RULE), "goods_posting": ("stock", card.RULE),
                    "party": ("S1", card.RULE), "item": ("__new__", card.RULE), "account": ("5100", card.SUGGESTED),
                    "payment": ("cash", card.RULE), "vat": ("expense", card.RULE)}
        self.assertEqual({kind: (items[kind]["value"], items[kind]["source"]) for kind in expected}, expected)
        merged = card.merge([found])
        self.assertTrue(all(item["state"] == card.PROPOSED for item in card.items_of(merged)))
        self.assertEqual([group["group"] for group in merged["groups"]], ["book", "purchase", "stock", "service", "payment"])

    def test_remembered_values_are_decided_and_task_defaults_are_not_listed(self):
        decided = {"applied": {"account": "5100", "payment": "credit", "vat": "claim"},
                   "decided_by": {"account": "remembered", "payment": "company_rule", "vat": "task_default"}}
        found, _calls, _m = self.run_document(decided=decided)
        items = self.by_kind(found)
        self.assertEqual({kind: (items[kind]["value"], items[kind]["source"]) for kind in items},
                         {"account": ("5100", card.REMEMBERED), "payment": ("credit", card.COMPANY_RULE)})
        self.assertEqual(card.merge([found])["ready"], ["h1"])
        self.assertFalse(card.needs_card(card.merge([found])))  # 直写全部已定就不出卡片（Q1）

    def test_ready_document_keeps_the_old_answer(self):
        found, _calls, _m = self.run_document()
        self.assertEqual(found["legacy"], {"state": "ready", "target": found["target"]})
        self.assertEqual(found["items"], [])

    def test_old_choose_answer_is_unchanged(self):
        found, _calls, _m = self.run_document(needed=(ITEM,))
        self.assertEqual(found["legacy"]["state"], "choose")
        self.assertEqual(found["legacy"]["needed"][0]["options"][:2],
                         [{"code": "__new__", "name": ""}])  # 存货新建排第一；没有主档时只有它

    def test_unknown_direction_asks_the_group_first(self):
        found, _calls, _m = self.run_document(direction="", needed=(ACCOUNT,))
        self.assertEqual([item["kind"] for item in found["items"]], ["direction"])
        self.assertEqual(found["items"][0]["subject"], "party|0105555000011|0")
        self.assertEqual(card.merge([found])["counts"]["open"], 1)

    def test_facts_are_not_choices(self):
        found, _calls, _m = self.run_document(document(last_push_reason="UNKNOWN", last_push_record="PR-1"))
        self.assertEqual((found["state"], found["record"]), (card.UNKNOWN, "PR-1"))
        self.assertEqual(found["legacy"]["state"], "blocked")
        found, _calls, _m = self.run_document(mismatch="push.date_outside_period")
        self.assertEqual((found["state"], found["reason"]), (card.NOT_WRITABLE, "push.date_outside_period"))
        self.assertEqual(found["legacy"]["message"], "push.date_outside_period")
        found, _calls, _m = self.run_document(block="document_policy_skip_payment_evidence")
        self.assertEqual(found["state"], card.NOT_WRITABLE)  # 这类单据选了不录：列在「不能写」，不问

    def test_a_document_held_for_its_account_set(self):
        books = {"account_sets": [{"key": "K1", "label": "2025"}, {"key": "K2", "label": "2026"}], "current": "K2"}
        found, _calls, _m = self.run_document(stop_book=books)
        item = self.by_kind(found)["current_book"]
        self.assertEqual((item["value"], item["source"], item["subject"]), ("K2", card.REMEMBERED, "K1|K2"))


class BigLists(unittest.TestCase):
    CHART = [{"code": "5100", "name": "ค่าใช้จ่าย"}] + [{"code": f"6{n:03d}", "name": f"บัญชี {n}"} for n in range(30)]

    def test_a_big_list_gives_only_the_preselect_and_the_search_kind(self):
        module, _calls, patcher = load(needed=(ACCOUNT,), accounts=self.CHART)
        self.addCleanup(patcher.stop)
        item = {i["kind"]: i for i in module.document("h1")["items"]}["account"]
        self.assertEqual((item["value"], item["value_label"], item["search"]), ("5100", "ค่าใช้จ่าย", "accounts"))
        self.assertNotIn("options", item)

    def test_a_small_list_puts_the_recommended_option_first_with_its_note(self):
        suggested = dict(ACCOUNT, suggested=[{"code": "6002", "name": "บัญชี 2", "reason": "history"}])
        module, _calls, patcher = load(needed=(suggested,), accounts=self.CHART[:5])
        self.addCleanup(patcher.stop)
        merged = card.merge([module.document("h1")])
        options = card.items_of(merged)[0]["options"]
        self.assertEqual(options[0], {"code": "6002", "name": "บัญชี 2", "note": "history", "recommended": True})
        self.assertEqual(len(options), 5)

    def test_search_pages_the_chart_of_the_document_account_set(self):
        config = {"reported_accounts": self.CHART, "reported_products": [{"code": "P1", "name": "LED", "kind": "stock"},
                                                                        {"code": "S1", "name": "Fee", "kind": "service"}],
                  "reported_suppliers": [{"code": "S9", "name": "ร้าน A"}]}
        module, _calls, patcher = load(config=config)
        self.addCleanup(patcher.stop)
        first = module.search("CO1", "h1", "accounts", "", 0, 20)
        self.assertEqual((len(first["options"]), first["more"]), (20, True))
        last = module.search("CO1", "h1", "accounts", "", 20, 20)
        self.assertEqual((len(last["options"]), last["more"]), (11, False))
        self.assertEqual(module.search("CO1", "h1", "accounts", "บัญชี 2", 0, 20)["options"][0]["code"], "6002")
        self.assertEqual([r["code"] for r in module.search("CO1", "h1", "products", "", 0, 20)["options"]],
                         ["__new__", "P1"])  # 只列库存品，第一页最前面是新建
        self.assertEqual([r["code"] for r in module.search("CO1", "h1", "parties", "ร้าน", 0, 20)["options"]], ["S9"])

    def test_search_on_a_document_the_user_cannot_read_is_refused(self):
        module, _calls, patcher = load()
        self.addCleanup(patcher.stop)

        def denied(name):
            raise PermissionError(name)

        sys.modules["pearnly_cowork.history.storage"].read = denied
        with self.assertRaises(PermissionError):
            module.search("CO1", "h-other", "accounts", "", 0, 20)


class Remember(unittest.TestCase):
    def test_answers_go_to_the_existing_stores_once_per_item(self):
        module, calls, patcher = load()
        self.addCleanup(patcher.stop)
        rows = [{"kind": "item", "subject": "s|bulb", "value": "__new__", "label": "LED 9W", "remember": True},
                {"kind": "payment", "subject": "purchase|1", "value": "cash", "label": "A", "remember": True},
                {"kind": "document_policy", "subject": "payment_evidence", "value": "skip", "label": "",
                 "remember": True},
                {"kind": "goods_posting", "subject": "tax:1", "value": "stock", "label": "", "remember": True},
                {"kind": "direction", "subject": "party|1|0", "value": "expense", "label": "A", "remember": True},
                {"kind": "current_book", "subject": "K1|K2", "value": "K2", "label": "", "remember": True},
                {"kind": "posting_kind", "subject": "h1", "value": "stock", "label": "", "remember": False}]
        out = module.remember("CO1", {"h1": rows, "h2": rows[:4]})
        saves = [c for c in calls if c[0] == "save"]
        # 写账规则的项照原样交复核页同一条路（连接、账套、四个字段），同一份账套同一项只记一次
        self.assertEqual(len(saves), 1)
        self.assertEqual(saves[0][1:4], ("h1", "C1", BOOK))
        self.assertEqual(json.loads(saves[0][4]), [{key: row[key] for key in ("kind", "subject", "value", "label")}
                                                   for row in rows[:2]])
        self.assertEqual([c for c in calls if c[0] in ("policy", "goods")],
                         [("policy", "h1", "skip"), ("goods", "h1", "stock")])
        self.assertEqual([c for c in calls if c[0] == "regroup"], [("regroup", "CO1", "h1", "expense")])
        self.assertEqual([c for c in calls if c[0] in ("current_book", "retarget")],
                         [("current_book", "K2"), ("retarget", "h1", "C1", "K2")])
        self.assertEqual(out["saved"], 6)

    def test_an_unknown_kind_is_refused(self):
        module, _calls, patcher = load()
        self.addCleanup(patcher.stop)
        with self.assertRaises(Stop):
            module.remember("CO1", {"h1": [{"kind": "warehouse", "subject": "x", "value": "1", "remember": True}]})


class SweepAndShared(unittest.TestCase):
    def test_sweep_records_what_to_choose_when_it_stops(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/push/automation/sweep.py").read_text(encoding="utf-8")
        body = source.split("def one(row)", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('if action == rules.STOP and found.get("needed"):', body)
        self.assertIn('push_state.record_failure(row.name, "choice_needed:" + ",".join(', body)
        # 只记原因、不改档位判断：判档与是否推照旧在前
        self.assertLess(body.index("_mark(row, codes"), body.index("choice_needed"))
        self.assertLess(body.index("choice_needed"), body.index("if action != rules.PUSH"))

    def test_hooks_declare_the_product(self):
        hooks = (ROOT / "apps/pearnly_cowork/pearnly_cowork/hooks.py").read_text(encoding="utf-8")
        self.assertIn('"allowed": "pearnly_cowork.assistant.operator_source.allowed"', hooks)
        for key in ("check", "remember", "search", "resume"):
            self.assertIn(f'"{key}": "pearnly_cowork.assistant.precheck_source.{key}"', hooks)

    def test_shared_answer_checks_everything_before_remembering(self):
        calls = []
        documents = [{"doc_id": "h1", "items": [dict(PAYMENT, value="cash", source=card.RULE, direction="purchase")]},
                     {"doc_id": "h2", "items": [dict(ACCOUNT, value="", source="", search="accounts")]}]
        def search(company, doc_id, kind, txt, start, length):
            if doc_id == "h-other":
                raise PermissionError(doc_id)  # 产品核权限：读不到的单照抛
            return {"options": [{"code": f"A{n}", "name": ""} for n in range(start, start + length)] + [{"name": "x"}],
                    "more": True}

        product = ns(allowed=lambda user, company: company == "CO1", search=search, check=lambda company, ids: {"company_title": "ABC", "documents": documents},
                     remember=lambda company, answers: calls.append(("remember", answers)),
                     resume=lambda company, ids: calls.append(("resume", ids)) or [{"doc_id": i} for i in ids])
        frappe = frappe_stub(calls)
        frappe.parse_json = json.loads
        frappe.whitelist = lambda **k: (lambda fn: fn)
        frappe.get_hooks = lambda name: {"check": "p.check", "remember": "p.remember", "resume": "p.resume",
                                         "search": "p.search", "allowed": "p.allowed"}
        frappe.session = ns(user="acc@firm.example")
        frappe.PermissionError = PermissionError
        frappe.throw = lambda message, exc=None: (_ for _ in ()).throw((exc or Stop)(message))
        frappe.get_attr = lambda path: getattr(product, path.split(".")[1])
        with mock.patch.dict(sys.modules, {"frappe": frappe}):
            sys.modules.pop("pearnly_core.write_precheck.products", None)
            sys.modules.pop("pearnly_core.write_precheck.api", None)
            from pearnly_core.write_precheck import api
            first = api.check("CO1", json.dumps(["h1", "h2"]), mode="operator")
            self.assertTrue(first["needs_card"])
            self.assertEqual(first["mode"], "operator")
            with self.assertRaises(Stop):
                api.check("CO1", ["h1"], mode="later")  # 认不出的模式拒收
            with self.assertRaises(Stop):
                api.check("CO1", [])
            self.assertEqual(first["company_title"], "ABC")
            key = {item["kind"]: item["key"] for item in card.items_of(first)}
            bad = api.answer("CO1", ["h1", "h2"], [{"key": key["payment"], "value": "later"}])
            self.assertEqual(bad["errors"], [{"key": key["payment"], "code": card.ERR_NOT_OFFERED}])
            self.assertEqual(calls, [])  # 答错整批不记
            out = api.answer("CO1", ["h1", "h2"], [{"key": key["payment"], "value": "credit"}], resume=1)
            self.assertEqual([c[0] for c in calls[:2]], ["remember", "commit"])  # 记下后先提交，再重判
            self.assertEqual(calls[0][1], {"h1": [{"kind": "payment", "subject": PAYMENT["subject"], "value": "credit",
                                                   "label": "ร้าน A", "remember": True}]})
            self.assertEqual((out["writable"], out["held"]), (["h1"], ["h2"]))  # 未定的科目没答：那张留下
            self.assertEqual(calls[2], ("resume", ["h1"]))
            page = api.search("CO1", "h1", "accounts", "", start=20, page_length=5)
            self.assertEqual(([o["code"] for o in page["options"]], page["more"]), (["A20", "A21", "A22", "A23", "A24"], True))
            for bad in ({"search": "warehouses"}, {"page_length": 500}, {"start": "x"}, {"doc_id": ""}):
                with self.subTest(bad=bad), self.assertRaises(Stop):
                    api.search(**{"company": "CO1", "doc_id": "h1", "search": "accounts", **bad})
            with self.assertRaises(PermissionError):
                api.search("CO1", "h-other", "accounts")
            # 共用层先守一道：这家公司不能写账的，三个接口都不交给产品
            before = len(calls)
            for call in (lambda: api.check("CO2", ["h1"]), lambda: api.search("CO2", "h1", "accounts"),
                         lambda: api.answer("CO2", ["h1"], [])):
                with self.assertRaises(PermissionError):
                    call()
            self.assertEqual(len(calls), before)
            # CC 工具：同一套取数与作答（JSON 文本交回模型），权限同白名单接口
            frappe.session = ns(user="acc@firm.example")
            told = json.loads(api.tool_check({"company": "CO1", "doc_ids": ["h1", "h2"], "mode": "pending"}))
            self.assertEqual((told["mode"], told["needs_card"]), ("pending", True))
            with self.assertRaises(Stop):
                api.tool_check({"company": "CO1", "doc_ids": ["h1"], "mode": "later"})
            saved = json.loads(api.tool_answer({"company": "CO1", "doc_ids": ["h1", "h2"],
                                                "answers": [{"key": key["payment"], "value": "cash"}]}))
            self.assertEqual((saved["writable"], saved["held"]), (["h1"], ["h2"]))
            refused = json.loads(api.tool_answer({"company": "CO1", "doc_ids": ["h1"],
                                                  "answers": [{"key": key["payment"], "value": "later"}]}))
            self.assertEqual([e["code"] for e in refused["errors"]], [card.ERR_NOT_OFFERED])
            # 11-05 测试机：旧工具要模型自己拼 subject，付款项漏了就报「请检查 ERP 连接配置」。新路只认 key：subject 取自卡片，
            # 认不出的 key / 空值回写账前置判定自己的原因码，不说成连接问题
            unknown = json.loads(api.tool_answer({"company": "CO1", "doc_ids": ["h1"],
                                                  "answers": [{"key": "not-a-key", "value": "cash"},
                                                              {"key": key["payment"], "value": ""}]}))
            self.assertEqual([e["code"] for e in unknown["errors"]], [card.ERR_UNKNOWN_KEY, card.ERR_EMPTY_VALUE])
            self.assertNotIn("connection", json.dumps(unknown["errors"]))
            self.assertEqual([c for c in calls if c[0] == "remember"][-1][1]["h1"][0]["subject"], PAYMENT["subject"])  # 记下的那次：subject 来自卡片项
            for call in (lambda: api.tool_check({"company": "CO2", "doc_ids": ["h1"]}),
                         lambda: api.tool_answer({"company": "CO2", "doc_ids": ["h1"], "answers": []})):
                with self.assertRaises(PermissionError):
                    call()
            frappe.session = ns(user="Guest")
            with self.assertRaises(PermissionError):
                api.check("CO1", ["h1"])
            sys.modules.pop("pearnly_core.write_precheck.products", None)
            sys.modules.pop("pearnly_core.write_precheck.api", None)


if __name__ == "__main__":
    unittest.main()
