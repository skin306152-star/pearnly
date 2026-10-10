"""智能体底座（ADR-019）的离线检查：纯约定、引擎目录与适配器、工具声明与智能体说明同源、
按设置自动备好远端资源不重复建、管理后台的设置校验。

不连库、不连厂商：Frappe 与 SDK 用最小替身。
"""
import importlib.util
import inspect
import json
import pathlib
import re
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CORE = ROOT / "apps/pearnly_core/pearnly_core"
CONTROL = ROOT / "apps/pearnly_control/pearnly_control"
KEY = "sk-ant-api03-" + "x" * 40


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


contracts = _load("assistant_contracts", CORE / "assistant/contracts.py")


class _Frappe(types.ModuleType):
    """够 config / tools / definition / engine 与控制面设置校验导入、运行的最小 frappe。"""

    class ValidationError(Exception):
        pass

    class PermissionError(Exception):
        pass

    def __init__(self):
        super().__init__("frappe")
        self.conf = {}
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

    def whitelist(self, *args, **kwargs):
        return lambda fn: fn

    def only_for(self, role):
        return None

    @staticmethod
    def _(text):
        return text


class _Sdk(types.ModuleType):
    """`anthropic` 替身：`Anthropic(...)` 交回测试放进去的假账号。"""

    def __init__(self):
        super().__init__("anthropic")
        self.api = None
        self.APIStatusError = type("APIStatusError", (Exception,), {"status_code": 500})

    def Anthropic(self, **kwargs):
        self.api.kwargs = kwargs
        return self.api


def _install_fakes():
    frappe, sdk = _Frappe(), _Sdk()
    sys.modules["frappe"], sys.modules["anthropic"] = frappe, sdk
    for name in ("pearnly_core", "pearnly_core.assistant"):
        sys.modules[name] = types.ModuleType(name)
    sys.modules["pearnly_core.assistant.contracts"] = sys.modules["pearnly_core.assistant"].contracts = contracts
    engines = _load("pearnly_core.assistant.engines", CORE / "assistant/engines/__init__.py")
    sys.modules["pearnly_core.assistant"].engines = engines
    for part in ("base", "anthropic_managed_agents"):
        setattr(engines, part, _load(f"pearnly_core.assistant.engines.{part}", CORE / f"assistant/engines/{part}.py"))
    for part in ("config", "tools", "definition", "engine", "context", "toolrun", "relay"):
        module = _load(f"pearnly_core.assistant.{part}", CORE / f"assistant/{part}.py")
        setattr(sys.modules["pearnly_core.assistant"], part, module)
    return frappe, sdk


frappe, sdk = _install_fakes()
engines = sys.modules["pearnly_core.assistant.engines"]
claude = sys.modules["pearnly_core.assistant.engines.anthropic_managed_agents"]
config = sys.modules["pearnly_core.assistant.config"]
tools = sys.modules["pearnly_core.assistant.tools"]
definition = sys.modules["pearnly_core.assistant.definition"]
engine = sys.modules["pearnly_core.assistant.engine"]
CLAUDE = "anthropic-managed-agents"


def _configure(engine_code=CLAUDE, key=KEY, model=""):
    frappe.conf = {"pearnly_product": "erp", "pearnly_assistant_engine": engine_code,
                   "pearnly_assistant_api_key": key, "pearnly_assistant_model": model}


class ContractTests(unittest.TestCase):
    def test_ids_are_opaque_and_safe(self):
        self.assertEqual(contracts.session_id(" sesn_011CZxAbc123 "), "sesn_011CZxAbc123")
        self.assertEqual(contracts.session_id("conv-2026.10:abc"), "conv-2026.10:abc")  # 别家引擎的编号格式
        for bad in ("", "abc", "evil/../x", "-lead", "a b", "x" * 200):
            with self.assertRaises(contracts.ContractError):
                contracts.session_id(bad)
        with self.assertRaises(contracts.ContractError):
            contracts.file_id("../file_x")

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

    def test_since_keeps_the_target_and_what_follows(self):
        newest_first = [{"id": f"sevt_{i:08d}"} for i in range(9, 0, -1)]
        self.assertEqual([e["id"] for e in contracts.since(iter(newest_first), "sevt_00000008")],
                         ["sevt_00000008", "sevt_00000009"])
        self.assertIsNone(contracts.since(iter(newest_first), "sevt_99999999"))

    def test_carry_over_keeps_recent_words_without_attachment_notes(self):
        def msg(kind, text):
            return {"type": kind, "content": [{"type": "text", "text": text}]}
        events = [msg("user.message", "old question"), msg("agent.message", "old answer"),
                  {"type": "agent.tool_use", "name": "read"},
                  msg("user.message", "这是什么\n\n[Attached file, mounted read-only in the sandbox:\n- a.jpg → /x/a.jpg]"),
                  msg("agent.message", "一张收据")]
        text = contracts.carry_over(events)
        self.assertEqual(text, "User: old question\n\nAssistant: old answer\n\nUser: 这是什么\n\nAssistant: 一张收据")
        self.assertEqual(contracts.carry_over(events, max_chars=30), "User: 这是什么\n\nAssistant: 一张收据")
        self.assertEqual(contracts.carry_message("x")["type"], "system.message")

    def test_context_size_counts_cached_input_too(self):
        events = [{"type": "span.model_request_end", "model_usage": {"input_tokens": 2, "cache_read_input_tokens": 100,
                                                                      "cache_creation_input_tokens": 10, "output_tokens": 50}},
                  {"type": "agent.message"}]
        self.assertEqual(contracts.context_size(events), 112)
        self.assertIsNone(contracts.context_size([{"type": "agent.message"}]))

    def test_answered_tool_uses(self):
        self.assertEqual(contracts.answered_tool_uses([
            {"type": "user.custom_tool_result", "custom_tool_use_id": "sevt_1"},
            {"type": "agent.custom_tool_use", "id": "sevt_2"}]), {"sevt_1"})


class EngineRegistryTests(unittest.TestCase):
    """引擎只在目录登记一次；后台、站点、开通步骤都按目录走，别处不写死厂商。"""

    def test_price_tiers_set_the_continuation_point(self):
        self.assertEqual(engines.context_limit(CLAUDE, "claude-haiku-5-5"), 60_000)  # 10 万分档线的 60%
        self.assertIsNone(engines.context_limit(CLAUDE, "claude-opus-5-5"))  # 1M 内同价：交给引擎自带的压缩
        for e in engines.ENGINES:
            for model, limit in e.tiers:
                self.assertIn(model, e.models)
                self.assertGreater(limit, 0)

    def test_check_follows_each_engines_own_rules(self):
        self.assertEqual(engines.check(CLAUDE, f"  {KEY} ", " "), (CLAUDE, KEY, ""))
        self.assertEqual(engines.check(CLAUDE, KEY, "claude-sonnet-5-5")[2], "claude-sonnet-5-5")
        for code, key, model in ((CLAUDE, "sk-proj-abc", ""), ("someone-else", KEY, ""), ("", KEY, ""),
                                 (CLAUDE, "", ""), (CLAUDE, KEY, "bad model"), (CLAUDE, KEY, "claude-not-listed")):
            with self.assertRaises(engines.EngineError):
                engines.check(code, key, model)

    def test_every_registered_engine_is_complete(self):
        codes = [e.code for e in engines.ENGINES]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertEqual([o["value"] for o in engines.options()], codes)
        for option in engines.options():
            self.assertTrue(option["models"], option["value"])
            self.assertEqual(len(option["models"]), len(set(option["models"])))
        dockerfile = (ROOT / "infrastructure/images/frappe.Dockerfile").read_text(encoding="utf-8")
        for e in engines.ENGINES:
            module, _, name = e.adapter.rpartition(".")
            adapter = getattr(sys.modules[module], name)
            self.assertTrue(issubclass(adapter, engines.base.Adapter))
            self.assertFalse(inspect.isabstract(adapter), f"{e.code} adapter misses part of the interface")
            self.assertRegex(e.package, r"^[A-Za-z0-9_.-]+==[0-9][0-9.]*$")
            self.assertIn(e.package, dockerfile, f"{e.code} SDK must be pinned in the runtime image")

    def test_registry_and_contracts_stay_pure(self):
        for path in (CORE / "assistant/engines/__init__.py", CORE / "assistant/engines/base.py",
                     CORE / "assistant/contracts.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotRegex(source, r"(?m)^\s*(import|from)\s+(frappe|anthropic)\b", path.name)

    def test_no_vendor_is_hardcoded_outside_its_adapter(self):
        vendor = re.compile(r"anthropic|sk-ant|claude-", re.I)
        shared = [p for p in (CORE / "assistant").rglob("*.py") if p.parent.name != "engines"]
        shared += [CORE / "assistant/engines/base.py", CONTROL / "onboarding/assistant.py",
                   CONTROL / "public/js/assistant_settings.js", CONTROL / "provisioning.py", CONTROL / "issues.py",
                   CONTROL / "pearnly_control/doctype/pearnly_tenant/pearnly_tenant.py"]
        for path in shared:
            self.assertIsNone(vendor.search(path.read_text(encoding="utf-8")), str(path.relative_to(ROOT)))
        step = (ROOT / "infrastructure/production/site-step.py").read_text(encoding="utf-8")
        branch = step.split('elif action == "set_assistant":', 1)[1].split("elif action", 1)[0]
        self.assertIsNone(vendor.search(branch))
        for doctype in ("pearnly_tenant", "pearnly_issue"):
            meta = (CONTROL / f"pearnly_control/doctype/{doctype}/{doctype}.json").read_text(encoding="utf-8")
            self.assertIsNone(vendor.search(meta), doctype)


class ConfigTests(unittest.TestCase):
    def test_settings_come_from_the_site_and_default_the_model(self):
        _configure()
        s = config.settings()
        self.assertEqual((s.engine.code, s.api_key, s.model), (CLAUDE, KEY, engines.default_model(CLAUDE)))
        _configure(model="claude-sonnet-5-5")
        self.assertEqual(config.settings().model, "claude-sonnet-5-5")

    def test_missing_or_unknown_settings_mean_not_set_up(self):
        for setup in ({"engine_code": ""}, {"key": ""}, {"engine_code": "gone-vendor"}, {"key": "sk-other"}):
            _configure(**setup)
            with self.assertRaises(frappe.ValidationError):
                config.settings()
            self.assertEqual(config.engine_code(), "")


class RelayTests(unittest.TestCase):
    """实时推送：一轮结束才收线，停下来等工具结果或批准时继续接着。"""

    def test_turn_end_is_idle_without_pending_action(self):
        relay = sys.modules["pearnly_core.assistant.relay"]
        self.assertTrue(relay.finished({"type": "session.status_idle", "stop_reason": {"type": "end_turn"}}))
        self.assertTrue(relay.finished({"type": "session.status_terminated"}))
        self.assertFalse(relay.finished({"type": "session.status_idle", "stop_reason": {"type": "requires_action"}}))
        self.assertFalse(relay.finished({"type": "agent.message"}))

    def test_user_side_actions_attach_the_relay_before_posting(self):
        source = (CORE / "assistant/sessions.py").read_text(encoding="utf-8")
        for name, action in (("send", ".post("), ("confirm", ".post("), ("run_tool", "toolrun.execute(")):
            body = source.split(f"def {name}(", 1)[1].split("\ndef ", 1)[0]
            self.assertLess(body.index("relay.ensure("), body.index(action), name)
        relay_source = (CORE / "assistant/relay.py").read_text(encoding="utf-8")
        self.assertNotIn("sessions", relay_source.split('"""', 2)[2])  # 不与 sessions 互相导入

    def test_relay_runs_pending_tools_itself(self):
        relay = sys.modules["pearnly_core.assistant.relay"]
        idle = {"type": "session.status_idle", "stop_reason": {"type": "requires_action", "event_ids": ["sevt_a1b2c3d4"]}}
        self.assertEqual(relay.pending_tools(idle), ["sevt_a1b2c3d4"])
        self.assertEqual(relay.pending_tools({"type": "session.status_idle", "stop_reason": {"type": "end_turn"}}), [])
        source = (CORE / "assistant/relay.py").read_text(encoding="utf-8")
        self.assertIn("frappe.set_user(user)", source)  # 以会话主人的身份执行
        self.assertIn("toolrun.execute(", source)
        run = (CORE / "assistant/toolrun.py").read_text(encoding="utf-8")
        self.assertIn("DuplicateEntryError", run)  # 推送线程与页面兜底谁先到谁执行，只执行一遍
        self.assertIn("except Exception", run)  # 意外出错也回结果，不让会话卡住

    def test_tools_can_reach_this_conversations_attachments_and_only_those(self):
        # 引擎不让下载用户上传的文件：原件在本站留一份挂在会话上；工具执行时知道是哪个对话，只取这个对话的附件。
        files = (CORE / "assistant/files.py").read_text(encoding="utf-8")
        upload = files.split("def upload(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('_keep(row, stored["file_id"], name, content)', upload)
        attachment = files.split("def attachment(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sessions.owned(", attachment)
        self.assertIn('"attached_to_name": row.name', attachment)
        run = (CORE / "assistant/toolrun.py").read_text(encoding="utf-8")
        self.assertIn('frappe.local.pearnly_assistant = {"session_id": conversation}', run)
        self.assertIn("frappe.local.pearnly_assistant = None", run.split("finally:", 1)[1])

    def test_new_turns_and_uploads_go_to_the_current_segment(self):
        source = (CORE / "assistant/sessions.py").read_text(encoding="utf-8")
        send = source.split("def send(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("segment_for_turn(row, api)", send)
        self.assertIn("carry_message(", send)
        self.assertIn("sessions.segment_for_turn(row, api)", (CORE / "assistant/files.py").read_text(encoding="utf-8"))


class ToolAndDefinitionTests(unittest.TestCase):
    def setUp(self):
        frappe.hooks = []
        _configure()

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
        brief = definition.brief("erp")
        self.assertEqual([t["name"] for t in brief["tools"]], names)  # 给模型看的工具表与服务端派发同源
        spec = claude.spec(brief, "claude-opus-5-5")
        self.assertEqual([t["name"] for t in spec["tools"] if t["type"] == "custom"], names)
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
        self.assertNotIn("label", json.dumps(definition.brief("erp")["tools"]))

    def test_fingerprint_follows_the_definition_and_model(self):
        first = claude.spec(definition.brief("erp"), "claude-opus-5-5")["metadata"]["pearnly_spec"]
        self.assertEqual(first, claude.spec(definition.brief("erp"), "claude-opus-5-5")["metadata"]["pearnly_spec"])
        self.assertNotEqual(first, claude.spec(definition.brief("dms"), "claude-opus-5-5")["metadata"]["pearnly_spec"])
        self.assertNotEqual(first, claude.spec(definition.brief("erp"), "claude-sonnet-5-5")["metadata"]["pearnly_spec"])


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


class PrepareTests(unittest.TestCase):
    def setUp(self):
        frappe.hooks, frappe.store = [], {}
        engine._adapters.clear()
        _configure()

    def test_adapter_is_reused_per_settings(self):
        sdk.api = _FakeApi()
        first = engine.adapter()
        self.assertIs(engine.adapter(), first)  # 同一组设置复用同一个客户端（保持连接）
        _configure(model="claude-sonnet-5-5")
        sdk.api = _FakeApi()
        self.assertIsNot(engine.adapter(), first)

    def _prepare(self, api):
        sdk.api = api
        engine._adapters.clear()  # 换一个假账号：不用上一个测试留在进程里的客户端
        adapter = engine.adapter()
        self.assertEqual(api.kwargs["api_key"], frappe.conf["pearnly_assistant_api_key"])
        return engine.prepared(adapter)

    def test_creates_once_then_uses_the_cache(self):
        api = _FakeApi()
        self.assertEqual(self._prepare(api), {"agent": "agent_new", "environment": "env_new"})
        self.assertEqual(len(api.created), 2)
        self.assertEqual(api.created[0]["model"]["id"], engines.default_model(CLAUDE))
        self.assertEqual(self._prepare(_FakeApi()), {"agent": "agent_new", "environment": "env_new"})  # 缓存命中

    def test_reuses_the_existing_agent_and_updates_a_stale_definition(self):
        spec = claude.spec(definition.brief("erp"), engines.default_model(CLAUDE))
        stale = types.SimpleNamespace(id="agent_old", metadata={**spec["metadata"], "pearnly_spec": "old"})
        other = types.SimpleNamespace(id="agent_dms", metadata={**spec["metadata"], "pearnly_product": "dms"})
        env = types.SimpleNamespace(id="env_old", name=claude.ENVIRONMENT["name"])
        api = _FakeApi(agents=[other, stale], environments=[env])
        self.assertEqual(self._prepare(api), {"agent": "agent_old", "environment": "env_old"})
        self.assertEqual(api.updated, ["agent_old"])
        self.assertEqual(api.created, [])

    def test_a_new_key_or_model_prepares_again(self):
        self._prepare(_FakeApi())
        for change in ({"key": "sk-ant-api03-" + "y" * 40}, {"model": "claude-sonnet-5-5"}):
            _configure(**change)
            api = _FakeApi()
            self._prepare(api)
            self.assertEqual(len(api.created), 2, change)


class PageTests(unittest.TestCase):
    def test_jinja_page_only_renders_its_two_variables(self):
        html = (CORE / "www/assistant.html").read_text(encoding="utf-8")
        self.assertEqual(sorted(re.findall(r"\{\{\s*(\w+)\s*\}\}", html)), ["csrf_token", "lang"])
        self.assertEqual(html.count("{{"), 2)
        self.assertNotIn("{%", html)

    def test_icons_are_fingerprinted_not_fixed_addresses(self):
        # /assets 缓存一年（Cloudflare 照存）：图标与 manifest 的地址由构建带内容指纹，页面不写死。
        html = (CORE / "www/assistant.html").read_text(encoding="utf-8")
        for name in ("favicon.ico", "icon-192.png", "apple-touch-icon.png", "manifest.webmanifest"):
            self.assertNotIn(name, html)
        self.assertNotIn('"manifest"', html)
        self.assertFalse((ROOT / "frontend/assistant/public/manifest.webmanifest").exists())
        config = (ROOT / "frontend/assistant/vite.config.ts").read_text(encoding="utf-8")
        self.assertIn("ccIcons()", config)
        for name in ("favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png"):
            self.assertIn(f'icon("{name}")', config)
            self.assertTrue((ROOT / "frontend/assistant/public" / name).exists())

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
            actual = (ROOT / f"frontend/assistant/src/brand/{name}").read_text(encoding="utf-8")
            self.assertEqual(actual, body, f"run scripts/build-assistant-brand.py ({name} drifted from website/)")
        self.assertEqual(sorted(builder.render()), ["cc.svg"])

    def test_assistant_guests_go_to_the_cc_entry_once(self):
        source = (CORE / "login_redirect.py").read_text(encoding="utf-8")
        entries = re.search(r"GUEST_ENTRY_PATHS = \{([^}]*)\}", source).group(1)
        self.assertIn('"/assistant"', entries)
        stubs = {"frappe": types.ModuleType("frappe"), "werkzeug": types.ModuleType("werkzeug"),
                 "werkzeug.exceptions": types.SimpleNamespace(HTTPException=Exception),
                 "werkzeug.utils": types.SimpleNamespace(redirect=None)}
        saved = {name: sys.modules.get(name) for name in stubs}
        sys.modules.update(stubs)
        try:
            gate = _load("assistant_login_redirect", CORE / "login_redirect.py")
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        # 第一次：去 CC 入口并留记号；记号还在又回来（本站没登上）：走本站登录口，不来回跳。
        self.assertEqual(gate.assistant_gate({}, "https://testai.pearnly.com/"), ("https://testai.pearnly.com/", True))
        self.assertEqual(gate.assistant_gate({gate.CC_ENTRY_MARK: "1"}, "https://fatcc.com"),
                         ("/api/method/pearnly_core.identity.begin_login?redirect_to=/assistant", False))
        cookies = {}
        gate.set_entry_mark(lambda key, value, **kw: cookies.update({key: (value, kw)}))
        self.assertEqual(cookies[gate.CC_ENTRY_MARK][1]["max_age"], 120)
        self.assertTrue(cookies[gate.CC_ENTRY_MARK][1]["httponly"])
        page = (CORE / "www/assistant.py").read_text(encoding="utf-8")
        self.assertIn("login_redirect.assistant_gate(", page)
        self.assertNotIn("begin_login", page)
        identity = (CORE / "identity.py").read_text(encoding="utf-8")
        self.assertIn('back = assistant_entry_url() + "/?fresh=1"', identity)
        client = (ROOT / "frontend/assistant/src/lib/frappe.ts").read_text(encoding="utf-8")
        self.assertNotIn("begin_login", client)


class _Doc(types.SimpleNamespace):
    """开户单 / 租户表单替身：`stored` 是库里那份密钥，`before` 是保存前的字段。"""

    def __init__(self, new=False, stored="", before=None, **fields):
        super().__init__(flags=types.SimpleNamespace(),
                         **{"assistant_engine": "", "assistant_model": "", "assistant_api_key": "", **fields})
        self._new, self._stored, self._before = new, stored, before or {}

    def is_new(self):
        return self._new

    def get(self, field):
        return getattr(self, field, None)

    def get_password(self, field, raise_exception=True):
        return self._stored

    def has_value_changed(self, field):
        return self._before.get(field, "") != (getattr(self, field) or "")


settings = _load("control_assistant_settings", CONTROL / "onboarding/assistant.py")


class ControlPlaneSettingsTests(unittest.TestCase):
    """引擎、模型、密钥由租户管理后台填写、可改可换，经开通步骤推到站点（ADR-019）。"""

    def test_validation_follows_the_engine_registry(self):
        settings.validate(_Doc(new=True))  # 三项全空：不开通
        ok = _Doc(new=True, assistant_engine=CLAUDE, assistant_api_key=KEY)
        settings.validate(ok)
        self.assertTrue(ok.flags.assistant_changed)
        self.assertEqual(settings.read(ok), {"engine": CLAUDE, "model": "", "api_key": KEY})
        for bad in (_Doc(new=True, assistant_api_key=KEY), _Doc(new=True, assistant_engine=CLAUDE),
                    _Doc(new=True, assistant_engine=CLAUDE, assistant_api_key="sk-other-1234567890123456789"),
                    _Doc(new=True, assistant_engine="gone-vendor", assistant_api_key=KEY)):
            with self.assertRaises(frappe.ValidationError):
                settings.validate(bad)

    def test_saved_key_is_read_back_and_same_length_rotation_is_noticed(self):
        masked = "*" * len(KEY)
        kept = _Doc(stored=KEY, assistant_engine=CLAUDE, assistant_api_key=masked,
                    before={"assistant_engine": CLAUDE, "assistant_api_key": masked})
        settings.validate(kept)
        self.assertFalse(kept.flags.assistant_changed)
        self.assertEqual(settings.read(kept)["api_key"], KEY)
        rotated = _Doc(stored=KEY, assistant_engine=CLAUDE, assistant_api_key="sk-ant-api03-" + "z" * 40,
                       before={"assistant_engine": CLAUDE, "assistant_api_key": masked})
        settings.validate(rotated)
        self.assertTrue(rotated.flags.assistant_changed)
        model = _Doc(stored=KEY, assistant_engine=CLAUDE, assistant_model="claude-sonnet-5-5",
                     assistant_api_key=masked, before={"assistant_engine": CLAUDE, "assistant_api_key": masked})
        settings.validate(model)
        self.assertTrue(model.flags.assistant_changed)

    def test_production_agent_accepts_the_step(self):
        spec = importlib.util.spec_from_file_location("provision_agent", ROOT / "infrastructure/production/provision-agent.py")
        agent = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(agent)
        job = {"action": "set_assistant", "payload": {"site": "acme-cowork.pearnly.com", "group": "cowork",
                                                       "tenant": "T-2026-1", "deployment": "DEP-2026-1"}}
        self.assertEqual(agent.validate(job)[0], "set_assistant")

    def test_site_step_checks_with_the_registry_and_writes_only_assistant_settings(self):
        source = (ROOT / "infrastructure/production/site-step.py").read_text(encoding="utf-8")
        branch = source.split('elif action == "set_assistant":', 1)[1].split("elif action", 1)[0]
        self.assertIn("engines.check(", branch)
        self.assertIn("config(pearnly_assistant_engine=engine, pearnly_assistant_model=model, "
                      "pearnly_assistant_api_key=key)", branch)
        self.assertNotIn("restart", branch)

    def test_plan_requeue_and_onboarding_carry_the_settings(self):
        plan = (CONTROL / "provisioning.py").read_text(encoding="utf-8")
        self.assertLess(plan.index('f"{dep.name}:set_assistant"'), plan.index('f"{dep.name}:bind_user"'))
        self.assertIn("def queue_assistant_config(tenant", plan)
        tenant = (CONTROL / "pearnly_control/doctype/pearnly_tenant/pearnly_tenant.py").read_text(encoding="utf-8")
        self.assertIn("assistant.validate(self)", tenant)
        self.assertIn("self.flags.assistant_changed", tenant)
        issue = (CONTROL / "pearnly_control/doctype/pearnly_issue/pearnly_issue.py").read_text(encoding="utf-8")
        self.assertIn("assistant.validate(self)", issue)
        self.assertIn("assistant.read(doc)", (CONTROL / "issues.py").read_text(encoding="utf-8"))
        hooks = (CONTROL / "hooks.py").read_text(encoding="utf-8")
        for doctype in ("Pearnly Tenant", "Pearnly Issue"):
            self.assertIn(f'"{doctype}": "public/js/assistant_settings.js"', hooks)
        for doctype in ("pearnly_tenant", "pearnly_issue"):
            meta = json.loads((CONTROL / f"pearnly_control/doctype/{doctype}/{doctype}.json").read_text(encoding="utf-8"))
            fields = {f["fieldname"]: f for f in meta["fields"]}
            self.assertEqual(fields["assistant_api_key"]["fieldtype"], "Password")
            for name in ("assistant_engine", "assistant_model"):
                self.assertEqual(fields[name]["fieldtype"], "Select")
                self.assertNotIn("options", fields[name])  # 选项来自引擎目录，不写死在表单定义里


if __name__ == "__main__":
    unittest.main()
