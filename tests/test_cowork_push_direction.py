"""推送准备按推往的账套税号判进 / 销项（2026-10-15 testcowork 实测）。不连库、不依赖 Frappe。

事务所替客户记账：工作空间税号不是客户账套的税号。快递公司开给 ซินเซียร์ไอซ์ 的无买方简易税票（卖方 0105599220248，
285.00 / VAT 18.64）推往 T.9 账套：方向判不出，账套核对按卖方比账套，报「本方税号与所选账套不符」。
现在工作空间税号判不出时按账套税号判（`contracts.push.resolve_account_direction`），判出的方向交给 Express 预检；
没有方向时账套核对不拿卖方当本方（`targets.account_match`）。买方是别家的票照旧拦。
"""
import copy
import dataclasses
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "tests"))
from test_cowork_push_fixed import Translated, _load, _service  # noqa: E402

amounts = _load("tax_amounts_direction", ROOT / "apps/pearnly_core/pearnly_core/tax_documents/amounts.py", {})
CORE = {"pearnly_core": types.ModuleType("pearnly_core"),
        "pearnly_core.tax_documents": types.SimpleNamespace(amounts=amounts),
        "pearnly_core.tax_documents.amounts": amounts}
push_contract = _load("push_contract_direction", APP / "contracts/push.py", CORE)
account_match = _load("account_match_direction", APP / "targets/account_match.py", CORE)

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


def wired(fields, *, book_tax=BOOK_TAX, own_tax=FIRM, direction=None, branches=None, work_branch=""):
    """推送服务按真实导入加载（`test_cowork_push_fixed._service`），方向与账套核对用真的纯规则，其余换成替身。
    ``branches``：同一税号各分店的账套 ``{账套键: 分店号}``（推往 BOOK）；``work_branch``：CC 对话指定的分店（钩子替身）。"""
    service, frappe, _calls = _service()
    seen = {}
    frappe.conf = {"pearnly_cowork_erp_push_enabled": True}
    frappe.db.get_value = lambda *a, **k: own_tax
    asked = seen.setdefault("work_branch_asked", [])
    frappe.get_hooks = lambda name: ["p.branch"] if name == "cowork_push_work_branch" else []
    frappe.get_attr = lambda path: lambda connection, account_set: asked.append((connection, account_set)) or work_branch
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
    # 加载时账套核对是替身：拒绝文案表的键换成真规则的原因码（表里依次是税号不符、期间不符）
    service._ACCOUNT_CONFLICTS = dict(zip((account_match.TAX_MISMATCH, account_match.PERIOD_MISMATCH,
                                           account_match.BRANCH_MISMATCH), service._ACCOUNT_CONFLICTS.values()))
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

    def test_a_document_naming_another_buyer_is_still_refused(self):
        with self.assertRaisesRegex(Translated, "own tax ID"):
            self.prepare(courier(buyer_name="Other Co", buyer_tax=OTHER))
        with self.assertRaisesRegex(Translated, "own tax ID"):
            self.prepare(courier(buyer_name="Other Co", buyer_tax=OTHER), direction="purchase")

    def test_an_explicit_direction_is_kept_as_it_is(self):
        seen = self.prepare(courier(seller_tax=BOOK_TAX), direction="expense")
        self.assertEqual((seen["direction"], seen["history"]["direction"]), ("purchase", "expense"))

    def test_an_account_set_without_a_tax_id_leaves_the_direction_undecided(self):
        seen = self.prepare(courier(), book_tax="")
        self.assertIsNone(seen["direction"])  # 预检照旧停在「方向判不出」，不猜
        self.assertNotIn("direction", seen["history"])
        self.assertEqual(seen["context"]["account_tax_id"], "")

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


class BranchRouting(unittest.TestCase):
    """补充 甲.3（用户 2026-10-15）：同一法人有几个分店的账套时，进项票票面的买方分店只认这个分店的账套；票面没有分店
    用 CC 对话的分店；与所选账套分店不符就拦（照现有账套核对的硬闸），不静默改。只有一个分店的不按分店拦。"""
    TWO = {BOOK: "00000", r"c:\pearnlyexpress\70exp\t9b12": "00012"}

    def prepare(self, fields, **kwargs):
        service, seen, _ = wired(fields, **kwargs)
        service.prepare("CWH-9151", "C1", "req-1", 3, 7, BOOK)
        return seen

    def bought(self, **fields):
        return courier(buyer_name="ซินเซียร์ไอซ์", buyer_tax=BOOK_TAX, **fields)

    def test_the_buyer_branch_printed_on_the_document_must_be_the_chosen_sets_branch(self):
        with self.assertRaisesRegex(Translated, "another branch"):
            self.prepare(self.bought(buyer_branch="00012"), branches=self.TWO)
        with self.assertRaisesRegex(Translated, "another branch"):
            self.prepare(self.bought(buyer_branch="สาขาที่ 12"), branches=self.TWO)
        self.assertEqual(self.prepare(self.bought(buyer_branch="00000"), branches=self.TWO)["direction"], "purchase")
        self.assertEqual(self.prepare(self.bought(buyer_branch="สำนักงานใหญ่"), branches=self.TWO)["direction"],
                         "purchase")

    def test_without_a_printed_branch_the_conversation_branch_decides(self):
        with self.assertRaisesRegex(Translated, "another branch"):
            self.prepare(self.bought(), branches=self.TWO, work_branch="00012")
        seen = self.prepare(self.bought(), branches=self.TWO, work_branch="00000")
        self.assertEqual(seen["work_branch_asked"], [("C1", BOOK)])
        # 对话没定分店：不拦（CC 按说明问一次）
        self.assertEqual(self.prepare(self.bought(), branches=self.TWO)["direction"], "purchase")
        # 票面印了分店就按票面，不看对话
        self.assertEqual(self.prepare(self.bought(buyer_branch="00000"), branches=self.TWO, work_branch="00012")
                         ["direction"], "purchase")

    def test_the_conversation_branch_is_only_asked_for_when_it_matters(self):
        # 只有票面没印分店、这家又有几个分店时才去取对话指定的分店（钩子）；普通推送不调钩子
        seen = self.prepare(self.bought(), branches={BOOK: "00000"}, work_branch="00012")
        self.assertEqual(seen["work_branch_asked"], [])
        seen = self.prepare(self.bought(buyer_branch="00000"), branches=self.TWO, work_branch="00012")
        self.assertEqual(seen["work_branch_asked"], [])

    def test_a_single_branch_company_is_not_checked_by_branch(self):
        self.assertEqual(self.prepare(self.bought(buyer_branch="00012"), branches={BOOK: "00000"})["direction"],
                         "purchase")
        self.assertEqual(self.prepare(self.bought(buyer_branch="00012"))["direction"], "purchase")

    def test_the_rule_itself(self):
        rows = [{"source_id": key, "attributes": {"tax_id": BOOK_TAX, "branch": branch}} for key, branch in self.TWO.items()]
        head = rows[0]
        conflict = account_match.conflict
        self.assertEqual(conflict("express", head, direction="sales", fields={"seller_tax": BOOK_TAX,
                                                                               "seller_branch": "12"}, catalog=rows),
                         account_match.BRANCH_MISMATCH)  # 销项看卖方分店
        self.assertEqual(conflict("express", head, direction="", fields={"buyer_branch": "00012"}, catalog=rows), "")
        self.assertEqual(conflict("mrerp", head, direction="purchase", fields={"buyer_branch": "00012"}, catalog=rows), "")
        self.assertEqual(account_match.company_branches(rows + [{"attributes": {"tax_id": OTHER, "branch": "00099"}}],
                                                        BOOK_TAX), {"00000", "00012"})


if __name__ == "__main__":
    unittest.main()
