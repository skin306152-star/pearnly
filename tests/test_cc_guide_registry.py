"""CC 引导声明：形状契约、注册表与闸门（总设计 ``docs/work/2026-11-14-cc-guided-master-design.md`` 2.2、2.4；P0 第 2 个提交）。

断言的是应有状态：
- 一份声明只认登记过的键与取值：多一个键、少一个必填、输入方式不在九种里、依赖了后面的步骤，都说得出哪里不对；
- 按表单的引导必须有步骤与确定后的动作，会改账本的还要有复核；只有开场一句的（chat）不许带步骤，也不算完整引导；
- 注册表：只列这个模式能办、这个人有权限的；坏声明不上菜单也不拖垮别的；同一个 id 声明两次算坏；
- 仓库里现有的声明全部过闸；与第 1 期的 hooks ``pearnly_guide_businesses`` 逐产品逐模式一致（菜单改读注册表前后不变）；
- 闸门认得出：点路径指向不存在的函数、工具不在这个产品里、两个产品同名业务的菜单字不一致。

替身 frappe，不连库。

    python3 tests/test_cc_guide_registry.py
"""
import ast
import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/pearnly_core"))

HOOKS = {}
LOGGED = []
ATTRS = {}
frappe = types.ModuleType("frappe")
frappe.get_hooks = lambda name: HOOKS.get(name, [])
frappe.get_attr = lambda path: ATTRS[path]
frappe.log_error = lambda title="", message="": LOGGED.append((title, message))
frappe.session = types.SimpleNamespace(user="u@example.com")
sys.modules["frappe"] = frappe

from pearnly_core.assistant.guide import declaration as d, registry  # noqa: E402

_spec = importlib.util.spec_from_file_location("check_cc_guides", ROOT / "scripts/check-cc-guides.py")
gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate)

FORM = {"id": "collection", "label_key": "guide.business.collection", "icon": "wallet", "order": 10, "modes": ["chat"],
        "ledgers": ["express_local"], "opening_key": "guide.open.collection", "source": "form", "writes": True,
        "steps": [{"key": "customer", "input": "choice", "rule": "always", "search": True, "options": "app.mod.customers"},
                  {"key": "invoices", "input": "amounts", "rule": "always", "after": ["customer"], "options": "app.mod.invoices"},
                  {"key": "method", "input": "split", "rule": "always", "options": "app.mod.methods"},
                  {"key": "voucher", "input": "file", "rule": "always", "when": {"key": "method", "has": "transfer"}},
                  {"key": "staff", "input": "choice", "rule": "auto", "options": "app.mod.staff", "default": "app.mod.staff_default"}],
        "review": "app.mod.review", "confirm": "app.mod.release", "result": "app.mod.status"}
DOCS = {"id": "purchase", "label_key": "guide.business.purchase", "icon": "shopping-cart", "order": 20, "modes": ["chat", "operator"],
        "ledgers": ["express_local", "mrerp"], "opening_key": "guide.open.purchase", "source": "documents", "writes": True,
        "steps": [{"key": "documents", "input": "file", "rule": "always"}]}
CHAT = {"id": "vat", "label_key": "guide.business.vat", "icon": "percent", "order": 50, "modes": ["chat"],
        "ledgers": ["express_local"], "opening_key": "guide.open.vat", "source": "chat", "writes": False}


def changed(base, **over):
    out = {k: v for k, v in base.items() if over.get(k, 0) is not None}
    out.update({k: v for k, v in over.items() if v is not None})
    return out


class ShapeTests(unittest.TestCase):
    def test_the_three_kinds_of_guide_are_well_formed(self):
        for guide in (FORM, DOCS, CHAT):
            self.assertEqual(d.problems(guide), [], guide["id"])

    def test_an_unknown_key_or_value_is_named(self):
        self.assertIn("collection: unknown key 'colour'", d.problems(changed(FORM, colour="red")))
        self.assertTrue(any("icon" in p for p in d.problems(changed(FORM, icon="rocket"))))
        self.assertTrue(any("modes" in p for p in d.problems(changed(FORM, modes=["chat", "line"]))))
        self.assertTrue(any("ledgers" in p for p in d.problems(changed(FORM, ledgers=[]))))
        self.assertTrue(any("source" in p for p in d.problems(changed(FORM, source="wizard"))))
        self.assertTrue(any("writes" in p for p in d.problems(changed(FORM, writes=None))))
        self.assertEqual(d.problems({"id": "Bad Id"}), ["id 'Bad Id' is not an id"])

    def test_a_step_takes_one_of_the_eleven_inputs_and_names_only_earlier_steps(self):
        self.assertEqual(len(d.INPUTS), 11)
        bad = changed(FORM, steps=[{"key": "customer", "input": "dropdown", "rule": "always"}])
        self.assertTrue(any("input 'dropdown'" in p for p in d.problems(bad)))
        later = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "always", "after": ["second"]},
                                     {"key": "second", "input": "month", "rule": "always"}])
        self.assertTrue(any("step first: after" in p for p in d.problems(later)))
        twice = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "always"}, {"key": "first", "input": "date", "rule": "always"}])
        self.assertTrue(any("used twice" in p for p in d.problems(twice)))
        when = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "always"},
                                    {"key": "second", "input": "file", "rule": "always", "when": {"key": "first", "value": "x", "has": "y"}}])
        self.assertTrue(any("step second: when" in p for p in d.problems(when)))

    def test_options_default_and_remember_follow_the_input_and_the_ask_rule(self):
        no_options = changed(FORM, steps=[{"key": "first", "input": "choice", "rule": "always"}])
        self.assertTrue(any("needs options" in p for p in d.problems(no_options)))
        stray = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "always", "options": "app.mod.fn"}])
        self.assertTrue(any("takes no options" in p for p in d.problems(stray)))
        search = changed(FORM, steps=[{"key": "first", "input": "split", "rule": "always", "options": "app.mod.fn", "search": True}])
        self.assertTrue(any("search" in p for p in d.problems(search)))
        auto = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "auto"}])
        self.assertTrue(any("rule auto needs default" in p for p in d.problems(auto)))
        remember = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "remember"}])
        self.assertTrue(any("rule remember needs remember" in p for p in d.problems(remember)))
        ok = changed(FORM, steps=[{"key": "first", "input": "month", "rule": "remember", "remember": "company"}])
        self.assertEqual(d.problems(ok), [])

    def test_n_capabilities_of_11_16_are_shaped(self):
        """引擎通用能力 N1–N10（约定 11-16）：子题输入要选项来源、零只在金额多选、记住范围有工作空间、允许卡只给写的、追问要有结果。"""
        questions = changed(FORM, steps=[{"key": "layout", "input": "questions", "rule": "always", "options": "app.mod.layout"}])
        self.assertEqual(d.problems(questions), [])
        self.assertTrue(any("needs options" in p for p in d.problems(
            changed(FORM, steps=[{"key": "layout", "input": "questions", "rule": "always"}]))))
        counted = changed(FORM, steps=[{"key": "rows", "input": "amounts", "rule": "always", "options": "app.mod.items",
                                        "search": True, "zero": True}])
        self.assertEqual(d.problems(counted), [])
        self.assertTrue(any("zero" in p for p in d.problems(
            changed(FORM, steps=[{"key": "rows", "input": "split", "rule": "always", "options": "app.mod.x", "zero": True}]))))
        self.assertEqual(d.problems(changed(FORM, steps=[{"key": "at", "input": "time", "rule": "always"}])), [])
        self.assertEqual(d.problems(changed(FORM, steps=[{"key": "kind", "input": "month", "rule": "remember",
                                                          "remember": "workspace"}])), [])
        self.assertEqual(d.problems(changed(FORM, consent="allow")), [])
        self.assertTrue(any("consent" in p for p in d.problems(changed(FORM, consent="allow", writes=False))))
        self.assertTrue(any("consent" in p for p in d.problems(changed(FORM, consent="sure"))))
        by_action = {"key": "customer", "in": ["C9"]}
        self.assertEqual(d.problems(changed(FORM, consent=by_action)), [])
        for bad in ({"key": "nowhere", "in": ["x"]}, {"key": "customer"}, {"key": "customer", "in": []},
                    {"key": "customer", "value": "x", "has": "y"}, {"key": "customer", "in": ["x"], "extra": 1}):
            self.assertTrue(any("consent" in p for p in d.problems(changed(FORM, consent=bad))), bad)
        self.assertTrue(any("consent" in p for p in d.problems(changed(FORM, consent=by_action, writes=False))))
        followed = changed(FORM, follow="app.mod.answer")
        self.assertEqual(d.problems(followed), [])
        self.assertIn("app.mod.answer", d.paths(followed))
        self.assertTrue(any("follow needs result" in p for p in d.problems(
            {key: value for key, value in followed.items() if key != "result"})))

    def test_a_form_guide_needs_steps_a_confirm_and_a_review_when_it_writes(self):
        self.assertTrue(any("needs steps" in p for p in d.problems(changed(FORM, steps=[]))))
        self.assertTrue(any("needs confirm" in p for p in d.problems(changed(FORM, confirm=None))))
        self.assertTrue(any("needs review" in p for p in d.problems(changed(FORM, review=None))))
        self.assertEqual(d.problems(changed(FORM, review=None, writes=False)), [])

    def test_a_documents_guide_declares_only_file_steps_and_a_chat_guide_none(self):
        asked = changed(DOCS, steps=[{"key": "payment", "input": "choice", "rule": "always", "options": "app.mod.fn"}])
        self.assertTrue(any("only declares file steps" in p for p in d.problems(asked)))
        self.assertTrue(any("chat guide has no steps" in p for p in d.problems(changed(CHAT, confirm="app.mod.go"))))
        self.assertTrue(d.complete(FORM) and d.complete(DOCS))
        self.assertFalse(d.complete(CHAT))

    def test_paths_lists_every_dotted_path_of_a_guide(self):
        self.assertEqual(sorted(d.paths(changed(FORM, allowed="app.mod.allowed"))),
                         sorted(["app.mod.allowed", "app.mod.review", "app.mod.release", "app.mod.status", "app.mod.customers",
                                 "app.mod.invoices", "app.mod.methods", "app.mod.staff", "app.mod.staff_default"]))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        HOOKS.clear(), LOGGED.clear(), ATTRS.clear()

    def declare(self, *guides, **attrs):
        HOOKS["pearnly_guides"] = ["app.guides.GUIDES"]
        ATTRS["app.guides.GUIDES"] = list(guides)
        ATTRS.update(attrs)

    def test_the_menu_lists_what_this_mode_can_do_in_order(self):
        self.declare(DOCS, CHAT, changed(FORM, allowed=None))
        self.assertEqual([item["id"] for item in registry.menu("chat")], ["collection", "purchase", "vat"])
        self.assertEqual([item["id"] for item in registry.menu("operator")], ["purchase"])
        self.assertEqual(registry.offered(), {"chat": ["collection", "purchase", "vat"], "operator": ["purchase"]})
        self.assertEqual(registry.menu("chat")[0], {"id": "collection", "label_key": "guide.business.collection", "icon": "wallet",
                                                    "order": 10, "source": "form"})
        self.assertEqual(registry.find("purchase")["source"], "documents")
        self.assertIsNone(registry.find("payment"))

    def test_a_business_shows_only_to_people_who_have_that_function(self):
        asked = []
        self.declare(changed(FORM, allowed="app.access.collector"), DOCS,
                     **{"app.access.collector": lambda user: asked.append(user) or False})
        self.assertEqual([item["id"] for item in registry.menu("chat")], ["purchase"])
        self.assertEqual(asked, ["u@example.com"])
        ATTRS["app.access.collector"] = lambda user: True
        self.assertEqual([item["id"] for item in registry.menu("chat")], ["collection", "purchase"])

    def test_a_failing_permission_check_hides_the_business_and_is_logged(self):
        def boom(user):
            raise RuntimeError("down")
        self.declare(changed(FORM, allowed="app.access.collector"), DOCS, **{"app.access.collector": boom})
        self.assertEqual([item["id"] for item in registry.menu("chat")], ["purchase"])
        self.assertEqual(LOGGED[-1][0], "Assistant guide permission check failed")

    def test_a_bad_or_repeated_declaration_stays_off_the_menu_without_breaking_the_rest(self):
        self.declare(DOCS, changed(CHAT, icon="rocket"), dict(DOCS))
        self.assertEqual([guide["id"] for guide in registry.declared()], ["purchase"])
        self.assertIn("vat: icon 'rocket' is not on the icon list", LOGGED[-1][1])
        self.assertIn("purchase: declared twice", LOGGED[-1][1])

    def test_no_declaration_means_no_menu(self):
        self.assertEqual(registry.offered(), {"chat": [], "operator": []})


# 第 1 期菜单（`pearnly_guide_businesses`）还没进 main 时的应有菜单；它进来后两处必须一致（菜单改读注册表前后不变）
PHASE_ONE_MENU = {"pearnly_cowork": {"chat": ["purchase", "sales"], "operator": ["purchase", "sales"]},
                  "pearnly_erp_portal": {"chat": ["purchase", "sales", "collection"], "operator": ["purchase", "sales"]}}


def _phase_one_businesses(app):
    tree = ast.parse((ROOT / "apps" / app / app / "hooks.py").read_text(encoding="utf-8"))
    try:
        return ast.literal_eval(gate._assigned(tree, "pearnly_guide_businesses"))
    except (TypeError, ValueError, AttributeError):
        return PHASE_ONE_MENU[app]


class RepositoryTests(unittest.TestCase):
    def test_every_declared_guide_passes_the_gate(self):
        self.assertEqual(gate.check(), [])

    def test_the_declarations_offer_exactly_what_phase_one_hooks_offer(self):
        guides, errors = gate.load()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(guides), ["pearnly_cowork", "pearnly_erp_portal"])
        for app, items in guides.items():
            for mode, wanted in _phase_one_businesses(app).items():
                if (app, mode) == ("pearnly_erp_portal", "chat"):
                    # 主会话 11-14：ERP 直写的采购 / 销售整链没走通过，从菜单拿掉、只留收款（P2 验过再放）。第 1 期的 hook 由它的窗口改，
                    # 改完这一条与别的组合一样逐项相同
                    wanted = [w for w in wanted if w == "collection"]
                self.assertEqual(sorted(g["id"] for g in items if mode in g["modes"]), sorted(wanted), (app, mode))

    def test_collection_is_only_in_erp_direct_write_and_asks_for_the_collection_function(self):
        guides, _ = gate.load()
        self.assertFalse([g for g in guides["pearnly_cowork"] if g["id"] == "collection"])
        collection = next(g for g in guides["pearnly_erp_portal"] if g["id"] == "collection")
        self.assertEqual(collection["modes"], ["chat"])
        self.assertEqual(collection["allowed"], "pearnly_erp_portal.collection.cc_guide.allowed")


def _tree(files):
    root = Path(tempfile.mkdtemp())
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _app(name, guides, extra=""):
    return {f"apps/{name}/{name}/hooks.py": f'pearnly_guides = ["{name}.guides.GUIDES"]\n',
            f"apps/{name}/{name}/guides.py": f"GUIDES = {guides!r}\n{extra}",
            f"apps/{name}/{name}/tools.py": 'TOOLS = [{"name": "%s_push", "handler": "x"}]\n' % name}


class GateTests(unittest.TestCase):
    def test_a_path_to_a_missing_function_and_a_tool_of_another_product_are_caught(self):
        guide = changed(FORM, tool="other_push")
        errors = gate.check(_tree(_app("app", [guide], "def customers(ctx):\n    return {}\n")))
        self.assertIn("app: collection: app.mod.invoices does not exist", errors)
        self.assertIn("app: collection: tool other_push is not declared in this product", errors)

    def test_a_guide_whose_functions_and_tool_exist_passes(self):
        guide = changed(CHAT, allowed="app.guides.allowed", tool="app_push")
        self.assertEqual(gate.check(_tree(_app("app", [guide], "def allowed(user):\n    return True\n"))), [])

    def test_the_same_business_in_two_products_shows_the_same_menu_entry(self):
        files = {**_app("one", [DOCS]), **_app("two", [changed(DOCS, label_key="guide.business.buying")])}
        self.assertIn("purchase: label_key differs between one and two", gate.check(_tree(files)))

    def test_a_declaration_that_is_not_a_plain_literal_is_refused(self):
        files = _app("app", [])
        files["apps/app/app/guides.py"] = "GUIDES = [make()]\n"
        self.assertEqual(gate.check(_tree(files)), ["app: app.guides.GUIDES is not a plain literal"])


if __name__ == "__main__":
    unittest.main()
