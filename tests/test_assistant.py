"""智能体底座（ADR-019）的离线检查：纯约定、引擎目录与适配器、工具声明与智能体说明同源、
按设置自动备好远端资源不重复建、管理后台的设置校验。

不连库、不连厂商：Frappe 与 SDK 用最小替身。
"""
import importlib.util
import inspect
import json
import os
import pathlib
import re
import sys
import types
import unittest
from unittest import mock

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

    def test_settings_tools_wait_for_allow_and_run_once_as_the_user(self):
        """改共用设置的工具先拦下等「允许」（用户 2026-10-10：经授权后代为操作）；允许只执行这一次，拒绝不执行。"""
        toolrun = sys.modules["pearnly_core.assistant.toolrun"]
        declared = [{"name": "always", "approval": True}, {"name": "when_writing", "approval": ["acccod"]},
                    {"name": "plain"}]
        with mock.patch.object(tools, "declared", return_value=declared):
            self.assertTrue(tools.needs_approval("always", {}))
            self.assertFalse(tools.needs_approval("when_writing", {"connection": "E1"}))  # 只是查看
            self.assertTrue(tools.needs_approval("when_writing", {"acccod": "1150"}))
            self.assertFalse(tools.needs_approval("plain", {"x": 1}))
        rows = {"r1": {"status": "awaiting_approval", "tool": "always", "tool_input": '{"task": "T1"}'}}
        posted, ran = [], []

        class Db:
            _cursor = types.SimpleNamespace(rowcount=0)

            def get_value(self, doctype, filters, field, as_dict=False):
                if isinstance(filters, dict):
                    return next((n for n, r in rows.items() if r["status"] == filters.get("status", r["status"])), None)
                row = rows[filters]
                return types.SimpleNamespace(**row) if as_dict else row[field]

            def sql(self, query, values):
                status, name = values
                hit = rows[name]["status"] == "awaiting_approval"
                if hit:
                    rows[name]["status"] = status
                self._cursor = types.SimpleNamespace(rowcount=int(hit))

            def set_value(self, doctype, name, field, value=None):
                rows[name].update(field if isinstance(field, dict) else {field: value})

            def commit(self):
                pass

            rollback = commit

        adapter = types.SimpleNamespace(post=lambda remote, events: posted.append(events[0]))
        with mock.patch.object(toolrun, "_use_lang", lambda conversation: None), \
                mock.patch.object(toolrun.frappe, "db", Db(), create=True), \
                mock.patch.object(toolrun.frappe, "local", types.SimpleNamespace(cache={}), create=True), \
                mock.patch.object(toolrun.engine, "adapter", return_value=adapter), \
                mock.patch.object(toolrun.tools, "run", lambda name, data: ran.append((name, data)) or ("saved", False)):
            self.assertEqual(toolrun.decide("conv", "sesn", "sevt_1", True), "done")
            self.assertEqual(toolrun.decide("conv", "sesn", "sevt_1", True), "not_waiting")  # 重复点击不再执行
        self.assertEqual(ran, [("always", {"task": "T1"})])
        self.assertEqual((rows["r1"]["status"], rows["r1"]["result"]), ("done", "saved"))
        self.assertEqual(len(posted), 1)
        # 卡片在点之前说清改什么：工具的 preview 现算；没写就列参数；ask 没写用步骤名
        declared = [{"name": "always", "label": "Saved", "ask": "Save?", "approval": True, "preview": "x.preview"},
                    {"name": "plain", "label": "Did", "approval": True}]
        with mock.patch.object(tools, "declared", return_value=declared), \
                mock.patch.object(tools.frappe, "get_attr", return_value=lambda data: [{"field": "Day", "before": "1", "after": "5"}], create=True):
            self.assertEqual(tools.approval_view("always", {"run_day": 5}),
                             {"ask": "Save?", "preview": [{"field": "Day", "before": "1", "after": "5"}]})
            self.assertEqual(tools.approval_view("plain", {"task": "T1"}),
                             {"ask": "Did", "preview": [{"field": "task", "before": "", "after": "T1"}]})
        # 业务上没办成（status not_saved 等）也按出错交回，对话里那一步显示失败
        self.assertTrue(tools._refused('{"status": "not_saved", "reason": "x"}'))
        self.assertFalse(tools._refused('{"status": "saved"}'))
        self.assertFalse(tools._refused("plain text"))
        source = (CORE / "assistant/sessions.py").read_text(encoding="utf-8")
        self.assertIn("tools.approval_view(", source.split("def run_tool(", 1)[1])
        # 结果面板预览 PDF：用 inline 的 pdf 回应，不是附件（binary 会变成下载、面板空白）
        files_source = (CORE / "assistant/files.py").read_text(encoding="utf-8").split("def download(", 1)[1]
        self.assertIn('frappe.local.response.type = "pdf"', files_source)
        confirm = source.split("def confirm(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("toolrun.decide(", confirm)  # 我们拦下的在本站处理，其余照旧交引擎

    def test_engine_callbacks_are_verified_and_find_the_waiting_calls(self):
        """页面关着由服务端接手（清单第 16 项）：回调按 Standard Webhooks 验签（官方示例向量），找出最近一次等待的调用。"""
        headers = {"Webhook-Id": "msg_p5jXN8AQM9LWM0D4loKWxJek", "Webhook-Timestamp": "1614265330",
                   "Webhook-Signature": "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="}
        secret, body = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw", '{"test": 2432232314}'
        contracts.verify_webhook(secret, headers, body, 1614265330)
        for bad in ({**headers, "Webhook-Signature": "v1,AAAA"}, {k: v for k, v in headers.items() if k != "Webhook-Id"}):
            with self.assertRaises(contracts.ContractError):
                contracts.verify_webhook(secret, bad, body, 1614265330)
        with self.assertRaises(contracts.ContractError):
            contracts.verify_webhook(secret, headers, body, 1614265330 + 301)  # 超过 5 分钟：防重放
        newest_first = [{"type": "session.status_idle", "stop_reason": {"type": "requires_action", "event_ids": ["e2", "e3"]}},
                        {"type": "agent.custom_tool_use", "id": "e3", "name": "b"},
                        {"type": "agent.custom_tool_use", "id": "e2", "name": "a"},
                        {"type": "agent.custom_tool_use", "id": "e1", "name": "old"}]
        self.assertEqual([c["id"] for c in contracts.pending_calls(newest_first)], ["e2", "e3"])
        self.assertEqual(contracts.pending_calls([{"type": "session.status_running"}] + newest_first), [])
        self.assertEqual(contracts.pending_calls([{"type": "session.status_idle", "stop_reason": {"type": "end_turn"}}]), [])
        self.assertTrue(contracts.site_host("testcowork.pearnly.com"))
        for bad in ("http://x.com", "x.com/a", "x.com:8000", "localhost", ""):
            self.assertFalse(contracts.site_host(bad), bad)
        self.assertEqual(claude.Adapter.wake({"data": {"type": "session.status_idled", "id": "sesn_abcd1234"}}),
                         "sesn_abcd1234")
        self.assertIsNone(claude.Adapter.wake({"data": {"type": "agent.created", "id": "agent_1234"}}))
        self._receive_flow()
        source = (CORE / "assistant/webhook.py").read_text(encoding="utf-8")
        run = (CORE / "assistant/toolrun.py").read_text(encoding="utf-8")
        self.assertIn("_use_lang(conversation)", run.split("def _finish(", 1)[1])  # 后台执行按对话的语言出结果
        sessions_src = (CORE / "assistant/sessions.py").read_text(encoding="utf-8").split("def send(", 1)[1]
        self.assertLess(sessions_src.index("toolrun.remember_lang("), sessions_src.index("api.post("))
        self.assertIn("allow_redirects=False", source)  # 3xx 会让引擎停用回调地址
        self.assertIn("toolrun.execute(", source)  # 与推送线程、页面兜底共用执行记录，只执行一遍

    def _receive_flow(self):
        """回调：验签过、同一事件只处理一次、本站的对话排后台任务以会话主人执行；验签不过回 401。"""
        import base64
        import hashlib
        import hmac

        sys.modules.setdefault("pearnly_core.execution", types.ModuleType("pearnly_core.execution"))
        seen = set()
        store = types.SimpleNamespace(make_key=lambda k: "t1|" + k,
                                      set=lambda key, value, nx=False, ex=None: (key not in seen) and not seen.add(key))
        state = types.ModuleType("pearnly_core.execution.state")
        state.store = lambda: store
        sys.modules["pearnly_core.execution.state"] = sys.modules["pearnly_core.execution"].state = state
        webhook = _load("pearnly_core.assistant.webhook", CORE / "assistant/webhook.py")
        secret = "whsec_" + base64.b64encode(b"k" * 32).decode()
        body = '{"type": "event", "id": "whe_0001abcd", "data": {"type": "session.status_idled", "id": "sesn_abcd1234"}}'
        now = int(webhook.time.time())
        sign = base64.b64encode(hmac.new(b"k" * 32, f"msg_1.{now}.{body}".encode(), hashlib.sha256).digest()).decode()
        headers = {"webhook-id": "msg_1", "webhook-timestamp": str(now), "webhook-signature": "v1," + sign}
        queued, response = [], types.SimpleNamespace()
        fake = types.SimpleNamespace(
            conf={"pearnly_assistant_webhook_secret": secret}, local=types.SimpleNamespace(site="t1", response=response),
            request=types.SimpleNamespace(get_data=lambda as_text=True: body, headers=headers),
            db=types.SimpleNamespace(get_value=lambda *a, **k: types.SimpleNamespace(
                session_id="sesn_abcd1234", user="owner@example.com", engine_code="anthropic-managed-agents")),
            enqueue=lambda method, **kw: queued.append(kw), whitelist=frappe.whitelist)
        adapter = types.SimpleNamespace(wake=lambda event: event["data"]["id"])
        with mock.patch.object(webhook, "frappe", fake), mock.patch.object(webhook.engine, "adapter", return_value=adapter), \
                mock.patch.object(webhook.config, "engine_code", return_value="anthropic-managed-agents"):
            self.assertEqual(webhook.receive(), {"status": "queued"})
            self.assertEqual(webhook.receive(), {"status": "duplicate"})  # 引擎重送同一事件
            self.assertEqual((queued[0]["user"], queued[0]["remote"]), ("owner@example.com", "sesn_abcd1234"))
            headers["webhook-signature"] = "v1,AAAA"
            self.assertEqual(webhook.receive(), {"status": "rejected"})
            self.assertEqual(response.http_status_code, 401)

    def test_tools_can_reach_this_conversations_attachments_and_only_those(self):
        # 引擎不让下载用户上传的文件：原件在本站留一份挂在会话上；工具执行时知道是哪个对话，只取这个对话的附件。
        files = (CORE / "assistant/files.py").read_text(encoding="utf-8")
        upload = files.split("def upload(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn('_keep(row, stored["file_id"], name, content)', upload)
        kept = files.split("def _kept(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("sessions.owned(", kept)
        self.assertIn('"attached_to_name": row.name', kept)
        for name in ("attachment(", "attachments("):
            self.assertIn("_kept(", files.split("def " + name, 1)[1].split("\ndef ", 1)[0])
        run = (CORE / "assistant/toolrun.py").read_text(encoding="utf-8")
        self.assertIn('frappe.local.pearnly_assistant = {"session_id": conversation}', run)
        self.assertIn("frappe.local.pearnly_assistant = None", run.split("finally:", 1)[1])
        self.assertIn("frappe.UniqueValidationError", run)  # 推送线程与页面同时执行：唯一冲突不弹到页面上
        self.assertIn("frappe.clear_messages()", run.split("UniqueValidationError", 1)[1].split("return", 1)[0])

    def test_existing_conversations_get_the_current_tools(self):
        # 会话绑定建时的智能体版本；工具可在会话中整组替换：每轮先核对，旧对话也能用新工具；新建的直接记为最新
        source = (CORE / "assistant/sessions.py").read_text(encoding="utf-8")
        turn = source.split("def segment_for_turn(", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("engine.keep_tools_current(api, remote)", turn)
        self.assertIn("engine.tools_current(fresh)", turn)
        self.assertIn("engine.tools_current(sid)", source)
        adapter = (CORE / "assistant/engines/anthropic_managed_agents.py").read_text(encoding="utf-8")
        self.assertIn('sessions.update(conversation, agent={"tools": spec(brief, self.model)["tools"]})', adapter)
        base = (CORE / "assistant/engines/base.py").read_text(encoding="utf-8")
        self.assertIn("def refresh(self, conversation: str, brief: dict) -> bool:", base)

    def test_attachment_copies_live_in_a_private_folder_per_conversation(self):
        # 不进 File 表：同一张票识别过以后，同内容的 File 会被 Cowork 原件守卫拒绝（2026-10-10 测试第二次发同一张票失败）
        import tempfile

        root = tempfile.mkdtemp()
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.SESSION = "Pearnly Assistant Session"
        sessions.owned = lambda sid, allow_archived=False: types.SimpleNamespace(name={"s1": "row1", "s2": "row2"}[sid])
        sessions._guard = lambda fn, *a: fn(*a)
        sys.modules["pearnly_core.assistant.sessions"] = sys.modules["pearnly_core.assistant"].sessions = sessions
        sys.modules.setdefault("pearnly_core.desk", types.ModuleType("pearnly_core.desk"))
        sys.modules["pearnly_core.desk.heic"] = sys.modules["pearnly_core.desk"].heic = types.ModuleType("heic")
        files = _load("pearnly_core.assistant.files", CORE / "assistant/files.py")
        saved = (getattr(frappe, "get_site_path", None), getattr(frappe, "get_all", None))
        frappe.get_site_path = lambda *parts: os.path.join(root, *parts)
        frappe.get_all = lambda *a, **k: []
        try:
            files._keep(types.SimpleNamespace(name="row1"), "file_A", "6800951.jpg", b"jpeg-bytes")
            files._keep(types.SimpleNamespace(name="row2"), "file_B", "6800951.jpg", b"jpeg-bytes")  # 同内容再发一次
            frappe.local.pearnly_assistant = {"session_id": "s1"}
            self.assertEqual(files.attachment("file_A"), ("6800951.jpg", b"jpeg-bytes"))
            # 挂载路径、文件名也认成本对话的那个附件
            self.assertEqual(files.attachment("/mnt/session/uploads/file_A/6800951.jpg")[0], "6800951.jpg")
            self.assertEqual(files.attachment("6800951.jpg")[0], "6800951.jpg")
            self.assertEqual(files.attachments(), [{"file_id": "file_A", "filename": "6800951.jpg"}])
            with self.assertRaises(Exception):
                files.attachment("file_B")  # 另一个对话的附件拿不到
            self.assertFalse(os.path.exists(os.path.join(root, "public")))
        finally:
            frappe.local.pearnly_assistant = None
            for name, value in zip(("get_site_path", "get_all"), saved):
                if value is None:
                    delattr(frappe, name)
                else:
                    setattr(frappe, name, value)

    def test_session_files_are_listed_oldest_first_so_the_panel_opens_the_newest(self):
        # 引擎列文件新的在前；面板默认打开最后一个（2026-10-10 测试：新导出出来面板停在旧的那份）
        sessions = types.ModuleType("pearnly_core.assistant.sessions")
        sessions.owned = lambda sid, allow_archived=False: "row"
        sessions.segments = lambda row: ["seg1", "seg2"]
        sys.modules["pearnly_core.assistant.sessions"] = sys.modules["pearnly_core.assistant"].sessions = sessions
        sys.modules.setdefault("pearnly_core.desk", types.ModuleType("pearnly_core.desk"))
        sys.modules["pearnly_core.desk.heic"] = sys.modules["pearnly_core.desk"].heic = types.ModuleType("heic")
        files = _load("pearnly_core.assistant.files", CORE / "assistant/files.py")
        listed = {"seg1": [{"id": "b", "created_at": "2026-10-10 22:05:00"}, {"id": "a", "created_at": "2026-10-10 21:00:00"}],
                  "seg2": [{"id": "c", "created_at": "2026-10-10 22:17:32"}]}
        with mock.patch.object(files.engine, "adapter", return_value=types.SimpleNamespace(files=listed.get)):
            self.assertEqual([f["id"] for f in files.listing("s1")], ["a", "b", "c"])

    def test_titles_leave_out_the_attachment_note(self):
        note = "\n\n[Attached file, mounted read-only in the sandbox:\n- r.jpg → /mnt/session/uploads/file_1/r.jpg]"
        self.assertEqual(contracts.title_from("识别后结果发给我" + note), "识别后结果发给我")
        self.assertEqual(contracts.title_from(note.strip()), "r.jpg")
        self.assertEqual(contracts.title_from("a   b"), "a b")

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
                      "pearnly_assistant_api_key=key,", branch)
        # 引擎回调签名密钥随开通一起写；未开通就清空
        self.assertIn('pearnly_assistant_webhook_secret=str(p.get("webhook_secret") or "") if engine else ""', branch)
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
