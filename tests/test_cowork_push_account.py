"""推送前的防错（用户 2026-09-27）：MR.ERP 进项不能按现金；Express 用户选的账套与票面确定对不上就不推。"""
import datetime as dt
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / "apps" / "pearnly_cowork"))
sys.path.insert(0, str(root / "apps" / "pearnly_core"))

import frappe  # noqa: E402

from pearnly_cowork.push.application import service  # noqa: E402

OWN = "0105551234567"
OTHER = "0105559999996"
BOOK = {"source_id": r"s:\2569\exp69\69acme", "label": "69ACME", "active": True,
        "attributes": {"root_label": "69EXP", "tax_id": OWN, "writable": True,
                       "period_start": "2025-11-01", "period_end": "2026-10-31", "next_end": "2027-10-31"}}


def _throw(message, exc=None, *args, **kwargs):
    raise (exc or frappe.ValidationError)(message)


def pages(**fields):
    return json.dumps([{"fields": {"invoice_number": "IV1", "total_amount": "10", **fields}}])


class MrerpPurchaseCashTest(unittest.TestCase):
    """MR.ERP 没有现金采购模块：进项 + 现金在服务端挡下，不悄悄改成赊购（此前只靠入口不给选项）。"""

    def test_purchase_cash_is_refused_before_anything_is_frozen(self):
        doc = frappe._dict(pages=pages(direction="purchase", posting_payment_manual="cash"), workspace="W1")
        with mock.patch.object(service, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(frappe, "throw", side_effect=_throw), \
                mock.patch.object(service, "freeze_target") as freeze:
            with self.assertRaises(frappe.ValidationError):
                service._prepare_mrerp(doc, mock.Mock(), "key", {}, "16:1", None, None)
        freeze.assert_not_called()

    def test_sales_cash_goes_on_to_freeze_the_target(self):
        doc = frappe._dict(pages=pages(direction="sales", posting_payment_manual="cash"), workspace="W1")
        with mock.patch.object(service, "freeze_target", side_effect=RuntimeError("frozen")) as freeze:
            with self.assertRaises(RuntimeError):
                service._prepare_mrerp(doc, mock.Mock(), "key", {}, "16:1", None, None)
        freeze.assert_called_once()


class ExpressAccountMismatchTest(unittest.TestCase):
    """用户选的 Express 账套：本方税号不同或日期不在会计期间里就不推；判断不了按用户的选择。"""

    def check(self, fields, invoice_date, rows=(BOOK,)):
        loaded = mock.Mock(evidence=mock.Mock(snapshot={"account_sets": json.dumps(list(rows))}))
        target = mock.Mock(account_set=r"S:\2569\EXP69\69ACME")
        with mock.patch.object(service, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(frappe, "throw", side_effect=_throw):
            service._require_matching_account(loaded, target, "purchase", fields, invoice_date)

    def test_matching_book_passes(self):
        self.check({"buyer_tax": OWN}, dt.date(2026, 3, 1))
        self.check({"buyer_tax": OWN}, None)

    def test_other_company_or_year_is_refused(self):
        with self.assertRaisesRegex(frappe.ValidationError, "own tax ID"):
            self.check({"buyer_tax": OTHER}, dt.date(2026, 3, 1))
        with self.assertRaisesRegex(frappe.ValidationError, "fiscal period"):
            self.check({"buyer_tax": OWN}, dt.date(2024, 3, 1))

    def test_book_missing_from_directory_is_left_to_the_other_checks(self):
        self.check({"buyer_tax": OTHER}, dt.date(2024, 3, 1), rows=())



class ExpressStockGroupTest(unittest.TestCase):
    """存货科目组每个账套选一次（用户 2026-09-28）：推哪份账套就带哪份账套选过的组。"""

    def test_the_chosen_group_of_the_target_account_set_is_used(self):
        stored = json.dumps({r"s:\2569\exp69\69acme": {"acccod": "SA"}, r"s:\2569\exp69\other": {"acccod": "SB"}})
        connection = frappe._dict(stock_acc_groups=stored)
        config = {"stock_acccod": "LEGACY"}
        service._apply_stock_group(config, connection, r"S:\2569\EXP69\69ACME")
        self.assertEqual(config["stock_acccod"], "SA")
        config = {}
        service._apply_stock_group(config, connection, r"S:\2569\EXP69\NEW")
        self.assertEqual(config, {})  # 这份账套没选过：照旧（零存货主档时按候选数解析）
        service._apply_stock_group(config, frappe._dict(stock_acc_groups=None), r"S:\2569\EXP69\69ACME")
        self.assertEqual(config, {})


class FolderTaskDefaultsTest(unittest.TestCase):
    """重推定时任务的票带任务默认处理（2026-10-12）：调用方给了 fixed（后台推送）照用；没给按钩子取、去掉对方档；
    被任务默认值决定的类别不写进账套记忆。纯 Python 替身版见 `test_cowork_push_fixed.py`。"""

    PROVIDERS = {"p.one": lambda *args: {"account": "5300", "party": "S001"}, "p.two": lambda *args: None}

    def test_given_fixed_does_not_ask_the_hook(self):
        with mock.patch.object(frappe, "get_hooks") as hooks:
            self.assertEqual(service._fixed("H1", "C1", BOOK["source_id"], {}), {})
            self.assertEqual(service._fixed("H1", "C1", BOOK["source_id"], {"payment": "cash"}), {"payment": "cash"})
        hooks.assert_not_called()

    def test_missing_fixed_merges_the_providers_without_the_party(self):
        with mock.patch.object(frappe, "get_hooks", return_value=list(self.PROVIDERS)), \
                mock.patch.object(frappe, "get_attr", side_effect=self.PROVIDERS.get):
            self.assertEqual(service._fixed("H1", "C1", BOOK["source_id"], None), {"account": "5300"})
        with mock.patch.object(frappe, "get_hooks", return_value=[]):
            self.assertIsNone(service._fixed("H1", "C1", BOOK["source_id"], None))

    def save(self, items):
        self.record = mock.Mock(return_value=True)
        loaded = mock.Mock(doc=frappe._dict(name="C1"))
        facts = (mock.Mock(account_set=BOOK["source_id"]), {}, {}, "purchase", {},
                 {"reported_accounts": [{"code": "5400"}], "reported_suppliers": [{"code": "S001"}]})
        with mock.patch.object(service.scope, "require"), \
                mock.patch.object(service.storage, "read", return_value=frappe._dict(name="H1", workspace="W1", client="")), \
                mock.patch.object(service.connections, "read_for_push", return_value=loaded), \
                mock.patch.object(service, "_express_facts", return_value=facts), \
                mock.patch.object(service, "_", side_effect=lambda text, *a, **k: text), \
                mock.patch.object(frappe, "throw", side_effect=_throw), \
                mock.patch.object(frappe, "get_hooks", return_value=list(self.PROVIDERS)), \
                mock.patch.object(frappe, "get_attr", side_effect=self.PROVIDERS.get), \
                mock.patch.object(service.choice_store, "record", self.record):
            return service.save_choices("H1", "C1", BOOK["source_id"], json.dumps(items))

    def test_save_choices_refuses_kinds_the_task_decides(self):
        with self.assertRaisesRegex(frappe.ValidationError, "cowork.choice.task_default"):
            self.save([{"kind": "party", "subject": "s", "value": "S001"},
                       {"kind": "account", "subject": "s", "value": "5400"}])
        self.record.assert_not_called()  # 整批不记，账套记忆不变

    def test_save_choices_records_what_the_task_does_not_decide(self):
        self.assertEqual(self.save([{"kind": "party", "subject": "s", "value": "S001"}]), {"saved": 1})
        self.record.assert_called_once()


if __name__ == "__main__":
    unittest.main()
