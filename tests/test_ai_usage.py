"""Anthropic 用量与费用账本（`pearnly_core.ai_usage`，2026-11-11）：价目三份一致、CC 对话按会话累计写当天增量（幂等、跨天、
官方 list_cost 优先）、标题 / 代录按次记、识别直接用结果里的美元且同一次识别只进账一次、报表与跨站点合计。不跑真模型。"""
import ast
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))


class Cache:
    def __init__(self):
        self.values = {}

    def get_value(self, key):
        return self.values.get(key)

    def set_value(self, key, value, expires_in_sec=None):
        self.values[key] = value


frappe = types.ModuleType("frappe")
frappe.cache = Cache()
frappe.logger = lambda name=None: types.SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None)
frappe.local = types.SimpleNamespace(site="t1.example")
frappe.conf = {"pearnly_product": "cowork", "pearnly_tenant": "T-2026-0001"}
frappe.session = types.SimpleNamespace(user="a@x.th")
frappe.utils = types.SimpleNamespace(nowdate=lambda: "2026-11-11")
frappe.enqueued = []
frappe.enqueue = lambda method, **kwargs: frappe.enqueued.append((method, kwargs))
sys.modules["frappe"] = frappe

from pearnly_core.ai_usage import contracts, ledger, prices, report  # noqa: E402
from pearnly_core.express_operator import ai  # noqa: E402

HAIKU, OPUS = "claude-haiku-5-5", "claude-opus-5-5"


def _constants(path, names):
    found = {}
    for node in ast.parse((ROOT / path).read_text(encoding="utf-8")).body:
        if isinstance(node, ast.Assign) and (getattr(node.targets[0], "id", "") in names
                                             or isinstance(node.targets[0], ast.Tuple)):
            exec(compile(ast.Module([node], []), path, "exec"), {}, found)  # noqa: S102 — 只取常量
    return found


class Prices(unittest.TestCase):
    def test_the_three_price_tables_agree_until_they_are_merged(self):
        # 技术债（11-11 主会话定）：识别引擎与代录各留一份，下次动识别时并成 ai_usage.prices；在那之前三份必须一样
        engine = _constants("apps/pearnly_core/pearnly_core/claude_ocr/engine.py", ("PRICE", "HAIKU", "OPUS"))
        for table in (engine["PRICE"], ai.PRICE):
            for model, row in table.items():
                self.assertEqual(tuple(Decimal(str(v)) for v in row), prices.PRICES[model], model)

    def test_cache_writes_are_a_quarter_over_input_and_the_1h_ones_double(self):
        for model, (pin, _out, pwrite, _read) in prices.PRICES.items():
            self.assertEqual(pwrite, pin * Decimal("1.25"), model)
        self.assertEqual(prices.usd(OPUS, cache_write_1h_tokens=1_000_000), Decimal(8))

    def test_usd_by_the_table(self):
        self.assertEqual(prices.usd(OPUS, 1_000_000, 1_000_000, 1_000_000, 1_000_000), Decimal("29.20"))
        self.assertEqual(prices.usd(HAIKU, 1000, 100), Decimal("0.00015"))
        self.assertIsNone(prices.usd("claude-unknown", 1000, 100))  # 不认识的模型不猜价

    def test_haiku_goes_to_the_high_tier_only_for_one_request_over_100k(self):
        self.assertEqual(prices.usd(HAIKU, 100_001, 0), Decimal("0.050000500"))
        self.assertEqual(prices.usd(HAIKU, 100_001, 0, single_request=False), Decimal("0.0100001"))


class Calls(unittest.TestCase):
    def test_a_messages_call_from_a_dict_or_an_sdk_object(self):
        usage = {"input_tokens": 1000, "output_tokens": 50, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300}
        row = contracts.call(HAIKU, usage)
        self.assertEqual({k: row[k] for k in contracts.TOKENS},
                         {"input_tokens": 1000, "output_tokens": 50, "cache_write_tokens": 200, "cache_read_tokens": 300})
        self.assertEqual(row["usd"], prices.usd(HAIKU, 1000, 50, 200, 300))
        self.assertEqual(row["usd_source"], "price_table")
        sdk = types.SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=None,
                                    cache_creation_input_tokens=70,
                                    cache_creation=types.SimpleNamespace(ephemeral_5m_input_tokens=30,
                                                                         ephemeral_1h_input_tokens=40))
        row = contracts.call(OPUS, sdk)
        self.assertEqual(row["cache_write_tokens"], 70)
        self.assertEqual(row["usd"], prices.usd(OPUS, 10, 5, 30, 0, 40))  # 1 小时的按 × 2
        self.assertIsNone(contracts.call("claude-unknown", usage)["usd"])
        self.assertEqual(contracts.call(HAIKU, None)["input_tokens"], 0)


class Ocr(unittest.TestCase):
    RESULT = {"read": {}, "usd": 0.012346, "stages": [
        {"model": HAIKU, "usd": 0.000123, "seconds": 2.5, "usage": {"input_tokens": 1000, "output_tokens": 100,
                                                                     "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}},
        {"model": OPUS, "usd": 0.012222, "seconds": 8, "usage": {"input_tokens": 2000, "output_tokens": 200,
                                                                  "cache_creation_input_tokens": 10, "cache_read_input_tokens": 5}},
        {"model": HAIKU, "usd": 0.000002, "seconds": 1, "usage": {"input_tokens": 3, "output_tokens": 4}}]}

    def test_the_result_s_own_usd_is_used_split_by_model_and_sums_back_exactly(self):
        rows = contracts.ocr_stages(self.RESULT)
        self.assertEqual([r["model"] for r in rows], [HAIKU, OPUS])
        self.assertEqual(sum(r["usd"] for r in rows), Decimal("0.012346"))  # 结果的美元，一分不差、不重算
        self.assertEqual(rows[0]["input_tokens"], 1003)
        self.assertEqual(rows[1]["cache_write_tokens"], 10)
        self.assertTrue(all(r["usd_source"] == "ocr_result" for r in rows))

    def test_an_old_result_without_stages_is_one_row_and_a_free_one_none(self):
        self.assertEqual([(r["model"], r["usd"]) for r in contracts.ocr_stages({"usd": 0.5})], [("", Decimal("0.5"))])
        self.assertEqual(contracts.ocr_stages({"usd": 0, "stages": []}), [])

    def _client(self):
        work = types.ModuleType("pearnly_core.execution.work")
        work.store = {}
        work.checkpoint = lambda key, state=None, body=None: work.store.get(key) if state is None \
            else work.store.__setitem__(key, {"state": state, "body": body})
        execution = types.ModuleType("pearnly_core.execution")
        execution.work = work
        patcher = mock.patch.dict(sys.modules, {"pearnly_core.execution": execution, "pearnly_core.execution.work": work})
        patcher.start()
        self.addCleanup(patcher.stop)
        from pearnly_core.claude_ocr import client
        return client, work

    def test_one_paid_read_enters_the_ledger_once_and_a_checkpoint_reuse_never(self):
        client, _work = self._client()
        recorded, paid = [], []
        with mock.patch.object(client.ledger, "record", lambda purpose, rows, **kw: recorded.append((purpose, rows, kw))):
            def call():
                paid.append(1)
                return dict(self.RESULT)
            first = client._checkpointed("claude:voucher:abc", call)
            again = client._checkpointed("claude:voucher:abc", call)  # 同一份重跑：用检查点，不付费
        self.assertEqual((len(paid), first.get("where"), again["where"]), (1, None, "checkpoint"))
        self.assertEqual(len(recorded), 1)
        purpose, rows, kw = recorded[0]
        self.assertEqual((purpose, kw["ref"]), ("ocr", "claude:voucher:abc"))
        self.assertEqual(sum(r["usd"] for r in rows), Decimal(str(self.RESULT["usd"])))

    def test_an_unreadable_paid_read_is_still_charged_once(self):
        client, work = self._client()
        recorded = []
        with mock.patch.object(client.ledger, "record", lambda purpose, rows, **kw: recorded.append(rows)):
            client._checkpointed("k", lambda: {"read": None, "usd": 0.3, "stages": []})
        self.assertEqual([[r["usd"] for r in rows] for rows in recorded], [[Decimal("0.3")]])
        self.assertNotIn("k", work.store)  # 读不出不存检查点（下次重读、照付照记）


class Operator(unittest.TestCase):
    def setUp(self):
        frappe.cache.values.clear()
        patcher = mock.patch.dict(os.environ, {"PEARNLY_ANTHROPIC_API_KEY": "k"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_answered_call_is_recorded_once_and_refusals_are_not(self):
        recorded = []
        usage = {"input_tokens": 1000, "output_tokens": 10}
        body = {"batch": "B1", "kind": "turn", "request": {"model": HAIKU, "max_tokens": 100, "messages": []}}
        with mock.patch.object(ai.ledger, "record", lambda purpose, rows, **kw: recorded.append((purpose, rows, kw))):
            ai.forward(body, post=lambda key, request: {"content": [], "usage": usage})
            ai.forward(body, post=lambda key, request: {"error": "anthropic_400", "refused": {"status": 400}})
            ai.forward(body, post=lambda key, request: (_ for _ in ()).throw(OSError()))
        self.assertEqual(len(recorded), 1)
        purpose, rows, kw = recorded[0]
        self.assertEqual((purpose, kw["ref"], rows[0]["model"], rows[0]["input_tokens"]), ("operator", "B1:turn", HAIKU, 1000))
        self.assertEqual(Decimal(str(ai.usd(HAIKU, usage))), rows[0]["usd"])  # 与预算估算同一个数


class Sessions(unittest.TestCase):
    SESSION = {"id": "sesn_1", "agent": {"model": {"id": OPUS}},
               "usage": {"input_tokens": 5000, "output_tokens": 800, "cache_read_input_tokens": 90000,
                         "cache_creation": {"ephemeral_5m_input_tokens": 3000, "ephemeral_1h_input_tokens": 0},
                         "active_seconds": 42.5, "list_cost": {"amount": "137", "currency": "USD"}}}

    def test_the_official_list_cost_wins_and_is_cents(self):
        snap = contracts.session_snapshot(self.SESSION)
        self.assertEqual((snap["model"], snap["usd"], snap["usd_source"]), (OPUS, Decimal("1.37"), "list_cost"))
        self.assertEqual((snap["cache_write_tokens"], snap["cache_read_tokens"], snap["active_seconds"]), (3000, 90000, 42.5))

    def test_without_a_list_cost_the_table_prices_it(self):
        session = json.loads(json.dumps(self.SESSION))
        del session["usage"]["list_cost"]
        session["agent"]["model"] = HAIKU
        snap = contracts.session_snapshot(session)
        self.assertEqual(snap["usd_source"], "price_table")
        self.assertEqual(snap["usd"], prices.usd(HAIKU, 5000, 800, 3000, 90000, single_request=False))
        self.assertIsNone(contracts.session_snapshot({"id": "sesn_2"}))

    def test_today_is_the_running_total_minus_the_other_days(self):
        day1 = contracts.day_delta(contracts.session_snapshot(self.SESSION), [])
        self.assertEqual((day1["input_tokens"], day1["usd"]), (5000, Decimal("1.37")))
        later = json.loads(json.dumps(self.SESSION))
        later["usage"].update(input_tokens=7000, active_seconds=60.0, list_cost={"amount": "200", "currency": "USD"})
        snap = contracts.session_snapshot(later)
        day2 = contracts.day_delta(snap, [day1])
        self.assertEqual((day2["input_tokens"], day2["output_tokens"], day2["usd"], day2["active_seconds"]),
                         (2000, 0, Decimal("0.63"), 17.5))
        self.assertEqual(contracts.day_delta(snap, [day1]), day2)  # 同一天再算一遍：同一个数（覆盖同一行，幂等）
        self.assertEqual(day1["usd"] + day2["usd"], Decimal("2.00"))  # 各天相加 = 官方累计
        self.assertEqual(contracts.day_delta(snap, [day1, day1, day1])["input_tokens"], 0)  # 不出负数

    def _write(self, existing=False, earlier=()):
        db = types.SimpleNamespace(exists=lambda doctype, name: existing if doctype == ledger.DOCTYPE else True,
                                   set_value=mock.Mock(), commit=mock.Mock())
        inserted = []

        def get_doc(values):
            return types.SimpleNamespace(insert=lambda **kw: inserted.append((values, kw)))
        with mock.patch.multiple(frappe, db=db, get_doc=get_doc, create=True), \
                mock.patch.object(frappe, "get_all", lambda *a, **kw: list(earlier), create=True):
            ledger.write_session("sesn_1", "sesn_1", "a@x.th", self.SESSION)
        return db, inserted

    def test_the_first_turn_inserts_today_s_row_by_name(self):
        db, inserted = self._write()
        values, kw = inserted[0]
        self.assertEqual((kw["set_name"], kw["ignore_if_duplicate"]), ("cc-sesn_1-2026-11-11", True))
        self.assertEqual((values["purpose"], values["day"], values["usd"], values["usd_source"], values["tenant"],
                          values["product"], values["segment"], values["model"]),
                         ("cc_chat", "2026-11-11", "1.37", "list_cost", "T-2026-0001", "cowork", "sesn_1", OPUS))
        db.commit.assert_called_once()

    def test_a_later_turn_the_same_day_overwrites_that_row(self):
        db, inserted = self._write(existing=True)
        self.assertEqual(inserted, [])
        name, fields = db.set_value.call_args.args[1], db.set_value.call_args.args[2]
        self.assertEqual((name, fields["usd"], fields["input_tokens"]), ("cc-sesn_1-2026-11-11", "1.37", 5000))


class Record(unittest.TestCase):
    def setUp(self):
        frappe.enqueued.clear()

    def test_rows_go_out_in_one_job_with_the_caller_s_context_and_decimals_intact(self):
        ledger.record("cc_title", [contracts.call(HAIKU, {"input_tokens": 100, "output_tokens": 10})], conversation="sesn_1")
        method, kw = frappe.enqueued[0]
        self.assertEqual((method, kw["queue"], kw["enqueue_after_commit"], kw["day"]),
                         ("pearnly_core.ai_usage.ledger.write", "short", False, "2026-11-11"))
        row = kw["rows"][0]
        self.assertEqual((row["purpose"], row["user"], row["conversation"]), ("cc_title", "a@x.th", "sesn_1"))
        self.assertIsInstance(row["usd"], Decimal)

    def test_nothing_is_queued_without_rows_a_known_purpose_or_a_site(self):
        ledger.record("cc_title", [])
        ledger.record("eval", [{"usd": Decimal(1)}])
        with mock.patch.object(frappe, "local", types.SimpleNamespace(site=None)):
            ledger.record("ocr", [{"usd": Decimal(1)}])
        self.assertEqual(frappe.enqueued, [])

    def test_a_queue_failure_never_reaches_the_caller(self):
        with mock.patch.object(frappe, "enqueue", side_effect=ConnectionError()):
            ledger.record("ocr", [{"usd": Decimal(1)}])


class Reports(unittest.TestCase):
    def test_groups_and_shape(self):
        self.assertEqual(report.groups_of("purpose, day,purpose,secret"), ["purpose", "day"])
        rows, total = report.shape([{"purpose": "cc_chat", "input_tokens": 10, "entries": 2, "usd": Decimal("1.5")},
                                    {"purpose": "ocr", "output_tokens": 3, "entries": 1, "usd": None}], ["purpose"])
        self.assertEqual([(r["purpose"], r["usd"], r["thb"]) for r in rows], [("cc_chat", "1.500000", "52.50"), ("ocr", "0.000000", "0.00")])
        self.assertEqual((total["input_tokens"], total["entries"], total["usd"], total["thb"]), (10, 3, "1.500000", "52.50"))

    def test_the_cross_site_total_reads_bench_output(self):
        spec = importlib.util.spec_from_file_location("ai_usage_total", ROOT / "scripts/ai-usage-total.py")
        total = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(total)
        site = {"site": "a", "tenant": "T1", "product": "erp", "by": ["purpose"], "total": {},
                "rows": [{"purpose": "cc_chat", "input_tokens": 5, "entries": 1, "usd": "0.100000"}]}
        lines = ["Loading...", json.dumps(site), json.dumps(dict(site, site="b", tenant="T2")), json.dumps(dict(site, site="c"))]
        merged = total.merge(total.sites(lines))
        self.assertEqual(merged["sites"], ["a", "b", "c"])
        self.assertEqual([(r["tenant"], r["usd"], r["input_tokens"]) for r in merged["rows"]],
                         [("T1", "0.200000", 10), ("T2", "0.100000", 5)])
        self.assertEqual((merged["total"]["usd"], merged["total"]["thb"]), ("0.300000", "10.50"))


if __name__ == "__main__":
    unittest.main()
