"""销项定时任务的默认收入科目（用户 2026-10-20「定时任务给收入科目」）。不连库、不依赖 Frappe。

- 任务表单：销项任务（服务、存货）的默认处理多一项 ``account``（收入科目），与进项费用科目同一个键、同一份存法；
  只能选这份账套科目表里的收入类（Express 科目类型 4，没报类型的按编号首位 4）；MR.ERP 任务照旧没有默认处理；
- 推送：任务设了就记它，来源 ``task_default``，自动推判档算 A；没设照连接的默认收入科目（``config_default``，B）；
  科目不在这份账套科目表里、或科目映射已定的不用；
- 进项不受影响。
"""
import datetime as dt
import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import test_cowork_da_automation as da  # noqa: E402  先装好 pearnly_cowork 的导入路径（同一份纯模块）
import test_cowork_folder_tasks_mrerp as harness  # noqa: E402

from pearnly_cowork.push.express.sales_mapper import build_express_sales_payload  # noqa: E402

auto, choice_rules, decide_rules, contracts = da.auto, da.choice_rules, da.decide_rules, harness.contracts

CHART = [{"code": "4100-01", "name": "รายได้จากการขาย", "type": "4"},
         {"code": "4200-01", "name": "รายได้ค่าบริการ"},
         {"code": "5100-01", "name": "ค่าเช่า", "type": "5"},
         {"code": "11-02-01-00", "name": "ลูกหนี้การค้า", "type": "1"}]
CONFIG = {"account_set": da.BOOK, "revenue_acc": "4900-00", "vat_output_acc": "2150-00", "ar_acc": "11-02-01-00",
          "reported_accounts": CHART}
CUSTOMER = "0105551234567"


def _sales_fields(**over):
    return da._fields(direction="sales", seller_name="ACME", seller_tax=da.OWN, buyer_name="บริษัท ลูกค้า จำกัด",
                      buyer_tax=CUSTOMER, **over)


def _push(fixed, *, config=CONFIG, account_resolved=False):
    """推送准备同一条路：判定核心 → 选择放进 config → 销项映射器 → 第 1 条分档。"""
    fields = _sales_fields()
    decision, _sources = decide_rules.decide(fields, direction="sales", posting_kind="service", config=config,
                                             remembered={}, fixed=fixed, account_resolved=account_resolved)
    history = {"id": "H1", "invoice_date": "2026-10-20", "invoice_no": "IV69-001", "total_amount": "1070.00",
               "fields": fields}
    result = build_express_sales_payload(history, config={**config, "choices": decision["applied"]},
                                         today=dt.date(2026, 10, 25), posting_kind="service")
    assert result.ok, result.reason
    facts = da._facts(direction="sales", fields=fields, payload=result.payload, decision=decision, config=config)
    return decision, result.payload, auto.tiers(facts)


class TaskForm(unittest.TestCase):
    def test_sales_tasks_get_an_income_account_and_purchases_are_unchanged(self):
        for kind in ("service", "stock"):
            self.assertIn("account", contracts.applicable("sales", kind))
        self.assertIn("account", contracts.applicable("purchase", "service"))
        self.assertNotIn("account", contracts.applicable("purchase", "stock"))

    def test_income_accounts_follow_the_express_account_type(self):
        self.assertEqual(contracts.income_codes(CHART), {"4100-01", "4200-01"})
        self.assertIsNone(contracts.income_codes(None))
        self.assertFalse(contracts.income_account({"code": "4100-01", "type": "5"}))  # 类型说了算
        self.assertFalse(contracts.income_account({"code": ""}))

    def test_saving_a_sales_task_accepts_only_an_income_account(self):
        case = harness.SaveMrerpTask()
        rules, saved, masters = case._rules("express")
        masters.return_value = {"reported_accounts": CHART}
        values = case.values(direction="sales", posting_kind="service")
        rules.save(json.dumps({**values, "defaults": {"account": "4100-01"}}))
        self.assertEqual(json.loads(saved[-1]["defaults_json"]), {"account": "4100-01"})
        rules.save(json.dumps({**values, "posting_kind": "stock", "defaults": {"account": "4200-01"}}))
        self.assertEqual(json.loads(saved[-1]["defaults_json"]), {"account": "4200-01"})
        for wrong in ("5100-01", "11-02-01-00", "9999"):
            with self.assertRaises(Exception):
                rules.save(json.dumps({**values, "defaults": {"account": wrong}}))
        # 没设：照旧没有这一项（推送照连接的默认收入科目）
        rules.save(json.dumps(values))
        self.assertEqual(json.loads(saved[-1]["defaults_json"]), {})
        # 进项不受影响：费用科目照旧只核在不在科目表里
        rules.save(json.dumps(case.values(posting_kind="service", defaults={"account": "5100-01"})))
        self.assertEqual(json.loads(saved[-1]["defaults_json"]), {"account": "5100-01"})
        # 表单选项：收入类标 income
        view = rules.options("C1", "Y1")
        self.assertEqual([row["code"] for row in view["accounts"] if row.get("income")], ["4100-01", "4200-01"])

    def test_mrerp_tasks_still_have_no_defaults(self):
        case = harness.SaveMrerpTask()
        rules, saved, _masters = case._rules("mrerp")
        rules.save(json.dumps(case.values(direction="sales", payment="cash", defaults={"account": "4100-01"})))
        self.assertEqual(json.loads(saved[-1]["defaults_json"]), {})


class Push(unittest.TestCase):
    def test_a_task_income_account_is_used_and_classed_a(self):
        decision, payload, tiers = _push({"account": "4100-01"})
        self.assertEqual(decision["applied"]["account"], "4100-01")
        self.assertEqual(decision["decided_by"].get("account"), "task_default")
        self.assertEqual(decision["needed"], [])
        revenue = [line for line in payload["lines"] if line["side"] == "C"][0]
        self.assertEqual((revenue["acc"], payload["items_account"], payload["account_source"], payload["account_review"]),
                         ("4100-01", "4100-01", "task_default", False))
        self.assertEqual(tiers["account"], "A")

    def test_without_a_task_income_account_the_connection_default_stays_b(self):
        for fixed in (None, {}, {"department": "HQ"}):
            decision, payload, tiers = _push(fixed)
            self.assertNotIn("account", decision["applied"])
            self.assertEqual((payload["items_account"], payload["account_source"], tiers["account"]),
                             ("4900-00", "config_default", "B"), fixed)

    def test_an_account_missing_from_the_chart_or_a_category_mapping_is_not_overridden(self):
        _decision, payload, tiers = _push({"account": "4300-01"})
        self.assertEqual((payload["account_source"], tiers["account"]), ("config_default", "B"))
        decision, _payload, _tiers = _push({"account": "4100-01"}, account_resolved=True)
        self.assertNotIn("account", decision["applied"])
        # 科目表还没读到：先用任务的（同进项），领单时再按主档核
        decision, payload, _tiers = _push({"account": "4100-01"}, config={**CONFIG, "reported_accounts": []})
        self.assertEqual(payload["items_account"], "4100-01")

    def test_purchases_keep_the_expense_account_rules(self):
        fields = da._fields()
        config = {"reported_accounts": [{"code": "5310-01"}]}
        decision, _ = decide_rules.decide(fields, direction="purchase", posting_kind="service", config=config,
                                          remembered={}, fixed={"account": "5310-01"})
        self.assertEqual((decision["applied"]["account"], decision["applied"]["account_from"]), ("5310-01", "task_default"))
        decision, _ = decide_rules.decide(fields, direction="purchase", posting_kind="service", config=config,
                                          remembered={})
        self.assertIn("account", [item["kind"] for item in decision["needed"]])


if __name__ == "__main__":
    unittest.main()
