"""重推定时任务的票带任务默认处理（F6，2026-10-12）：网页复核页、手机、CC 重推与后台推送同一口径。不连库、不依赖 Frappe。

- 定时任务的票推往该任务的连接与账套：推送服务经 `cowork_push_fixed_choices` 钩子取任务默认值（`folder_intake.push.fixed_for`）；
  推往别的连接 / 账套、不是这个任务的票、取不到时回 {}，不抛错；
- 推送服务：调用方给了 fixed（后台推送，{} 也算）照用、不查钩子；没给就合并钩子结果并去掉对方档；
- 被任务默认值决定的类别不写进账套记忆（`save_choices` 拒收）。
"""
import ast
import importlib.util
import json
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path.insert(0, str(ROOT / "tests"))
from test_cowork_delete_everything import FakeDB, _frappe  # noqa: E402

HISTORY, FILE, RULE = "Cowork OCR History", "Cowork Folder Intake File", "Cowork Folder Intake Rule"
BOOK = r"S:\2569\EXP69\69ACME"


def _load(name, path, modules):
    with mock.patch.dict(sys.modules, modules):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


contracts = _load("folder_contracts_fixed", APP / "folder_intake/contracts.py", {})
identity = _load("targets_identity_fixed", APP / "targets/identity.py", {})
# 函数内导入（`fixed_for` 里的 `targets.identity`）在调用时解析，测试期间一直挂着。
IDENTITY = {"pearnly_cowork.targets": types.SimpleNamespace(identity=identity),
            "pearnly_cowork.targets.identity": identity}


class Translated(Exception):
    """`frappe.throw(_(key))` 抛出的错误：带词条键，便于断言。"""


# ── 提供者：folder_intake.push.fixed_for ──────────────────────────────────────────


class FixedFor(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(sys.modules, {"pearnly_cowork": types.ModuleType("pearnly_cowork"), **IDENTITY})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _push(self, tables, broken=None):
        db = FakeDB(tables)
        if broken:
            db.get_value = mock.Mock(side_effect=broken)
        self.logged = []
        frappe = _frappe(db)
        frappe.log_error = lambda **kw: self.logged.append(kw)
        modules = {"frappe": frappe,
                   "pearnly_cowork.folder_intake": types.SimpleNamespace(contracts=contracts,
                                                                         destinations=types.SimpleNamespace()),
                   "pearnly_cowork.history": types.SimpleNamespace(push_state=types.SimpleNamespace()),
                   "pearnly_cowork.push.application": types.SimpleNamespace(service=types.SimpleNamespace())}
        return _load("folder_push_fixed", APP / "folder_intake/push.py", modules)

    @staticmethod
    def _tables(defaults=None, histories=("H1",), source="SF1", rule=True, enabled=1, book=BOOK, connection="C1"):
        tables = {HISTORY: {"H1": {"source_file": source}, "H2": {"source_file": "SF-WORKBOOK"}},
                  FILE: {"F1": {"rule": "R1", "source_file": "SF1", "histories_json": json.dumps(list(histories))}},
                  RULE: {}}
        if rule:
            tables[RULE]["R1"] = {"connection": connection, "account_set_key": book, "enabled": enabled,
                                  "defaults_json": json.dumps(defaults if defaults is not None else
                                                              {"payment": "credit", "account": "5300",
                                                               "item": "ask", "department": "HQ"})}
        return tables

    def test_same_connection_and_account_set_gets_the_task_defaults(self):
        push = self._push(self._tables())
        expected = {"payment": "credit", "account": "5300", "department": "HQ"}  # 「停下选」不算
        self.assertEqual(push.fixed_for("H1", "C1", BOOK), expected)
        # 账套路径写法不同（大小写、斜杠、结尾分隔符）也是同一份账套
        self.assertEqual(push.fixed_for("H1", "C1", "s:/2569/exp69/69acme/"), expected)

    def test_a_turned_off_task_still_applies(self):
        push = self._push(self._tables(enabled=0))
        self.assertEqual(push.fixed_for("H1", "C1", BOOK)["account"], "5300")

    def test_other_account_set_or_connection_gets_nothing(self):
        push = self._push(self._tables())
        self.assertEqual(push.fixed_for("H1", "C1", r"S:\2569\EXP69\OTHER"), {})
        self.assertEqual(push.fixed_for("H1", "C2", BOOK), {})
        self.assertEqual(push.fixed_for("H1", "", BOOK), {})

    def test_documents_not_from_this_task_get_nothing(self):
        # 同一原件但这张票不在这份文件的票里
        self.assertEqual(self._push(self._tables(histories=("H9",))).fixed_for("H1", "C1", BOOK), {})
        # 回导工作簿生成的新票：原件是工作簿，查不到任务
        self.assertEqual(self._push(self._tables()).fixed_for("H2", "C1", BOOK), {})
        # 没有原件的票（网页 / LINE 之外的来源）、找不到的票
        self.assertEqual(self._push(self._tables(source="")).fixed_for("H1", "C1", BOOK), {})
        self.assertEqual(self._push(self._tables()).fixed_for("NOPE", "C1", BOOK), {})
        # 任务已删除
        self.assertEqual(self._push(self._tables(rule=False)).fixed_for("H1", "C1", BOOK), {})
        # 任务没设默认处理
        self.assertEqual(self._push(self._tables(defaults={})).fixed_for("H1", "C1", BOOK), {})

    def test_lookup_problem_returns_nothing_instead_of_failing_every_push(self):
        push = self._push(self._tables(), broken=RuntimeError("db gone"))
        self.assertEqual(push.fixed_for("H1", "C1", BOOK), {})
        self.assertEqual(len(self.logged), 1)
        tables = self._tables()
        tables[FILE]["F1"]["histories_json"] = "not json"
        self.assertEqual(self._push(tables).fixed_for("H1", "C1", BOOK), {})

    def test_rule_fixed_is_what_the_background_push_passes(self):
        push = self._push(self._tables())
        self.assertEqual(push.rule_fixed('{"payment": "cash", "item": "ask", "party": "X", "vat": ""}'),
                         {"payment": "cash"})
        self.assertEqual(push.rule_fixed(None), {})


# ── 推送服务：push.application.service ───────────────────────────────────────────


def _service(hooks=(), providers=None):
    """按真实导入加载推送服务，所有依赖换成替身；钩子与提供者可指定。"""
    calls = {"providers": [], "records": []}

    def throw(message, exc=None):
        raise Translated(message)

    def get_attr(path):
        def provider(history, connection, account_set):
            calls["providers"].append((path, history, connection, account_set))
            return (providers or {}).get(path)
        return provider

    frappe = types.SimpleNamespace(
        _=lambda text, *a, **k: text, throw=throw, get_hooks=mock.Mock(return_value=list(hooks)), get_attr=get_attr,
        db=types.SimpleNamespace(rollback=lambda: None, get_value=lambda *a, **k: None),
        ValidationError=Translated, PermissionError=Translated, DoesNotExistError=Translated,
        session=types.SimpleNamespace(user="a@example.com"), conf={})
    frappe_module = types.ModuleType("frappe")
    frappe_module.__dict__.update(vars(frappe))
    stub = mock.MagicMock
    choice_rules = types.SimpleNamespace(PAYMENT_VALUES=("cash", "credit"), VAT_VALUES=("claim", "expense"),
                                         NEW="__new__")
    choice_store = types.SimpleNamespace(
        record=lambda *args: calls["records"].append(args) or True, remembered=lambda *a: {})
    modules = {"frappe": frappe_module, "pearnly_cowork": types.ModuleType("pearnly_cowork")}
    for name in ("access", "connections", "targets", "contracts", "contracts.history", "contracts.push", "history",
                 "push", "push.application", "push.application.provenance", "push.express", "push.express.common",
                 "push.express.prepare", "push.express.preflight", "push.express.history", "push.mrerp",
                 "push.native", "push.receipts", "targets.choice", "targets.contracts", "targets.freeze",
                 "targets.selection"):
        modules["pearnly_cowork." + name] = stub()
    modules["pearnly_cowork.targets.identity"] = identity
    modules["pearnly_cowork.push"].choices = choice_store
    modules["pearnly_cowork.push.express"].choices = choice_rules
    service = _load("push_service_fixed", APP / "push/application/service.py", modules)
    return service, frappe_module, calls


class ServiceFixed(unittest.TestCase):
    def test_given_fixed_is_used_without_asking_the_providers(self):
        service, frappe, calls = _service(hooks=["p.one"], providers={"p.one": {"account": "9999"}})
        self.assertEqual(service._fixed("H1", "C1", BOOK, {"payment": "cash"}), {"payment": "cash"})
        self.assertEqual(service._fixed("H1", "C1", BOOK, {}), {})  # 后台推送：任务没设默认值也算给了
        frappe.get_hooks.assert_not_called()
        self.assertEqual(calls["providers"], [])

    def test_missing_fixed_merges_the_providers_and_drops_the_party(self):
        service, _frappe_module, calls = _service(
            hooks=["p.one", "p.two", "p.none"],
            providers={"p.one": {"account": "5300", "party": "S001"}, "p.two": {"payment": "credit"}})
        self.assertEqual(service._fixed("H1", "C1", BOOK, None), {"account": "5300", "payment": "credit"})
        self.assertEqual([call[1:] for call in calls["providers"]], [("H1", "C1", BOOK)] * 3)

    def test_nothing_from_the_providers_is_none(self):
        service, _frappe_module, _calls = _service(hooks=["p.one"], providers={"p.one": {"party": "S001"}})
        self.assertIsNone(service._fixed("H1", "C1", BOOK, None))
        service, _frappe_module, _calls = _service()
        self.assertIsNone(service._fixed("H1", "C1", BOOK, None))


class ServiceEntrypoints(unittest.TestCase):
    """复核页 / 手机 / CC 走的入口（`choices_view`、`save_choices`）自己取任务默认值，调用方不用改。"""

    def _wired(self, service, config=None):
        doc = types.SimpleNamespace(name="H1", workspace="W1", client="")
        loaded = types.SimpleNamespace(endpoint={"adapter": "express"}, doc=types.SimpleNamespace(name="C1"))
        target = types.SimpleNamespace(account_set=BOOK)
        facts = (target, {}, {}, "purchase", {}, config or {
            "reported_accounts": [{"code": "5300"}, {"code": "5400"}], "reported_suppliers": [{"code": "S001"}],
            "reported_departments": [{"code": "HQ"}]})
        service.scope.require = lambda *a: None
        service.storage.read = lambda history, **k: doc
        service.connections.read_for_push = lambda *a, **k: loaded
        service.connections.ensure_account_masters = lambda *a, **k: dict(self.masters)
        service._express_facts = lambda *a: facts
        service._document_block = lambda doc: None

    masters = {"state": "ready", "present": True}

    def test_choices_view_applies_and_shows_the_task_defaults(self):
        service, _frappe_module, _calls = _service(hooks=["p.one"], providers={"p.one": {"account": "5300"}})
        self._wired(service)
        seen = []
        service._decisions = lambda *args, **kwargs: seen.append(args[-1]) or {"needed": [], "applied": {}}
        view = service.choices_view("H1", "C1", BOOK, None)
        self.assertEqual(seen, [{"account": "5300"}])
        self.assertEqual(view["fixed"], {"account": "5300"})
        # 后台推送显式给 {}：照用，不查钩子，也不回 fixed
        seen.clear()
        view = service.choices_view("H1", "C1", BOOK, None, fixed={})
        self.assertEqual((seen, "fixed" in view), ([{}], False))

    def test_choices_view_waits_for_a_chart_still_being_read(self):
        """推往电脑没选的账套、科目表还没读到：要选费用科目却没有可选值时回 `wait`（服务端已请电脑读），不给空列表。"""
        service, frappe_module, _calls = _service(hooks=[], providers={})
        self._wired(service, config={})
        commits = []
        frappe_module.db.commit = lambda: commits.append(1)
        service._decisions = lambda *a, **k: {"needed": [{"kind": "account", "subject": "S"}], "applied": {}}
        self.masters = {"state": "reading", "requested": True}
        view = service.choices_view("H1", "C1", BOOK, None)
        self.assertEqual((view["wait"]["state"], view["masters"]), ("reading", {"state": "reading"}))
        self.assertEqual(commits, [1])  # 建了读的请求：当场提交（网页取「要选的项」是 GET）
        # 不靠科目表的项（付款方式）照常问；科目表在的时候也照常问
        service._decisions = lambda *a, **k: {"needed": [{"kind": "payment", "subject": "S"}], "applied": {}}
        self.assertNotIn("wait", service.choices_view("H1", "C1", BOOK, None))
        service._decisions = lambda *a, **k: {"needed": [{"kind": "account", "subject": "S"}], "applied": {}}
        self.masters = {"state": "ready", "present": True}
        self.assertNotIn("wait", service.choices_view("H1", "C1", BOOK, None))

    def test_choices_view_without_task_defaults_has_no_fixed(self):
        service, _frappe_module, _calls = _service(hooks=["p.one"], providers={})
        self._wired(service)
        seen = []
        service._decisions = lambda *args, **kwargs: seen.append(args[-1]) or {"needed": [], "applied": {}}
        self.assertNotIn("fixed", service.choices_view("H1", "C1", BOOK, None))
        self.assertEqual(seen, [None])

    def test_save_choices_refuses_kinds_covered_by_the_task_defaults(self):
        service, _frappe_module, calls = _service(hooks=["p.one"], providers={"p.one": {"account": "5300"}})
        self._wired(service)
        items = [{"kind": "party", "subject": "s", "value": "S001"}, {"kind": "account", "subject": "s", "value": "5400"}]
        with self.assertRaises(Translated) as raised:
            service.save_choices("H1", "C1", BOOK, json.dumps(items))
        self.assertEqual(str(raised.exception), "cowork.choice.task_default")
        self.assertEqual(calls["records"], [])  # 整批不记，账套记忆不变

    def test_save_choices_still_records_what_the_task_does_not_decide(self):
        service, _frappe_module, calls = _service(hooks=["p.one"], providers={"p.one": {"account": "5300"}})
        self._wired(service)
        items = [{"kind": "party", "subject": "s", "value": "S001"}, {"kind": "department", "subject": "*", "value": "HQ"}]
        self.assertEqual(service.save_choices("H1", "C1", BOOK, json.dumps(items)), {"saved": 2})
        self.assertEqual([record[2] for record in calls["records"]], ["party", "department"])
        # 不是定时任务的票：照旧都记
        service, _frappe_module, calls = _service(hooks=["p.one"], providers={})
        self._wired(service)
        items = [{"kind": "account", "subject": "s", "value": "5400"}]
        self.assertEqual(service.save_choices("H1", "C1", BOOK, json.dumps(items)), {"saved": 1})


# ── 接线：钩子登记、两个入口都经 _fixed、词条 ────────────────────────────────────────


class Wiring(unittest.TestCase):
    def test_hook_is_declared_and_points_at_a_function(self):
        tree = ast.parse((APP / "hooks.py").read_text())
        declared = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "cowork_push_fixed_choices"}
        self.assertEqual(declared, {"cowork_push_fixed_choices": ["pearnly_cowork.folder_intake.push.fixed_for"]})
        functions = {node.name for node in ast.parse((APP / "folder_intake/push.py").read_text()).body
                     if isinstance(node, ast.FunctionDef)}
        self.assertTrue({"fixed_for", "rule_fixed"} <= functions)

    def test_prepare_and_choices_view_take_fixed_through_the_hook(self):
        tree = ast.parse((APP / "push/application/service.py").read_text())
        for name in ("prepare", "choices_view", "save_choices"):
            function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
            called = {node.func.id for node in ast.walk(function)
                      if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
            self.assertIn("_fixed", called, name)

    def test_push_domain_does_not_import_folder_intake(self):
        for path in (APP / "push").rglob("*.py"):
            tree = ast.parse(path.read_text())
            modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
            modules |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
            self.assertFalse([m for m in modules if m.startswith("pearnly_cowork.folder_intake")], path)

    def test_refusal_is_translated_in_four_languages(self):
        rows = [line.split("\t") for line in (APP / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()]
        found = [row for row in rows if row[-1] == "cowork.choice.task_default"]
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]), 5)
        self.assertTrue(all(text.strip() for text in found[0][:4]))
        self.assertFalse(any("Express" in text for text in found[0][:4]))


if __name__ == "__main__":
    unittest.main()
