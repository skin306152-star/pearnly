"""定时任务的服务端规则（2026-10-12，`docs/work/2026-10-12-cc-closed-loop.md` 第一批接口约定 D）。不连库、不依赖 Frappe。

- 批量保存（CC 一张「允许」卡片存几个任务）全有或全无：任一个不通过就一个都不存，失败项之后的照样校验、一次报全；
  同一批里前面的任务对后面的「文件夹不能重叠」可见；
- 新任务处理文件夹里已有的文件（`include_existing`），老任务换了文件夹也改成这个口径；
- 提醒开关带了才改，打开 / 换负责人 / 改运行时间时从现在起算；CC 对话编号只从参数取，网页报文传不进来；
- 列表的待处理数、表单的文件夹计数：不知道就是 None，不用 0 冒充。
"""
import copy
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "tests"))
from test_cowork_delete_everything import Row, _frappe, _load  # noqa: E402

contracts = _load("folder_contracts_batch", APP / "folder_intake/contracts.py", {})
desktop_update = _load("connections_desktop_update_batch", APP / "connections/desktop_update.py", {})
RULE = "Cowork Folder Intake Rule"
ROOT_A, ROOT_B = "a" * 16, "b" * 16


class Doc(Row):
    def is_new(self):
        return not self.get("name")

    def update(self, values):
        dict.update(self, values)

    def __setattr__(self, key, value):
        self[key] = value


class Store:
    """内存里的任务表，带保存点：回滚到保存点就还原那一刻的整张表（同一事务里没提交的行对后面的查询可见）。"""

    def __init__(self):
        self.rules, self.points, self.rollbacks, self.writes, self.counter = {}, {}, [], 0, 0

    def savepoint(self, name):
        self.points[name] = copy.deepcopy(self.rules)

    def rollback(self, save_point=None):
        self.rollbacks.append(save_point)
        self.rules = copy.deepcopy(self.points[save_point])

    @staticmethod
    def _reader(row):
        return row.get("reader") or row.get("connection")

    def sql(self, query, values=None, as_dict=False):
        if "COUNT(*)" in query:
            return [[sum(1 for row in self.rules.values() if self._reader(row) == values[0])]]
        if "WHERE enabled = %s AND root_id = %s" in query:
            enabled, root, reader = values
            return [Row(name=row["name"], title=row["title"], folder=row.get("folder")) for row in self.rules.values()
                    if int(row.get("enabled") or 0) == enabled and row.get("root_id") == root
                    and self._reader(row) == reader]
        raise AssertionError(query)

    def get_all(self, doctype, filters=None, pluck=None, **_ignored):
        return [name for name, row in self.rules.items()
                if all(row.get(key) == value for key, value in (filters or {}).items())]

    def get_value(self, doctype, name, field, *args, **kwargs):
        return "{}" if field == "report_json" else None

    def write(self, doc):
        self.writes += 1
        if not doc.get("name"):
            self.counter += 1
            doc["name"] = f"R{self.counter}"
        self.rules[doc["name"]] = dict(doc)

    def read(self, doctype, name, for_update=False):
        return Doc(copy.deepcopy(self.rules[name]))


class Harness(unittest.TestCase):
    NOW = datetime(2026, 10, 12, 9, 0)

    def setUp(self):
        self.store = Store()
        self.clock = [self.NOW]
        self.user = types.SimpleNamespace(user="owner@example.com")
        self.fresh = True
        self.agents = {"C1": {"roots": [{"id": ROOT_A, "label": "D:\\Bills", "folders": ["A", "A\\B", "B", "C"],
                                          "counts": {"": 9, "A": 4, "C": 2}, "partial": True}],
                              "pending": {"R1": 3}, "version": "1.4.4"},
                       "EX": {"roots": [{"id": ROOT_B, "label": "S:\\Scans", "folders": ["X"]}], "version": "1.3.23"}}
        self.masters = {}
        self.ensured = []
        self.masters_state = {"state": "ready"}
        self.rules = self._rules()

    def _rules(self):
        connections = {"C1": Row(name="C1", adapter="express", workspace="W", client="", binding_generation=0, label="C1"),
                       "EX": Row(name="EX", adapter="mrerp", workspace="W", client="", binding_generation=0, label="EX")}

        def get_doc(doctype, name):
            if name not in connections:
                raise frappe.DoesNotExistError(name)
            return connections[name]

        stub = types.SimpleNamespace
        frappe = _frappe(self.store, get_doc=get_doc, new_doc=lambda doctype: Doc(), session=self.user,
                         clear_messages=lambda: None)
        group = {"label": "Company", "items": [{"value": "Y1", "label": "2569"}]}
        modules = {"frappe": frappe, "frappe.utils": stub(now_datetime=lambda: self.clock[0]),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub(require=lambda *a: None, default_workspace=lambda: "W")),
                   "pearnly_cowork.companion": stub(devices=stub(reporter_label=lambda connection: "", newest_version=lambda connection: "")),
                   "pearnly_cowork.connections": stub(
                       access=stub(require_connection=lambda doc: None), desktop_update=desktop_update,
                       preparation=stub(reported_masters=lambda doc, key: self.masters),
                       service=stub(targets=lambda name: {"groups": [group]}, stock_acc_group=lambda *a: {},
                                    records=lambda *a: [{"name": "C1", "adapter": "express", "label": "Express"}],
                                    ensure_account_masters=lambda *a: self.ensured.append(a) or dict(self.masters_state))),
                   "pearnly_cowork.folder_intake": stub(
                       agent=stub(read=lambda name: copy.deepcopy(self.agents.get(name, {})), fresh=lambda state: self.fresh),
                       contracts=contracts, destinations=stub(supported=lambda adapter: adapter in ("express", "mrerp")),
                       inbox=stub()),
                   "pearnly_cowork.folder_intake.access": stub(FILE="F", RULE=RULE, RUN="U", read=self.store.read,
                                                               write=self.store.write),
                   "pearnly_cowork.targets.identity": stub(normalize_express_account_key=lambda v: v)}
        rules = _load("folder_rules_batch_under_test", APP / "folder_intake/rules.py", modules)
        rules.project = lambda rule: dict(rule)
        return rules

    @staticmethod
    def values(**extra):
        return {"title": "Bills", "connection": "C1", "account_set_key": "Y1", "direction": "purchase",
                "posting_kind": "service", "root_id": ROOT_A, "folder": "A", "run_day": 1, "run_time": "22:00", **extra}


class BatchSave(Harness):
    def test_nested_folders_in_one_batch_save_nothing(self):
        result = self.rules.save_many(json.dumps([self.values(title="Purchases", folder="A"),
                                                  self.values(title="Sales", folder="A\\B", direction="sales")]))
        self.assertEqual(result, {"saved": [], "failed": [{"task": 2, "title": "Sales",
                                                           "reason": "cowork.folder.overlap_with"}]})
        self.assertEqual(self.store.rules, {})
        self.assertEqual(self.store.rollbacks, ["cowork_folder_batch_1", "cowork_folder_batch"])

    def test_a_valid_batch_saves_every_task(self):
        result = self.rules.save_many([self.values(title=name, folder=name) for name in ("A", "B", "C")])
        self.assertEqual(([row["title"] for row in result["saved"]], result["failed"]), (["A", "B", "C"], []))
        self.assertEqual(sorted(row["folder"] for row in self.store.rules.values()), ["A", "B", "C"])
        self.assertEqual(self.store.rollbacks, [])

    def test_every_refused_task_is_reported_at_once(self):
        result = self.rules.save_many([self.values(title="Late", folder="B", run_day=32), self.values(title="Ok"),
                                       self.values(title="Inside", folder="A\\B"), self.values(title="", folder="C")])
        self.assertEqual([(row["task"], row["title"]) for row in result["failed"]], [(1, "Late"), (3, "Inside"), (4, "")])
        self.assertEqual(result["saved"], [])
        self.assertEqual(self.store.rules, {})

    def test_the_batch_shape_is_checked_before_anything_is_saved(self):
        for items in ([], [self.values()] * 21, ["x"], {"tasks": []}, [self.values(name="R9"), self.values(name="R9")]):
            with self.assertRaises(Exception):
                self.rules.save_many(items)
        self.assertEqual(self.store.writes, 0)
        self.assertEqual(self.rules.BATCH_MAX, 20)

    def test_the_conversation_comes_only_from_the_caller(self):
        self.rules.save_many([self.values(cc_session="sesn_from_the_page")], cc_session="sesn_from_cc_1")
        self.rules.save(self.values(folder="B", cc_session="sesn_from_the_page"))
        self.rules.save(self.values(folder="C"), cc_session="bad id")
        stored = {row["folder"]: row.get("cc_session") for row in self.store.rules.values()}
        self.assertEqual(stored, {"A": "sesn_from_cc_1", "B": None, "C": None})


class SaveRules(Harness):
    def _saved(self, name="R1"):
        return self.store.rules[name]

    def test_new_tasks_include_existing_files_and_start_without_reminders(self):
        self.rules.save(self.values())
        row = self._saved()
        self.assertEqual((row["include_existing"], row["remind"], row.get("remind_since")), (1, 0, None))
        self.rules.save(self.values(folder="B", remind=True))
        self.assertEqual((self._saved("R2")["remind"], self._saved("R2")["remind_since"]), (1, self.NOW))

    def test_an_old_task_moves_to_the_new_rule_only_when_its_folder_changes(self):
        self.rules.save(self.values())
        self.store.rules["R1"]["include_existing"] = 0  # 加列前建的老任务
        self.rules.save(self.values(name="R1", title="Renamed"))
        self.assertEqual(self._saved()["include_existing"], 0)
        self.rules.save(self.values(name="R1", folder="C"))
        self.assertEqual(self._saved()["include_existing"], 1)

    def test_a_folder_name_that_differs_only_in_case_is_the_same_folder(self):
        """business-logic-2：Windows 上「Purchases」与「purchases」是同一个文件夹，老任务不因此改成处理已有文件。"""
        self.rules.save(self.values(folder="A"))
        self.store.rules["R1"]["include_existing"] = 0
        self.rules.save(self.values(name="R1", folder="a", remind=1))
        self.assertEqual((self._saved()["include_existing"], self._saved()["folder"]), (0, "a"))

    def test_a_new_task_on_a_paused_tasks_folder_continues_it(self):
        """business-logic-1：照「请停用后新建定时任务」做 —— 新任务的文件夹与停用的任务重叠，就是接续它，只收此后放进来的
        （小助手拍底），不把已入账的历史票据再识别、再推一遍；与停用任务不重叠的新任务照旧处理已有文件。"""
        self.rules.save(self.values(title="Old purchases", folder="A"))
        self.rules.set_enabled("R1", 0)
        self.assertEqual(self.rules.continues_from("C1", ROOT_A, "A\\B"), "Old purchases")
        self.assertEqual(self.rules.continues_from("C1", ROOT_A, "a"), "Old purchases")  # 不分大小写
        self.assertIsNone(self.rules.continues_from("C1", ROOT_A, "C"))
        self.assertIsNone(self.rules.continues_from("C1", ROOT_A, "A", current="R1"))  # 不比自己
        self.assertIsNone(self.rules.continues_from("NOPE", ROOT_A, "A"))  # 没有这台小助手的使用权
        self.assertIsNone(self.rules.continues_from("C1", ROOT_A, "..\\x"))
        self.rules.save(self.values(title="New purchases", folder="A"))
        self.assertEqual(self._saved("R2")["include_existing"], 0)
        self.rules.save(self.values(title="Sales", folder="C", direction="sales"))
        self.assertEqual(self._saved("R3")["include_existing"], 1)
        # 一个已有任务换到停用任务的文件夹里：也只收此后放进来的，启用时刻记成现在（小助手按它重新拍底）
        self.rules.set_enabled("R2", 0)
        self.clock[0] = self.NOW + timedelta(days=2)
        self.rules.save(self.values(name="R3", title="Sales", folder="A\\B", direction="sales"))
        self.assertEqual((self._saved("R3")["include_existing"], self._saved("R3")["active_since"]),
                         (0, self.NOW + timedelta(days=2)))
        # 没换文件夹的改动不重算
        self.rules.save(self.values(name="R3", title="Sales 2", folder="A\\B", direction="sales"))
        self.assertEqual(self._saved("R3")["active_since"], self.NOW + timedelta(days=2))

    def test_the_reminder_is_kept_unless_given_and_restarts_when_its_time_or_owner_changes(self):
        self.rules.save(self.values(remind=1))
        self.clock[0] = self.NOW + timedelta(hours=1)
        self.rules.save(self.values(name="R1", title="Renamed"))  # 网页表单没带 remind：保留
        self.assertEqual((self._saved()["remind"], self._saved()["remind_since"]), (1, self.NOW))
        self.rules.save(self.values(name="R1", run_time="21:30"))
        self.assertEqual(self._saved()["remind_since"], self.NOW + timedelta(hours=1))
        self.clock[0] = self.NOW + timedelta(hours=2)
        self.user.user = "colleague@example.com"
        self.rules.save(self.values(name="R1", run_time="21:30"))
        self.assertEqual(self._saved()["remind_since"], self.NOW + timedelta(hours=2))
        self.rules.save(self.values(name="R1", remind="0"))
        self.assertEqual(self._saved()["remind"], 0)

    def test_chosen_reminder_times_are_checked_stored_and_restart_the_reminder(self):
        """2026-10-13：提醒时刻带了才改（存规范化后的 JSON），带了而没带 remind 就打开提醒；改了从现在起算，没改不重算；
        不合规的整条不存。老任务没选过，生效的就是默认那组。"""
        self.rules.save(self.values())
        self.assertEqual((self._saved()["remind"], self._saved().get("remind_times")), (0, None))
        self.assertEqual(self.rules._times(self._saved()), contracts.DEFAULT_REMIND_TIMES)
        self.clock[0] = self.NOW + timedelta(hours=1)
        times = [{"days_before": 0, "time": "07:05"}, {"days_before": 3, "time": "16:45"}]
        self.rules.save(self.values(name="R1", remind_times=times))
        row = self._saved()
        self.assertEqual((row["remind"], json.loads(row["remind_times"]), row["remind_since"]),
                         (1, [{"days_before": 3, "time": "16:45"}, {"days_before": 0, "time": "07:05"}],
                          self.NOW + timedelta(hours=1)))
        # 网页表单只改名（没带提醒时刻）：保留；同一组（换了顺序）：不重算起点
        self.clock[0] = self.NOW + timedelta(hours=2)
        self.rules.save(self.values(name="R1", title="Renamed"))
        self.rules.save(self.values(name="R1", remind=1, remind_times=list(reversed(times))))
        self.assertEqual((json.loads(self._saved()["remind_times"])[0]["time"], self._saved()["remind_since"]),
                         ("16:45", self.NOW + timedelta(hours=1)))
        # 换一组：从现在起算
        self.rules.save(self.values(name="R1", remind_times=json.dumps([{"days_before": 1, "time": "09:30"}])))
        self.assertEqual(self._saved()["remind_since"], self.NOW + timedelta(hours=2))
        # 关提醒、同时带提醒时刻：照存，提醒保持关
        self.rules.save(self.values(name="R1", remind=0, remind_times=times))
        self.assertEqual((self._saved()["remind"], len(json.loads(self._saved()["remind_times"]))), (0, 2))
        writes = self.store.writes
        for bad in ([{"days_before": 8, "time": "08:00"}], [{"days_before": 1, "time": "8:00"}],
                    [{"days_before": 1, "time": "08:00"}] * 2 + [{"days_before": 2, "time": "08:00"}] * 2, "x"):
            with self.assertRaises(Exception, msg=bad) as caught:
                self.rules.save(self.values(name="R1", remind_times=bad))
            self.assertIn("cowork.folder.invalid", str(caught.exception))
        self.assertEqual(self.store.writes, writes)
        # 新任务只给提醒时刻：就是要提醒
        self.rules.save(self.values(folder="B", remind_times=[{"days_before": 0, "time": "06:00"}]))
        self.assertEqual((self._saved("R2")["remind"], self._saved("R2")["remind_since"]), (1, self.NOW + timedelta(hours=2)))

    def test_a_same_day_reminder_that_can_never_fire_is_refused(self):
        """当天的提醒要早于运行时间（22:00）：不早于的整条不存，CC 与两个表单都拿到同一句错；改运行时间让已有的当天提醒
        发不出也一样拒；提醒关着时不查（表单关着提醒不摆提醒时刻），打开时再查。"""
        self.rules.save(self.values())
        writes = self.store.writes
        for late in ("22:00", "23:15"):
            with self.assertRaises(Exception) as caught:
                self.rules.save(self.values(name="R1", remind_times=[{"days_before": 0, "time": late}]))
            self.assertIn("cowork.folder.remind_same_day", str(caught.exception))
            with self.assertRaises(Exception) as caught:
                self.rules.set_enabled("R1", None, remind_times=[{"days_before": 0, "time": late}])
            self.assertIn("cowork.folder.remind_same_day", str(caught.exception))
        with self.assertRaises(Exception):  # 新任务也一样
            self.rules.save(self.values(folder="B", remind=1, remind_times=[{"days_before": 0, "time": "22:30"}]))
        self.assertEqual((self.store.writes, self._saved().get("remind_times"), self._saved()["remind"]), (writes, None, 0))
        # 早于运行时间：存下
        self.rules.save(self.values(name="R1", remind_times=[{"days_before": 0, "time": "21:30"}]))
        self.assertEqual((self._saved()["remind"], json.loads(self._saved()["remind_times"])),
                         (1, [{"days_before": 0, "time": "21:30"}]))
        # 运行时间改到 21:00：那条当天 21:30 永远发不出，整条不存（同一句错）；连同提醒时刻一起改就行
        with self.assertRaises(Exception) as caught:
            self.rules.save(self.values(name="R1", run_time="21:00"))
        self.assertIn("cowork.folder.remind_same_day", str(caught.exception))
        self.assertEqual(self._saved()["run_time"], "22:00")
        self.rules.save(self.values(name="R1", run_time="21:00", remind_times=[{"days_before": 0, "time": "20:00"}]))
        self.assertEqual(self._saved()["run_time"], "21:00")
        # 提醒关着：存下的当天提醒不查；再打开（启停接口）时查
        self.rules.save(self.values(name="R1", remind=0, run_time="06:00"))
        self.assertEqual((self._saved()["remind"], self._saved()["run_time"]), (0, "06:00"))
        with self.assertRaises(Exception) as caught:
            self.rules.set_enabled("R1", None, remind=True)
        self.assertIn("cowork.folder.remind_same_day", str(caught.exception))
        self.assertEqual(self._saved()["remind"], 0)
        self.rules.set_enabled("R1", 0)  # 只停用：不因提醒拒
        self.assertEqual(self._saved()["enabled"], 0)

    def test_set_enabled_changes_the_reminder_times(self):
        self.rules.save(self.values())
        self.clock[0] = self.NOW + timedelta(hours=1)
        times = [{"days_before": 2, "time": "10:15"}]
        self.rules.set_enabled("R1", None, remind_times=times, cc_session="sesn_cc_2")
        row = self._saved()
        self.assertEqual((row["remind"], json.loads(row["remind_times"]), row["remind_since"], row["cc_session"]),
                         (1, times, self.NOW + timedelta(hours=1), "sesn_cc_2"))
        writes = self.store.writes
        self.clock[0] = self.NOW + timedelta(hours=2)
        self.rules.set_enabled("R1", None, remind_times=json.dumps(times))  # 同一组：不写
        self.assertEqual(self.store.writes, writes)
        self.rules.set_enabled("R1", None, remind=False, remind_times=[])  # 空 = 默认那组，提醒关
        row = self._saved()
        self.assertEqual((row["remind"], json.loads(row["remind_times"])), (0, contracts.DEFAULT_REMIND_TIMES))
        with self.assertRaises(Exception):
            self.rules.set_enabled("R1", None, remind_times=[{"days_before": 1}])
        self.assertEqual(self.rules._times({"remind_times": "{broken"}), contracts.DEFAULT_REMIND_TIMES)

    def test_set_enabled_changes_only_what_is_given(self):
        self.rules.save(self.values())
        revision, writes = self._saved()["rule_revision"], self.store.writes
        self.rules.set_enabled("R1")
        self.assertEqual(self.store.writes, writes)  # 什么都没给：不写
        self.clock[0] = self.NOW + timedelta(hours=1)
        self.rules.set_enabled("R1", None, remind=True, cc_session="sesn_cc_2")
        row = self._saved()
        self.assertEqual((row["enabled"], row["remind"], row["remind_since"], row["cc_session"], row["rule_revision"]),
                         (1, 1, self.NOW + timedelta(hours=1), "sesn_cc_2", revision))
        self.rules.set_enabled("R1", 0)
        self.assertEqual((self._saved()["enabled"], self._saved()["rule_revision"]), (0, revision + 1))

    def test_only_the_owners_own_conversation_is_kept(self):
        """security-3：同事在自己的对话里替负责人开关提醒或停用，不把负责人的对话换成同事的（提醒卡片仍回负责人那段）；
        同事重新启用就成了负责人，对话跟着换成他的。"""
        self.rules.save(self.values(remind=1), cc_session="sesn_owner_1")
        self.user.user = "colleague@example.com"
        self.rules.set_enabled("R1", None, remind=True, cc_session="sesn_colleague_1")
        self.rules.set_enabled("R1", None, remind=False, cc_session="sesn_colleague_1")
        self.assertEqual((self._saved()["cc_session"], self._saved()["remind"], self._saved()["rule_owner"]),
                         ("sesn_owner_1", 0, "owner@example.com"))
        self.rules.set_enabled("R1", 0, cc_session="sesn_colleague_1")
        self.assertEqual(self._saved()["cc_session"], "sesn_owner_1")
        self.rules.set_enabled("R1", 1, cc_session="sesn_colleague_1")
        self.assertEqual((self._saved()["cc_session"], self._saved()["rule_owner"]),
                         ("sesn_colleague_1", "colleague@example.com"))

    def test_turning_back_on_restarts_the_schedule_floor_owner_and_reminder(self):
        self.rules.save(self.values(remind=1))
        self.rules.set_enabled("R1", "0")
        self.clock[0] = self.NOW + timedelta(days=3)
        self.user.user = "colleague@example.com"
        self.rules.set_enabled("R1", "1")
        row = self._saved()
        later = self.NOW + timedelta(days=3)
        self.assertEqual((row["enabled"], row["active_since"], row["rule_owner"], row["remind_since"]),
                         (1, later, "colleague@example.com", later))
        self.assertEqual(row["include_existing"], 1)  # 新任务重新启用不改口径（小助手不重新拍底）


class CountsAndLabels(Harness):
    def test_folder_counts_for_new_task_cards(self):
        self.assertEqual(self.rules.waiting("C1", ROOT_A, "a"), {"files": 4, "partial": True})
        self.assertEqual(self.rules.waiting("C1", ROOT_A, "B"), {"files": 0, "partial": True})
        self.assertEqual(self.rules.waiting("C1", ROOT_A, ""), {"files": 9, "partial": True})
        for args in (("C1", ROOT_A, "Elsewhere"), ("C1", ROOT_B, ""), ("C1", ROOT_A, "..\\x"), ("NOPE", ROOT_A, "A"),
                     ("EX", ROOT_B, "X")):  # 旧小助手没报计数
            self.assertIsNone(self.rules.waiting(*args), args)
        self.fresh = False
        self.assertIsNone(self.rules.waiting("C1", ROOT_A, "A"))

    def test_the_task_list_shows_pending_files_only_when_known(self):
        self.rules.save(self.values())
        self.rules.save(self.values(folder="C"))
        self.rules.set_enabled("R2", 0)
        rows = {row["name"]: row for row in self.rules.listing()["rules"]}
        self.assertEqual((rows["R1"]["waiting"], rows["R1"]["waiting_partial"]), (3, True))
        self.assertEqual((rows["R2"]["waiting"], rows["R2"]["waiting_partial"]), (None, False))  # 停用：小助手不算
        self.fresh = False
        self.assertIsNone({row["name"]: row for row in self.rules.listing()["rules"]}["R1"]["waiting"])

    def test_options_hide_counts_from_a_helper_that_has_not_synced_lately(self):
        self.assertEqual(self.rules.options("C1")["readers"][0]["roots"][0]["counts"], {"": 9, "A": 4, "C": 2})
        self.fresh = False
        root = self.rules.options("C1")["readers"][0]["roots"][0]
        self.assertNotIn("counts", root)
        self.assertNotIn("partial", root)

    def test_the_account_sets_chart_is_requested_when_a_task_is_saved_or_run(self):
        """2026-10-13：定时任务推往电脑没选的账套，科目表从没读过，票全停在「要人选」。存任务、立即运行时就请电脑读。"""
        self.rules.save(self.values())
        self.assertEqual(self.ensured, [("C1", "Y1")])
        self.rules.run_now("R1")
        self.assertEqual(self.ensured[-1], ("C1", "Y1"))
        rolled = len(self.ensured)
        self.rules.save_many([self.values(title="Late", folder="B", run_day=32)])
        self.assertEqual(len(self.ensured), rolled)  # 校验没过的不请读（批量回滚时请求随之撤掉）

    def test_options_say_the_chart_is_being_read(self):
        commits = []
        self.store.commit = lambda: commits.append(1)
        self.masters_state = {"state": "reading", "requested": True}
        options = self.rules.options("C1", "Y1")
        self.assertEqual((options["masters"], options["accounts"]), ({"state": "reading"}, None))
        self.assertEqual(commits, [1])  # 取选项是 GET：请求要自己提交
        self.masters_state = {"state": "ready"}
        self.masters = {"reported_accounts": [{"code": "5300", "name": "Office"}]}
        options = self.rules.options("C1", "Y1")
        self.assertEqual((options["masters"]["state"], options["accounts"]), ("ready", [{"code": "5300", "name": "Office"}]))
        self.assertEqual(commits, [1])

    def test_account_label_and_master_names_for_card_previews(self):
        self.assertEqual(self.rules.account_label("C1", "Y1"), "Company · 2569")
        self.assertEqual(self.rules.account_label("C1", "Y9"), "")
        self.masters = {"reported_accounts": [{"code": "5300", "name": "Office"}, {"code": ""}, "x"],
                        "reported_departments": [{"code": "01", "name": "HQ"}]}
        self.assertEqual(self.rules.master_names("C1", "Y1"), {"accounts": {"5300": "Office"}, "departments": {"01": "HQ"}})
        empty = {"accounts": {}, "departments": {}}
        self.assertEqual(self.rules.master_names("EX", "Y1"), empty)  # MR.ERP 没有 Express 主档
        self.assertEqual(self.rules.master_names("NOPE", "Y1"), empty)


if __name__ == "__main__":
    unittest.main()
