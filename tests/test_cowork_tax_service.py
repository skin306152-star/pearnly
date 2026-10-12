"""报税草稿服务层（`tax.access` / `tax.service` / `tax.sources` / `tax.memory`）与 50 ทวิ 参考记录
（`history.certificates`）的离线检查：Frappe 用内存替身，读回（L1）用假模块注入。

权限（功能码、角色、管理员）；没有回读回 readback_needed；同一公司同月刷新同一份草稿并保留选项与逐行改动；
所得类型记住后重出即套用；识别记录按票据日区间、经权限查询读；参考记录只在「不录入」时写、重放不重复写、
写失败不连累票据入库；识别存档在保存批次前写参考记录。
"""
import ast
import importlib.abc
import importlib.machinery
import json
from datetime import date, datetime
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "apps" / "pearnly_cowork"), str(ROOT / "apps" / "pearnly_core")]
COMPANY = "0105556000009"
SUPPLIER = "0105546015062"
LANDLORD = "3100500000003"


class Flags(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


class Doc:
    KEYS = ("record_id", "choice_key", "certificate_key")

    def __init__(self, frappe, data):
        self.__dict__.update(_f=frappe, data=dict(data), flags=Flags(), new=True)

    def __getattr__(self, key):
        return self.data.get(key)

    def __setattr__(self, key, value):
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)

    def update(self, values):
        self.data.update(values)

    def is_new(self):
        return self.new

    def insert(self, **kwargs):
        name = self.data.get("name") or next(self.data[key] for key in self.KEYS if self.data.get(key))
        self.data["name"] = name
        self._f.store[(self.data["doctype"], name)] = self
        self.__dict__["new"] = False
        self._f.writes.append((self.data["doctype"], name, dict(self.flags)))
        return self

    def save(self, **kwargs):
        self._f.writes.append((self.data["doctype"], self.data["name"], dict(self.flags)))
        return self


class Row(dict):
    __getattr__ = dict.get


class FakeFrappe(types.ModuleType):
    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    class DoesNotExistError(Exception):
        pass

    class QueryDeadlockError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.reset()

    def reset(self):
        self.conf = {"pearnly_product": "cowork", "pearnly_features": ["intake", "tax_drafts"]}
        self.session = types.SimpleNamespace(user="acct@example.com")
        self.local = types.SimpleNamespace(site="s1", lang="th", pearnly_assistant=None)
        self.flags = Flags()
        self.roles = {"Cowork Tax Filing"}
        self.store, self.writes, self.lists, self.list_calls, self.logged = {}, [], {}, [], []
        self.savepoints, self.rolled_back = [], []
        self.db = types.SimpleNamespace(exists=self._exists, get_value=self._get_value, commit=lambda: None,
                                        rollback=self._rollback, savepoint=self.savepoints.append)

    def _rollback(self, save_point=None):
        self.rolled_back.append(save_point)

    def _exists(self, doctype, name=None):
        return (doctype, name) in self.store

    def _get_value(self, doctype, name, field="name", for_update=False, **kwargs):
        if doctype == "User":
            return 1
        if doctype == "Cowork Workspace":
            return name
        doc = self.store.get((doctype, name))
        return doc.get(field) if doc else None

    @staticmethod
    def _(text, lang=None):
        return text

    def get_roles(self, user=None):
        return list(self.roles)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    def get_doc(self, doctype, name=None, for_update=False):
        if isinstance(doctype, dict):
            return Doc(self, doctype)
        if (doctype, name) not in self.store:
            raise self.DoesNotExistError(name)
        return self.store[(doctype, name)]

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        rows = []
        for (kind, _name), doc in self.store.items():
            if kind != doctype or any(doc.get(key) != value for key, value in (filters or {}).items()
                                      if not isinstance(value, list)):
                continue
            rows.append(Row({field: doc.get(field) for field in fields or ["name"]}))
        return rows

    def get_list(self, doctype, filters=None, fields=None, limit_start=0, limit_page_length=None, **kwargs):
        self.list_calls.append({"doctype": doctype, "filters": filters, "fields": fields, **kwargs})
        rows = self.lists.get(doctype, [])
        for key, value in (filters or {}).items():
            if isinstance(value, list) and value[0] == "in":
                rows = [row for row in rows if row.get(key) in value[1]]
        rows = rows[limit_start:limit_start + limit_page_length if limit_page_length else None]
        return [Row({field: row.get(field) for field in fields}) for row in rows]

    def delete_doc(self, doctype, name, **kwargs):
        self.store.pop((doctype, name), None)

    def log_error(self, title=None, message=None):
        self.logged.append(title)

    @staticmethod
    def get_traceback():
        return "traceback"


frappe = FakeFrappe()
utils = types.ModuleType("frappe.utils")
utils.now_datetime = lambda: datetime(2026, 10, 13, 9, 0, 0)
frappe.utils = utils
model = types.ModuleType("frappe.model")
document = types.ModuleType("frappe.model.document")


class Document:
    pass


document.Document = Document
model.document = document
sys.modules.update({"frappe": frappe, "frappe.utils": utils, "frappe.model": model, "frappe.model.document": document})

shell = types.ModuleType("pearnly_core.shell")
shell.entitlements = lambda: list(frappe.conf.get("pearnly_features") or [])
sys.modules["pearnly_core.shell"] = shell
import pearnly_core  # noqa: E402

pearnly_core.shell = shell

scope = types.ModuleType("pearnly_cowork.access.scope")
scope.READ_FEATURES = ("intake", "ocr_history")
scope.read_allowed = True
scope.contains = lambda workspace, client=None, user=None: workspace == "WS1"
scope.default_workspace = lambda: "WS1"
scope.allowed_any = lambda features, user=None: scope.read_allowed


def _require_read():
    if not scope.read_allowed:
        frappe.throw("Not Permitted", frappe.PermissionError)


def _require_scope(workspace, client=None):
    if workspace != "WS1":
        frappe.throw("Not Permitted", frappe.PermissionError)


scope.require_read = _require_read
scope.require_scope = _require_scope
access_package = types.ModuleType("pearnly_cowork.access")
access_package.__path__ = []
access_package.scope = scope
sys.modules.update({"pearnly_cowork.access": access_package, "pearnly_cowork.access.scope": scope})

from pearnly_cowork.history import certificates  # noqa: E402
from pearnly_cowork.tax import access, memory, service, sources, wht  # noqa: E402


def readback_module(records, header=None):
    package = types.ModuleType("pearnly_cowork.readback")
    package.__path__ = []
    module = types.ModuleType("pearnly_cowork.readback.service")
    module.calls = []

    def vat_records(connection, account_set_key, year, month):
        module.calls.append((connection, account_set_key, year, month))
        if records is None:
            return None
        return {"ref": "RB-1", "read_at": "2026-10-13 08:00:00",
                "header": header or {"company": "Our Co", "tax_id": COMPANY, "branch": "00000"}, "records": records}

    module.vat_records = vat_records
    package.service = module
    return {"pearnly_cowork.readback": package, "pearnly_cowork.readback.service": module}


def isvat(side="P", refnum="T1", amount="1000.00", vat="70.00", tax_date="2026-09-10", period="2026-09", **extra):
    return {"side": side, "rectyp": "3", "period": period, "tax_date": tax_date, "doc_date": tax_date,
            "docnum": "RR" + refnum, "refnum": refnum, "name": "Supplier", "tax_id": SUPPLIER, "branch": "00000",
            "amount": amount, "vat": vat, "status": "N", "deleted": False, **extra}


class Base(unittest.TestCase):
    def setUp(self):
        frappe.reset()
        scope.read_allowed = True


class Access(Base):
    def test_feature_role_and_product_are_all_required(self):
        self.assertTrue(access.allowed())
        frappe.roles = {"Cowork Admin"}
        self.assertTrue(access.allowed())
        frappe.roles = {"Cowork VAT Check"}
        self.assertFalse(access.allowed())
        frappe.roles = {"Cowork Tax Filing"}
        frappe.conf["pearnly_features"] = ["intake"]
        self.assertFalse(access.allowed())
        frappe.conf.update(pearnly_features=["tax_drafts"], pearnly_product="erp")
        self.assertFalse(access.allowed())
        with self.assertRaises(frappe.PermissionError):
            access.require()

    def test_generic_delete_and_share_are_refused(self):
        doc = types.SimpleNamespace(workspace="WS1")
        self.assertTrue(access.has_permission(doc, "read"))
        for ptype in ("delete", "share", "submit"):
            with self.assertRaises(frappe.PermissionError):
                access.has_permission(doc, ptype)
        with self.assertRaises(frappe.PermissionError):
            access.has_permission(types.SimpleNamespace(workspace="WS2"), "read")
        frappe.roles = set()
        self.assertEqual(access.task_query(), "1=0")


class VatDraft(Base):
    def draft(self, records, options=None, header=None, period="2026-09"):
        with mock.patch.dict(sys.modules, readback_module(records, header)):
            return service.vat_draft("CONN-1", "ACC-2026", period, options)

    def test_without_a_readback_it_asks_for_one_and_writes_nothing(self):
        self.assertEqual(self.draft(None)["status"], "readback_needed")
        with mock.patch.dict(sys.modules, {"pearnly_cowork.readback": None}):
            self.assertEqual(service.vat_draft("CONN-1", "ACC-2026", "2026-09")["status"], "readback_needed")
        self.assertEqual(frappe.writes, [])

    def test_the_draft_is_the_express_records_and_is_stored_once_per_company_month(self):
        frappe.lists["Cowork OCR History"] = [
            {"name": "HIS-1", "invoice_no": "T1", "seller_tax": SUPPLIER,
             "pages": json.dumps([{"fields": {"document_type": "simplified_tax_invoice", "seller_tax": SUPPLIER,
                                              "invoice_number": "T1"}}])}]
        records = [isvat(refnum="T1"), isvat(refnum="T2", amount="2000.00", vat="140.00"),
                   isvat(side="S", refnum="S1", amount="10000.00", vat="700.00")]
        summary = self.draft(records, options={"excess_brought_forward": "100"})
        self.assertEqual(summary["status"], "drafted")
        self.assertEqual((summary["boxes"]["5"], summary["boxes"]["7"], summary["boxes"]["10"], summary["boxes"]["11"]),
                         ("700.00", "210.00", "100.00", "390.00"))
        self.assertEqual(summary["boxes"]["13"], "")
        self.assertEqual(summary["claimable_if_fixed"], {"amount": "2000.00", "vat": "140.00"})
        self.assertEqual(summary["issues_by_code"].get("input_abbreviated"), 1)
        self.assertEqual(summary["deadlines"], {"paper": "2026-10-15", "efiling": "2026-10-23"})
        self.assertEqual(summary["tax_total"], "390.00")
        stored = frappe.store[("Cowork Tax Draft", summary["draft"])]
        self.assertEqual((stored.kind, stored.period, stored.company_tax_id, stored.source_ref),
                         ("vat_return", "2026-09", COMPANY, "RB-1"))
        self.assertEqual(stored.flags.get("tax_draft_write") is not None, True)
        # 同一公司同月再出：同一份草稿，选项保留
        again = self.draft(records)
        self.assertEqual(again["draft"], summary["draft"])
        self.assertEqual(again["options"]["excess_brought_forward"], "100.00")
        self.assertEqual(len([key for key in frappe.store if key[0] == "Cowork Tax Draft"]), 1)

    def test_rows_that_cannot_be_read_are_listed_not_summed(self):
        summary = self.draft([isvat(refnum="T1"), isvat(refnum="T2", amount="n/a")])
        self.assertEqual(summary["boxes"]["6"], "1000.00")
        unreadable = [item for item in summary["issues"] if item["code"] == "row_unreadable"]
        self.assertEqual([(item["index"], item["docnum"], item["level"]) for item in unreadable], [(2, "RRT2", "error")])

    def test_without_read_access_the_documents_are_not_checked(self):
        scope.read_allowed = False
        summary = self.draft([isvat()])
        self.assertFalse(summary["documents_checked"])
        self.assertNotIn("input_no_document", summary["issues_by_code"])
        self.assertEqual(frappe.list_calls, [])

    def test_last_months_overpayment_is_offered_as_a_hint(self):
        previous = service.record_id("WS1", "vat_return", COMPANY, "00000", "2026-08")
        frappe.store[("Cowork Tax Draft", previous)] = Doc(frappe, {"summary_json": json.dumps({"boxes": {"12": "55.50"}})})
        self.assertEqual(self.draft([isvat()])["previous_excess"], "55.50")

    def test_bad_requests_are_refused(self):
        with self.assertRaises(frappe.ValidationError):
            self.draft([isvat()], period="2026-13")
        with self.assertRaises(frappe.ValidationError):
            self.draft([isvat()], options={"exempt_sales": "-1"})
        with self.assertRaises(frappe.ValidationError):
            service.vat_draft("", "ACC", "2026-09")
        frappe.roles = set()
        with self.assertRaises(frappe.PermissionError):
            self.draft([isvat()])


def history_page(**fields):
    return json.dumps([{"fields": fields}])


class WhtDraft(Base):
    def setUp(self):
        super().setUp()
        frappe.lists["Cowork OCR History"] = [
            {"name": "HIS-1", "seller_tax": SUPPLIER, "buyer_tax": COMPANY,
             "pages": history_page(document_type="tax_invoice", invoice_number="INV1", date="2026-09-05",
                                   seller_name="Transport", seller_tax=SUPPLIER, buyer_name="Our Co",
                                   buyer_tax=COMPANY, subtotal="10000", vat="700", total_amount="10700",
                                   wht_amount="100")},
            {"name": "HIS-9", "seller_tax": "0105536000011", "buyer_tax": "0105556999995", "pages": history_page()}]
        self.certificates = [{"ref": "REF-1", "origin": "reference", "fields": {
            "document_flag": "withholding_certificate", "date": "2026-09-20", "seller_tax": COMPANY,
            "buyer_tax": LANDLORD, "buyer_name": "นายสมชาย ใจดี", "subtotal": "20000", "wht_amount": "1000"}}]

    def draft(self, edits=None, tax_id=COMPANY):
        with mock.patch.object(certificates, "listing", return_value=list(self.certificates)) as listed:
            summary = service.wht_draft(tax_id, "2026-09", edits)
        self.listed = listed
        return summary

    def test_documents_are_read_through_the_permission_query_by_invoice_date(self):
        summary = self.draft()
        first = frappe.list_calls[0]
        self.assertEqual(first["filters"], {"invoice_date": ["between", [date(2026, 6, 3), date(2026, 9, 30)]]})
        self.assertEqual(frappe.list_calls[1]["filters"], {"name": ["in", ["HIS-1"]]})
        self.listed.assert_called_once_with("WS1", date(2026, 6, 3), date(2026, 9, 30))
        self.assertEqual(summary["company"], {"name": "Our Co", "tax_id": COMPANY, "branch": "00000"})
        self.assertEqual([line["row"] for line in summary["forms"]["pnd53"]["lines"]], ["HIS-1"])
        # 50 ทวิ 的对方没有往来、也没记过：先放待确认
        self.assertEqual([row["row"] for row in summary["review"]], ["REF-1"])
        self.assertEqual(summary["deadlines"], {"paper": "2026-10-07", "efiling": "2026-10-15"})

    def test_answers_are_remembered_and_applied_next_time(self):
        self.assertEqual(service.answer([{"payee_tax_id": LANDLORD, "income_type": "rent", "payee_name": "สมชาย"},
                                         {"category": " ค่าขนส่ง ", "income_type": "transport"}])["status"], "saved")
        remembered = memory.choices("WS1")
        self.assertEqual(remembered["payee:" + LANDLORD]["income_type"], "rent")
        self.assertIn("category:ค่าขนส่ง", remembered)
        summary = self.draft()
        line = summary["forms"]["pnd3"]["lines"][0]
        self.assertEqual((line["row"], line["income_type"], line["name_first"]), ("REF-1", "rent", "สมชาย"))
        self.assertEqual(service.forget(payee_tax_id=LANDLORD)["status"], "removed")
        self.assertNotIn("payee:" + LANDLORD, memory.choices("WS1"))
        with self.assertRaises(frappe.ValidationError):
            service.answer([{"payee_tax_id": LANDLORD, "income_type": "salary"}])
        with self.assertRaises(frappe.ValidationError):
            service.answer([{"income_type": "rent"}])

    def test_edits_are_kept_on_the_draft(self):
        summary = self.draft(edits=[{"row": "REF-1", "include": True}, {"row": "NOPE", "include": True},
                                    {"row": "HIS-1", "rate": "abc"}])
        self.assertEqual(summary["edits_rejected"], [{"row": "NOPE", "reason": "row_unknown"},
                                                     {"row": "HIS-1", "reason": "edit_invalid"}])
        self.assertEqual([line["row"] for line in summary["forms"]["pnd3"]["lines"]], ["REF-1"])
        again = self.draft()
        self.assertEqual(again["draft"], summary["draft"])
        self.assertEqual([line["row"] for line in again["forms"]["pnd3"]["lines"]], ["REF-1"])
        reset = self.draft(edits=[{"row": "REF-1", "reset": True}])
        self.assertEqual(reset["forms"]["pnd3"]["lines"], [])

    def test_company_tax_id_and_read_access_are_required(self):
        with self.assertRaises(frappe.ValidationError):
            self.draft(tax_id="12345")
        scope.read_allowed = False
        with self.assertRaises(frappe.PermissionError):
            self.draft()


class Sources(Base):
    def test_a_missing_readback_app_means_no_readback_but_other_import_errors_surface(self):
        with mock.patch.dict(sys.modules, {"pearnly_cowork.readback": None}):
            self.assertIsNone(sources.vat_readback("C", "A", 2026, 9))

        class Broken(importlib.abc.MetaPathFinder):
            def find_spec(self, name, path=None, target=None):
                if name == "pearnly_cowork.readback":
                    raise ModuleNotFoundError("No module named 'openpyxl'", name="openpyxl")
                return None

        finder = Broken()
        sys.meta_path.insert(0, finder)
        try:
            with mock.patch.dict(sys.modules):
                sys.modules.pop("pearnly_cowork.readback", None)
                with self.assertRaises(ModuleNotFoundError):
                    sources.vat_readback("C", "A", 2026, 9)
        finally:
            sys.meta_path.remove(finder)

    def test_purchase_rows_link_by_record_or_by_seller_and_number(self):
        frappe.lists["Cowork OCR History"] = [
            {"name": "HIS-1", "invoice_no": "t 1", "seller_tax": SUPPLIER,
             "pages": history_page(document_type="receipt", seller_tax=SUPPLIER, seller_name="S", invoice_number="T1")},
            {"name": "HIS-2", "invoice_no": "T1", "seller_tax": "0105536000011", "pages": history_page()}]
        record = types.SimpleNamespace(history="HIS-1", refnum="T1")
        found = sources.purchase_documents([record])
        self.assertEqual(set(found), {"HIS-1", SUPPLIER + "|T1"})
        self.assertEqual(found["HIS-1"]["document_type"], "receipt")


class Certificates(Base):
    BATCH = types.SimpleNamespace(name="COB-1", workspace="WS1", source_file="SRC-1",
                                  get=lambda key, default=None: {"client": "", "source_file": "SRC-1"}.get(key, default))
    PAGES = [{"page_index": 1, "fields": {"document_flag": "withholding_certificate", "is_not_invoice": True,
                                          "date": "2026-09-20", "seller_tax": COMPANY, "buyer_tax": LANDLORD,
                                          "subtotal": "20000", "wht_amount": "1000", "items": []}},
             {"page_index": 2, "fields": {"document_type": "tax_invoice"}}]

    def test_only_skipped_certificates_are_kept_once(self):
        self.assertEqual(certificates.keep_skipped(self.BATCH, self.PAGES, {}), 0)
        self.assertEqual(certificates.keep_skipped(self.BATCH, self.PAGES, {"withholding_certificate": "skip"}), 1)
        self.assertEqual(certificates.keep_skipped(self.BATCH, self.PAGES, {"withholding_certificate": "skip"}), 0)
        (kind, name), = [key for key in frappe.store]
        doc = frappe.store[(kind, name)]
        self.assertEqual((kind, doc.batch, doc.page_index, doc.invoice_date, doc.buyer_tax),
                         ("Cowork WHT Certificate", "COB-1", 1, date(2026, 9, 20), LANDLORD))
        self.assertNotIn("items", json.loads(doc.fields_json))
        self.assertEqual(frappe.savepoints, [certificates.SAVEPOINT, certificates.SAVEPOINT])

    def test_a_failed_reference_does_not_fail_the_invoices(self):
        with mock.patch.object(frappe, "get_doc", side_effect=RuntimeError("boom")):
            self.assertEqual(certificates.keep_skipped(self.BATCH, self.PAGES, {"withholding_certificate": "skip"}), 0)
        self.assertEqual(frappe.rolled_back, [certificates.SAVEPOINT])
        self.assertEqual(frappe.logged, ["Cowork WHT certificate reference not kept"])
        with mock.patch.object(frappe, "get_doc", side_effect=frappe.QueryDeadlockError()):
            with self.assertRaises(frappe.QueryDeadlockError):
                certificates.keep_skipped(self.BATCH, self.PAGES, {"withholding_certificate": "skip"})

    def test_listing_needs_read_access(self):
        certificates.keep_skipped(self.BATCH, self.PAGES, {"withholding_certificate": "skip"})
        with mock.patch.object(frappe, "get_all", wraps=frappe.get_all) as get_all:
            found = certificates.listing("WS1", date(2026, 9, 1), date(2026, 9, 30))
        self.assertEqual(get_all.call_args.kwargs["filters"],
                         {"workspace": "WS1", "invoice_date": ["between", [date(2026, 9, 1), date(2026, 9, 30)]]})
        self.assertEqual([item["origin"] for item in found], ["reference"])
        self.assertEqual(found[0]["fields"]["wht_amount"], "1000")
        scope.read_allowed = False
        with self.assertRaises(frappe.PermissionError):
            certificates.listing("WS1", date(2026, 9, 1), date(2026, 9, 30))

    def test_the_recognition_archive_keeps_references_before_saving_the_batch(self):
        source = (ROOT / "apps/pearnly_cowork/pearnly_cowork/ocr/execution/workflow.py").read_text(encoding="utf-8")
        function = next(node for node in ast.parse(source).body
                        if isinstance(node, ast.FunctionDef) and node.name == "_persist")
        body = ast.get_source_segment(source, function)
        self.assertIn('certificates.keep_skipped(doc, plan.get("raw_pages") or [], policy)', body)
        self.assertIn("document_policy=policy", body)
        self.assertLess(body.index("certificates.keep_skipped"), body.index("store.save(doc)"))
        # 计费口径不变：没有成票就不计费，参考记录不算票
        self.assertIn('doc.billing_state = "pending" if rows else "not_billable"', body)


if __name__ == "__main__":
    unittest.main()
