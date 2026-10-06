"""Zero-I/O tests for the "顺手同步" engine (`adapter/change_sync`).

替身只做三件事：假 frappe、假快照存储（store/manual）、假流水客户端。断言的是
**判断规则**本身：没变化就只花一个请求、点不到记录就整组重读、读不到就停手且
绝不推进水位、并发时不重复干、以及任何失败都不能冒泡给操作员那一笔。
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.eventlog_reader import ACTION_DELETE, ACTION_EDIT

CUSTOMERS = "ลูกค้า"
CARS = "รุ่นรถ"
PROVINCES = "จังหวัด"
ID_CARD = "1103702587460"
# DMS 使用记录点名客户用的是客户代码（车行自编），不是身份证号（2026-10-06 实测）。
CODE = "9112"


def row(seq, module, action=ACTION_EDIT, target=CODE, account="sale02"):
    spans = "".join(f"<span>{value}</span>" for value in
                    (account, "25/09/2569", "10:00:00", "10.0.0.1", module, action, target))
    return (f'<div data-val="{seq}||2" data-allo="" data-cf="">'
            f'<div class="detaildata"><p>{spans}</p></div></div>')


class FakeEventLog:
    """只认计数（无 sdtamt）与分页两个形状，与实测的原生响应一致。"""

    def __init__(self, modules=None, *, failing=()):
        self.modules = modules or {}
        self.failing = set(failing)
        self.calls = []

    def _post_text(self, path, data):
        self.calls.append((path, dict(data)))
        module = (data.get("ftd") or "").split("||")[0]
        if module in self.failing:
            raise RuntimeError("transport down")
        spec = self.modules.get(module) or {"count": 0, "rows": []}
        if "sdtamt" not in data:
            return "dt::%d" % spec["count"]
        size, page = int(data["sdtamt"]), int(data["sdtpage"])
        rows = spec["rows"][(page - 1) * size: page * size]
        return "dt::\t\t\t\t\t" + "".join(rows) if rows else "ndt::"


class FakeStore:
    DT = "DMS Read Snapshot"
    CHANGE_DATASET = "changes"

    def __init__(self, state=None, *, customers=None, scope="scope-1"):
        self.scope_key = scope
        self.state = state
        self.customers = dict(customers or {})
        self.written = []
        self.verified = []
        self.forgotten = []

    def scope(self, endpoint, **_kwargs):
        return self.scope_key

    def change_state(self, endpoint):
        return self.state

    def merge_change_state(self, endpoint, incoming, *, base, expected_scope=None):
        self.written.append(incoming)
        self.state = incoming
        return incoming

    code_reads = 0

    def customer_codes(self, endpoint):
        self.code_reads += 1
        return dict(self.customers)

    def verified_customer(self, endpoint, customer_id, fields, *, expected_scope=None):
        self.verified.append((customer_id, fields))

    def forget_customer(self, endpoint, customer_id, *, expected_scope=None):
        self.forgotten.append(customer_id)
        return True


class FakeManual:
    QUEUE = "dms_sync"

    def __init__(self, *, completed=None, fail=False):
        self.requests = []
        self.completed = dict(completed or {})
        self.fail = fail

    def request(self, endpoint, dataset, *, reader="authority", params=None, actor=None):
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.requests.append({"dataset": dataset, "reader": reader, "actor": actor})
        return {"state": "queued"}

    def status(self, endpoint, dataset, *, reader="authority"):
        return {"completed_at": self.completed.get(dataset, 0)}


class FakeCache:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def make_key(self, key):
        return key  # 生产里按站点加前缀；替身原样，键名照旧可断言

    def release(self, key, owner):
        """与 pearnly_core.execution.state.release 同义：值仍是 owner 才删。"""
        if self.values.get(key) == owner:
            self.values.pop(key, None)
            return True
        return False

    def delete(self, key):
        self.values.pop(key, None)


class ChangeSyncTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.generate_hash = Mock(side_effect=lambda length=16: "token-" + "x" * length)
        self.cache = FakeCache()
        self.frappe.cache = Mock(return_value=self.cache)
        self.frappe.log_error = Mock()
        self.store = FakeStore()
        self.manual = FakeManual()
        self.modules = patch.dict(sys.modules, {
            "frappe": self.frappe,
            "pearnly_dms.snapshots.store": self.store,
            "pearnly_dms.snapshots.manual": self.manual,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("pearnly_dms.adapter.change_sync", None)
        self.sync = importlib.import_module("pearnly_dms.adapter.change_sync")
        # 锁在不淘汰的状态存储（pearnly_core.execution.state）；替身同一个假缓存
        self.enterContext(patch.object(self.sync, "_state_store", return_value=self.cache))
        self.enterContext(patch.object(self.sync, "_state_release", side_effect=self.cache.release))
        self.read = Mock(return_value={"customer_id": "59", "cuscode": CODE, "people_id": ID_CARD})
        self.enterContext(patch.object(self.sync, "read_customer_complete", self.read))
        # 这些用例测的是"点不出记录就整组重读"本身，与白天/夜里无关：默认不延后（ADR-012）。
        self.defer = self.enterContext(
            patch("pearnly_dms.adapter.live_sync.defer_heavy", return_value=False))

    def baseline(self, **overrides):
        """一个已建好的水位：默认每个模块都是"零事件、水位 1"，可逐个改。"""
        modules = {module: {"count": 0, "seq": 1, "dirty_since": 0}
                   for module, _dataset, _records in self.sync.MODULES}
        modules.update(overrides)
        return {"version": 1, "actor": "Administrator", "seeded_at": 1.0,
                "seed_attempt": 1.0, "next_index": 0, "modules": modules}

    def catch_up(self, log):
        return self.sync.catch_up("PRIMARY", client=log)

    def test_unchanged_counts_cost_one_request_per_module(self):
        self.store.state = self.baseline()
        log = FakeEventLog()

        self.catch_up(log)

        self.assertEqual(len(log.calls), len(self.sync.MODULES))
        self.assertTrue(all("sdtamt" not in data for _path, data in log.calls),
                        "没有变化就只读计数，不翻流水")
        self.assertEqual(self.store.verified, [])
        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.store.written[-1]["next_index"], 0)
        self.assertEqual(self.store.written[-1]["modules"], self.store.state["modules"])

    def test_named_customer_change_is_read_back_per_record(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified, [("59", {"customer_id": "59", "cuscode": CODE, "people_id": ID_CARD})])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["count"], entry["seq"], entry["dirty_since"]), (231, 832, 0))
        self.assertEqual(self.manual.requests, [], "按记录补上了就不该整组重读")

    def test_new_customer_named_by_its_dealer_code_is_found_and_cached(self):
        """2026-10-06：新增客户 134 在使用记录里是「เพิ่ม | 9112」，缓存里还没有它。"""
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 281, "seq": 981, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"count": 282, "rows": [row(982, CUSTOMERS, "เพิ่ม", CODE)]}})
        self.read.return_value = {"customer_id": "134", "cuscode": CODE, "people_id": "1310200999112"}
        with patch.object(self.sync, "_search_customer", return_value="134") as search:
            self.catch_up(log)

        search.assert_called_once_with(log, CODE)
        self.assertEqual([cid for cid, _fields in self.store.verified], ["134"])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"]), (982, 0))
        self.assertEqual(self.manual.requests, [])

    def test_customer_the_read_account_cannot_see_is_retried_not_frozen(self):
        """认不出：挂进 pending 之后每轮再试，水位照常前进，不马上整组重读。"""
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 281, "seq": 981, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"count": 282, "rows": [row(982, CUSTOMERS, "เพิ่ม", CODE)]}})
        with patch.object(self.sync, "_search_customer", return_value=""), \
                self.assertLogs(self.sync.logger, "WARNING") as logs:
            self.catch_up(log)

        self.assertEqual(self.store.verified, [])
        self.assertTrue(any(CODE in line for line in logs.output))
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"], list(entry["pending"])), (982, 0, [CODE]))
        self.assertEqual(self.manual.requests, [])

    def test_daytime_leaves_a_big_group_read_for_the_night(self):
        """白天点不出记录：客户组只标记待重读、水位推进，不马上登记一个多小时的整组读取。"""
        self.defer.side_effect = lambda dataset: dataset == "customers"
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual(self.manual.requests, [])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["count"], entry["seq"]), (231, 832))
        self.assertGreater(entry["dirty_since"], 0, "留着待重读标记，凌晨全量读完才清")

    def test_change_without_an_identified_record_falls_back_to_a_group_read(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["count"], 231, "读到的位置要记下，避免反复翻同一页")
        self.assertEqual(entry["seq"], 832)
        self.assertTrue(entry["dirty_since"], "没真正补上之前，整组重读的义务要一直挂着")

    def test_delete_is_not_guessed_per_record(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"count": 231,
                                        "rows": [row(832, CUSTOMERS, ACTION_DELETE)]}})

        self.catch_up(log)

        self.assertEqual(self.read.call_count, 0)
        self.assertEqual(self.store.verified, [])
        self.assertEqual(self.store.forgotten, ["59"],
                         "自己这条 overlay 必须先撤掉，否则整组重读会被它永久毒住")
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_delete_without_a_local_customer_has_nothing_to_forget(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"count": 231,
                                        "rows": [row(832, CUSTOMERS, ACTION_DELETE)]}})

        self.catch_up(log)

        self.assertEqual(self.store.forgotten, [])
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_an_edit_and_a_delete_in_the_same_batch_both_land(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.store.customers["1000001205453"] = "60"
        log = FakeEventLog({CUSTOMERS: {"count": 232, "rows": [
            row(832, CUSTOMERS, ACTION_EDIT, CODE),
            row(833, CUSTOMERS, ACTION_DELETE, "1000001205453"),
        ]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified[0][0], "59")
        self.assertEqual(self.store.forgotten, ["60"])
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_unreadable_log_stops_without_moving_any_watermark(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        before = {key: dict(value) for key, value in self.store.state["modules"].items()}
        log = FakeEventLog(failing={CUSTOMERS})

        self.catch_up(log)

        written = self.store.written[-1]["modules"]
        self.assertEqual(written[CUSTOMERS], before[CUSTOMERS])
        self.assertEqual(self.manual.requests, [])
        self.assertEqual(log.calls[0][1]["ftd"], CUSTOMERS + "||2", "按模块逐个查，没有全局过滤器")

    def test_record_read_failure_is_retried_on_the_next_pull(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.read.side_effect = RuntimeError("read failed")
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["count"], entry["seq"], list(entry["pending"])), (231, 832, [CODE]),
                         "水位前进，这一条挂起下一轮重试")
        self.assertEqual(self.manual.requests, [], "一次读失败不等于要整组重读")

        self.read.side_effect = None
        self.catch_up(log)
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(self.store.verified[-1][0], "59", "下一轮补上")
        self.assertEqual(entry["pending"], {})

    def test_readback_identity_mismatch_is_never_written(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.read.return_value = {"customer_id": "59", "cuscode": "0001", "people_id": ID_CARD}
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified, [], "读回来的不是这个人就不能写进缓存")
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], list(entry["pending"])), (832, [CODE]), "挂起重试，过期交整组重读")

    def test_truncated_backlog_asks_for_a_group_read(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 100, "seq": 1, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        rows = [row(1000 - index, CUSTOMERS) for index in range(200)]
        log = FakeEventLog({CUSTOMERS: {"count": 400, "rows": rows}})

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["count"]), (1000, 400),
                         "翻到的照常补、水位前进，翻不到的那一段交整组重读")
        self.assertTrue(entry["dirty_since"])
        self.assertEqual(self.read.call_count, 1, "翻到的那批（同一客户）照常补读")

    def test_dirty_dataset_clears_once_the_group_read_actually_landed(self):
        self.store.state = self.baseline(**{CARS: {"count": 20, "seq": 900, "dirty_since": 50.0}})
        self.manual.completed["vehicles"] = 100.0
        log = FakeEventLog({CARS: {"count": 20, "rows": [row(902, CARS)]}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CARS]
        self.assertEqual((entry["count"], entry["seq"], entry["dirty_since"]), (20, 902, 0))
        self.assertEqual(self.manual.requests, [], "已经落地就不用再登记")

    def test_dirty_dataset_is_not_re_requested_inside_the_cooldown(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**{CARS: {"count": 20, "seq": 900, "dirty_since": now}})
        log = FakeEventLog({CARS: {"count": 20, "rows": [row(902, CARS)]}})

        with patch.object(self.sync.time, "time", return_value=now + 5):
            self.catch_up(log)

        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.store.written[-1]["modules"][CARS]["dirty_since"], now)

    def test_a_held_lock_skips_the_whole_pull(self):
        self.cache.values["dms-changes:scope-1"] = "someone-else"
        log = FakeEventLog()

        self.catch_up(log)

        self.assertEqual(log.calls, [])
        self.assertEqual(self.store.written, [])

    def test_failures_never_reach_the_caller(self):
        self.store.scope = Mock(side_effect=RuntimeError("no credentials"))
        self.catch_up(FakeEventLog())
        self.store.change_state = Mock(side_effect=RuntimeError("db down"))
        self.catch_up(FakeEventLog())

    def test_missing_baseline_is_established_without_applying_anything(self):
        self.store.state = None
        log = FakeEventLog({CUSTOMERS: {"count": 230, "rows": [row(831, CUSTOMERS)]}})

        self.catch_up(log)

        state = self.store.written[-1]
        self.assertTrue(state["seeded_at"])
        self.assertEqual(state["modules"][CUSTOMERS]["count"], 230)
        self.assertEqual(state["modules"][CUSTOMERS]["seq"], 831)
        self.assertEqual(self.store.verified, [], "建基线不补数据")
        self.assertEqual(self.read.call_count, 0)

    def test_half_built_baseline_is_not_marked_seeded(self):
        self.store.state = None
        log = FakeEventLog({CARS: {"count": 20, "rows": []}}, failing={CARS})

        self.catch_up(log)

        self.assertFalse(self.store.written[-1]["seeded_at"], "没读全就不能算基线，下一轮还得重来")

    def test_group_read_registration_uses_the_service_actor(self):
        self.store.state = self.baseline(**{CARS: {"count": 20, "seq": 900, "dirty_since": 0}})
        log = FakeEventLog({CARS: {"count": 21, "rows": [row(901, CARS)]}})

        self.catch_up(log)

        self.assertEqual(self.manual.requests,
                         [{"dataset": "vehicles", "reader": "authority", "actor": "Administrator"}])

    def test_registration_failure_keeps_the_old_watermark(self):
        self.store.state = self.baseline(**{PROVINCES: {"count": 3, "seq": 800, "dirty_since": 0}})
        self.manual.fail = True
        log = FakeEventLog({PROVINCES: {"count": 4, "rows": [row(801, PROVINCES, target="99")]}})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][PROVINCES]["seq"], 800)

    def test_a_group_waiting_for_the_night_still_takes_new_edits_per_record(self):
        """2026-10-06：待重读期间不再冻结——之后的客户改动照常一分钟内补进缓存。"""
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 500.0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified[-1][0], "59")
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"]), (832, 500.0), "待重读标记留到整组重读落地")
        self.assertEqual(self.manual.requests, [])

    def test_the_landed_group_read_clears_the_mark_and_the_pending_codes(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 500.0,
                                                        "pending": {"OLD-1": 400.0}}})
        self.manual.completed["customers"] = 900.0
        log = FakeEventLog({CUSTOMERS: {"count": 230, "rows": []}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["dirty_since"], entry["pending"]), (0.0, {}))

    def test_a_code_still_unresolved_after_the_window_goes_to_the_night(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0,
            "pending": {CODE: now - self.sync.PENDING_SECONDS - 1}}})
        log = FakeEventLog({CUSTOMERS: {"count": 230, "rows": []}})

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["dirty_since"], entry["pending"]), (now, {}))
        self.assertEqual([r["dataset"] for r in self.manual.requests], ["customers"])

    def test_customer_group_registration_failure_keeps_the_watermark(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.manual.fail = True
        log = FakeEventLog({CUSTOMERS: {"count": 231, "rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][CUSTOMERS]["seq"], 831, "下一轮从原处重来")

    def test_an_idle_minute_does_not_load_the_customer_cache(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"count": 230, "seq": 831, "dirty_since": 0}})
        self.catch_up(FakeEventLog({CUSTOMERS: {"count": 230, "rows": []}}))
        self.assertEqual(self.store.code_reads, 0, "没有改动就不读整份客户缓存（ris 1.5 万位）")

    def test_seed_records_the_current_head_and_applies_nothing(self):
        self.store.state = None
        log = FakeEventLog({CUSTOMERS: {"count": 230, "rows": [row(831, CUSTOMERS)]}})

        result = self.sync._establish_baseline("PRIMARY", client=log, actor="admin@example.test")

        self.assertTrue(result["seeded"])
        state = self.store.written[-1]
        self.assertEqual(state["actor"], "admin@example.test")
        self.assertEqual(state["modules"][CUSTOMERS]["count"], 230)
        self.assertEqual(state["modules"][CUSTOMERS]["seq"], 831)
        self.assertEqual(self.store.verified, [])


if __name__ == "__main__":
    unittest.main()
