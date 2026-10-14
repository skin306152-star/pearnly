"""简式税票含税价拆税（2026-10-15）：识别存档把税额读成 0 / 没写的 ABB 按 7/107 拆出，销项、进项一样拆；
进项拆出后抵不抵仍走「小票 / 简式税票问一次抵不抵进项」。纯离线，不需要 Frappe、pydantic 或网络。

识别存档那一步（`ocr.results.prepare_history_batch`）的整链测试在 `tests/test_cowork_invoice_results.py`（要 pydantic）。
"""
from copy import deepcopy
from datetime import date
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_cowork"))
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

from pearnly_cowork.ocr import pages  # noqa: E402
from pearnly_cowork.push.express import choices as C  # noqa: E402
from pearnly_cowork.push.express.mapper import build_express_payload  # noqa: E402
from pearnly_cowork.push.express.sales_mapper import build_express_sales_payload  # noqa: E402

D2 = "0105560000001"
SUPPLIER = "0105536080112"
TODAY = date(2026, 10, 15)


def abb(**fields):
    """D2 自开的手写 ABB（2026-10-13 testcowork 教程录制的识别结果）：合计 727.00，税额读成 "0"。"""
    return {"document_type": "simplified_tax_invoice", "invoice_number": "ABB-0042", "date": "2026-10-13",
            "seller_name": "D2", "seller_tax": D2, "buyer_name": "", "buyer_tax": "",
            "subtotal": "727.00", "vat": "0", "total_amount": "727.00", "discount": "", "wht_amount": "",
            "currency": "", "notes": "ABB / VAT INCLUDED", "is_not_invoice": False,
            "items": [{"name": "ข้าวมันไก่", "qty": "1", "price": "727.00", "subtotal": "727.00"}], **fields}


def page(fields, **extra):
    return {"page_number": 1, "page_index": 1, "fields": fields, "_extraction_audit": {}, **extra}


def split(fields, **extra):
    entry = page(deepcopy(fields), **extra)
    changed = pages.split_included_vat([entry])
    return changed, entry


class SplitOnRecognizedPages(unittest.TestCase):
    def test_the_reported_sales_abb_becomes_base_plus_output_vat(self):
        changed, entry = split(abb(direction="sales"))
        self.assertEqual(changed, 1)
        fields = entry["fields"]
        self.assertEqual((fields["subtotal"], fields["vat"], fields["total_amount"]), ("679.44", "47.56", "727.00"))
        # 原读数只进排查用的审计块；不加提示、不改复核标记
        self.assertEqual(entry["_extraction_audit"], {"vat_included": {"subtotal": "727.00", "vat": "0"}})
        self.assertNotIn("_validation_warnings", entry)
        self.assertNotIn("_needs_manual_review", entry)
        # 再跑一遍不再改（已经有税额）
        self.assertEqual(pages.split_included_vat([entry]), 0)
        self.assertEqual((fields["subtotal"], fields["vat"]), ("679.44", "47.56"))

    def test_vat_missing_or_written_as_zero_string(self):
        for values in ({"vat": "0"}, {"vat": "0.00"}, {"vat": ""}, {"vat": None}, {"vat": "", "subtotal": ""},
                       {"subtotal": "727", "vat": "0"}):
            with self.subTest(values):
                changed, entry = split(abb(**values))
                self.assertEqual((changed, entry["fields"]["subtotal"], entry["fields"]["vat"]), (1, "679.44", "47.56"))

    def test_already_split_full_tax_invoices_and_other_documents_are_left_alone(self):
        cases = (abb(subtotal="557.94", vat="39.06", total_amount="597.00"),  # 同一本另三张：模型自己拆对了
                 abb(document_type="tax_invoice"),
                 abb(document_type="receipt"),
                 abb(currency="USD"),
                 abb(subtotal="700.00"),
                 abb(is_not_invoice=True),
                 abb(is_not_invoice="true"))
        for fields in cases:
            with self.subTest(fields):
                changed, entry = split(fields)
                self.assertEqual((changed, entry["fields"]), (0, fields))
                self.assertEqual(entry["_extraction_audit"], {})

    def test_a_document_marked_exempt_or_zero_rated_is_left_alone(self):
        cases = (abb(notes="สินค้ายกเว้นภาษีมูลค่าเพิ่ม"),
                 abb(items=[{"name": "ผักสด (NON-VAT)", "subtotal": "727.00"}]),
                 abb(notes="", source_refs={"vat": {"value": "0", "source_text": "VAT 0%", "source_column": "VAT"}}),
                 abb(source_refs={"total_amount": {"value": "727", "source_text": "727.00",
                                                   "source_column": "รวมเงิน (อัตราศูนย์)"}}))
        for fields in cases:
            with self.subTest(fields):
                self.assertEqual(split(fields)[0], 0)
        # 「VAT INCLUDED」「รวมภาษีมูลค่าเพิ่มแล้ว」是含税的说明，不是免税
        included = abb(source_refs={"total_amount": {"value": "727", "source_column": "รวมเงิน",
                                                     "source_text": "รวมเงิน (รวมภาษีมูลค่าเพิ่มแล้ว) 727.00"}})
        self.assertEqual(split(included)[0], 1)

    def test_a_vat_printed_as_zero_on_the_document_is_left_alone(self):
        """审查 2026-10-15：全免税商品的简易税票印着 VAT 0.00，识别把栏名「VAT 7%」与原文「0.00」分开记。"""
        exempt = abb(notes="", vat="0.00", source_refs={"vat": {"value": "0", "source_text": "0.00",
                                                                "source_column": "VAT 7%"}})
        changed, entry = split(exempt)
        self.assertEqual((changed, entry["fields"]), (0, exempt))
        self.assertEqual(pages.included_vat(exempt).reason, "vat_printed_zero")
        # 栏名与原文拼成一条再认免税字样：「VAT」+「0%」
        joined = abb(notes="", source_refs={"vat": {"value": "0", "source_text": "0%", "source_column": "VAT"}})
        self.assertEqual(pages.included_vat(joined).reason, "no_vat_marked")
        self.assertIn("VAT 0%", pages.printed_texts(joined))

    def test_the_handwritten_abb_without_a_printed_vat_is_still_split(self):
        # D2 03/0102 那种：票面没印税额、字段被模型填 0 —— 本批修复的原案例，仍要拆
        for refs in (None, {}, {"vat": {"value": "0", "source_text": "", "source_column": "VAT"}},
                     {"vat": {"value": "0", "source_text": "-", "source_column": ""}},
                     {"vat": {"value": "0", "source_column": "VAT 7%"}},
                     {"total_amount": {"value": "727", "source_text": "727.00", "source_column": "รวมเงิน"}}):
            with self.subTest(refs=refs):
                changed, entry = split(abb(source_refs=refs))
                self.assertEqual((changed, entry["fields"]["subtotal"], entry["fields"]["vat"]), (1, "679.44", "47.56"))

    def test_each_page_of_the_invoice_is_split_and_other_audit_is_kept(self):
        first, second = page(abb()), page(abb(), _extraction_audit={"amount_source": "column"})
        third = page({"is_not_invoice": True, "document_type": "simplified_tax_invoice", "total_amount": "5"})
        self.assertEqual(pages.split_included_vat([first, second, third, {"fields": None}, "junk"]), 2)
        self.assertEqual(second["_extraction_audit"]["amount_source"], "column")
        self.assertEqual(second["fields"]["vat"], "47.56")
        self.assertEqual(third["fields"]["total_amount"], "5")


class PushAfterTheSplit(unittest.TestCase):
    CONFIG = {"account_set": "DATAT", "revenue_acc": "41-01-00-00", "vat_output_acc": "21-05-01-00",
              "ar_acc": "11-02-01-00", "fallback_acc": "53-01-00-00", "vat_input_acc": "11-05-04-01",
              "ap_acc": "21-02-01-00"}

    def history(self, fields):
        return {"id": "hist-abb", "invoice_date": fields["date"], "invoice_no": fields["invoice_number"],
                "total_amount": fields["total_amount"], "fields": fields}

    def test_sales_records_output_vat_47_56(self):
        _, entry = split(abb(direction="sales"))
        result = build_express_sales_payload(self.history(entry["fields"]), config=self.CONFIG, today=TODAY)
        self.assertTrue(result.ok, result.reason)
        payload = result.payload
        self.assertEqual((payload["base_amount"], payload["vat_amount"], payload["total_amount"]),
                         ("679.44", "47.56", "727.00"))
        self.assertIn({"acc": "21-05-01-00", "side": "C", "amount": "47.56", "desc": "ภาษีขาย"}, payload["lines"])
        # 改之前：全额记收入、销项税 0（ภ.พ.30 少 47.56）
        before = build_express_sales_payload(self.history(abb(direction="sales")), config=self.CONFIG, today=TODAY)
        self.assertEqual((before.payload["base_amount"], before.payload["vat_amount"]), ("727.00", "0.00"))

    def purchase(self):
        return abb(direction="purchase", seller_name="ร้านข้าวมันไก่", seller_tax=SUPPLIER,
                   buyer_name="D2", buyer_tax=D2, posting_payment_manual="cash")

    def test_purchase_is_split_too_and_still_asks_once_whether_to_claim(self):
        _, entry = split(self.purchase())
        fields = entry["fields"]
        self.assertEqual((fields["subtotal"], fields["vat"]), ("679.44", "47.56"))
        result = C.resolve(fields, direction="purchase", posting_kind="service", config={}, remembered={},
                           account_resolved=True)
        self.assertEqual([item["kind"] for item in result["needed"]], ["vat"])
        # 改之前税额是 0：根本不会问
        before = C.resolve(self.purchase(), direction="purchase", posting_kind="service", config={}, remembered={},
                           account_resolved=True)
        self.assertEqual(before["needed"], [])
        # 选过的照用：不抵 → 含税全额记费用（同改之前）；抵 → 记进项税 47.56
        subject = result["needed"][0]["subject"]
        for value, base, vat in (("expense", "727.00", "0.00"), ("claim", "679.44", "47.56")):
            with self.subTest(value):
                applied = C.resolve(fields, direction="purchase", posting_kind="service", config={},
                                    remembered={"vat": {subject: value}}, account_resolved=True)["applied"]
                self.assertEqual(applied["vat"], value)
                payload = build_express_payload(self.history(fields), config={**self.CONFIG, "choices": applied},
                                                today=TODAY).payload
                self.assertEqual((payload["base_amount"], payload["vat_amount"]), (base, vat))


class ReplayScript(unittest.TestCase):
    SCRIPT = ROOT / "scripts/cowork-vat-included-replay.py"

    def run_script(self, directory, *args):
        done = subprocess.run([sys.executable, str(self.SCRIPT), str(directory), *args], capture_output=True,
                              text=True, encoding="utf-8")
        return done.returncode, done.stdout, done.stderr

    def test_lists_only_zero_vat_abbreviated_invoices_and_reads_nothing_else(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "runs").mkdir()
            # eval-invoices.py 的 results.jsonl：invoices[].fields 只有金额与票种
            records = [{"编号": "S001", "state": "saved", "invoices": [
                           {"history": "H-1", "fields": {"document_type": "simplified_tax_invoice", "invoice_number": "A1",
                                                         "subtotal": "727.00", "vat": "0", "total_amount": "727.00"}},
                           {"history": "H-2", "fields": {"document_type": "simplified_tax_invoice", "invoice_number": "A2",
                                                         "subtotal": "557.94", "vat": "39.06", "total_amount": "597.00"}},
                           {"history": "H-3", "fields": {"document_type": "tax_invoice", "invoice_number": "T1",
                                                         "subtotal": "100", "vat": "0", "total_amount": "100"}}]},
                       {"编号": "S002", "state": "not_invoice", "invoices": []}]
            (root / "runs/results.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records),
                                                     encoding="utf-8")
            # 识别记录导出：pages 是 JSON 字符串，ai_raw 是副本（不重复计）
            pages_json = json.dumps([page(abb(notes="ผักสด ยกเว้นภาษี"))], ensure_ascii=False)
            (root / "history.json").write_text("﻿" + json.dumps(
                {"name": "H-9", "pages": pages_json, "ai_raw": pages_json}, ensure_ascii=False), encoding="utf-8")
            # PipelineResult：同页第二张票在 additional_invoices 里
            (root / "result.json").write_text(json.dumps({"pages": [{"page_number": 1, "invoice": {
                **abb(invoice_number="P1"), "additional_invoices": [abb(invoice_number="P2", vat="", subtotal="")]}}]},
                ensure_ascii=False), encoding="utf-8")
            (root / "broken.json").write_text("{not json", encoding="utf-8")
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

            code, out, _ = self.run_script(root)
            self.assertEqual(code, 0)
            lines = out.strip().split("\n")
            self.assertEqual(lines[0].split("\t")[:3], ["file", "where", "id"])
            changed = [line.split("\t") for line in lines[1:] if line and not line.startswith("{")]
            self.assertEqual(sorted(row[2] for row in changed),
                             ["invoice=P1", "invoice=P2", "编号=S001 history=H-1 invoice=A1"])
            row = next(row for row in changed if "A1" in row[2])
            self.assertEqual(row[5:9], ["727.00", "727.00→679.44", "0→47.56", "no"])
            summary = json.loads(lines[-1])["summary"]
            self.assertEqual((summary["would_change"], summary["abbreviated"], summary["unreadable_files"]), (3, 5, 1))
            self.assertEqual(summary["unchanged_abbreviated"], {"vat_present": 1, "no_vat_marked": 1})
            self.assertEqual(summary["would_change_without_document_text"], 1)

            code, out, _ = self.run_script(root, "--all", "--json")
            rows = [json.loads(line) for line in out.strip().split("\n")]
            self.assertEqual(sorted(row["reason"] for row in rows),
                             ["no_vat_marked", "split", "split", "split", "vat_present"])
            # 只读：一个字节都没改
            self.assertEqual({path: path.read_bytes() for path in root.rglob("*") if path.is_file()}, before)

    def test_a_missing_or_empty_directory_exits_2(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(self.run_script(Path(folder))[0], 2)
            self.assertEqual(self.run_script(Path(folder) / "nope")[0], 2)


if __name__ == "__main__":
    unittest.main()
