"""文件夹自动录入：纯规则 + 「只新增、不改现有」的守护（不连库、不依赖 Frappe）。"""
import ast
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("folder_contracts", APP / "folder_intake/contracts.py")


class ContractTests(unittest.TestCase):
    def test_folder_is_relative_and_never_the_result_folder(self):
        self.assertEqual(contracts.folder(" 冰厂/进项\\2026-10 "), "冰厂\\进项\\2026-10")
        self.assertEqual(contracts.folder(""), "")
        for bad in ("..\\x", "a\\..\\b", "C:\\x", "a|b", "_Pearnly结果", "a\\_pearnly结果\\b", "a.\\b"):
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
        for day, at in ((29, "22:00"), (1, "24:00"), (1, "7:00"), (True, "22:00")):
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
        for bad in ("../c.pdf", "_Pearnly结果\\2026-10\\x.pdf", ""):
            with self.assertRaises(contracts.ContractError):
                contracts.relpath(bad)
        self.assertEqual(contracts.filename("x.HEIC"), "x.HEIC")
        for bad in ("x.xlsx", "x.docx", "a\\x.pdf"):
            with self.assertRaises(contracts.ContractError):
                contracts.filename(bad)

    def test_workbook_names(self):
        self.assertEqual(contracts.workbook_name("INV/001", "0105536082808", "a.pdf"), "INV-001_2808.xlsx")
        self.assertEqual(contracts.workbook_name("", "", "收据 照片.jpg"), "未识别_收据 照片.xlsx")
        self.assertEqual(contracts.workbook_name("A1", "", "x.pdf"), "A1.xlsx")


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


if __name__ == "__main__":
    unittest.main()
