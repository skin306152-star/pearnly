"""MR.ERP 结果不明的「核对」出口（用户 11-28 批准；评估 handoff cc-coverage-5-mrerp-核对-评估.md）：ERP 采购 / 销售单据弹窗、收款页、
CC（单据 erp_resolve_push_result、收款 erp_collection_resolve_result）同一套口径——只收「账本是 MR.ERP + 停在结果不明」，已入账记
单号、没有入账回到可再推 / 撤回；同 Cowork 复核页核对（`push.native.service.resolve_unknown`）。按源码与纯规则守，行为另见
test_erp_mrerp_direct / test_erp_mrerp_collection / test_erp_collection_cc。

    python3 tests/test_erp_mrerp_unknown_check.py
"""
from __future__ import annotations

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
sys.path.insert(0, str(ROOT / "apps/pearnly_erp_portal"))


def _read(path) -> str:
    return (ERP / path).read_text(encoding="utf-8")


class PureRule(unittest.TestCase):
    def test_checked_values_match_cowork(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("mrerp_contracts", ERP / "mrerp/contracts.py")
        contracts = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(contracts)
        self.assertEqual(contracts.checked(True, " PG1 "), {"ledger_state": "written", "ledger_docnum": "PG1", "ledger_reason": ""})
        self.assertEqual(contracts.checked(False), {"ledger_state": "held", "ledger_reason": contracts.CONFIRMED_NOT_ENTERED})
        for wrong in (1, "true", None):
            with self.subTest(wrong=wrong), self.assertRaises(ValueError):
                contracts.checked(wrong)
        cowork = (ROOT / "apps/pearnly_cowork/pearnly_cowork/push/native/service.py").read_text(encoding="utf-8")
        self.assertIn("if type(entered) is not bool:", cowork)  # 同 Cowork：只收布尔


class Entrances(unittest.TestCase):
    def test_the_document_dialog_offers_check_only_for_an_mrerp_unknown(self):
        api = _read("express/api.py")
        view = api.split("def _mrerp_view(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('"can_check": _mrerp_unknown(row) and writer', view)
        resolve = api.split("def resolve_unknown(", 1)[1].split("\n@frappe.whitelist", 1)[0]
        self.assertIn("_document(doctype, name, write=True)", resolve)  # 权限同「重试」
        self.assertIn("doc_ledger(row) != LEDGER_MRERP or not _mrerp_unknown(row)", resolve)
        page = _read("public/desk/transactions.js")
        self.assertIn('info.can_check ? __("mrerp.unknown.check")', page)
        self.assertIn('api + "resolve_unknown"', page)

    def test_the_collections_page_offers_check_instead_of_withdraw(self):
        service = _read("collection/service.py")
        self.assertIn('"checkable": ledger.handles(doc.company) and ledger.unknown(doc)', service)
        check = service.split("def check_unknown(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("_own(name, for_update=True)", check)  # 权限同撤回
        page = _read("public/desk/collections.js")
        self.assertIn('dialog.set_primary_action(__("mrerp.unknown.check")', page)
        self.assertIn("!can_retry && !data.checkable", page)
        self.assertIn('api + "api_collection_check"', page)

    def test_cc_records_what_the_user_found_for_an_mrerp_unknown(self):
        tools = _read("express/cc_tools.py")  # 行为另见 test_erp_cc_chat_push.ResolveTests.test_an_mrerp_unknown_is_recorded_as_the_user_found_it
        resolve = tools.split("def resolve_push_result(", 1)[1].split("\ndef ", 1)[0]
        self.assertLess(resolve.index("direct_push.permitted("), resolve.index("api.resolve_unknown("))  # 只在同意了的写入计划里
        self.assertIn("type(entered) is not bool", resolve)
        collection = _read("collection/cc_tools.py")
        self.assertIn('"name": "erp_collection_resolve_result"', collection)
        self.assertIn("service.check_unknown", collection.split("def check(", 1)[1].split("\ndef ", 1)[0])

    def test_words_in_four_languages(self):
        rows = {line.split("\t")[4]: line.split("\t")[1:4] for line in _read("i18n/messages.tsv").splitlines()
                if line.count("\t") >= 4}
        for key in ("mrerp.unknown.check", "mrerp.unknown.outcome", "mrerp.unknown.entered", "mrerp.unknown.not_entered",
                    "mrerp.reason.checkedNotEntered"):
            with self.subTest(key=key):
                self.assertEqual(len([cell for cell in rows.get(key, []) if cell.strip()]), 3)
        self.assertEqual(rows["mrerp.unknown.check"][0], "已在 MR.ERP 核对")


if __name__ == "__main__":
    unittest.main()
