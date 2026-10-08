# -*- coding: utf-8 -*-
"""回传云端的 meta 守门。

meta 是云端唯一能知道「ERP 里到底发生了什么」的通道 —— 导出给会计的复核表全靠它。
少一个字段,会计就只能看到"新建了个客户"却不知道码是多少,没法去 ERP 清理误建的档。
"""

import unittest

from companion.worker import _push_meta
from companion.write_result import DbfWriteResult


def _res(**kw):
    """真产物构造结果,不用 SimpleNamespace 桩 —— 桩造得出产品里不存在的形状(比如漏掉
    written),测试照样绿,而线上第一张票就 AttributeError。"""
    base = {
        "ok": True,
        "docnum": "SA1-0722",
        "party_code": "ก001",
        "created_party": False,
        "error_code": "",
        "backup_dir": None,
    }
    base.update(kw)
    return DbfWriteResult(**base)


ACCT = r"\\accserver\ACCOUNT\70EXP\TEST"


class PartyCodeTests(unittest.TestCase):
    def test_party_code_reported(self):
        m = _push_meta(_res(), "sales", ACCT)
        self.assertEqual(m["party_code"], "ก001")

    def test_party_code_reported_for_purchase_too(self):
        m = _push_meta(_res(party_code="ผ012"), "purchase", ACCT)
        self.assertEqual(m["party_code"], "ผ012")

    def test_absent_party_code_is_omitted_not_blank(self):
        """没有就不报 —— 报个空串会让下游以为"码就是空的"。"""
        self.assertNotIn("party_code", _push_meta(_res(party_code=""), "sales", ACCT))
        self.assertNotIn("party_code", _push_meta(_res(party_code=None), "sales", ACCT))


class DirectionTests(unittest.TestCase):
    def test_doc_type_carries_direction(self):
        """云端按这个字段把行分进销项/进项表 —— 分错了会计看到的方向就是反的。"""
        self.assertEqual(_push_meta(_res(), "sales", ACCT)["doc_type"], "sales")
        self.assertEqual(_push_meta(_res(), "purchase", ACCT)["doc_type"], "purchase")

    def test_created_party_key_follows_direction(self):
        self.assertIn("created_customer", _push_meta(_res(), "sales", ACCT))
        self.assertIn("created_supplier", _push_meta(_res(), "purchase", ACCT))


class DuplicateSkippedTests(unittest.TestCase):
    """「真写进去了」与「本来就有,什么都没做」必须分得开。

    两种情况都是 ok=True 且 docnum 有值,推送日志上长得一模一样。会计要判"到底进没进",
    只剩去 ERP 里翻这一条路 —— 2026-07-25 现场就是这样,幂等闸在软删行上误命中,
    连报三次"成功"没人看出来。
    """

    def test_fresh_write_is_reported_as_not_skipped(self):
        self.assertIs(
            _push_meta(_res(skipped=False), "sales", ACCT)["duplicate_skipped"], False
        )

    def test_idempotent_hit_is_reported(self):
        self.assertIs(
            _push_meta(_res(skipped=True), "sales", ACCT)["duplicate_skipped"], True
        )

    def test_failed_push_does_not_claim_either_way(self):
        self.assertNotIn("duplicate_skipped", _push_meta(_res(ok=False), "sales", ACCT))


class WrittenFlagTests(unittest.TestCase):
    """`written` 是「到底进没进去」的唯一判据,且**每条返回路径都要有**。

    duplicate_skipped 只在 ok 分支上报 —— 失败/拒单时调用方连它都拿不到,只能猜。
    2026-07-27 靶场 S3 的谎(ok=true + 零写入)就是从"没有一个字段直说没写"长出来的。
    """

    def test_fresh_write_says_written(self):
        self.assertIs(_push_meta(_res(), "sales", ACCT)["written"], True)

    def test_idempotent_hit_says_not_written_even_though_ok(self):
        m = _push_meta(_res(skipped=True), "sales", ACCT)
        self.assertIs(m["written"], False)
        self.assertIs(m["duplicate_skipped"], True)

    def test_failed_push_still_reports_written(self):
        self.assertIs(_push_meta(_res(ok=False), "sales", ACCT)["written"], False)


class FailurePathTests(unittest.TestCase):
    def test_failed_push_has_no_index_requery_claim(self):
        """没成功就不能声称索引回查命中过。"""
        m = _push_meta(_res(ok=False), "sales", ACCT)
        self.assertNotIn("index_requery", m)
        self.assertNotIn("created_customer", m)

    def test_version_and_account_always_present(self):
        m = _push_meta(_res(), "sales", ACCT)
        self.assertTrue(m["companion_version"])
        self.assertEqual(m["account_dir"], ACCT)


if __name__ == "__main__":
    unittest.main()
