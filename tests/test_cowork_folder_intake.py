"""文件夹自动录入：纯规则 + 「只新增、不改现有」的守护（不连库、不依赖 Frappe）。"""
import ast
import csv
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

    def test_hook_targets_exist(self):
        for dotted in ("pearnly_cowork.folder_intake.schedule.tick", "pearnly_cowork.folder_intake.events.recognized", "pearnly_cowork.folder_intake.access.deny",
                       "pearnly_cowork.folder_intake.access.deny_query", "pearnly_cowork.folder_intake.access.reject_share",
                       "pearnly_cowork.folder_intake.pump.run"):
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


class ScheduleSlots(unittest.TestCase):
    """服务端算的定时时刻与小助手 `folder_plan` 逐位一致（运行记录按它归属完成简报）。"""

    def test_latest_slot_matches_the_companion(self):
        start = datetime(2026, 1, 1, tzinfo=contracts.BANGKOK).timestamp()
        for step in range(0, 3 * 366 * 24, 7):
            now = start + step * 3600 + 1234
            for day in (0, 1, 15, 28, 29, 30, 31):
                for at in ("00:00", "07:30", "22:00", "23:59"):
                    self.assertEqual(contracts.latest_slot(now, day, at), folder_plan.latest_slot(now, day, at),
                                     (now, day, at))

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

    def test_notify_reads_only_this_pcs_tasks_from_the_shared_notices(self):
        seen = []
        notices = types.SimpleNamespace(
            for_references=lambda doctype, names, since, limit: seen.append((doctype, names, since, limit)) or [{"id": "x"}])
        modules = {"pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(),
                   "pearnly_cowork.folder_intake.access": types.SimpleNamespace(RULE="Cowork Folder Intake Rule"),
                   "pearnly_core": types.ModuleType("pearnly_core"),
                   "pearnly_core.assistant": types.SimpleNamespace(notices=notices)}
        with mock.patch.dict(sys.modules, modules):
            notify = _load("folder_notify_under_test", APP / "folder_intake/notify.py")
            self.assertEqual(notify.for_rules([], 1.0), [])
            self.assertEqual(notify.for_rules(["R1", "", "R2"], 5.0, 20), [{"id": "x"}])
        self.assertEqual(seen, [("Cowork Folder Intake Rule", ["R1", "R2"], 5.0, 20)])


class MachineSync(unittest.TestCase):
    """同步：下发 existing、进货任务在前、收待处理数、新版提示与提醒；运行记录归属简报（`_record_run`）。"""
    NOW = 1_760_000_000.0  # 2025-10-09 15:53 曼谷

    def _machine(self, rules, *, latest=None, notices=None, before=None, pending_run=None):
        calls = {"remember": [], "changed": [], "for_rules": [], "written": []}
        tables = {"Cowork Folder Intake Rule": {row["name"]: Row(row) for row in rules}}

        class Doc(Row):
            def is_new(self):
                return True

            def update(self, values):
                dict.update(self, values)

            def __setattr__(self, key, value):
                self[key] = value

        def sql(query, values=None, as_dict=False):
            if "brief_state = 'pending'" in query:
                return [Row(slot_at=pending_run)] if pending_run else []
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
        items = [{"id": "pnotice_1", "title": "T", "text": "x", "created": self.NOW - 5, "session": "", "user": "u"}]
        machine, calls = self._machine([self._rule("P1"), self._rule("S2", "sales", enabled=0)], latest=latest,
                                       notices=items)
        reply = machine._sync(types.SimpleNamespace(connection="C1"), {"notices_after": self.NOW - 60})
        self.assertEqual(reply["companion_latest"], latest)
        self.assertEqual(reply["notices"], [{"id": "pnotice_1", "title": "T", "text": "x", "created": self.NOW - 5,
                                             "session": ""}])
        # 停用的任务的完成简报也要弹：按本机读的全部任务取
        self.assertEqual(calls["for_rules"], [(["P1", "S2"], self.NOW - 60, contracts.NOTICE_LIMIT)])

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
        self.assertEqual((written["run_owner"], written["brief_state"], written["slot_at"]),
                         ("owner@example.com", "pending", self._naive(slot)))
        # 旧小助手不报所属时刻（或报得离谱）：按任务的运行日与时间算最近一次
        expected = self._naive(contracts.latest_slot(self.NOW, 0, "08:00"))
        self.assertEqual(self._run({"trigger": "catch_up"})["slot_at"], expected)
        self.assertEqual(self._run({"slot": self.NOW - 50 * 86400})["slot_at"], expected)

    def test_run_now_has_no_slot_and_retries_join_only_an_unsent_brief(self):
        written = self._run({"trigger": "manual"})
        self.assertEqual((written["slot_at"], written["brief_state"]), (None, "pending"))
        earlier = datetime(2026, 10, 1, 8, 0)
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 1}, pending_run=earlier)
        self.assertEqual((written["slot_at"], written["brief_state"]), (earlier, "pending"))
        written = self._run({"trigger": "manual", "note": "retry", "new_files": 0}, pending_run=earlier)
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
        self.assertEqual(self._field("cowork_folder_intake_file", "source_file")["search_index"], 1)
        for name in (rule, run, "cowork_folder_intake_file"):
            doctype = self._doctype(name)
            self.assertEqual(doctype["modified"], "2026-10-12 12:00:00.000000")  # 改了字段就升 modified，migrate 才同步
            self.assertEqual(doctype["field_order"], [field["fieldname"] for field in doctype["fields"]])

    def test_history_runs_get_no_brief(self):
        self.assertIn("pearnly_cowork.patches.v0_0_16_folder_run_briefs", (APP / "patches.txt").read_text())
        patch = (APP / "patches/v0_0_16_folder_run_briefs.py").read_text()
        self.assertIn('if not frappe.db.has_column(RUN, "brief_state"):', patch)
        self.assertIn("SET brief_state = 'skipped' WHERE IFNULL(brief_state, '') IN ('', 'pending')", patch)

    def test_rules_and_sync_follow_the_agreement(self):
        rules = (APP / "folder_intake/rules.py").read_text()
        self.assertIn("if not name or place != (reader, root, folder):\n        rule.include_existing = 1", rules)
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
        live = (APP / "folder_intake/live.py").read_text()
        self.assertIn('EVENT = "cowork_folder_changed"', live)
        self.assertIn("after_commit=True", live)

    def test_new_terms_exist_in_four_languages_without_express(self):
        with (APP / "i18n/messages.tsv").open(encoding="utf-8") as handle:
            rows = {row["key"]: row for row in csv.DictReader(handle, delimiter="\t") if row.get("key")}
        for key in ("cowork.folder.waiting", "cowork.folder.moving", "cowork.folder.enableConfirmAll",
                    "cowork.folder.field.remind", "cowork.folder.remind.on", "cowork.folder.remind.off",
                    "cowork.folder.waiting_purchases"):
            for lang in ("en", "zh", "th", "ja"):
                self.assertTrue(rows[key][lang].strip(), (key, lang))
                self.assertNotIn("express", rows[key][lang].casefold(), (key, lang))
        self.assertIn("{0}", rows["cowork.folder.waiting"]["th"])


if __name__ == "__main__":
    unittest.main()
