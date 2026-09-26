# -*- coding: utf-8 -*-
"""防重单闸守门:会计改过票号再回导时,旧单还在就必须拒绝。

既有幂等按 YOUREF+CUSCOD 认。可「纠正错票号」恰恰是复核流程的主要目的之一 ——
号一改就认不出,旧单还躺在 ERP 里、新单再建一张,就是一张票记两遍账。
"""

import unittest

from companion.dbf_sales import ERR_PRIOR_DOC_EXISTS, _docnum_exists


class FakeRec:
    def __init__(self, docnum, deleted=False):
        self.DOCNUM = docnum
        self._deleted = deleted


class FakeTable(list):
    pass


def _patch_is_deleted(monkey_target):
    import companion.dbf_sales as m

    orig = m.dbf.is_deleted
    m.dbf.is_deleted = lambda r: getattr(r, "_deleted", False)
    return orig


class DocnumExistsTests(unittest.TestCase):
    def setUp(self):
        import companion.dbf_sales as m

        self.m = m
        self._orig = m.dbf.is_deleted
        m.dbf.is_deleted = lambda r: getattr(r, "_deleted", False)

    def tearDown(self):
        self.m.dbf.is_deleted = self._orig

    def test_existing_docnum_found(self):
        t = FakeTable([FakeRec("SA1-0723"), FakeRec("SA2-0723")])
        self.assertTrue(_docnum_exists(t, "SA1-0723"))

    def test_absent_docnum_not_found(self):
        t = FakeTable([FakeRec("SA2-0723")])
        self.assertFalse(_docnum_exists(t, "SA1-0723"))

    def test_soft_deleted_does_not_count(self):
        """会计已经在 Express 里删掉了 → 软删标记在 · 必须放行,否则他删了也导不进来。"""
        t = FakeTable([FakeRec("SA1-0723", deleted=True)])
        self.assertFalse(_docnum_exists(t, "SA1-0723"))

    def test_blank_docnum_is_not_a_match(self):
        """没给 prior_docnum 时不该把空串匹上任何行,那会把所有推送都拦死。"""
        t = FakeTable([FakeRec(""), FakeRec("SA1-0723")])
        self.assertFalse(_docnum_exists(t, ""))
        self.assertFalse(_docnum_exists(t, "   "))

    def test_whitespace_tolerant(self):
        t = FakeTable([FakeRec("  SA1-0723  ")])
        self.assertTrue(_docnum_exists(t, "SA1-0723"))


class ErrorCodeTests(unittest.TestCase):
    def test_error_code_is_distinct_and_actionable(self):
        """独立错误码 —— 云端要据此给出"先去 Express 删掉哪一张"的提示,
        混进通用失败码就只能显示"推送失败",会计不知道该做什么。"""
        self.assertEqual(ERR_PRIOR_DOC_EXISTS, "PRIOR_DOC_STILL_IN_ERP")
        from companion.dbf_sales import ERR_CUSTOMER_DUP, ERR_NEW_CUSTOMER

        self.assertNotIn(ERR_PRIOR_DOC_EXISTS, (ERR_CUSTOMER_DUP, ERR_NEW_CUSTOMER))


class BothDirectionsCoveredTests(unittest.TestCase):
    """闸必须销项进项都装。之前只装了销项 —— 进项票忘删旧单再回导照样建重复单。"""

    def test_purchase_writer_has_the_guard(self):
        import inspect

        from companion import dbf_writer

        self.assertIn("check_prior_doc", inspect.getsource(dbf_writer.write_purchase))

    def test_sales_writer_has_the_guard(self):
        import inspect

        from companion import dbf_sales

        self.assertIn("check_prior_doc", inspect.getsource(dbf_sales.write_sale))

    def test_the_gate_itself_reads_prior_docnum_and_gives_the_code(self):
        """两侧共用的那一份闸真读 prior_docnum、真给独立码 —— 光有调用点是空壳。"""
        import inspect

        from companion import write_gates

        src = inspect.getsource(write_gates.check_prior_doc)
        self.assertIn("prior_docnum", src)
        self.assertIn("ERR_PRIOR_DOC_EXISTS", src)

    def test_duplicate_check_runs_before_the_prior_doc_gate(self):
        """闸序:查重先说话。反了 = 重投变硬失败,还叫会计去删一张好单(桥侧同款断言)。"""
        import inspect

        from companion import dbf_sales, dbf_writer

        for fn in (dbf_writer.write_purchase, dbf_sales.write_sale):
            src = inspect.getsource(fn)
            self.assertLess(
                src.index("check_duplicate"),
                src.index("check_prior_doc"),
                f"{fn.__name__} 的防重单闸跑在查重之前",
            )

    def test_single_shared_helper_and_code(self):
        """两侧共用一份 _docnum_exists 和一个错误码 —— 各写一份迟早行为会漂。"""
        from companion import dbf_sales, dbf_writer

        self.assertIs(dbf_sales._docnum_exists, dbf_writer._docnum_exists)
        self.assertIs(dbf_sales.ERR_PRIOR_DOC_EXISTS, dbf_writer.ERR_PRIOR_DOC_EXISTS)


if __name__ == "__main__":
    unittest.main()
