"""复核卡的票面与标记（`pearnly_core.write_precheck.review`；第 2 批识别后复核卡，设计 0.2 / 0.3，主会话 11-10 审定）。

锁住：票面行的形状；标记只标不拦（税号校验位、分店位数、未税 + 税额 ≠ 合计、税额与 7% 差 1 泰铢以上、明细金额 / 明细合计），
写不进 Express 的才挡（单号空、日期不是日期、金额不是数字）；佛历日期换公历；改一格后标记重算（改对了标记消失）；不能改的格是
程序错；确认后又改过的票不写。纯函数，直接载入真模块。
"""
import importlib.util
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


for package in ("pearnly_core", "pearnly_core.tax_documents", "pearnly_core.write_precheck"):
    sys.modules.setdefault(package, types.ModuleType(package))
_load("pearnly_core.tax_documents.amounts", CORE / "tax_documents/amounts.py")
review = _load("pearnly_core.write_precheck.review", CORE / "write_precheck/review.py")

GOOD_TAX = "0105561077332"  # 校验位对
ROW = {"doc_no": "IV69/00751", "party_name": "บมจ.บิ๊กซี", "party_tax": GOOD_TAX, "party_branch": "00000", "date": "2026-10-01",
       "subtotal": "268.00", "vat": "18.76", "total": "286.76",
       "lines": [{"description": "น้ำแข็งหลอด", "qty": "4", "unit_price": "67.00", "amount": "268.00"}]}


class FaceTests(unittest.TestCase):
    def test_a_face_keeps_known_cells_and_turns_a_buddhist_date_into_gregorian(self):
        row = review.face({**ROW, "date": "2569-10-01", "pages": "x", "lines": [{**ROW["lines"][0], "other": 1}]})
        self.assertEqual(set(row), set(review.FIELDS) | {"lines"})
        self.assertEqual(row["date"], "2026-10-01")
        self.assertEqual(set(row["lines"][0]), set(review.LINE_FIELDS))

    def test_a_clean_ticket_has_no_marks(self):
        self.assertEqual(review.check(review.face(ROW)), {"marks": [], "cells": {}, "blocking": []})


class MarkTests(unittest.TestCase):
    def marks(self, **change):
        return review.check(review.face({**ROW, **change}))

    def test_tax_id_and_branch(self):
        self.assertEqual(self.marks(party_tax="0105561077333")["cells"], {"party_tax": "tax_id"})  # 校验位错
        self.assertEqual(self.marks(party_tax="010556107733")["cells"], {"party_tax": "tax_id"})  # 12 位
        self.assertEqual(self.marks(party_branch="0001")["cells"], {"party_branch": "tax_id"})
        self.assertEqual(self.marks(party_tax="", party_branch="")["marks"], [])  # 没有的不标（简易税票）

    def test_amounts_are_marked_not_blocked(self):
        out = self.marks(total="290.00")
        self.assertEqual((out["cells"], out["blocking"]), ({"total": "amount"}, []))
        self.assertEqual(self.marks(vat="25.00", total="293.00")["cells"], {"vat": "amount"})  # 与 7% 差 1 泰铢以上
        self.assertEqual(self.marks(vat="18.00", total="286.00")["marks"], [])  # 差 1 泰铢内算平
        out = self.marks(lines=[{**ROW["lines"][0], "amount": "260.00"}])
        self.assertEqual(out["cells"], {"lines.0.amount": "amount", "subtotal": "amount"})

    def test_what_cannot_go_into_express_blocks_that_ticket(self):
        out = self.marks(doc_no="", date="31/10", total="abc")
        self.assertEqual(sorted(out["blocking"]), ["date", "doc_no", "total"])
        self.assertEqual(out["marks"], ["number", "amount", "date"])  # 按 MARKS 顺序
        self.assertEqual(self.marks(date="2026-02-30")["blocking"], ["date"])


class EditTests(unittest.TestCase):
    def test_fixing_a_cell_clears_its_mark_and_keeps_what_the_person_typed(self):
        wrong = review.face({**ROW, "total": "290.00"})
        self.assertEqual(review.check(wrong)["marks"], ["amount"])
        fixed, marks = review.edit(wrong, "total", "286.76")
        self.assertEqual((fixed["total"], marks["marks"]), ("286.76", []))
        kept, marks = review.edit(fixed, "party_tax", "123")  # 校验不过也存，只标格子
        self.assertEqual((kept["party_tax"], marks["cells"]), ("123", {"party_tax": "tax_id"}))
        dated, _marks = review.edit(fixed, "date", "2569-11-02")
        self.assertEqual(dated["date"], "2026-11-02")
        lined, marks = review.edit(fixed, "lines.0.qty", "5")
        self.assertEqual(marks["cells"], {"lines.0.amount": "amount"})

    def test_cells_that_cannot_be_changed_are_programming_errors(self):
        for field in ("pages", "lines.3.amount", "lines.0.other", "lines.x.qty", "docnum"):
            with self.assertRaises(review.ReviewError):
                review.edit(review.face(ROW), field, "1")


class ConfirmTests(unittest.TestCase):
    def test_only_a_confirmed_ticket_not_changed_since_is_written(self):
        self.assertTrue(review.still_confirmed(3, 3))
        self.assertFalse(review.still_confirmed(3, 4))  # 确认后又改过
        self.assertFalse(review.still_confirmed(None, 3))  # 没确认过


if __name__ == "__main__":
    unittest.main()
