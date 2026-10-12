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
        if "AND root_id = %s" in query:
            root, reader = values
            return [Row(name=row["name"], title=row["title"], folder=row.get("folder")) for row in self.rules.values()
                    if row.get("enabled") and row.get("root_id") == root and self._reader(row) == reader]
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
                   "pearnly_cowork.connections": stub(
                       access=stub(require_connection=lambda doc: None),
                       preparation=stub(reported_masters=lambda doc, key: self.masters),
                       service=stub(targets=lambda name: {"groups": [group]}, stock_acc_group=lambda *a: {},
                                    records=lambda *a: [{"name": "C1", "adapter": "express", "label": "Express"}])),
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
