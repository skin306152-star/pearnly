"""Zero-I/O tests for the "顺手同步" engine (`adapter/change_sync`).

替身只做三件事：假 frappe、假快照存储（store/manual）、假流水客户端。断言的是
**判断规则**本身：没变化就只花一个请求、点不到记录就整组重读、连不上就停手且
绝不推进水位、读不懂只跳过那一个模块（读不懂太久后台改走整组重读）、并发时不重复干、
以及任何失败都不能冒泡给操作员那一笔。
"""
from __future__ import annotations

import importlib
from pathlib import Path
import sys
import time
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_dms"))

from pearnly_dms.client.eventlog_reader import ACTION_DELETE, ACTION_EDIT, HEAD_ROWS
from pearnly_dms.contracts.client_base import DmsClientError

CUSTOMERS = "ลูกค้า"
CARS = "รุ่นรถ"
PROVINCES = "จังหวัด"
ID_CARD = "1103702587460"
# DMS 使用记录点名客户用的是客户代码（车行自编），不是身份证号（2026-10-06 实测）。
CODE = "9112"


def row(seq, module, action=ACTION_EDIT, target=CODE, account="sale02"):
    """2026-10-11 改版后的行形状：每项一个 `<p>`，标识 `<流水号>||`。"""
    def pair(a, b):
        return f"<div><p>{a}</p><p>{b}</p></div>"
    return (f'<div data-val="{seq}||" data-allo="" data-cf=""><div class="detaildata">'
            f'<div>{pair(account, "10.0.0.1")}{pair("25/09/2569", "10:00:00")}</div>'
            f'<div>{pair(module, action)}<div><p>{target}</p></div></div></div></div>')


def _seq(html):
    return int(html.split('data-val="', 1)[1].split("||", 1)[0])


class FakeEventLog:
    """照 2026-10-11 改版后的原生行为：不回总数；`sdtamt=n&sdtpage=p` 从第 (p-1)·n 行起回 n+1 行
    （页与页重叠一行）；不传 `sdtamt` 只回最新一行。

    `rows` 只写"新"的那几行；水位所在那一行（历史）由 `history` 自动补在最后 —— 真实流水里它一直在。
    """

    def __init__(self, modules=None, *, failing=(), history=None, error=None, garbled=(),
                 deep_error=None):
        self.modules = modules or {}
        self.failing = set(failing)
        self.error = error              # `failing` 的模块抛什么（默认传输断开）
        self.garbled = set(garbled)     # 这些模块回来的是我们不认识的结构（对方改版）
        self.deep_error = dict(deep_error or {})   # 只在往回翻（每页 200 行）时抛
        self.history = history
        self.calls = []

    def modules_called(self):
        return [data["ftd"].split("||")[0] for _path, data in self.calls]

    def rows_of(self, module):
        rows = list((self.modules.get(module) or {}).get("rows", []))
        old = (self.history or {}).get(module)
        if old and old not in {_seq(item) for item in rows}:
            rows.append(row(old, module))
        return sorted(rows, key=_seq, reverse=True)

    def _post_text(self, path, data):
        self.calls.append((path, dict(data)))
        module = (data.get("ftd") or "").split("||")[0]
        if module in self.failing:
            raise self.error or RuntimeError("transport down")
        if module in self.garbled:
            # 2026-10-11 那样的改版：回的还是 `dt::`，但一页里是我们不认识的结构。
            return "dt::\t<table><tr><td>991</td><td>" + module + "</td></tr></table>"
        size = int(data.get("sdtamt") or 0)
        if module in self.deep_error and size != HEAD_ROWS:
            raise self.deep_error[module]
        rows = self.rows_of(module)
        page = int(data.get("sdtpage") or 1)
        rows = rows[(page - 1) * size: (page - 1) * size + size + 1]
        return "dt::\t\t\t\t" + "".join(rows) if rows else "ndt::"


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
    ACTIVE = {"queued", "running"}

    def __init__(self, *, completed=None, fail=False):
        self.requests = []
        self.completed = dict(completed or {})
        self.statuses = {}      # dataset → {"state", "updated_at"}（与 `manual.status` 同名字段）
        self.status_calls = []
        self.fail = fail

    def request(self, endpoint, dataset, *, reader="authority", params=None, actor=None):
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.requests.append({"dataset": dataset, "reader": reader, "actor": actor})
        self.statuses[dataset] = {"state": "queued", "updated_at": time.time()}
        return {"state": "queued"}

    def status(self, endpoint, dataset, *, reader="authority"):
        self.status_calls.append(dataset)
        return {"state": "idle", "updated_at": 0.0, "completed_at": self.completed.get(dataset, 0),
                **self.statuses.get(dataset, {})}


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
        modules = {module: {"seq": 1, "dirty_since": 0}
                   for module, _dataset, _records in self.sync.MODULES}
        modules.update(overrides)
        return {"version": 1, "actor": "Administrator", "seeded_at": 1.0,
                "seed_attempt": 1.0, "next_index": 0, "modules": modules}

    def history_without(self, module):
        """水位所在那一行只对 `module` 不在了（别的模块照常）。"""
        return {name: entry.get("seq") for name, entry in self.store.state["modules"].items()
                if name != module}

    def catch_up(self, log, *, background=False):
        if log.history is None:
            log.history = {module: entry.get("seq") for module, entry in
                           ((self.store.state or {}).get("modules") or {}).items()}
        return self.sync.catch_up("PRIMARY", client=log, background=background)

    def test_unchanged_counts_cost_one_request_per_module(self):
        # 客户组平时的条目带 `pending`（每轮都写回空的），这里照实际的平稳状态建。
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 1, "dirty_since": 0, "pending": {}}})
        before_state = dict(self.store.state)
        before = {name: dict(entry) for name, entry in self.store.state["modules"].items()}
        log = FakeEventLog()

        self.catch_up(log, background=True)

        self.assertEqual(len(log.calls), len(self.sync.MODULES))
        self.assertTrue(all(data.get("sdtpage") == "1" for _path, data in log.calls),
                        "没有变化就只读最上面几行，不翻流水")
        self.assertEqual(self.store.verified, [])
        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.manual.status_calls, [], "平时不读整组重读的状态（不多查一次库）")
        state = self.store.written[-1]
        self.assertEqual(state["next_index"], 0)
        self.assertEqual(set(state), set(before_state) | {"checked_at"}, "写回的状态不多一个键")
        # 逐项和改前比（不能和刚写进去的自己比）：除了检查时刻，什么都不该变，也不多键。
        self.assertEqual(set(state["modules"]), set(before))
        for name, entry in state["modules"].items():
            self.assertEqual({key: value for key, value in entry.items() if key != "checked_at"},
                             before[name], name)

    def test_named_customer_change_is_read_back_per_record(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified, [("59", {"customer_id": "59", "cuscode": CODE, "people_id": ID_CARD})])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"]), (832, 0))
        self.assertEqual(self.manual.requests, [], "按记录补上了就不该整组重读")

    def test_new_customer_named_by_its_dealer_code_is_found_and_cached(self):
        """2026-10-06：新增客户 134 在使用记录里是「เพิ่ม | 9112」，缓存里还没有它。"""
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 981, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"rows": [row(982, CUSTOMERS, "เพิ่ม", CODE)]}})
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
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 981, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"rows": [row(982, CUSTOMERS, "เพิ่ม", CODE)]}})
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
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual(self.manual.requests, [])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 832)
        self.assertGreater(entry["dirty_since"], 0, "留着待重读标记，凌晨全量读完才清")

    def test_change_without_an_identified_record_falls_back_to_a_group_read(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 832, "读到的位置要记下，避免反复翻同一页")
        self.assertEqual(entry["seq"], 832)
        self.assertTrue(entry["dirty_since"], "没真正补上之前，整组重读的义务要一直挂着")

    def test_delete_is_not_guessed_per_record(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {
                                        "rows": [row(832, CUSTOMERS, ACTION_DELETE)]}})

        self.catch_up(log)

        self.assertEqual(self.read.call_count, 0)
        self.assertEqual(self.store.verified, [])
        self.assertEqual(self.store.forgotten, ["59"],
                         "自己这条 overlay 必须先撤掉，否则整组重读会被它永久毒住")
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_delete_without_a_local_customer_has_nothing_to_forget(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        log = FakeEventLog({CUSTOMERS: {
                                        "rows": [row(832, CUSTOMERS, ACTION_DELETE)]}})

        self.catch_up(log)

        self.assertEqual(self.store.forgotten, [])
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_an_edit_and_a_delete_in_the_same_batch_both_land(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.store.customers["1000001205453"] = "60"
        log = FakeEventLog({CUSTOMERS: {"rows": [
            row(832, CUSTOMERS, ACTION_EDIT, CODE),
            row(833, CUSTOMERS, ACTION_DELETE, "1000001205453"),
        ]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified[0][0], "59")
        self.assertEqual(self.store.forgotten, ["60"])
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])

    def test_unreadable_log_stops_without_moving_any_watermark(self):
        # 连不上（传输失败）：与以前完全一样 —— 只花 1 个请求就整轮停手，下一轮仍从它开始。
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        before = {key: dict(value) for key, value in self.store.state["modules"].items()}
        log = FakeEventLog(failing={CUSTOMERS})

        self.catch_up(log)

        written = self.store.written[-1]["modules"]
        self.assertEqual(written[CUSTOMERS], before[CUSTOMERS])
        self.assertEqual(self.manual.requests, [])
        self.assertEqual(log.calls[0][1]["ftd"], CUSTOMERS + "||2", "按模块逐个查，没有全局过滤器")
        self.assertEqual(len(log.calls), 1, "连不上就整轮停手，不去碰后面的模块")
        self.assertEqual(self.store.written[-1]["next_index"], 0, "下一轮仍从它重来")

    def test_only_unreadable_content_skips_a_module(self):
        """白名单：只有"内容读不懂"才跳过；传输失败、非 200、让路给操作员都照旧整轮停手。"""
        from pearnly_dms.adapter.read_priority import ReadDeferred
        errors = (RuntimeError("transport down"),
                  DmsClientError("eventlog/component/showdata.php http=502", "ERR_DMS_TECHNICAL"),
                  ReadDeferred("The DMS account is busy"))
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.store.state = self.baseline()
                log = FakeEventLog(failing={CUSTOMERS}, error=error)

                self.catch_up(log, background=True)

                state = self.store.written[-1]
                self.assertEqual((len(log.calls), state["next_index"]), (1, 0))
                self.assertNotIn("unreadable_since", state["modules"][CUSTOMERS])
                self.assertEqual(self.manual.requests, [])

    def test_one_unreadable_module_is_skipped_and_the_rest_still_sync(self):
        """#11：客户模块读不懂时，以前整轮停在它上面（28 个模块 0 个处理），一直到凌晨。"""
        now = 1_000_000.0
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0},
                                            CARS: {"seq": 900, "dirty_since": 0}})
        log = FakeEventLog({CARS: {"rows": [row(901, CARS)]}}, garbled={CUSTOMERS})

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(log)   # 后台与操作员顺手同步共用这一段

        self.assertEqual(set(log.modules_called()), {module for module, _d, _r in self.sync.MODULES},
                         "其余 27 个模块照常检查")
        state = self.store.written[-1]
        entry = state["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"], entry["unreadable_since"]), (831, 0, now),
                         "读不懂的模块水位不动（不当成没变化），只记下从何时起读不懂")
        self.assertEqual(state["next_index"], 0)
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["vehicles"],
                         "后面模块的改动照常处理")

    def test_the_first_unreadable_time_is_kept_across_rounds(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0,
                                                        "unreadable_since": 500.0}})
        self.catch_up(FakeEventLog(garbled={CUSTOMERS}))
        self.assertEqual(self.store.written[-1]["modules"][CUSTOMERS]["unreadable_since"], 500.0)

    def test_two_unreadable_modules_in_a_row_stop_and_rotate_past_them(self):
        """整体改版：每轮只花 2 个请求，停在这两个之后，下一轮接着往后轮（不再永远卡在第一个）。"""
        names = [module for module, _dataset, _records in self.sync.MODULES]
        self.store.state = self.baseline()
        log = FakeEventLog(garbled=set(names))

        self.catch_up(log)

        self.assertEqual(log.modules_called(), names[:2])
        state = self.store.written[-1]
        self.assertEqual(state["next_index"], 2)
        self.assertTrue(all(state["modules"][name].get("unreadable_since") for name in names[:2]))
        self.assertTrue(all("unreadable_since" not in state["modules"][name] for name in names[2:]))

        log.calls.clear()
        self.catch_up(log)
        self.assertEqual(log.modules_called(), names[2:4])
        self.assertEqual(self.store.written[-1]["next_index"], 4)

        for _round in range(len(names) // 2):
            self.catch_up(log)
        modules = self.store.written[-1]["modules"]
        self.assertTrue(all(modules[name].get("unreadable_since") for name in names),
                        "约 14 轮把全部模块都标上")
        self.assertTrue(all(modules[name]["seq"] == 1 for name in names), "水位一个都没动")
        self.assertEqual(self.manual.requests, [])

    def test_a_module_readable_again_clears_the_mark_and_catches_up_from_the_old_watermark(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0,
                                                        "unreadable_since": 500.0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified[-1][0], "59", "停摆期间的改动从旧水位接着补")
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 832)
        self.assertNotIn("unreadable_since", entry)

    def test_the_mark_is_cleared_even_if_the_catch_up_after_it_fails(self):
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 0,
                                                   "unreadable_since": 500.0}})
        log = FakeEventLog({CARS: {"rows": [row(901, CARS)]}},
                           deep_error={CARS: DmsClientError("http=502", "ERR_DMS_TECHNICAL")})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][CARS], {"seq": 900, "dirty_since": 0},
                         "读得懂了就清标记；翻流水连不上照旧保留旧水位、下一轮重来")

    # ── 往回翻时读不懂 ──────────────────────────────────────────────────
    def test_an_unreadable_older_customer_page_is_incomplete_not_a_frozen_module(self):
        """最新几行读得懂、往回翻的那页里有一行缺账号：以前异常被吞掉，水位永远停在原处、每轮白翻一遍。"""
        self.defer.side_effect = lambda dataset: dataset == "customers"   # 白天
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        rows = [row(seq, CUSTOMERS, target=f"C{seq}") for seq in range(845, 833, -1)]
        rows += [row(833, CUSTOMERS, account=""), row(832, CUSTOMERS)]   # 挤出最新几行之外
        log = FakeEventLog({CUSTOMERS: {"rows": rows}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 845, "水位记到此刻最新一行，读不到的那段交整组重读")
        self.assertTrue(entry["dirty_since"])
        self.assertNotIn("unreadable_since", entry)
        self.assertEqual(self.manual.requests, [], "白天客户组的整组重读仍留给凌晨")

        log.calls.clear()
        self.catch_up(log)
        self.assertEqual(log.modules_called().count(CUSTOMERS), 1, "下一轮不再白翻那几页")

    def test_an_unreadable_older_page_of_a_small_group_asks_for_a_group_read(self):
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 0}})
        rows = [row(seq, CARS) for seq in range(915, 902, -1)]
        rows += [row(902, CARS, account=""), row(901, CARS)]
        log = FakeEventLog({CARS: {"rows": rows}})

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["vehicles"])
        entry = self.store.written[-1]["modules"][CARS]
        self.assertEqual(entry["seq"], 900, "车辆组照现有规则：整组重读落地前水位不动")
        self.assertTrue(entry["dirty_since"])

    def test_a_transport_failure_on_an_older_page_keeps_the_old_watermark(self):
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 0}})
        log = FakeEventLog({CARS: {"rows": [row(901, CARS)]}},
                           deep_error={CARS: DmsClientError("http=502", "ERR_DMS_TECHNICAL")})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][CARS], {"seq": 900, "dirty_since": 0})
        self.assertEqual(self.manual.requests, [], "连不上不是读不懂：不交整组重读，下一轮重来")

    # ── 读不懂太久：白天同步的后台兜底 ──────────────────────────────────
    def stalled_since(self, since, *datasets):
        datasets = datasets or ("customers", "vehicles", "company", "geo")
        return {module: {"seq": 1, "dirty_since": 0, "unreadable_since": since}
                for module, dataset, _records in self.sync.MODULES if dataset in datasets}

    def everything_garbled(self):
        return FakeEventLog(garbled={module for module, _dataset, _records in self.sync.MODULES})

    def test_a_stall_shorter_than_ten_minutes_asks_for_no_group_read(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - self.sync.STALL_SECONDS + 1))

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(self.everything_garbled(), background=True)

        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.manual.status_calls, [])

    def test_a_ten_minute_stall_reads_vehicles_and_company_once_each_never_the_big_groups(self):
        """10-11 那次：整体改版后车辆、公司选项一整天不变；现在每组每小时最多整组重读一次。
        客户、地址按常量排除，与白天/夜里的判断无关（这里 `defer_heavy` 恒为 False）。"""
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - self.sync.STALL_SECONDS))
        log = self.everything_garbled()

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(log, background=True)

        self.assertEqual(len(log.calls), 2, "整体改版时每轮仍只花 2 个请求")
        self.assertEqual(self.manual.requests, [
            {"dataset": "vehicles", "reader": "authority", "actor": "Administrator"},
            {"dataset": "company", "reader": "authority", "actor": "Administrator"}])

        with patch.object(self.sync.time, "time", return_value=now + 60):
            self.catch_up(log, background=True)
        self.assertEqual(len(self.manual.requests), 2, "已登记（排队中）就不再重复排")

        self.manual.statuses = {"vehicles": {"state": "done", "updated_at": now + 600},
                                "company": {"state": "failed", "updated_at": now + 900}}
        self.manual.completed["vehicles"] = now + 600
        with patch.object(self.sync.time, "time", return_value=now + 600 + self.sync.STALL_REFRESH_SECONDS - 1):
            self.catch_up(log, background=True)
        self.assertEqual(len(self.manual.requests), 2, "一小时内读过、试过都不再排")

        with patch.object(self.sync.time, "time", return_value=now + 900 + self.sync.STALL_REFRESH_SECONDS):
            self.catch_up(log, background=True)
        self.assertEqual([item["dataset"] for item in self.manual.requests[2:]], ["vehicles", "company"],
                         "满一小时仍读不懂：再各排一次")

    def test_a_group_already_being_read_is_not_queued_again(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - 5000, "vehicles", "company"))
        self.manual.statuses = {"vehicles": {"state": "running", "updated_at": now - 5000},
                                "company": {"state": "queued", "updated_at": now - 5000}}

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(self.everything_garbled(), background=True)

        self.assertEqual(self.manual.requests, [])

    def test_the_big_groups_are_never_read_in_the_day_even_after_a_long_stall(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - 86400, "customers", "geo"))

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(self.everything_garbled(), background=True)

        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.manual.status_calls, [])

    def test_the_operator_path_never_adds_a_group_read(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - 5000))

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(self.everything_garbled())

        self.assertEqual(self.manual.requests, [])
        self.assertEqual(self.manual.status_calls, [])

    def test_the_daytime_round_is_the_one_that_turns_a_stall_into_group_reads(self):
        """`live_sync.pull`（后台每分钟一轮）传 `background=True`，兜底才会发生。"""
        from pearnly_dms.adapter import live_sync
        now = 1_000_000.0
        self.store.state = self.baseline(**self.stalled_since(now - self.sync.STALL_SECONDS))
        log = self.everything_garbled()
        log.history = {}
        operations = types.ModuleType("pearnly_dms.adapter.operations")
        operations.run_logged_in = lambda endpoint, fn, **_kwargs: fn(log, None)

        with patch.dict(sys.modules, {"pearnly_dms.adapter.operations": operations}), \
                patch.object(live_sync, "_admin_account_in_use", return_value=False), \
                patch.object(self.sync.time, "time", return_value=now):
            self.assertEqual(live_sync.pull("PRIMARY"), {"ok": True})

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["vehicles", "company"])

    def test_record_read_failure_is_retried_on_the_next_pull(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.read.side_effect = RuntimeError("read failed")
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], list(entry["pending"])), (832, [CODE]),
                         "水位前进，这一条挂起下一轮重试")
        self.assertEqual(self.manual.requests, [], "一次读失败不等于要整组重读")

        self.read.side_effect = None
        self.catch_up(log)
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(self.store.verified[-1][0], "59", "下一轮补上")
        self.assertEqual(entry["pending"], {})

    def test_readback_identity_mismatch_is_never_written(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        self.read.return_value = {"customer_id": "59", "cuscode": "0001", "people_id": ID_CARD}
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified, [], "读回来的不是这个人就不能写进缓存")
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], list(entry["pending"])), (832, [CODE]), "挂起重试，过期交整组重读")

    def test_truncated_backlog_asks_for_a_group_read(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 1, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        rows = [row(5000 - index, CUSTOMERS) for index in range(1000)]
        log = FakeEventLog({CUSTOMERS: {"rows": rows}})

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 5000,
                         "翻到的照常补、水位前进，翻不到的那一段交整组重读")
        self.assertTrue(entry["dirty_since"])
        self.assertEqual(self.read.call_count, 1, "翻到的那批（同一客户）照常补读")

    def test_dirty_dataset_clears_once_the_group_read_actually_landed(self):
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 50.0}})
        self.manual.completed["vehicles"] = 100.0
        log = FakeEventLog({CARS: {"rows": [row(902, CARS)]}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CARS]
        self.assertEqual((entry["seq"], entry["dirty_since"]), (902, 0))
        self.assertEqual(self.manual.requests, [], "已经落地就不用再登记")

    def test_dirty_dataset_is_not_re_requested_inside_the_cooldown(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": now}})
        log = FakeEventLog({CARS: {"rows": [row(902, CARS)]}})

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
        log = FakeEventLog({CUSTOMERS: {"rows": [row(831, CUSTOMERS)]}})

        self.catch_up(log)

        state = self.store.written[-1]
        self.assertTrue(state["seeded_at"])
        self.assertEqual(state["modules"][CUSTOMERS]["seq"], 831)
        self.assertEqual(self.store.verified, [], "建基线不补数据")
        self.assertEqual(self.read.call_count, 0)

    def test_half_built_baseline_is_not_marked_seeded(self):
        self.store.state = None
        log = FakeEventLog({CARS: {"rows": []}}, failing={CARS})

        self.catch_up(log)

        self.assertFalse(self.store.written[-1]["seeded_at"], "没读全就不能算基线，下一轮还得重来")

    def test_group_read_registration_uses_the_service_actor(self):
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 0}})
        log = FakeEventLog({CARS: {"rows": [row(901, CARS)]}})

        self.catch_up(log)

        self.assertEqual(self.manual.requests,
                         [{"dataset": "vehicles", "reader": "authority", "actor": "Administrator"}])

    def test_registration_failure_keeps_the_old_watermark(self):
        self.store.state = self.baseline(**{PROVINCES: {"seq": 800, "dirty_since": 0}})
        self.manual.fail = True
        log = FakeEventLog({PROVINCES: {"rows": [row(801, PROVINCES, target="99")]}})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][PROVINCES]["seq"], 800)

    def test_a_group_waiting_for_the_night_still_takes_new_edits_per_record(self):
        """2026-10-06：待重读期间不再冻结——之后的客户改动照常一分钟内补进缓存。"""
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 500.0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS)]}})

        self.catch_up(log)

        self.assertEqual(self.store.verified[-1][0], "59")
        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["seq"], entry["dirty_since"]), (832, 500.0), "待重读标记留到整组重读落地")
        self.assertEqual(self.manual.requests, [])

    def test_the_landed_group_read_clears_the_mark_and_the_pending_codes(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 500.0,
                                                        "pending": {"OLD-1": 400.0}}})
        self.manual.completed["customers"] = 900.0
        log = FakeEventLog({CUSTOMERS: {"rows": []}})

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["dirty_since"], entry["pending"]), (0.0, {}))

    def test_a_code_still_unresolved_after_the_window_goes_to_the_night(self):
        now = 1_000_000.0
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0,
            "pending": {CODE: now - self.sync.PENDING_SECONDS - 1}}})
        log = FakeEventLog({CUSTOMERS: {"rows": []}})

        with patch.object(self.sync.time, "time", return_value=now):
            self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual((entry["dirty_since"], entry["pending"]), (now, {}))
        self.assertEqual([r["dataset"] for r in self.manual.requests], ["customers"])

    def test_customer_group_registration_failure_keeps_the_watermark(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.manual.fail = True
        log = FakeEventLog({CUSTOMERS: {"rows": [row(832, CUSTOMERS, target="")]}})

        self.catch_up(log)

        self.assertEqual(self.store.written[-1]["modules"][CUSTOMERS]["seq"], 831, "下一轮从原处重来")

    def test_an_idle_minute_does_not_load_the_customer_cache(self):
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.catch_up(FakeEventLog({CUSTOMERS: {"rows": []}}))
        self.assertEqual(self.store.code_reads, 0, "没有改动就不读整份客户缓存（ris 1.5 万位）")

    def test_seed_records_the_current_head_and_applies_nothing(self):
        self.store.state = None
        log = FakeEventLog({CUSTOMERS: {"rows": [row(831, CUSTOMERS)]}})

        result = self.sync._establish_baseline("PRIMARY", client=log, actor="admin@example.test")

        self.assertTrue(result["seeded"])
        state = self.store.written[-1]
        self.assertEqual(state["actor"], "admin@example.test")
        self.assertEqual(state["modules"][CUSTOMERS]["seq"], 831)
        self.assertEqual(self.store.verified, [])

    def test_overlapping_native_pages_yield_each_event_once_but_count_as_incomplete(self):
        # 2026-10-11 起对方每页多给一行、第 2 页从上一页最后一行开始，并且按日期时间排：
        # 翻过第 1 页时同一秒压页边的行可能两页都没出现 —— 照常补翻到的，但算不完整（交整组重读）。
        from pearnly_dms.client.eventlog_reader import EventLogLimits, Usage
        rows = [row(seq, CUSTOMERS, target=f"C{seq}") for seq in range(20, 10, -1)]
        log = FakeEventLog({CUSTOMERS: {"rows": rows}}, history={CUSTOMERS: 10})
        limits = EventLogLimits(page_size=3)

        events, newest, complete = self.sync._new_events(log, CUSTOMERS, cursor=10, limits=limits,
                                                         usage=Usage(limits))

        self.assertEqual([event.seq for event in events], list(range(20, 10, -1)))
        self.assertEqual((newest, complete), (20, False))

    def test_new_events_found_on_the_first_page_are_complete(self):
        from pearnly_dms.client.eventlog_reader import EventLogLimits, Usage
        rows = [row(seq, CUSTOMERS, target=f"C{seq}") for seq in (13, 12, 11)]
        log = FakeEventLog({CUSTOMERS: {"rows": rows}}, history={CUSTOMERS: 10})
        limits = EventLogLimits(page_size=5)

        events, newest, complete = self.sync._new_events(log, CUSTOMERS, cursor=10, limits=limits,
                                                         usage=Usage(limits))

        self.assertEqual(([event.seq for event in events], newest, complete), ([13, 12, 11], 13, True))

    def test_head_older_than_the_watermark_is_unknown(self):
        # 记录被删（对方新增"按日期删除使用记录"）：最新一行比水位还旧 → 整组重读，不当没变化。
        self.store.state = self.baseline(**{CARS: {"seq": 900, "dirty_since": 0}})
        log = FakeEventLog({CARS: {"rows": [row(850, CARS)]}}, history=self.history_without(CARS))

        self.catch_up(log)

        self.assertEqual([item["dataset"] for item in self.manual.requests], ["vehicles"])
        self.assertEqual(self.store.written[-1]["modules"][CARS]["seq"], 850)

    def test_reaching_the_end_without_the_watermark_is_unknown(self):
        # 新事件都在，但水位那一行已经不在了：中间少没少不知道 → 整组重读，能认出的照常补。
        self.store.state = self.baseline(**{CUSTOMERS: {"seq": 831, "dirty_since": 0}})
        self.store.customers[CODE] = "59"
        log = FakeEventLog({CUSTOMERS: {"rows": [row(833, CUSTOMERS), row(832, CUSTOMERS)]}},
                           history=self.history_without(CUSTOMERS))

        self.catch_up(log)

        entry = self.store.written[-1]["modules"][CUSTOMERS]
        self.assertEqual(entry["seq"], 833)
        self.assertTrue(entry["dirty_since"])
        self.assertEqual([item["dataset"] for item in self.manual.requests], ["customers"])
        self.assertEqual(self.store.verified[0][0], "59")


if __name__ == "__main__":
    unittest.main()
