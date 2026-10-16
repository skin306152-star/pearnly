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
        self.files, self.queries = [], []

    def savepoint(self, name):
        self.points[name] = copy.deepcopy(self.rules)

    def rollback(self, save_point=None):
        self.rollbacks.append(save_point)
        self.rules = copy.deepcopy(self.points[save_point])

    @staticmethod
    def _reader(row):
        return row.get("reader") or row.get("connection")

    def sql(self, query, values=None, as_dict=False):
        query = " ".join(query.split())
        if "GROUP BY rule, state, last_code" in query:  # 月结台账：这个月登记的文件（`rules.month_files`）
            self.queries.append((" ".join(query.split()), values))
            *names, start, end, pos = values
            counted = {}
            for row in self.files:
                if row["rule"] in names and start <= row["registered_at"] < end and (
                        row["state"] in ("failed", "not_invoice")
                        or (row["state"] == "source_ready" and row["last_code"] == pos)):
                    key = (row["rule"], row["state"], row["last_code"])
                    counted[key] = counted.get(key, 0) + 1
            return [Row(rule=rule, state=state, last_code=code, total=total)
                    for (rule, state, code), total in counted.items()]
        if "COUNT(*)" in query:
            if "WHERE reader_device = %s" in query:  # 每台电脑最多 50 个（ADR-021 第 4 批按电脑计）
                return [[sum(1 for row in self.rules.values() if row.get("reader_device") == values[0])]]
            return [[sum(1 for row in self.rules.values() if self._reader(row) == values[0]
                         and not row.get("reader_device"))]]
        if "WHERE enabled = %s AND root_id = %s" in query:
            enabled, root, reader = values
            same = (lambda row: row.get("reader_device") == reader) if "AND reader_device = %s" in query else (
                lambda row: self._reader(row) == reader and not row.get("reader_device"))
            return [Row(name=row["name"], title=row["title"], folder=row.get("folder")) for row in self.rules.values()
                    if int(row.get("enabled") or 0) == enabled and row.get("root_id") == root and same(row)]
        if "WHERE workspace = %s AND enabled = %s" in query:
            return [Row({key: row.get(key) for key in ("name", "title", "connection", "reader", "reader_device",
                                                        "root_id", "folder")})
                    for row in self.rules.values() if int(row.get("enabled") or 0) == values[1]]
        raise AssertionError(query)

    def get_all(self, doctype, filters=None, pluck=None, **_ignored):
        return [name for name, row in self.rules.items()
                if all(row.get(key) == value for key, value in (filters or {}).items())]

    def get_value(self, doctype, name, field, *args, **kwargs):
        if field == "report_json":
            return "{}"
        if isinstance(field, (list, tuple)) and name in self.rules:
            # 改任务时读原来的读者（`rules.save` 的 keep）
            return Row({key: self.rules[name].get(key) for key in field})
        return None

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
        # 各条连接上的电脑（`credentials.pool_peers`，ADR-021 第 4 批）：C1 池里一台 PC1，MR.ERP 的 EX 上一台 PX
        self.members = {"C1": {"PC1": {"label": "Office PC", "online": True, "version": "1.6.0"}},
                        "EX": {"PX": {"label": "Front desk", "online": True, "version": "1.6.0"}}}
        # 跟随公司当年账套（T1）：所选账套属于哪家公司主档（`follow.company_of` → hooks → `companies.tasks`）
        self.company_link = {"company": "", "branch": ""}
        self.company_asked = []
        self.rules = self._rules()

    def _company_of(self, connection, account_set_key):
        self.company_asked.append((connection, account_set_key))
        return dict(self.company_link)

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

        def read_agent(name, reader_device=""):
            # 本机情况按（电脑，成员连接）存：先看「电脑|连接」的；没有时这条连接上登记的电脑读按连接写的那份
            own = self.agents.get(f"{reader_device}|{name}")
            if own is not None:
                return copy.deepcopy(own)
            if reader_device in self.members.get(name, {}):
                return copy.deepcopy(self.agents.get(name, {}))
            return {}

        agent = stub(read=read_agent, fresh=lambda state: self.fresh)
        readers = _load("folder_readers_batch_under_test", APP / "folder_intake/readers.py", {
            "frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
            "pearnly_cowork.companion": stub(credentials=stub(
                pool_peers=lambda connection: copy.deepcopy(self.members.get(connection, {})),
                device_id=lambda credential: credential)),
            "pearnly_cowork.folder_intake": stub(agent=agent, contracts=contracts),
            "pearnly_cowork.folder_intake.access": stub(COMPANY_FOLDER="CF", FILE="F", RULE=RULE, RUN="U")})
        self.readers = readers
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
                       agent=agent, readers=readers,
                       contracts=contracts, destinations=stub(supported=lambda adapter: adapter in ("express", "mrerp")),
                       inbox=stub(), follow=stub(company_of=self._company_of)),
                   "pearnly_cowork.folder_intake.access": stub(FILE="F", RULE=RULE, RUN="U", read=self.store.read,
                                                               write=self.store.write),
                   "pearnly_cowork.targets.identity": stub(normalize_express_account_key=lambda v: v)}
        rules = _load("folder_rules_batch_under_test", APP / "folder_intake/rules.py", modules)
        self.real_project = rules.project
        rules.project = lambda rule: dict(rule)
        return rules

    @staticmethod
    def values(**extra):
        return {"title": "Bills", "connection": "C1", "account_set_key": "Y1", "direction": "purchase",
                "posting_kind": "service", "root_id": ROOT_A, "folder": "A", "run_day": 1, "run_time": "22:00", **extra}


class FollowCompanyYear(Harness):
    """T1：保存任务时由服务端按所选账套反查公司主档与分店记下（前端不改、报文里传的不收），之后到点按单据日期换账套。"""

    def test_saving_records_the_company_and_branch_of_the_chosen_account_set(self):
        self.company_link = {"company": "CO-1", "branch": "00012"}
        saved = self.rules.save(self.values(company="FORGED", company_branch="99999"))
        self.assertEqual((saved["company"], saved["company_branch"], saved["account_set_key"]), ("CO-1", "00012", "Y1"))
        self.assertEqual(self.company_asked, [("C1", "Y1")])  # 按目录里的那份账套问，不按报文

    def test_an_account_set_of_no_single_company_keeps_the_task_fixed(self):
        saved = self.rules.save(self.values())
        self.assertEqual((saved["company"], saved["company_branch"], saved["account_set_key"]), ("", "", "Y1"))

    def test_saving_again_follows_the_account_set_now_chosen(self):
        self.company_link = {"company": "CO-1", "branch": ""}
        name = self.rules.save(self.values())["name"]
        self.company_link = {"company": "", "branch": ""}  # 改选了一份对不上公司的账套：不留旧公司
        saved = self.rules.save(self.values(name=name))
        self.assertEqual((saved["company"], saved["company_branch"]), ("", ""))


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


class ReaderDevices(Harness):
    """读者 =（电脑，成员连接）（ADR-021 第 4 批，方案 2.7）：池里每台电脑都能读文件夹；只给连接的旧写法按根目录认电脑；
    每台电脑最多 50 个任务；同一台电脑不分成员按根目录比重叠；读它的电脑被移除时列表标出来。"""
    ROOT_C = "c" * 16

    def setUp(self):
        super().setUp()
        self.members["C1"]["PC2"] = {"label": "Accounting PC", "online": False, "version": "1.6.0"}
        self.agents["PC2|C1"] = {"roots": [{"id": self.ROOT_C, "label": "E:\\Scans", "folders": ["A"]}],
                                 "version": "1.6.0", "device": "ACC-02"}

    def test_options_list_every_computer_of_the_pool_with_its_reader_id(self):
        readers = self.rules.options("C1")["readers"]
        self.assertEqual([(row["reader"], row["connection"], row["device"]) for row in readers],
                         [("PC1|C1", "C1", "PC1"), ("PC2|C1", "C1", "PC2")])
        self.assertEqual([row["label"] for row in readers], ["Office PC", "ACC-02"])  # 同步报的电脑名优先
        self.assertEqual(self.rules.options("C1")["roots"][0]["id"], ROOT_A)  # 旧字段：本连接第一台

    def test_a_bare_connection_is_resolved_by_its_root_and_a_reader_id_must_hold_the_root(self):
        self.rules.save(self.values(root_id=self.ROOT_C, folder="A"))
        row = self.store.rules["R1"]
        self.assertEqual((row["reader"], row["reader_device"]), ("C1", "PC2"))
        self.rules.save(self.values(reader="PC1|C1", folder="B"))
        self.assertEqual(self.store.rules["R2"]["reader_device"], "PC1")
        with self.assertRaises(Exception) as caught:
            self.rules.save(self.values(reader="PC2|C1", folder="C"))  # PC2 没报过 ROOT_A
        self.assertIn("root_invalid", str(caught.exception))
        with self.assertRaises(Exception):
            self.rules.save(self.values(reader="PC9|C1", folder="C"))  # 不在这条连接上的电脑
        # 两台都报了同一个根目录（各自映射了同一个盘符）：只给连接、又不是改任务时，不猜是哪台（第 4 批审查 M2）
        self.agents["PC1|C1"] = {"roots": [{"id": self.ROOT_C, "label": "S:\\Scans", "folders": ["A"]},
                                           *self.agents["C1"]["roots"]], "version": "1.6.0", "at": "2026-10-12T09:00:00"}
        with self.assertRaises(Exception) as caught:
            self.rules.save(self.values(root_id=self.ROOT_C, folder="A\\B", title="New"))
        self.assertIn("root_invalid", str(caught.exception))
        self.assertEqual(sorted(self.store.rules), ["R1", "R2"])
        self.rules.save(self.values(reader="PC1|C1", root_id=self.ROOT_C, folder="B", title="Mine"))  # 编号照常
        self.assertEqual(self.store.rules["R3"]["reader_device"], "PC1")
        # 改任务时只给连接：原来那台还报着这个根目录就还是它（PC1 刚同步过也不换）
        self.rules.save(self.values(name="R1", root_id=self.ROOT_C, folder="A", title="Renamed"))
        self.assertEqual(self.store.rules["R1"]["reader_device"], "PC2")

    def test_fifty_tasks_per_computer_not_per_connection(self):
        self.rules.MAX_RULES = 1
        self.rules.save(self.values(reader="PC1|C1"))
        self.rules.save(self.values(reader="PC2|C1", root_id=self.ROOT_C, folder="A"))  # 同一条连接的另一台照样能建
        with self.assertRaises(Exception) as caught:
            self.rules.save(self.values(reader="PC1|C1", folder="B"))
        self.assertIn("too_many", str(caught.exception))

    def test_the_same_computer_overlaps_across_its_members(self):
        """同一台电脑上 Express 与 MR.ERP 两个成员授权过同一个根目录：任务按电脑比，同一文件夹不能各跑一遍。"""
        self.members["EX"]["PC1"] = dict(self.members["C1"]["PC1"])
        self.agents["PC1|EX"] = copy.deepcopy(self.agents["C1"])
        self.rules.save(self.values(reader="PC1|C1", folder="A"))
        with self.assertRaises(Exception) as caught:
            self.rules.save(self.values(connection="EX", reader="PC1|EX", folder="A\\B", posting_kind="",
                                        payment="credit"))
        self.assertIn("overlap_with", str(caught.exception))

    def test_the_list_marks_a_task_whose_computer_was_removed(self):
        self.rules.save(self.values(reader="PC2|C1", root_id=self.ROOT_C, folder="A"))
        self.rules.project = self.real_project
        self.rules.counts = self.rules.results = lambda *args, **kwargs: {}
        del self.members["C1"]["PC2"]
        row = self.rules.listing()["rules"][0]
        self.assertEqual((row["reader"], row["reader_device"], row["reader_id"], row["reader_gone"]),
                         ("C1", "PC2", "PC2|C1", True))

    def test_waiting_and_continues_take_a_reader_id_or_a_bare_connection(self):
        self.assertEqual(self.rules.waiting("PC1|C1", ROOT_A, ""), {"files": 9, "partial": True})
        self.assertEqual(self.rules.waiting("C1", ROOT_A, ""), {"files": 9, "partial": True})
        self.assertIsNone(self.rules.waiting("PC2|C1", ROOT_A, ""))  # PC2 没有这个根目录


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


class MonthFiles(Harness):
    """月结台账「收单」一段（数字会计员第 1 期）：任务这个月登记的文件里读不了、不是发票、等人认列的各几份，一次分组查询。"""

    def test_files_registered_this_month_are_counted_per_task(self):
        day = datetime(2026, 9, 10)
        self.store.files = [
            {"rule": "R1", "state": "failed", "last_code": "", "registered_at": day},
            {"rule": "R1", "state": "failed", "last_code": "x", "registered_at": day},
            {"rule": "R1", "state": "not_invoice", "last_code": "", "registered_at": day},
            {"rule": "R1", "state": "source_ready", "last_code": contracts.POS_LAYOUT_NEEDED, "registered_at": day},
            {"rule": "R1", "state": "source_ready", "last_code": "", "registered_at": day},  # 在路上，不算
            {"rule": "R1", "state": "done", "last_code": "", "registered_at": day},
            {"rule": "R1", "state": "failed", "last_code": "", "registered_at": datetime(2026, 10, 1)},  # 下个月
            {"rule": "R2", "state": "failed", "last_code": "", "registered_at": day},
            {"rule": "R9", "state": "failed", "last_code": "", "registered_at": day}]  # 没问的任务
        found = self.rules.month_files(["R1", "R2", "R1", ""], datetime(2026, 9, 1), datetime(2026, 10, 1))
        self.assertEqual(found, {"R1": {"failed": 2, "not_invoice": 1, "pos_layout_needed": 1}, "R2": {"failed": 1}})
        self.assertEqual(len(self.store.queries), 1)
        self.assertEqual(self.store.queries[0][1][:2], ("R1", "R2"))
        self.assertEqual(self.rules.month_files([], datetime(2026, 9, 1), datetime(2026, 10, 1)), {})
        self.assertEqual(len(self.store.queries), 1)  # 没有任务就不查


if __name__ == "__main__":
    unittest.main()
