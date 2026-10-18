"""数字会计员第 3 期第 3–5、7–10 条（阶段 B）：公司档位、谁触发自动推、自动推的边界、升降档的证据与评估、存储与权限。

- 纯规则：档位允许表（`push/automation/rules.action`）、回读不旧、写的电脑够新、测试站点路径、提议与人推比对、领单指纹；
  证据统计与升降（`companies/automation_rules`：样本下限、向下取整、合格回读时点、核不上、降后重新满期、L2 全局前提）；
- 取数与写路径（Frappe 用内存替身）：取档位出错按 L0（`push/automation/service`）、公司档位的评估与核不上即降
  （`companies/automation`）、票上记判定（`history/push_state`）、定时任务（`folder_intake/push`）、网页上传 / LINE 的扫描
  （`push/automation/sweep`）、自动推前的判定（`push/application/assess`）、领单重核作废（`push/native/service`）、
  自动推不弹「账套被占用」（`companion/route_contracts`）。
"""
import ast
import datetime as dt
import importlib
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
for _path in (ROOT / "apps/pearnly_core", ROOT / "apps/pearnly_cowork"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))
for _name in [n for n in sys.modules if n.split(".")[0] in ("pearnly_cowork", "frappe")]:
    del sys.modules[_name]

from pearnly_cowork.companies import automation_rules as evidence  # noqa: E402
from pearnly_cowork.companion import route_contracts  # noqa: E402
from pearnly_cowork.push.automation import rules  # noqa: E402
from pearnly_cowork.push.express import automation as auto  # noqa: E402

SELLER, OWN = "0107561000013", "0994000333447"
BOOK = r"C:\PearnlyExpress\69ACME"


def _load(name, path, modules):
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def ns(**kwargs):
    return types.SimpleNamespace(**kwargs)


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Denied(Exception):
    pass


class Invalid(Exception):
    pass


# ── 第 3、4 条：档位允许表 ─────────────────────────────────────────────────────


class TierTable(unittest.TestCase):
    def test_who_pushes_what_at_each_tier(self):
        # 定时任务：A 类从 L0 起就照推（任务默认值是人定的规则，用户 10-16）；B 类在 L0 / L1 提议
        for tier in rules.TIERS:
            self.assertEqual(rules.action("A", tier, rules.TASK)[0], rules.PUSH, tier)
        # 网页上传 / LINE：L0 只提议，L1 推 A
        for trigger in (rules.UPLOAD, rules.LINE):
            self.assertEqual(rules.action("A", rules.L0, trigger), (rules.PROPOSE, "tier"))
            self.assertEqual(rules.action("A", rules.L1, trigger), (rules.PUSH, ""))
            self.assertEqual(rules.action("A", rules.L2, trigger), (rules.PUSH, ""))
        # B 类：L2 也只在全局前提成立后才推（回放 B 类单独过达标线之前是常量 False）
        self.assertFalse(rules.B_AT_L2)
        for trigger in rules.TRIGGERS:
            for tier in rules.TIERS:
                self.assertEqual(rules.action("B", tier, trigger), (rules.PROPOSE, "tier"))
        with mock.patch.object(rules, "B_AT_L2", True):
            self.assertEqual(rules.action("B", rules.L2, rules.UPLOAD), (rules.PUSH, ""))
            self.assertEqual(rules.action("B", rules.L1, rules.TASK), (rules.PROPOSE, "tier"))
        # C 类：一律照现有停下
        for trigger in rules.TRIGGERS:
            for tier in rules.TIERS:
                self.assertEqual(rules.action("C", tier, trigger)[0], rules.STOP)
        # 认不出的档位按 L0
        self.assertEqual(rules.tier_of("L9"), rules.L0)
        self.assertEqual(rules.action("A", None, rules.UPLOAD), (rules.PROPOSE, "tier"))

    def test_no_recent_readback_pauses_only_tier_driven_pushes(self):
        self.assertTrue(rules.readback_ok("2026-08", "2026-10-05"))
        self.assertFalse(rules.readback_ok("2026-07", "2026-10-05"))  # 早于本单期间两个月以上
        self.assertFalse(rules.readback_ok("", "2026-10-05"))
        self.assertFalse(rules.readback_ok("2026-08", ""))
        self.assertEqual(rules.action("A", rules.L1, rules.UPLOAD, readback=False), (rules.PROPOSE, "readback_stale"))
        # 定时任务 A 类的基线推送不靠档位，新客户没有回读也照推（新客户第一个月就推 A 类）
        self.assertEqual(rules.action("A", rules.L0, rules.TASK, readback=False), (rules.PUSH, ""))

    def test_the_scheduled_task_baseline_is_only_for_new_customers(self):
        # 审查处理 10-16：已有证据的按证据（被改 / 核不上出提议，回读旧了也出提议）；L1 / L2 同网页上传
        task = lambda **kw: rules.action("A", rules.L0, rules.TASK, **kw)  # noqa: E731
        self.assertEqual(task(baseline=rules.BASELINE_NEW), (rules.PUSH, ""))
        self.assertEqual(task(baseline=rules.BASELINE_OK), (rules.PUSH, ""))
        self.assertEqual(task(baseline=rules.BASELINE_OK, readback=False), (rules.PROPOSE, "readback_stale"))
        self.assertEqual(task(baseline="changed"), (rules.PROPOSE, "changed"))
        self.assertEqual(task(baseline="written_mismatch"), (rules.PROPOSE, "written_mismatch"))
        self.assertEqual(rules.action("A", rules.L1, rules.TASK, readback=False), (rules.PROPOSE, "readback_stale"))
        self.assertEqual(rules.action("A", rules.L1, rules.TASK, baseline="changed"), (rules.PUSH, ""))
        self.assertEqual(rules.action("B", rules.L0, rules.TASK, baseline=rules.BASELINE_NEW), (rules.PROPOSE, "tier"))

    def test_only_computers_that_return_the_write_check_take_automatic_pushes(self):
        self.assertTrue(rules.verify_capable(["1.8.0", "1.9.2"]))
        self.assertFalse(rules.verify_capable(["1.8.0", "1.7.0"]))  # 有一台旧版就可能被它领到
        self.assertFalse(rules.verify_capable([]))
        self.assertFalse(rules.verify_capable([""]))

    def test_test_sites_only_write_account_sets_under_the_lab_folder(self):
        self.assertTrue(rules.test_path_ok("c:\\pearnlyexpress\\69acme"))
        self.assertTrue(rules.test_path_ok("C:/PearnlyExpress/69ACME"))
        for key in ("s:\\70exp\\t9sincer", "\\\\accserver\\account\\69acme", "c:\\pearnlyexpress\\", "",
                    "c:\\pearnlyexpressx\\69acme"):
            self.assertFalse(rules.test_path_ok(key), key)

    def test_the_lease_fingerprint_only_covers_what_this_document_uses(self):
        fields = {"seller_name": "ผู้ขาย", "seller_tax": SELLER, "seller_branch": "00000"}
        scope = rules.subjects("purchase", fields)
        supplier = f"purchase|{SELLER}"
        memory = {"account": {supplier: "5310-01", "purchase|0105556012341": "5320-01"}, "department": {"*": "HQ"}}
        base = rules.fingerprint(memory, (), scope)
        # 别的供应商被人选了：不作废这一张
        other = {**memory, "account": {**memory["account"], "purchase|0105556012341": "5330-01"}}
        self.assertEqual(rules.fingerprint(other, (), scope), base)
        # 这家供应商的科目、整份账套的部门、这家的公司规则变了：变
        self.assertNotEqual(rules.fingerprint({**memory, "account": {supplier: "5399"}}, (), scope), base)
        self.assertNotEqual(rules.fingerprint({**memory, "department": {"*": "BR"}}, (), scope), base)
        rule = {"kind": "payment", "subject": supplier, "value": "cash", "branch": ""}
        self.assertNotEqual(rules.fingerprint(memory, [rule], scope), base)
        self.assertEqual(rules.fingerprint(memory, [{**rule, "subject": "purchase|0105556012341"}], scope), base)
        # 存货按对方开头；POS 收款科目只在 POS 票上算
        item = {**memory, "item": {f"{supplier}|น้ำดื่ม": "W01"}}
        self.assertNotEqual(rules.fingerprint(item, (), scope), base)
        self.assertEqual(rules.fingerprint({**memory, "receipt": {"qr": "1120"}}, (), scope), base)
        self.assertNotEqual(rules.fingerprint({**memory, "receipt": {"qr": "1120"}}, (),
                                              rules.subjects("purchase", fields, pos=True)),
                            rules.fingerprint(memory, (), rules.subjects("purchase", fields, pos=True)))
        # 指纹 + 档位 + 票的版本
        self.assertNotEqual(rules.automation_hash(3, rules.L1, base), rules.automation_hash(3, rules.L0, base))
        self.assertNotEqual(rules.automation_hash(3, rules.L1, base), rules.automation_hash(4, rules.L1, base))


# ── 第 7 条：提议与人推的比对 ──────────────────────────────────────────────────


PAYLOAD = {"direction": "purchase", "doctype": "RR", "ref_no": "IV69-001", "supplier": {"code": "S001", "tax_id": SELLER},
           "base_amount": "1000", "vat_amount": "70", "total_amount": "1070", "items_account": "5310-01",
           "depcod": "HQ", "items": [{"name": "น้ำ", "code": "W01", "item_mode": "stock_item"}]}


class Proposals(unittest.TestCase):
    def test_a_proposal_freezes_each_value_the_date_number_party_and_amounts(self):
        proposed = rules.proposal("B", rules.L0, rules.UPLOAD, PAYLOAD, {"date": "2026-10-20"}, at="2026-10-21T01:00:00",
                                  items={"account": "B"})
        self.assertEqual(proposed["values"], {"party": "S001", "item": "W01", "account": "5310-01", "payment": "RR",
                                              "department": "HQ", "vat": "claim"})
        self.assertEqual((proposed["date"], proposed["ref_no"], proposed["party_tax"], proposed["before_vat"],
                          proposed["vat"], proposed["total"]),
                         ("2026-10-20", "IV69-001", SELLER, "1000.00", "70.00", "1070.00"))
        self.assertEqual((proposed["class"], proposed["tier"], proposed["trigger"]), ("B", "L0", "upload"))

    def test_a_person_choosing_otherwise_is_a_change_but_a_rule_change_is_only_stale(self):
        proposed = rules.proposal("A", rules.L0, rules.UPLOAD, PAYLOAD, {}, at="2026-10-21T01:00:00")
        same = rules.compare(proposed, PAYLOAD)
        self.assertEqual(same, {"class": "A", "edited": False, "changed": [], "stale": [], "values": {}})
        other = {**PAYLOAD, "items_account": "5320-01", "doctype": "HP"}
        # 不同的项带上提议的值与推出去的值（10-18：统计人在复核时把提议的哪个值改成了什么，R1 的前提）
        self.assertEqual(rules.compare(proposed, other, chosen={"account"}),
                         {"class": "A", "edited": False, "changed": ["account"], "stale": ["payment"],
                          "values": {"account": {"from": "5310-01", "to": "5320-01"},
                                     "payment": {"from": "RR", "to": "HP"}}})
        self.assertTrue(rules.compare(proposed, PAYLOAD, edited=True)["edited"])
        self.assertIsNone(rules.compare(None, PAYLOAD))
        self.assertIsNone(rules.compare({"class": "C"}, PAYLOAD))


# ── 第 7、8 条：证据统计与升降档 ────────────────────────────────────────────────


def _doc(cls="A", *, automated=False, proposal=True, covered=True, readback="", verify="", outcome=None):
    return {"cls": cls, "automated": automated, "proposal": proposal and not automated, "covered": covered,
            "readback": readback, "verify": verify, "outcome": outcome}


class Evidence(unittest.TestCase):
    def test_only_documents_covered_by_a_qualified_readback_count(self):
        docs = [_doc(), _doc(readback="changed"), _doc(covered=False, readback="deleted"),
                _doc("B"), _doc("B", outcome={"edited": True}), _doc("C"),
                _doc(automated=True), _doc(automated=True, readback="voided"),
                _doc(automated=True, covered=False, verify="mismatch"),
                _doc(outcome={"stale": ["account"], "changed": [], "edited": False}),
                _doc(outcome={"stale": ["account"], "changed": ["payment"], "edited": False}),
                _doc(proposal=False)]
        found = evidence.tally(docs)
        # A：两张提议 + 两张自动推 + 人另选的那张；过时的不进样本；没被覆盖的不算（核不上照计）
        self.assertEqual(found, {"a": 5, "a_changed": 3, "b": 2, "b_changed": 1, "auto": 2, "auto_changed": 1,
                                 "mismatch": 1, "stale": 1})

    def test_a_readback_counts_only_when_started_after_the_pp30_deadline(self):
        deadline = dt.date(2026, 11, 23)
        self.assertFalse(evidence.qualified(dt.date(2026, 11, 23), deadline))
        self.assertTrue(evidence.qualified(dt.date(2026, 11, 24), deadline))
        self.assertFalse(evidence.qualified(None, deadline))

    def test_changes_allowed_round_down(self):
        self.assertEqual([evidence.allowed(n, "0.01") for n in (20, 99, 100, 250)], [0, 0, 1, 2])
        self.assertEqual([evidence.allowed(n, "0.005") for n in (199, 200, 401)], [0, 1, 2])

    def test_l0_to_l1_needs_twenty_rule_decided_documents_in_one_complete_period(self):
        good = {"a": 20, "a_changed": 0}
        self.assertEqual(evidence.evaluate("L0", None, {"2026-09": good}), ("L1", "readback"))
        self.assertEqual(evidence.evaluate("L0", None, {"2026-09": {"a": 19, "a_changed": 0}}), ("L0", ""))
        self.assertEqual(evidence.evaluate("L0", None, {"2026-09": {"a": 99, "a_changed": 1}}), ("L0", ""))
        self.assertEqual(evidence.evaluate("L0", None, {"2026-09": {"a": 100, "a_changed": 1}}), ("L1", "readback"))
        # 最近一期说了算：上一期好、最近一期不够
        self.assertEqual(evidence.evaluate("L0", None, {"2026-08": good, "2026-09": {"a": 5}}), ("L0", ""))
        self.assertEqual(evidence.evaluate("L0", None, {}), ("L0", ""))

    def test_l1_to_l2_needs_two_periods_and_the_global_b_premise(self):
        stats = {"a": 40, "auto": 200, "auto_changed": 1, "b": 20, "b_changed": 0}
        periods = {"2026-09": stats, "2026-10": stats}
        since = dt.date(2026, 8, 15)
        self.assertEqual(evidence.evaluate("L1", since, periods), ("L1", ""))  # 全局前提没成立
        self.assertEqual(evidence.evaluate("L1", since, periods, b_at_l2=True), ("L2", "readback"))
        self.assertEqual(evidence.evaluate("L1", since, {"2026-10": stats}, b_at_l2=True), ("L1", ""))
        thin = {**stats, "b": 19}
        self.assertEqual(evidence.evaluate("L1", since, {"2026-09": stats, "2026-10": thin}, b_at_l2=True), ("L1", ""))
        noisy = {**stats, "auto_changed": 2}  # 两期合计 400 张自动推被改 3 张 > 0.5%
        self.assertEqual(evidence.evaluate("L1", since, {"2026-09": stats, "2026-10": noisy}, b_at_l2=True),
                         ("L1", ""))

    def test_down_one_tier_when_automatic_pushes_were_changed(self):
        since = dt.date(2026, 8, 15)
        self.assertEqual(evidence.evaluate("L2", since, {"2026-09": {"auto": 100, "auto_changed": 2}}), ("L1", "changed"))
        self.assertEqual(evidence.evaluate("L1", since, {"2026-09": {"auto": 100, "auto_changed": 2}}), ("L0", "changed"))
        self.assertEqual(evidence.evaluate("L1", since, {"2026-09": {"auto": 300, "auto_changed": 3}}), ("L1", ""))
        self.assertEqual(evidence.evaluate("L1", since, {"2026-09": {"auto": 10, "auto_changed": 1}}), ("L1", ""))

    def test_after_a_change_only_periods_that_start_later_count(self):
        good = {"a": 30, "a_changed": 0}
        # 9-10 降到 L0：九月不算，十月起算
        self.assertEqual(evidence.evaluate("L0", dt.date(2026, 9, 10), {"2026-09": good}), ("L0", ""))
        self.assertEqual(evidence.evaluate("L0", dt.date(2026, 9, 10), {"2026-09": good, "2026-10": good}),
                         ("L1", "readback"))
        self.assertEqual(evidence.evaluate("L0", dt.date(2026, 10, 1), {"2026-10": good}), ("L0", ""))

    def test_the_task_baseline_follows_the_latest_complete_period(self):
        good = {"a": 30, "a_changed": 0, "mismatch": 0}
        # 从没升降过、没有合格回读的期：新客户
        self.assertEqual(evidence.baseline("L0", None, {}), "new")
        self.assertEqual(evidence.baseline("L0", None, {"2026-10": good}), "ok")
        # 最近一期 A 类被改超过 1%（不到 100 张即 0 张）、核不上：不推
        self.assertEqual(evidence.baseline("L0", None, {"2026-10": {**good, "a_changed": 1}}), "changed")
        self.assertEqual(evidence.baseline("L0", None, {"2026-10": {"a": 200, "a_changed": 2, "mismatch": 0}}), "ok")
        self.assertEqual(evidence.baseline("L0", None, {"2026-10": {**good, "mismatch": 1}}), "written_mismatch")
        self.assertEqual(evidence.baseline("L0", None, {"2026-09": {**good, "a_changed": 5}, "2026-10": good}), "ok")
        # 降过档（或 L0 上核不上重新起算）、之后还没有完整期证据：不推，原因同上次
        self.assertEqual(evidence.baseline("L0", dt.date(2026, 10, 16), {"2026-10": good}, "changed"), "changed")
        self.assertEqual(evidence.baseline("L0", dt.date(2026, 10, 16), {}, "written_mismatch"), "written_mismatch")
        self.assertEqual(evidence.baseline("L0", dt.date(2026, 10, 16), {"2026-11": good}, "changed"), "ok")
        self.assertEqual(evidence.baseline("L1", None, {}), "ok")

    def test_history_keeps_the_last_24_changes(self):
        rows = []
        for index in range(30):
            rows = evidence.keep_history(rows, evidence.history_entry("L0", "L1", "readback", index))
        self.assertEqual((len(rows), rows[0]["at"], rows[-1]["at"]), (24, "6", "29"))

    def test_thresholds_are_one_constant_table(self):
        source = (APP / "companies/automation.py").read_text(encoding="utf-8")
        self.assertIn("THRESHOLDS = automation_rules.THRESHOLDS", source)
        self.assertEqual(evidence.THRESHOLDS["up_l1"], {"periods": 1, "a_min": 20, "a_rate": "0.01"})
        self.assertEqual(evidence.THRESHOLDS["up_l2"]["periods"], 2)
        self.assertEqual(evidence.THRESHOLDS["down"], {"auto_changed_min": 2, "auto_rate": "0.01"})


# ── 推送侧取数：取档位出错按 L0 ────────────────────────────────────────────────


def _frappe(hooks=None, attrs=None, site="cowork.pearnly.com"):
    logged = []
    frappe = types.ModuleType("frappe")
    frappe.get_hooks = lambda name: list((hooks or {}).get(name, []))
    frappe.get_attr = lambda path: (attrs or {})[path]
    frappe.log_error = lambda title=None, message=None: logged.append(title)
    frappe.get_traceback = lambda: "Traceback"
    frappe.clear_messages = lambda: None
    frappe.local = ns(site=site)
    frappe.session = ns(user="a@example.com")
    frappe.db = ns(savepoint=lambda name: None, rollback=lambda save_point=None: None)
    frappe.logged = logged
    utils = types.ModuleType("frappe.utils")
    utils.get_datetime = lambda value: value if isinstance(value, dt.datetime) else dt.datetime.fromisoformat(str(value))
    utils.get_system_timezone = lambda: "Asia/Bangkok"
    frappe.utils = utils
    return frappe, utils


def _automation_service(hooks=None, attrs=None, site="cowork.pearnly.com", peers=None, writers=None, remembered=None,
                        chosen=None, overrides=None):
    frappe, utils = _frappe(hooks, attrs, site)
    pool = ns(presence=lambda connection: dict(peers or {}),
              owned=lambda found, actor, device="": ({device: found[device]} if device in found else {}) if device
              else {name: peer for name, peer in found.items() if actor in peer.get("users", ())},
              writers=lambda doc, key, peers=None: [name for name in sorted(peers or {}) if name in (writers or ())])
    frappe.get_doc = lambda doctype, name: ns(name=name)
    store = ns(remembered=lambda connection, account_set: dict(remembered or {}),
               chosen_at=lambda connection, account_set, kind, subject: (chosen or {}).get(kind),
               overrides=overrides or (lambda *a, **k: []))
    module = _load("da_tiers_automation_service", APP / "push/automation/service.py", {
        "frappe": frappe, "frappe.utils": utils, "pearnly_cowork.connections": ns(pool=pool),
        "pearnly_cowork.connections.pool": pool, "pearnly_cowork.push.choices": store})
    return module, frappe


class PushSideLookups(unittest.TestCase):
    def test_a_failing_tier_lookup_counts_as_l0(self):
        def broken(*args):
            raise RuntimeError("boom")

        service, frappe = _automation_service({"cowork_push_company_tier": ["x.tier"]}, {"x.tier": broken})
        self.assertEqual(service.tier("C1", BOOK, {}), {"company": "", "tier": "L0", "readback": "",
                                                       "baseline": "tier_unknown"})
        self.assertEqual(frappe.logged, ["Cowork push company tier failed"])
        service, _frappe_module = _automation_service()
        self.assertEqual(service.tier("C1", BOOK, {})["tier"], "L0")  # 没建主档 / 没有行
        service, _frappe_module = _automation_service({"cowork_push_company_tier": ["x.tier"]},
                                                      {"x.tier": lambda *a: {"company": "CO1", "tier": "L9"}})
        self.assertEqual(service.tier("C1", BOOK, {}), {"company": "CO1", "tier": "L0", "readback": "",
                                                       "baseline": "new"})
        service, _frappe_module = _automation_service({"cowork_push_company_tier": ["x.tier"]}, {
            "x.tier": lambda *a: {"company": "CO1", "tier": "L1", "readback": "2026-09"}})
        self.assertEqual(service.tier("C1", BOOK, {}), {"company": "CO1", "tier": "L1", "readback": "2026-09",
                                                       "baseline": "new"})

    def test_day_books_that_fail_cannot_be_chosen(self):
        def broken(*args):
            raise RuntimeError("boom")

        service, _frappe_module = _automation_service({"cowork_push_day_books": ["x.books"]}, {"x.books": broken})
        self.assertIsNone(service.day_books("C1", {}, "2026-10-20", []))
        service, _frappe_module = _automation_service({"cowork_push_day_books": ["x.books"]},
                                                      {"x.books": lambda *a: [BOOK.lower()]})
        self.assertEqual(service.day_books("C1", {}, "2026-10-20", []), [BOOK.lower()])
        self.assertIsNone(service.day_books("C1", {}, "", []))

    def test_readback_marks_that_fail_count_as_unchecked(self):
        def broken(*args):
            raise RuntimeError("boom")

        records = [{"record": "R1", "invoice_date": "2026-10-03", "completed_at": None}]
        service, frappe = _automation_service({"cowork_push_readback_marks": ["x.marks"]}, {"x.marks": broken})
        self.assertEqual(service.readback_marks("C1", BOOK, records), {})
        self.assertEqual(frappe.logged, ["Cowork push readback marks failed"])
        service, _frappe_module = _automation_service({"cowork_push_readback_marks": ["x.marks"]},
                                                      {"x.marks": lambda *a: {"R1": "confirmed"}})
        self.assertEqual(service.readback_marks("C1", BOOK, records), {"R1": "confirmed"})
        self.assertEqual(service.readback_marks("C1", BOOK, []), {})

    def test_r1_is_judged_again_from_the_frozen_memory_values_when_a_computer_leases(self):
        """领单重核（R1，10-18）：自动推判 A 时冻结的账套记忆值与证据时刻（``overrule``）再查一次这份账套的改值记录，判完之后有人把
        同一个记忆值改掉的类进指纹（`rules.automation_hash`），指纹变了就不写旧判定；没有 ``overrule`` 不查库、指纹同以前。"""
        asked = []
        found = []

        def overrides(connection, account_set, values, since=None):
            asked.append((connection, account_set, dict(values)))
            return list(found)
        service, frappe = _automation_service(overrides=overrides)
        frozen = {"account": {"value": "5310-01", "since": "2026-10-20T09:00:00"},
                  "payment": {"value": "credit", "since": "2026-10-20T09:00:00"}}
        today = dt.date(2026, 10, 25)
        self.assertEqual(service.overruled_kinds("C1", BOOK, frozen, today), [])
        self.assertEqual(asked, [("C1", BOOK, {"account": "5310-01", "payment": "credit"})])
        found[:] = [{"kind": "payment", "old": "credit", "new": "cash", "at": dt.datetime(2026, 10, 21, 8, 0)},
                    {"kind": "account", "old": "5310-01", "new": "5320-01", "at": dt.datetime(2026, 10, 19, 8, 0)}]
        self.assertEqual(service.overruled_kinds("C1", BOOK, frozen, today), ["payment"])  # 科目那次改在证据之前
        asked.clear()
        self.assertEqual(service.overruled_kinds("C1", BOOK, None), [])
        self.assertEqual(service.overruled_kinds("C1", BOOK, {"party": {"value": "S1", "since": "x"}}), [])
        self.assertEqual(asked, [])
        # 指纹：没被挡的同以前；被挡了就变
        frappe.utils.nowdate = lambda: "2026-10-25"
        frappe.utils.getdate = lambda value: dt.date.fromisoformat(str(value)[:10])
        before = service.current_hash(3, "C1", BOOK, {}, None)
        found[:] = []
        self.assertEqual(service.current_hash(3, "C1", BOOK, {}, None, frozen), before)
        found[:] = [{"kind": "payment", "old": "credit", "new": "cash", "at": dt.datetime(2026, 10, 21, 8, 0)}]
        self.assertNotEqual(service.current_hash(3, "C1", BOOK, {}, None, frozen), before)
        self.assertEqual(rules.automation_hash(3, "L0", "m"), rules.automation_hash(3, "L0", "m", ()))
        self.assertNotEqual(rules.automation_hash(3, "L0", "m"), rules.automation_hash(3, "L0", "m", ["payment"]))
        # 冻结进目标：只存值与时刻
        stored = rules.frozen("A", "L1", "upload", (), "H", {"party": ["x"]}, {"payment": {"value": "credit",
                                                                                        "since": "2026-10-20T09:00:00",
                                                                                        "extra": 1}})
        self.assertEqual(stored["overrule"], {"payment": {"value": "credit", "since": "2026-10-20T09:00:00"}})
        self.assertNotIn("overrule", rules.frozen("A", "L1", "upload", (), "H"))

    def test_the_lab_path_guard_only_applies_on_test_sites(self):
        for site, key, ok in (("testcowork.pearnly.com", "s:\\70exp\\t9sincer", False),
                              ("testcowork.pearnly.com", "c:\\pearnlyexpress\\69acme", True),
                              ("lab-1.localhost", "s:\\70exp\\t9sincer", False),
                              ("cowork.pearnly.com", "s:\\70exp\\t9sincer", True)):
            service, frappe = _automation_service(site=site)
            with mock.patch.dict(sys.modules, {"frappe": frappe}):  # `TEST_SITES` 在共用层，导入时要 frappe
                self.assertEqual(service.test_site_ok(key), ok, (site, key))

    def test_automatic_pushes_go_only_to_computers_that_return_the_write_check(self):
        peers = {"PC-NEW": {"users": {"a@example.com"}, "version": "1.8.0"},
                 "PC-OLD": {"users": {"a@example.com"}, "version": "1.7.0"},
                 "PC-READ": {"users": set(), "version": "1.8.0"}}
        service, _frappe_module = _automation_service(peers=peers, writers={"PC-NEW", "PC-OLD", "PC-READ"})
        self.assertFalse(service.verify_capable("C1", BOOK, actor="a@example.com"))  # 自己的电脑里有一台旧版
        self.assertTrue(service.verify_capable("C1", BOOK, actor="a@example.com", route_device="PC-READ"))
        self.assertFalse(service.verify_capable("C1", BOOK, actor="b@example.com"))  # 没有自己的电脑
        service, _frappe_module = _automation_service(peers=peers, writers={"PC-NEW"})
        self.assertTrue(service.verify_capable("C1", BOOK, actor="a@example.com"))

    def test_what_the_person_chose_after_the_proposal(self):
        chosen = {"account": "2026-10-21 09:00:00", "payment": "2026-10-20 07:00:00"}  # 站点时区（曼谷）
        service, _frappe_module = _automation_service(chosen=chosen)
        fields = {"seller_tax": SELLER, "seller_name": "ผู้ขาย", "seller_branch": "00000"}
        found = service.chosen_kinds("C1", BOOK, fields, "purchase", {"doctype_src": "manual"},
                                     dt.datetime(2026, 10, 21, 1, 0))
        self.assertEqual(found, {"account", "payment"})  # 科目是提议之后选的（UTC 02:00）；付款是票上人改的
        self.assertEqual(service.chosen_kinds("C1", BOOK, fields, "purchase", {}, dt.datetime(2026, 10, 21, 3, 0)),
                         set())

    def test_a_company_rule_or_task_default_changed_after_the_proposal_is_a_change_not_stale(self):
        # 审查处理 10-16：会计改公司规则（设为全公司）或任务默认来纠正提议，算被改，不算过时
        fields = {"seller_tax": SELLER, "seller_name": "ผู้ขาย", "seller_branch": "00000"}
        subject = "purchase|" + SELLER
        rules_found = {"company": "CO1", "branch": "00000", "rules": [
            {"kind": "account", "subject": subject, "value": "5320", "branch": "", "at": "2026-10-21 10:00:00"},
            {"kind": "payment", "subject": subject, "value": "cash", "branch": "", "at": "2026-10-19 10:00:00"}]}
        service, _frappe_module = _automation_service({"cowork_push_company_choices": ["x.rules"]},
                                                      {"x.rules": lambda *a: rules_found})
        since = dt.datetime(2026, 10, 21, 1, 0)  # UTC；规则 10:00 曼谷 = 03:00 UTC，在提议之后
        self.assertEqual(service.chosen_kinds("C1", BOOK, fields, "purchase", {}, since, {"source_id": BOOK}),
                         {"account"})
        self.assertEqual(service.chosen_kinds("C1", BOOK, fields, "purchase", {}, since), set())  # 目录里没有这一行
        found = service.chosen_kinds("C1", BOOK, fields, "purchase",
                                     {"doctype_src": "task_default", "account_source": "company_rule"}, None)
        self.assertEqual(found, {"payment", "account"})
        proposed = rules.proposal("A", "L0", "task", {"direction": "purchase", "items_account": "5310"}, {}, at=since)
        outcome = rules.compare(proposed, {"direction": "purchase", "items_account": "5320"},
                                chosen=service.chosen_kinds("C1", BOOK, fields, "purchase", {}, since,
                                                            {"source_id": BOOK}))
        self.assertEqual((outcome["changed"], outcome["stale"]), (["account"], []))
        self.assertEqual(evidence.tally([{"proposal": True, "cls": "A", "covered": True, "outcome": outcome}])["a_changed"],
                         1)


# ── 第 9 条：票上记判定 ────────────────────────────────────────────────────────


class Assessment(unittest.TestCase):
    def test_the_assessment_is_written_without_bumping_the_record_version(self):
        writes, events = [], []
        frappe = types.ModuleType("frappe")
        frappe._ = lambda text: text
        rows = {"H1": Row(last_push_record="CWP-1", auto_reasons="judged:account", owner="u@example.com")}
        frappe.db = ns(set_value=lambda doctype, name, values, update_modified=True: writes.append(
            (name, values, update_modified)), get_value=lambda doctype, name, fields, as_dict=False: rows.get(name))
        frappe.publish_realtime = lambda *a, **k: events.append(a)
        frappe.session = ns(user="a@example.com")
        frappe.get_hooks = lambda name: []
        push_state = _load("da_tiers_push_state", APP / "history/push_state.py", {"frappe": frappe})
        at = dt.datetime(2026, 10, 21, 1, 0)
        push_state.record_assessment("H1", cls="B", tier="L0", reasons="x" * 200, proposal={"class": "B"},
                                     checked_at=at)
        name, values, modified = writes[-1]
        self.assertEqual((name, values["auto_class"], values["auto_tier"], len(values["auto_reasons"]), modified),
                         ("H1", "B", "L0", 140, False))
        self.assertNotIn("record_version", values)
        self.assertEqual((json.loads(values["auto_proposal"]), values["auto_checked_at"]), ({"class": "B"}, at))
        push_state.record_assessment("H1", cls="X", tier="L7", checked_at=at)
        self.assertEqual((writes[-1][1]["auto_class"], writes[-1][1]["auto_tier"], writes[-1][1]["auto_proposal"]),
                         ("", "", None))
        # 领单时判定过时：票上的推送状态若还指着这条，清回「没推」，改记成提议
        push_state.withdraw_automation(Row(name="CWP-1", history="H1"), "automation_changed", proposal={"class": "A"})
        values = writes[-1][1]
        self.assertEqual((values["last_push_status"], values["last_push_record"], values["auto_reasons"]),
                         (None, None, "judged:account,automation_changed"))
        self.assertEqual(json.loads(values["auto_proposal"]), {"class": "A"})
        push_state.withdraw_automation(Row(name="CWP-9", history="H1"), "automation_changed")
        self.assertNotIn("last_push_status", writes[-1][1])  # 票上已是别的推送：只记原因


# ── 第 8、10 条：公司档位的评估、核不上即降、看档位的权限 ──────────────────────


class Store:
    """`companies.automation` 的内存替身：档位行、主档、推送记录、回读变化。"""

    def __init__(self):
        self.rows, self.companies, self.records, self.changes = {}, {"CO1": Row(name="CO1", workspace="W1")}, {}, []
        self.read_allowed, self.intake = True, True
        self.others = {}  # (doctype, 名称) → 行：连接、工作空间

    def get_value(self, doctype, filters, fields="name", as_dict=False, for_update=False):
        if doctype in ("Cowork Company", "Cowork ERP Connection", "Cowork Workspace"):
            row = self.companies.get(filters) if doctype == "Cowork Company" else self.others.get((doctype, filters))
            if row is None:
                return None
            return row.get(fields) if isinstance(fields, str) else Row({field: row.get(field) for field in fields})
        row = next((row for row in self.rows.values() if row["company"] == filters.get("company")), None)
        if row is None:
            return None
        if isinstance(fields, str):
            return row.get(fields)
        return Row({field: row.get(field) for field in fields})

    def sql(self, query, values=(), as_dict=False):
        assert "FROM `tabCowork Push Record` r JOIN `tabCowork OCR History` h" in query
        connection, key = values[0], values[1]
        return [Row(row) for row in self.records.get((connection, key.casefold()), [])]


def _companies(store, *, company="CO1", books=None):
    frappe, utils = _frappe({"cowork_company_month_changes": ["rb.changes"]},
                            {"rb.changes": lambda sets, period: [dict(item) for item in store.changes]})
    frappe.db = ns(get_value=store.get_value, sql=store.sql)
    now = dt.datetime(2026, 11, 25, 3, 0)
    utils.now_datetime = lambda: now

    class Doc(Row):
        def is_new(self):
            return not self.get("name")

    frappe.new_doc = lambda doctype: Doc(doctype=doctype)

    def write(doc):
        doc.name = doc.name or doc.company
        store.rows[doc.name] = doc
        return doc

    access = ns(AUTOMATION="Cowork Company Automation", read=lambda doctype, name, for_update=False: store.rows[name],
                write=write)

    def require_read():
        if not store.read_allowed:
            raise Denied("read")

    scope = ns(require_read=require_read, allowed=lambda feature: store.intake)
    books = books if books is not None else [
        {"connection": "C1", "adapter": "express", "key": BOOK.lower(), "label": "ACME 2569",
         "period_start": "2026-01-01", "period_end": "2026-12-31", "branch": "00000"}]
    service = ns(books=lambda row, limit=None: list(books), get=lambda name: Row(name=name),
                 facts=lambda connection: {"account_sets": [{"source_id": BOOK, "attributes": {"tax_id": OWN}}]})
    tasks = ns(books_of=lambda name, connection, day, branch="", remembered=True: [{"key": BOOK.lower()}])
    rules_module = ns(account_company=lambda connection, row: (company, "00000") if row else (None, ""))
    # 「现用的那份」按这次的替身 frappe 读工作空间：每次重载（包上留着的上一次的模块会被 `from … import` 先拿到）
    package = sys.modules.get("pearnly_cowork.companies")
    if package is not None:
        vars(package).pop("current_book", None)
    current = _load("da_tiers_current_book", APP / "companies/current_book.py", {"frappe": frappe})
    module = _load("da_tiers_companies_automation", APP / "companies/automation.py", {
        "frappe": frappe, "frappe.utils": utils, "pearnly_cowork.access": ns(scope=scope),
        "pearnly_cowork.access.scope": scope, "pearnly_cowork.companies.access": access,
        "pearnly_cowork.companies.service": service, "pearnly_cowork.companies.tasks": tasks,
        "pearnly_cowork.companies.rules": rules_module, "pearnly_cowork.companies.current_book": current})
    return module


def _record(name, *, automated=None, proposal=None, completed="2026-10-20 05:00:00", verify="", auto_class=""):
    target = {}
    if automated:
        target["automation"] = {"class": automated, "tier": "L0", "trigger": "task"}
    if proposal:
        target["proposal"] = proposal
    return {"name": name, "history": "H-" + name, "target_json": json.dumps(target), "verify_state": verify,
            "completed_at": dt.datetime.fromisoformat(completed), "auto_class": auto_class}


class CompanyTier(unittest.TestCase):
    def _readback(self, requested, changes=None, truncated=False):
        return {"connection": "C1", "key": BOOK.lower(), "readback": "RB1", "requested_at": requested,
                "changes": changes or {}, "truncated": truncated}

    def test_no_row_is_l0_and_twenty_unchanged_rule_decided_documents_after_the_deadline_move_up(self):
        store = Store()
        module = _companies(store)
        self.assertEqual(module.state("CO1")["tier"], "L0")
        self.assertEqual(module.tier_for_push("C1", BOOK, {"source_id": BOOK}),
                         {"company": "CO1", "tier": "L0", "readback": "", "baseline": "new"})
        store.records[("C1", BOOK.lower())] = [_record(f"R{i}", proposal={"class": "A", "changed": [], "stale": [],
                                                                          "edited": False}) for i in range(19)]
        store.records[("C1", BOOK.lower())].append(_record("AUTO", automated="A"))
        # 十月期的截止日 11-23：当天发起的回读不合格，什么都不写
        store.changes = [self._readback(dt.datetime(2026, 11, 23, 3, 0))]
        self.assertEqual(module.on_readback("C1", BOOK, "2026-10"), {"company": "CO1", "tier": "L0", "changed": False})
        self.assertEqual(store.rows, {})
        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0))]
        self.assertEqual(module.on_readback("C1", BOOK, "2026-10"), {"company": "CO1", "tier": "L1", "changed": True})
        row = store.rows["CO1"]
        self.assertEqual((row.tier, row.reason, row.workspace), ("L1", "readback", "W1"))
        found = json.loads(row.evidence_json)
        self.assertEqual((found["readback"], found["periods"]["2026-10"]["a"], found["periods"]["2026-10"]["auto"]),
                         ("2026-10", 20, 1))
        self.assertEqual(json.loads(row.history_json)[-1]["to"], "L1")
        self.assertEqual(module.tier_for_push("C1", BOOK, {"source_id": BOOK}),
                         {"company": "CO1", "tier": "L1", "readback": "2026-10", "baseline": "ok"})

    def test_pushes_after_the_readback_started_and_truncated_differences_are_not_trusted(self):
        store = Store()
        module = _companies(store)
        late = [_record(f"R{i}", proposal={"class": "A"}, completed="2026-11-25 05:00:00") for i in range(20)]
        store.records[("C1", BOOK.lower())] = late
        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0))]
        module.on_readback("C1", BOOK, "2026-10")
        self.assertEqual((store.rows["CO1"].tier, json.loads(store.rows["CO1"].evidence_json)["periods"]["2026-10"]["a"]),
                         ("L0", 0))  # 推在回读发起之后：没被这份回读覆盖
        store.records[("C1", BOOK.lower())] = [_record(f"R{i}", proposal={"class": "A"}) for i in range(20)]
        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0), truncated=True)]
        module.on_readback("C1", BOOK, "2026-10")
        self.assertEqual(json.loads(store.rows["CO1"].evidence_json)["periods"]["2026-10"]["a_changed"], 20)

    def test_readback_marks_for_the_memory_streak(self):
        """推送的钩子 `readback_marks`：几张推成功的单在合格回读里是 confirmed（覆盖、没被改）/ changed（被改、差异表截断）；
        回读不合格、推在回读发起之后、没有回读的不列（同 `documents` 的覆盖口径，审查处理 10-16）。"""
        store = Store()
        module = _companies(store)
        records = [{"record": "R1", "invoice_date": "2026-10-03", "completed_at": dt.datetime(2026, 10, 20, 5, 0)},
                   {"record": "R2", "invoice_date": "2026-10-09", "completed_at": dt.datetime(2026, 10, 21, 5, 0)},
                   {"record": "R3", "invoice_date": "2026-10-12", "completed_at": dt.datetime(2026, 11, 25, 5, 0)},
                   {"record": "R4", "invoice_date": "2026-09-30", "completed_at": dt.datetime(2026, 10, 1, 5, 0)}]
        calls = []
        hook = module.frappe.get_attr

        def counted(path):
            found = hook(path)
            return lambda sets, period: calls.append(period) or found(sets, period)

        module.frappe.get_attr = counted

        def marks(rows):
            module.frappe.local.cowork_readback_month_cache = {}  # 每次当成新的一轮（缓存只活在一个请求 / 任务里）
            return module.readback_marks("C1", BOOK, rows)

        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0), changes={"R2": "changed"})]
        found = marks(records)
        self.assertEqual(found, {"R1": "confirmed", "R2": "changed", "R4": "confirmed"})
        # 同一轮里再判别的单：每期的回读只取一次（审查处理 10-17）
        self.assertEqual(sorted(calls), ["2026-09", "2026-10"])
        self.assertEqual(module.readback_marks("C1", BOOK, records[:2]), {"R1": "confirmed", "R2": "changed"})
        self.assertEqual(len(calls), 2)
        store.changes = [self._readback(dt.datetime(2026, 11, 23, 3, 0))]   # 截止日当天发起：不合格
        self.assertEqual(marks(records[:2]), {})
        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0), truncated=True)]
        self.assertEqual(marks(records[:1]), {"R1": "changed"})
        store.changes = []
        self.assertEqual(marks(records), {})
        self.assertEqual(module.readback_marks("", BOOK, records), {})

    def test_readback_marks_say_which_kinds_were_changed(self):
        """``detail``（R2，10-18）：每张覆盖到的单带上改了科目 / 现 / 赊 / 对方档里的哪几类（`readback.service.month_changes` 的
        ``kinds``）；confirmed 为空表；差异表截断时只知道整张被改（None）；不带 ``detail`` 照旧只给整张结果。"""
        store = Store()
        module = _companies(store)
        records = [{"record": "R1", "invoice_date": "2026-10-03", "completed_at": dt.datetime(2026, 10, 20, 5, 0)},
                   {"record": "R2", "invoice_date": "2026-10-09", "completed_at": dt.datetime(2026, 10, 21, 5, 0)},
                   {"record": "R5", "invoice_date": "2026-10-11", "completed_at": dt.datetime(2026, 10, 21, 6, 0)}]

        def marks(rows, **kwargs):
            module.frappe.local.cowork_readback_month_cache = {}
            return module.readback_marks("C1", BOOK, rows, **kwargs)
        store.changes = [{**self._readback(dt.datetime(2026, 11, 24, 3, 0), changes={"R2": "changed", "R5": "deleted"}),
                          "kinds": {"R2": ["payment"], "R5": ["account", "payment", "party"]}}]
        self.assertEqual(marks(records, detail=True), {
            "R1": {"readback": "confirmed", "changed": []}, "R2": {"readback": "changed", "changed": ["payment"]},
            "R5": {"readback": "changed", "changed": ["account", "payment", "party"]}})
        self.assertEqual(marks(records), {"R1": "confirmed", "R2": "changed", "R5": "changed"})
        store.changes = [{**self._readback(dt.datetime(2026, 11, 24, 3, 0), changes={"R2": "changed"}, truncated=True),
                          "kinds": {"R2": ["payment"]}}]
        self.assertEqual(marks(records[:2], detail=True), {"R1": {"readback": "changed", "changed": None},
                                                           "R2": {"readback": "changed", "changed": None}})
        # 旧的回读提供者没有 ``kinds``：被改的按不知道
        store.changes = [self._readback(dt.datetime(2026, 11, 24, 3, 0), changes={"R2": "changed"})]
        self.assertEqual(marks(records[1:2], detail=True), {"R2": {"readback": "changed", "changed": None}})

    def test_a_write_mismatch_drops_to_l0_at_once(self):
        store = Store()
        store.rows["CO1"] = Row(name="CO1", company="CO1", workspace="W1", tier="L2", since=None, reason="readback",
                                evidence_json="{}", history_json="[]")
        module = _companies(store)
        self.assertEqual(module.on_mismatch("C1", BOOK, {"source_id": BOOK}),
                         {"company": "CO1", "tier": "L0", "changed": True})
        self.assertEqual((store.rows["CO1"].tier, store.rows["CO1"].reason), ("L0", "written_mismatch"))
        self.assertEqual(module.on_mismatch("C1", BOOK, {"source_id": BOOK})["changed"], False)
        self.assertIsNone(_companies(Store(), company=None).on_mismatch("C1", BOOK, {}))

    def test_a_write_mismatch_at_l0_stops_the_task_baseline_until_new_evidence(self):
        # 审查处理 10-16：已在 L0（定时任务基线推的）核不上也从现在重新起算，定时任务 A 类改出提议
        store = Store()
        module = _companies(store)
        self.assertEqual(module.tier_for_push("C1", BOOK, {"source_id": BOOK})["baseline"], "new")
        module.on_mismatch("C1", BOOK, {"source_id": BOOK})
        row = store.rows["CO1"]
        self.assertEqual((row.tier, row.reason, bool(row.since)), ("L0", "written_mismatch", True))
        self.assertEqual(json.loads(row.history_json)[-1]["reason"], "written_mismatch")
        self.assertEqual(module.tier_for_push("C1", BOOK, {"source_id": BOOK})["baseline"], "written_mismatch")

    def test_seeing_the_tier_needs_the_ledger_read_and_the_stats_need_intake(self):
        store = Store()
        store.rows["CO1"] = Row(name="CO1", company="CO1", workspace="W1", tier="L1",
                                since=dt.datetime(2026, 11, 24, 3, 0), reason="readback",
                                evidence_json=json.dumps({"periods": {"2026-08": {"a": 1}, "2026-09": {"a": 2},
                                                                      "2026-10": {"a": 3}}, "readback": "2026-10"}),
                                history_json="[]")
        module = _companies(store)
        found = module.view("CO1")
        self.assertEqual((found["tier"], found["since"], found["reason"]), ("L1", "2026-11-24T03:00:00Z", "readback"))
        self.assertEqual(sorted(found["stats"]), ["2026-09", "2026-10"])
        store.intake = False
        self.assertIsNone(module.view("CO1")["stats"])
        store.read_allowed = False
        with self.assertRaises(Denied):
            module.view("CO1")

    def test_day_books_follow_the_company_master_like_the_scheduled_task(self):
        module = _companies(Store())
        self.assertEqual(module.day_books("C1", {"source_id": BOOK}, "2026-10-20"), [BOOK.lower()])
        catalog = [{"source_id": BOOK, "attributes": {"tax_id": OWN, "branch": "00000", "period_start": "2026-01-01",
                                                      "period_end": "2026-12-31"}},
                   {"source_id": r"C:\PearnlyExpress\68ACME",
                    "attributes": {"tax_id": OWN, "branch": "00000", "period_start": "2025-01-01",
                                   "period_end": "2025-12-31"}}]
        nobody = _companies(Store(), company=None)
        self.assertEqual(nobody.day_books("C1", catalog[0], "2026-10-20", catalog), [BOOK.lower()])
        self.assertEqual(nobody.day_books("C1", catalog[0], "2025-03-01", catalog), [r"c:\pearnlyexpress\68acme"])
        self.assertIsNone(nobody.day_books("C1", {}, "2026-10-20", catalog))

    def test_day_books_follow_the_company_choice_when_two_books_cover_the_day(self):
        """用户 10-17：选出多份同期时，这家公司对这一组选过现用的那份就只回它（同 T1，`companies.current_book`）；没选过
        照旧回多份（判档记 ``book_many``）。建了主档的身份取主档上的，没建的按目录行。"""
        policy = importlib.import_module("pearnly_cowork.contracts.document_policy")
        twin = r"C:\PearnlyExpress\69ACME2"
        attributes = {"tax_id": OWN, "branch": "00000", "period_start": "2026-01-01", "period_end": "2026-12-31"}
        catalog = [{"source_id": BOOK, "attributes": dict(attributes)}, {"source_id": twin, "attributes": dict(attributes)}]
        keys = [BOOK.lower(), twin.lower()]
        store = Store()
        nobody = _companies(store, company=None)
        self.assertEqual(nobody.day_books("C1", catalog[0], "2026-10-20", catalog), keys)
        store.others[("Cowork ERP Connection", "C1")] = Row(workspace="W1")
        key = policy.current_book_key(f"tax:{OWN}", "00000", "cy", keys)
        store.others[("Cowork Workspace", "W1")] = Row(document_policy=json.dumps({key: twin.lower()}))
        self.assertEqual(nobody.day_books("C1", catalog[0], "2026-10-20", catalog), [twin.lower()])
        # 用户 10-18 加重问：回答时记了各候选的最新录入；选中的那份有新录入照选，未被选中的那份有了更晚的录入就照旧回多份
        # （判档记 ``book_many``、停下再问，不自动挑它）
        seen = policy.book_marks({keys[0]: "2026-10-01T08:00:00", keys[1]: "2026-10-01T09:00:00"})
        store.others[("Cowork Workspace", "W1")] = Row(document_policy=json.dumps(
            {key: twin.lower(), policy.current_book_seen_key(key): seen}))
        written, asked = {}, []
        pool = ns(account_sets_written=lambda connection, account_keys: asked.append((connection, list(account_keys)))
                  or {item: written[item] for item in account_keys if item in written})
        with mock.patch.dict(sys.modules, {"pearnly_cowork.connections": ns(service=pool),
                                           "pearnly_cowork.connections.service": pool}):
            written.update({keys[0]: dt.datetime(2026, 10, 1, 8, 0), keys[1]: dt.datetime(2026, 10, 3, 9, 0)})
            self.assertEqual(nobody.day_books("C1", catalog[0], "2026-10-20", catalog), [twin.lower()])
            written[keys[0]] = dt.datetime(2026, 10, 4, 9, 0)
            self.assertEqual(nobody.day_books("C1", catalog[0], "2026-10-20", catalog), keys)
        self.assertEqual(asked, [("C1", keys)] * 2)
        store.others[("Cowork Workspace", "W1")] = Row(document_policy=json.dumps({key: twin.lower()}))
        # 主档上的身份与工作空间（主档建档时的身份，目录行同税号）
        store.companies["CO1"] = Row(name="CO1", workspace="W1", identity=f"tax:{OWN}")
        module = _companies(store)
        both = [{"key": item, "period_start": "2026-01-01", "period_end": "2026-12-31", "branch": "00000"}
                for item in keys]
        module.tasks = ns(books_of=lambda name, connection, day, branch="", remembered=True: list(both))
        self.assertEqual(module.day_books("C1", catalog[0], "2026-10-20", catalog), [twin.lower()])
        self.assertEqual(module.day_scope("C1", catalog[0], "2026-10-20", catalog)["books"], both)
        store.others[("Cowork Workspace", "W1")] = Row(document_policy="{}")
        self.assertEqual(module.day_books("C1", catalog[0], "2026-10-20", catalog), keys)


# ── 第 4 条：定时任务 ──────────────────────────────────────────────────────────


def _folder_push(judged, adapter="express"):
    calls = {"submit": [], "assess": [], "judged": [], "confirm": []}

    class Error(Exception):
        pass

    db = ns(get_value=lambda doctype, name, fields=None, as_dict=False: (
        ns(edit_count=0, last_push_status="", last_push_record="", record_version=3)
        if doctype == "Cowork OCR History" else (adapter if fields == "adapter" else 7)),
        commit=lambda: None, rollback=lambda: None)
    frappe = ns(db=db, ValidationError=Error, PermissionError=Error, AuthenticationError=Error,
                clear_messages=lambda: None)

    def submit(history, connection, key, *args, **kwargs):
        calls["submit"].append(kwargs)
        return {"status": "prepared", "name": "CWP-1", "revision": 1, "has_writer": True}

    application = ns(choices_view=lambda *a, **k: {"needed": []}, submit=submit, cancel_prepared=lambda *a: None,
                     submission_failed=lambda *a: None,
                     assess=lambda *a, **k: calls["assess"].append(k) or dict(judged))
    contracts = importlib.import_module("pearnly_cowork.folder_intake.contracts")
    destinations = ns(of=lambda adapter: ns(confirm=lambda *a: calls["confirm"].append(a)),
                      READY="ready", UNREACHABLE="unreachable", MISSING="missing",
                      writer_state=lambda adapter, connection, device="": "ready", ready=lambda adapter, p: True)
    push_state = ns(record_assessment=lambda history, **kwargs: calls["judged"].append(kwargs),
                    record_failure=lambda *a, **k: None)
    module = _load("da_tiers_folder_push", APP / "folder_intake/push.py", {
        "frappe": frappe, "pearnly_cowork.folder_intake": ns(
            contracts=contracts, destinations=destinations, follow=ns(choose=lambda rule, history, device=None: None)),
        "pearnly_cowork.history": ns(push_state=push_state), "pearnly_cowork.push": ns(choices=ns()),
        "pearnly_cowork.push.application": ns(service=application)})
    rule = ns(connection="C1", account_set_key=BOOK, posting_kind="service", defaults_json='{"payment": "cash"}',
              reader_device="PC-READ")
    return module, rule, ns(name="E1", filename="bill.pdf", reader_device="PC-READ"), calls


def _judged(cls, action, **over):
    found = {"assessed": True, "class": cls, "tier": "L0", "action": action, "why": "", "codes": "judged:account",
             "account_set": BOOK,
             "proposal": {"class": cls}, "checked_at": dt.datetime(2026, 10, 21),
             "automation": {"class": cls, "tier": "L0", "trigger": "task", "reasons": []}}
    found.update(over)
    return found


class ScheduledTask(unittest.TestCase):
    def test_a_is_pushed_from_l0_with_the_automation_frozen(self):
        push, rule, entry, calls = _folder_push(_judged("A", "push", codes="", proposal=None))
        self.assertEqual(push.push_one(entry, rule, "H1", 0)["status"], "sent")
        self.assertEqual(calls["assess"][0], {"fixed": {"payment": "cash"}, "trigger": "task",
                                              "route_device": "PC-READ"})
        self.assertEqual(calls["submit"][0]["automation"]["class"], "A")
        self.assertEqual(calls["submit"][0]["route_device"], "PC-READ")  # 读文件夹的电脑照旧（ADR-021 第 51 条）
        self.assertEqual((calls["judged"][0]["cls"], calls["judged"][0]["proposal"]), ("A", None))

    def test_b_at_l0_becomes_a_proposal_and_c_stops_without_preparing(self):
        push, rule, entry, calls = _folder_push(_judged("B", "propose"))
        self.assertEqual(push.push_one(entry, rule, "H1", 0), {"status": "held", "reason": "automation:proposal"})
        self.assertEqual((calls["submit"], calls["confirm"]), ([], []))
        self.assertEqual(calls["judged"][0]["proposal"], {"class": "B"})
        push, rule, entry, calls = _folder_push(_judged("C", "stop", codes="image,text_total"))
        self.assertEqual(push.push_one(entry, rule, "H1", 0), {"status": "held", "reason": "automation:review"})
        self.assertEqual((calls["submit"], calls["judged"][0]["reasons"], calls["judged"][0]["proposal"]),
                         ([], "image,text_total", None))
        contracts = importlib.import_module("pearnly_cowork.folder_intake.contracts")
        self.assertEqual(contracts.ticket_state({"status": "held", "reason": "automation:proposal"}, ""),
                         ("stopped", "review"))
        # 没过核对的 C 类不是现成的提议：简报不写「待推送」，同待处理的「还没推送」（审查处理 10-16）
        self.assertEqual(contracts.ticket_state({"status": "held", "reason": "automation:review"}, ""),
                         ("stopped", "checked"))
        briefs = (APP / "folder_intake/briefs.py").read_text(encoding="utf-8")
        checked = briefs.split('if found["stop_reasons"].get("checked"):', 1)[1].split("\n    if ", 1)[0]
        self.assertIn('_("cowork.company.pending.not_pushed")', checked)

    def test_mr_erp_is_not_judged(self):
        push, rule, entry, calls = _folder_push(_judged("C", "stop"), adapter="mrerp")
        self.assertEqual(push.push_one(entry, rule, "H1", 0)["status"], "sent")
        self.assertEqual((calls["assess"], calls["judged"]), ([], []))
        self.assertIsNone(calls["submit"][0]["automation"])

    def test_a_judging_error_is_kept_on_the_ticket_like_a_failed_submit(self):
        push, rule, entry, calls = _folder_push(_judged("A", "push"))
        failed = []
        push.application.submission_failed = lambda history, reason: failed.append((history, reason))

        def refuse(*args, **kwargs):
            raise push.frappe.PermissionError("push.no_access")

        push.application.assess = refuse
        self.assertEqual(push.push_one(entry, rule, "H1", 0), {"status": "failed", "reason": "push.no_access"})
        self.assertEqual((failed, calls["submit"]), ([("H1", "push.no_access")], []))


# ── 第 4、5、10 条：网页上传 / LINE 的扫描 ─────────────────────────────────────


class Sweep:
    def __init__(self, rows, judged, *, allowed=True, writer=True, tasks=()):
        self.rows, self.judged, self.allowed, self.writer, self.tasks = rows, judged, allowed, writer, set(tasks)
        self.filters, self.users, self.marks, self.submits, self.confirms, self.commits = None, [], [], [], [], 0
        frappe = types.ModuleType("frappe")
        frappe.conf = {"pearnly_product": "cowork"}
        frappe.ValidationError, frappe.PermissionError, frappe.AuthenticationError = Invalid, Denied, Invalid
        frappe.session = ns(user="Administrator")
        frappe.set_user = lambda user: (self.users.append(user), setattr(frappe.session, "user", user))
        frappe.clear_messages = lambda: None
        frappe.log_error = lambda title=None, message=None: None
        frappe.get_traceback = lambda: ""
        frappe.get_hooks = lambda name: ["fi.tasks"] if name == "cowork_push_task_histories" else []
        frappe.get_attr = lambda path: lambda names: [name for name in names if name in self.tasks]
        frappe.get_all = self.get_all
        frappe.db = ns(commit=self.commit, rollback=lambda: None, get_value=lambda *a: 5)
        utils = types.ModuleType("frappe.utils")
        utils.now_datetime = lambda: dt.datetime(2026, 10, 21, 12, 0)
        utils.get_datetime = lambda value: value
        frappe.utils = utils
        scope = ns(allowed=lambda feature, user=None: self.allowed)
        push_state = ns(record_assessment=lambda history, **kwargs: self.marks.append((history, kwargs)))
        application = ns(assess=self.assess, submit=self.submit, confirm=self.confirm,
                         cancel_prepared=lambda *a: None, submission_failed=lambda *a: None)
        ledger = ns(has_writer=lambda connection, key, actor, route_device="": self.writer)
        self.module = _load("da_tiers_sweep", APP / "push/automation/sweep.py", {
            "frappe": frappe, "frappe.utils": utils, "pearnly_cowork.access": ns(scope=scope),
            "pearnly_cowork.access.scope": scope, "pearnly_cowork.history": ns(push_state=push_state),
            "pearnly_cowork.history.push_state": push_state,
            "pearnly_cowork.push.application": ns(service=application),
            "pearnly_cowork.push.application.service": application,
            "pearnly_cowork.push.native": ns(service=ledger), "pearnly_cowork.push.native.service": ledger})

    def commit(self):
        self.commits += 1

    def get_all(self, doctype, filters=None, fields=None, order_by=None, limit_page_length=None):
        self.filters = filters
        return [Row(row) for row in self.rows][:limit_page_length]

    def assess(self, history, connection, account_set, posting_kind, **kwargs):
        self.assessed_as = getattr(self, "assessed_as", []) + [(history, kwargs["trigger"],
                                                                self.users[-1] if self.users else "")]
        return dict(self.judged)

    def submit(self, history, connection, key, version, revision, account_set, *args, automation=None, **kwargs):
        self.submits.append((history, key, version, automation))
        return {"status": "prepared", "name": "CWP-" + history, "revision": 1}

    def confirm(self, name, revision, key):
        self.confirms.append((name, key))
        return {"status": "pending"}


def _ticket(name, **over):
    row = {"name": name, "owner": "u@example.com", "source": "manual", "push_connection": "C1",
           "push_account_set": BOOK, "record_version": 2, "posting_kind": "", "edit_count": 0,
           "creation": dt.datetime(2026, 10, 21, 9, 0)}
    row.update(over)
    return row


class UploadSweep(unittest.TestCase):
    def test_only_untouched_express_tickets_settled_for_fifteen_minutes_within_three_days(self):
        sweep = Sweep([_ticket("H1"), _ticket("H2", edit_count=1), _ticket("H3")], _judged("A", "push"),
                      tasks={"H3"})
        found = sweep.module.candidates()
        self.assertEqual([row.name for row in found], ["H1"])  # 改过的、定时任务的不扫
        filters = sweep.filters
        self.assertEqual(filters["intake_target"], "express")
        for field in ("last_push_status", "last_push_record", "last_push_reason", "auto_checked_at"):
            self.assertEqual(filters[field], ["is", "not set"], field)
        self.assertEqual(filters["creation"], ["between", [dt.datetime(2026, 10, 18, 12, 0),
                                                           dt.datetime(2026, 10, 21, 11, 45)]])
        many = Sweep([_ticket(f"H{i}") for i in range(300)], _judged("A", "push"))
        self.assertEqual(len(many.module.candidates()), sweep.module.BATCH)
        self.assertEqual(sweep.module.BATCH, 200)

    def test_an_a_ticket_at_l1_is_judged_marked_then_pushed_as_the_uploader(self):
        sweep = Sweep([_ticket("H1"), _ticket("H2", source="cowork_line")],
                      _judged("A", "push", tier="L1", codes=""))
        self.assertEqual(sweep.module.run(), {"pushed": 2})
        self.assertEqual(sweep.assessed_as, [("H1", "upload", "u@example.com"), ("H2", "line", "u@example.com")])
        self.assertEqual(sweep.users[-1], "Administrator")  # 做完还原身份
        key = sweep.module.push_key("H1", 2)
        self.assertEqual(sweep.submits[0], ("H1", key, 2, _judged("A", "push")["automation"]))
        self.assertEqual(sweep.confirms[0], ("CWP-H1", "confirm:" + key))
        self.assertEqual(sweep.module.push_key("H1", 2), key)  # 请求号由票与版本定：重放回原来那条
        # 先记判定（auto_checked_at）再推：下一轮不再扫到它
        self.assertEqual(sweep.marks[0][1]["cls"], "A")
        self.assertIsNotNone(sweep.marks[0][1]["checked_at"])

    def test_proposals_stops_no_permission_and_no_computer_are_left_to_people(self):
        sweep = Sweep([_ticket("H1")], _judged("B", "propose"))
        self.assertEqual(sweep.module.run(), {"propose": 1})
        self.assertEqual((sweep.submits, sweep.marks[0][1]["proposal"]), ([], {"class": "B"}))
        sweep = Sweep([_ticket("H1")], _judged("C", "stop"))
        self.assertEqual(sweep.module.run(), {"stop": 1})
        self.assertIsNone(sweep.marks[0][1]["proposal"])
        sweep = Sweep([_ticket("H1")], _judged("A", "push"), allowed=False)
        self.assertEqual(sweep.module.run(), {"no_permission": 1})
        self.assertEqual((sweep.marks[0][1]["reasons"], sweep.submits, getattr(sweep, "assessed_as", [])),
                         ("no_permission", [], []))
        sweep = Sweep([_ticket("H1")], _judged("A", "push", codes=""), writer=False)
        self.assertEqual(sweep.module.run(), {"no_computer": 1})
        self.assertEqual((sweep.marks[0][1]["reasons"], sweep.marks[0][1]["proposal"], sweep.submits),
                         ("no_computer", {"class": "A"}, []))
        sweep = Sweep([_ticket("H1", owner="Administrator")], _judged("A", "push"))
        self.assertEqual(sweep.module.run(), {"no_permission": 1})

    def test_a_permission_error_while_judging_skips_the_ticket(self):
        sweep = Sweep([_ticket("H1")], _judged("A", "push"))

        def refuse(*args, **kwargs):
            raise Denied("push.no_access")

        sweep.module.application.assess = refuse
        self.assertEqual(sweep.module.run(), {"no_permission": 1})
        self.assertEqual(sweep.submits, [])

    def test_the_sweep_runs_with_the_five_minute_cron_and_task_tickets_are_excluded_by_a_hook(self):
        tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
        values = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                  and node.targets[0].id in ("scheduler_events", "cowork_push_task_histories",
                                             "cowork_push_company_tier", "cowork_push_day_books",
                                             "cowork_readback_ready", "cowork_company_month_changes",
                                             "cowork_push_verify_mismatch", "cowork_push_readback_marks")}
        self.assertIn("pearnly_cowork.push.automation.sweep.run", values["scheduler_events"]["cron"]["*/5 * * * *"])
        self.assertEqual(values["cowork_push_task_histories"], ["pearnly_cowork.folder_intake.push.task_histories"])
        self.assertEqual(values["cowork_push_company_tier"], ["pearnly_cowork.companies.automation.tier_for_push"])
        self.assertEqual(values["cowork_push_day_books"], ["pearnly_cowork.companies.automation.day_books"])
        self.assertEqual(values["cowork_readback_ready"], ["pearnly_cowork.companies.automation.on_readback"])
        self.assertEqual(values["cowork_company_month_changes"], ["pearnly_cowork.readback.service.month_changes"])
        self.assertEqual(values["cowork_push_verify_mismatch"], ["pearnly_cowork.companies.automation.on_mismatch"])
        self.assertEqual(values["cowork_push_readback_marks"], ["pearnly_cowork.companies.automation.readback_marks"])


# ── 第 5 条：自动推前的判定（边界） ────────────────────────────────────────────


def _paged(pushes, per_period=None):
    """按上传先后（新的在前）排好的推成功的单 → 像库一样按 ``LIMIT`` 与「比上一批最后一张更早」取的 SQL 替身（`assess._recent`
    分批往前取）；记下每批的参数与取回的行数。``per_period``：每这么多张往前一个税期（单据日期从 2026-09 往回），没给都在 2026-09。"""
    start = dt.datetime(2026, 10, 20, 12, 0)

    def day(index):
        back = index // per_period if per_period else 0
        return dt.date(2026 - (back + 3) // 12, (8 - back) % 12 + 1, 1)

    rows = [ns(name=f"R{index}", history_name=f"H{100000 - index}", uploaded=start - dt.timedelta(minutes=index),
               payload_json=json.dumps(item), completed_at=dt.datetime(2026, 9, 5), invoice_date=day(index))
            for index, item in enumerate(pushes)]
    calls = []

    def run_sql(query, values=(), as_dict=False):
        if "INNER JOIN" not in query:
            return []
        limit = values[-1]
        found = rows
        if "h.creation < %s" in query:
            uploaded, _same, history = values[5:8]
            found = [row for row in rows if (row.uploaded, row.history_name) < (uploaded, history)]
        found = found[:limit]
        calls.append({"query": query, "values": values, "limit": limit, "rows": len(found)})
        return found

    return run_sql, calls


def _assess(*, site_ok=True, capable=True, tier=None, text=True, needed=(), readback="2026-09", trigger="upload",
            prior=(), sql=None, recent=None, posting=(None, ""), marks=None, decided=None, payload_over=None,
            suppliers=None, direction="purchase", fields_over=None, responses=None, posting_history="", marks_hook=None,
            overrides=None, stamped=False):
    fields = {"document_type": "tax_invoice", "direction": "purchase", "seller_name": "ผู้ขาย", "seller_tax": SELLER,
              "seller_branch": "00000", "buyer_name": "ACME", "buyer_tax": OWN, "buyer_branch": "00000",
              "invoice_number": "IV69-001", "date": "2026-10-20", "subtotal": "1000.00", "vat": "70.00",
              "total_amount": "1070.00", **(fields_over or {})}
    account = {"source_id": BOOK, "attributes": {"tax_id": OWN, "branch": "00000", "period_start": "2026-01-01",
                                                 "period_end": "2026-12-31"}}
    payload = {"direction": "purchase", "doctype": "RR", "account_set": BOOK, "ref_no": "IV69-001",
               "supplier": {"code": "S001", "tax_id": SELLER}, "base_amount": "1000.00", "vat_amount": "70.00",
               "total_amount": "1070.00", "items": [], "items_account": "5310-01", "account_source": "remembered",
               "doctype_src": "explicit", "item_src": "choice"}
    payload.update(payload_over or {})
    seen = {"rollback": 0, "marks_calls": 0}
    frappe = types.ModuleType("frappe")
    frappe.session = ns(user="u@example.com")
    # 这家供应商在这份账套里最近推成功的单（账套记忆定的科目要连续 5 张、现 / 赊与对方档 3 张有证据的同值，`assess._recent`）：
    # 默认 5 张凭记忆推的同科目、同记法、同一档的单，合格回读都核过（``marks``，钩子 `cowork_push_readback_marks`）。
    # ``stamped``：推送记录带上传时刻（R1 比改值的先后要它，10-18）；``overrides``：这份账套的改值记录（`push.choices.overrides`）
    recent = [{"direction": "purchase", "supplier": {"tax_id": SELLER, "code": "S001", "branch": "00000"},
               "items_account": "5310-01", "account_source": "remembered", "doctype": "RR",
               "doctype_src": "remembered"}] * 5 if recent is None else recent
    marks = {f"R{index}": "confirmed" for index in range(len(recent))} if marks is None else marks

    def run_sql(query, values=(), as_dict=False):
        if "INNER JOIN" in query:
            seen.setdefault("recent_sql", []).append((query, values))
            # 回执（``response_json``）：CC 桌面版实际写的对方档码、各行写入模式；没给的按旧记录（没有这一列）
            return [ns(name=f"R{index}", payload_json=json.dumps(item), completed_at=dt.datetime(2026, 9, 5),
                       invoice_date=dt.date(2026, 9, 1),
                       **({"uploaded": dt.datetime(2026, 9, 3, 9, 0) - dt.timedelta(minutes=index)} if stamped else {}),
                       **({"response_json": json.dumps(responses[index])} if responses and responses[index] else {}))
                    for index, item in enumerate(recent)]
        seen.setdefault("sql", []).append((query, values))
        return [ns(payload_json=json.dumps(item)) for item in prior]

    frappe.db = ns(rollback=lambda: seen.__setitem__("rollback", seen["rollback"] + 1), sql=sql or run_sql)
    frappe.utils = ns(getdate=lambda value: dt.date.fromisoformat(str(value)[:10]))
    doc = ns(name="H1", workspace="W1", client="", review_reasons="[]", creation=dt.datetime(2026, 10, 21, 9, 0),
             source_total=1, invoice_no="IV69-001")
    loaded = ns(endpoint={"adapter": "express"}, doc=ns(name="C1"), access=ns(tenant_id="T"),
                evidence=ns(snapshot={"account_sets": [account]}))
    config = {"reported_suppliers": suppliers or [{"code": "S001", "tax_id": SELLER, "branch": "00000"}],
              "reported_accounts": [{"code": "5310-01"}]}
    pages = [{"_layer_chain": ["text", "L2"] if text else ["direct"], "fields": fields, "_needs_manual_review": False}]

    def marked(connection, key, records, detail=False):
        seen["marks_calls"] += 1
        seen.setdefault("marks", (connection, key, records))
        seen["marks_detail"] = detail
        return marks_hook(connection, key, records, detail=detail) if marks_hook else marks

    def changes(connection, key, values, since=None):
        seen.setdefault("overrides", []).append((connection, key, dict(values)))
        return list(overrides or ())
    service = ns(
        _express_context=lambda doc, loaded, key: {
            "target": ns(account_set=BOOK), "flat": {}, "fields": fields, "direction": direction,
            "preparation": {"mappings": {}, "supplier_profiles": {}, "bank_index": ()},
            "selected": {"config": config}, "history_value": {"pages": pages}, "own_tax": OWN,
            "account_row": account, "invoice_date": dt.date(2026, 10, 20)},
        _document_block=lambda doc: None, _work_branch=lambda *a: "", _fixed=lambda *a: None,
        _company_rules=lambda *a: (None, True), _same_company=lambda *a: [], _apply_stock_group=lambda *a: None,
        _account_tax=lambda row: OWN, _loads=json.loads,
        _posting_decision=lambda doc, connection, row, kind, flat, direction, config, today, decide: (
            seen.setdefault("posting", (doc, connection, row, kind)) and (*posting, decide(posting[0]))),
        _posting_history=lambda connection, key, fields, direction, posting_from: (
            seen.setdefault("posting_history", (connection, key, direction, posting_from)) and posting_history),
        _decisions=lambda *a, **k: {"applied": {"account": "5310-01"}, "needed": list(needed),
                                    "decided_by": decided or {"account": "remembered", "vat": "remembered"}})
    automation_service = ns(
        tier=lambda *a: tier or {"company": "CO1", "tier": "L1", "readback": readback, "baseline": "ok"},
        day_books=lambda *a: [BOOK.lower()], test_site_ok=lambda key: site_ok,
        readback_marks=marked,
        verify_capable=lambda connection, key, actor, route_device="": capable)
    printed = f"{SELLER} IV69-001 วันที่ 20/10/2569 รวม 1,000.00 ภาษี 70.00 รวมทั้งสิ้น 1,070.00"
    text_layer = ns(pages=lambda doc: [printed], text=lambda doc, found=None: "\n".join(found or [printed]))
    prepared = ns(payload=payload, disposition="ready", reason="")
    module = _load("da_tiers_assess", APP / "push/application/assess.py", {
        "frappe": frappe, "pearnly_cowork.access": ns(scope=ns(require=lambda feature: None)),
        "pearnly_cowork.connections": ns(service=ns(read_for_push=lambda *a, **k: loaded)),
        "pearnly_cowork.history": ns(storage=ns(read=lambda name: doc), text_layer=text_layer),
        "pearnly_cowork.push": ns(choices=ns(remembered=lambda *a: {}, overrides=changes)),
        "pearnly_cowork.push.application": ns(service=service),
        "pearnly_cowork.push.application.provenance": ns(original_history=lambda doc: doc.name),
        "pearnly_cowork.push.automation": ns(rules=rules, service=automation_service),
        "pearnly_cowork.push.express.preflight": ns(ExpressContext=lambda **kwargs: kwargs),
        "pearnly_cowork.push.express.prepare": ns(prepare_express=lambda *a, **k: prepared)})
    return module.run("H1", "C1", BOOK, None, trigger=trigger, today=dt.date(2026, 10, 25)), seen


class Judging(unittest.TestCase):
    def test_a_clean_rule_decided_text_pdf_at_l1_is_pushed_and_only_looked_at(self):
        found, seen = _assess()
        self.assertEqual((found["class"], found["tier"], found["action"], found["why"]), ("A", "L1", "push", ""))
        self.assertEqual(found["automation"], {"class": "A", "tier": "L1", "trigger": "upload", "reasons": []})
        self.assertEqual(found["proposal"]["values"]["account"], "5310-01")
        self.assertEqual(seen["rollback"], 1)  # 只看不记：结束整笔回滚

    def test_a_remembered_account_needs_five_recent_pushes_with_it(self):
        """账套记忆的科目：这家供应商在这份账套里最近 5 张有证据的推送都记这个科目才算 A（`_recent`，10-16 定 3 张，10-18 R3 改 5 张）。"""
        found, seen = _assess()
        self.assertEqual(found["class"], "A")
        query, values = seen["recent_sql"][0]
        self.assertIn("h.workspace=%s AND h.seller_tax IN %s", query)
        self.assertEqual((values[0], set(values[1]), values[2:5]), ("W1", {SELLER}, ("H1", "C1", BOOK)))
        connection, key, records = seen["marks"]
        self.assertEqual((connection, key, records[0]["record"], records[0]["invoice_date"]), ("C1", BOOK, "R0",
                                                                                              "2026-09-01"))
        mine = {"direction": "purchase", "supplier": {"tax_id": SELLER}, "items_account": "5310-01",
                "account_source": "remembered"}
        two = [mine] * 2
        found, _seen = _assess(recent=two)
        self.assertEqual((found["class"], found["reasons"]), ("B", ["judged:account"]))
        other = two + [{**mine, "items_account": "5320-01"}]
        self.assertEqual(_assess(recent=other)[0]["class"], "B")
        # 别家（载荷里的供应商税号不同）、销项的不算，跳过后再数
        mixed = [{"direction": "sales", "items_account": "5310-01"},
                 {"direction": "purchase", "supplier": {"tax_id": "0105556012341"}, "items_account": "5310-01"}] + two * 2
        self.assertEqual(_assess(recent=mixed, marks={f"R{i}": "confirmed" for i in range(6)})[0]["class"], "B")  # 4 张
        self.assertEqual(_assess(recent=mixed + [mine], marks={f"R{i}": "confirmed" for i in range(7)})[0]["class"], "A")
        self.assertEqual(_assess(recent=[mine] * 3)[0]["reasons"], ["judged:account"])  # 3 张：R3 之前是 A

    def test_pushes_by_memory_count_only_after_a_qualified_readback(self):
        """凭账套记忆推出去的单要合格回读覆盖、没被改才算证据（审查处理 10-16：CC 自己推的单不能攒成「连续 3 张」）；
        推的时候科目由人选的（chosen）照算；回读里被改的断开。"""
        self.assertEqual(_assess(marks={})[0]["class"], "B")                       # 都还没回读核过
        self.assertEqual(_assess(marks={"R0": "confirmed", "R1": "changed", "R2": "confirmed"})[0]["class"], "B")
        chosen = [{"direction": "purchase", "supplier": {"tax_id": SELLER}, "items_account": "5310-01",
                   "account_source": "chosen"}] * 5
        self.assertEqual(_assess(recent=chosen, marks={})[0]["class"], "A")

    def test_remembered_payment_and_party_need_three_pushes_with_evidence(self):
        """账套记忆定的现 / 赊、对方档同科目（10-17 推广）：一次查询取这家最近推成功的单、回读只调一次，三类各数各的；对方档只数
        同税号 + 同一分店的；一张单回读里被改，三类一起断开；都不是账套记忆定的不查库；销项不查、记忆定的对方档按 0 张。"""
        two = [{"code": code, "tax_id": SELLER, "branch": "00000"} for code in ("S001", "S002")]
        memory = dict(decided={"account": "remembered", "vat": "remembered", "party": "remembered"},
                      payload_over={"doctype_src": "remembered", "supplier": {"code": "S001", "tax_id": SELLER}},
                      suppliers=two)
        found, seen = _assess(**memory)
        self.assertEqual((found["class"], found["items"]["party"], found["items"]["payment"]), ("A", "A", "A"))
        self.assertEqual((len(seen["recent_sql"]), seen["marks_calls"]), (1, 1))
        # 现 / 赊只有 2 张核过（科目这几张是人选的、照算）：现 / 赊 B
        chosen = [{"direction": "purchase", "supplier": {"tax_id": SELLER, "code": "S001", "branch": "00000"},
                   "items_account": "5310-01", "account_source": "chosen", "doctype": "RR",
                   "doctype_src": "remembered"}] * 5
        found, _seen = _assess(recent=chosen, marks={"R0": "confirmed", "R1": "confirmed"}, **memory)
        self.assertEqual((found["class"], found["reasons"]), ("B", ["judged:party", "judged:payment"]))
        # 记的现 / 赊与最近几张不同：B
        found, _seen = _assess(**{**memory, "payload_over": {**memory["payload_over"], "doctype": "HP"}})
        self.assertEqual((found["items"]["payment"], found["items"]["party"]), ("B", "A"))
        # 别的分店推的不算对方档的连续
        branches = [{**chosen[0], "account_source": "remembered",
                     "supplier": {**chosen[0]["supplier"], "branch": branch}}
                    for branch in ("00000", "00001", "00000", "00000")]
        found, _seen = _assess(recent=branches, **memory)
        self.assertEqual((found["items"]["party"], found["items"]["payment"]), ("A", "A"))
        found, _seen = _assess(recent=branches[:3], **memory)
        self.assertEqual((found["items"]["party"], found["items"]["payment"]), ("B", "A"))
        # 一张回读里被改：科目、现 / 赊、对方档一起断开
        found, _seen = _assess(marks={"R0": "confirmed", "R1": "changed", "R2": "confirmed"}, **memory)
        self.assertEqual((found["items"]["account"], found["items"]["payment"], found["items"]["party"]),
                         ("B", "B", "B"))
        # 都不是账套记忆定的：不查库
        found, seen = _assess(payload_over={"account_source": "chosen"})
        self.assertEqual(found["class"], "A")
        self.assertNotIn("recent_sql", seen)
        self.assertEqual(seen["marks_calls"], 0)
        # 销项：记忆定的对方档不查、按 0 张算
        sales = {"direction": "sales", "doctype": "IV", "doctype_src": "explicit", "account_source": "task_default",
                 "customer": {"code": "C2", "tax_id": SELLER}}
        found, seen = _assess(direction="sales", decided={"party": "remembered"}, payload_over=sales)
        self.assertEqual(found["items"]["party"], "B")
        self.assertNotIn("recent_sql", seen)

    def test_the_evidence_follows_what_cc_desktop_wrote(self):
        """审查处理 10-17：对方档取 CC 桌面版实际写入的码（回执 ``meta.party_code``，回读比的就是它）；载荷写定一档、桌面版写了
        另一档按断开算；真写了库存行的单回读不比分录，科目一类的 confirmed 不算证据；票面写明现 / 赊的单不算现 / 赊的证据；
        没出载荷不查库；对方税号不是 13 位时不查库、记忆定的现 / 赊与对方档都按 0 张（B）。"""
        two = [{"code": code, "tax_id": SELLER, "branch": "00000"} for code in ("S001", "S002")]
        memory = dict(decided={"account": "remembered", "vat": "remembered", "party": "remembered"},
                      payload_over={"doctype_src": "remembered", "supplier": {"code": "S002", "tax_id": SELLER}},
                      suppliers=two)
        # 推的时候 CC 自己找档（载荷没写码）、桌面版写了 S002，会计也认了：记忆 S002 攒得起来
        found_by_cc = [{"direction": "purchase", "supplier": {"tax_id": SELLER, "branch": "00000"},
                        "items_account": "5310-01", "account_source": "remembered", "doctype": "RR",
                        "doctype_src": "remembered"}] * 3
        written = [{"meta": {"party_code": "S002"}}] * 3
        found, _seen = _assess(recent=found_by_cc, responses=written, **memory)
        self.assertEqual(found["items"]["party"], "A")
        self.assertEqual(_assess(recent=found_by_cc, **memory)[0]["items"]["party"], "B")  # 旧记录没有回执：码取不到
        # 载荷写定 S001、桌面版写了 S002：按断开算
        planned = [{**found_by_cc[0], "supplier": {"tax_id": SELLER, "code": "S002", "branch": "00000"}}] * 3
        swapped = [{"meta": {"party_code": "S002"}}, {"meta": {"party_code": "S001"}}, {"meta": {"party_code": "S002"}}]
        self.assertEqual(_assess(recent=planned, responses=swapped, **memory)[0]["items"]["party"], "B")
        # 真写了库存行的单：科目只认推的时候的来源，回读 confirmed 不算
        stock = [{"line_modes": [{"seq": 1, "mode": "stock_item"}]}] * 5
        found, _seen = _assess(responses=stock)
        self.assertEqual((found["class"], found["items"]["account"]), ("B", "B"))
        # 票面写明的现 / 赊回读核过也不是现 / 赊的证据
        explicit = [{**found_by_cc[0], "supplier": {"tax_id": SELLER, "code": "S002", "branch": "00000"},
                     "doctype_src": "explicit"}] * 3
        found, _seen = _assess(recent=explicit, **memory)
        self.assertEqual((found["items"]["payment"], found["items"]["party"]), ("B", "A"))
        # 没出载荷（要人选）：本来就是 C，不查库
        found, seen = _assess(needed=[{"kind": "account"}], **memory)
        self.assertEqual(found["class"], "C")
        self.assertNotIn("recent_sql", seen)
        # 对方税号不是 13 位：不查库，记忆定的现 / 赊与对方档按 0 张
        found, seen = _assess(fields_over={"seller_tax": "123456"}, **memory)
        self.assertNotIn("recent_sql", seen)
        self.assertEqual(seen["marks_calls"], 0)
        self.assertEqual((found["items"]["payment"], found["items"]["party"]), ("B", "B"))

    def test_the_company_goods_posting_choice_reaches_the_judgement(self):
        """商品单入账方式按公司选过（`service._posting_decision`）：判定用选的入账类型，posting 一项算 B（审查处理 10-16）。"""
        found, seen = _assess(posting=("stock", "company_choice"))
        self.assertEqual((found["class"], found["items"].get("posting")), ("B", "B"))
        self.assertEqual(seen["posting"][3], None)  # 调用方给的入账类型原样交给它判
        # 这家供应商的 Express 历史（只读存着的，`service._posting_history`）：与公司选的相反交人（用户 10-17）
        self.assertEqual(seen["posting_history"], ("C1", BOOK, "purchase", "company_choice"))
        found, _seen = _assess(posting=("service", "company_choice"), posting_history="stock")
        self.assertEqual((found["class"], found["items"]["posting"]), ("C", "C"))
        self.assertIn("conflict:posting", found["reasons"])
        # 一致、载荷正好按它写（记费用、没有库存行）：入账方式一项 A，整张 A
        found, _seen = _assess(posting=("service", "company_choice"), posting_history="service")
        self.assertEqual((found["class"], found["items"]["posting"]), ("A", "A"))

    def test_the_evidence_window_skips_unchecked_memory_pushes_first(self):
        """证据窗口（10-17 改取法，`automation.memory_window`）：先跳过凭记忆推出去、还没合格回读核过的，再在能当证据的里取最近的。
        高频供应商最新几百张都是没核过的记忆推送、更早有合格回读核过的三张同值：分批往前取到它们就停，规则定（A）；没有任何回读的
        照样 B；回读里被改的照样断开；往回最多看 `automation.RECENT_SCAN_MAX` 张（6 次查询、1,000 行），证据在更早的也是 B。"""
        memory = {"direction": "purchase", "supplier": {"tax_id": SELLER, "code": "S001", "branch": "00000"},
                  "items_account": "5310-01", "account_source": "remembered", "doctype": "RR",
                  "doctype_src": "remembered"}
        checked = {f"R{index}": "confirmed" for index in range(600, 605)}
        sql, calls = _paged([memory] * 605)
        found, seen = _assess(sql=sql, marks=checked)
        self.assertEqual((found["class"], found["items"]["account"]), ("A", "A"))
        # 30 + 200 + 200 + 200 张取到第 605 张就停；每批标一次回读；往前接着取按上一批最后一张
        self.assertEqual([call["limit"] for call in calls], [30, 200, 200, 200])
        self.assertEqual(seen["marks_calls"], 4)
        self.assertNotIn("h.creation < %s", calls[0]["query"])
        self.assertIn("ORDER BY h.creation DESC, h.name DESC", calls[0]["query"])
        self.assertEqual(calls[1]["values"][5:8], (dt.datetime(2026, 10, 20, 11, 31),) * 2 + ("H99971",))
        # 记忆定的现 / 赊、对方档同一个窗口
        memory_all = dict(decided={"account": "remembered", "vat": "remembered", "party": "remembered"},
                          payload_over={"doctype_src": "remembered"})
        sql, calls = _paged([memory] * 605)
        found, _seen = _assess(sql=sql, marks=checked, **memory_all)
        self.assertEqual((found["class"], found["items"]["payment"], found["items"]["party"]), ("A", "A", "A"))
        # 没有任何回读核过：取到底（不足上限）还是 0 张，B
        sql, calls = _paged([memory] * 605)
        found, _seen = _assess(sql=sql, marks={})
        self.assertEqual((found["class"], found["reasons"]), ("B", ["judged:account"]))
        self.assertEqual(sum(call["rows"] for call in calls), 605)
        # 回读里被改的照样断开
        sql, _calls = _paged([memory] * 605)
        broken = {"R600": "confirmed", "R601": "changed", "R602": "confirmed", "R603": "confirmed", "R604": "confirmed"}
        self.assertEqual(_assess(sql=sql, marks=broken)[0]["class"], "B")
        # 推的时候由人选的照常算证据（不用等回读）
        chosen = {**memory, "account_source": "chosen"}
        sql, calls = _paged([memory] * 100 + [chosen] * 5)
        self.assertEqual(_assess(sql=sql, marks={})[0]["class"], "A")
        self.assertEqual(len(calls), 2)
        # 上界：往回最多 1,000 张、6 次查询，核过的五张在第 1,001 张以后够不着：B
        far = {f"R{index}": "confirmed" for index in range(1000, 1005)}
        sql, calls = _paged([memory] * 1005)
        found, seen = _assess(sql=sql, marks=far)
        self.assertEqual(found["class"], "B")
        self.assertEqual([call["limit"] for call in calls], [30, 200, 200, 200, 200, 170])
        self.assertEqual((sum(call["rows"] for call in calls), seen["marks_calls"]), (auto.RECENT_SCAN_MAX, 6))
        self.assertEqual(auto.RECENT_SCAN_MAX, 1000)
        # 正好落在上限里（第 996–1,000 张）：A
        near = {f"R{index}": "confirmed" for index in range(995, 1000)}
        sql, calls = _paged([memory] * 1005)
        self.assertEqual(_assess(sql=sql, marks=near)[0]["class"], "A")

    def test_the_evidence_window_bounds_readback_queries_by_tax_period(self):
        """回读一侧的上界（审查处理 10-17）：每个税期另查一次回读（`companies.automation.readback_marks` → 钩子
        ``cowork_company_month_changes``），按条数的上限挡不住期数——月推 20 张、一直凑不出证据的供应商往回 1,000 张跨 50 期。窗口
        碰到第 `automation.RECENT_PERIOD_MAX` + 1 个不同的税期就截掉、不再往前取：一张单至多查 6 次回读；同一请求里第二张不再查；
        证据真不够的照样 B，落在税期上限以内的照样 A。用真的 `readback_marks`，只数钩子按期的调用。"""
        memory = {"direction": "purchase", "supplier": {"tax_id": SELLER, "code": "S001", "branch": "00000"},
                  "items_account": "5310-01", "account_source": "remembered", "doctype": "RR",
                  "doctype_src": "remembered"}
        self.assertEqual(auto.RECENT_PERIOD_MAX, 6)
        store = Store()
        companies = _companies(store)
        hook = companies.frappe.get_attr
        asked = []
        readback = {"connection": "C1", "key": BOOK.lower(), "readback": "RB1",
                    "requested_at": dt.datetime(2026, 11, 24, 3, 0), "changes": {}, "truncated": False}

        def run(checked_from, *, fresh=True):
            """``checked_from``：这一期及更早的税期有合格回读（None 一期都没有）。"""
            if fresh:
                companies.frappe.local.cowork_readback_month_cache = {}  # 新的一个请求
            store.changes = [readback]
            companies.frappe.get_attr = lambda path: (
                lambda sets, period: asked.append(period) or (
                    hook(path)(sets, period) if checked_from and period <= checked_from else []))
            asked.clear()
            sql, calls = _paged([memory] * 1003, per_period=20)
            found, seen = _assess(sql=sql, marks_hook=companies.readback_marks)
            return found, seen, calls

        # 一期都没有回读：取 30 + 200 张，第 121 张落进第 7 个税期就截掉、停；回读只查 6 期（旧的 1,000 张会查 50 期）
        found, seen, calls = run(None)
        self.assertEqual((found["class"], found["reasons"]), ("B", ["judged:account"]))
        self.assertEqual([call["limit"] for call in calls], [30, 200])
        self.assertEqual(seen["marks_calls"], 2)
        self.assertEqual(asked, ["2026-09", "2026-08", "2026-07", "2026-06", "2026-05", "2026-04"])
        self.assertEqual(len(asked), auto.RECENT_PERIOD_MAX)
        # 同一请求里再判一张：各期都在缓存里，不再查
        found, _seen, _calls = run(None, fresh=False)
        self.assertEqual((found["class"], asked), ("B", []))
        # 最近三期还没回读核过、更早的核过：在税期上限以内凑够，A；查过的期不超过 6
        found, _seen, calls = run("2026-06")
        self.assertEqual(found["class"], "A")
        self.assertLessEqual(len(asked), auto.RECENT_PERIOD_MAX)
        # 只有第 7 期及更早的核过：够不着，B，照样只查 6 期
        found, _seen, calls = run("2026-03")
        self.assertEqual(found["class"], "B")
        self.assertEqual(len(asked), auto.RECENT_PERIOD_MAX)
        self.assertEqual(sum(call["rows"] for call in calls), 230)

    def test_r1_people_changing_the_same_memory_value_in_the_account_set_makes_it_b(self):
        """R1（10-18 E1）：账套记忆定的科目 / 现 / 赊连续张数够了，才取这份账套近 60 天的改值记录（`push.choices.overrides`，一次查询）；
        有人把同一个记忆值改掉、且晚于这家最近 3 张证据里最早那张（推送记录的上传时刻）→ 这一项降 B；判成 A 的那几类冻结记忆值与
        证据时刻（``automation.overrule``），领单时再判一次。"""
        memory = dict(decided={"account": "remembered", "vat": "remembered"}, payload_over={"doctype_src": "remembered"})
        found, seen = _assess(stamped=True, **memory)
        self.assertEqual(found["class"], "A")
        self.assertEqual(seen["overrides"], [("C1", BOOK, {"account": "5310-01", "payment": "credit"})])
        since = "2026-09-03T08:58:00"  # 最近 3 张证据（R0–R2）里最早那张
        self.assertEqual(found["automation"]["overrule"], {"account": {"value": "5310-01", "since": since},
                                                           "payment": {"value": "credit", "since": since}})
        later = {"kind": "payment", "old": "credit", "new": "cash", "at": dt.datetime(2026, 9, 20, 10, 0)}
        found, seen = _assess(stamped=True, overrides=[later], **memory)
        self.assertEqual((found["class"], found["reasons"], found["items"]["account"]), ("B", ["judged:payment"], "A"))
        self.assertEqual(set(found["automation"]["overrule"]), {"account"})
        moved = {"kind": "account", "old": "5310-01", "new": "5320-01", "at": dt.datetime(2026, 10, 1, 8, 0)}
        self.assertEqual(_assess(stamped=True, overrides=[moved], **memory)[0]["reasons"], ["judged:account"])
        # 改在这家证据之前：证据是改法之后攒的，照 A
        earlier = {**later, "at": dt.datetime(2026, 9, 3, 8, 0)}
        self.assertEqual(_assess(stamped=True, overrides=[earlier], **memory)[0]["class"], "A")
        # 连续张数不够（本来就是 B）、推送记录没有上传时刻：不查库、不冻结
        two = [{"direction": "purchase", "supplier": {"tax_id": SELLER}, "items_account": "5310-01",
                "account_source": "remembered"}] * 2
        found, seen = _assess(stamped=True, recent=two)
        self.assertNotIn("overrides", seen)
        self.assertNotIn("overrule", found["automation"])
        found, seen = _assess(overrides=[later], **memory)
        self.assertEqual(found["class"], "A")
        self.assertNotIn("overrides", seen)
        # 都不是账套记忆定的：不查
        found, seen = _assess(stamped=True, payload_over={"account_source": "chosen"})
        self.assertNotIn("overrides", seen)

    def test_r2_the_share_of_readback_account_changes_counts_the_whole_window(self):
        """R2（10-18 E1，只管科目）：证据窗口里有合格回读结果的推送中科目被改超过 20% → 科目降 B。回读标记带上改了哪几类
        （``detail``）；科目连续张数够了时取满整个窗口才算比例（凑够张数就停只对不够的、和现 / 赊、对方档），上界不变。"""
        memory = {"direction": "purchase", "supplier": {"tax_id": SELLER, "code": "S001", "branch": "00000"},
                  "items_account": "5310-01", "account_source": "remembered", "doctype": "RR",
                  "doctype_src": "remembered"}

        def hook(changed, kinds):
            def marks(connection, key, records, detail=False):
                out = {}
                for item in records:
                    index = int(item["record"][1:])
                    mark = "changed" if index in changed else "confirmed"
                    out[item["record"]] = {"readback": mark, "changed": list(kinds) if mark == "changed" else []} \
                        if detail else mark
                return out
            return marks
        sql, calls = _paged([memory] * 300)
        found, seen = _assess(sql=sql, marks_hook=hook(set(), ()))
        self.assertEqual(found["class"], "A")
        self.assertTrue(seen["marks_detail"])
        self.assertEqual([call["limit"] for call in calls], [30, 200, 200])  # 连续 5 张够了：取满窗口算比例
        sixty = set(range(240, 300))  # 300 张里 60 张科目被改：正好 20%，不降
        sql, _calls = _paged([memory] * 300)
        self.assertEqual(_assess(sql=sql, marks_hook=hook(sixty, ("account",)))[0]["class"], "A")
        sql, _calls = _paged([memory] * 300)
        found, _seen = _assess(sql=sql, marks_hook=hook(sixty | {239}, ("account",)))
        self.assertEqual((found["class"], found["reasons"]), ("B", ["judged:account"]))
        # 只改了别的类的不算科目被改；只知道整张被改（老的提供者只给整张结果）的算
        sql, _calls = _paged([memory] * 300)
        self.assertEqual(_assess(sql=sql, marks_hook=hook(sixty | {239}, ("payment",)))[0]["class"], "A")
        sql, _calls = _paged([memory] * 300)
        plain = {f"R{index}": "changed" if index in sixty | {239} else "confirmed" for index in range(300)}
        self.assertEqual(_assess(sql=sql, marks=plain)[0]["class"], "B")
        # 科目连续张数不够：不再往前取（本来就是 B）；科目不是账套记忆定的：凑够现 / 赊就停
        sql, calls = _paged([memory] * 300)
        self.assertEqual(_assess(sql=sql, marks_hook=hook({0}, ("account",)))[0]["class"], "B")
        self.assertEqual([call["limit"] for call in calls], [30])
        sql, calls = _paged([{**memory, "account_source": "chosen"}] * 300)
        found, _seen = _assess(sql=sql, marks_hook=hook(set(), ()), payload_over={"account_source": "chosen",
                                                                                   "doctype_src": "remembered"})
        self.assertEqual((found["class"], [call["limit"] for call in calls]), ("A", [30]))

    def test_boundaries_turn_a_push_into_a_proposal(self):
        found, _seen = _assess(site_ok=False)
        self.assertEqual((found["action"], found["why"], found["codes"]), ("propose", "test_path", "test_path"))
        found, _seen = _assess(capable=False)
        self.assertEqual((found["action"], found["why"]), ("propose", "verify_version"))
        found, _seen = _assess(readback="2026-07")
        self.assertEqual((found["action"], found["why"]), ("propose", "readback_stale"))
        found, _seen = _assess(tier={"company": "", "tier": "L0", "readback": "", "baseline": "new"})
        self.assertEqual((found["action"], found["why"]), ("propose", "tier"))

    def test_the_scheduled_task_pushes_a_at_l0_only_on_its_baseline(self):
        found, _seen = _assess(trigger="task", tier={"company": "CO1", "tier": "L0", "readback": "", "baseline": "new"})
        self.assertEqual((found["class"], found["action"]), ("A", "push"))
        found, _seen = _assess(trigger="task", tier={"company": "CO1", "tier": "L0", "readback": "2026-09",
                                                     "baseline": "changed"})
        self.assertEqual((found["action"], found["why"]), ("propose", "changed"))
        self.assertIn("changed", found["codes"])

    def test_an_already_queued_same_document_is_not_pushed_again(self):
        # 审查处理 10-16：同一轮扫描里先判的那张已排进队列（还没写、没有 Express 单号），后一张归 C、记 duplicate_number
        queued = {"direction": "purchase", "supplier": {"tax_id": SELLER}, "ref_no": "IV69-001",
                  "total_amount": "1070.00"}
        found, seen = _assess(prior=[queued])
        self.assertEqual((found["class"], found["action"]), ("C", "stop"))
        self.assertIn("duplicate_number", found["reasons"])
        query, values = seen["sql"][0]
        self.assertIn("status IN %s", query)
        self.assertEqual(values[2], ("prepared", "pending", "leased", "manual", "success"))
        self.assertNotIn("cancelled", values[2])
        found, _seen = _assess(prior=[{**queued, "total_amount": "1071.00"}])
        self.assertEqual(found["class"], "A")
        found, _seen = _assess(prior=[{**queued, "direction": "sales"}])
        self.assertEqual(found["class"], "A")

    def test_images_and_needed_choices_stop(self):
        found, _seen = _assess(text=False)
        self.assertEqual((found["class"], found["action"]), ("C", "stop"))
        self.assertIn("image", found["reasons"])
        found, _seen = _assess(needed=[{"kind": "account"}])
        self.assertEqual((found["class"], found["action"], found["proposal"]), ("C", "stop", None))


# ── 第 5 条：领单重核与不弹「账套被占用」 ────────────────────────────────────


class LeaseRecheck(unittest.TestCase):
    def _ledger(self, current):
        saved, withdrawn = [], []
        stub = mock.MagicMock
        modules = {"frappe": stub(), "pearnly_cowork": types.ModuleType("pearnly_cowork")}
        for name in ("access", "access.scope", "history", "history.push_state", "connections", "connections.service",
                     "connections.access", "connections.pool", "connections.preparation", "contracts",
                     "contracts.push", "push", "push.express", "push.express.common", "push.lifecycle",
                     "push.express.referenced", "push.native", "push.native.contracts", "push.native.documents",
                     "push.native.completion", "targets", "targets.freeze", "targets.contracts", "targets.identity"):
            modules["pearnly_cowork." + name] = stub()
        modules["pearnly_cowork.push.automation"] = ns(rules=rules, service=ns(
            current_hash=lambda version, connection, key, account, scope=None, overrule=None: (
                current(version, account, scope) if overrule is None else current(version, account, scope, overrule))))
        modules["pearnly_cowork.push.native.documents"].TYPES = ("Cowork Push Record", "Cowork Push Attempt", "Lock")
        modules["pearnly_cowork.targets.identity"].normalize_express_account_key = lambda value: str(value).lower()
        push_state = ns(withdraw_automation=lambda row, reason, proposal=None: withdrawn.append((row.name, reason,
                                                                                                 proposal)))
        modules["pearnly_cowork.history"].push_state = push_state
        modules["pearnly_cowork.history.push_state"] = push_state
        module = _load("pearnly_cowork.push.native.service_da_tiers", APP / "push/native/service.py", modules)
        module.push_state = push_state
        # 领单重核按需导入 `push.automation`（只有自动推的单才用得着）：调用时换成替身
        patcher = mock.patch.dict(sys.modules, {
            "pearnly_cowork.push.automation": modules["pearnly_cowork.push.automation"],
            "pearnly_cowork.push.automation.service": modules["pearnly_cowork.push.automation"].service})
        patcher.start()
        self.addCleanup(patcher.stop)
        module._save_record = lambda row, timestamp, select_history=False: saved.append((row.status, row.reason))
        module.now_utc = lambda value=None: dt.datetime(2026, 10, 21, 3, 0)
        module.project = lambda row: dict(row)
        return module, saved, withdrawn

    def test_a_stale_automatic_push_is_cancelled_and_turned_into_a_proposal(self):
        frozen = {"automation": {"class": "A", "tier": "L1", "trigger": "upload", "hash": "H-OLD",
                                 "scope": {"party": ["x"]}}}
        context = ns(history_version=3, connection="C1", account=BOOK.lower())
        loaded = ns(evidence=ns(snapshot={"account_sets": [{"source_id": BOOK}]}))
        module, saved, withdrawn = self._ledger(lambda version, account, scope: "H-OLD")
        self.assertFalse(module._automation_stale(frozen, context, loaded))
        self.assertFalse(module._automation_stale({}, context, loaded))  # 人发起的单不核
        module, saved, withdrawn = self._ledger(lambda version, account, scope: (
            "H-NEW" if account == {"source_id": BOOK} and scope == {"party": ["x"]} and version == 3 else "?"))
        self.assertTrue(module._automation_stale(frozen, context, loaded))
        row = Row(name="CWP-1", history="H1", status="pending")
        result = module._withdraw_automation(row, frozen, {"direction": "purchase", "items_account": "5310-01"}, None)
        self.assertEqual((result["leased"], result["reason"]), (False, "automation_changed"))
        self.assertEqual(saved, [("cancelled", "push.automation_changed")])
        self.assertEqual(withdrawn[0][:2], ("CWP-1", "automation_changed"))
        self.assertEqual(withdrawn[0][2]["values"], {"account": "5310-01"})
        self.assertEqual(withdrawn[0][2]["class"], "A")
        source = (APP / "push/native/service.py").read_text(encoding="utf-8")
        lease = source.split("def lease(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if _automation_stale(target, context, loaded):", lease)

    def test_the_frozen_memory_values_go_into_the_lease_recheck(self):
        """R1（10-18）：冻结的 ``overrule`` 交给 `current_hash`（领单时再判同账套有没有人刚改掉同一个记忆值）；没有的照旧。"""
        overrule = {"payment": {"value": "credit", "since": "2026-10-20T09:00:00"}}
        frozen = {"automation": {"class": "A", "tier": "L1", "trigger": "upload", "hash": "H-OLD",
                                 "scope": {"party": ["x"]}, "overrule": overrule}}
        context = ns(history_version=3, connection="C1", account=BOOK.lower())
        loaded = ns(evidence=ns(snapshot={"account_sets": [{"source_id": BOOK}]}))
        module, _saved, _withdrawn = self._ledger(lambda version, account, scope, overrule=None: (
            "H-OLD" if overrule is None else "H-NEW"))
        self.assertTrue(module._automation_stale(frozen, context, loaded))
        plain = {"automation": {**frozen["automation"]}}
        plain["automation"].pop("overrule")
        self.assertFalse(module._automation_stale(plain, context, loaded))
        source = (APP / "push/application/service.py").read_text(encoding="utf-8")
        self.assertIn('overrule = automation.get("overrule") or None', source)
        self.assertIn("account_row, subjects, overrule), subjects, overrule)", source)

    def test_an_automatic_push_leased_by_an_older_cc_desktop_becomes_a_proposal(self):
        # 审查处理 10-16：判定时写的电脑都够新，之后上线 / 配对的旧版也可能来领；领单时按来领的这台再核
        module, saved, withdrawn = self._ledger(lambda version, account, scope: "H")
        frozen = {"automation": {"class": "A", "tier": "L1", "trigger": "upload", "hash": "H"}}
        self.assertTrue(module._automation_old_writer(frozen, ns(version="1.7.3")))
        self.assertTrue(module._automation_old_writer(frozen, ns(version="")))
        self.assertFalse(module._automation_old_writer(frozen, ns(version="1.8.0")))
        self.assertFalse(module._automation_old_writer({}, ns(version="1.7.3")))  # 人发起的单不核
        row = Row(name="CWP-2", history="H2", status="pending")
        result = module._withdraw_automation(row, frozen, {"direction": "purchase"}, None, why="verify_version")
        self.assertEqual((result["leased"], saved[-1]), (False, ("cancelled", "push.automation_changed")))
        self.assertEqual(withdrawn[-1][:2], ("CWP-2", "verify_version"))
        source = (APP / "push/native/service.py").read_text(encoding="utf-8")
        lease = source.split("def lease(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("if _automation_old_writer(target, adapter):", lease)
        transport = (APP / "companion/transport.py").read_text(encoding="utf-8")
        self.assertIn("['device_owner', 'companion_version']", transport)
        self.assertIn("route=decision.route, version=version)", transport)

    def test_automatic_pushes_never_ask_about_a_busy_account_set(self):
        peer = route_contracts.Peer(users=frozenset({"u@example.com"}))
        job = route_contracts.Job("CWP-1", BOOK, "u@example.com")
        self.assertTrue(route_contracts.attended(job, peer))
        self.assertFalse(route_contracts.attended(job._replace(automated=True), peer))
        self.assertFalse(route_contracts.attended(job._replace(route_device="PC-READ"), peer))
        transport = (APP / "companion/transport.py").read_text(encoding="utf-8")
        self.assertIn("attended = route_contracts.attended(job._replace(automated=automated)", transport)


# ── R1 的改值记录（`push/choices.py`，10-18） ──────────────────────────────────


def _choice_store(existing=None, found=()):
    """`push/choices.py` 配内存替身的 Frappe：``existing`` 是已记过的选择 {name: 行}，``found`` 是改值查询 / 删记忆查询回的行。"""
    rows, inserted, queries, deleted = dict(existing or {}), [], [], []
    frappe = types.ModuleType("frappe")
    frappe._ = lambda text: text
    frappe.PermissionError = Denied
    frappe.session = ns(user="acc@example.com")

    def throw(message, error=Invalid):
        raise error(message)
    frappe.throw = throw

    class Doc(Row):
        def save(self, ignore_permissions=False):
            rows[self.name] = self

        def insert(self, ignore_permissions=False):
            inserted.append(dict(self))
            if self.get("input_key"):
                self.name = self.input_key
                rows[self.name] = self

    def get_doc(value, name=None, for_update=False):
        if isinstance(value, dict):
            doc = Doc(value)
        else:
            doc = rows[name] = Doc(rows[name])
        doc.flags = ns()
        return doc
    frappe.get_doc = get_doc

    def sql(query, values=(), as_dict=False, pluck=False):
        queries.append((query, values))
        return [Row(row) for row in found] if query.lstrip().startswith("SELECT") else []
    frappe.db = ns(exists=lambda doctype, name: name in rows, sql=sql,
                   delete=lambda doctype, filters: deleted.append((doctype, filters)))
    utils = types.ModuleType("frappe.utils")
    now = dt.datetime(2026, 10, 25, 9, 30)
    utils.now_datetime = lambda: now
    frappe.utils = utils
    module = _load("da_tiers_push_choices", APP / "push/choices.py", {"frappe": frappe, "frappe.utils": utils})
    return module, ns(rows=rows, inserted=inserted, queries=queries, deleted=deleted, now=now, Doc=Doc)


class ChoiceChanges(unittest.TestCase):
    SUBJECT = "purchase|" + SELLER

    def _existing(self, module, kind, value):
        name = module._key("C1", BOOK.lower(), kind, self.SUBJECT)
        return {name: Row(name=name, input_key=name, connection="C1", account_set=BOOK.lower(), kind=kind,
                          subject=self.SUBJECT, value=value, record_revision=1)}

    def test_a_remembered_account_or_payment_changed_to_another_value_is_logged(self):
        """`record`：记过的科目 / 现 / 赊被人改成别的值，另记一行「账套、类、归类键、旧值 → 新值、谁、何时」（来源 choice），顺手删
        这份账套 90 天前的；第一次记、同值、对方档 / 存货等别的类不记。"""
        probe, _state = _choice_store()
        module, state = _choice_store(self._existing(probe, "payment", "cash"))
        self.assertTrue(module.record("C1", BOOK, "payment", self.SUBJECT, "credit", "Makro"))
        change = [row for row in state.inserted if row["doctype"] == module.OVERRIDE_DOCTYPE]
        self.assertEqual(len(change), 1)
        self.assertEqual({key: change[0][key] for key in ("connection", "account_set", "kind", "subject", "old_value",
                                                           "new_value", "changed_by", "changed_at", "source")},
                         {"connection": "C1", "account_set": BOOK.lower(), "kind": "payment", "subject": self.SUBJECT,
                          "old_value": "cash", "new_value": "credit", "changed_by": "acc@example.com",
                          "changed_at": state.now, "source": "choice"})
        prune = [(query, values) for query, values in state.queries if query.startswith("DELETE")]
        self.assertEqual(len(prune), 1)
        self.assertIn("WHERE connection=%s AND account_set=%s AND changed_at < %s", prune[0][0])
        self.assertEqual(prune[0][1], ("C1", BOOK.lower(), state.now - dt.timedelta(days=module.OVERRIDE_KEEP_DAYS)))
        self.assertEqual(module.OVERRIDE_KEEP_DAYS, 90)
        # 同值不改、不记；第一次记不记
        module, state = _choice_store(self._existing(probe, "payment", "cash"))
        self.assertFalse(module.record("C1", BOOK, "payment", self.SUBJECT, "cash"))
        self.assertTrue(module.record("C1", BOOK, "account", self.SUBJECT, "5310-01"))
        self.assertEqual([row for row in state.inserted if row["doctype"] == module.OVERRIDE_DOCTYPE], [])
        # 别的类（对方档）改了也不记（R1 只管科目与现 / 赊）
        module, state = _choice_store(self._existing(probe, "party", "S1"))
        self.assertTrue(module.record("C1", BOOK, "party", self.SUBJECT, "S2"))
        self.assertEqual([row for row in state.inserted if row["doctype"] == module.OVERRIDE_DOCTYPE], [])

    def test_a_company_rule_replacing_a_memory_logs_the_change_and_a_plain_delete_does_not(self):
        found = [{"name": "N1", "account_set": BOOK.lower(), "subject": self.SUBJECT, "value": "5310-01"},
                 {"name": "N2", "account_set": "c:\\pearnlyexpress\\68acme", "subject": self.SUBJECT, "value": "5320-01"}]
        module, state = _choice_store(found=found)
        self.assertEqual(module.forget("C1", [BOOK], "account", [self.SUBJECT], replaced_by="5320-01"), 2)
        change = [row for row in state.inserted if row["doctype"] == module.OVERRIDE_DOCTYPE]
        self.assertEqual([(row["account_set"], row["old_value"], row["new_value"], row["source"]) for row in change],
                         [(BOOK.lower(), "5310-01", "5320-01", "rule")])  # 记的就是规则的值：不算改
        self.assertEqual(state.deleted, [(module.DOCTYPE, {"name": ["in", ["N1", "N2"]]})])
        module, state = _choice_store(found=found)
        self.assertEqual(module.forget("C1", [BOOK], "account", [self.SUBJECT]), 2)
        self.assertEqual(state.inserted, [])

    def test_the_changes_are_read_with_one_bounded_query(self):
        rows = [{"kind": "payment", "old_value": "cash", "new_value": "credit", "changed_at": dt.datetime(2026, 10, 20)}]
        module, state = _choice_store(found=rows)
        found = module.overrides("C1", BOOK, {"account": "5310-01", "payment": "cash", "party": "S1"})
        self.assertEqual(found, [{"kind": "payment", "old": "cash", "new": "credit", "at": dt.datetime(2026, 10, 20)}])
        [(query, values)] = state.queries
        self.assertIn("WHERE connection=%s AND kind IN %s AND changed_at >= %s AND account_set=%s AND old_value IN %s",
                      query)
        self.assertIn("ORDER BY changed_at DESC LIMIT %s", query)
        self.assertNotRegex(query, r"\{connection\}|\{kind\}|\{values?\}")
        self.assertEqual(values, ("C1", ("account", "payment"), state.now - dt.timedelta(days=auto.OVERRIDE_DAYS),
                                  BOOK.lower(), ("5310-01", "cash"), module.OVERRIDE_SCAN_MAX))
        self.assertEqual(module.OVERRIDE_SCAN_MAX, 200)
        module, state = _choice_store()
        self.assertEqual(module.overrides("C1", BOOK, {"party": "S1"}), [])
        self.assertEqual(module.overrides("", BOOK, {"account": "5310-01"}), [])
        self.assertEqual(state.queries, [])

    def test_the_change_table_is_guarded_and_indexed(self):
        folder = APP / "pearnly_cowork/doctype/cowork_express_choice_change"
        meta = json.loads((folder / "cowork_express_choice_change.json").read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in meta["fields"]}
        self.assertEqual(meta["name"], "Cowork Express Choice Change")
        self.assertTrue(all(field.get("read_only") for field in meta["fields"]))
        self.assertEqual(fields["kind"]["options"].split("\n"), list(auto.OVERRIDE_KINDS))
        self.assertEqual((fields["old_value"]["length"], fields["new_value"]["length"]), (60, 60))
        self.assertEqual(meta.get("modified"), "2026-10-18 12:00:00.000000")
        controller = (folder / "cowork_express_choice_change.py").read_text(encoding="utf-8")
        for hook in ("check_permission", "before_validate", "on_trash", "before_rename", "on_doctype_update"):
            self.assertIn(f"def {hook}", controller)
        self.assertIn('"connection_kind_changed_index": ["connection", "kind", "changed_at"]', controller)
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('"Cowork Express Choice Change": "pearnly_cowork.push.choices.deny_query"', hooks)
        self.assertIn('"Cowork Express Choice Change": "pearnly_cowork.push.choices.deny"', hooks)
        module, _state = _choice_store()
        with self.assertRaises(Denied):
            module.reject_share(ns(share_doctype="Cowork Express Choice Change"))
        with self.assertRaises(Denied):
            module.guard(ns(flags=ns(cowork_choice_write=None)))
        self.assertEqual(module.deny_query(), "1=0")


# ── 依赖方向 ──────────────────────────────────────────────────────────────────


class Boundaries(unittest.TestCase):
    def test_push_does_not_import_companies_folder_intake_or_readback(self):
        for path in [*(APP / "push/automation").glob("*.py"), APP / "push/application/assess.py"]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            for name in imported:
                self.assertFalse(name.startswith(("pearnly_cowork.companies", "pearnly_cowork.folder_intake",
                                                  "pearnly_cowork.readback")), (path.name, name))

    def test_pure_rules_do_not_touch_the_framework(self):
        for path in (APP / "push/automation/rules.py", APP / "companies/automation_rules.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            names |= {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
            self.assertFalse(any(name.split(".")[0] == "frappe" for name in names), path.name)

    def test_the_automation_table_is_guarded_and_has_one_row_per_company(self):
        meta = json.loads((APP / "pearnly_cowork/doctype/cowork_company_automation/cowork_company_automation.json")
                          .read_text(encoding="utf-8"))
        fields = {field["fieldname"]: field for field in meta["fields"]}
        self.assertEqual({name for name, field in fields.items() if field.get("unique")}, {"company"})
        self.assertEqual(fields["tier"]["options"].split("\n"), list(rules.TIERS))
        self.assertTrue(all(field.get("read_only") for field in meta["fields"]))
        history = json.loads((APP / "pearnly_cowork/doctype/cowork_ocr_history/cowork_ocr_history.json")
                             .read_text(encoding="utf-8"))
        added = {field["fieldname"]: field for field in history["fields"] if field["fieldname"].startswith("auto_")}
        self.assertEqual(set(added), {"auto_class", "auto_tier", "auto_reasons", "auto_proposal", "auto_checked_at"})
        self.assertEqual((added["auto_reasons"]["length"], added["auto_checked_at"].get("search_index")), (140, 1))
        record = json.loads((APP / "pearnly_cowork/doctype/cowork_push_record/cowork_push_record.json")
                            .read_text(encoding="utf-8"))
        self.assertTrue({"verify_state", "verify_json"} <= {field["fieldname"] for field in record["fields"]})
        source = (APP / "companies/service.py").read_text(encoding="utf-8")
        self.assertIn('frappe.db.delete(AUTOMATION, {"company": row.name})', source)


if __name__ == "__main__":
    unittest.main()
