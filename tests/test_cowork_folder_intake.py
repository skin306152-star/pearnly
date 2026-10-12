"""文件夹自动录入：纯规则 + 「只新增、不改现有」的守护（不连库、不依赖 Frappe）。"""
import ast
import csv
import hashlib
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("folder_contracts", APP / "folder_intake/contracts.py")
identity = _load("folder_targets_identity", APP / "targets/identity.py")
folder_plan = _load("companion_folder_plan", ROOT / "companion/src/companion/folder_plan.py")


class Row(dict):
    __getattr__ = dict.get


class ContractTests(unittest.TestCase):
    def test_folder_is_relative_and_never_the_result_folder(self):
        self.assertEqual(contracts.folder(" 冰厂/进项\\2026-10 "), "冰厂\\进项\\2026-10")
        self.assertEqual(contracts.folder(""), "")
        for bad in ("..\\x", "a\\..\\b", "C:\\x", "a|b", "_Pearnly结果", "a\\_pearnly结果\\b", "a.\\b",
                    "_ผลลัพธ์ Pearnly", "a\\_pearnly results", "_Pearnly結果"):
            with self.assertRaises(contracts.ContractError, msg=bad):
                contracts.folder(bad)

    def test_nested_folders_overlap(self):
        self.assertTrue(contracts.nested("", "a"))
        self.assertTrue(contracts.nested("A\\b", "a"))
        self.assertTrue(contracts.nested("a", "a"))
        self.assertFalse(contracts.nested("a\\b", "a\\c"))
        self.assertFalse(contracts.nested("ab", "a"))

    def test_schedule(self):
        self.assertEqual(contracts.schedule("1", "22:00"), (1, "22:00"))
        self.assertEqual(contracts.schedule(0, "07:30"), (0, "07:30"))
        self.assertEqual(contracts.schedule(29, "22:00"), (29, "22:00"))  # 2026-10-09 起可选 29–31 日
        for day, at in ((32, "22:00"), (-1, "22:00"), (1, "24:00"), (1, "7:00"), (True, "22:00")):
            with self.assertRaises(contracts.ContractError):
                contracts.schedule(day, at)

    def test_defaults_keep_only_what_the_rule_meets(self):
        raw = {"payment": "credit", "vat": "claim", "account": "5300", "item": "__new__", "department": "01", "party": "X"}
        self.assertEqual(contracts.defaults(raw, direction="sales", posting_kind="service", departments={"01"}),
                         {"department": "01"})
        self.assertEqual(contracts.defaults(raw, direction="purchase", posting_kind="service", accounts={"5300"}),
                         {"payment": "credit", "vat": "claim", "account": "5300", "department": "01"})
        self.assertEqual(contracts.defaults(raw, direction="purchase", posting_kind="stock")["item"], "__new__")
        with self.assertRaises(contracts.ContractError):
            contracts.defaults({"payment": "later"}, direction="purchase", posting_kind="service")
        with self.assertRaises(contracts.ContractError):
            contracts.defaults({"account": "9999"}, direction="purchase", posting_kind="service", accounts={"5300"})
        self.assertEqual(contracts.default_value({"item": "ask"}, "item"), "")

    def test_entry_identity_ignores_who_uploads(self):
        digest = "a" * 64
        self.assertEqual(contracts.entry_key("R1", "X\\a.pdf", digest), contracts.entry_key("R1", "x\\A.PDF", digest))
        self.assertNotEqual(contracts.entry_key("R1", "x\\a.pdf", digest), contracts.entry_key("R1", "x\\b.pdf", digest))
        self.assertNotEqual(contracts.entry_key("R1", "x\\a.pdf", digest), contracts.entry_key("R2", "x\\a.pdf", digest))

    def test_paths_from_the_companion(self):
        self.assertEqual(contracts.relpath("a/b/c.pdf"), "a\\b\\c.pdf")
        for bad in ("../c.pdf", "_Pearnly结果\\2026-10\\x.pdf", "_ผลลัพธ์ Pearnly\\2026-10\\x.pdf", ""):
            with self.assertRaises(contracts.ContractError):
                contracts.relpath(bad)
        self.assertEqual(contracts.filename("x.HEIC"), "x.HEIC")
        for bad in ("x.xlsx", "x.docx", "a\\x.pdf"):
            with self.assertRaises(contracts.ContractError):
                contracts.filename(bad)

    def test_workbook_names(self):
        self.assertEqual(contracts.workbook_name("INV/001", "0105536082808", "a.pdf"), "INV-001_2808.xlsx")
        self.assertEqual(contracts.workbook_name("", "", "收据 照片.jpg", "无发票号_"), "无发票号_收据 照片.xlsx")
        self.assertEqual(contracts.workbook_name("", "", "a.pdf", "ไม่มีเลขที่_"), "ไม่มีเลขที่_a.xlsx")
        self.assertEqual(contracts.workbook_name("", "", "a.pdf", "x/y:"), "x-y-a.xlsx")
        self.assertEqual(contracts.workbook_name("A1", "", "x.pdf"), "A1.xlsx")


    def test_result_folder_follows_the_rule_owner_language_and_old_rules_keep_theirs(self):
        self.assertEqual(contracts.result_folder_for("th"), "_ผลลัพธ์ Pearnly")
        self.assertEqual(contracts.result_folder_for("zh"), "_Pearnly结果")
        self.assertEqual(contracts.result_folder_for("en"), "_Pearnly Results")
        self.assertEqual(contracts.result_folder_for("ja"), "_Pearnly結果")
        self.assertEqual(contracts.result_folder_for("zh-TW"), "_Pearnly结果")
        self.assertEqual(contracts.result_folder_for(None), "_ผลลัพธ์ Pearnly")
        self.assertEqual(contracts.result_folder(""), "_Pearnly结果")
        self.assertEqual(contracts.result_folder("_Pearnly Results"), "_Pearnly Results")
        self.assertEqual(contracts.result_folder("..\\x"), "_Pearnly结果")


class AdditiveGuards(unittest.TestCase):
    """现有三个机器方法、复核表、录入列表脚本加载顺序都不因本功能改变。"""

    def test_machine_tables(self):
        path = APP / "companion/http_auth.py"
        source = path.read_text()
        tree = ast.parse(source)
        values = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in ("METHODS", "FOLDER_METHODS"):
                values[node.targets[0].id] = {k.value: (v.value if isinstance(v, ast.Constant) else v.id)
                                              for k, v in zip(node.value.keys, node.value.values)}
        self.assertEqual(values["METHODS"], {"heartbeat": "METHOD", "lease": "pearnly_cowork.companion.queue_api.lease",
                                             "ack": "pearnly_cowork.companion.queue_api.acknowledge"})
        self.assertFalse(set(values["METHODS"]) & set(values["FOLDER_METHODS"]))
        self.assertTrue(all(v.startswith("pearnly_cowork.folder_intake.machine.") for v in values["FOLDER_METHODS"].values()))

    def test_companion_calls_exactly_the_server_folder_methods(self):
        tree = ast.parse((ROOT / "apps/pearnly_cowork/pearnly_cowork/companion/http_auth.py").read_text())
        server = set()
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "FOLDER_METHODS":
                server = {"/api/method/" + value.value for value in node.value.values}
        source = (ROOT / "companion/src/companion/folder_client.py").read_text()
        prefix = ast.literal_eval(source.split("_METHOD = ", 1)[1].splitlines()[0])
        client = {prefix + name for name in ("sync", "begin", "finish", "workbook_file")}
        self.assertEqual(client, server)
        for name in ("sync", "begin", "finish", "workbook_file"):
            self.assertIn(f'_METHOD + "{name}"', source)

    def test_machine_module_only_has_machine_methods(self):
        tree = ast.parse((APP / "folder_intake/machine.py").read_text())
        whitelisted = sorted(node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                             and any("whitelist" in ast.unparse(d) for d in node.decorator_list))
        self.assertEqual(whitelisted, ["begin", "finish", "sync", "workbook_file"])

    def test_hooks_only_append(self):
        source = (APP / "hooks.py").read_text()
        tree = ast.parse(source)
        scripts = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "app_include_js":
                scripts = [ast.unparse(item) for item in node.value.elts]
        self.assertEqual(scripts[:12], [f"versioned_asset('pearnly_cowork', 'desk/{name}.js')" for name in (
            "choice", "targets", "intake", "intake-choices", "intake-upload", "team", "post-file", "stocktake-entry",
            "stocktake", "recon", "line-binding", "work")])
        self.assertEqual(scripts[12:], ["versioned_asset('pearnly_cowork', 'desk/intake-folders.js')"])
        # 识别后的按类别钩子不碰（只能替换、不能追加的那几个 dict）。
        # 识别后的钩子只在 LINE 之后追加文件夹的推进（列表，追加不替换）；按类别的 dict 不碰。
        self.assertIn('cowork_ocr_recognized = ["pearnly_cowork.line.intake.recognized", '
                      '"pearnly_cowork.folder_intake.events.recognized"]', source)
        self.assertIn('pearnly_ocr_failure_handlers = {"cowork.invoice": "pearnly_cowork.line.intake.ocr_failed"}', source)
        self.assertIn('pearnly_cowork.folder_intake.schedule.tick', source)

    def test_reminders_and_briefs_run_every_five_minutes(self):
        tree = ast.parse((APP / "hooks.py").read_text())
        events = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events")
        five = events["cron"]["*/5 * * * *"]
        # 只在末尾追加，现有三项不动
        self.assertEqual(five, ["pearnly_cowork.push.mrerp.runner.sweep", "pearnly_cowork.work.reminders.run",
                                "pearnly_cowork.recon_center.jobs.resume_abandoned",
                                "pearnly_cowork.folder_intake.schedule.notices"])
        self.assertIn("pearnly_cowork.folder_intake.schedule.tick", events["cron"]["* * * * *"])
        self.assertNotIn("pearnly_cowork.folder_intake.schedule.notices", events["cron"]["* * * * *"])

    def test_hook_targets_exist(self):
        for dotted in ("pearnly_cowork.folder_intake.schedule.tick", "pearnly_cowork.folder_intake.events.recognized", "pearnly_cowork.folder_intake.access.deny",
                       "pearnly_cowork.folder_intake.access.deny_query", "pearnly_cowork.folder_intake.access.reject_share",
                       "pearnly_cowork.folder_intake.pump.run", "pearnly_cowork.folder_intake.schedule.notices",
                       "pearnly_cowork.folder_intake.reminders.run", "pearnly_cowork.folder_intake.briefs.run"):
            module, name = dotted.rsplit(".", 1)
            tree = ast.parse((ROOT / "apps/pearnly_cowork" / (module.replace(".", "/") + ".py")).read_text())
            self.assertIn(name, {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}, dotted)

    def test_roundtrip_untouched_by_workbook(self):
        tree = ast.parse((APP / "folder_intake/workbook.py").read_text())
        imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        self.assertIn("pearnly_cowork.roundtrip.export", imported)
        self.assertNotIn("pearnly_cowork.roundtrip.export_writer", imported)


class OfflineCompanionRetry(unittest.TestCase):
    """小助手没连上：不准备（或撤回刚好断线时的准备）、回 retry、重试换请求号；连着但没报上账套、或最后一次，照现有规则走。"""

    def _push(self, reported=True, online=lambda: True, status="prepared"):
        calls = {"cancel": [], "confirm": [], "keys": []}

        class Error(Exception):
            pass

        db = types.SimpleNamespace(
            get_value=lambda doctype, name, fields=None, as_dict=False: (
                types.SimpleNamespace(edit_count=0, last_push_status="", last_push_record="", record_version=3)
                if doctype == "Cowork OCR History" else ("express" if fields == "adapter" else 7)),
            commit=lambda: None, rollback=lambda: None)
        frappe = types.SimpleNamespace(db=db, ValidationError=Error, PermissionError=Error,
                                       AuthenticationError=Error, clear_messages=lambda: None)

        def submit(history, connection, key, *args, **kwargs):
            calls["keys"].append(key)
            calls.setdefault("fixed", []).append(kwargs.get("fixed"))
            return {"status": status, "name": "CWP-1", "revision": 1, "has_reported_credential": reported}

        application = types.SimpleNamespace(
            choices_view=lambda *args, **kwargs: {"needed": []}, submit=submit,
            cancel_prepared=lambda name, revision: calls["cancel"].append(name),
            submission_failed=lambda history, reason: None)
        express = types.SimpleNamespace(confirm=lambda *args: calls["confirm"].append(args))
        destinations = types.SimpleNamespace(of=lambda adapter: express, online=lambda adapter, connection: online(),
                                             ready=lambda adapter, prepared: bool(prepared.get("has_reported_credential")))
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(contracts=contracts, destinations=destinations),
                   "pearnly_cowork.history": types.SimpleNamespace(push_state=types.SimpleNamespace()),
                   "pearnly_cowork.push": types.SimpleNamespace(choices=types.SimpleNamespace()),
                   "pearnly_cowork.push.application": types.SimpleNamespace(service=application)}
        with mock.patch.dict(sys.modules, modules):
            push = _load("folder_push_under_test", APP / "folder_intake/push.py")
        rule = types.SimpleNamespace(connection="C", account_set_key="A", posting_kind="purchase", defaults_json="{}")
        return push, rule, types.SimpleNamespace(name="E"), calls

    def test_offline_companion_waits_without_preparing(self):
        push, rule, entry, calls = self._push(online=lambda: False)
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False), {"status": "retry", "reason": push.OFFLINE})
        self.assertEqual((calls["keys"], calls["cancel"], calls["confirm"]), ([], [], []))

    def test_drop_while_preparing_is_withdrawn_and_retried_with_a_new_key(self):
        answers = iter([True, False, True, False])
        push, rule, entry, calls = self._push(reported=False, online=lambda: next(answers))
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False)["status"], "retry")
        self.assertEqual(push.push_one(entry, rule, "H", 1, final=False)["status"], "retry")
        self.assertEqual((calls["cancel"], calls["confirm"]), (["CWP-1", "CWP-1"], []))
        self.assertEqual(calls["keys"], [push.push_key("E", "H", 3), push.push_key("E", "H", 3, 1)])
        self.assertNotEqual(calls["keys"][0], calls["keys"][1])

    def test_online_but_unreported_account_set_and_last_attempt_follow_the_existing_push(self):
        push, rule, entry, calls = self._push(reported=False, online=lambda: True)
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False)["status"], "sent")
        push, rule, entry, calls2 = self._push(reported=False, online=lambda: False)
        self.assertEqual(push.push_one(entry, rule, "H", 36, final=True)["status"], "sent")
        self.assertEqual((len(calls["confirm"]), len(calls2["confirm"]), calls["cancel"]), (1, 1, []))

    def test_rule_defaults_go_with_this_push_only(self):
        # 用户 2026-10-08：规则的「这一类都这样处理」当这张票的选择用，不写进账套记忆（这里也没有可写的入口）
        push, rule, entry, calls = self._push()
        rule.defaults_json = '{"payment": "cash", "item": "ask", "department": "HQ"}'
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=True)["status"], "sent")
        self.assertEqual(calls["fixed"], [{"payment": "cash", "department": "HQ"}])

    def test_replayed_withdrawn_preparation_is_not_counted_as_sent(self):
        push, rule, entry, calls = self._push(status="cancelled")
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False)["status"], "retry")
        self.assertEqual(push.push_one(entry, rule, "H", 36, final=True)["status"], "failed")
        self.assertEqual(calls["confirm"], [])


class RuleSafety(unittest.TestCase):
    """规则文件夹：跨电脑网络共享重叠、小助手报的文件夹在不在（2026-10-08）。"""

    def test_network_paths_overlap_but_local_drives_do_not(self):
        a = contracts.share_path("\\\\Server\\Scans", "A")
        self.assertEqual(a, "\\\\server\\scans\\a")
        self.assertTrue(contracts.path_nested(a, contracts.share_path("\\\\server\\scans", "")))
        self.assertFalse(contracts.path_nested(a, contracts.share_path("\\\\server\\scans", "B")))
        self.assertEqual(contracts.share_path("", "A"), "")  # 本机盘：不比对
        rows = contracts.roots([{"id": "R1", "label": "S:\\a", "share": "\\\\srv\\a"},
                                {"id": "R2", "label": "C:\\b", "share": "C:\\b"}])
        self.assertEqual([r["share"] for r in rows], ["\\\\srv\\a", ""])

    def test_rule_health_keeps_only_known_states(self):
        self.assertEqual(contracts.health({"R1": "ok", "R2": "folder_missing", "R3": "boom", 4: "ok"}),
                         {"R1": "ok", "R2": "folder_missing"})
        self.assertEqual(contracts.health(None), {})


class FolderCounts(unittest.TestCase):
    """F1（2026-10-12）：小助手报的文件夹计数与各任务待处理数；不知道就没有，不用 0 冒充。"""

    def test_roots_keep_counts_of_reported_folders_only(self):
        rows = contracts.roots([{"id": "R1", "label": "D:\\Bills", "folders": ["A", "A\\Sub", "B"],
                                 "counts": {"": 7, "a": 5, "A\\Sub": 2, "B": 0, "C": 3, "..\\x": 1, "A\\x": True,
                                            "Z": -1, 4: 1, "a\\sub": "2"},
                                 "partial": True},
                                {"id": "R2", "label": "S:\\Scans", "folders": ["X"], "partial": True},
                                {"id": "R3", "label": "E:\\Docs", "counts": {"": 1}, "partial": "yes"}])
        self.assertEqual(rows[0]["counts"], {"": 7, "A": 5, "A\\Sub": 2})
        self.assertIs(rows[0]["partial"], True)
        self.assertNotIn("counts", rows[1])  # 旧小助手没报计数：不知道
        self.assertNotIn("partial", rows[1])
        self.assertEqual((rows[2]["counts"], rows[2]["partial"]), ({"": 1}, False))

    def test_pending_keeps_well_formed_rule_counts(self):
        self.assertEqual(contracts.pending({"R1": 3, "R2": 0, "R3": -1, "R4": True, "R5": "2", 6: 1, "x" * 141: 1}),
                         {"R1": 3, "R2": 0})
        self.assertEqual(contracts.pending(None), {})
        self.assertEqual(contracts.pending([["R1", 1]]), {})

    def test_moving_skips_uploads_stuck_for_a_day(self):
        now = datetime(2026, 10, 12, 20, 0)
        self.assertTrue(contracts.moving("submitted", now - timedelta(days=5), now))
        self.assertTrue(contracts.moving("registered", now - timedelta(hours=23), now))
        self.assertFalse(contracts.moving("registered", now - timedelta(hours=25), now))
        self.assertFalse(contracts.moving("registered", None, now))
        for state in ("done", "failed", "not_invoice"):
            self.assertFalse(contracts.moving(state, now, now))


class SessionsAndNotices(unittest.TestCase):
    def test_session_ref_follows_the_assistant_session_shape(self):
        self.assertEqual(contracts.session_ref("sesn_01ABCdef"), "sesn_01ABCdef")
        for bad in ("", None, "abc", "-bad-start", "a b c d", "x" * 129, 12345):
            self.assertEqual(contracts.session_ref(bad), "")

    def test_notices_after_is_clamped_to_the_last_twelve_hours(self):
        now = 1_760_000_000.0
        window = now - contracts.NOTICE_WINDOW
        self.assertEqual(contracts.notices_after(now - 60, now), now - 60)
        for value in (None, "123", True, -5, 0, now - 13 * 3600, float("nan"), float("-inf")):
            self.assertEqual(contracts.notices_after(value, now), window, value)
        self.assertEqual(contracts.notices_after(now + 3600, now), now)
        self.assertEqual(contracts.NOTICE_FIELDS, ("id", "title", "text", "created", "session"))
        self.assertEqual((contracts.NOTICE_WINDOW, contracts.NOTICE_LIMIT), (12 * 3600, 20))

    def test_a_pc_only_gets_notices_about_its_own_tasks(self):
        """security-1：只要涉及一个不是本机读的任务，整条不给；说不清涉及谁（没有 refs）的也不给。"""
        items = [{"id": "a", "refs": ["T1"]}, {"id": "b", "refs": ["T1", "T2"]}, {"id": "c", "refs": ["T2"]},
                 {"id": "d", "refs": []}, {"id": "e"}, {"id": "f", "refs": "T1"}, "bad", {"id": "g", "refs": ["T1", "T3"]}]
        self.assertEqual([item["id"] for item in contracts.machine_notices(items, ["T1", "T3", ""])], ["a", "g"])
        self.assertEqual(contracts.machine_notices(items, []), [])


class ScheduleSlots(unittest.TestCase):
    """服务端算的定时时刻与小助手 `folder_plan` 逐位一致（运行记录按它归属完成简报）。"""

    def test_latest_slot_matches_the_companion(self):
        """2026-01 到 2029-03 每 7 小时一个点：上一次与小助手逐位一致，下一次严格在此刻之后、中间没有别的时刻。"""
        start = datetime(2026, 1, 1, tzinfo=contracts.BANGKOK).timestamp()
        end = datetime(2029, 3, 31, tzinfo=contracts.BANGKOK).timestamp()
        for now in range(int(start) + 1234, int(end), 7 * 3600):
            for day in (0, 1, 15, 28, 29, 30, 31):
                for at in ("00:00", "07:30", "22:00", "23:59"):
                    latest, upcoming = contracts.latest_slot(now, day, at), contracts.next_slot(now, day, at)
                    self.assertEqual(latest, folder_plan.latest_slot(now, day, at), (now, day, at))
                    self.assertTrue(latest <= now < upcoming, (now, day, at))
                    self.assertEqual(contracts.latest_slot(upcoming, day, at), upcoming)
                    self.assertEqual(contracts.latest_slot(upcoming - 1, day, at), latest)
                    self.assertEqual(folder_plan.latest_slot(upcoming, day, at), upcoming)

    def _at(self, *parts):
        return datetime(*parts, tzinfo=contracts.BANGKOK).timestamp()

    def _local(self, epoch):
        moment = datetime.fromtimestamp(epoch, contracts.BANGKOK)
        return moment.year, moment.month, moment.day, moment.hour, moment.minute

    def test_next_slot_across_months_years_and_short_months(self):
        cases = [((2026, 10, 31, 23, 0), 31, "22:00", (2026, 11, 30, 22, 0)),   # 31 日遇小月
                 ((2026, 12, 31, 22, 0), 31, "22:00", (2027, 1, 31, 22, 0)),    # 恰好在运行时刻：进到下一次（跨年）
                 ((2026, 12, 20, 9, 0), 1, "00:30", (2027, 1, 1, 0, 30)),
                 ((2026, 2, 1, 0, 0), 29, "08:00", (2026, 2, 28, 8, 0)),        # 2026 年 2 月 28 天
                 ((2028, 2, 1, 0, 0), 30, "08:00", (2028, 2, 29, 8, 0)),        # 2028 年 2 月 29 天
                 ((2028, 2, 29, 8, 0), 0, "08:00", (2028, 3, 31, 8, 0)),        # 月末：恰好在时刻上 → 下个月末
                 ((2028, 2, 10, 8, 0), 0, "08:00", (2028, 2, 29, 8, 0)),
                 ((2026, 4, 30, 21, 59), 31, "22:00", (2026, 4, 30, 22, 0))]
        for now, day, at, expected in cases:
            self.assertEqual(self._local(contracts.next_slot(self._at(*now), day, at)), expected, (now, day, at))
        exact = self._at(2026, 10, 14, 22, 0)
        self.assertEqual(contracts.latest_slot(exact, 14, "22:00"), exact)  # 这一次属于「上一次」
        self.assertEqual(self._local(contracts.next_slot(exact, 14, "22:00")), (2026, 11, 14, 22, 0))

    def test_reminder_windows_the_day_before(self):
        def slot(*now, since=0.0, day=14, at="22:00"):
            hit = contracts.reminder_slot(self._at(*now), day, at, since)
            return hit and (self._local(hit[0]), hit[1], self._local(hit[2]))

        run = (2026, 10, 14, 22, 0)
        self.assertIsNone(slot(2026, 10, 13, 7, 59))
        self.assertEqual(slot(2026, 10, 13, 8, 0), (run, "08", (2026, 10, 13, 8, 0)))
        self.assertEqual(slot(2026, 10, 13, 13, 59)[1], "08")
        self.assertEqual(slot(2026, 10, 13, 14, 0), (run, "14", (2026, 10, 13, 14, 0)))
        self.assertEqual(slot(2026, 10, 14, 21, 59)[1], "14")  # 运行前 1 分钟（停机后补发）仍是 14:00 那条
        self.assertIsNone(slot(2026, 10, 14, 22, 0))  # 运行之后：落到下个月，还没到窗口
        self.assertIsNone(slot(2026, 10, 15, 9, 0))
        # 打开提醒（换负责人、改时间）晚于 08:00：08:00 那条不补，14:00 那条照发
        since = self._at(2026, 10, 13, 9, 30)
        self.assertIsNone(slot(2026, 10, 13, 10, 0, since=since))
        self.assertEqual(slot(2026, 10, 13, 14, 5, since=since)[1], "14")
        self.assertIsNone(slot(2026, 10, 13, 15, 0, since=self._at(2026, 10, 13, 14, 30)))
        # 跨年：1 日 00:30 的任务，前一天是 12-31；14:00 那条跨过午夜还在
        self.assertEqual(slot(2026, 12, 31, 8, 0, day=1, at="00:30"), ((2027, 1, 1, 0, 30), "08", (2026, 12, 31, 8, 0)))
        self.assertEqual(slot(2027, 1, 1, 0, 10, day=1, at="00:30")[1], "14")
        # 月末任务在 2 月：前一天是 2-27（2026）
        self.assertEqual(slot(2026, 2, 27, 8, 0, day=0, at="22:00")[0], (2026, 2, 28, 22, 0))

    def test_words_time_left_and_keys(self):
        run = self._at(2026, 10, 14, 22, 0)
        self.assertEqual(contracts.day_word(self._at(2026, 10, 13, 8, 5), run), "tomorrow")
        self.assertEqual(contracts.day_word(self._at(2026, 10, 14, 0, 5), run), "today")
        self.assertEqual(contracts.time_left(self._at(2026, 10, 13, 8, 5), run), (1, 13, 55))
        self.assertEqual(contracts.time_left(self._at(2026, 10, 14, 21, 0, 30), run), (0, 0, 59))
        self.assertEqual(contracts.time_left(run + 60, run), (0, 0, 0))
        self.assertEqual((contracts.clock(run), contracts.stamp(run), contracts.month_of(run)),
                         ("22:00", "2026-10-14 22:00", "2026-10"))
        key = contracts.remind_key("owner@example.com", run, "08", "C1")
        self.assertTrue(key.startswith("cowork.folder.remind:") and len(key) <= 140)
        self.assertEqual(key, contracts.remind_key("owner@example.com", run + 0.4, "08", "C1"))
        self.assertNotEqual(key, contracts.remind_key("owner@example.com", run, "14", "C1"))
        self.assertNotEqual(key, contracts.remind_key("other@example.com", run, "08", "C1"))
        # 同一负责人同一时刻，另一台电脑读的任务另成一条（2026-10-13）
        self.assertNotEqual(key, contracts.remind_key("owner@example.com", run, "08", "C2"))
        self.assertEqual(contracts.brief_key("o", ["b", "a"]), contracts.brief_key("o", ["a", "b"]))
        self.assertNotEqual(contracts.brief_key("o", ["a"]), contracts.brief_key("o", ["a", "b"]))
        self.assertRegex(contracts.brief_key("o", ["a"]), r"^cowork\.folder\.brief:[0-9a-f]{48}$")

    def test_reminders_merge_per_owner_time_and_slot(self):
        rows = [Row(name=f"R{index}", title=title, owner=owner, run_at=run_at, label=label)
                for index, (title, owner, run_at, label) in enumerate([
                    ("Sales", "a", 200.0, "08"), ("Bills", "a", 200.0, "08"), ("Other", "b", 200.0, "08"),
                    ("Later", "a", 300.0, "08"), ("Bills 2", "a", 200.0, "14")])]
        groups = contracts.reminder_groups(rows)
        self.assertEqual([(g["owner"], g["run_at"], g["label"], [r["title"] for r in g["rules"]]) for g in groups],
                         [("a", 200.0, "08", ["Bills", "Sales"]), ("b", 200.0, "08", ["Other"]),
                          ("a", 200.0, "14", ["Bills 2"]), ("a", 300.0, "08", ["Later"])])

    def test_reminders_never_mix_tasks_read_by_different_pcs(self):
        """security-1：提醒会被读文件夹的那台电脑取回弹出，同一负责人同一时刻、不同电脑读的任务各成一条。"""
        rows = [Row(name="T1", title="Client X", owner="a", run_at=200.0, label="08", reader="M1"),
                Row(name="T2", title="Client Y", owner="a", run_at=200.0, label="08", reader="M2"),
                Row(name="T3", title="Client X 2", owner="a", run_at=200.0, label="08", reader="M1")]
        groups = contracts.reminder_groups(rows)
        self.assertEqual([(g["reader"], [r["name"] for r in g["rules"]]) for g in groups],
                         [("M1", ["T1", "T3"]), ("M2", ["T2"])])

    def test_full_path_on_the_pc(self):
        self.assertEqual(contracts.full_path("D:\\Bills\\", "冰厂\\进项", "_Pearnly结果", "2026-10"),
                         "D:\\Bills\\冰厂\\进项\\_Pearnly结果\\2026-10")
        self.assertEqual(contracts.full_path("\\\\server\\share", "", "_Pearnly Results", ""), "\\\\server\\share\\_Pearnly Results")
        self.assertEqual(contracts.full_path("", "A\\B"), "A\\B")
        self.assertEqual(contracts.full_path("", ""), "")

    def test_month_end_and_short_months(self):
        epoch = contracts.slot_at(2026, 2, 31, "22:00")
        self.assertEqual(datetime.fromtimestamp(epoch, contracts.BANGKOK).day, 28)
        self.assertEqual(datetime.fromtimestamp(contracts.slot_at(2028, 2, 0, "08:00"), contracts.BANGKOK).day, 29)
        january = datetime(2027, 1, 1, 0, 10, tzinfo=contracts.BANGKOK).timestamp()
        previous = datetime.fromtimestamp(contracts.latest_slot(january, 31, "22:00"), contracts.BANGKOK)
        self.assertEqual((previous.year, previous.month, previous.day), (2026, 12, 31))

    def test_reported_slot_is_trusted_only_near_now(self):
        now = 1_760_000_000.0
        self.assertEqual(contracts.run_slot(now - 86400, now), now - 86400)
        self.assertEqual(contracts.run_slot(int(now), now), now)
        for bad in (None, "1760000000", True, now - 41 * 86400, now + 41 * 86400, float("nan")):
            self.assertIsNone(contracts.run_slot(bad, now), bad)


class ResultTally(unittest.TestCase):
    """任务列表、运行记录、完成简报同一个结果口径（`contracts.tally`），简报何时发（`brief_groups`）。"""
    NOW = datetime(2026, 10, 14, 9, 0)

    @staticmethod
    def _done(*tickets):
        """tickets: (去向, 去向原因, 票上状态)。"""
        outcome = {f"H{index}": {"status": kind, "reason": reason} for index, (kind, reason, _status) in enumerate(tickets)}
        outcome["_signature"] = [[f"H{index}", status, ""] for index, (_kind, _reason, status) in enumerate(tickets)]
        return Row(state="done", outcome_json=json.dumps(outcome), registered_at=datetime(2026, 10, 14, 8, 0))

    def test_ticket_states(self):
        cases = [(({"status": "sent"}, "success"), ("pushed", "")),
                 (({"status": "held", "reason": "choice_needed:account,department"}, "success"), ("pushed", "")),
                 (({"status": "sent"}, "deleted"), ("deleted", "")),
                 (({"status": "sent"}, "pending"), ("moving", "")),
                 (({"status": "sent"}, "manual"), ("failed", "")),  # 转人工就是失败（录入列表同一口径）
                 (({"status": "sent"}, ""), ("failed", "")),
                 (({"status": "held", "reason": "choice_needed:account,department"}, ""), ("stopped", "choice:account,department")),
                 (({"status": "human"}, ""), ("stopped", "human")),
                 (({"status": "stopped", "reason": "cowork.folder.rule_disabled"}, ""), ("stopped", "task_off")),
                 (({"status": "failed", "reason": "x"}, ""), ("failed", "")),
                 (({"status": "missing"}, ""), ("failed", "")),
                 ((None, ""), ("failed", ""))]
        for args, expected in cases:
            self.assertEqual(contracts.ticket_state(*args), expected, args)

    def test_tally_counts_tickets_and_files(self):
        rows = [self._done(("sent", "", "success"), ("held", "choice_needed:party,account", ""), ("sent", "", "manual")),
                self._done(("human", "", ""), ("stopped", "x", ""), ("sent", "", "deleted"), ("sent", "", "pending")),
                Row(state="failed"), Row(state="not_invoice"),
                Row(state="submitted", last_code="insufficient_balance", registered_at=self.NOW),
                Row(state="recognized", last_code=contracts.WAITING_PURCHASES, registered_at=self.NOW),
                Row(state="pushing", last_code="cowork.folder.companion_offline", registered_at=self.NOW),
                Row(state="registered", registered_at=self.NOW - timedelta(hours=2)),
                Row(state="registered", registered_at=self.NOW - timedelta(hours=30)),  # 一天前没传完的残留：不算处理中
                Row(state="uploaded", registered_at=self.NOW)]
        found = contracts.tally(rows, self.NOW)
        self.assertEqual({key: found[key] for key in ("files", "pushed", "stopped", "failed", "not_invoice", "moving")},
                         {"files": 10, "pushed": 1, "stopped": 3, "failed": 2, "not_invoice": 1, "moving": 6})
        self.assertEqual(found["stop_reasons"], {"choice": 1, "human": 1, "task_off": 1})
        self.assertEqual(found["choice_kinds"], ["party", "account"])
        self.assertEqual(found["moving_reasons"], {"low_balance": 1, "waiting_purchases": 1, "offline": 1, "uploading": 1})
        empty = contracts.tally([], self.NOW)
        self.assertEqual((empty["files"], empty["moving"], empty["choice_kinds"]), (0, 0, []))
        broken = contracts.tally([Row(state="done", outcome_json="not json"), Row(state="done", outcome_json=None)], self.NOW)
        self.assertEqual((broken["files"], broken["pushed"], broken["failed"]), (2, 0, 0))

    @staticmethod
    def _constant(path, name):
        tree = ast.parse((APP / path).read_text())
        return next(ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == name)

    def test_codes_match_the_pump_and_push(self):
        self.assertEqual(self._constant("folder_intake/push.py", "OFFLINE"), contracts.COMPANION_OFFLINE)
        self.assertEqual(self._constant("folder_intake/pump.py", "IN_FLIGHT"), contracts.IN_FLIGHT)
        self.assertIn('_later(entry, 600 if code == "insufficient_balance" else 120, code)',
                      (APP / "folder_intake/pump.py").read_text())
        # 要人选一次的几类，与推送那边同一组
        self.assertEqual(self._constant("push/express/choices.py", "KINDS"), contracts.CHOICE_KINDS)

    def test_not_uploaded_counts_failures_and_files_still_being_written(self):
        runs = [Row(upload_failed=2, note="still_writing:3"), Row(upload_failed=1, note="folder_missing"),
                Row(note="retry", new_files=4, upload_failed=1), Row(note="still_writing:x")]
        self.assertEqual(contracts.not_uploaded(runs), 2)
        self.assertEqual(contracts.not_uploaded([Row(note="retry", new_files=5)]), 0)

    def _run(self, name, owner="o", slot=datetime(2026, 10, 14, 8, 0), started=datetime(2026, 10, 14, 8, 1),
             scanned=True, **extra):
        return Row(name=name, run_owner=owner, slot_at=slot, started_at=started, scanned_at=started if scanned else None,
                   **extra)

    def test_a_brief_waits_for_every_file_and_three_quiet_minutes(self):
        runs = [self._run("U1"), self._run("U2", started=datetime(2026, 10, 14, 8, 5))]
        files = {"U1": [Row(state="done")], "U2": [Row(state="submitted", registered_at=datetime(2026, 10, 14, 8, 5))]}
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 14, 9, 0)), ([], []))
        files["U2"][0]["state"] = "not_invoice"
        self.assertEqual(contracts.BRIEF_SETTLE, timedelta(minutes=3))
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 14, 8, 7)), ([], []))
        ready, stale = contracts.brief_groups(runs, files, datetime(2026, 10, 14, 8, 8))
        self.assertEqual((ready, stale), ([{"owner": "o", "slot_at": datetime(2026, 10, 14, 8, 0), "reader": "",
                                            "runs": ["U1", "U2"], "complete": True}], []))
        runs[0]["scanned_at"] = None  # 还没扫描完的运行
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 14, 8, 15)), ([], []))

    def test_quiet_minutes_count_from_the_last_file_that_finished(self):
        """business-logic-4：「全部到终态后再等几分钟」——最后一份文件 08:40 才走完，08:43 才发。"""
        runs = [self._run("U1")]
        files = {"U1": [Row(state="done", finished_at=datetime(2026, 10, 14, 8, 20)),
                        Row(state="not_invoice", finished_at=datetime(2026, 10, 14, 8, 40))]}
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 14, 8, 42)), ([], []))
        ready, _stale = contracts.brief_groups(runs, files, datetime(2026, 10, 14, 8, 43))
        self.assertEqual([(group["runs"], group["complete"]) for group in ready], [(["U1"], True)])

    def test_a_stuck_brief_goes_after_thirteen_hours_and_old_ones_are_dropped(self):
        self.assertEqual(contracts.BRIEF_MAX_WAIT, contracts.ORDER_MAX_WAIT + timedelta(hours=1))
        runs = [self._run("U1")]
        files = {"U1": [Row(state="submitted", registered_at=datetime(2026, 10, 14, 8, 1))]}
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 14, 21, 0)), ([], []))
        ready, _stale = contracts.brief_groups(runs, files, datetime(2026, 10, 14, 21, 1))
        self.assertEqual([(group["runs"], group["complete"]) for group in ready], [(["U1"], False)])
        self.assertEqual(contracts.brief_groups(runs, files, datetime(2026, 10, 17, 8, 2)), ([], ["U1"]))
        self.assertEqual(contracts.brief_groups([self._run("U9", owner="")], {}, datetime(2026, 10, 14, 9, 0)), ([], ["U9"]))
        self.assertEqual(contracts.brief_groups([self._run("U8", started=None)], {}, datetime(2026, 10, 14, 9, 0)), ([], ["U8"]))

    def test_one_brief_per_owner_and_slot_and_each_batch_of_run_now_on_its_own(self):
        """interfaces-1 / frappe-runtime-1：立即运行不再按负责人整个并成一份 —— 相隔一小时的 U3 / U4 各自一份；
        同一批（分组记成第一次运行的名字）的 U6 / U7 仍是一份。"""
        runs = [self._run("U1"), self._run("U2", owner="p"), self._run("U3", slot=None),
                self._run("U4", slot=None, started=datetime(2026, 10, 14, 7, 0)), self._run("U5", slot=datetime(2026, 9, 14, 8, 0)),
                self._run("U6", slot=None, started=datetime(2026, 10, 14, 7, 30), brief_group="U6"),
                self._run("U7", slot=None, started=datetime(2026, 10, 14, 7, 35), brief_group="U6")]
        ready, stale = contracts.brief_groups(runs, {}, datetime(2026, 10, 14, 9, 0))
        self.assertEqual([(group["owner"], group["slot_at"], group["runs"]) for group in ready],
                         [("o", None, ["U4"]), ("o", None, ["U6", "U7"]), ("o", datetime(2026, 10, 14, 8, 0), ["U1"]),
                          ("p", datetime(2026, 10, 14, 8, 0), ["U2"]), ("o", None, ["U3"]),
                          ("o", datetime(2026, 9, 14, 8, 0), ["U5"])])
        self.assertEqual(stale, [])

    def test_a_stuck_run_now_does_not_hold_back_a_later_one(self):
        """interfaces-1：09:00 立即运行 A 有一份卡在 pushing（小助手离线 / 等进货），15:00 立即运行 B 几分钟走完 ——
        B 15:10 照样单独发，完整；A 仍等到 13 小时才带「处理中」发。"""
        runs = [self._run("A", slot=None, started=datetime(2026, 10, 14, 9, 0), brief_group="A"),
                self._run("B", slot=None, started=datetime(2026, 10, 14, 15, 0), brief_group="B")]
        files = {"A": [Row(state="pushing", registered_at=datetime(2026, 10, 14, 9, 1))],
                 "B": [Row(state="done", finished_at=datetime(2026, 10, 14, 15, 0))]}
        ready, _stale = contracts.brief_groups(runs, files, datetime(2026, 10, 14, 15, 10))
        self.assertEqual([(group["runs"], group["complete"]) for group in ready], [(["B"], True)])
        ready, _stale = contracts.brief_groups(runs[:1], files, datetime(2026, 10, 14, 22, 0))
        self.assertEqual([(group["runs"], group["complete"]) for group in ready], [(["A"], False)])

    def test_scheduled_runs_of_one_slot_split_by_the_pc_that_read_them(self):
        """security-1：同一负责人同一时刻，两台电脑读的任务各一份简报（简报会被那台电脑取回弹出）。"""
        runs = [self._run("U1", connection="M1"), self._run("U2", connection="M2"), self._run("U3", connection="M1")]
        ready, _stale = contracts.brief_groups(runs, {}, datetime(2026, 10, 14, 9, 0))
        self.assertEqual([(group["reader"], group["runs"]) for group in ready], [("M1", ["U1", "U3"]), ("M2", ["U2"])])

    def test_brief_group_keys(self):
        slot = datetime(2026, 10, 14, 8, 0, 0, 123)
        self.assertEqual(contracts.slot_group(slot), "2026-10-14T08:00:00")
        self.assertEqual(contracts.slot_group("2026-10-14 08:00:00.000000"), "2026-10-14T08:00:00")
        self.assertEqual(contracts.group_of(Row(name="U1", brief_group="G", slot_at=slot)), "G")
        # 加字段前记下的运行：有所属时刻按时刻，立即运行自成一份
        self.assertEqual(contracts.group_of(Row(name="U1", slot_at=slot)), "2026-10-14T08:00:00")
        self.assertEqual(contracts.group_of(Row(name="U1", slot_at=None, brief_group="")), "U1")


class StockOrderContracts(unittest.TestCase):
    """F7：同一连接同一账套里，存货销售等存货进货先写；服务类、MR.ERP 不参与。"""
    NOW = datetime(2026, 10, 12, 20, 0)
    SALE = datetime(2026, 10, 12, 19, 5)

    def test_stock_role(self):
        self.assertEqual(contracts.stock_role("purchase", "stock"), "purchase")
        self.assertEqual(contracts.stock_role("sales", "stock"), "sale")
        for direction, kind in (("purchase", "service"), ("sales", "service"), ("purchase", ""), ("sales", None)):
            self.assertEqual(contracts.stock_role(direction, kind), "")

    def test_purchases_go_first_and_keep_their_order(self):
        rows = [Row(name="S1", direction="sales"), Row(name="P1", direction="purchase"),
                Row(name="S2", direction="sales"), Row(name="P2", direction="purchase")]
        self.assertEqual([row.name for row in contracts.purchases_first(rows)], ["P1", "P2", "S1", "S2"])

    def _waits(self, files=(), runs=(), sale=None):
        return contracts.waits_for_purchases(sale or self.SALE, list(files), list(runs), self.NOW)

    def test_purchases_still_on_their_way_hold_the_sale(self):
        for state in ("uploaded", "source_ready", "submitted", "recognized", "pushing"):
            self.assertTrue(self._waits([{"state": state, "registered_at": self.SALE - timedelta(minutes=5)}]), state)
        # 立即运行的竞态：进货比销售晚登记、仍在路上
        self.assertTrue(self._waits([{"state": "submitted", "registered_at": self.SALE + timedelta(minutes=10)}]))
        self.assertTrue(self._waits([{"state": "registered", "registered_at": self.NOW - timedelta(minutes=30)}]))

    def test_settled_old_or_stuck_purchases_do_not_hold_the_sale(self):
        for state in ("done", "failed", "not_invoice"):
            self.assertFalse(self._waits([{"state": state, "registered_at": self.SALE}]), state)
        self.assertFalse(self._waits([{"state": "submitted", "registered_at": self.SALE - timedelta(hours=13)}]))
        self.assertFalse(self._waits([{"state": "registered", "registered_at": self.NOW - timedelta(hours=3)}]))
        self.assertFalse(self._waits([{"state": "submitted", "registered_at": self.SALE}],
                                     sale=self.NOW - timedelta(hours=13)))
        self.assertFalse(self._waits())
        self.assertFalse(contracts.waits_for_purchases(None, [{"state": "submitted", "registered_at": self.SALE}], [],
                                                       self.NOW))

    def test_a_purchase_run_requested_but_not_reported_holds_the_sale(self):
        asked = self.NOW - timedelta(minutes=10)
        self.assertTrue(self._waits(runs=[{"run_request_at": asked, "last_reported_at": None}]))
        self.assertTrue(contracts.purchase_run_pending(asked, asked - timedelta(days=1), self.NOW))
        self.assertFalse(contracts.purchase_run_pending(asked, asked + timedelta(minutes=1), self.NOW))
        self.assertFalse(contracts.purchase_run_pending(self.NOW - timedelta(minutes=70), None, self.NOW))
        self.assertFalse(contracts.purchase_run_pending(None, None, self.NOW))


class StockOrderPump(unittest.TestCase):
    """F7 在后台推进里：存货销售先不准备、一分钟后再看；有了结论才推（`pump._push_step`）。"""
    NOW = datetime(2026, 10, 12, 20, 0)
    KEY = "S:\\2569\\EXP69\\69SINCER"

    def _pump(self, rule, purchases=(), runs=(), last_code=""):
        calls = {"later": [], "push": [], "write": [], "sql": []}

        def sql(query, values=None, as_dict=False):
            calls["sql"].append(query)
            if "f.registered_at" in query:
                self.assertIn("r.direction = 'purchase' AND r.posting_kind = 'stock'", query)
                self.assertEqual(values, (contracts.ORDER_WAITING,))
                return [Row(row) for row in purchases]
            if "run_request_at" in query:
                return [Row(row) for row in runs]
            return []

        rules = {"RS": Row(rule) if rule else None}
        db = types.SimpleNamespace(sql=sql, commit=lambda: None, rollback=lambda: None,
                                   get_value=lambda doctype, name, fields=None, as_dict=False: rules.get(name))
        error = type("Error", (Exception,), {})
        frappe = types.SimpleNamespace(db=db, QueryDeadlockError=error, DoesNotExistError=error,
                                       clear_messages=lambda: None, log_error=lambda **kw: None,
                                       get_traceback=lambda: "")
        stub = types.SimpleNamespace
        modules = {"frappe": frappe, "frappe.utils": stub(add_to_date=None, now_datetime=lambda: self.NOW),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": stub(contracts=contracts, destinations=stub(), inbox=stub(),
                                                        settings=stub()),
                   "pearnly_cowork.folder_intake.access": stub(FILE="F", RULE="R", RUN="U", read=None, write=None),
                   "pearnly_cowork.folder_intake.machine": stub(owner_ready=None, request_key=None),
                   "pearnly_cowork.folder_intake.push": stub(push_one=None),
                   "pearnly_cowork.targets.identity": identity}
        with mock.patch.dict(sys.modules, modules):
            pump = _load("folder_pump_under_test", APP / "folder_intake/pump.py")
        entry = Row(name="S1", rule="RS", connection="C1", registered_at=self.NOW - timedelta(minutes=55),
                    last_code=last_code)
        pump._due = lambda state, limit=50: ["S1"]
        pump.read = lambda doctype, name, for_update=False: entry
        pump._pending_pushes = lambda connection: 0
        pump._later = lambda item, seconds, code: calls["later"].append((item.name, seconds, code))
        pump._push = lambda item, cfg, deadline: calls["push"].append(item.name)
        pump.write = lambda item: calls["write"].append((item.name, item.last_code))
        return pump, entry, calls

    def _sale(self, **extra):
        return {"enabled": 1, "connection": "C1", "account_set_key": self.KEY, "direction": "sales",
                "posting_kind": "stock", **extra}

    def _purchase(self, state="submitted", **extra):
        return {"state": state, "registered_at": self.NOW - timedelta(minutes=58), "connection": "C1",
                "account_set_key": self.KEY, **extra}

    def _step(self, pump):
        pump._push_step({"push_pending": 5}, float("inf"))

    def test_a_stock_sale_waits_while_a_purchase_is_on_its_way(self):
        pump, entry, calls = self._pump(self._sale(), purchases=[self._purchase()])
        self._step(pump)
        self.assertEqual(calls["later"], [("S1", pump.ORDER_RECHECK_SECONDS, contracts.WAITING_PURCHASES)])
        self.assertEqual(calls["push"], [])

    def test_the_sale_goes_once_purchases_are_settled_and_the_wait_note_is_cleared(self):
        pump, entry, calls = self._pump(self._sale(), last_code=contracts.WAITING_PURCHASES)
        self._step(pump)
        self.assertEqual((calls["later"], calls["push"]), ([], ["S1"]))
        self.assertEqual(calls["write"], [("S1", "")])

    def test_service_mrerp_and_turned_off_tasks_do_not_wait(self):
        for rule in (self._sale(posting_kind="service"), self._sale(posting_kind=""), self._sale(enabled=0), None):
            pump, entry, calls = self._pump(rule, purchases=[self._purchase()])
            self._step(pump)
            self.assertEqual((calls["later"], calls["push"], calls["sql"]), ([], ["S1"], []), rule)

    def test_purchases_on_another_account_set_or_connection_do_not_hold_the_sale(self):
        for other in (self._purchase(account_set_key="S:\\2569\\EXP69\\OTHER"), self._purchase(connection="C2")):
            pump, entry, calls = self._pump(self._sale(), purchases=[other])
            self._step(pump)
            self.assertEqual((calls["later"], calls["push"]), ([], ["S1"]))

    def test_the_account_set_key_is_compared_normalized(self):
        pump, entry, calls = self._pump(self._sale(), purchases=[self._purchase(account_set_key="s:/2569/exp69/69sincer/")])
        self._step(pump)
        self.assertEqual(len(calls["later"]), 1)

    def test_a_purchase_run_just_requested_holds_the_sale(self):
        run = {"connection": "C1", "account_set_key": self.KEY, "run_request_at": self.NOW - timedelta(minutes=5),
               "last_reported_at": None}
        pump, entry, calls = self._pump(self._sale(), runs=[run])
        self._step(pump)
        self.assertEqual(len(calls["later"]), 1)


class LiveAndNotify(unittest.TestCase):
    def _live(self, owner="owner@example.com", fail=False):
        published = []

        def publish(event, data, user=None, after_commit=False):
            if fail:
                raise RuntimeError("socket down")
            published.append((event, data, user, after_commit))

        frappe = types.SimpleNamespace(db=types.SimpleNamespace(get_value=lambda doctype, name, field: owner),
                                       publish_realtime=publish)
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(),
                   "pearnly_cowork.folder_intake.access": types.SimpleNamespace(RULE="Cowork Folder Intake Rule")}
        with mock.patch.dict(sys.modules, modules):
            live = _load("folder_live_under_test", APP / "folder_intake/live.py")
        return live, published

    def test_changes_reach_the_owner_and_the_file_actor_after_commit(self):
        live, published = self._live()
        live.changed("R1", "actor@example.com", "owner@example.com", None, "Guest")
        self.assertEqual(sorted(published), [("cowork_folder_changed", {"rule": "R1"}, "actor@example.com", True),
                                             ("cowork_folder_changed", {"rule": "R1"}, "owner@example.com", True)])
        live.changed("")
        self.assertEqual(len(published), 2)

    def test_a_push_that_cannot_be_sent_never_breaks_the_caller(self):
        live, _published = self._live(fail=True)
        live.changed("R1", "actor@example.com")

    def _notify(self, notices, local=None):
        local = local or types.SimpleNamespace(lang="en")
        modules = {"frappe": types.SimpleNamespace(local=local),
                   "frappe.translate": types.SimpleNamespace(get_user_lang=lambda user: {"th@example.com": "th"}.get(user, "zh")),
                   "frappe.utils": types.SimpleNamespace(get_datetime=lambda value: value,
                                                         get_system_timezone=lambda: "Asia/Bangkok"),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(contracts=contracts),
                   "pearnly_cowork.folder_intake.access": types.SimpleNamespace(RULE="Cowork Folder Intake Rule"),
                   "pearnly_core": types.ModuleType("pearnly_core"),
                   "pearnly_core.assistant": types.SimpleNamespace(notices=notices)}
        patcher = mock.patch.dict(sys.modules, modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        return _load("folder_notify_under_test", APP / "folder_intake/notify.py")

    def test_notify_reads_only_this_pcs_tasks_from_the_shared_notices(self):
        seen = []
        notify = self._notify(types.SimpleNamespace(
            for_references=lambda doctype, names, since, limit: seen.append((doctype, names, since, limit)) or [{"id": "x"}]))
        self.assertEqual(notify.for_rules([], 1.0), [])
        self.assertEqual(notify.for_rules(["R1", "", "R2"], 5.0, 20), [{"id": "x"}])
        self.assertEqual(seen, [("Cowork Folder Intake Rule", ["R1", "R2"], 5.0, 20)])

    def test_notify_sends_through_the_shared_notices_with_the_tasks_as_references(self):
        posted, keys = [], {"cowork.folder.remind:old"}

        def post(user, kind, text, dedup_key, **kwargs):
            posted.append((user, kind, text, dedup_key, kwargs))
            return {"name": "n1", "id": "pnotice_n1", "session_id": "", "created": True}

        notify = self._notify(types.SimpleNamespace(post=post, exists=lambda key: key in keys))
        result = notify.send("owner@example.com", notify.KIND_BRIEF, "Title", "Text", "cowork.folder.brief:x",
                             rules=["R1", "R2"], conversation="sesn_cc_1", ttl=notify.BRIEF_TTL)
        self.assertTrue(result["created"])
        self.assertEqual(posted, [("owner@example.com", "cowork.folder.brief", "Text", "cowork.folder.brief:x",
                                   {"title": "Title", "conversation": "sesn_cc_1",
                                    "reference_doctype": "Cowork Folder Intake Rule", "reference_names": ("R1", "R2"),
                                    "ttl": 24 * 3600})])
        self.assertEqual((notify.KIND_REMIND, notify.REMIND_TTL), ("cowork.folder.reminder", 6 * 3600))
        self.assertTrue(notify.sent("cowork.folder.remind:old"))
        self.assertFalse(notify.sent("cowork.folder.remind:new"))

    def test_notify_writes_in_the_owner_language_and_picks_the_latest_conversation(self):
        job = types.SimpleNamespace(lang_resolved=False)
        local = types.SimpleNamespace(lang="en", job=job)
        asked = []

        def owned_conversation(user, candidates):
            # 共用层判对话归属的替身：sesn_not_mine_1 是别人的对话
            asked.append((user, list(candidates)))
            return next((sid for sid in candidates if sid != "sesn_not_mine_1"), "")

        notify = self._notify(types.SimpleNamespace(owned_conversation=owned_conversation), local)
        with notify.in_lang("th@example.com"):
            self.assertEqual((local.lang, job.lang_resolved), ("th", True))
        self.assertEqual(local.lang, "en")
        with self.assertRaises(RuntimeError):
            with notify.in_lang("zh@example.com"):
                raise RuntimeError("boom")
        self.assertEqual(local.lang, "en")
        rules = [Row(cc_session="sesn_old_1", modified=datetime(2026, 10, 1)),
                 Row(cc_session="", modified=datetime(2026, 10, 9)),
                 Row(cc_session="sesn_new_1", modified=datetime(2026, 10, 5)),
                 Row(cc_session="bad id", modified=datetime(2026, 10, 8))]
        self.assertEqual(notify.conversation("owner@example.com", rules), "sesn_new_1")
        self.assertEqual(asked[-1], ("owner@example.com", ["sesn_new_1", "sesn_old_1"]))
        # security-3：最近改过的那条挂着别人的对话（旧数据：同事替负责人开关过提醒）：退回负责人自己的那段，不是不挂
        rules.append(Row(cc_session="sesn_not_mine_1", modified=datetime(2026, 10, 10)))
        self.assertEqual(notify.conversation("owner@example.com", rules), "sesn_new_1")
        self.assertEqual(asked[-1], ("owner@example.com", ["sesn_not_mine_1", "sesn_new_1", "sesn_old_1"]))
        calls = len(asked)
        self.assertEqual(notify.conversation("owner@example.com", rules[1:2]), "")
        self.assertEqual(len(asked), calls)  # 没有候选：不问共用层
        self.assertEqual(notify.epoch(datetime(2026, 10, 14, 22, 0)),
                         datetime(2026, 10, 14, 22, 0, tzinfo=contracts.BANGKOK).timestamp())


class MachineSync(unittest.TestCase):
    """同步：下发 existing、进货任务在前、收待处理数、新版提示与提醒；运行记录归属简报（`_record_run`）。"""
    NOW = 1_760_000_000.0  # 2025-10-09 15:53 曼谷

    def _machine(self, rules, *, latest=None, notices=None, before=None, pending_run=None, manual_run=None):
        """``pending_run``：这条任务还没发简报的上一次非补传运行（补传并进它）；``manual_run``：同一负责人 10 分钟内
        开始、还没发简报的上一次立即运行（并进它那一批）。"""
        calls = {"remember": [], "changed": [], "for_rules": [], "written": [], "sql": []}
        tables = {"Cowork Folder Intake Rule": {row["name"]: Row(row) for row in rules}}

        class Doc(Row):
            def is_new(self):
                return True

            def update(self, values):
                dict.update(self, values)

            def __setattr__(self, key, value):
                self[key] = value

        def sql(query, values=None, as_dict=False):
            calls["sql"].append((" ".join(query.split()), values))
            if "`trigger` = 'manual'" in query:
                return [manual_run] if manual_run else []
            if "brief_state = 'pending'" in query:
                return [pending_run] if pending_run else []
            return [Row(name=row["name"], direction=row["direction"], enabled=row["enabled"]) for row in rules]

        db = types.SimpleNamespace(
            sql=sql, get_value=lambda *args, **kwargs: Row(adapter="express", label="Office PC"),
            exists=lambda doctype, name=None: name in tables.get(doctype, {}))
        frappe = types.SimpleNamespace(db=db, whitelist=lambda **kw: (lambda fn: fn), new_doc=lambda doctype: Doc(),
                                       log_error=lambda **kw: None, get_traceback=lambda: "")
        stub = types.SimpleNamespace

        def for_rules(names, since, limit):
            calls["for_rules"].append((names, since, limit))
            if notices is Exception:
                raise RuntimeError("notices table missing")
            return notices or []

        agent = stub(read=lambda connection: {"pending": before or {}},
                     remember=lambda connection, **kw: calls["remember"].append(kw))
        modules = {"frappe": frappe,
                   "frappe.utils": stub(get_datetime=lambda value: value, get_system_timezone=lambda: "Asia/Bangkok",
                                        now_datetime=lambda: datetime(2026, 10, 12, 20, 0)),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub()),
                   "pearnly_cowork.companion": stub(http_auth=stub(), release=stub(latest=lambda: latest)),
                   "pearnly_cowork.connections": stub(access=stub()),
                   "pearnly_cowork.folder_intake": stub(
                       agent=agent, contracts=contracts, inbox=stub(), settings=stub(), workbook=stub(),
                       live=stub(changed=lambda rule, *users: calls["changed"].append(rule)),
                       notify=stub(for_rules=for_rules)),
                   "pearnly_cowork.folder_intake.access": stub(
                       FILE="Cowork Folder Intake File", RULE="Cowork Folder Intake Rule", RUN="Cowork Folder Intake Run",
                       read=lambda doctype, name, for_update=False: tables[doctype][name],
                       write=lambda doc: calls["written"].append(dict(doc)))}
        with mock.patch.dict(sys.modules, modules):
            machine = _load("folder_machine_under_test", APP / "folder_intake/machine.py")
        machine.owner_ready = lambda rule: True
        machine.time = types.SimpleNamespace(time=lambda: self.NOW)
        return machine, calls

    @staticmethod
    def _rule(name, direction="purchase", enabled=1, existing=0, **extra):
        return {"name": name, "direction": direction, "enabled": enabled, "include_existing": existing,
                "rule_revision": 1, "title": name, "root_id": "R", "folder": "", "run_day": 1, "run_time": "22:00",
                "active_since": None, "run_request": 0, "reader": "C1", "connection": "C1",
                "rule_owner": "owner@example.com", **extra}

    def test_purchases_first_with_the_new_task_flag(self):
        machine, calls = self._machine([self._rule("S1", "sales", existing=1), self._rule("P1"),
                                        self._rule("S2", "sales", enabled=0), self._rule("P2", existing=1)])
        reply = machine._sync(types.SimpleNamespace(connection="C1"), {"rule_pending": {"S1": 4, "P1": "x"}})
        self.assertEqual([rule["name"] for rule in reply["rules"]], ["P1", "P2", "S1"])
        self.assertEqual({rule["name"]: rule["existing"] for rule in reply["rules"]}, {"P1": 0, "P2": 1, "S1": 1})
        self.assertEqual(calls["remember"][0]["pending"], {"S1": 4})
        self.assertEqual(calls["changed"], ["S1"])  # 只有待处理数变了的任务推实时事件
        self.assertNotIn("companion_latest", reply)
        self.assertNotIn("notices", reply)  # 旧小助手不带 notices_after：不查
        self.assertEqual(calls["for_rules"], [])

    def test_new_helpers_get_the_latest_version_and_their_notices(self):
        latest = {"version": "1.4.4", "url": "/downloads/companion/CC-Setup.exe"}
        items = [{"id": "pnotice_1", "title": "T", "text": "x", "created": self.NOW - 5, "session": "", "user": "u",
                  "refs": ["P1", "S2"]}]
        machine, calls = self._machine([self._rule("P1"), self._rule("S2", "sales", enabled=0)], latest=latest,
                                       notices=items)
        reply = machine._sync(types.SimpleNamespace(connection="C1"), {"notices_after": self.NOW - 60})
        self.assertEqual(reply["companion_latest"], latest)
        self.assertEqual(reply["notices"], [{"id": "pnotice_1", "title": "T", "text": "x", "created": self.NOW - 5,
                                             "session": ""}])
        # 停用的任务的完成简报也要弹：按本机读的全部任务取
        self.assertEqual(calls["for_rules"], [(["P1", "S2"], self.NOW - 60, contracts.NOTICE_LIMIT)])
        # 本机读的全部任务名（含停用的）：小助手据此清掉久未出现任务的台账；旧小助手不带 notices_after 就不给
        self.assertEqual(reply["rule_names"], ["P1", "S2"])
        old_helper = machine._sync(types.SimpleNamespace(connection="C1"), {})
        self.assertNotIn("rule_names", old_helper)

    def test_a_notice_that_also_names_another_pcs_task_is_not_sent_here(self):
        """security-1：M1 同步时，同一条提醒里只要带着别的电脑读的任务 T2（客户 Y 的任务名与 \\\\srv 路径），整条不给。"""
        items = [{"id": "pnotice_mine", "title": "T", "text": "T1 only", "created": self.NOW - 9, "session": "",
                  "refs": ["T1"]},
                 {"id": "pnotice_mixed", "title": "T", "text": "T1 + T2 \\\\srv\\ClientY", "created": self.NOW - 5,
                  "session": "", "refs": ["T1", "T2"]}]
        machine, _calls = self._machine([self._rule("T1")], notices=items)
        reply = machine._sync(types.SimpleNamespace(connection="M1"), {"notices_after": self.NOW - 60})
        self.assertEqual([item["id"] for item in reply["notices"]], ["pnotice_mine"])
        self.assertNotIn("ClientY", json.dumps(reply, ensure_ascii=False))

    def test_notice_errors_never_break_the_folder_sync(self):
        machine, calls = self._machine([self._rule("P1")], notices=Exception)
        reply = machine._sync(types.SimpleNamespace(connection="C1"), {"notices_after": "bad"})
        self.assertEqual((reply["notices"], [rule["name"] for rule in reply["rules"]]), ([], ["P1"]))
        self.assertEqual(calls["for_rules"][0][1], self.NOW - contracts.NOTICE_WINDOW)

    def _run(self, row, **kwargs):
        machine, calls = self._machine([self._rule("P1", run_day=0, run_time="08:00")], **kwargs)
        base = {"rule": "P1", "run_key": "a" * 32, "trigger": "schedule", "found": 2, "new_files": 2, "skipped": 0,
                "upload_failed": 0, "scanned": True}
        machine._record_run(types.SimpleNamespace(connection="C1"), {**base, **row})
        return calls["written"][-1]

    def _naive(self, epoch):
        return datetime.fromtimestamp(epoch, ZoneInfo("Asia/Bangkok")).replace(tzinfo=None)

    def test_scheduled_runs_belong_to_their_slot(self):
        slot = self.NOW - 3 * 86400
        written = self._run({"slot": slot})
        self.assertEqual((written["run_owner"], written["brief_state"], written["slot_at"], written["brief_group"]),
                         ("owner@example.com", "pending", self._naive(slot), contracts.slot_group(self._naive(slot))))
        # 旧小助手不报所属时刻（或报得离谱）：按任务的运行日与时间算最近一次
        expected = self._naive(contracts.latest_slot(self.NOW, 0, "08:00"))
        self.assertEqual(self._run({"trigger": "catch_up"})["slot_at"], expected)
        self.assertEqual(self._run({"slot": self.NOW - 50 * 86400})["brief_group"], contracts.slot_group(expected))

    def test_run_now_starts_its_own_brief_or_joins_the_batch_of_the_last_ten_minutes(self):
        """interfaces-1：立即运行不再都并进（负责人, 空）一份：10 分钟内没有同一负责人的立即运行就自己起一批（分组 =
        自己的记录名）；有就并进那一批（CC 连着点几个任务仍是一份）。"""
        key = hashlib.sha256(f"P1\0{'a' * 32}".encode()).hexdigest()
        machine, calls = self._machine([self._rule("P1", run_day=0, run_time="08:00")])
        machine._record_run(types.SimpleNamespace(connection="C1"),
                            {"rule": "P1", "run_key": "a" * 32, "trigger": "manual", "found": 0, "new_files": 0,
                             "skipped": 0, "upload_failed": 0})
        written = calls["written"][-1]
        self.assertEqual((written["slot_at"], written["brief_state"], written["brief_group"]), (None, "pending", key))
        query, values = next(item for item in calls["sql"] if "`trigger` = 'manual'" in item[0])
        self.assertIn("brief_state = 'pending' AND run_owner = %s", query)
        self.assertEqual(values, ("owner@example.com", datetime(2026, 10, 12, 19, 50)))  # 此刻 20:00 往前 10 分钟
        joined = self._run({"trigger": "manual"}, manual_run=Row(name="FIRST", slot_at=None, brief_group="FIRST"))
        self.assertEqual((joined["slot_at"], joined["brief_group"]), (None, "FIRST"))
        # 那一批的第一次运行是加字段前记下的（没有分组）：按它的记录名
        legacy = self._run({"trigger": "manual"}, manual_run=Row(name="OLD", slot_at=None, brief_group=None))
        self.assertEqual(legacy["brief_group"], "OLD")

    def test_retries_join_only_an_unsent_brief_and_take_its_group(self):
        earlier = datetime(2026, 10, 1, 8, 0)
        parent = Row(name="PARENT", slot_at=earlier, brief_group="2026-10-01T08:00:00")
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 1}, pending_run=parent)
        self.assertEqual((written["slot_at"], written["brief_state"], written["brief_group"]),
                         (earlier, "pending", "2026-10-01T08:00:00"))
        # 补的是一次立即运行：跟着它那一批
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 1},
                            pending_run=Row(name="RUNNOW", slot_at=None, brief_group="FIRST"))
        self.assertEqual((written["slot_at"], written["brief_group"]), (None, "FIRST"))
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 0}, pending_run=parent)
        self.assertEqual((written["slot_at"], written["brief_state"]), (None, "skipped"))
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 3})
        self.assertEqual(written["brief_state"], "skipped")


class TaskSources(unittest.TestCase):
    """新字段、补丁、Desk 页与词条的源码守护（2026-10-12）。"""

    @staticmethod
    def _doctype(name):
        return json.loads((APP / f"pearnly_cowork/doctype/{name}/{name}.json").read_text())

    def _field(self, doctype, fieldname):
        return next(field for field in self._doctype(doctype)["fields"] if field["fieldname"] == fieldname)

    def test_new_fields_are_read_only_and_old_tasks_keep_their_baseline(self):
        rule = "cowork_folder_intake_rule"
        existing = self._field(rule, "include_existing")
        # 老任务（加列前建的）默认 0 = 照旧拍底；只有新建或换了文件夹才是 1
        self.assertEqual((existing["fieldtype"], existing["default"], existing["read_only"]), ("Check", "0", 1))
        self.assertEqual((self._field(rule, "remind")["fieldtype"], self._field(rule, "remind")["default"]), ("Check", "0"))
        self.assertEqual(self._field(rule, "cc_session")["length"], 140)
        run = "cowork_folder_intake_run"
        state = self._field(run, "brief_state")
        self.assertEqual((state["options"], state["search_index"]), ("pending\nsent\nskipped", 1))
        self.assertEqual(self._field(run, "run_owner")["options"], "User")
        group = self._field(run, "brief_group")  # 2026-10-13：简报分组（定时时刻 / 立即运行那一批 / 补传跟父运行）
        self.assertEqual((group["fieldtype"], group["read_only"], group["search_index"]), ("Data", 1, 1))
        self.assertEqual(self._field("cowork_folder_intake_file", "source_file")["search_index"], 1)
        # 改了字段就升 modified，migrate 才同步
        for name, modified in ((rule, "2026-10-12"), (run, "2026-10-13"), ("cowork_folder_intake_file", "2026-10-12")):
            doctype = self._doctype(name)
            self.assertEqual(doctype["modified"], modified + " 12:00:00.000000")
            self.assertEqual(doctype["field_order"], [field["fieldname"] for field in doctype["fields"]])

    def test_history_runs_get_no_brief(self):
        self.assertIn("pearnly_cowork.patches.v0_0_16_folder_run_briefs", (APP / "patches.txt").read_text())
        patch = (APP / "patches/v0_0_16_folder_run_briefs.py").read_text()
        self.assertIn('if not frappe.db.has_column(RUN, "brief_state"):', patch)
        self.assertIn("SET brief_state = 'skipped' WHERE IFNULL(brief_state, '') IN ('', 'pending')", patch)

    def test_rules_and_sync_follow_the_agreement(self):
        rules = (APP / "folder_intake/rules.py").read_text()
        # 换没换文件夹不分大小写（Windows 上是同一个文件夹）；接续停用任务的只收此后放进来的
        self.assertIn('place = (_reader(rule), rule.root_id, (rule.folder or "").casefold()) if name else None', rules)
        self.assertIn("if not name or place != (reader, root, folder.casefold()):", rules)
        self.assertIn("rule.include_existing = 0 if continued else 1", rules)
        self.assertIn("def save(values, *, cc_session=None):", rules)
        self.assertIn("def save_many(items, *, cc_session=None):", rules)
        self.assertIn("def set_enabled(name, enabled=None, *, remind=None, cc_session=None):", rules)
        self.assertNotIn('values.get("cc_session")', rules)  # 对话编号只从参数取，网页报文传不进来
        self.assertEqual(ast.literal_eval(rules.split("BATCH_MAX = ", 1)[1].split()[0]), 20)
        api = (APP / "folder_intake/api.py").read_text()
        self.assertIn("def set_enabled(name, enabled):\n    return rules.set_enabled(name, enabled)", api)
        machine = (APP / "folder_intake/machine.py").read_text()
        self.assertIn('"existing": int(rule.include_existing or 0),', machine)
        self.assertIn('pending = contracts.pending(body.get("rule_pending"))', machine)

    def test_the_desk_page_refreshes_itself_and_shows_progress(self):
        source = (APP / "public/desk/intake-folders.js").read_text()
        self.assertIn('events: ["cowork_folder_changed"],', source)
        self.assertIn("live.stop();", source)
        self.assertIn('row.results?.moving ? ["blue", ft("cowork.folder.moving", row.results.moving)]', source)
        self.assertIn('"cowork.folder.enableConfirmAll"', source)
        self.assertIn('{fieldtype: "Check", fieldname: "remind", label: ft("cowork.folder.field.remind")', source)
        self.assertIn("remind: values.remind ? 1 : 0", source)
        self.assertIn('ft("cowork.folder.waiting", partial ? `${count}+` : count)', source)
        # interfaces-3 / 4：列表与编辑弹窗（文件夹没改）说待处理数，不说「现有」；只有 >0 才说
        self.assertIn('count > 0 ? ft("cowork.folder.pending", partial ? `${count}+` : count) : ""', source)
        self.assertIn("row.enabled ? pending_hint(row.waiting, row.waiting_partial) : \"\"", source)
        self.assertIn("{files: rule.waiting, partial: rule.waiting_partial, pending: true}", source)
        self.assertIn("(found.pending ? pending_hint : files_hint)(found.files, found.partial)", source)
        # business-logic-5：小助手离线时在途的票不会动，不进 10 秒兜底
        self.assertIn("busy: () => (data.rules || []).some(row => row.results?.moving && row.agent_fresh),", source)
        live = (APP / "folder_intake/live.py").read_text()
        self.assertIn('EVENT = "cowork_folder_changed"', live)
        self.assertIn("after_commit=True", live)

    def test_new_terms_exist_in_four_languages_without_express(self):
        with (APP / "i18n/messages.tsv").open(encoding="utf-8") as handle:
            rows = {row["key"]: row for row in csv.DictReader(handle, delimiter="\t") if row.get("key")}
        for key in ("cowork.folder.waiting", "cowork.folder.moving", "cowork.folder.enableConfirmAll",
                    "cowork.folder.field.remind", "cowork.folder.remind.on", "cowork.folder.remind.off",
                    "cowork.folder.waiting_purchases", "cowork.folder.pending", "cowork.folder.continues"):
            for lang in ("en", "zh", "th", "ja"):
                self.assertTrue(rows[key][lang].strip(), (key, lang))
                self.assertNotIn("express", rows[key][lang].casefold(), (key, lang))
        self.assertIn("{0}", rows["cowork.folder.waiting"]["th"])
        # 待处理数与接续停用任务各一个占位符（CC 任务页 `tr('cowork.folder.pending', n)` 同一条）
        for key in ("cowork.folder.pending", "cowork.folder.continues"):
            for lang in ("en", "zh", "th", "ja"):
                self.assertEqual((rows[key][lang].count("{0}"), "{1}" in rows[key][lang]), (1, False), (key, lang))
        self.assertEqual(rows["cowork.folder.pending"]["zh"], "还有 {0} 份未处理，到点处理")
        self.assertEqual(rows["cowork.folder.continues"]["zh"], "接续定时任务「{0}」，只处理此后放入的文件")
        # 提醒里的份数是待处理数，不说「现有」
        for key in ("cowork.folder.remind.count", "cowork.folder.remind.lineCount", "cowork.folder.pending"):
            self.assertNotIn("现有", rows[key]["zh"], key)

    def test_reminder_and_brief_terms_in_four_languages_without_express(self):
        import re

        with (APP / "i18n/messages.tsv").open(encoding="utf-8") as handle:
            rows = {row["key"]: row for row in csv.DictReader(handle, delimiter="\t") if row.get("key")}
        sources = (APP / "folder_intake/reminders.py").read_text() + (APP / "folder_intake/briefs.py").read_text()
        used = set(re.findall(r'_\("(cowork\.[A-Za-z0-9_.]+)"\)', sources))
        new = {key for key in used if key.startswith(("cowork.folder.remind.", "cowork.folder.brief."))}
        self.assertEqual(len(new), 29)
        self.assertTrue({"cowork.folder.line", "cowork.folder.more", "cowork.folder.moving", "cowork.folder.folderMissing",
                         "cowork.folder.state.registered", "cowork.choice.account", "cowork.folder.rootItself"} <= used)
        for key in used:
            placeholders = set(re.findall(r"\{\d\}", rows[key]["en"]))
            for lang in ("en", "zh", "th", "ja"):
                self.assertTrue(rows[key][lang].strip(), (key, lang))
                self.assertEqual(set(re.findall(r"\{\d\}", rows[key][lang])), placeholders, (key, lang))
                if key in new or key in ("cowork.folder.line", "cowork.folder.more"):
                    self.assertNotIn("express", rows[key][lang].casefold(), (key, lang))
        self.assertNotIn("cowork.choice.party", used)  # 现有的对方档词条带 Express，简报另起一条
        self.assertEqual(rows["cowork.folder.remind.one"]["zh"], "「{0}」{1} 开始（{2}）。请将资料放入 {3}，确保资料齐全。")


if __name__ == "__main__":
    unittest.main()
