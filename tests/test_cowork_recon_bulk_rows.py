"""对账结果行一次批量写入（2026-10-02 压测：逐行完整插入让任务头和行的事务开 60–100 多秒）。

守住：一份对账只写一次（不逐行插入）、名称取 record_id、工作空间权限查一次、选项与长度不合照旧报错且一行不写、空日期存 NULL、
字段与逐行插入时相同。离线：数据库、权限与表结构用替身（表结构读 DocType 定义文件）。"""
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

import frappe

from pearnly_cowork.reconciliation import bank_recon_service as service

HERE = os.path.dirname(os.path.abspath(__file__))
ROW_JSON = os.path.join(HERE, "..", "apps", "pearnly_cowork", "pearnly_cowork", "pearnly_cowork", "doctype",
                        "cowork_bank_reconciliation_row", "cowork_bank_reconciliation_row.json")


class Meta:
    def __init__(self):
        self.fields = {f["fieldname"]: frappe._dict(f) for f in json.load(open(ROW_JSON))["fields"]}

    def get_field(self, name):
        return self.fields.get(name)


def result(**values):
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
        patches = [
            mock.patch.object(service, "_", side_effect=lambda text: text),
            mock.patch.object(service, "now_datetime", return_value="2026-10-02 09:00:00"),
            mock.patch.object(service.frappe, "throw", side_effect=lambda message: (_ for _ in ()).throw(
                frappe.ValidationError(message))),
            mock.patch.object(service.frappe, "get_meta", return_value=Meta()),
            mock.patch.object(service.bank_recon_access, "require", side_effect=self.required.append),
            mock.patch.object(service, "persist", side_effect=AssertionError("rows must not be inserted one by one")),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.task = frappe._dict(name="CBR-1", workspace="W1")

    def test_all_rows_are_written_in_one_bulk_insert_named_by_record_id(self):
        service._persist_rows(self.task, [result(), result(stmt_balance_ok=None), result(match_status="gl_debit_only")])
        self.assertEqual(len(self.calls), 1)
        (doctype, fields, values), kwargs = self.calls[0]
        self.assertEqual(doctype, service.ROW)
        values = [dict(zip(fields, row)) for row in values]
        self.assertEqual([row["name"] for row in values], ["CBR-1-1", "CBR-1-2", "CBR-1-3"])
        self.assertEqual([row["record_id"] for row in values], ["CBR-1-1", "CBR-1-2", "CBR-1-3"])
        self.assertEqual({row["workspace"] for row in values} | {row["task"] for row in values}, {"W1", "CBR-1"})
        self.assertEqual([row["row_index"] for row in values], [1, 2, 3])
        self.assertEqual([row["stmt_balance_ok"] for row in values], ["ok", "", "ok"])
        self.assertIsNone(values[0]["gl_date"])  # 空日期存 NULL
        self.assertEqual(values[0]["stmt_date"], "2026-09-01")
        self.assertEqual(set(fields) - {"name", "owner", "modified_by", "creation", "modified", "docstatus", "idx"},
                         {"record_id", "workspace", "task", "row_index", *service.row_values(result())})
        self.assertEqual((values[0]["owner"], values[0]["docstatus"], values[0]["idx"]), ("a@x", 0, 0))
        self.assertEqual(self.required, ["W1"])  # 权限只查一次

    def test_a_bad_value_writes_nothing(self):
        for bad in (result(match_status="nonsense"), result(stmt_desc="x" * 301), result(stmt_confidence="x" * 11)):
            self.calls.clear()
            with self.assertRaises(frappe.ValidationError):
                service._persist_rows(self.task, [result(), bad])
            self.assertEqual(self.calls, [])

    def test_no_results_write_nothing(self):
        service._persist_rows(self.task, [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
