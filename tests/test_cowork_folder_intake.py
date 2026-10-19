"""文件夹自动录入：纯规则 + 「只新增、不改现有」的守护（不连库、不依赖 Frappe）。"""
import ast
import csv
import hashlib
import importlib.util
import json
import pathlib
import re
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
desktop_update = _load("connections_desktop_update", APP / "connections/desktop_update.py")


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


class FetchContracts(unittest.TestCase):
    """CC 从已授权文件夹列文件、取文件（2026-10 CC 闭环 L2）：路径规则、类型与对话附件同一份。"""

    def test_fetch_types_are_the_chat_attachment_types(self):
        shared = _load("assistant_contracts_for_fetch", ROOT / "apps/pearnly_core/pearnly_core/assistant/contracts.py")
        self.assertEqual(set(contracts.FETCH_EXTENSIONS), set(shared.UPLOAD_EXTENSIONS))
        self.assertEqual(len(contracts.FETCH_EXTENSIONS), len(set(contracts.FETCH_EXTENSIONS)))
        # 本模块按路径单独加载、不依赖共用层（字面值）
        tree = ast.parse((APP / "folder_intake/contracts.py").read_text(encoding="utf-8"))
        imported = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)} | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertFalse([name for name in imported if name.startswith(("pearnly_", "frappe"))])

    def test_a_fetch_path_stays_under_the_root_and_may_be_in_the_result_folder(self):
        self.assertEqual(contracts.fetch_path(" ACME/bank\\kbank-09.PDF "), "ACME\\bank\\kbank-09.PDF")
        self.assertEqual(contracts.fetch_path("ACME\\_Pearnly结果\\2026-10\\INV1.xlsx"), "ACME\\_Pearnly结果\\2026-10\\INV1.xlsx")
        for bad in ("..\\x.pdf", "a\\..\\b.pdf", "C:\\x.pdf", "a|b.pdf", "", "\\\\", "a.\\b.pdf"):
            with self.assertRaises(contracts.ContractError, msg=bad) as caught:
                contracts.fetch_path(bad)
            self.assertEqual(caught.exception.code, "cowork.folder.fetch_path_invalid")
        for bad in ("a.exe", "a", "a.pdf.lnk"):
            with self.assertRaises(contracts.ContractError, msg=bad) as caught:
                contracts.fetch_path(bad)
            self.assertEqual(caught.exception.code, "cowork.folder.fetch_type_invalid")
        self.assertEqual(contracts.browse_folder(""), "")
        self.assertEqual(contracts.browse_folder("ACME/_Pearnly Results"), "ACME\\_Pearnly Results")
        with self.assertRaises(contracts.ContractError):
            contracts.browse_folder("..")

    def test_each_part_of_a_path_is_kept_as_it_is(self):
        # 名字首尾带空格的文件夹 / 文件（经 SMB 从别的系统建的）：清单里怎么报就怎么取，与小助手 `_parts` 同一规则
        self.assertEqual(contracts.fetch_path("ACME \\ scan.pdf"), "ACME \\ scan.pdf")
        self.assertEqual(contracts.browse_folder("ACME /bank"), "ACME \\bank")
        listed = contracts.fetched_listing({"files": [{"path": "ACME \\ scan.pdf", "size": 1, "modified": 1}]})
        self.assertEqual([(row["path"], row["name"]) for row in listed["files"]], [("ACME \\ scan.pdf", " scan.pdf")])
        for bad in ("ACME\\ \\a.pdf", "ACME\\ /a.pdf"):
            with self.assertRaises(contracts.ContractError, msg=bad) as caught:
                contracts.fetch_path(bad)
            self.assertEqual(caught.exception.code, "cowork.folder.fetch_path_invalid")

    def test_types_and_the_returned_listing(self):
        self.assertEqual(contracts.fetch_extensions(["PDF", ".xlsx", "pdf"]), [".pdf", ".xlsx"])
        self.assertEqual(contracts.fetch_extensions(None), [])
        with self.assertRaises(contracts.ContractError):
            contracts.fetch_extensions([".exe"])
        listed = contracts.fetched_listing({"files": [
            {"path": "ACME\\a.pdf", "size": 10, "modified": 1.5},
            {"path": "..\\evil.pdf", "size": 1, "modified": 1},  # 不合规的丢掉
            {"path": "ACME\\_ผลลัพธ์ Pearnly\\r.xlsx", "size": 2, "modified": 2},
            {"path": "ACME\\b.pdf", "size": True, "modified": 2}, {"path": "ACME\\c.pdf", "size": 1, "modified": 1e300},
            {"path": "ACME\\d.pdf", "size": 1, "modified": float("nan")}, "junk"], "truncated": True, "scanned": 9})
        self.assertEqual([row["path"] for row in listed["files"]], ["ACME\\a.pdf", "ACME\\_ผลลัพธ์ Pearnly\\r.xlsx"])
        self.assertEqual([row["result_folder"] for row in listed["files"]], [False, True])
        self.assertEqual((listed["truncated"], listed["scanned"]), (True, 9))
        with self.assertRaises(contracts.ContractError):
            contracts.fetched_listing([])


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
        # 推送状态变了：定时任务那份文件马上刷新（只此一个入口）
        self.assertIn('cowork_push_state_changed = ["pearnly_cowork.folder_intake.events.pushed"]', source)

    def test_reminders_and_briefs_run_every_five_minutes(self):
        tree = ast.parse((APP / "hooks.py").read_text())
        events = next(ast.literal_eval(node.value) for node in tree.body
                      if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "scheduler_events")
        five = events["cron"]["*/5 * * * *"]
        # 只在末尾追加，现有三项不动
        self.assertEqual(five, ["pearnly_cowork.push.mrerp.runner.sweep", "pearnly_cowork.work.reminders.run",
                                "pearnly_cowork.recon_center.jobs.resume_abandoned",
                                "pearnly_cowork.folder_intake.schedule.notices",
                                # 数字会计员第 3 期：网页上传 / LINE 的票按公司档位自动推，同一个 5 分钟入口
                                "pearnly_cowork.push.automation.sweep.run"])
        self.assertIn("pearnly_cowork.folder_intake.schedule.tick", events["cron"]["* * * * *"])
        self.assertNotIn("pearnly_cowork.folder_intake.schedule.notices", events["cron"]["* * * * *"])

    def test_hook_targets_exist(self):
        for dotted in ("pearnly_cowork.folder_intake.schedule.tick", "pearnly_cowork.folder_intake.events.recognized",
                       "pearnly_cowork.folder_intake.events.pushed", "pearnly_cowork.folder_intake.pump.refresh",
                       "pearnly_cowork.folder_intake.access.deny",
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


# `application.assess` 判成 A 类、这次照推的样子（数字会计员第 3 期）
PUSH_A = {"assessed": True, "class": "A", "tier": "L0", "action": "push", "why": "", "codes": "", "proposal": None,
          "checked_at": None, "automation": {"class": "A", "tier": "L0", "trigger": "task", "reasons": []}}


class OfflineCompanionRetry(unittest.TestCase):
    """小助手没连上：不准备（或撤回刚好断线时的准备）、回 retry、重试换请求号；连着但没报上账套、或最后一次，照现有规则走。"""

    def _push(self, reported=True, online=lambda: True, status="prepared", queueable=None):
        """``online``：替这张单干活的电脑里有没有能写的（`destinations.writer_state`，D4：电脑关着也算）：真 = ``ready``、
        假 = ``missing``（一台电脑都没有），也可直接回状态串；``queueable`` 不给就同它。"""
        queueable = queueable or online

        def state():
            found = queueable()
            return found if isinstance(found, str) else ("ready" if found else "missing")
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
            calls.setdefault("route_device", []).append(kwargs.get("route_device"))
            return {"status": status, "name": "CWP-1", "revision": 1, "has_reported_credential": reported}

        # 推 Express 之前的判定（第 3 期 `application.assess`）：这里的票都判成 A 类、照推
        application = types.SimpleNamespace(
            choices_view=lambda *args, **kwargs: {"needed": []}, submit=submit,
            cancel_prepared=lambda name, revision: calls["cancel"].append(name),
            submission_failed=lambda history, reason: None,
            assess=lambda *args, **kwargs: calls.setdefault("assessed", []).append(kwargs) or dict(PUSH_A))
        express = types.SimpleNamespace(confirm=lambda *args: calls["confirm"].append(args))
        destinations = types.SimpleNamespace(of=lambda adapter: express, online=lambda adapter, connection: False,
                                             READY="ready", UNREACHABLE="unreachable", MISSING="missing",
                                             writer_state=lambda adapter, connection, device="": (
                                                 calls.setdefault("asked", []).append(device) or state()),
                                             ready=lambda adapter, prepared: bool(prepared.get("has_reported_credential")))
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   # 任务没记公司（T1 之前的任务）：不跟随，照旧推往固定账套
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(
                       contracts=contracts, destinations=destinations,
                       follow=types.SimpleNamespace(choose=lambda rule, history, device=None: None)),
                   "pearnly_cowork.history": types.SimpleNamespace(push_state=types.SimpleNamespace(
                       record_assessment=lambda history, **kwargs: calls.setdefault("judged", []).append(kwargs))),
                   "pearnly_cowork.push": types.SimpleNamespace(choices=types.SimpleNamespace()),
                   "pearnly_cowork.push.application": types.SimpleNamespace(service=application)}
        with mock.patch.dict(sys.modules, modules):
            push = _load("folder_push_under_test", APP / "folder_intake/push.py")
        rule = types.SimpleNamespace(connection="C", account_set_key="A", posting_kind="purchase", defaults_json="{}",
                                     reader_device="CCD-RULE")
        return push, rule, types.SimpleNamespace(name="E", filename="bill.pdf", reader_device="CCD-READ"), calls

    def test_days_from_a_pos_export_push_with_their_own_posting_kind(self):
        push, rule, entry, calls = self._push()
        seen = []
        submit = push.application.submit
        push.application.submit = lambda *args, **kwargs: seen.append(args[8]) or submit(*args, **kwargs)
        rule.posting_kind = "stock"
        push.push_one(entry, rule, "H", 0)
        push.push_one(types.SimpleNamespace(name="E2", filename="ยอดขาย.xlsx"), rule, "H", 0)
        self.assertEqual(seen, ["stock", None])  # 带商品的天是存货，其余按金额记：按票上自己的口径

    def test_a_pool_with_a_writer_queues_even_while_its_computers_are_off(self):
        """D4（ADR-021 第 4 批）：池里有能写的电脑（关着也算）就准备、确认、排队，开机后写；不再每 10 分钟重试、6 小时记失败。
        电脑此刻在不在线（`destinations.online`，这里一直是不在线）不再拦定时任务的推送。"""
        push, rule, entry, calls = self._push(queueable=lambda: True)
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False)["status"], "sent")
        self.assertEqual((len(calls["keys"]), len(calls["confirm"]), calls["cancel"]), (1, 1, []))
        source = (APP / "folder_intake/push.py").read_text(encoding="utf-8")
        self.assertNotIn("destinations.online(", source)

    def test_a_scheduled_ticket_is_frozen_to_the_computer_that_read_its_folder(self):
        """用户 10-16（ADR-021 第 51 条）：定时任务由读那个文件夹的电脑写——能不能排按那台判，推送记录上冻结它。文件行记的
        电脑优先于任务上的；都没记（补丁前的旧任务）按负责人自己的电脑。"""
        push, rule, entry, calls = self._push(queueable=lambda: True)
        push.push_one(entry, rule, "H", 0, final=False)
        self.assertEqual((calls["asked"], calls["route_device"]), (["CCD-READ"], ["CCD-READ"]))
        entry.reader_device = ""
        push.push_one(entry, rule, "H", 1, final=False)
        self.assertEqual(calls["route_device"][-1], "CCD-RULE")
        entry.reader_device = rule.reader_device = None
        push.push_one(entry, rule, "H", 2, final=False)
        self.assertEqual((calls["asked"][-1], calls["route_device"][-1]), ("", ""))

    def test_offline_companion_waits_without_preparing(self):
        push, rule, entry, calls = self._push(online=lambda: False)
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False), {"status": "retry", "reason": push.OFFLINE})
        self.assertEqual((calls["keys"], calls["cancel"], calls["confirm"]), ([], [], []))

    def test_a_reader_that_cannot_reach_a_writable_set_fails_at_once_instead_of_retrying(self):
        """10-16 审查：读文件夹的那台电脑在、却够不到能写的账套（只读成员、账套只在别人电脑上、已移除）不是暂时的——不按
        「CC 桌面版未连接」每十分钟重试 6 小时，照常准备、确认，由确认按现有原因（``push.no_writer``）直接记下。"""
        push, rule, entry, calls = self._push(reported=False, queueable=lambda: "unreachable")
        failed = []
        push.application.submission_failed = lambda history, reason: failed.append((history, reason))

        def refuse(*args):
            raise push.frappe.ValidationError("push.no_writer")

        push.destinations.of = lambda adapter: types.SimpleNamespace(confirm=refuse)
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False), {"status": "failed", "reason": "push.no_writer"})
        self.assertEqual((len(calls["keys"]), calls["cancel"], failed), (1, ["CWP-1"], [("H", "push.no_writer")]))
        self.assertEqual(calls["asked"][0], "CCD-READ")

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

    def test_a_chart_still_being_read_is_retried_soon_without_preparing(self):
        """2026-10-13 录制：定时任务推往电脑没选的账套，科目表没读过，95 张票全停在「要人选」却没有可选的科目。
        现在推送服务已请电脑读（`wait`），这里过一分钟再推；电脑离线按离线重试；要升级 / 读不到、或最后一次照实记在票上。"""
        push, rule, entry, calls = self._push()
        failures = []
        push.push_state.record_failure = lambda history, reason, **k: failures.append((history, reason, k))
        waiting = {"state": "reading", "message": "reading"}
        push.application.choices_view = lambda *a, **k: {"needed": [{"kind": "account"}], "wait": dict(waiting)}
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False),
                         {"status": "retry", "reason": push.READING, "seconds": push.READING_SECONDS})
        waiting["state"] = "offline"
        self.assertEqual(push.push_one(entry, rule, "H", 0, final=False)["reason"], push.OFFLINE)
        self.assertEqual((calls["keys"], failures), ([], []))
        for state, final in (("update_needed", False), ("reading", True)):
            waiting.update(state=state, message="msg:" + state)
            self.assertEqual(push.push_one(entry, rule, "H", 36, final=final)["status"], "failed")
        going = {"connection": rule.connection, "account_set": rule.account_set_key}  # 停下时去向一并抄到票上（验收 F17）
        self.assertEqual(failures, [("H", "msg:update_needed", going), ("H", "msg:reading", going)])
        self.assertEqual(calls["keys"], [])  # 没有准备过、也没有留下「要人选」的记录

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


class PosExports(unittest.TestCase):
    """L9（2026-10-13）：推往 Express 的销售任务另收 POS 导出（Excel / CSV），不交识别、按天建票；列认不准时停下待人。"""

    def test_only_sales_tasks_into_express_take_pos_exports(self):
        self.assertEqual(contracts.extensions_for("sales", "express"), contracts.EXTENSIONS + contracts.TABLE_EXTENSIONS)
        self.assertEqual(contracts.extensions_for("purchase", "express"), contracts.EXTENSIONS)
        self.assertEqual(contracts.extensions_for("sales", "mrerp"), contracts.EXTENSIONS)
        sales = contracts.extensions_for("sales", "express")
        self.assertEqual(contracts.filename("ยอดขาย ต.ค..xlsx", sales), "ยอดขาย ต.ค..xlsx")
        self.assertEqual(contracts.filename("Receipts.CSV", sales), "Receipts.CSV")
        for name, allowed in (("Receipts.csv", contracts.EXTENSIONS), ("old.xls", contracts.EXTENSIONS),
                              ("a.docx", sales)):
            with self.assertRaises(contracts.ContractError):
                contracts.filename(name, allowed)
        self.assertEqual(contracts.filename("bill.pdf"), "bill.pdf")  # 旧调用照旧
        self.assertTrue(contracts.is_table("Receipts.xlsm") and not contracts.is_table("bill.pdf"))
        # 旧版 Excel 照样登记（登记即记失败、写明原因），不静默漏掉
        self.assertEqual(contracts.filename("old.XLS", sales), "old.XLS")
        self.assertTrue(contracts.is_legacy_table("old.XLS") and not contracts.is_legacy_table("pos.xlsx"))

    def test_the_result_workbook_of_an_export_is_one_file(self):
        self.assertEqual(contracts.pos_workbook_name("ยอดขาย ต.ค. 2569.xlsx"), "ยอดขาย ต.ค. 2569_POS.xlsx")
        self.assertEqual(contracts.pos_workbook_name('a:b?.csv'), "a-b-_POS.xlsx")  # Windows 不认的字符换成 -
        self.assertEqual(contracts.pos_workbook_name(".csv"), "POS_POS.xlsx")

    def test_an_export_waiting_for_its_columns_is_stopped_not_moving(self):
        now = datetime(2026, 10, 13, 20, 0)
        held = {"state": "source_ready", "last_code": contracts.POS_LAYOUT_NEEDED, "registered_at": now}
        self.assertFalse(contracts.moving(held["state"], now, now, held["last_code"]))
        self.assertTrue(contracts.moving("source_ready", now, now, ""))
        found = contracts.tally([held, {"state": "source_ready", "last_code": "", "registered_at": now}], now)
        self.assertEqual((found["stopped"], found["moving"], found["stop_reasons"]), (1, 1, {"pos_layout": 1}))
        runs = [{"name": "R1", "run_owner": "o", "brief_group": "g", "connection": "C1", "scanned_at": now,
                 "started_at": now - timedelta(minutes=30)}]
        ready, _stale = contracts.brief_groups(runs, {"R1": [{**held, "finished_at": None}]}, now)
        self.assertEqual([group["complete"] for group in ready], [True])  # 不为它等到最长 13 小时
        self.assertIn("receipt", contracts.CHOICE_KINDS)

    def test_pos_exports_are_counted_apart_from_documents(self):
        rows = contracts.roots([{"id": "R1", "label": "D:\\Shop", "folders": ["Sales"],
                                 "counts": {"": 3, "Sales": 1}, "table_counts": {"Sales": 2, "Nope": 1}, "partial": False}])
        self.assertEqual((rows[0]["counts"], rows[0]["table_counts"]), ({"": 3, "Sales": 1}, {"Sales": 2}))
        self.assertNotIn("table_counts", contracts.roots([{"id": "R1", "label": "x", "table_counts": {"": 1}}])[0])

    def test_helper_extensions_match_the_server(self):
        self.assertEqual(folder_plan.EXTENSIONS, contracts.EXTENSIONS)
        self.assertEqual(folder_plan.TABLE_EXTENSIONS, contracts.TABLE_EXTENSIONS)
        self.assertEqual(folder_plan.rule_extensions({}), folder_plan.EXTENSIONS)  # 旧云端不下发：只收票据
        self.assertEqual(folder_plan.rule_extensions({"extensions": [".PDF", ".csv", ".exe"]}), (".pdf", ".csv"))
        self.assertEqual(folder_plan.rule_extensions({"extensions": "bad"}), folder_plan.EXTENSIONS)

    def test_helper_scans_and_counts_by_each_tasks_extensions(self):
        import tempfile

        with tempfile.TemporaryDirectory() as root:
            base = pathlib.Path(root)
            (base / "Sales").mkdir()
            for name in ("Sales/a.pdf", "Sales/pos.xlsx", "Sales/pos.csv", "Sales/old.xls", "Sales/notes.txt"):
                (base / name).write_bytes(b"x")
            sales = folder_plan.rule_extensions({"extensions": list(contracts.extensions_for("sales", "express"))})
            self.assertEqual(sorted(c.relpath for c in folder_plan.scan(base / "Sales")), ["a.pdf"])
            self.assertEqual(sorted(c.relpath for c in folder_plan.scan(base / "Sales", extensions=sales)),
                             ["a.pdf", "old.xls", "pos.csv", "pos.xlsx"])
            seen, complete = folder_plan.survey(base, deadline=float("inf"), budget=[1000])
            self.assertTrue(complete)
            self.assertEqual(folder_plan.folder_counts(seen, ["", "Sales"]), {"": 1, "Sales": 1})
            self.assertEqual(folder_plan.folder_counts(seen, ["", "Sales"], folder_plan.TABLE_EXTENSIONS),
                             {"": 3, "Sales": 3})
            never = lambda relpath, size, mtime: False  # noqa: E731
            pending = folder_plan.pending_counts(seen, [("S", "Sales", never, set(), sales), ("P", "Sales", never, set())])
            self.assertEqual(pending, {"S": 4, "P": 1})

    def test_old_tasks_take_new_extensions_only_after_baselining_them(self):
        sales = list(contracts.extensions_for("sales", "express"))
        self.assertEqual(folder_plan.baselined_extensions({}), folder_plan.EXTENSIONS)  # 1.5 之前拍的底
        self.assertEqual(folder_plan.taken_extensions({"extensions": sales}, {}), folder_plan.EXTENSIONS)
        self.assertEqual(folder_plan.taken_extensions({"extensions": sales}, {"baseline_exts": sales}),
                         folder_plan.rule_extensions({"extensions": sales}))
        # 新任务：本机已经收着的（seen_exts）才扫；1.5 之前就见过的新任务那时只收 PDF / 图片，后来多收的先补拍底
        self.assertEqual(folder_plan.taken_extensions({"extensions": sales, "existing": 1}, {}), folder_plan.EXTENSIONS)
        self.assertEqual(folder_plan.taken_extensions({"extensions": sales, "existing": 1}, {"seen_exts": sales}),
                         folder_plan.rule_extensions({"extensions": sales}))

    def test_the_pump_builds_days_before_recognition_and_keeps_tables_out_of_it(self):
        source = (APP / "folder_intake/pump.py").read_text()
        steps = re.search(r"for step in \(([^)]*)\)", source).group(1)
        self.assertLess(steps.index("_pos_step"), steps.index("_submit_step"))
        self.assertIn('_due_kind("source_ready", room, False)', source)
        self.assertIn('_due_kind("source_ready", 20, True)', source)
        self.assertNotIn('_due("source_ready"', source)


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

    def test_remind_times_are_checked_sorted_and_default_when_empty(self):
        """2026-10-13（用户：哪天、什么时间都可以自定义）：最多 3 个「运行日前 0–7 天 + HH:MM」，去重后按离运行由远到近排。"""
        default = [{"days_before": 1, "time": "08:00"}, {"days_before": 1, "time": "14:00"}]
        for empty in (None, "", "  ", [], "[]"):
            self.assertEqual(contracts.remind_times(empty), default, empty)
        self.assertEqual(contracts.DEFAULT_REMIND_TIMES, default)
        contracts.remind_times(None)[0]["time"] = "09:00"  # 拿到的是副本，改了不动默认值
        self.assertEqual(contracts.DEFAULT_REMIND_TIMES, default)
        given = [{"days_before": 0, "time": "07:05"}, {"days_before": 3, "time": "16:45"},
                 {"days_before": "0", "time": " 06:59 "}]
        expected = [{"days_before": 3, "time": "16:45"}, {"days_before": 0, "time": "06:59"},
                    {"days_before": 0, "time": "07:05"}]
        self.assertEqual(contracts.remind_times(given), expected)
        self.assertEqual(contracts.remind_times(json.dumps(given)), expected)  # 库里存的 JSON
        # 去重：同一天同一时间只算一个（3 个里去掉重复的也行）
        self.assertEqual(contracts.remind_times([{"days_before": 7, "time": "23:59"}, {"days_before": 7, "time": "23:59"},
                                                 {"days_before": 0, "time": "00:00"}]),
                         [{"days_before": 7, "time": "23:59"}, {"days_before": 0, "time": "00:00"}])
        bad = [{"days_before": 8, "time": "08:00"}, {"days_before": -1, "time": "08:00"},
               {"days_before": True, "time": "08:00"}, {"days_before": 1.0, "time": "08:00"},
               {"days_before": 1, "time": "8:00"}, {"days_before": 1, "time": "24:00"},
               {"days_before": 1, "time": "08:60"}, {"days_before": 1, "time": "08:00:00"}, {"days_before": 1},
               {"time": "08:00"}, "08:00", None]
        for item in bad:
            with self.assertRaises(contracts.ContractError, msg=item) as caught:
                contracts.remind_times([item])
            self.assertEqual(caught.exception.code, "cowork.folder.invalid")
        for value in ([{"days_before": day, "time": "08:00"} for day in range(4)], {"days_before": 1, "time": "08:00"},
                      "not json", "{}", 3):
            with self.assertRaises(contracts.ContractError, msg=value):
                contracts.remind_times(value)
        # 标签：前一天整点沿用原来的「08」「14」（上线前已发的那条不再发一次），其余带天数与时间
        self.assertEqual([contracts.remind_label(item) for item in default], ["08", "14"])
        self.assertEqual([contracts.remind_label(item) for item in expected], ["3d16:45", "0d06:59", "0d07:05"])
        self.assertEqual(contracts.remind_label({"days_before": 1, "time": "08:30"}), "1d08:30")

    def test_a_same_day_reminder_must_be_earlier_than_the_run_time(self):
        """当天的提醒不早于运行时间就永远发不出：给了运行时间就拒（`cowork.folder.remind_same_day`），不静默存下；
        前几天的提醒任何时间都行；不给运行时间（发提醒时读库）不查。"""
        same_day = [{"days_before": 0, "time": "21:59"}, {"days_before": 1, "time": "23:30"}]
        self.assertEqual(contracts.remind_times(same_day, "22:00"),
                         [{"days_before": 1, "time": "23:30"}, {"days_before": 0, "time": "21:59"}])
        self.assertEqual(len(contracts.remind_times(same_day, "22:00:00")), 2)  # 库里可能带秒
        for late in ("22:00", "22:01", "23:59"):
            with self.assertRaises(contracts.ContractError) as caught:
                contracts.remind_times([{"days_before": 0, "time": late}], "22:00")
            self.assertEqual(caught.exception.code, "cowork.folder.remind_same_day")
        with self.assertRaises(contracts.ContractError):
            contracts.remind_times([{"days_before": 0, "time": "00:00"}], "00:00")  # 运行在 00:00：当天没有更早的
        self.assertEqual(len(contracts.remind_times([{"days_before": 0, "time": "23:00"}])), 1)
        self.assertEqual(len(contracts.remind_times([{"days_before": 0, "time": "23:00"}], "")), 1)
        self.assertEqual(contracts.remind_times(None, "00:00"), contracts.DEFAULT_REMIND_TIMES)

    def test_reminder_windows_for_chosen_times(self):
        def slot(*now, times, since=0.0, day=14, at="22:00"):
            hit = contracts.reminder_slot(self._at(*now), day, at, since, times)
            return hit and (self._local(hit[0]), hit[1], self._local(hit[2]))

        run = (2026, 10, 14, 22, 0)
        times = [{"days_before": 3, "time": "16:45"}, {"days_before": 0, "time": "07:05"},
                 {"days_before": 0, "time": "23:00"}]  # 当天 23:00 比运行（22:00）晚：不发
        self.assertIsNone(slot(2026, 10, 11, 16, 44, times=times))
        self.assertEqual(slot(2026, 10, 11, 16, 45, times=times), (run, "3d16:45", (2026, 10, 11, 16, 45)))
        self.assertEqual(slot(2026, 10, 14, 7, 4, times=times)[1], "3d16:45")  # 一直到下一个提醒时刻
        self.assertEqual(slot(2026, 10, 14, 7, 5, times=times), (run, "0d07:05", (2026, 10, 14, 7, 5)))
        self.assertEqual(slot(2026, 10, 14, 21, 59, times=times)[1], "0d07:05")  # 最后一个到运行时刻
        self.assertIsNone(slot(2026, 10, 14, 22, 0, times=times))
        self.assertIsNone(slot(2026, 10, 14, 23, 30, times=times))
        # 只有一个落在运行之后的提醒：什么都不发；与运行同一时刻的也不发
        for late in ([{"days_before": 0, "time": "23:00"}], [{"days_before": 0, "time": "22:00"}]):
            for now in ((2026, 10, 14, 21, 0), (2026, 10, 14, 22, 30), (2026, 10, 14, 23, 30)):
                self.assertIsNone(slot(*now, times=late), (late, now))
        # 打开提醒（改提醒时刻）晚于 3 天前那条：那条不补，当天 07:05 那条照发
        since = self._at(2026, 10, 12, 9, 0)
        self.assertIsNone(slot(2026, 10, 12, 10, 0, times=times, since=since))
        self.assertEqual(slot(2026, 10, 14, 7, 10, times=times, since=since)[1], "0d07:05")
        # 跨月、跨年：1 日 00:30 的任务，前 7 天是上一年 12-25；当天 00:10 在运行之前
        times = [{"days_before": 7, "time": "09:00"}, {"days_before": 0, "time": "00:10"}]
        self.assertIsNone(slot(2026, 12, 25, 8, 59, times=times, day=1, at="00:30"))
        self.assertEqual(slot(2026, 12, 25, 9, 0, times=times, day=1, at="00:30"),
                         ((2027, 1, 1, 0, 30), "7d09:00", (2026, 12, 25, 9, 0)))
        self.assertEqual(slot(2027, 1, 1, 0, 9, times=times, day=1, at="00:30")[1], "7d09:00")
        self.assertEqual(slot(2027, 1, 1, 0, 20, times=times, day=1, at="00:30"),
                         ((2027, 1, 1, 0, 30), "0d00:10", (2027, 1, 1, 0, 10)))
        # 月末任务（run_day 0）：2026 年 2 月末是 28 日，前 2 天是 2-26；3 月 1 日前 1 天是 2-28
        times = [{"days_before": 2, "time": "10:00"}]
        self.assertEqual(slot(2026, 2, 26, 10, 0, times=times, day=0, at="22:00"),
                         ((2026, 2, 28, 22, 0), "2d10:00", (2026, 2, 26, 10, 0)))
        self.assertEqual(slot(2026, 2, 28, 10, 0, times=[{"days_before": 1, "time": "10:00"}], day=1, at="08:00"),
                         ((2026, 3, 1, 8, 0), "10", (2026, 2, 28, 10, 0)))  # 前一天整点：标签同原写法
        # 默认那组与原来逐位一致（老任务 remind_times 为空）
        self.assertEqual(slot(2026, 10, 13, 8, 0, times=None), (run, "08", (2026, 10, 13, 8, 0)))
        self.assertEqual(slot(2026, 10, 13, 14, 0, times=""), (run, "14", (2026, 10, 13, 14, 0)))
        with self.assertRaises(contracts.ContractError):
            slot(2026, 10, 13, 8, 0, times="[{}]")

    def test_the_run_day_word_and_date(self):
        run = self._at(2026, 10, 15, 19, 0)
        self.assertEqual(contracts.day_word(self._at(2026, 10, 15, 7, 0), run), "today")
        self.assertEqual(contracts.day_word(self._at(2026, 10, 14, 23, 59), run), "tomorrow")
        self.assertEqual(contracts.day_word(self._at(2026, 10, 13, 9, 0), run), "later")
        self.assertEqual(contracts.day_word(self._at(2026, 10, 8, 9, 0), run), "later")
        self.assertEqual(contracts.month_day(run), (10, 15))
        self.assertEqual(contracts.month_day(self._at(2027, 1, 1, 0, 30)), (1, 1))

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
        # 等电脑读科目表的重试按推送给的间隔（一分钟），离线照旧十分钟
        self.assertIn('_later(entry, result.get("seconds") or OFFLINE_SECONDS, result["reason"])',
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

    def test_tasks_following_one_company_share_a_lane_whatever_their_saved_year(self):
        """T1：跟随公司当年账套的任务按（推送连接，公司 + 分店）排先后与限流：进项任务存的是 2568、销项存的是 2569 也同一条道；
        别的分店、固定账套的任务不在这条道上。"""
        company = {"company": "CO-1", "company_branch": "00000"}
        sale = self._sale(**company)
        for purchase, waits in ((self._purchase(account_set_key="S:\\2568\\EXP68\\68SINCER", **company), True),
                                (self._purchase(company="CO-1", company_branch="00012"), False),
                                (self._purchase(), False)):
            pump, entry, calls = self._pump(sale, purchases=[purchase])
            self._step(pump)
            self.assertEqual(len(calls["later"]), int(waits), purchase)
        self.assertEqual(pump._lane("C1", self.KEY, "CO-1", "00000"), ("C1", "company:CO-1|00000"))
        self.assertEqual(pump._lane("C1", "s:/2569/exp69/69sincer/", "", ""), ("C1", identity.normalize_express_account_key(self.KEY)))

    def test_a_purchase_run_just_requested_holds_the_sale(self):
        run = {"connection": "C1", "account_set_key": self.KEY, "run_request_at": self.NOW - timedelta(minutes=5),
               "last_reported_at": None}
        pump, entry, calls = self._pump(self._sale(), runs=[run])
        self._step(pump)
        self.assertEqual(len(calls["later"]), 1)


class PushRefreshesTheFile(unittest.TestCase):
    """票的推送状态一变（后台、网页、手机、CC 任何入口推的），定时任务那份文件在提交后马上刷新结果与结果表格版本，
    不等每 10 分钟的跟进（2026-10-13 测试机：CC 重推三天 POS，两份刷新了，一份的结果表格与运行结果停在重推之前）。"""
    NOW = datetime(2026, 10, 13, 9, 0)

    def _pump(self, entries, signatures):
        calls = {"write": [], "later": []}
        stub = types.SimpleNamespace
        error = type("Error", (Exception,), {})
        frappe = stub(db=stub(commit=lambda: None, rollback=lambda: None), QueryDeadlockError=error,
                      DoesNotExistError=error, clear_messages=lambda: None, log_error=lambda **kw: None,
                      get_traceback=lambda: "")
        modules = {"frappe": frappe,
                   "frappe.utils": stub(add_to_date=lambda value, **delta: value + timedelta(**delta),
                                        now_datetime=lambda: self.NOW),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": stub(contracts=contracts, destinations=stub(), inbox=stub(),
                                                        settings=stub()),
                   "pearnly_cowork.folder_intake.access": stub(FILE="F", RULE="R", RUN="U", read=None, write=None),
                   "pearnly_cowork.folder_intake.machine": stub(owner_ready=None, request_key=None),
                   "pearnly_cowork.folder_intake.push": stub(push_one=None),
                   "pearnly_cowork.targets.identity": identity}
        with mock.patch.dict(sys.modules, modules):
            pump = _load("folder_pump_refresh_under_test", APP / "folder_intake/pump.py")
        pump.read = lambda doctype, name, for_update=False: entries[name]
        pump.write = lambda entry: calls["write"].append(entry.name)
        pump._signature = lambda entry: signatures[entry.name]
        pump._later = lambda entry, seconds, code: calls["later"].append((entry.name, seconds))
        return pump, calls

    @staticmethod
    def _file(name, state, outcome, revision=1, next_at=None):
        return types.SimpleNamespace(name=name, state=state, outcome_json=json.dumps(outcome),
                                     workbook_revision=revision, next_attempt_at=next_at, finished_at=None)

    def test_a_push_from_cc_refreshes_the_result_and_the_workbook_at_once(self):
        held = {"H1": {"status": "held", "reason": "choice_needed:receipt"},
                "_signature": [["H1", "", "choice_needed:receipt"]]}
        parked = {"H2": {"status": "sent"}, "_signature": [["H2", "success", ""]]}
        entries = {"F1": self._file("F1", "done", held, 2, self.NOW + timedelta(minutes=8)),
                   # 之前全部成功、停止定时跟进（31 天后才看）的文件
                   "F2": self._file("F2", "done", parked, 1, self.NOW + timedelta(days=31)),
                   "F3": self._file("F3", "pushing", {"H3": {"status": "sent"}}),
                   "F4": self._file("F4", "recognized", {}),
                   "F5": self._file("F5", "pushing", {"H5": {"status": "sent"}})}
        signatures = {"F1": [["H1", "success", ""]], "F2": [["H2", "manual", "push.native_target_mismatch"]],
                      "F3": [["H3", "success", ""]], "F5": [["H5", "pending", ""]]}
        pump, calls = self._pump(entries, signatures)
        before = contracts.tally([{"state": "done", "outcome_json": entries["F1"].outcome_json}], self.NOW)
        self.assertEqual((before["pushed"], before["stopped"]), (0, 1))
        pump.refresh(["F1", "F2", "F3", "F4", "F5", "F1", None])
        first = entries["F1"]
        self.assertEqual((first.workbook_revision, json.loads(first.outcome_json)["_signature"], first.next_attempt_at),
                         (3, [["H1", "success", ""]], self.NOW + timedelta(minutes=10)))
        after = contracts.tally([{"state": "done", "outcome_json": first.outcome_json}], self.NOW)
        self.assertEqual((after["pushed"], after["stopped"]), (1, 0))  # 运行结果、任务列表跟着变
        self.assertEqual((entries["F2"].workbook_revision, entries["F2"].next_attempt_at),
                         (2, self.NOW + timedelta(minutes=10)))  # 停止跟进的也重算，并重新跟进
        self.assertEqual((entries["F3"].state, entries["F3"].workbook_revision, entries["F3"].finished_at),
                         ("done", 2, self.NOW))  # 推送中的写完了：马上完成，不等下一分钟
        self.assertEqual(entries["F4"].workbook_revision, 1)  # 还在等后台推的文件不动
        self.assertEqual((entries["F5"].state, calls["later"]), ("pushing", [("F5", 30)]))  # 还在路上：照旧等
        self.assertEqual(calls["write"], ["F1", "F2", "F3"])

    def test_the_periodic_follow_still_stops_once_everything_is_pushed(self):
        parked = {"H2": {"status": "sent"}, "_signature": [["H2", "success", ""]]}
        entries = {"F2": self._file("F2", "done", parked)}
        pump, calls = self._pump(entries, {})
        pump._follow(entries["F2"])
        self.assertEqual((entries["F2"].workbook_revision, entries["F2"].next_attempt_at),
                         (1, self.NOW + timedelta(days=pump.FOLLOW_DAYS + 1)))

    def _events(self, rows, sources):
        seen = {"enqueue": [], "filters": []}

        def get_all(doctype, filters=None, fields=None, ignore_permissions=False):
            seen["filters"].append((doctype, filters, ignore_permissions))
            return [Row(row) for row in rows if row["source_file"] == filters["source_file"]]

        frappe = types.SimpleNamespace(
            db=types.SimpleNamespace(get_value=lambda doctype, name, field: sources.get(name)), get_all=get_all,
            enqueue=lambda method, **kwargs: seen["enqueue"].append((method, kwargs)))
        events = _load("folder_events_under_test", APP / "folder_intake/events.py")
        return events, frappe, seen

    def test_only_folder_task_tickets_queue_a_refresh_after_commit(self):
        rows = [{"name": "F1", "source_file": "S1", "histories_json": json.dumps(["H1", "H2"])},
                {"name": "F9", "source_file": "S1", "histories_json": json.dumps(["H7"])}]
        events, frappe, seen = self._events(rows, {"H1": "S1", "H8": "", "H7": "S1", "H6": "S6"})
        with mock.patch.dict(sys.modules, {"frappe": frappe}):
            events.pushed("H1")
            events.pushed("H8")  # 网页上传的票：没有原件对应的定时任务
            events.pushed("H6")
        self.assertEqual(seen["enqueue"], [("pearnly_cowork.folder_intake.pump.refresh",
                                            {"queue": "default", "enqueue_after_commit": True, "files": ["F1"]})])
        self.assertEqual(seen["filters"][0], ("Cowork Folder Intake File",
                                              {"source_file": "S1", "state": ["in", ("pushing", "done")]}, True))

    def _push_state(self, hooks=("pearnly_cowork.folder_intake.events.pushed",), hook=None, previous=None):
        announced, logged = [], []
        local = types.SimpleNamespace(lang="en")
        translate = types.SimpleNamespace(get_all_translations=lambda lang: {})

        def get_hooks(name):
            if isinstance(hooks, Exception):
                raise hooks
            return list(hooks) if name == "cowork_push_state_changed" else []

        before = previous or {"workspace": "W", "client": "", "last_push_record": "R1", "last_push_status": "pending"}
        db = types.SimpleNamespace(sql=lambda *args, **kwargs: [Row(before)],
                                   set_value=lambda *args, **kwargs: None, get_value=lambda *args: "owner@x")
        frappe = types.SimpleNamespace(db=db, local=local, translate=translate, _=lambda text: text,
                                       session=types.SimpleNamespace(user="pusher@x"),
                                       publish_realtime=lambda *args, **kwargs: None, get_hooks=get_hooks,
                                       get_attr=lambda path: hook or announced.append,
                                       log_error=lambda **kwargs: logged.append(kwargs["title"]),
                                       get_traceback=lambda: "", PermissionError=PermissionError,
                                       throw=lambda *args: None)
        modules = {"frappe": frappe, "frappe.translate": translate}
        with mock.patch.dict(sys.modules, modules):
            push_state = _load("push_state_announce_under_test", APP / "history/push_state.py")
        return push_state, modules, announced, logged

    def test_a_stop_records_where_the_document_was_going(self):
        """验收 F17：CC 对话、定时任务推之前停下时，这次要推往的连接与账套一并抄到票上；没给就只记原因。"""
        push_state, modules, announced, logged = self._push_state()
        written = []
        modules["frappe"].db.set_value = lambda doctype, name, values, **kwargs: written.append((name, values))
        with mock.patch.dict(sys.modules, modules):
            push_state.record_failure("H3", "choice_needed:payment,account", connection="C1", account_set="c:\\70exp\\t9niexcp")
            push_state.record_failure("H4", "target offline")
            push_state.record_failure("H5", "choice_needed:account", connection="C1", account_set="")
        self.assertEqual(written, [
            ("H3", {"last_push_reason": "choice_needed:payment,account", "push_connection": "C1",
                    "push_account_set": "c:\\70exp\\t9niexcp"}),
            ("H4", {"last_push_reason": "target offline"}),
            ("H5", {"last_push_reason": "choice_needed:account"})])

    def test_every_push_state_change_is_announced_and_never_breaks_the_push(self):
        row = Row(name="R1", history="H1", status="success", workspace="W", client="", reason="")
        push_state, modules, announced, logged = self._push_state()
        with mock.patch.dict(sys.modules, modules):
            push_state.mirror(row, "2026-10-13")
            push_state.record_failure("H2", "choice_needed:receipt")
        self.assertEqual(announced, ["H1", "H2"])
        push_state, modules, announced, logged = self._push_state(
            previous={"workspace": "W", "client": "", "last_push_record": "R1", "last_push_status": "success"})
        with mock.patch.dict(sys.modules, modules):
            push_state.mirror(row, "2026-10-13")  # 状态没变：不刷新
        self.assertEqual(announced, [])

        def broken(history):
            raise RuntimeError("queue down")

        push_state, modules, announced, logged = self._push_state(hook=broken)
        with mock.patch.dict(sys.modules, modules):
            push_state.mirror(row, "2026-10-13")
        self.assertEqual(logged, ["Cowork push state hook failed"])
        push_state, modules, announced, logged = self._push_state(hooks=RuntimeError("no site"))
        with mock.patch.dict(sys.modules, modules):
            push_state.mirror(row, "2026-10-13")
        self.assertEqual((announced, logged), ([], []))


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

    def _notify(self, notices, local=None, hooks=None):
        local = local or types.SimpleNamespace(lang="en")
        hooks = hooks or {}
        errors = []
        frappe = types.SimpleNamespace(local=local, get_hooks=lambda name: list(hooks.get(name) or []),
                                       get_attr=lambda path: hooks[path], log_error=lambda title=None: errors.append(title))
        self.notify_errors = errors
        modules = {"frappe": frappe,
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
                                    "ttl": 24 * 3600, "view": ""})])
        self.assertEqual((notify.KIND_REMIND, notify.REMIND_TTL), ("cowork.folder.reminder", 6 * 3600))
        self.assertTrue(notify.sent("cowork.folder.remind:old"))
        self.assertFalse(notify.sent("cowork.folder.remind:new"))

    def test_a_notice_of_one_companys_tasks_opens_that_company(self):
        # ADR-020：提醒 / 简报的任务都属于同一家公司 → 没挂上对话时点开到这家的主档页；归不上 → 维持现状（定时任务页）
        posted, asked = [], []

        def post(user, kind, text, dedup_key, **kwargs):
            posted.append(kwargs["view"])
            return {"name": "n1", "id": "pnotice_n1", "session_id": "", "created": True}

        def company_of(user, rules):
            asked.append((user, rules))
            return "abc1234567" if rules == ["R1"] else ""

        notify = self._notify(types.SimpleNamespace(post=post), hooks={
            "cowork_rules_company": ["companies.of"], "companies.of": company_of})
        notify.send("owner@example.com", notify.KIND_REMIND, "T", "X", "k1", rules=["R1"])
        notify.send("owner@example.com", notify.KIND_REMIND, "T", "X", "k2", rules=["R1", "R2"])
        self.assertEqual(posted, ["company:abc1234567", ""])
        self.assertEqual(asked, [("owner@example.com", ["R1"]), ("owner@example.com", ["R1", "R2"])])

        def broken(user, rules):
            raise RuntimeError("catalog down")

        notify = self._notify(types.SimpleNamespace(post=post), hooks={
            "cowork_rules_company": ["companies.of"], "companies.of": broken})
        notify.send("owner@example.com", notify.KIND_BRIEF, "T", "X", "k3", rules=["R1"])
        self.assertEqual((posted[-1], self.notify_errors), ("", ["Cowork rules company failed"]))

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

    def _machine(self, rules, *, latest=None, notices=None, before=None, pending_run=None, manual_run=None,
                 roots_in_use=None):
        """``pending_run``：这条任务还没发简报的上一次非补传运行（补传并进它）；``manual_run``：同一负责人 10 分钟内
        开始、还没发简报的上一次立即运行（并进它那一批）。"""
        calls = {"remember": [], "changed": [], "for_rules": [], "written": [], "sql": [], "remember_roots": [],
                 "in_use": [], "adopt": []}
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

        agent = stub(read=lambda connection, reader_device="": {"pending": before or {}},
                     remember=lambda connection, **kw: calls["remember"].append(kw),
                     remember_roots=lambda connection, **kw: calls["remember_roots"].append(kw))
        # 读者 =（电脑，成员连接）（ADR-021 第 4 批）：这台小助手的密钥挂在电脑 PC1 上
        readers = stub(device_of=lambda context: "PC1",
                       of=lambda row: (row.get("reader_device") or "", row.get("reader") or row.get("connection")),
                       adopt=lambda connection, device, roots: calls["adopt"].append((connection, device, roots)))

        def in_use(reader, device=""):
            calls["in_use"].append((reader, device))
            if roots_in_use is Exception:
                raise RuntimeError("table missing")
            return roots_in_use or []
        modules = {"frappe": frappe,
                   "frappe.utils": stub(get_datetime=lambda value: value, get_system_timezone=lambda: "Asia/Bangkok",
                                        now_datetime=lambda: datetime(2026, 10, 12, 20, 0)),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.access": stub(scope=stub()),
                   "pearnly_cowork.companion": stub(http_auth=stub(), release=stub(latest=lambda: latest)),
                   "pearnly_cowork.connections": stub(access=stub(), desktop_update=desktop_update),
                   "pearnly_cowork.folder_intake": stub(
                       agent=agent, readers=readers, contracts=contracts, inbox=stub(), settings=stub(), workbook=stub(),
                       live=stub(changed=lambda rule, *users: calls["changed"].append(rule)),
                       notify=stub(for_rules=for_rules),
                       company_folders=stub(roots_in_use=lambda reader, device="": in_use(reader, device))),
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
                "active_since": None, "run_request": 0, "reader": "C1", "reader_device": "PC1", "connection": "C1",
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
        # 多台电脑（ADR-021 第 4 批）：只下发这台电脑这个成员读的任务；补丁认不出电脑的任务由来同步的这台接手；
        # 本机情况按（电脑，成员连接）记
        query, values = next(item for item in calls["sql"] if "reader_device = %s" in item[0])
        self.assertIn("IFNULL(NULLIF(reader, ''), connection) = %s AND reader_device = %s", query)
        self.assertEqual(values, ("C1", "PC1"))
        self.assertEqual(calls["adopt"], [("C1", "PC1", [])])  # 这台这回没报根目录：没有可接手的
        machine._sync(types.SimpleNamespace(connection="C1"), {"roots": [{"id": "R7", "label": "D:\\Bills"}]})
        self.assertEqual(calls["adopt"][-1], ("C1", "PC1", ["R7"]))
        self.assertEqual(calls["remember"][0]["reader_device"], "PC1")
        self.assertNotIn("notices", reply)  # 旧小助手不带 notices_after：不查
        self.assertEqual(calls["for_rules"], [])

    def test_the_sync_keeps_what_the_computer_says_about_a_blocked_update(self):
        # 教程问题第 1 条：只读文件夹的 MR.ERP 成员没有心跳，「自动更新被 Windows 拦下」随文件夹同步报（规整成 UTC）；
        # 没被拦（null）、旧版不带、带的不合形状都存 None
        machine, calls = self._machine([self._rule("P1")])
        context = types.SimpleNamespace(connection="C1")
        machine._sync(context, {"update_blocked": {"version": "1.5.2", "at": "2026-10-13T18:18:00+07:00"}})
        machine._sync(context, {"update_blocked": None})
        machine._sync(context, {})
        machine._sync(context, {"update_blocked": {"version": "1.5.2", "at": "yesterday"}})
        self.assertEqual([kw["update_blocked"] for kw in calls["remember"]],
                         [{"version": "1.5.2", "at": "2026-10-13T11:18:00+00:00"}, None, None, None])

    def test_sales_tasks_into_express_also_take_pos_exports(self):
        machine, _calls = self._machine([self._rule("S1", "sales"), self._rule("P1")])
        base = machine.frappe.db.get_value
        machine.frappe.db.get_value = lambda doctype, name, field=None, **kw: (
            "express" if field == "adapter" else base(doctype, name, field, **kw))
        reply = machine._sync(types.SimpleNamespace(connection="C1"), {})
        extensions = {rule["name"]: rule["extensions"] for rule in reply["rules"]}
        self.assertEqual(extensions, {"P1": list(contracts.EXTENSIONS),
                                      "S1": list(contracts.EXTENSIONS + contracts.TABLE_EXTENSIONS)})

    def test_an_old_excel_file_is_registered_as_failed_without_upload(self):
        machine, calls = self._machine([self._rule("S1", "sales")])
        base = machine.frappe.db.get_value
        machine.frappe.db.get_value = lambda doctype, name, field=None, **kw: (
            "express" if field == "adapter" else base(doctype, name, field, **kw))
        machine.settings = types.SimpleNamespace(config=lambda: {"inline_max_mib": 8})
        uploads = []
        machine._upload_target = lambda rule, entry: uploads.append(entry) or {"direct": 0}
        reply = machine._begin(types.SimpleNamespace(connection="C1"),
                               {"rule": "S1", "relpath": "2026-10\\ยอดขาย.xls", "filename": "ยอดขาย.xls",
                                "sha256": "a" * 64, "byte_size": 2048})
        self.assertEqual((reply["state"], reply["code"]), ("failed", "pos.xls_not_supported"))
        self.assertEqual(uploads, [])
        self.assertEqual((calls["written"][-1]["state"], calls["written"][-1]["last_code"]),
                         ("failed", "pos.xls_not_supported"))
        # 进项任务不收 .xls（与 PDF / 图片以外的类型一样拒收）
        machine, _calls = self._machine([self._rule("P1")])
        machine.settings = types.SimpleNamespace(config=lambda: {"inline_max_mib": 8})
        with self.assertRaises(contracts.ContractError):
            machine._begin(types.SimpleNamespace(connection="C1"),
                           {"rule": "P1", "relpath": "a.xls", "filename": "a.xls", "sha256": "a" * 64, "byte_size": 9})

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
        machine._record_run(types.SimpleNamespace(connection="C1"), {**base, **row}, "PC1")
        return calls["written"][-1]

    def _naive(self, epoch):
        return datetime.fromtimestamp(epoch, ZoneInfo("Asia/Bangkok")).replace(tzinfo=None)

    def test_scheduled_runs_belong_to_their_slot(self):
        slot = self.NOW - 3 * 86400
        written = self._run({"slot": slot})
        # 别的电脑（同一条连接上的 PC2）报这条任务的运行：不收（这条任务归 PC1 读）
        machine, _calls = self._machine([self._rule("P1")])
        with self.assertRaises(contracts.ContractError):
            machine._record_run(types.SimpleNamespace(connection="C1"), {"rule": "P1", "run_key": "b" * 32}, "PC2")
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
                             "skipped": 0, "upload_failed": 0}, "PC1")
        written = calls["written"][-1]
        self.assertEqual(written["reader_device"], "PC1")  # 运行记录记下是哪台电脑跑的（简报按电脑分）
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
        # 2026-10-13：提醒时刻（JSON 列表，空 = 默认），只经任务服务写
        times = self._field(rule, "remind_times")
        self.assertEqual((times["fieldtype"], times["read_only"]), ("Small Text", 1))
        run = "cowork_folder_intake_run"
        state = self._field(run, "brief_state")
        self.assertEqual((state["options"], state["search_index"]), ("pending\nsent\nskipped", 1))
        self.assertEqual(self._field(run, "run_owner")["options"], "User")
        group = self._field(run, "brief_group")  # 2026-10-13：简报分组（定时时刻 / 立即运行那一批 / 补传跟父运行）
        self.assertEqual((group["fieldtype"], group["read_only"], group["search_index"]), ("Data", 1, 1))
        self.assertEqual(self._field("cowork_folder_intake_file", "source_file")["search_index"], 1)
        # 读文件夹的电脑（ADR-021 第 4 批）：规则、文件、运行都记，只经本域写；规则按它查（每台最多 50 个、同步按它下发）
        for name in (rule, run, "cowork_folder_intake_file"):
            device = self._field(name, "reader_device")
            self.assertEqual((device["fieldtype"], device["read_only"], device["length"]), ("Data", 1, 140))
        self.assertEqual(self._field(rule, "reader_device")["search_index"], 1)
        # 改了字段就升 modified，migrate 才同步（2026-10-16：多台电脑第 4 批加读文件夹的电脑；
        # 2026-10-17：T1 任务加公司主档与分店；2026-10-19：存量文案 C2 删 DocType 说明）
        for name, modified in ((rule, "2026-10-19 12:00"), (run, "2026-10-19 12:00"),
                               ("cowork_folder_intake_file", "2026-10-19 12:00")):
            doctype = self._doctype(name)
            self.assertEqual(doctype["modified"], modified + ":00.000000")
            self.assertEqual(doctype["field_order"], [field["fieldname"] for field in doctype["fields"]])

    def test_history_runs_get_no_brief(self):
        self.assertIn("pearnly_cowork.patches.v0_0_16_folder_run_briefs", (APP / "patches.txt").read_text())
        patch = (APP / "patches/v0_0_16_folder_run_briefs.py").read_text()
        self.assertIn('if not frappe.db.has_column(RUN, "brief_state"):', patch)
        self.assertIn("SET brief_state = 'skipped' WHERE IFNULL(brief_state, '') IN ('', 'pending')", patch)

    def test_rules_and_sync_follow_the_agreement(self):
        rules = (APP / "folder_intake/rules.py").read_text()
        # 换没换文件夹不分大小写（Windows 上是同一个文件夹）；接续停用任务的只收此后放进来的
        self.assertIn('place = (readers.of(rule), rule.root_id, (rule.folder or "").casefold()) if name else None', rules)
        self.assertIn("if not name or place != (pair, root, folder.casefold()):", rules)
        self.assertIn("rule.include_existing = 0 if continued else 1", rules)
        self.assertIn("def save(values, *, cc_session=None):", rules)
        self.assertIn("def save_many(items, *, cc_session=None):", rules)
        self.assertIn("def set_enabled(name, enabled=None, *, remind=None, remind_times=None, cc_session=None):", rules)
        self.assertNotIn('values.get("cc_session")', rules)  # 对话编号只从参数取，网页报文传不进来
        self.assertEqual(ast.literal_eval(rules.split("BATCH_MAX = ", 1)[1].split()[0]), 20)
        api = (APP / "folder_intake/api.py").read_text()
        self.assertIn("def set_enabled(name, enabled):\n    return _retried(rules.set_enabled, name, enabled)", api)
        machine = (APP / "folder_intake/machine.py").read_text()
        self.assertIn('"existing": int(rule.include_existing or 0),', machine)
        self.assertIn('pending = contracts.pending(body.get("rule_pending"))', machine)

    def test_web_writes_retry_a_deadlock_with_the_heartbeat(self):
        """第 7 批 B7 收尾（10-18 lab）：删除定时任务第一次回 500 QueryDeadlockError（与电脑心跳 / 同步并发），再点才删掉。
        保存、启停、删除与读选项一样：回滚后整次重来，最多 3 次，第 3 次仍死锁才报出去。"""
        class Deadlock(Exception):
            pass

        rollbacks = []
        frappe = types.SimpleNamespace(QueryDeadlockError=Deadlock, whitelist=lambda **kw: (lambda f: f),
                                       db=types.SimpleNamespace(rollback=lambda: rollbacks.append(1)))
        calls = {"n": 0}

        def flaky(names, fail=1):
            calls["n"] += 1
            if calls["n"] <= fail:
                raise Deadlock()
            return {"deleted": 1}

        rules = types.SimpleNamespace(delete=flaky, save=None, set_enabled=None, options=None)
        package = types.SimpleNamespace(rules=rules)
        with mock.patch.dict(sys.modules, {"frappe": frappe, "pearnly_cowork.folder_intake": package,
                                           "pearnly_cowork.folder_intake.rules": rules}):
            spec = importlib.util.spec_from_file_location("folder_api_under_test", APP / "folder_intake/api.py")
            api = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(api)
            self.assertEqual(api.delete('["R1"]'), {"deleted": 1})
            self.assertEqual((calls["n"], len(rollbacks)), (2, 1))
            calls["n"] = 0
            rules.delete = lambda names: flaky(names, fail=3)
            with self.assertRaises(Deadlock):
                api.delete('["R1"]')
            self.assertEqual(calls["n"], 3)

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
        # 2026-10-13 加 remind.date（两天及以后写日期）与 POS 导出停着等认列（brief.why.posLayout）；
        # 数字会计员第 3 期加 brief.why.review（自动推前判成提议或要人核对：待推送）
        self.assertEqual(len(new), 32)
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
        # 两天及以后写日期：月、日、时间三个占位符
        self.assertEqual(rows["cowork.folder.remind.date"]["zh"], "{0} 月 {1} 日 {2}")

    def test_chosen_reminder_times_in_the_forms_and_terms(self):
        """2026-10-13（用户：哪天、什么时间都可以自定义）：Desk 弹窗勾上「提醒」才摆三行「提醒日｜提醒时间」（原生 Select 与
        Time 控件，存 HH:MM），保存时带 remind_times；词条四种语言齐全、占位符一致、不写 Express。"""
        import re

        source = (APP / "public/desk/intake-folders.js").read_text()
        self.assertIn("const REMIND_MAX = 3;", source)
        self.assertIn('{fieldtype: "Section Break", hide_border: 1, depends_on: "eval:doc.remind"},', source)
        self.assertIn('{fieldtype: "Time", fieldname: `remind_time_${i}`', source)
        self.assertIn("...remind_fields(rule?.remind_times?.length ? rule.remind_times : DEFAULT_REMIND),", source)
        self.assertIn("remind: values.remind ? 1 : 0, ...(values.remind ? {remind_times} : {})", source)
        hhmm = source.split("const hhmm = value => {", 1)[1].split("};", 1)[0]
        self.assertIn("/^(\\d{1,2}):(\\d{2})/", hhmm)
        self.assertIn('padStart(2, "0")', hhmm)
        with (APP / "i18n/messages.tsv").open(encoding="utf-8") as handle:
            rows = {row["key"]: row for row in csv.DictReader(handle, delimiter="\t") if row.get("key")}
        keys = ("cowork.folder.field.remind", "cowork.folder.field.remindDay", "cowork.folder.field.remindTime",
                "cowork.folder.remind.sameDay", "cowork.folder.remind.dayBefore", "cowork.folder.remind.daysBefore",
                "cowork.folder.remind.add", "cowork.folder.remind.date", "cowork.folder.remind_same_day")
        for key in keys:
            placeholders = set(re.findall(r"\{\d\}", rows[key]["en"]))
            for lang in ("en", "zh", "th", "ja"):
                self.assertTrue(rows[key][lang].strip(), (key, lang))
                self.assertEqual(set(re.findall(r"\{\d\}", rows[key][lang])), placeholders, (key, lang))
                self.assertNotIn("express", rows[key][lang].casefold(), (key, lang))
        # 提醒不再只在前一天：开关的叫法不带「前一天」
        self.assertEqual(rows["cowork.folder.field.remind"]["zh"], "提醒")
        self.assertEqual((rows["cowork.folder.remind.sameDay"]["zh"], rows["cowork.folder.remind.daysBefore"]["zh"]),
                         ("当天", "前 {0} 天"))
        self.assertEqual(rows["cowork.folder.remind_same_day"]["zh"], "当天提醒要早于运行时间")
        # 空行的提醒日按同一顺序取还没有的（同 CC 任务页「添加时间」），时间空着不存；不拿「当天」当默认
        self.assertIn('const REMIND_NEXT = [...DEFAULT_REMIND, {days_before: 2, time: "08:00"}, {days_before: 1, time: "18:00"}];',
                      source)
        self.assertIn("while (rows.length < REMIND_MAX) rows.push({...next_remind(rows), spare: true});", source)
        self.assertIn('default: item.spare ? "" : `${item.time}:00`},', source)
        self.assertEqual(rows["cowork.folder.remind.one"]["zh"], "「{0}」{1} 开始（{2}）。请将资料放入 {3}，确保资料齐全。")


class CompanyFolderSync(unittest.TestCase):
    """公司主档的文件夹（ADR-020）：一台电脑最多 200 个根目录、文件夹名合计 2 万个，多的只丢多余的；快速同步
    `roots_only` 只记根目录；同步回复带 `max_roots` 与 `roots_in_use`（读它的任务，停用的也算；任何公司的文件夹）。"""

    def _roots(self, count, folders=()):
        return [{"id": f"R{index}", "label": f"D:\\c{index}", "folders": list(folders)} for index in range(count)]

    def test_up_to_200_roots_and_the_rest_are_dropped_not_the_whole_sync(self):
        self.assertEqual((contracts.MAX_ROOTS, contracts.FOLDERS_TOTAL), (200, 20_000))
        self.assertEqual(len(contracts.roots(self._roots(200))), 200)
        kept = contracts.roots(self._roots(201))
        self.assertEqual((len(kept), kept[-1]["id"]), (200, "R199"))
        # 坏的一项只丢这一项
        self.assertEqual([row["id"] for row in contracts.roots([{"id": "bad id"}, {"id": "R1", "label": "x"}])], ["R1"])

    def test_folder_names_are_capped_in_total_and_deep_ones_go_first(self):
        # 每个根目录最多 2000 个（`MAX_FOLDERS`），合计最多 2 万个：第 11 个根目录只剩根目录本身，不让整次同步失败
        roots = [{"id": f"R{index}", "label": "x", "folders": [f"f{index}_{n}" for n in range(2000)]}
                 for index in range(11)]
        kept = contracts.roots(roots)
        self.assertEqual(sum(len(row["folders"]) for row in kept), contracts.FOLDERS_TOTAL)
        self.assertEqual((len(kept), kept[-1]["folders"]), (11, []))
        # 一个根目录里放不下时先留浅的
        roots[10] = {"id": "R10", "label": "x", "folders": ["a\\b\\deep"] + [f"s{n}" for n in range(1999)]}
        roots[9] = {"id": "R9", "label": "x", "folders": [f"f9_{n}" for n in range(1500)]}
        last = contracts.roots(roots)[-1]
        self.assertEqual(len(last["folders"]), 500)
        self.assertNotIn("a\\b\\deep", last["folders"])

    def test_a_quick_sync_keeps_the_folders_it_did_not_send(self):
        quick = contracts.roots([{"id": "R1", "label": "x"}, {"id": "R2", "label": "y", "folders": ["a"]}],
                                keep_missing=True)
        self.assertNotIn("folders", quick[0])
        self.assertEqual(quick[1]["folders"], ["a"])
        self.assertEqual(contracts.roots([{"id": "R1", "label": "x"}])[0]["folders"], [])  # 完整同步照旧

        stored = {}
        store = types.SimpleNamespace(set_value=lambda key, value, expires_in_sec=None: stored.__setitem__(key, value),
                                      get_value=lambda key, use_local_cache=False: stored.get(key))
        modules = {"frappe": types.ModuleType("frappe"),
                   "frappe.utils": types.SimpleNamespace(now_datetime=lambda: datetime(2026, 10, 15, 9, 0)),
                   "pearnly_core": types.ModuleType("pearnly_core"),
                   "pearnly_core.execution": types.SimpleNamespace(state=types.SimpleNamespace(store=lambda: store))}
        with mock.patch.dict(sys.modules, modules):
            agent = _load("folder_agent_under_test", APP / "folder_intake/agent.py")
        agent.remember("C1", roots=[{"id": "R1", "label": "x", "folders": ["old"], "counts": {"": 3}}], version="1.5",
                       health={"T1": "ok"}, device="PC", pending={"T1": 2})
        agent.remember_roots("C1", roots=quick, version="1.6.0", device="PC2")
        state = agent.read("C1")
        self.assertEqual([row["folders"] for row in state["roots"]], [["old"], ["a"]])
        self.assertEqual(state["roots"][0]["counts"], {"": 3})
        self.assertEqual((state["version"], state["device"], state["pending"], state["health"]),
                         ("1.6.0", "PC2", {"T1": 2}, {"T1": "ok"}))

    def test_folder_states_are_worked_out_when_read(self):
        roots = [{"id": "R1", "label": "D:\\ABC", "folders": ["2569", "2569\\09", "a\\b\\c"]}]
        self.assertEqual(contracts.folder_state(False, roots, "R1", "", 0), "offline")
        self.assertEqual(contracts.folder_state(True, roots, "R9", "", 60), "waiting")
        self.assertEqual(contracts.folder_state(True, roots, "R9", "", 3600), "missing")
        self.assertEqual(contracts.folder_state(True, roots, "R1", "2570", 0), "folder_missing")
        self.assertEqual(contracts.folder_state(True, roots, "R1", "2569\\09", 0), "ok")
        self.assertEqual(contracts.folder_state(True, roots, "R1", "a\\b\\c\\d", 0), "ok")  # 比上报深：不算缺
        self.assertEqual(set(contracts.FOLDER_STATES), {"ok", "waiting", "offline", "missing", "folder_missing"})
        self.assertEqual(contracts.folder_label(roots, "R1", "2569", "stored"), "D:\\ABC\\2569")
        self.assertEqual(contracts.folder_label(roots, "R9", "x", "stored"), "stored")

    def _sync(self, roots_in_use=None, **body):
        machine, calls = MachineSync._machine(self, [MachineSync._rule("P1")], roots_in_use=roots_in_use)
        return machine._sync(types.SimpleNamespace(connection="C1"), body), calls

    NOW = MachineSync.NOW

    def test_the_quick_sync_only_records_roots_and_both_replies_carry_the_limits(self):
        reply, calls = self._sync(["R1"], roots_only=True, version="1.6.0", device="PC",
                                  roots=[{"id": "R1", "label": "x"}])
        self.assertEqual((reply["max_roots"], reply["roots_in_use"]), (200, ["R1"]))
        self.assertNotIn("rules", reply)
        self.assertEqual((calls["remember"], len(calls["remember_roots"])), ([], 1))
        self.assertNotIn("folders", calls["remember_roots"][0]["roots"][0])
        reply, calls = self._sync(["R1", "R2"], roots=[{"id": "R1", "label": "x", "folders": []}])
        self.assertEqual((reply["max_roots"], reply["roots_in_use"]), (200, ["R1", "R2"]))
        self.assertEqual([rule["name"] for rule in reply["rules"]], ["P1"])
        # 查不出还被用着的根目录：不给这个键（电脑不给就永不自动收回），同步照常
        reply, _calls = self._sync(Exception, roots=[])
        self.assertNotIn("roots_in_use", reply)
        self.assertEqual(reply["max_roots"], 200)


class ReaderDevicesPlumbing(unittest.TestCase):
    """读者 =（电脑，成员连接）的接手与转移（ADR-021 第 4 批）：补丁认不出电脑的任务由第一台来同步的电脑接手；同一台电脑的
    旧记录改挂时任务、文件、还没发简报的运行、公司文件夹一并转过去；补丁按读者连接上那把有效密钥回填电脑。"""

    def _readers(self, empty=True):
        queries = []

        def sql(query, values=None, as_dict=False):
            queries.append((" ".join(query.split()), values))
            return [("R1",)] if empty and query.lstrip().startswith("SELECT") else []

        stub = types.SimpleNamespace
        frappe = stub(db=stub(sql=sql), throw=None, _=lambda text: text)
        modules = {"frappe": frappe, "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.companion": stub(credentials=stub()),
                   "pearnly_cowork.folder_intake": stub(agent=stub(), contracts=contracts),
                   "pearnly_cowork.folder_intake.access": stub(COMPANY_FOLDER="CF", FILE="F", RULE="R", RUN="U")}
        with mock.patch.dict(sys.modules, modules):
            readers = _load("folder_readers_plumbing", APP / "folder_intake/readers.py")
        return readers, queries

    def test_a_task_without_a_computer_is_adopted_by_the_computer_reporting_its_root(self):
        readers, queries = self._readers()
        readers.adopt("C1", "PC1", ["R2", "R1", "R1"])
        self.assertEqual(len(queries), 2)
        self.assertIn("UPDATE `tabR` SET reader_device = %s", queries[1][0])
        # 条件更新（两台同时来只有一台改得到），只接手根目录是它报的那些
        self.assertIn("AND IFNULL(reader_device, '') = '' AND root_id IN %s", queries[1][0])
        self.assertEqual(queries[1][1], ("PC1", "C1", ("R1", "R2")))
        readers, queries = self._readers(empty=False)
        readers.adopt("C1", "PC1", ["R1"])
        self.assertEqual(len(queries), 1)  # 每次同步都走：没有这种任务就不发更新语句
        self.assertNotIn("FOR UPDATE", queries[0][0])
        readers, queries = self._readers()
        readers.adopt("C1", "PC1", [])
        self.assertEqual(queries, [])  # 没报根目录：什么都不查

    def test_a_moved_computer_takes_its_tasks_files_runs_and_company_folders(self):
        readers, queries = self._readers(empty=False)
        deleted = []
        readers.frappe.db.delete = lambda doctype, filters: deleted.append((doctype, filters))
        readers.moved("C1", "OLD", "NEW")
        moves = [(query, values) for query, values in queries if query.startswith("UPDATE")]
        self.assertEqual([query.split(" SET ")[0] for query, _values in moves],
                         ["UPDATE `tabR`", "UPDATE `tabF`", "UPDATE `tabU`", "UPDATE `tabCF`"])
        self.assertTrue(all(values == ("NEW", "OLD", "C1") for _query, values in moves))
        self.assertIn("brief_state = 'pending'", moves[2][0])
        self.assertEqual(deleted, [])  # NEW 上什么都没有：不停用、不删
        count = len(queries)
        readers.moved("C1", "SAME", "SAME")
        self.assertEqual(len(queries), count)

    def test_a_moved_computer_does_not_end_with_two_of_the_same(self):
        """第 4 批审查 L4：NEW 上已有同一个文件夹的启用任务时，转过来的那条停用（不在同一台电脑上跑两遍）；这家公司在 NEW 上
        这条连接已有文件夹绑定时，OLD 那一行删掉（不留两行让解绑永远拒绝）。"""
        queries, deleted = [], []

        def sql(query, values=None, as_dict=False):
            text = " ".join(query.split())
            queries.append((text, values))
            if text.startswith("SELECT root_id, folder FROM `tabR`"):
                self.assertEqual(values, ("NEW",))
                return [Row(root_id="R1", folder="A"), Row(root_id="R2", folder="")]
            if text.startswith("SELECT name, root_id, folder FROM `tabR`"):
                self.assertEqual(values, ("OLD", "C1"))
                return [Row(name="T1", root_id="R1", folder="a\\b"), Row(name="T2", root_id="R1", folder="B"),
                        Row(name="T3", root_id="R2", folder="X"), Row(name="T4", root_id="R9", folder="A")]
            if text.startswith("SELECT name FROM `tabCF`"):
                self.assertEqual(values, ("OLD", "C1", "NEW", "C1"))
                return [("F-OLD",)]
            return []

        readers, _queries = self._readers()
        readers.frappe.db.sql = sql
        readers.frappe.db.delete = lambda doctype, filters: deleted.append((doctype, filters))
        readers.moved("C1", "OLD", "NEW")
        disabled = [values for text, values in queries if text.startswith("UPDATE `tabR` SET enabled = 0")]
        self.assertEqual(disabled, [("T1",), ("T3",)])  # A 包含 a\b；R2 的根目录整个已被 NEW 读
        self.assertEqual(deleted, [("CF", {"name": ("in", ["F-OLD"])})])
        last = [text.split(" SET ")[0] for text, _values in queries if text.startswith("UPDATE") and "enabled" not in text]
        self.assertEqual(last, ["UPDATE `tabR`", "UPDATE `tabF`", "UPDATE `tabU`", "UPDATE `tabCF`"])

    def test_a_bare_connection_is_never_guessed_between_computers_holding_the_same_root(self):
        """第 4 批审查 M2：根目录编号是本机路径的摘要、不含电脑，几台都映射成 S 盘就是同一个编号。只给连接时：只有一台报过
        就是它；几台都报过按绑定带的电脑名认出唯一一台，认不出拒，不按最近同步猜。"""
        readers, _queries = self._readers()

        class Invalid(Exception):
            pass

        def throw(message):
            raise Invalid(message)

        readers.frappe.throw = throw
        peers = {"PC1": {}, "PC2": {}, "PC3": {}}
        states = {"PC1": {"roots": [{"id": "R1"}], "device": "ACC-01", "at": "2026-10-15T09:00:00"},
                  "PC2": {"roots": [{"id": "R1"}, {"id": "R2"}], "device": "ACC-02", "at": "2026-10-15T09:05:00"},
                  "PC3": {"roots": [], "device": "ACC-03"}}
        readers.credentials = types.SimpleNamespace(pool_peers=lambda connection: peers)
        readers.agent = types.SimpleNamespace(read=lambda connection, device: states.get(device, {}))
        self.assertEqual(readers.resolve("C1", "R2"), ("PC2", "C1"))  # 只有一台报过
        with self.assertRaises(Invalid):
            readers.resolve("C1", "R1")  # 两台都报过：不取最近同步的 PC2
        self.assertEqual(readers.resolve("C1", "R1", name="ACC-01"), ("PC1", "C1"))
        with self.assertRaises(Invalid):
            readers.resolve("C1", "R1", name="ACC-03")  # 电脑名对上的那台没报过这个根目录
        self.assertEqual(readers.resolve("C1", "R1", keep="PC1"), ("PC1", "C1"))  # 改任务：原来那台
        self.assertEqual(readers.resolve("PC2|C1", "R1"), ("PC2", "C1"))  # 读者编号照常
        self.assertEqual(readers.resolve("C1", "R7"), ("", "C1"))  # 谁都没报过：等电脑（之后按根目录核时拒）
        hooks = (APP / "hooks.py").read_text(encoding="utf-8")
        self.assertIn('cowork_reader_device_moved = ["pearnly_cowork.folder_intake.readers.moved",', hooks)
        # 10-16 审查：冻结给旧记录的定时任务推送记录也跟着改挂（推送域同一个钩子）
        self.assertIn('"pearnly_cowork.push.native.service.reroute"]', hooks)
        self.assertIn('cowork_device_retiring = ["pearnly_cowork.push.native.service.release_route"]', hooks)

    def test_reader_ids(self):
        self.assertEqual(contracts.reader_ref("CCD-1", "CWEC-a"), "CCD-1|CWEC-a")
        self.assertEqual(contracts.reader_ref("", "CWEC-a"), "CWEC-a")
        self.assertEqual(contracts.parse_reader("CCD-1|CWEC-a"), ("CCD-1", "CWEC-a"))
        self.assertEqual(contracts.parse_reader(" CWEC-a "), ("", "CWEC-a"))
        for bad in ("", "a|", "a b|c", "x" * 141, None, "|c|d"):
            with self.assertRaises(contracts.ContractError, msg=bad):
                contracts.parse_reader(bad)

    def test_the_patch_backfills_the_computer_of_the_readers_key(self):
        rules = [{"name": "T1", "reader": "C1"}, {"name": "T2", "reader": "C2"}, {"name": "T3", "reader": "M1"}]
        keys = {"C1": [{"name": "K1", "device": "PCA", "report_credential": "K2"},
                       {"name": "K2", "device": "PCB", "report_credential": "K2"}],
                "M1": [{"name": "K3", "device": "PCM", "report_credential": None}]}
        written, queries = [], []

        def sql(query, values=None, as_dict=False):
            text = " ".join(query.split())
            queries.append(text)
            if "FROM `tabCowork Folder Intake Rule`" in text:
                return [Row(row) for row in rules]
            if "FROM `tabCowork Express Agent Credential` k" in text:
                return [Row(row) for row in keys.get(values[0], [])]
            if "FROM `tabCowork Folder Intake Run`" in text:
                return [Row(name="U1", connection="C1")]
            if "FROM `tabCowork Company Folder`" in text:
                return [Row(name="F1", reader="M1"), Row(name="F2", reader="C9")]
            return []

        stub = types.SimpleNamespace
        frappe = stub(db=stub(has_column=lambda doctype, column: True, sql=sql,
                              set_value=lambda doctype, name, field, value, update_modified=True:
                              written.append((doctype, name, field, value, update_modified))))
        with mock.patch.dict(sys.modules, {"frappe": frappe}):
            patch = _load("patch_reader_devices", APP / "patches/v0_0_21_reader_devices.py")
            patch.execute()
        self.assertEqual(written, [
            ("Cowork Folder Intake Rule", "T1", "reader_device", "PCB", False),  # 连接当前上报的那把
            ("Cowork Folder Intake Rule", "T3", "reader_device", "PCM", False),  # T2：连接上没有有效密钥，留空
            ("Cowork Folder Intake Run", "U1", "reader_device", "PCB", False),
            ("Cowork Company Folder", "F1", "reader_device", "PCM", False)])
        self.assertTrue(any("WHERE IFNULL(reader_device, '') = ''" in query for query in queries))  # 只补空着的
        # 第 4 批审查 L3：退役不吊销密钥，回填只取没退役的电脑
        key_query = next(query for query in queries if "FROM `tabCowork Express Agent Credential` k" in query)
        self.assertIn("JOIN `tabCowork CC Desktop Device` d ON d.name = k.device AND d.retired_at IS NULL", key_query)
        self.assertTrue(any("UPDATE `tabCowork Companion Request` SET target_device = ''" in query for query in queries))
        self.assertIn("pearnly_cowork.patches.v0_0_21_reader_devices", (APP / "patches.txt").read_text())


class CompanyFolderBindings(unittest.TestCase):
    """公司 × 电脑各一行（`binding_key` 预留 `reader_device`）；同一键再绑是替换；`roots_in_use` 不按人。"""

    def _module(self):
        rows, deleted, sql = {}, [], []

        class Doc(Row):
            def update(self, values):
                dict.update(self, values)

            def __setattr__(self, key, value):
                self[key] = value

        def write(doc):
            if not doc.get("name") and doc["binding_key"] in rows:
                raise KeyError("duplicate binding_key")  # 主键唯一（autoname field:binding_key）
            rows[doc["binding_key"]] = Row(doc, name=doc["binding_key"])

        def get_all(doctype, filters=None, fields=None, order_by=None, limit_page_length=None, pluck=None):
            found = [row for row in rows.values() if all(row.get(key) == value for key, value in filters.items())]
            found = found[:limit_page_length] if limit_page_length else found
            return [row[pluck] for row in found] if pluck else found

        def delete(doctype, filters):
            deleted.append(filters)
            for name in [name for name, row in rows.items()
                         if all(row.get(key if key != "name" else "binding_key") == value
                                for key, value in filters.items())]:
                rows.pop(name)

        def query(text, values):
            sql.append((" ".join(text.split()), values))
            return [("R1",), ("R2",), ("R1",), (None,)]

        frappe = types.SimpleNamespace(
            throw=lambda message, exc=None: (_ for _ in ()).throw(ValueError(message)), _=lambda text: text,
            db=types.SimpleNamespace(exists=lambda doctype, name: name in rows, sql=query,
                                     count=lambda doctype, filters: sum(1 for row in rows.values()
                                                                        if row["company"] == filters["company"]),
                                     delete=delete),
            get_doc=lambda doctype, name, for_update=False: Doc(rows[name]), new_doc=lambda doctype: Doc(),
            get_all=get_all)
        rules = types.SimpleNamespace(_require=lambda: None,
                                      _connection=lambda name: Row(name=name) if name in ("C1", "C2") else
                                      (_ for _ in ()).throw(PermissionError("connection")))
        states = {"C1": {"roots": [{"id": "R1", "label": "D:\\Clients", "folders": ["ABC", "ABC\\2569-09"]}],
                         "device": "OFFICE-PC", "at": "2026-10-15T09:00:00"}}
        agent = types.SimpleNamespace(read=lambda reader: states.get(reader, {}), fresh=lambda state: bool(state))

        def resolve(value, root_id=None, name=None):
            # 只给连接时按根目录认电脑：C1 上报过 R1 的是 PC1；C2 这台还没同步过、认不出
            device, connection = contracts.parse_reader(value)
            return device or ("PC1" if connection == "C1" and root_id == "R1" else ""), connection

        # 池里的电脑：C1 上 PC1、PC2 两台（PC9 已移除），C2 上一台没有（第 6 批「电脑已移除」按它判）
        pools = {"C1": {"PC1": {}, "PC2": {}}, "C2": {}}
        readers = types.SimpleNamespace(
            members=lambda connection: pools.get(connection, {}),
            resolve=resolve, state=lambda device, connection: states.get(connection, {}) if device else {},
            of=lambda row: (row.get("reader_device") or "", row.get("reader") or row.get("connection")),
            ref=lambda row: contracts.reader_ref(row.get("reader_device"), row.get("reader")))
        modules = {"frappe": frappe,
                   "frappe.utils": types.SimpleNamespace(get_datetime=lambda value: value,
                                                         now_datetime=lambda: datetime(2026, 10, 15, 9, 0)),
                   "pearnly_cowork": types.ModuleType("pearnly_cowork"),
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(agent=agent, contracts=contracts, rules=rules,
                                                                         readers=readers),
                   "pearnly_cowork.folder_intake.rules": rules,
                   "pearnly_cowork.folder_intake.access": types.SimpleNamespace(
                       COMPANY_FOLDER="Cowork Company Folder", RULE="Cowork Folder Intake Rule", write=write)}
        with mock.patch.dict(sys.modules, modules):
            module = _load("company_folders_under_test", APP / "folder_intake/company_folders.py")
            yield module, rows, deleted, sql

    def test_one_row_per_company_and_computer_and_binding_again_replaces(self):
        for module, rows, deleted, sql in self._module():
            binding = {"reader": "C1", "root_id": "R1", "folder": "ABC", "label": "D:\\Clients\\ABC", "share": "",
                       "device": "OFFICE-PC"}
            row = module.bind("co1", json.dumps(binding), "a@example.com")
            self.assertEqual((row["state"], row["label"], row["device"], row["reported"]),
                             ("ok", "D:\\Clients\\ABC", "OFFICE-PC", ["ABC", "ABC\\2569-09"]))
            # 读者电脑（ADR-021 第 4 批）：桥只带成员连接，按根目录认出是 PC1
            key = module.binding_key("co1", "C1", "PC1")
            self.assertEqual(key, hashlib.sha256(json.dumps(["co1", "C1", "PC1"]).encode()).hexdigest())
            self.assertEqual(list(rows), [key])
            self.assertEqual((rows[key]["reader_device"], rows[key]["bound_by"]), ("PC1", "a@example.com"))
            self.assertEqual(row["reader_id"], "PC1|C1")
            module.bind("co1", {**binding, "folder": "ABC\\2569-09"}, "b@example.com")
            self.assertEqual((len(rows), rows[key]["folder"], rows[key]["bound_by"]), (1, "ABC\\2569-09", "b@example.com"))
            module.bind("co2", binding, "a@example.com")  # 另一家公司同一台电脑：另一行
            self.assertEqual(len(rows), 2)
            for bad in ({**binding, "folder": "..\\x"}, {**binding, "root_id": "bad id"}, "not json", ["x"]):
                with self.assertRaises(ValueError, msg=bad):
                    module.bind("co1", bad, "a@example.com")
            with self.assertRaises(PermissionError):
                module.bind("co1", {**binding, "reader": "C9"}, "a@example.com")
            self.assertTrue(module.unbind("co1", "C1"))
            self.assertFalse(module.unbind("co1", "C1"))
            module.forget("co2")
            self.assertEqual(rows, {})
            self.assertEqual(module.roots_in_use("C1", "PC1"), ["R1", "R2"])
            self.assertIn("UNION", sql[-1][0])
            self.assertNotIn("user", sql[-1][0].lower().split("where")[1])  # 不按人过滤
            self.assertEqual(sql[-1][1], ("C1", "PC1", "C1", "PC1"))  # 这台电脑的，与还没记电脑的老行
            self.assertIn("IFNULL(reader_device, '') IN (%s, '')", sql[-1][0])

    def test_two_computers_are_two_rows_and_an_unreported_root_waits(self):
        for module, rows, _deleted, _sql in self._module():
            base = {"root_id": "R1", "folder": "ABC", "label": "D:\\Clients\\ABC", "device": "PC"}
            module.bind("co1", {**base, "reader": "C1"}, "a@example.com")
            row = module.bind("co1", {**base, "reader": "C2", "root_id": "R7"}, "a@example.com")
            self.assertEqual(len(rows), 2)
            self.assertEqual(row["state"], "offline")  # C2 这台没同步过
            self.assertEqual([item["reader"] for item in module.view("co1")], ["C1", "C2"])
            self.assertEqual([item["reader_gone"] for item in module.view("co1")], [False, False])  # 没认出电脑不算移除

    def test_a_folder_read_by_a_removed_computer_says_so(self):
        """第 6 批：读它的电脑已被移除（这条连接上不在了）的公司文件夹带 ``reader_gone``，页面写「电脑已移除」、按读者编号解绑。"""
        for module, rows, _deleted, _sql in self._module():
            rows["gone"] = Row(name="gone", binding_key="gone", company="co1", reader="C1", reader_device="PC9",
                               root_id="R1", folder="ABC")
            rows["here"] = Row(name="here", binding_key="here", company="co1", reader="C1", reader_device="PC2",
                               root_id="R1", folder="ABC")
            found = {item["reader_id"]: item["reader_gone"] for item in module.view("co1")}
            self.assertEqual(found, {"PC9|C1": True, "PC2|C1": False})
            self.assertTrue(module.unbind("co1", "PC9|C1"))
            self.assertEqual(list(rows), ["here"])

    def test_computers_on_one_connection_each_have_a_row_and_unbinding_does_not_guess(self):
        """同一个池里两台电脑各绑一行（读者编号「电脑|连接」）；只给连接的旧页面在有两行时不删（不猜是哪台）；补丁认不出
        电脑留下的老行（电脑为空）由这台再绑时接手，不另起一行。"""
        for module, rows, _deleted, _sql in self._module():
            base = {"root_id": "R1", "folder": "ABC", "label": "D:\\Clients\\ABC", "device": "PC"}
            rows["legacy"] = Row(name="legacy", binding_key="legacy", company="co1", reader="C1", reader_device="",
                                 root_id="R1", folder="OLD")
            module.bind("co1", {**base, "reader": "C1"}, "a@example.com")
            self.assertEqual(list(rows), ["legacy"])  # 接手老行
            self.assertEqual((rows["legacy"]["reader_device"], rows["legacy"]["folder"]), ("PC1", "ABC"))
            module.bind("co1", {**base, "reader": "PC2|C1", "root_id": "R2"}, "a@example.com")
            self.assertEqual(len(rows), 2)
            self.assertFalse(module.unbind("co1", "C1"))  # 两台都有：只给连接不删
            self.assertTrue(module.unbind("co1", "PC2|C1"))
            self.assertTrue(module.unbind("co1", "C1"))  # 只剩一行：旧页面照常能删
            self.assertEqual(rows, {})

    def test_a_drifted_key_does_not_block_binding_again(self):
        """第 4 批审查 M1：主键是新建那一刻的键，之后补丁回填、电脑改挂只改字段。再绑一行「没认出电脑」的同一家公司时，
        按字段找不到它，算出的键却已被它占着：换个键另起一行，不撞主键报错。"""
        for module, rows, _deleted, _sql in self._module():
            base = {"reader": "C2", "root_id": "R7", "folder": "ABC", "label": "D:\\Clients\\ABC", "device": "PC"}
            module.bind("co1", base, "a@example.com")  # C2 这台还没报过 R7：电脑为空
            key = module.binding_key("co1", "C2", "")
            rows[key]["reader_device"] = "PC5"  # 补丁回填 / 电脑改挂之后：字段变了，键没变
            module.bind("co1", base, "b@example.com")
            self.assertEqual(len(rows), 2)
            fresh = next(row for name, row in rows.items() if name != key)
            self.assertEqual((fresh["reader_device"], fresh["bound_by"]), ("", "b@example.com"))
            self.assertRegex(fresh["binding_key"], r"^[0-9a-f]{32}$")
            self.assertEqual(rows[key]["bound_by"], "a@example.com")  # 原来那一行不动

    def test_the_server_answers_the_sync_keys_the_desktop_reads(self):
        # 契约测试（tests/test_companion_contract.py CompanyFolders）比对的服务端那一半
        server = "\n".join(path.read_text(encoding="utf-8") for path in sorted((APP / "folder_intake").glob("*.py")))
        for key in ("roots_only", "max_roots", "roots_in_use"):
            self.assertRegex(server, r"[\"']%s[\"']" % key)
        source = (APP / "folder_intake/company_folders.py").read_text(encoding="utf-8")
        self.assertNotIn("pearnly_cowork.companies", source)  # folder_intake 不导入 companies（不成环）


if __name__ == "__main__":
    unittest.main()
