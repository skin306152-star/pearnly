"""对账结果行一次批量写入（2026-10-02 压测：逐行完整插入让任务头和行的事务开 60–100 多秒）。银行、收入对账同一做法。

守住：一份对账只写一次（不逐行插入）、名称取 record_id、工作空间权限查一次、选项与长度不合照旧报错且一行不写、空日期存 NULL、
字段与逐行插入时相同；银行新建、收入新建、收入重算都走这一处。离线：数据库、权限与表结构用替身（表结构读 DocType 定义文件）。"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.reconciliation import bank_recon_service, income_recon_service, recon_documents

DOCTYPES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "apps", "pearnly_cowork", "pearnly_cowork",
                        "pearnly_cowork", "doctype")


class Meta:
    def __init__(self, folder):
        self.fields = {f["fieldname"]: frappe._dict(f)
                       for f in json.load(open(os.path.join(DOCTYPES, folder, folder + ".json")))["fields"]}

    def get_field(self, name):
        return self.fields.get(name)


def bank(**values):
    base = dict(match_status="matched", match_layer=1, date_diff_days=0, stmt_date="2026-09-01", stmt_desc="x",
                stmt_withdrawal=0.0, stmt_deposit=100.0, stmt_balance=1100.0, stmt_confidence="high",
                stmt_balance_ok=True, stmt_autocorrected=False, gl_date="", gl_doc_no="JV1", gl_account_code="1110",
                gl_desc="", gl_debit=100.0, gl_credit=0.0, gl_balance=0.0, source_stmt_file="s.pdf",
                source_gl_file="g.xlsx", group_no=0)
    return SimpleNamespace(**{**base, **values})


class BulkRows(unittest.TestCase):
    def setUp(self):
        self.calls, self.required = [], []
        frappe.local.db = mock.Mock(bulk_insert=mock.Mock(side_effect=lambda *a, **k: self.calls.append((a, k))))
        frappe.local.session = frappe._dict(user="a@x")
        self.addCleanup(lambda: setattr(frappe.local, "db", None))
        self.meta = {}
        patches = [
            mock.patch.object(recon_documents, "_", side_effect=lambda text: text),
            mock.patch.object(recon_documents, "now_datetime", return_value="2026-10-02 09:00:00"),
            mock.patch.object(recon_documents.frappe, "throw", side_effect=lambda message: (_ for _ in ()).throw(
                frappe.ValidationError(message))),
            mock.patch.object(recon_documents.frappe, "get_meta", side_effect=lambda doctype: self.meta[doctype]),
            mock.patch.object(bank_recon_service.bank_recon_access, "require", side_effect=self.required.append),
            mock.patch.object(income_recon_service.income_recon_access, "require", side_effect=self.required.append),
            mock.patch.object(bank_recon_service, "persist", side_effect=AssertionError("no row-by-row insert")),
            mock.patch.object(income_recon_service, "persist", side_effect=AssertionError("no row-by-row insert")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.meta[bank_recon_service.ROW] = Meta("cowork_bank_reconciliation_row")
        self.meta[income_recon_service.ROW] = Meta("cowork_income_reconciliation_row")
        self.task = frappe._dict(name="CBR-1", workspace="W1")

    def rows(self):
        (doctype, fields, values), _kwargs = self.calls[0]
        return doctype, fields, [dict(zip(fields, row)) for row in values]

    def test_bank_rows_are_written_in_one_bulk_insert_named_by_record_id(self):
        bank_recon_service._persist_rows(self.task, [bank(), bank(stmt_balance_ok=None),
                                                     bank(match_status="gl_debit_only")])
        self.assertEqual(len(self.calls), 1)
        doctype, fields, values = self.rows()
        self.assertEqual(doctype, bank_recon_service.ROW)
        self.assertEqual([row["name"] for row in values], ["CBR-1-1", "CBR-1-2", "CBR-1-3"])
        self.assertEqual([row["record_id"] for row in values], ["CBR-1-1", "CBR-1-2", "CBR-1-3"])
        self.assertEqual({row["workspace"] for row in values} | {row["task"] for row in values}, {"W1", "CBR-1"})
        self.assertEqual([row["row_index"] for row in values], [1, 2, 3])
        self.assertEqual([row["stmt_balance_ok"] for row in values], ["ok", "", "ok"])
        self.assertIsNone(values[0]["gl_date"])  # 空日期存 NULL
        self.assertEqual(values[0]["stmt_date"], "2026-09-01")
        self.assertEqual(set(fields) - {"name", "owner", "modified_by", "creation", "modified", "docstatus", "idx"},
                         {"record_id", "workspace", "task", "row_index", *bank_recon_service.row_values(bank())})
        self.assertEqual((values[0]["owner"], values[0]["docstatus"], values[0]["idx"]), ("a@x", 0, 0))
        self.assertEqual(self.required, ["W1"])  # 权限只查一次

    def test_income_rows_use_the_same_bulk_insert(self):
        task = frappe._dict(name="CIR-1", workspace="W2")
        values = {"doc_no": "IV1", "vat_date": "2026-09-01", "customer_name": "c", "vat_amount": 7.0,
                  "has_gl_match": 1, "gl_amount": 7.0, "diff": 0.0, "account_codes": "4100"}
        with mock.patch.object(income_recon_service, "row_values", side_effect=lambda result: dict(result)):
            income_recon_service._persist_rows(task, [values, {**values, "doc_no": "IV2"}])
        doctype, _fields, rows = self.rows()
        self.assertEqual(doctype, income_recon_service.ROW)
        self.assertEqual([row["name"] for row in rows], ["CIR-1-1", "CIR-1-2"])
        self.assertEqual([row["doc_no"] for row in rows], ["IV1", "IV2"])
        self.assertEqual(self.required, ["W2"])
        self.calls.clear()
        with mock.patch.object(income_recon_service, "row_values", side_effect=lambda result: dict(result)):
            with self.assertRaises(frappe.ValidationError):
                income_recon_service._persist_rows(task, [values, {**values, "vat_date": "x" * 41}])
        self.assertEqual(self.calls, [])

    def test_a_bad_value_writes_nothing(self):
        for bad in (bank(match_status="nonsense"), bank(stmt_desc="x" * 301), bank(stmt_confidence="x" * 11)):
            self.calls.clear()
            with self.assertRaises(frappe.ValidationError):
                bank_recon_service._persist_rows(self.task, [bank(), bad])
            self.assertEqual(self.calls, [])

    def test_no_results_write_nothing(self):
        bank_recon_service._persist_rows(self.task, [])
        self.assertEqual(self.calls, [])

    def test_every_writer_of_reconciliation_rows_goes_through_the_bulk_insert(self):
        import inspect
        from pearnly_cowork.reconciliation import revision
        for module in (bank_recon_service, income_recon_service, revision):
            source = inspect.getsource(module)
            self.assertNotRegex(source, r'persist\(frappe\.get_doc\(\{\s*"doctype":\s*(income_recon_service\.)?ROW')


if __name__ == "__main__":
    unittest.main()
