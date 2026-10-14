"""定时任务的前一天提醒（F2）与完成简报（F3）（2026-10-12，`docs/work/2026-10-12-cc-closed-loop.md`）。不连库、不依赖 Frappe。

- 提醒：运行前一天 08:00 / 14:00 各一次；同一负责人、同一运行时刻、同一时段合成一条，最多列 10 个；先查去重键再做权限复核；
  负责人没权限的任务不提醒；打开提醒之前的时段不补发；按负责人语言写好；挂在最近在 CC 里改过的那段对话上；
- 简报：一份 = 同一负责人 + 同一定时时刻，立即运行单独一份；文件都走完且安静 3 分钟才发，最多等 13 小时带「处理中」；
  3 天前的不发；按 run_owner 复核权限；同键已发过也记成 sent；数字与任务列表同一个口径；
- 调度入口两样互不影响；只在 Cowork 站点跑。

共用层提醒服务用替身（同一去重键只记一条，与 `pearnly_core.assistant.notices.post` 同一语义），`notify` 用真的。
"""
import copy
import csv
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
RULE, RUN, FILE = "Cowork Folder Intake Rule", "Cowork Folder Intake Run", "Cowork Folder Intake File"
OWNER = "owner@example.com"


class Row(dict):
    __getattr__ = dict.get

    def __setattr__(self, key, value):
        self[key] = value


def _terms():
    with (APP / "i18n/messages.tsv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    return {lang: {row["key"] or row["en"]: row[lang] for row in rows} for lang in ("en", "zh", "th", "ja")}


TERMS = _terms()


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _datetime(value):
    return value if isinstance(value, datetime) else datetime.fromisoformat(str(value))


class Notices:
    """共用层提醒服务的替身：同一去重键只记一条，再发回 created False（不再推送）。"""

    def __init__(self):
        self.rows, self.fail, self.foreign, self.asked = {}, set(), set(), []

    def owned_conversation(self, user, candidates):
        """共用层判对话归属的替身：``foreign`` 里的对话不属于收件人（别人的、归档了、换了引擎），按给的先后取第一段。"""
        self.asked.append((user, list(candidates)))
        return next((sid for sid in candidates if sid not in self.foreign), "")

    def post(self, user, kind, text, dedup_key, *, title="", conversation="", reference_doctype="", reference_names=(),
             ttl=0, view=""):
        if user in self.fail:
            raise RuntimeError("notices down")
        if dedup_key in self.rows:
            return {"name": dedup_key, "id": "pnotice_x", "session_id": "", "created": False}
        self.rows[dedup_key] = Row(user=user, kind=kind, text=text, title=title, conversation=conversation,
                                   reference_doctype=reference_doctype, refs=tuple(reference_names), ttl=ttl,
                                   view=view)
        return {"name": dedup_key, "id": "pnotice_x", "session_id": conversation, "created": True}

    def exists(self, dedup_key):
        return dedup_key in self.rows

    def sent(self):
        return list(self.rows.values())


class FakeDB:
    """任务、运行记录、文件三张表（只认本测试用到的查询）。"""

    def __init__(self):
        self.rules, self.runs, self.files = {}, {}, []
        self.commits = self.rollbacks = 0
        self.queries = []

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def exists(self, doctype, name):
        assert doctype == RULE
        return name in self.rules

    def read(self, doctype, name, for_update=False):
        assert doctype == RULE
        return Row(copy.deepcopy(self.rules[name]))

    def get_all(self, doctype, filters=None, fields=None, ignore_permissions=False, **_ignored):
        assert doctype == RULE and ignore_permissions
        self.queries.append("rules")
        found = [row for row in self.rules.values() if all(row.get(key) == value for key, value in (filters or {}).items())]
        return [Row({field: copy.deepcopy(row.get(field)) for field in fields}) for row in found]

    def sql(self, query, values=None, as_dict=False):
        query = " ".join(query.split())
        if query.startswith(f"UPDATE `tab{RUN}` SET brief_state"):
            state, at, key, names = values
            for name in names:
                self.runs[name].update(brief_state=state, brief_at=at, brief_key=key)
            return []
        fields = [field.strip() for field in query.split("SELECT ", 1)[1].split(" FROM", 1)[0].split(",")]
        if f"FROM `tab{RUN}` WHERE brief_state = 'pending'" in query:
            return [Row({field: row.get(field) for field in fields}) for row in self.runs.values()
                    if row["brief_state"] == "pending"]
        if f"FROM `tab{FILE}` WHERE rule IN %s AND run IN %s" in query:
            rules, runs = values
            return [Row({field: row.get(field) for field in fields}) for row in self.files
                    if row["rule"] in rules and row["run"] in runs]
        raise AssertionError(query)


class Harness(unittest.TestCase):
    def setUp(self):
        self.db, self.notices = FakeDB(), Notices()
        self.local = types.SimpleNamespace(lang="en")
        self.langs, self.denied, self.checked, self.errors = {}, set(), [], []
        self.fresh, self.product = True, "cowork"
        self.agents = {"C1": {"roots": [{"id": "RA", "label": "D:\\Bills", "folders": ["A"], "partial": False}],
                              "pending": {"R1": 3}},
                       "C2": {"roots": [{"id": "RB", "label": "\\\\server\\scans"}]}}
        frappe = types.SimpleNamespace(
            conf=types.SimpleNamespace(get=lambda key: self.product if key == "pearnly_product" else None),
            db=self.db, local=self.local, _=lambda key: TERMS[self.local.lang or "en"].get(key, key), _dict=Row,
            get_all=self.db.get_all, log_error=lambda title, message="": self.errors.append(title),
            get_traceback=lambda: "traceback", get_hooks=lambda name: list(self.hooked.get(name) or []),
            get_attr=lambda path: self.attrs[path])
        self.hooked, self.attrs = {}, {}
        package = types.ModuleType("pearnly_cowork.folder_intake")
        modules = {"frappe": frappe,
                   "frappe.utils": types.SimpleNamespace(get_datetime=_datetime, now_datetime=lambda: self.now,
                                                         get_system_timezone=lambda: "Asia/Bangkok"),
                   "frappe.translate": types.SimpleNamespace(get_user_lang=lambda user: self.langs.get(user, "en")),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"), "pearnly_cowork.folder_intake": package,
                   "pearnly_cowork.folder_intake.access": types.SimpleNamespace(RULE=RULE, RUN=RUN, FILE=FILE,
                                                                                read=self.db.read),
                   "pearnly_core": types.ModuleType("pearnly_core"),
                   "pearnly_core.assistant": types.SimpleNamespace(notices=self.notices)}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = datetime(2026, 10, 13, 8, 5)
        package.contracts = self.contracts = _load("folder_contracts_notices", APP / "folder_intake/contracts.py")
        package.agent = types.SimpleNamespace(read=lambda name: copy.deepcopy(self.agents.get(name, {})),
                                              fresh=lambda state: self.fresh and bool(state))
        package.machine = types.SimpleNamespace(owner_ready=self._owner_ready)
        package.notify = _load("folder_notify_notices", APP / "folder_intake/notify.py")
        package.reminders = self.reminders = _load("folder_reminders_under_test", APP / "folder_intake/reminders.py")
        package.briefs = self.briefs = _load("folder_briefs_under_test", APP / "folder_intake/briefs.py")
        self.schedule = _load("folder_schedule_under_test", APP / "folder_intake/schedule.py")

    def _owner_ready(self, rule):
        self.checked.append((rule.rule_owner, rule.connection))
        return (rule.rule_owner, rule.connection) not in self.denied

    def rule(self, name, title="Bills", owner=OWNER, day=14, at="22:00", folder="A", **extra):
        row = Row(name=name, title=title, rule_owner=owner, connection="C1", reader="C1", root_id="RA", folder=folder,
                  run_day=day, run_time=at, enabled=1, remind=1, remind_since=datetime(2026, 10, 1),
                  modified=datetime(2026, 10, 1), cc_session="", result_folder="_Pearnly Results")
        row.update(extra)
        self.db.rules[name] = row
        return row


class Reminders(Harness):
    def remind(self, at):
        self.reminders.run(at)
        return self.notices.sent()

    def test_the_day_before_at_eight_and_two_once_each(self):
        self.rule("R1")
        self.assertEqual(self.remind("2026-10-13 07:55"), [])
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0].user, sent[0].kind, sent[0].title, sent[0].reference_doctype, sent[0].refs, sent[0].ttl),
                         (OWNER, "cowork.folder.reminder", "Scheduled task reminder", RULE, ("R1",), 6 * 3600))
        self.assertEqual(sent[0].text, "«Bills» starts tomorrow at 22:00 (1 d 13 h left). Put the documents in "
                                       "D:\\Bills\\A and make sure they are complete.\n3 files not processed yet.")
        checks = len(self.checked)
        for at in ("2026-10-13 08:10", "2026-10-13 13:55"):
            self.assertEqual(len(self.remind(at)), 1)
        self.assertEqual(len(self.checked), checks)  # 已发过：先查键，不再复核权限
        sent = self.remind("2026-10-13 14:00")
        self.assertEqual(len(sent), 2)
        self.assertIn("(1 d 8 h left)", sent[1].text)
        for at in ("2026-10-14 21:59", "2026-10-14 22:05", "2026-10-15 08:05"):
            self.assertEqual(len(self.remind(at)), 2)
        self.assertGreaterEqual(self.db.commits, 2)

    def test_tasks_for_the_same_owner_and_time_merge_into_one(self):
        self.rule("R1", title="Sales", folder="S")
        self.rule("R2", title="Bills", folder="B")
        self.rule("R3", title="Cash", folder="C")
        self.agents["C1"]["pending"] = {"R2": 5}
        self.agents["C1"]["roots"][0]["partial"] = True  # 数到上限：N+
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].text, "3 scheduled tasks start tomorrow at 22:00 (1 d 13 h left). Put the documents in "
                                       "these folders and make sure they are complete:\n"
                                       "· Bills: D:\\Bills\\B (5+ not processed yet)\n· Cash: D:\\Bills\\C\n"
                                       "· Sales: D:\\Bills\\S")
        self.assertEqual(sent[0].refs, ("R2", "R3", "R1"))

    def test_tasks_read_by_another_pc_get_their_own_reminder(self):
        """security-1：同一负责人、同一时刻，M1 读的客户 X 与 M2 读的客户 Y（\\\\srv 共享盘）各一条提醒，
        各自只带本机的任务名与路径；去重键带电脑，两条都发。"""
        self.rule("T1", title="Client X", folder="X")
        self.rule("T2", title="Client Y", reader="C2", connection="C2", root_id="RB", folder="ClientY")
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual(sorted(row.refs for row in sent), [("T1",), ("T2",)])
        mine = next(row for row in sent if row.refs == ("T1",))
        self.assertNotIn("Client Y", mine.text)
        self.assertNotIn("server", mine.text)
        self.assertEqual(len(self.remind("2026-10-13 09:00")), 2)  # 各自只发一次

    def test_no_line_for_zero_files_waiting(self):
        """interfaces-3：待处理数是 0（文件夹里可能留着处理过的文件）不写「0 份」。"""
        self.rule("R1")
        self.agents["C1"]["pending"] = {"R1": 0}
        self.assertEqual(self.remind("2026-10-13 08:05")[0].text,
                         "«Bills» starts tomorrow at 22:00 (1 d 13 h left). Put the documents in D:\\Bills\\A and make "
                         "sure they are complete.")
        self.rule("R2", title="Cash", folder="C", at="23:00")
        self.rule("R3", title="Sales", folder="S", at="23:00")
        self.agents["C1"]["pending"] = {"R2": 0, "R3": 2}
        self.assertEqual(self.remind("2026-10-13 08:06")[1].text.split("\n")[1:],
                         ["· Cash: D:\\Bills\\C", "· Sales: D:\\Bills\\S (2 not processed yet)"])

    def test_more_than_ten_tasks_list_ten_and_count_the_rest(self):
        for index in range(12):
            self.rule(f"R{index:02}", title=f"Task {index:02}", folder=f"F{index:02}")
        text = self.remind("2026-10-13 08:05")[0].text.split("\n")
        self.assertEqual(len(text), 12)
        self.assertEqual((text[1], text[10], text[11]), ("· Task 00: D:\\Bills\\F00", "· Task 09: D:\\Bills\\F09", "and 2 more"))

    def test_other_owners_and_other_run_times_stay_apart(self):
        self.rule("R1")
        self.rule("R2", owner="colleague@example.com")
        self.rule("R3", at="23:00")
        self.rule("R4", day=15)  # 运行在 15 日：14 日才提醒
        self.assertEqual(sorted((row.user, row.refs) for row in self.remind("2026-10-13 08:05")),
                         [("colleague@example.com", ("R2",)), (OWNER, ("R1",)), (OWNER, ("R3",))])
        # 14 日早上：15 日那个的 08:00 那条；13 日下午没跑到的三条 14:00 提醒在运行前照样补上
        self.assertEqual(len(self.remind("2026-10-14 08:05")), 7)

    def test_turned_off_tasks_and_tasks_without_reminders_get_nothing(self):
        self.rule("R1", enabled=0)
        self.rule("R2", remind=0)
        self.rule("R3", rule_owner="")
        self.rule("R4", run_time="bad")
        self.assertEqual(self.remind("2026-10-13 08:05"), [])

    def test_a_new_run_time_starts_over_and_earlier_slots_are_not_sent(self):
        self.rule("R1", remind_since=datetime(2026, 10, 13, 9, 30))  # 09:30 才打开提醒
        self.assertEqual(self.remind("2026-10-13 10:00"), [])
        self.assertEqual(len(self.remind("2026-10-13 14:05")), 1)
        # 改到 15 日（`rules.save` 把 remind_since 记成那一刻）：13 日不再发，14 日两条照发
        self.db.rules["R1"].update(run_day=15, remind_since=datetime(2026, 10, 13, 15, 0))
        self.assertEqual(len(self.remind("2026-10-13 16:00")), 1)
        self.assertEqual(len(self.remind("2026-10-14 08:05")), 2)
        del self.db.rules["R1"]  # 删了的任务
        self.assertEqual(len(self.remind("2026-10-14 14:05")), 2)

    def test_tasks_the_owner_can_no_longer_use_are_left_out(self):
        self.rule("R1", title="Allowed")
        self.rule("R2", title="Gone", connection="C9")
        self.rule("R3", owner="former@example.com")
        self.denied = {(OWNER, "C9"), ("former@example.com", "C1")}
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual([(row.user, row.refs) for row in sent], [(OWNER, ("R1",))])
        self.assertTrue(sent[0].text.startswith("«Allowed» starts tomorrow"))  # 剩一个就是单条的写法
        self.denied = set()  # 权限恢复，还在时段里：照发
        self.assertEqual(len(self.remind("2026-10-13 09:00")), 2)

    def test_the_file_count_only_when_the_pc_synced_lately(self):
        self.rule("R1")
        self.fresh = False
        self.assertNotIn("not processed yet", self.remind("2026-10-13 08:05")[0].text)
        self.agents = {}  # 小助手一周没同步、记不得根目录：只写规则文件夹
        self.rule("R2", folder="Sales", at="23:00")
        self.assertIn("Put the documents in Sales and", self.remind("2026-10-13 08:06")[1].text)

    def test_written_in_the_owner_language(self):
        self.rule("R1")
        self.langs[OWNER] = "zh"
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual(sent[0].title, "定时任务提醒")
        self.assertEqual(sent[0].text, "「Bills」明天 22:00 开始（还有 1 天 13 小时）。请将资料放入 D:\\Bills\\A，确保资料齐全。\n"
                                       "还有 3 份未处理。")
        self.assertEqual(self.local.lang, "en")
        self.langs[OWNER] = "th"
        text = self.remind("2026-10-13 14:05")[1].text
        self.assertTrue(text.startswith("«Bills» จะเริ่มพรุ่งนี้ เวลา 22:00 น. (อีก 1 วัน 7 ชั่วโมง)"), text)
        self.assertIn("D:\\Bills\\A", text)
        # 14:00 那条跨过午夜才发（停机后补发）：写「今天」
        self.rule("R2", at="22:30")
        self.assertIn("วันนี้ เวลา 22:30 น.", self.remind("2026-10-14 00:10")[2].text)

    def test_the_card_goes_to_the_latest_conversation_about_these_tasks(self):
        self.rule("R1", cc_session="sesn_old_1", modified=datetime(2026, 10, 1))
        self.rule("R2", cc_session="sesn_new_1", modified=datetime(2026, 10, 5))
        self.rule("R3", cc_session="", modified=datetime(2026, 10, 9))
        self.assertEqual(self.remind("2026-10-13 08:05")[0].conversation, "sesn_new_1")
        self.assertEqual(self.notices.asked[-1], (OWNER, ["sesn_new_1", "sesn_old_1"]))

    def test_a_colleagues_conversation_does_not_hide_the_card(self):
        """security-3：最近改过的那条挂着同事的对话（同事替负责人开关过提醒的旧数据）：退回负责人自己的那段对话。"""
        self.rule("R1", cc_session="sesn_owner_1", modified=datetime(2026, 10, 1))
        self.rule("R2", cc_session="sesn_colleague_1", modified=datetime(2026, 10, 5))
        self.notices.foreign = {"sesn_colleague_1"}
        self.assertEqual(self.remind("2026-10-13 08:05")[0].conversation, "sesn_owner_1")

    def test_only_cowork_sites(self):
        self.rule("R1")
        self.product = "dms"
        self.assertEqual(self.remind("2026-10-13 08:05"), [])
        self.assertEqual(self.db.queries, [])

    def test_a_failing_group_does_not_stop_the_others(self):
        self.rule("R1", owner="broken@example.com")
        self.rule("R2")
        self.notices.fail = {"broken@example.com"}
        sent = self.remind("2026-10-13 08:05")
        self.assertEqual([row.user for row in sent], [OWNER])
        self.assertEqual((self.errors, self.db.rollbacks), (["Cowork folder reminder failed"], 1))
        self.notices.fail = set()
        self.assertEqual(len(self.remind("2026-10-13 08:10")), 2)  # 下一轮补上

    def test_the_default_time_is_now(self):
        self.rule("R1")
        self.now = datetime(2026, 10, 13, 14, 1)
        self.reminders.run()
        self.assertIn("(1 d 7 h left)", self.notices.sent()[0].text)


class ChosenReminderTimes(Harness):
    """2026-10-13（用户：提醒时间可自定义，哪天、什么时间都可以）：每个任务最多 3 个「运行日前 0–7 天 + 时间」，
    各在自己的时刻发一次；库里存 JSON（`remind_times`），空的老任务照默认（前一天 08:00、14:00）。"""

    TIMES = json.dumps([{"days_before": 3, "time": "16:45"}, {"days_before": 0, "time": "07:05"},
                        {"days_before": 0, "time": "23:00"}])  # 当天 23:00 晚于运行（22:00）：不发

    def remind(self, at):
        self.reminders.run(at)
        return self.notices.sent()

    def test_each_chosen_time_fires_once_at_its_moment(self):
        self.rule("R1", remind_times=self.TIMES)
        self.assertEqual(self.remind("2026-10-11 16:40"), [])
        sent = self.remind("2026-10-11 16:45")
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0].text, "«Bills» starts on 14/10 at 22:00 (3 d 5 h left). Put the documents in "
                                       "D:\\Bills\\A and make sure they are complete.\n3 files not processed yet.")
        for at in ("2026-10-12 09:00", "2026-10-13 08:05", "2026-10-13 14:05", "2026-10-14 07:00"):
            self.assertEqual(len(self.remind(at)), 1, at)  # 默认的 08:00、14:00 不再发
        sent = self.remind("2026-10-14 07:05")
        self.assertEqual(len(sent), 2)
        self.assertTrue(sent[1].text.startswith("«Bills» starts today at 22:00 (14 h 55 min left)."), sent[1].text)
        for at in ("2026-10-14 21:59", "2026-10-14 23:05", "2026-10-15 08:00"):
            self.assertEqual(len(self.remind(at)), 2, at)

    def test_tasks_with_the_same_moment_merge_and_other_moments_stay_apart(self):
        same = json.dumps([{"days_before": 1, "time": "09:30"}])
        self.rule("R1", title="Sales", folder="S", remind_times=same)
        self.rule("R2", title="Bills", folder="B", remind_times=same)
        self.rule("R3", title="Cash", folder="C", remind_times=json.dumps([{"days_before": 1, "time": "10:00"}]))
        self.rule("R4", title="Stock", folder="K")  # 没选过：前一天 08:00、14:00
        self.rule("R5", title="Late", folder="L", at="23:00", remind_times=same)  # 同一提醒时刻、不同运行时刻
        sent = self.remind("2026-10-13 09:30")
        self.assertEqual(sorted(row.refs for row in sent), [("R2", "R1"), ("R4",), ("R5",)])
        merged = next(row for row in sent if len(row.refs) == 2)
        self.assertTrue(merged.text.startswith("2 scheduled tasks start tomorrow at 22:00 (1 d 12 h left)."), merged.text)
        sent = self.remind("2026-10-13 10:00")
        self.assertEqual((len(sent), sent[-1].refs), (4, ("R3",)))  # R3 自己一条
        self.assertEqual(len(self.remind("2026-10-13 14:00")), 5)  # R4 的 14:00 那条

    def test_moments_before_the_times_were_changed_are_not_sent(self):
        self.rule("R1", remind_times=self.TIMES, remind_since=datetime(2026, 10, 12, 9, 0))
        self.assertEqual(self.remind("2026-10-12 10:00"), [])  # 3 天前那条早于改提醒时刻
        self.assertEqual(len(self.remind("2026-10-14 07:05")), 1)

    def test_a_broken_stored_value_sends_nothing_for_that_task(self):
        self.rule("R1", remind_times="not json")
        self.rule("R2", title="Cash", folder="C")
        self.assertEqual([row.refs for row in self.remind("2026-10-13 08:05")], [("R2",)])

    def test_the_date_is_written_in_the_owner_language(self):
        two_days = json.dumps([{"days_before": 2, "time": "09:00"}])
        self.rule("R1", remind_times=two_days)
        self.langs[OWNER] = "zh"
        self.assertEqual(self.remind("2026-10-12 09:00")[0].text,
                         "「Bills」10 月 14 日 22:00 开始（还有 2 天 13 小时）。请将资料放入 D:\\Bills\\A，确保资料齐全。\n"
                         "还有 3 份未处理。")
        self.rule("R2", title="Cash", folder="C", at="23:00", remind_times=two_days)
        self.langs[OWNER] = "th"
        self.assertTrue(self.remind("2026-10-12 09:01")[1].text.startswith(
            "«Cash» จะเริ่มวันที่ 14/10 เวลา 23:00 น. (อีก 2 วัน 13 ชั่วโมง)"))
        self.rule("R3", title="Sales", folder="S", at="21:00", remind_times=two_days)
        self.langs[OWNER] = "ja"
        self.assertTrue(self.remind("2026-10-12 09:02")[2].text.startswith("「Sales」は10月14日 21:00に開始します（あと 2 日 11 時間）。"))


class Briefs(Harness):
    SLOT = datetime(2026, 10, 14, 8, 0)

    def setUp(self):
        super().setUp()
        self.rule("R1", day=14, at="08:00", cc_session="sesn_cc_1")

    def run_row(self, name, rule="R1", owner=OWNER, slot=SLOT, started=datetime(2026, 10, 14, 8, 1), scanned=True,
                **extra):
        reader = self.db.rules[rule].get("reader") if rule in self.db.rules else "C1"
        row = Row(name=name, rule=rule, connection=reader or "C1", run_owner=owner, slot_at=slot, started_at=started,
                  scanned_at=started if scanned else None, new_files=0, upload_failed=0, note="",
                  brief_state="pending", brief_at=None, brief_key="",
                  brief_group=self.contracts.slot_group(slot) if slot else name)
        row.update(extra)
        self.db.runs[name] = row

    def file_row(self, run, *tickets, rule="R1", state="done", last_code="", registered=datetime(2026, 10, 14, 8, 2)):
        outcome = {f"H{index}": {"status": kind, "reason": reason} for index, (kind, reason, _status) in enumerate(tickets)}
        outcome["_signature"] = [[f"H{index}", status, ""] for index, (_kind, _reason, status) in enumerate(tickets)]
        row = Row(name=f"F{len(self.db.files)}", rule=rule, run=run, state=state, last_code=last_code,
                  outcome_json=json.dumps(outcome), registered_at=registered)
        self.db.files.append(row)
        return row

    def brief(self, at):
        self.briefs.run(at)
        return self.notices.sent()

    def test_sent_once_every_file_has_finished_and_things_are_quiet(self):
        self.run_row("U1", new_files=4)
        self.file_row("U1", ("sent", "", "success"), ("sent", "", "success"), ("held", "choice_needed:account", ""))
        self.file_row("U1", state="not_invoice")
        stuck = self.file_row("U1", state="submitted")
        self.assertEqual(self.brief("2026-10-14 08:30"), [])
        stuck.state = "failed"
        self.assertEqual(self.brief("2026-10-14 08:03"), [])  # 最近一次运行开始（08:01）还不到 3 分钟
        sent = self.brief("2026-10-14 08:30")
        self.assertEqual(len(sent), 1)
        self.assertEqual((sent[0].user, sent[0].kind, sent[0].title, sent[0].refs, sent[0].ttl, sent[0].conversation),
                         (OWNER, "cowork.folder.brief", "Scheduled task results", ("R1",), 24 * 3600, "sesn_cc_1"))
        self.assertEqual(sent[0].text, "Scheduled run at 2026-10-14 08:00 (tasks: 1):\n"
                                       "· Bills: Entered 2 · To check 1 (choose Expense account) · Failed 1 · Not invoices 1\n"
                                       "  Results: D:\\Bills\\A\\_Pearnly Results\\2026-10")
        run = self.db.runs["U1"]
        self.assertEqual((run.brief_state, run.brief_at, run.brief_key),
                         ("sent", datetime(2026, 10, 14, 8, 30), self.contracts.brief_key(OWNER, ["U1"])))
        self.assertEqual(len(self.brief("2026-10-14 09:00")), 1)

    def test_files_still_on_their_way_go_after_thirteen_hours_marked_in_progress(self):
        self.run_row("U1")
        self.file_row("U1", ("sent", "", "success"))
        self.file_row("U1", state="submitted", last_code="insufficient_balance")
        self.file_row("U1", state="recognized", last_code="cowork.folder.waiting_purchases")
        self.assertEqual(self.brief("2026-10-14 21:00"), [])
        text = self.brief("2026-10-14 21:05")[0].text
        self.assertEqual(text.split("\n")[1], "· Bills: Entered 1 · In progress 2 (low balance / waiting for purchases)")
        self.assertEqual(text.split("\n")[-1], "Some files are still in progress.")

    def test_run_now_gets_its_own_brief_and_empty_runs_still_report(self):
        self.run_row("U1")
        self.run_row("U2", slot=None, started=datetime(2026, 10, 14, 10, 0))
        self.file_row("U2", ("sent", "", "success"))
        sent = self.brief("2026-10-14 10:20")
        self.assertEqual([row.text.split("\n")[:2] for row in sent],
                         [["Scheduled run at 2026-10-14 08:00 (tasks: 1):", "· Bills: No new files"],
                          ["Run now (tasks: 1):", "· Bills: Entered 1"]])

    def test_a_stuck_run_now_does_not_hold_back_a_later_one(self):
        """interfaces-1 / frappe-runtime-1 / business-logic-4：09:00 立即运行存货销售 A，有一份等进货一直在路上；
        15:00 立即运行服务任务 B 几分钟走完 —— B 15:10 单独一份、完整；A 到 22:00（满 13 小时）才带「处理中」发。"""
        self.rule("R2", title="Service", folder="B")
        self.run_row("A", slot=None, started=datetime(2026, 10, 14, 9, 0))
        self.file_row("A", state="recognized", last_code="cowork.folder.waiting_purchases",
                      registered=datetime(2026, 10, 14, 9, 1))
        self.run_row("B", rule="R2", slot=None, started=datetime(2026, 10, 14, 15, 0))
        self.file_row("B", ("sent", "", "success"), rule="R2", registered=datetime(2026, 10, 14, 15, 1))
        sent = self.brief("2026-10-14 15:10")
        self.assertEqual([(row.refs, row.text.split("\n")) for row in sent],
                         [(("R2",), ["Run now (tasks: 1):", "· Service: Entered 1",
                                     "  Results: D:\\Bills\\B\\_Pearnly Results\\2026-10"])])
        self.assertEqual((self.db.runs["A"].brief_state, self.db.runs["B"].brief_state), ("pending", "sent"))
        self.assertEqual(len(self.brief("2026-10-14 21:59")), 1)
        late = self.brief("2026-10-14 22:00")[1]
        self.assertEqual((late.refs, late.text.split("\n")[-1]), (("R1",), "Some files are still in progress."))

    def test_each_pc_gets_its_own_brief(self):
        """security-1：同一负责人同一时刻，M1（C1）读的客户 X 与 M2（C2）读的客户 Y 各一份，互不带对方的任务与路径。"""
        self.rule("R2", title="Client Y", connection="C2", reader="C2", root_id="RB", folder="")
        self.run_row("U1")
        self.run_row("U2", rule="R2")
        self.file_row("U2", ("sent", "", "success"), rule="R2")
        sent = self.brief("2026-10-14 08:30")
        self.assertEqual(sorted(row.refs for row in sent), [("R1",), ("R2",)])
        mine = next(row for row in sent if row.refs == ("R1",))
        self.assertNotIn("Client Y", mine.text)
        self.assertNotIn("server", mine.text)
        self.assertEqual({run.brief_state for run in self.db.runs.values()}, {"sent"})

    def test_missing_folders_and_files_not_uploaded(self):
        self.rule("R2", title="Cash", folder="C")
        self.run_row("U1", note="folder_missing")
        self.run_row("U2", rule="R2", upload_failed=2, note="still_writing:1", new_files=1)
        self.run_row("U3", rule="R2", note="retry", new_files=1)  # 同一份简报里的补传：传上了一份
        self.file_row("U2", ("sent", "", "success"), rule="R2")
        self.file_row("U3", ("human", "", ""), ("stopped", "cowork.folder.rule_disabled", ""), rule="R2")
        self.assertEqual(self.brief("2026-10-14 08:30")[0].text.split("\n")[1:],
                         ["· Bills: Folder not available",
                          "· Cash: Entered 1 · To check 2 (changed by a person / task off or no access) · Not uploaded 2",
                          "  Results: D:\\Bills\\C\\_Pearnly Results\\2026-10"])

    def test_old_runs_are_dropped_without_a_brief(self):
        self.run_row("U1", started=datetime(2026, 10, 10, 8, 1), slot=datetime(2026, 10, 10, 8, 0))
        self.assertEqual(self.brief("2026-10-14 08:30"), [])
        self.assertEqual(self.db.runs["U1"].brief_state, "skipped")

    def test_the_owner_who_ran_it_must_still_have_access(self):
        self.rule("R2", title="Other", connection="C2", reader="C2", root_id="RB", folder="")
        self.db.rules["R1"].rule_owner = "new-owner@example.com"  # 任务之后换了负责人：简报仍发给当时处理的人
        self.run_row("U1")
        self.run_row("U2", rule="R2")
        self.file_row("U2", ("sent", "", "success"), rule="R2")
        self.denied = {(OWNER, "C1")}
        sent = self.brief("2026-10-14 08:30")
        self.assertEqual([(row.user, row.refs) for row in sent], [(OWNER, ("R2",))])
        self.assertEqual(sent[0].text.split("\n")[1:], ["· Other: Entered 1",
                                                        "  Results: \\\\server\\scans\\_Pearnly Results\\2026-10"])
        self.assertIn((OWNER, "C1"), self.checked)
        self.assertEqual((self.db.runs["U1"].brief_state, self.db.runs["U2"].brief_state), ("skipped", "sent"))
        self.run_row("U3", started=datetime(2026, 10, 14, 9, 0))
        self.assertEqual(len(self.brief("2026-10-14 09:30")), 1)  # 整组都没权限：不发
        self.assertEqual(self.db.runs["U3"].brief_state, "skipped")

    def test_a_key_already_sent_still_marks_the_runs(self):
        self.run_row("U1")
        key = self.contracts.brief_key(OWNER, ["U1"])
        self.notices.rows[key] = Row(text="earlier")
        self.assertEqual(len(self.brief("2026-10-14 08:30")), 1)
        self.assertEqual(self.notices.rows[key].text, "earlier")
        self.assertEqual((self.db.runs["U1"].brief_state, self.db.runs["U1"].brief_key), ("sent", key))

    def test_results_without_a_month_when_the_files_cross_months(self):
        self.run_row("U1", slot=datetime(2026, 10, 31, 23, 40), started=datetime(2026, 10, 31, 23, 41))
        self.file_row("U1", ("sent", "", "success"), registered=datetime(2026, 10, 31, 23, 50))
        self.file_row("U1", ("sent", "", "success"), registered=datetime(2026, 11, 1, 0, 10))
        self.assertTrue(self.brief("2026-11-01 08:00")[0].text.endswith("Results: D:\\Bills\\A\\_Pearnly Results"))

    def test_written_in_the_owner_language(self):
        self.langs[OWNER] = "zh"
        self.run_row("U1")
        self.file_row("U1", ("held", "choice_needed:party,department", ""))
        sent = self.brief("2026-10-14 08:30")[0]
        self.assertEqual(sent.title, "定时任务结果")
        self.assertEqual(sent.text.split("\n")[:2], ["2026-10-14 08:00 定时运行（1 个任务）：",
                                                     "· Bills：待处理 1（待选对方档案 / 待选部门）"])
        self.assertNotIn("Express", sent.text)
        self.assertEqual(self.local.lang, "en")

    def test_more_than_ten_tasks_in_one_brief(self):
        for index in range(12):
            self.rule(f"T{index:02}", title=f"Task {index:02}", folder=f"F{index:02}")
            self.run_row(f"U{index:02}", rule=f"T{index:02}")
        lines = self.brief("2026-10-14 08:30")[0].text.split("\n")
        self.assertEqual((lines[0], lines[10], lines[11]),
                         ("Scheduled run at 2026-10-14 08:00 (tasks: 12):", "· Task 09: No new files", "and 2 more"))

    def test_a_failing_brief_is_retried_next_round(self):
        self.run_row("U1")
        self.notices.fail = {OWNER}
        self.assertEqual(self.brief("2026-10-14 08:30"), [])
        self.assertEqual((self.errors, self.db.runs["U1"].brief_state), (["Cowork folder brief failed"], "pending"))
        self.notices.fail = set()
        self.assertEqual(len(self.brief("2026-10-14 08:35")), 1)

    def test_only_cowork_sites(self):
        self.run_row("U1")
        self.product = "dms"
        self.assertEqual(self.brief("2026-10-14 08:30"), [])
        self.assertEqual(self.db.runs["U1"].brief_state, "pending")


class ScheduleEntry(Harness):
    def test_reminders_and_briefs_run_side_by_side(self):
        self.rule("R1")
        self.db.runs["U1"] = Row(name="U1", rule="R1", run_owner=OWNER, slot_at=None, started_at=datetime(2026, 10, 13, 7, 0),
                                 scanned_at=datetime(2026, 10, 13, 7, 0), new_files=0, upload_failed=0, note="",
                                 brief_state="pending")
        self.schedule.notices("2026-10-13 08:05")
        self.assertEqual(sorted(row.kind for row in self.notices.sent()), ["cowork.folder.brief", "cowork.folder.reminder"])

    def test_one_failing_does_not_stop_the_other(self):
        self.db.runs["U1"] = Row(name="U1", rule="R1", run_owner=OWNER, slot_at=None, started_at=datetime(2026, 10, 13, 7, 0),
                                 scanned_at=datetime(2026, 10, 13, 7, 0), new_files=0, upload_failed=0, note="",
                                 brief_state="pending")
        self.rule("R1")
        self.reminders.run = mock.Mock(side_effect=RuntimeError("boom"))
        self.schedule.notices("2026-10-13 08:05")
        self.assertEqual(self.errors, ["Cowork folder notices failed"])
        self.assertEqual([row.kind for row in self.notices.sent()], ["cowork.folder.brief"])

    def test_jobs_declared_in_hooks_run_in_the_same_entry(self):
        """「待处理」早上汇总（ADR-020）经 hooks `cowork_scheduled_notices` 接在同一个入口上：同样拿到 now、出错只记日志。"""
        seen = []
        self.hooked["cowork_scheduled_notices"] = ["x.morning", "x.broken"]
        self.attrs.update({"x.morning": lambda now: seen.append(now),
                           "x.broken": mock.Mock(side_effect=RuntimeError("boom"))})
        self.schedule.notices("2026-10-15 08:30")
        self.assertEqual((seen, self.errors), (["2026-10-15 08:30"], ["Cowork folder notices failed"]))


class SameNumbersAsTheTaskList(unittest.TestCase):
    """运行记录与任务列表（`rules.results`）和简报同一个 `tally`：已删的票不算待处理，一天前没传完的不算处理中。"""

    def test_results_use_the_shared_tally(self):
        now = datetime(2026, 10, 14, 9, 0)
        outcome = {"H1": {"status": "sent"}, "H2": {"status": "held", "reason": "choice_needed:item"},
                   "H3": {"status": "sent"}, "H4": {"status": "sent"},
                   "_signature": [["H1", "success", ""], ["H2", "", ""], ["H3", "deleted", ""], ["H4", "manual", ""]]}
        rows = [Row(state="done", outcome_json=json.dumps(outcome), registered_at=now), Row(state="failed"),
                Row(state="submitted", registered_at=now), Row(state="registered", registered_at=now - timedelta(days=2))]
        queries, stamp, store = [], [(4, datetime(2026, 10, 14, 8, 30), 1)], {}

        def sql(query, values=None, as_dict=False):
            queries.append((" ".join(query.split()), values))
            return stamp if "COUNT(*)" in query else rows

        stub = types.SimpleNamespace
        cache = stub(get_value=lambda key: copy.deepcopy(store.get(key)),
                     set_value=lambda key, value, expires_in_sec=None: store.__setitem__(key, copy.deepcopy(value)))
        frappe = stub(db=stub(sql=sql), _=lambda key: key, throw=None, cache=cache)
        contracts = _load("folder_contracts_results", APP / "folder_intake/contracts.py")
        modules = {"frappe": frappe, "frappe.utils": stub(now_datetime=lambda: now),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub()),
                   "pearnly_cowork.connections": stub(access=stub(), preparation=stub(), service=stub(),
                                                      desktop_update=_load("connections_desktop_update_results",
                                                                           APP / "connections/desktop_update.py")),
                   "pearnly_cowork.folder_intake": stub(agent=stub(), contracts=contracts, destinations=stub(), inbox=stub()),
                   "pearnly_cowork.folder_intake.access": stub(FILE=FILE, RULE=RULE, RUN=RUN, read=None, write=None),
                   "pearnly_cowork.targets.identity": stub(normalize_express_account_key=lambda value: value)}
        with mock.patch.dict(sys.modules, modules):
            rules = _load("folder_rules_results", APP / "folder_intake/rules.py")
        tally = (f"SELECT state, last_code, outcome_json, registered_at FROM `tab{FILE}` WHERE rule=%s", ["R1"])
        fingerprint = (f"SELECT COUNT(*), MAX(modified), SUM(state = 'registered' AND registered_at < %s) "
                       f"FROM `tab{FILE}` WHERE rule=%s", (now - timedelta(hours=24), "R1"))
        self.assertEqual(rules.results("R1"), {"entered": 1, "attention": 3, "moving": 1})
        self.assertEqual(rules.results("R1", "U1"), {"entered": 1, "attention": 3, "moving": 1})
        self.assertEqual(queries, [fingerprint, tally,
                                   (f"SELECT state, last_code, outcome_json, registered_at FROM `tab{FILE}` WHERE rule=%s "
                                    "AND run=%s", ["R1", "U1"])])
        found = contracts.tally(rows, now)
        self.assertEqual((found["pushed"], found["stopped"] + found["failed"], found["moving"]), (1, 3, 1))

        # frappe-runtime-3 / business-logic-5：列表每 10 秒重拉，文件行没变就不再读全部推送结果逐行解析
        queries.clear()
        self.assertEqual(rules.results("R1"), {"entered": 1, "attention": 3, "moving": 1})
        self.assertEqual(queries, [fingerprint])
        # 文件行变了（后台推进存了一行：最晚修改时间变了），或有 registered 行满了 24 小时：重新统计
        for changed in ((4, datetime(2026, 10, 14, 8, 31), 1), (4, datetime(2026, 10, 14, 8, 31), 2), (5, None, 2)):
            stamp[0] = changed
            queries.clear()
            rules.results("R1")
            self.assertEqual(queries, [fingerprint, tally], changed)


if __name__ == "__main__":
    unittest.main()
