"""CC 覆盖总表闸（引导侧）：一份数据、总表由它生成、与引导声明对得上（``scripts/check-cc-coverage.py``；总设计
``docs/work/2026-11-14-cc-guided-master-design.md`` 第 4 节，P0 第 1 个提交）。

断言的是应有状态：
- 一格只有六种写法，范围外必须写依据；
- 标「做完 / 做了一半」的格，这个产品这个模式必须声明了这项业务；「做完」不能是只有开场一句的引导，也要面向那种账本；
  声明了的格不许标范围外；
- 仓库里的数据过闸，总表文件里的那一段与数据生成的一致；
- 现状如实：还没有一格做完；收款只在 ERP 直写；代录只有采购、销售两项业务在范围内。

    python3 tests/test_cc_coverage.py
"""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_cc_coverage", ROOT / "scripts/check-cc-coverage.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

GUIDES = {"pearnly_cowork": [{"id": "purchase", "modes": ["chat", "operator"], "source": "documents", "ledgers": ["express_local"]}],
          "pearnly_erp_portal": [{"id": "collection", "modes": ["chat"], "source": "chat", "ledgers": ["express_local", "mrerp"]}]}
OUT = ["out:not here"]


def row(**cells):
    base = {"id": "purchase", "name": "Purchase", "guide": "purchase", "cowork_direct": OUT, "cowork_operator": OUT,
            "erp_direct": OUT, "erp_operator": OUT}
    return {**base, **cells}


class CellTests(unittest.TestCase):
    def test_the_six_ways_to_write_a_cell(self):
        self.assertEqual(gate.parse("done"), ("done", "", ""))
        self.assertEqual(gate.parse("half:p2"), ("half", "p2", ""))
        self.assertEqual(gate.parse("todo:p4"), ("todo", "p4", ""))
        self.assertEqual(gate.parse("no:p2:desktop"), ("no", "p2", "desktop"))
        self.assertEqual(gate.parse("wait:cloud"), ("wait", "", "cloud"))
        self.assertEqual(gate.parse("out:只在 ERP"), ("out", "", "只在 ERP"))

    def test_anything_else_is_refused(self):
        for bad in ("done:p1", "half", "todo:p9", "no:p2", "wait:someone", "out:", "out", "maybe"):
            self.assertRaises(ValueError, gate.parse, bad)


class RuleTests(unittest.TestCase):
    def test_done_or_half_needs_a_declared_guide_for_that_product_and_mode(self):
        self.assertEqual(gate.check([row(cowork_direct=["half:p2", "wait:mrerp", "wait:cloud"], cowork_operator=["todo:p2"])],
                                    GUIDES), [])
        errors = gate.check([row(erp_operator=["half:p2", "wait:mrerp", "wait:cloud"], cowork_direct=["todo:p2"],
                                 cowork_operator=["todo:p2"])], GUIDES)
        self.assertEqual(len(errors), 1)
        self.assertIn("purchase.erp_operator[express_local]: marked half but pearnly_erp_portal declares no guide", errors[0])

    def test_done_needs_a_complete_guide_that_covers_the_ledger(self):
        chat = {"id": "collection", "name": "Collection", "guide": "collection", "cowork_direct": OUT, "cowork_operator": OUT,
                "erp_direct": ["done", "wait:mrerp", "wait:cloud"], "erp_operator": OUT}
        self.assertTrue(any("only has an opening line" in e for e in gate.check([chat], GUIDES)))
        ledger = row(cowork_direct=["done", "done", "wait:cloud"], cowork_operator=["todo:p2"])
        self.assertEqual(gate.check([ledger], GUIDES),
                         ["purchase.cowork_direct[mrerp]: marked done but the guide does not cover mrerp"])

    def test_a_declared_guide_cannot_be_marked_out_of_scope(self):
        errors = gate.check([row(cowork_operator=["todo:p2"])], GUIDES)
        self.assertEqual(errors, ["purchase.cowork_direct: marked out of scope but pearnly_cowork declares this guide for chat"])

    def test_a_group_has_one_cell_or_three_and_a_row_a_unique_id(self):
        self.assertTrue(any("one cell" in e for e in gate.check([row(erp_direct=["todo:p2", "wait:mrerp"])], {})))
        self.assertTrue(any("unique id" in e for e in gate.check([row(guide=None), row(guide=None)], {})))


class RepositoryTests(unittest.TestCase):
    def setUp(self):
        self.rows = gate.load()
        self.guides, self.errors = gate.guides_gate.load()

    def test_the_data_passes_and_the_table_in_the_matrix_is_generated_from_it(self):
        self.assertEqual(self.errors, [])
        self.assertEqual(gate.check(self.rows, self.guides), [])
        self.assertEqual(gate.doc_section(gate.DOC.read_text(encoding="utf-8")), gate.render(self.rows))

    def test_the_state_is_reported_as_it_is(self):
        found = gate.counts(self.rows)
        self.assertEqual(found["done"], 0)
        self.assertEqual(found["in_scope"], sum(v for k, v in found.items() if k != "in_scope"))
        by_id = {r["id"]: r for r in self.rows}
        self.assertTrue(all(gate.parse(c)[0] == "out" for g in ("cowork_direct", "cowork_operator", "erp_operator")
                            for c in by_id["collection"][g]))
        in_operator = [r["id"] for r in self.rows for g in ("cowork_operator", "erp_operator")
                       if any(gate.parse(c)[0] != "out" for c in r[g])]
        self.assertEqual(sorted(set(in_operator)), ["documents", "new_master", "purchase", "resolve", "sales"])


if __name__ == "__main__":
    unittest.main()
