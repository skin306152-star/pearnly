"""推送准备按推往的账套税号判进 / 销项（2026-10-15 testcowork 实测）。不连库、不依赖 Frappe。

事务所替客户记账：工作空间税号不是客户账套的税号。快递公司开给 ซินเซียร์ไอซ์ 的无买方简易税票（卖方 0105599220248，
285.00 / VAT 18.64）推往 T.9 账套：方向判不出，账套核对按卖方比账套，报「本方税号与所选账套不符」。
现在工作空间税号判不出时按账套税号判（`contracts.push.resolve_account_direction`），判出的方向交给 Express 预检。
票面本方税号、本方分店与所选账套不同不再拦（用户 2026-11-04：Express 不按它们拦，直写与代录同步）。
"""
from pathlib import Path
import copy
import dataclasses
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "tests"))
from test_cowork_push_fixed import Translated, _load, _service  # noqa: E402

amounts = _load("tax_amounts_direction", ROOT / "apps/pearnly_core/pearnly_core/tax_documents/amounts.py", {})
CORE = {"pearnly_core": types.ModuleType("pearnly_core"),
        "pearnly_core.tax_documents": types.SimpleNamespace(amounts=amounts),
        "pearnly_core.tax_documents.amounts": amounts}
push_contract = _load("push_contract_direction", Path(__file__).resolve().parents[1] / "apps/pearnly_core/pearnly_core/express_rules/contracts/push.py", CORE)
account_match = _load("account_match_direction", Path(__file__).resolve().parents[1] / "apps/pearnly_core/pearnly_core/express_rules/targets/account_match.py", CORE)

BOOK = r"c:\pearnlyexpress\70exp\t9sincer"
BOOK_TAX, FIRM, OTHER = "0105551234567", "0107536000188", "0105559999996"  # 账套 / 事务所工作空间 / 别家（校验位都对）
COURIER = "0105599220248"


@dataclasses.dataclass
class Target:
    account_set: str = BOOK


def courier(**fields):
    return {"document_type": "simplified_tax_invoice", "seller_name": "บริษัท ด่วนจี่ เอ็กซ์เพรส", "seller_tax": COURIER,
            "buyer_name": "", "buyer_tax": "", "total_amount": "285.00", "vat": "18.64", "date": "2026-10-14",
            **fields}


def operator_check(service, name):
    """代录预检 `assistant.precheck_source.operator_check`（写账前置判定）：判定模块按真文件加载，推送服务换成 ``service``（已接好替身），单据状态与待选账套
    走替身（没有结果不明、不在待选账套）。"""
    import importlib.util

    ns = types.SimpleNamespace
    policy = ns(SKIPPED_PREFIX="document_policy_skip_", document_class=lambda fields: "")
    frappe = ns(**{**vars(service.frappe), "_": lambda text: text})
    modules = {
        "frappe": frappe, "pearnly_cowork.access": ns(scope=service.scope),
        "pearnly_cowork.access.scope": service.scope,
        "pearnly_cowork.push.application": ns(service=service), "pearnly_cowork.push.application.service": service,
        "pearnly_cowork.push.application.provenance": ns(original_history=lambda d: None),
        "pearnly_cowork.push.lifecycle": ns(result_unknown=lambda *a: False),
        "pearnly_cowork.push": ns(lifecycle=ns(result_unknown=lambda *a: False)),
        "pearnly_cowork.history": ns(storage=service.storage), "pearnly_cowork.history.storage": service.storage,
        "pearnly_cowork.connections": ns(service=service.connections),
        "pearnly_cowork.connections.service": service.connections,
        "pearnly_cowork.contracts": ns(document_policy=policy), "pearnly_cowork.contracts.document_policy": policy,
        "pearnly_cowork.companies": ns(pending=ns(account_set_stop=lambda row: False)),
        "pearnly_cowork.push.express": ns(goods_posting=ns(blocked=lambda *a, **k: False)),
        "pearnly_cowork.push.express.goods_posting": ns(blocked=lambda *a, **k: False),
    }
    path = ROOT / "apps/pearnly_cowork/pearnly_cowork/assistant/precheck_source.py"
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location("precheck_under_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.document(name)["legacy"]


def wired(fields, *, book_tax=BOOK_TAX, own_tax=FIRM, direction=None, branches=None, company_tax=""):
    """推送服务按真实导入加载（`test_cowork_push_fixed._service`），方向与账套核对用真的纯规则，其余换成替身。
    ``branches``：同一税号各分店的账套 ``{账套键: 分店号}``（推往 BOOK）。``seen["hooks"]``：推送问过的钩子名。"""
    service, frappe, _calls = _service()
    seen = {}
    frappe.conf = {"pearnly_cowork_erp_push_enabled": True}
    frappe.db.get_value = lambda *a, **k: own_tax
    hooks = seen.setdefault("hooks", [])
    frappe.get_hooks = lambda name: hooks.append(name) or (["companies.rules.tax_for_push"]
                                                           if company_tax and name == "cowork_push_company_tax" else [])
    frappe.get_attr = lambda path: (lambda connection, row: seen.setdefault("company_tax_asked", (connection, row)) and company_tax)
    doc = types.SimpleNamespace(name="CWH-9151", workspace="W1", client="", record_version=3, pages="[]")
    period = {"period_start": "2025-11-01", "period_end": "2026-10-31", "next_end": "2027-10-31"}
    snapshot = {"account_sets": [{"source_id": key, "attributes": {"tax_id": book_tax, "branch": branch, **period}}
                                 for key, branch in (branches or {}).items()] or
                [{"source_id": BOOK, "attributes": {"tax_id": book_tax, **period}}]}
    loaded = types.SimpleNamespace(endpoint={"adapter": "express"}, revision=7, doc=types.SimpleNamespace(name="C1"),
                                   evidence=types.SimpleNamespace(snapshot=snapshot),
                                   access=types.SimpleNamespace(tenant_id="T1", user_id="u1"))
    history = {"pages": [{"fields": fields}], "workspace_client_id": "W1", **({"direction": direction} if direction else {})}

    def decisions(*args, **kwargs):
        seen["direction"] = args[4]
        return {"needed": [], "applied": {}}

    def prepare_express(endpoint, value, *, posting_kind, context):
        seen.update(history=value, context=context)
        return types.SimpleNamespace(payload={}, disposition="ready", reason=None,
                                     preflight=types.SimpleNamespace(checks_json=lambda: []))

    def collect(loaded, target, *, seller_tax, invoice_date):
        seen["seller_tax"] = seller_tax
        return {"reported_config": {}, "mappings": {}, "supplier_profiles": {}, "bank_index": ()}, {}

    service.scope.require = lambda *a: None
    service.storage.read = lambda *a, **k: doc
    service.storage.revision = int
    service.storage.project = lambda d: copy.deepcopy(history)
    service.connections.read_for_push = lambda *a, **k: loaded
    service.ledger.lookup_request = lambda *a: None
    service.ledger.read_success = lambda **k: None
    service.ledger.reserve = lambda **k: {"name": "R1", "status": "prepared"}
    service.preparation_sources.collect = collect
    service.freeze_target = lambda *a, **k: Target()
    service.resolve_account_choice = lambda *a, **k: None
    service.endpoint_with_account_choice = lambda endpoint, choice: {"config": {}}
    service.original_history = lambda d: None
    service.primary_page = lambda pages: pages[0]
    service.flatten_history_for_mrerp = lambda value: {**value, "fields": value["pages"][0]["fields"]}
    service.parse_invoice_date = lambda value: None
    service.clean_tax_id = push_contract.clean_tax_id
    service.explicit_direction = push_contract.explicit_direction
    service.resolve_account_direction = push_contract.resolve_account_direction
    service.account_match = account_match
    # 加载时账套核对是替身：拒绝文案表换成真规则里的那份（`account_match.CONFLICT_MESSAGES`）
    service._ACCOUNT_CONFLICTS = account_match.CONFLICT_MESSAGES
    service.ExpressContext = lambda **kwargs: kwargs
    service.prepare_express = prepare_express
    service._document_block = lambda d: None
    service._fixed = lambda *a: None
    service._apply_stock_group = lambda *a: None
    service._decisions = decisions
    return service, seen, (doc, loaded)


class PrepareDirection(unittest.TestCase):
    def prepare(self, fields, **kwargs):
        service, seen, _ = wired(fields, **kwargs)
        service.prepare("CWH-9151", "C1", "req-1", 3, 7, BOOK)
        return seen

    def test_buyerless_document_from_another_seller_goes_in_as_a_purchase(self):
        seen = self.prepare(courier())
        self.assertEqual(seen["direction"], "purchase")
        self.assertEqual(seen["seller_tax"], COURIER)  # 进项按卖方取对方档案
        self.assertEqual(seen["history"]["direction"], "purchase")  # 预检看到明确方向
        self.assertEqual(seen["context"]["account_tax_id"], BOOK_TAX)

    def test_own_abbreviated_sales_invoice_is_a_sale(self):
        seen = self.prepare(courier(seller_name="ซินเซียร์ไอซ์", seller_tax=BOOK_TAX))
        self.assertEqual((seen["direction"], seen["history"]["direction"], seen["seller_tax"]), ("sales", "sales", ""))

    def test_a_document_naming_another_buyer_goes_into_the_chosen_set(self):
        """直写（用户 2026-11-04）：票面买方是别家（本方税号与所选账套不同）照常准备推往所选账套，不拦。"""
        seen = self.prepare(courier(buyer_name="Other Co", buyer_tax=OTHER), direction="purchase")
        self.assertEqual((seen["direction"], seen["history"]["direction"]), ("purchase", "purchase"))
        self.assertEqual(seen["context"]["account_tax_id"], BOOK_TAX)
        self.assertEqual(seen["seller_tax"], COURIER)
        # 销项：票面卖方是别家，同样照推
        seen = self.prepare(courier(seller_tax=OTHER, buyer_tax=BOOK_TAX), direction="sales")
        self.assertEqual(seen["direction"], "sales")
        # 没写明方向、两边税号都对不上：不拦，方向判不出照旧交给预检停下问进项还是销项（同 Express 自己要选单种）
        seen = self.prepare(courier(buyer_name="Other Co", buyer_tax=OTHER))
        self.assertIsNone(seen["direction"])
        self.assertNotIn("direction", seen["history"])

    def test_the_operator_precheck_lets_another_buyer_through(self):
        """代录开始前检查（`operator_check`，用户 2026-11-04 BJC 实测 22 张全被拦）：买方是别家照常放行。"""
        def check(**fields):
            service, _seen, (doc, _loaded) = wired(courier(buyer_name="Other Co", buyer_tax=OTHER, **fields),
                                                   direction="purchase")
            doc.push_connection, doc.push_account_set = "C1", BOOK
            doc.last_push_status = doc.last_push_reason = doc.last_push_record = ""
            service.frappe.db.rollback = lambda: None
            # 代录预检转调写账前置判定（`assistant.precheck_source.document`）：判定模块按真的加载，取数仍是这份推送服务
            service._choices = lambda *a, **k: ({}, {"needed": [], "applied": {}}, (None, "", None))
            return operator_check(service, "CWH-9151")

        ready = check()
        self.assertEqual(ready["state"], "ready")
        self.assertEqual(ready["target"]["account_set"], BOOK)  # 写进的就是所选账套
        # 同一条路上的期间核对照旧在（Express 自己也拒）：日期晚于账套次年段的停下
        stopped = check(date="2028-01-02")
        self.assertEqual(stopped["state"], "blocked")
        self.assertIn("fiscal period", stopped["message"])

    def test_an_explicit_direction_is_kept_as_it_is(self):
        seen = self.prepare(courier(seller_tax=BOOK_TAX), direction="expense")
        self.assertEqual((seen["direction"], seen["history"]["direction"]), ("purchase", "expense"))

    def test_an_account_set_without_a_tax_id_leaves_the_direction_undecided(self):
        seen = self.prepare(courier(), book_tax="")
        self.assertIsNone(seen["direction"])  # 预检照旧停在「方向判不出」，不猜
        self.assertNotIn("direction", seen["history"])
        self.assertEqual(seen["context"]["account_tax_id"], "")

    def test_without_a_book_tax_id_the_tax_id_remembered_on_the_company_decides(self):
        """A 线 11-10：工作空间、账套目录都没税号时，用这份账套所属公司记下的税号（CC 卡片问过一次「本公司是票上哪一方」）。"""
        seen = self.prepare(courier(buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX), book_tax="", own_tax="",
                            company_tax=BOOK_TAX)
        self.assertEqual(seen["direction"], "purchase")
        self.assertEqual(seen["context"]["account_tax_id"], BOOK_TAX)
        self.assertEqual(seen["company_tax_asked"][0], "C1")
        sale = self.prepare(courier(seller_tax=BOOK_TAX), book_tax="", own_tax="", company_tax=BOOK_TAX)
        self.assertEqual(sale["direction"], "sales")
        # 目录报了税号就用目录的，不问公司
        listed = self.prepare(courier(buyer_tax=BOOK_TAX), company_tax=OTHER)
        self.assertNotIn("company_tax_asked", listed)

    def test_a_misread_own_seller_tax_id_is_not_taken_for_another_company(self):
        """审查 2026-10-15：自开的无买方简易税票卖方税号读错一位，不能当别家开来的票按进项记。"""
        bad_check = "0105551234568"  # 账套 0105551234567 末位读错：校验位不对
        one_off = "0115551234567"  # 第 3 位读错：校验位恰好仍对，只差一位
        two_off = "0105551234664"  # 差两位、校验位对
        self.assertFalse(amounts.valid_tax_id(bad_check))
        self.assertTrue(amounts.valid_tax_id(one_off) and amounts.valid_tax_id(two_off))
        for seller in (bad_check, one_off, two_off):
            with self.subTest(seller=seller):
                self.assertIsNone(push_contract.detect_by_account({"fields": courier(seller_tax=seller)}, BOOK_TAX))
                seen = self.prepare(courier(seller_tax=seller))
                self.assertIsNone(seen["direction"])  # 判不出，交人选
                self.assertNotIn("direction", seen["history"])

    def test_a_real_third_party_seller_is_still_a_purchase(self):
        for seller in (COURIER, OTHER):
            with self.subTest(seller=seller):
                self.assertEqual(push_contract.detect_by_account({"fields": courier(seller_tax=seller)}, BOOK_TAX),
                                 "purchase")

    def test_the_review_choices_use_the_same_direction(self):
        service, _seen, (doc, loaded) = wired(courier())
        self.assertEqual(service._express_facts(doc, loaded, BOOK)[3], "purchase")


class BranchNotChecked(unittest.TestCase):
    """补充 甲.3 的分店硬闸已取消（用户 2026-11-04：Express 单据上没有本方分店可比，不拦；直写与代录同步）：同一法人有几个
    分店的账套时，票面本方分店与所选账套不同也照推；CC 对话指定的分店不再取（钩子 ``cowork_push_work_branch`` 已删）。"""
    TWO = {BOOK: "00000", r"c:\pearnlyexpress\70exp\t9b12": "00012"}

    def prepare(self, fields, **kwargs):
        service, seen, _ = wired(fields, **kwargs)
        service.prepare("CWH-9151", "C1", "req-1", 3, 7, BOOK)
        return seen

    def bought(self, **fields):
        return courier(buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX, **fields)

    def test_another_branch_printed_on_the_document_is_pushed_to_the_chosen_set(self):
        for branch in ("00012", "สาขาที่ 12", "00000", "สำนักงานใหญ่", ""):
            with self.subTest(branch=branch):
                seen = self.prepare(self.bought(buyer_branch=branch), branches=self.TWO)
                self.assertEqual(seen["direction"], "purchase")
                self.assertNotIn("cowork_push_work_branch", seen["hooks"])

    def test_the_rule_itself_no_longer_looks_at_branches(self):
        head = {"source_id": BOOK, "attributes": {"tax_id": BOOK_TAX, "branch": "00000"}}
        conflict = account_match.conflict
        self.assertEqual(conflict("express", head, fields={"seller_tax": BOOK_TAX, "seller_branch": "12"}), "")
        self.assertEqual(conflict("express", head, fields={"buyer_tax": BOOK_TAX, "buyer_branch": "00012"}), "")
        self.assertFalse(hasattr(account_match, "BRANCH_MISMATCH"))
        self.assertFalse(hasattr(account_match, "TAX_MISMATCH"))

    def test_another_own_tax_id_is_not_a_conflict(self):
        """本方税号（进项看买方、销项看卖方）是别家的有效税号：账套核对不再回原因码，期间核对照旧。"""
        book = {"source_id": BOOK, "attributes": {"tax_id": BOOK_TAX, "period_start": "2026-01-01",
                                                  "period_end": "2026-12-31"}}
        conflict = account_match.conflict
        self.assertEqual(conflict("express", book, fields={"buyer_tax": OTHER, "date": "2026-03-01"}), "")
        self.assertEqual(conflict("express", book, fields={"seller_tax": OTHER, "buyer_tax": COURIER,
                                                          "date": "2026-03-01"}), "")
        self.assertEqual(conflict("express", book, fields={"buyer_tax": OTHER, "date": "2027-03-01"}),
                         account_match.PERIOD_MISMATCH)


class NextYearPeriod(unittest.TestCase):
    """新年度账套还没建时，下一年的单记进旧账套的次年段（认 ``next_end``）；按日期选账套（T1、自动推，`account_match.for_day`）
    新年度账套一建好就选新账套（用户 2026-10-16）。人选了旧账套推次年段的单不拦（用户 2026-11-04：Express 的期间窗含次年段，照收）。"""

    @staticmethod
    def book(key, start, end, next_end="", tax=BOOK_TAX, branch="00000", company="ACME"):
        return {"source_id": key, "attributes": {"tax_id": tax, "branch": branch, "company": company,
                                                 "period_start": start, "period_end": end, "next_end": next_end}}

    def test_the_part_of_the_fiscal_period(self):
        part = account_match.fiscal_part
        self.assertEqual(part("2025-01-01", "2025-12-31", "2026-12-31", "2025-06-01"), account_match.CURRENT)
        self.assertEqual(part("2025-01-01", "2025-12-31", "2026-12-31", "2026-06-01"), account_match.NEXT)
        self.assertEqual(part("2025-01-01", "2025-12-31", "2026-12-31", "2027-01-01"), "")
        self.assertEqual(part("2025-01-01", "2025-12-31", "", "2026-01-01"), "")
        self.assertEqual(part("", "", "2026-12-31", "2026-01-01"), "")

    def test_three_cross_year_cases_pick_the_book(self):
        span = lambda row: (row["attributes"]["period_start"], row["attributes"]["period_end"],  # noqa: E731
                            row["attributes"]["next_end"])
        old = self.book("t8", "2025-01-01", "2025-12-31", "2026-12-31")
        new = self.book("y26", "2026-01-01", "2026-12-31", "2027-12-31")
        twin = self.book("t9", "2025-01-01", "2025-12-31", "2026-12-31")
        keys = lambda found: [row["source_id"] for row in found]  # noqa: E731
        self.assertEqual(keys(account_match.for_day([old, new], "2026-03-01", span)), ["y26"])   # 新账套已建
        self.assertEqual(keys(account_match.for_day([old], "2026-03-01", span)), ["t8"])         # 未建：旧账套次年段
        self.assertEqual(keys(account_match.for_day([old, twin], "2026-03-01", span)), ["t8", "t9"])  # 两份同期：调用方停下
        self.assertEqual(keys(account_match.for_day([old, new], "2025-03-01", span)), ["t8"])
        self.assertEqual(account_match.for_day([old], "2027-03-01", span), [])
        # 「新年度账套还没建」核不了就停下（审查处理 10-16）：同组里有会计期间没报全的账套（ISPRD 读不到，可能正是新的那份）、
        # 或本年起始晚于旧账套本年止的账套
        bare = self.book("y26", "", "", "")
        later = self.book("y27", "2027-01-01", "2027-12-31", "2028-12-31")
        older = self.book("t7", "2024-01-01", "2024-12-31", "2025-12-31")
        self.assertEqual(account_match.for_day([old, bare], "2026-03-01", span), [])
        self.assertEqual(account_match.for_day([old, later], "2026-03-01", span), [])
        self.assertEqual(keys(account_match.for_day([old, older], "2026-03-01", span)), ["t8"])  # 更早的年度不挡
        self.assertEqual(keys(account_match.for_day([old, bare], "2025-03-01", span)), ["t8"])   # 本年覆盖照旧

    def test_by_month_the_same_rule(self):
        """按月取账套（月结台账、主档页、档位统计）：本年期间与这个月有重叠的；没有就用次年段含这个月的旧账套，同按日期的规则。"""
        span = lambda row: (row["attributes"]["period_start"], row["attributes"]["period_end"],  # noqa: E731
                            row["attributes"]["next_end"])
        keys = lambda found: [row["source_id"] for row in found]  # noqa: E731
        old = self.book("t8", "2025-01-01", "2025-12-31", "2026-12-31")
        new = self.book("y26", "2026-01-01", "2026-12-31", "2027-12-31")
        month = account_match.for_month
        self.assertEqual(keys(month([old, new], "2026-01-01", "2026-01-31", span)), ["y26"])
        self.assertEqual(keys(month([old], "2026-01-01", "2026-01-31", span)), ["t8"])
        self.assertEqual(keys(month([old], "2025-12-01", "2025-12-31", span)), ["t8"])
        self.assertEqual(month([old], "2027-01-01", "2027-01-31", span), [])
        self.assertEqual(month([old, self.book("y26", "", "", "")], "2026-01-01", "2026-01-31", span), [])
        self.assertEqual(month([old], "", "2026-01-31", span), [])

    def test_the_hard_gate_only_stops_dates_outside_the_whole_fiscal_window(self):
        conflict = account_match.conflict
        old = self.book("t8", "2025-01-01", "2025-12-31", "2026-12-31")
        fields = {"buyer_tax": BOOK_TAX, "date": "2026-03-01"}
        # 次年段：新年度账套建没建都照推（Express 的期间窗含次年段）
        self.assertEqual(conflict("express", old, fields=fields), "")
        self.assertFalse(hasattr(account_match, "newer_book_covers"))
        self.assertEqual(conflict("express", old, fields={"date": "2025-06-01"}), "")
        # 晚于次年段、早于本年：照旧拦（Express Message#. 70）
        self.assertEqual(conflict("express", old, fields={**fields, "date": "2027-01-02"}), account_match.PERIOD_MISMATCH)
        self.assertEqual(conflict("express", old, fields={**fields, "date": "2024-12-31"}), account_match.PERIOD_MISMATCH)
        # 没报次年段：只认本年
        bare = self.book("n8", "2025-01-01", "2025-12-31")
        self.assertEqual(conflict("express", bare, fields={"date": "2026-03-01"}), account_match.PERIOD_MISMATCH)
        # 读不出日期、账套没报期间：不拦
        self.assertEqual(conflict("express", old, fields={"buyer_tax": BOOK_TAX}), "")
        self.assertEqual(conflict("express", self.book("x", "", ""), fields=fields), "")
        self.assertEqual(conflict("mrerp", old, fields={**fields, "date": "2030-01-01"}), "")


class FrozenTarget(unittest.TestCase):
    """冻结目标只建一次（并入 CC 外壳分支时冲突第 2 处，合并审查第 1 条）：``decided_by``（F19 的 written 读它）与准备证据
    v2 的 ``basis_hash``、写者锁 v2 并存；「仍然推送」写进载荷在算 ``basis_hash`` 之前；``decided_by`` 不进载荷。10-17 再并入
    外壳：用上的公司规则 ``company_rules`` 同样只进这一份冻结目标。"""

    def prepare(self, decided_by=None, confirm_duplicate=False, company_rules=None):
        service, _seen, _ = wired(courier(buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX))
        reserved, hashed = [], []
        decision = {"needed": [], "applied": {}, **({"decided_by": decided_by} if decided_by else {}),
                    **({"company_rules": company_rules} if company_rules else {})}
        service._decisions = lambda *a, **k: copy.deepcopy(decision)
        service.preparation_sources.collect = lambda *a, **k: (
            {"reported_config": {}, "mappings": {}, "supplier_profiles": {}, "bank_index": ()}, {"version": 2})
        service.prepare_express = lambda endpoint, value, *, posting_kind, context: types.SimpleNamespace(
            payload={"doctype": "RR"}, disposition="ready", reason=None,
            preflight=types.SimpleNamespace(checks_json=lambda: []))
        service.ledger.basis_hash = lambda facts, payload: hashed.append(copy.deepcopy(payload)) or "H-1"
        service.ledger.writer_scope = lambda connection, key: {"lock_scheme": "v2", "company_key": f"tax:{BOOK_TAX}"}
        service.ledger.reserve = lambda **kwargs: reserved.append(kwargs) or {"name": "R1", "status": "prepared"}
        service.prepare("CWH-9151", "C1", "req-1", 3, 7, BOOK, confirm_duplicate=confirm_duplicate)
        self.assertEqual(len(reserved), 1)
        return reserved[0], hashed

    def test_decided_by_sits_beside_the_v2_evidence_and_the_writer_lock(self):
        reserved, hashed = self.prepare({"account": "company_rule", "payment": "remembered"}, confirm_duplicate=True)
        frozen = reserved["frozen_target"]
        self.assertEqual(frozen["decided_by"], {"account": "company_rule", "payment": "remembered"})
        self.assertEqual(frozen["preparation_evidence"], {"version": 2, "basis_hash": "H-1"})
        self.assertEqual((frozen["lock_scheme"], frozen["company_key"]), ("v2", f"tax:{BOOK_TAX}"))
        self.assertEqual((frozen["account_set"], frozen["request_intent"]["confirm_duplicate"]), (BOOK, True))
        self.assertEqual(hashed, [{"doctype": "RR", "duplicate_confirmed": True}])  # 先写「仍然推送」再算
        self.assertEqual(reserved["frozen_payload"], {"doctype": "RR", "duplicate_confirmed": True})
        self.assertEqual(reserved["disposition"], "prepared")

    def test_without_decided_by_the_frozen_target_is_unchanged(self):
        reserved, hashed = self.prepare()
        frozen = reserved["frozen_target"]
        self.assertNotIn("decided_by", frozen)
        self.assertNotIn("company_rules", frozen)
        self.assertEqual((frozen["preparation_evidence"]["basis_hash"], frozen["lock_scheme"]), ("H-1", "v2"))
        self.assertEqual(hashed, [{"doctype": "RR"}])

    def test_company_rules_used_sit_in_the_same_single_frozen_target(self):
        # 并入外壳（10-17）冲突第 1 处：外壳分支在载荷之前另建的那份 frozen 又多了 company_rules（用上的是哪几条公司规则，
        # F19 的 written.company_rules 读它）；只留本分支那一份，company_rules 加在它上面，不进载荷、不改 basis_hash
        rules = [{"id": "CCH-1", "kind": "account", "subject": f"purchase|{BOOK_TAX}|", "label": "ซินเซียร์ไอซ์",
                  "value": "53-05-28-00", "value_label": "", "branch": ""}]
        reserved, hashed = self.prepare({"account": "company_rule"}, company_rules=rules)
        frozen = reserved["frozen_target"]
        self.assertEqual(frozen["company_rules"], rules)
        self.assertEqual(frozen["decided_by"], {"account": "company_rule"})
        self.assertEqual((frozen["preparation_evidence"]["basis_hash"], frozen["lock_scheme"]), ("H-1", "v2"))
        self.assertEqual(hashed, [{"doctype": "RR"}])
        self.assertEqual(reserved["frozen_payload"], {"doctype": "RR"})


# MR.ERP 的分流与方向闸（共用层 `pearnly_core.mrerp.push.routing`）：方向判定与方向闸是真规则，货 / 费判据、付款判据、单据防呆换成替身
# （这里只核方向）。货 / 费替身：复核屏点过「费用」才是费用。
_ROUTING_STUBS = {
    **CORE,
    "pearnly_core.express_rules.contracts.push": push_contract,
    "pearnly_core.express_rules.push.doc_sanity": types.SimpleNamespace(check_document=lambda *a, **k: None),
    "pearnly_core.express_rules.push.field_rules": types.SimpleNamespace(
        item_verdict=lambda fields, today: (fields.get("posting_item_type_manual") == "expense", "stub")),
    "pearnly_core.express_rules.push.payment": types.SimpleNamespace(
        payment_verdict_for=lambda flat, fields, mappings, direction: (False, None)),
    "pearnly_core.mrerp.push.models": types.SimpleNamespace(FailedRow=object, ImportResult=object),
    "pearnly_core.mrerp.push.modules": types.SimpleNamespace(get_module=lambda doc_type: None),
    "pearnly_core.mrerp.push.xlsx": types.SimpleNamespace(generator=types.SimpleNamespace()),
}
routing = _load("mrerp_routing_direction", APP.parents[1] / "pearnly_core/pearnly_core/mrerp/push/routing.py",
                _ROUTING_STUBS)


def mrerp_flat(direction=None, **fields):
    """推往 MR.ERP 的一张票（`flatten_history_for_mrerp` 的形状：票面在 ``fields``，声明的方向在顶层）。"""
    return {"id": "CWH-9152", **({"direction": direction} if direction else {}), "fields": dict(fields)}


class MrErpDirection(unittest.TestCase):
    """MR.ERP 按推往的这家公司在 MR.ERP 登记的税号判进 / 销项（T2，2026-10-19，同 Express 2b825a00）。

    事务所替客户记账：工作空间税号（FIRM）不是票上的买卖方；推往的公司（BOOK_TAX）才是。以前只按工作空间税号判，
    判不出就按默认赊销、方向闸（原「匹配闸」，11-04 改名）停下。方向闸只管「进项还是销项判不出」，不看票是不是这家公司的。"""

    def route(self, flat, account=BOOK_TAX, own=FIRM):
        return routing.choose_doc_type(flat, flat, own_tax_id=own, account_tax_id=account, mappings={},
                                       today=None)

    def gate(self, flat, expected, account=BOOK_TAX, own=FIRM):
        return routing.direction_unsettled(flat, flat, own_tax_id=own, expected_direction=expected,
                                           account_tax_id=account)

    def test_the_company_as_buyer_is_a_purchase(self):
        flat = mrerp_flat(seller_tax=OTHER, buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX)
        self.assertEqual(self.route(flat), "purchase")
        self.assertEqual(self.route(mrerp_flat(**flat["fields"], posting_item_type_manual="expense")),
                         "purchase_expense")
        self.assertFalse(self.gate(flat, "purchase"))

    def test_the_company_as_seller_is_a_sale(self):
        flat = mrerp_flat(seller_tax=BOOK_TAX, buyer_name="ลูกค้า", buyer_tax=OTHER)
        self.assertEqual(self.route(flat), "sales_credit")
        self.assertFalse(self.gate(flat, "sales"))

    def test_bookkeeping_for_a_client_no_longer_hits_the_gate(self):
        # 工作空间是事务所：票上买方是客户公司、或是快递公司开给客户的无买方简易税票
        for flat in (mrerp_flat(seller_tax=OTHER, buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX),
                     mrerp_flat(**courier())):
            with self.subTest(flat=flat["fields"].get("seller_tax")):
                # 以前（只有工作空间税号）：判不出 → 默认赊销模块，匹配闸拦下
                self.assertIsNone(self.route(flat, account=""))
                self.assertTrue(self.gate(flat, "sales", account=""))
                # 现在：按这家公司的税号判出进项，进项模块的匹配闸放行
                self.assertEqual(self.route(flat), "purchase")
                self.assertFalse(self.gate(flat, "purchase"))
        sale = mrerp_flat(seller_tax=BOOK_TAX, buyer_name="ลูกค้า", buyer_tax=OTHER)
        self.assertEqual(self.route(sale), "sales_credit")
        self.assertFalse(self.gate(sale, "sales"))

    def test_without_a_company_tax_id_the_workspace_tax_id_decides_as_before(self):
        flat = mrerp_flat(seller_tax=OTHER, buyer_tax=BOOK_TAX)
        # MR.ERP 没登记、登记的不是有效税号（样板库 8-888-888-88888-8、分店填的 1）→ 不当锚点
        for raw in ("", None, "8-888-888-88888-8", "1"):
            self.assertEqual(routing.account_tax(raw), "")
        self.assertEqual(routing.account_tax("0-1055-51234-56-7"), BOOK_TAX)
        self.assertIsNone(self.route(flat, account=""))
        self.assertTrue(self.gate(flat, "sales", account=""))
        # 工作空间就是这家公司（自己记账）：照旧按工作空间税号判
        self.assertEqual(self.route(flat, account="", own=BOOK_TAX), "purchase")
        self.assertFalse(self.gate(flat, "purchase", account="", own=BOOK_TAX))
        # 两个税号都没有：不判、不拦（同旧版）
        self.assertFalse(self.gate(flat, "sales", account="", own=""))

    def test_a_seller_tax_id_close_to_the_company_is_not_taken_for_another_seller(self):
        # 客户自开的无买方简易税票，卖方税号读错一位：不当成别家开来的进项（同 Express `_other_seller`）
        flat = mrerp_flat(**courier(seller_tax="0105551234568"))
        self.assertIsNone(self.route(flat))
        self.assertTrue(self.gate(flat, "sales"))  # 判不出就不按默认赊销推，转人工（同旧版）

    def test_an_explicit_direction_is_kept(self):
        flat = mrerp_flat("sales", seller_tax=OTHER, buyer_tax=BOOK_TAX)
        self.assertEqual(self.route(flat), "sales_credit")
        self.assertFalse(self.gate(flat, "sales"))

    def test_another_companys_document_with_a_known_direction_goes_in(self):
        """用户 2026-11-04（Express 不拦、MR.ERP 不拦，我们就不拦）：买卖方都不是这家公司的票，写明了进项 / 销项就照常写。"""
        purchase = mrerp_flat("purchase", seller_tax=OTHER, buyer_name="บริษัทอื่น", buyer_tax=COURIER)
        self.assertEqual(self.route(purchase), "purchase")
        self.assertFalse(self.gate(purchase, "purchase"))
        sale = mrerp_flat("sales", seller_tax=COURIER, buyer_name="บริษัทอื่น", buyer_tax=OTHER)
        self.assertEqual(self.route(sale), "sales_credit")
        self.assertFalse(self.gate(sale, "sales"))

    def test_an_undecidable_direction_still_stops_to_ask(self):
        # 没写方向、两边税号都不是这家：判不出进项还是销项，停下问一次（不落进默认赊销模块）
        flat = mrerp_flat(seller_tax=OTHER, buyer_name="บริษัทอื่น", buyer_tax=COURIER)
        self.assertIsNone(self.route(flat))
        self.assertTrue(self.gate(flat, "sales"))
        self.assertTrue(self.gate(flat, "purchase"))
        # 买卖方同一个税号（原单据防呆 seller_buyer_same_tax，11-04 取消）：方向判不出，同样停下
        same = mrerp_flat(seller_tax=BOOK_TAX, buyer_tax=BOOK_TAX)
        self.assertIsNone(self.route(same))
        self.assertTrue(self.gate(same, "sales"))
        self.assertFalse(self.gate(mrerp_flat("purchase", seller_tax=BOOK_TAX, buyer_tax=BOOK_TAX), "purchase"))
        # 零售小票一个税号都没读到：照旧走默认模块
        self.assertFalse(self.gate(mrerp_flat(seller_name="ร้าน"), "sales"))
        # 方向判出来了、却被推进相反的模块：照旧停
        self.assertTrue(self.gate(mrerp_flat(seller_tax=OTHER, buyer_tax=BOOK_TAX), "sales"))


if __name__ == "__main__":
    unittest.main()
