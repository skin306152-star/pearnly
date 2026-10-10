"""智能体底座（ADR-019）的离线检查：纯约定、工具声明与智能体定义同源、按密钥自动建智能体不重复建。

不连库、不连 Anthropic：Frappe 与 SDK 用最小替身。
"""
import importlib.util
import json
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("assistant_contracts", CORE / "assistant/contracts.py")


class _Frappe(types.ModuleType):
    """够 tools / definition / provision 导入与运行的最小 frappe。"""

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.conf = {"pearnly_product": "erp", "pearnly_assistant_api_key": "sk-ant-test"}
        self.hooks = []
        self.local = types.SimpleNamespace(site="t1.example", lang="th")
        self.session = types.SimpleNamespace(user="a@example.com")
        self.utils = types.SimpleNamespace(get_fullname=lambda user: "A")
        self.db = types.SimpleNamespace(rollback=lambda: None)
        self.store = {}
        outer = self

        class _Lock:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        self.cache = types.SimpleNamespace(
            get_value=lambda key: outer.store.get(key),
            set_value=lambda key, value, expires_in_sec=None: outer.store.__setitem__(key, value),
            lock=lambda name, timeout=None: _Lock())

    def get_hooks(self, name):
        return self.hooks if name == "pearnly_assistant_tools" else []

    def get_attr(self, path):
        module, _, attr = path.rpartition(".")
        return getattr(sys.modules[module], attr)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    @staticmethod
    def _(text):
        return text


def _install_fakes():
    frappe = _Frappe()
    sys.modules["frappe"] = frappe
    sys.modules["anthropic"] = types.SimpleNamespace(Anthropic=object, APIStatusError=Exception)
    for name in ("pearnly_core", "pearnly_core.assistant"):
        sys.modules[name] = types.ModuleType(name)
    for part in ("config", "tools", "definition", "client", "provision"):
        module = _load(f"pearnly_core.assistant.{part}", CORE / f"assistant/{part}.py")
        setattr(sys.modules["pearnly_core.assistant"], part, module)
    return frappe


frappe = _install_fakes()
tools = sys.modules["pearnly_core.assistant.tools"]
definition = sys.modules["pearnly_core.assistant.definition"]
provision = sys.modules["pearnly_core.assistant.provision"]


class ContractTests(unittest.TestCase):
    def test_ids(self):
        self.assertEqual(contracts.session_id(" sesn_011CZxAbc123 "), "sesn_011CZxAbc123")
        for bad in ("", "sesn_", "evil/../x", "sevt_abc12345", "sesn_a b"):
            with self.assertRaises(contracts.ContractError):
                contracts.session_id(bad)
        with self.assertRaises(contracts.ContractError):
            contracts.event_id("sesn_abc12345")

    def test_message_and_title(self):
        with self.assertRaises(contracts.ContractError):
            contracts.message_text("   ")
        with self.assertRaises(contracts.ContractError):
            contracts.message_text("x" * (contracts.TEXT_MAX + 1))
        self.assertEqual(contracts.title_from("  a \n b  " + "c" * 100), ("a b " + "c" * 100)[:contracts.TITLE_MAX])

    def test_confirmations(self):
        events = contracts.confirmations([
            {"tool_use_id": "sevt_aaaa1111", "result": "allow", "deny_message": "ignored"},
            {"tool_use_id": "sevt_bbbb2222", "result": "deny", "deny_message": " not now "},
        ])
        self.assertEqual(events[0], {"type": "user.tool_confirmation", "tool_use_id": "sevt_aaaa1111", "result": "allow"})
        self.assertEqual(events[1]["deny_message"], "not now")
        for bad in ([], [{"tool_use_id": "sevt_aaaa1111", "result": "maybe"}], "x"):
            with self.assertRaises(contracts.ContractError):
                contracts.confirmations(bad)

    def test_newer_than(self):
        newest_first = [{"id": f"sevt_{i:08d}"} for i in range(9, 0, -1)]
        self.assertEqual([e["id"] for e in contracts.newer_than(iter(newest_first), "sevt_00000007")],
                         ["sevt_00000008", "sevt_00000009"])
        self.assertEqual(contracts.newer_than(iter(newest_first), "sevt_00000009"), [])
        self.assertIsNone(contracts.newer_than(iter(newest_first), "sevt_99999999"))

    def test_upload_names(self):
        self.assertEqual(contracts.upload_name("IMG 0001 (1).HEIC", 10), "IMG_0001_(1).heic")
        self.assertEqual(contracts.upload_name("ใบเสร็จ.pdf", 5), "ใบเสร็จ.pdf")
        self.assertEqual(contracts.upload_name("C:\\fake\\发票.jpg", 3), "发票.jpg")
        self.assertEqual(contracts.upload_name("../../etc/x.csv", 1), "x.csv")
        for name, size in (("a.exe", 1), ("noext", 1), ("a.pdf", 0), ("a.pdf", contracts.UPLOAD_MAX_BYTES + 1)):
            with self.assertRaises(contracts.ContractError):
                contracts.upload_name(name, size)
        self.assertEqual(contracts.mount_path("file_abc1", "a.pdf"), "/mnt/session/uploads/file_abc1/a.pdf")
        with self.assertRaises(contracts.ContractError):
            contracts.file_id("../file_x")

    def test_answered_tool_uses(self):
        self.assertEqual(contracts.answered_tool_uses([
            {"type": "user.custom_tool_result", "custom_tool_use_id": "sevt_1"},
            {"type": "agent.custom_tool_use", "id": "sevt_2"}]), {"sevt_1"})


class ToolAndDefinitionTests(unittest.TestCase):
    def setUp(self):
        frappe.hooks = []

    def test_product_declarations_join_core_tools_and_agent_definition(self):
        product = types.ModuleType("fake_product_tools")
        product.TOOLS = [{"name": "erp_ping", "description": "d", "input_schema": {"type": "object"},
                          "handler": "fake_product_tools.ping"}]
        product.ping = lambda tool_input: "pong"
        sys.modules["fake_product_tools"] = product
        frappe.hooks = ["fake_product_tools.TOOLS"]
        names = [t["name"] for t in tools.declared()]
        self.assertEqual(names, sorted(names))
        self.assertIn("erp_ping", names)
        self.assertIn("pearnly_whoami", names)
        spec = definition.spec("erp")
        custom = [t["name"] for t in spec["tools"] if t["type"] == "custom"]
        self.assertEqual(custom, names)  # 给模型看的工具表与服务端派发同源
        self.assertEqual(tools.run("erp_ping", {}), ("pong", False))
        self.assertTrue(tools.run("not_declared", {})[1])

    def test_refusals_become_error_results(self):
        product = types.ModuleType("fake_refuse")
        product.TOOLS = [{"name": "refuse", "description": "d", "input_schema": {"type": "object"},
                          "handler": "fake_refuse.go"}]

        def go(_input):
            raise frappe.PermissionError("no access to this account set")

        product.go = go
        sys.modules["fake_refuse"] = product
        frappe.hooks = ["fake_refuse.TOOLS"]
        self.assertEqual(tools.run("refuse", {}), ("no access to this account set", True))

    def test_core_tools_have_translated_labels(self):
        terms = (CORE / "i18n/messages.tsv").read_text(encoding="utf-8").splitlines()
        english = {line.split("\t", 1)[0] for line in terms[1:] if line.strip()}
        for tool in tools.CORE_TOOLS:
            self.assertIn(tool.get("label"), english, f"{tool['name']} needs a label in the core term table")
        spec = definition.spec("erp")
        self.assertNotIn("label", json.dumps([t for t in spec["tools"] if t["type"] == "custom"]))

    def test_fingerprint_follows_the_definition(self):
        first = definition.spec("erp")["metadata"]["pearnly_spec"]
        self.assertEqual(first, definition.spec("erp")["metadata"]["pearnly_spec"])
        self.assertNotEqual(first, definition.spec("dms")["metadata"]["pearnly_spec"])


class _FakeApi:
    def __init__(self, agents=(), environments=()):
        self.created, self.updated = [], []
        api = self

        class Agents:
            def list(self):
                return list(agents)

            def create(self, **body):
                api.created.append(body)
                return types.SimpleNamespace(id="agent_new")

            def update(self, agent_id, **body):
                api.updated.append(agent_id)

        class Environments:
            def list(self):
                return list(environments)

            def create(self, **body):
                api.created.append(body)
                return types.SimpleNamespace(id="env_new")

        self.beta = types.SimpleNamespace(agents=Agents(), environments=Environments())


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        frappe.hooks, frappe.store = [], {}

    def _use(self, api):
        sys.modules["pearnly_core.assistant.client"].client = lambda: api

    def test_creates_once_then_uses_the_cache(self):
        api = _FakeApi()
        self._use(api)
        self.assertEqual(provision.ensure(), ("agent_new", "env_new"))
        self.assertEqual(len(api.created), 2)
        self._use(_FakeApi())
        self.assertEqual(provision.ensure(), ("agent_new", "env_new"))  # 缓存命中，不再建

    def test_reuses_the_existing_agent_and_updates_a_stale_definition(self):
        spec = definition.spec("erp")
        stale = types.SimpleNamespace(id="agent_old", metadata={**spec["metadata"], "pearnly_spec": "old"})
        other = types.SimpleNamespace(id="agent_dms", metadata={**spec["metadata"], "pearnly_product": "dms"})
        env = types.SimpleNamespace(id="env_old", name=definition.ENVIRONMENT_NAME)
        api = _FakeApi(agents=[other, stale], environments=[env])
        self._use(api)
        self.assertEqual(provision.ensure(), ("agent_old", "env_old"))
        self.assertEqual(api.updated, ["agent_old"])
        self.assertEqual(api.created, [])

    def test_a_new_key_does_not_reuse_another_accounts_ids(self):
        self._use(_FakeApi())
        provision.ensure()
        frappe.conf["pearnly_assistant_api_key"] = "sk-ant-other"
        try:
            api = _FakeApi(agents=[], environments=[])
            self._use(api)
            provision.ensure()
            self.assertEqual(len(api.created), 2)
        finally:
            frappe.conf["pearnly_assistant_api_key"] = "sk-ant-test"


class PageTests(unittest.TestCase):
    def test_jinja_page_only_renders_its_two_variables(self):
        html = (CORE / "www/assistant.html").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"\{\{\s*(\w+)\s*\}\}", html)), ["csrf_token", "lang"])
        self.assertEqual(html.count("{{"), 2)
        self.assertNotIn("{%", html)

    def test_doctypes_are_platform_records_not_customer_desk_pages(self):
        for name in ("pearnly_assistant_session", "pearnly_assistant_tool_run"):
            meta = json.loads((CORE / f"pearnly_core/doctype/{name}/{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["permissions"], [{"role": "System Manager", "read": 1}])
            self.assertTrue(any(f.get("unique") for f in meta["fields"]))
        session = json.loads((CORE / "pearnly_core/doctype/pearnly_assistant_session/pearnly_assistant_session.json")
                             .read_text(encoding="utf-8"))
        self.assertIn("starred", [f["fieldname"] for f in session["fields"]])


class BrandAndLoginTests(unittest.TestCase):
    def test_brand_files_come_from_the_website(self):
        builder = _load("assistant_brand", ROOT / "scripts/build-assistant-brand.py")
        for name, body in builder.render().items():
            actual = (ROOT / f"frontend/assistant/public/brand/{name}").read_text(encoding="utf-8")
            self.assertEqual(actual, body, f"run scripts/build-assistant-brand.py ({name} drifted from website/)")
        self.assertEqual(sorted(builder.render()), ["cowork.svg", "dms.svg", "erp.svg"])

    def test_assistant_uses_the_single_login_entry(self):
        source = (CORE / "login_redirect.py").read_text(encoding="utf-8")
        entries = re.search(r"GUEST_ENTRY_PATHS = \{([^}]*)\}", source).group(1)
        self.assertIn('"/assistant"', entries)
        page = (CORE / "www/assistant.py").read_text(encoding="utf-8")
        self.assertIn("pearnly_core.identity.begin_login?redirect_to=/assistant", page)


if __name__ == "__main__":
    unittest.main()
