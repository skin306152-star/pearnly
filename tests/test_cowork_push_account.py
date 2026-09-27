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


if __name__ == "__main__":
    unittest.main()
