"""ERP 任务管理（看板，2026-10-23 从 Cowork 复制进 ERP 自有 App）的离线检查：不连站点、不连库。

- 看板权限矩阵（`board_rights` / `member_changes`）与 Cowork 逐个组合比对：ERP 照 Cowork 同一口径隔离用户，改一边要同改另一边；
- ERP 的判据（`work.access`）：未登录、站点不是 ERP、没买「任务管理」、账号停用、没勾「任务管理」一律拒绝；勾了的员工只看得到
  团队可见的板和自己是成员的私有板，团队可见的板上没加进来的人只读，ERP 管理员管得了所有看板；原生资源只给读；分享被拒；
  卡片附件只给能看那块板的人；
- 声明：侧栏「销售系统」下方单独一项、功能目录、岗位 / 能力、团队管理勾选分组；只勾任务管理的员工不因此看得到商品与库存；
- ERP 不导入 Cowork（ERP 站点不装 Cowork）。
"""
import ast
import importlib.util
import itertools
import json
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
ERP = ROOT / "apps/pearnly_erp_portal/pearnly_erp_portal"
COWORK = ROOT / "apps/pearnly_cowork/pearnly_cowork"
sys.path[:0] = [str(ROOT / "apps/pearnly_erp_portal"), str(ROOT / "apps/pearnly_core")]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ERP_CONTRACTS = _load(ERP / "work/contracts.py", "erp_work_contracts")
COWORK_CONTRACTS = _load(COWORK / "work/contracts.py", "cowork_work_contracts")


def _declaration():
    tree = ast.parse((ERP / "hooks.py").read_text())
    node = next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "pearnly_navigation" for t in node.targets))
    return ast.literal_eval(node.value)[0]


class SameRulesAsCowork(unittest.TestCase):
    def test_the_permission_vocabulary_is_the_same(self):
        for name in ("VISIBILITIES", "ROLES", "COMMENTING", "MEMBER_MANAGEMENT", "RIGHTS"):
            with self.subTest(name=name):
                self.assertEqual(getattr(ERP_CONTRACTS, name), getattr(COWORK_CONTRACTS, name))

    def test_board_rights_match_for_every_combination(self):
        erp, cowork = ERP_CONTRACTS, COWORK_CONTRACTS
        combos = itertools.product((None, *erp.ROLES), erp.VISIBILITIES, (False, True), (False, True),
                                   erp.COMMENTING, erp.MEMBER_MANAGEMENT, (False, True))
        count = 0
        for role, visibility, team_user, admin, commenting, management, default in combos:
            common = {"role": role, "visibility": visibility, "team_user": team_user, "commenting": commenting,
                      "member_management": management, "is_default": default}
            self.assertEqual(erp.board_rights(**common, product_admin=admin),
                             cowork.board_rights(**common, cowork_admin=admin), common)
            count += 1
        self.assertEqual(count, 4 * 2 * 2 * 2 * 3 * 2 * 2)

    def test_member_changes_match(self):
        def outcome(module, *args, **kwargs):
            try:
                return module.member_changes(*args, **kwargs)
            except module.Invalid as error:
                return ("refused", error.args[0].rsplit(".", 1)[-1])

        current = {"a@x": "admin", "m@x": "member", "o@x": "observer"}
        wanted_options = [
            dict(current), {**current, "n@x": "member"}, {**current, "n@x": "admin"},
            {"a@x": "admin", "m@x": "member"}, {"m@x": "member", "o@x": "observer"},
            {"a@x": "admin", "m@x": "observer", "o@x": "observer"}, {"a@x": "member", "m@x": "member"},
            {"m@x": "member", "o@x": "observer", "a@x": "admin", "x@x": "boss"},
        ]
        for wanted, actor, manage, can_members in itertools.product(
                wanted_options, ("a@x", "m@x", "o@x"), (False, True), (False, True)):
            args = (current, wanted)
            kwargs = {"actor": actor, "manage": manage, "can_members": can_members}
            with self.subTest(wanted=wanted, **kwargs):
                self.assertEqual(outcome(ERP_CONTRACTS, *args, **kwargs), outcome(COWORK_CONTRACTS, *args, **kwargs))


class Doc(types.SimpleNamespace):
    def get(self, key, default=None):
        return getattr(self, key, default)


class FakeDB:
    def __init__(self, frappe):
        self.frappe = frappe

    def get_value(self, doctype, name, fields, as_dict=False, **kwargs):
        if doctype == "User":
            return self.frappe.users.get(name, {}).get("enabled")
        if doctype == "ERP Work Board":
            row = self.frappe.boards.get(name)
            return types.SimpleNamespace(name=name, **row) if row else None
        if doctype == "ERP Work Card":
            return self.frappe.cards.get(name, {}).get(fields)
        raise AssertionError((doctype, name, fields))

    def escape(self, value):
        return "'" + str(value).replace("'", "''") + "'"


class FakeFrappe(types.ModuleType):
    def __init__(self):
        super().__init__("frappe")
        self.conf = {"pearnly_product": "erp", "pearnly_features": ["sales", "tasks"]}
        self.session = types.SimpleNamespace(user="staff@x")
        self.local = types.SimpleNamespace()
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.AuthenticationError = type("AuthenticationError", (Exception,), {})
        self.users = {}
        self.boards = {}
        self.members = {}
        self.cards = {}
        self.db = FakeDB(self)

    def _(self, text, *args):
        return text

    def throw(self, message, exc=Exception):
        raise exc(message)

    def get_roles(self, user):
        if user == "Administrator":
            return ["System Manager", "ERP Admin", "ERP Tasks", "ERP Menu tasks"]
        return list(self.users.get(user, {}).get("roles", ()))

    def get_all(self, doctype, filters=None, pluck=None, fields=None, **kwargs):
        if doctype == "Has Role":
            wanted = set(filters["role"][1])
            return [user for user, row in self.users.items() if wanted & set(row.get("roles", ()))]
        if doctype == "User":
            return [user for user in filters["name"][1] if self.users.get(user, {}).get("enabled")]
        if doctype == "ERP Work Board Member":
            return [types.SimpleNamespace(user=user, role=role)
                    for user, role in self.members.get(filters["parent"], {}).items()]
        raise AssertionError((doctype, filters))


class Access(unittest.TestCase):
    def setUp(self):
        self.frappe = FakeFrappe()
        self.frappe.utils = types.ModuleType("frappe.utils")
        shell = types.ModuleType("pearnly_core.shell")
        shell.entitlements = lambda: list(self.frappe.conf.get("pearnly_features") or [])
        self.saved = {name: sys.modules.get(name) for name in ("frappe", "frappe.utils", "pearnly_core.shell")}
        sys.modules.update({"frappe": self.frappe, "frappe.utils": self.frappe.utils, "pearnly_core.shell": shell})
        for name in [name for name in sys.modules if name.startswith("pearnly_erp_portal.work")
                     or name in ("pearnly_erp_portal.access.guard",)]:
            del sys.modules[name]
        from pearnly_erp_portal.work import access
        self.access = access
        users = {
            "boss@x": ["ERP Admin", "ERP Tasks"],
            "staff@x": ["ERP Menu home", "ERP Menu tasks"],
            "other@x": ["ERP Menu home", "ERP Menu tasks"],
            "seller@x": ["ERP Menu home", "ERP Menu sales"],
            "legacy@x": ["ERP Tasks"],
            "gone@x": ["ERP Menu tasks"],
        }
        self.frappe.users = {user: {"roles": roles, "enabled": 0 if user == "gone@x" else 1}
                             for user, roles in users.items()}
        board = {"title": "", "commenting": "members", "member_management": "admins", "archived": 0}
        self.frappe.boards = {
            "default": {**board, "visibility": "team", "is_default": 1},
            "team": {**board, "visibility": "team", "is_default": 0},
            "secret": {**board, "visibility": "private", "is_default": 0},
            "closed": {**board, "visibility": "private", "is_default": 0, "archived": 1},
        }
        self.frappe.members = {"team": {"other@x": "admin"}, "secret": {"other@x": "admin"},
                               "closed": {"staff@x": "admin"}}
        self.frappe.cards = {"c-secret": {"board": "secret"}, "c-team": {"board": "team"}}

    def tearDown(self):
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def as_user(self, user):
        self.frappe.session.user = user
        self.access.forget()

    def test_who_can_open_task_management(self):
        self.assertTrue(self.access.allowed("staff@x"))  # 团队管理里勾了「任务管理」
        self.assertTrue(self.access.allowed("boss@x"))  # ERP 管理员
        self.assertTrue(self.access.allowed("legacy@x"))  # 开通时发的功能岗位
        self.assertFalse(self.access.allowed("seller@x"))  # 只勾了销售
        self.assertFalse(self.access.allowed("gone@x"))  # 账号停用
        self.assertFalse(self.access.allowed("Guest"))
        self.assertFalse(self.access.allowed("nobody@x"))

    def test_not_signed_in_is_refused(self):
        self.as_user("Guest")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require()

    def test_without_the_feature_nobody_gets_in(self):
        self.frappe.conf["pearnly_features"] = ["sales"]
        for user in ("staff@x", "boss@x", "legacy@x"):
            with self.subTest(user=user):
                self.assertFalse(self.access.allowed(user))
        self.assertEqual(self.access.team_users(), set())
        self.as_user("boss@x")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require()

    def test_only_an_erp_site_counts(self):
        self.frappe.conf["pearnly_product"] = "cowork"
        self.assertFalse(self.access.allowed("staff@x"))

    def test_team_users_are_the_enabled_people_with_task_management(self):
        self.assertEqual(self.access.team_users(), {"boss@x", "staff@x", "other@x", "legacy@x"})

    def test_board_rights_follow_members_and_visibility(self):
        rights = self.access.rights
        self.assertEqual(rights("default", "staff@x"), frozenset({"view", "edit", "comment"}))
        self.assertEqual(rights("team", "staff@x"), frozenset({"view"}))  # 没加进来：只读
        self.assertEqual(rights("secret", "staff@x"), frozenset())  # 别人的私有看板：看不到
        self.assertEqual(rights("secret", "other@x"), frozenset(ERP_CONTRACTS.RIGHTS))
        self.assertEqual(rights("secret", "boss@x"), frozenset(ERP_CONTRACTS.RIGHTS))  # ERP 管理员管所有看板
        self.assertEqual(rights("closed", "staff@x"), frozenset({"view", "manage"}))  # 归档的只读、能恢复
        self.assertEqual(rights("team", "seller@x"), frozenset())
        self.assertEqual(rights("missing", "boss@x"), frozenset())

    def test_require_right_refuses_reading_a_private_board_and_editing_a_read_only_one(self):
        self.as_user("staff@x")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require_right("secret", "view")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.require_right("team", "edit")
        self.access.require_right("team", "view")
        self.access.require_right("default", "edit")

    def test_native_resources_are_read_only_and_filtered_by_board(self):
        card = Doc(doctype="ERP Work Card", board="team")
        self.assertTrue(self.access.has_permission(card, "read", "staff@x"))
        for ptype in ("write", "create", "delete", "share"):
            with self.subTest(ptype=ptype), self.assertRaises(self.frappe.PermissionError):
                self.access.has_permission(card, ptype, "staff@x")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.has_permission(Doc(doctype="ERP Work Card", board="secret"), "read",
                                       "staff@x")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.has_permission(card, "read", "seller@x")
        rule = Doc(doctype="ERP Work Rule", board="default")
        with self.assertRaises(self.frappe.PermissionError):  # 规则只给能管这块板的人
            self.access.has_permission(rule, "read", "staff@x")

    def test_list_queries_only_reach_visible_boards(self):
        self.assertEqual(self.access.card_query("seller@x"), "1=0")
        clause = self.access.card_query("staff@x")
        self.assertIn("visibility='team'", clause)
        self.assertIn("m.user='staff@x'", clause)
        self.assertNotIn("workspace", clause)
        self.assertIn("`tabERP Work Board`", self.access.board_query("boss@x"))
        self.assertNotIn("visibility", self.access.board_query("boss@x"))  # 管理员是全部
        self.assertIn("`tabERP Work Notification`.user='staff@x'", self.access.notification_query("staff@x"))

    def test_card_attachments_follow_the_board(self):
        other = types.SimpleNamespace(attached_to_doctype="ERP Sales Document", attached_to_name="x")
        self.assertTrue(self.access.file_permission(other, "read", "seller@x"))
        hidden = types.SimpleNamespace(attached_to_doctype="ERP Work Card", attached_to_name="c-secret")
        with self.assertRaises(self.frappe.PermissionError):
            self.access.file_permission(hidden, "read", "staff@x")
        shown = types.SimpleNamespace(attached_to_doctype="ERP Work Card", attached_to_name="c-team")
        self.assertTrue(self.access.file_permission(shown, "read", "staff@x"))
        self.assertIn("!= 'ERP Work Card'", self.access.file_query("seller@x"))

    def test_sharing_a_board_record_is_refused(self):
        with self.assertRaises(self.frappe.PermissionError):
            self.access.reject_share(types.SimpleNamespace(share_doctype="ERP Work Card"))
        self.access.reject_share(types.SimpleNamespace(share_doctype="ToDo"))

    def test_viewers_and_editors(self):
        self.assertEqual(self.access.viewers("secret"), {"other@x", "boss@x"})
        self.assertEqual(self.access.viewers("team"), self.access.team_users())
        self.assertEqual(self.access.editors("team"), {"other@x", "boss@x"})
        self.assertEqual(self.access.editors("default"), self.access.team_users())


class Declarations(unittest.TestCase):
    def test_sidebar_item_sits_below_the_sales_group(self):
        nav = _declaration()
        self.assertIn(("erp.nav.work", "pearnly-tasks", "square-kanban", "tasks"),
                      [tuple(page) for page in nav["pages"]])
        self.assertIn("pearnly-tasks", nav["available"])
        labels = [group[0] for group in nav["groups"]]
        self.assertEqual(labels[labels.index("nav.group.sales") + 1], None)
        self.assertEqual(nav["groups"][labels.index(None)][2], ["pearnly-tasks"])
        self.assertIn("pearnly-tasks", dict(nav["permission_groups"])["General"])
        page = json.loads((ERP / "pearnly_erp_portal/page/pearnly_tasks/pearnly_tasks.json").read_text())
        self.assertEqual((page["name"], page["title"]), ("pearnly-tasks", "erp.nav.work"))

    def test_feature_catalog_and_roles(self):
        catalog = {item["code"]: item for item in json.loads((ERP / "features.json").read_text())["features"]}
        tasks = catalog["tasks"]
        self.assertEqual(tasks["role"], "ERP Tasks")
        self.assertTrue(tasks["default_on"])
        self.assertEqual(sorted(tasks["read_only"]), sorted(tasks["doctypes"]))  # 原生只读，写走专用接口
        from pearnly_erp_portal.access import menu, rules
        self.assertEqual(rules.FEATURE_ROLES["tasks"], "ERP Tasks")
        self.assertEqual(rules.required_roles("tasks.use"), ("ERP Tasks",))
        self.assertEqual(menu.route("tasks"), "pearnly-tasks")
        self.assertEqual(menu.CATALOG["tasks"][0], "tasks")
        self.assertEqual(set(menu.CATALOG["tasks"][4]), set(tasks["doctypes"]))
        self.assertTrue(rules.can({menu.role("tasks")}, "tasks.use"))
        self.assertTrue(rules.can({"ERP Tasks"}, "tasks.use"))
        self.assertFalse(rules.can({menu.role("sales")}, "tasks.use"))

    def test_task_management_alone_opens_no_business_data(self):
        from pearnly_erp_portal.access import menu, rules
        granted = menu.capabilities({menu.role("home"), menu.role("profile"), menu.role("tasks")})
        self.assertNotIn("products.view", granted)
        self.assertNotIn("stock.read", granted)
        for capability in ("products.view", "stock.read", "sales.read", "purchase.read", "team.manage"):
            with self.subTest(capability=capability):
                self.assertFalse(rules.can({menu.role("tasks"), "ERP Tasks"}, capability))

    def test_hooks_register_the_guards(self):
        source = (ERP / "hooks.py").read_text()
        for doctype in ("ERP Work Board", "ERP Work Card", "ERP Work Label", "ERP Work Activity",
                        "ERP Work Notification", "ERP Work Column", "ERP Work Field", "ERP Work Mirror",
                        "ERP Work Rule"):
            self.assertIn(f'"{doctype}"', source)
        for target in ("work.access.file_permission", "work.access.file_query", "work.access.reject_share",
                       "work.reminders.run", "work.service.ensure_on_migrate", '"desk/work.js"', '"desk/work.css"'):
            self.assertIn(target, source)

    def test_every_controller_goes_through_the_write_gate(self):
        folders = sorted((ERP / "pearnly_erp_portal/doctype").glob("erp_work_*"))
        self.assertEqual(len(folders), 15)
        for folder in folders:
            meta = json.loads((folder / f"{folder.name}.json").read_text())
            with self.subTest(doctype=meta["name"]):
                self.assertNotIn("workspace", [field["fieldname"] for field in meta["fields"]])
                if not meta.get("istable"):
                    self.assertIn("WorkDocument", (folder / f"{folder.name}.py").read_text())
                    self.assertEqual({row["role"] for row in meta["permissions"]}, {"ERP Admin", "ERP Tasks"})
                    self.assertTrue(all(set(row) <= {"role", "read"} for row in meta["permissions"]))


class NoCoworkImport(unittest.TestCase):
    def test_erp_never_imports_cowork(self):
        for path in ERP.rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                for name in names:
                    with self.subTest(path=str(path.relative_to(ROOT)), name=name):
                        self.assertFalse(name.startswith("pearnly_cowork"))

    def test_page_calls_only_erp_endpoints_and_terms(self):
        source = (ERP / "public/desk/work.js").read_text()
        self.assertNotIn("pearnly_cowork", source)
        self.assertNotIn('"cowork.', source)
        self.assertNotIn("`cowork.", source)
        self.assertIn('const API = "pearnly_erp_portal.work.api.";', source)
        self.assertIn('const ROOT = "pearnly-tasks";', source)


class Terms(unittest.TestCase):
    def test_every_fixed_term_the_domain_and_page_use_is_in_the_erp_glossary(self):
        import csv
        import re
        with (ERP / "i18n/messages.tsv").open() as handle:
            keys = {row["key"] for row in csv.DictReader(handle, delimiter="\t") if row.get("key")}
        used = set()
        for path in [*(ERP / "work").glob("*.py"), ERP / "public/desk/work.js"]:
            used |= set(re.findall(r"""["'](erp\.(?:work|nav)\.[\w.]+[\w])["']""", path.read_text()))
        self.assertTrue(used)
        self.assertEqual(sorted(used - keys), [])


if __name__ == "__main__":
    unittest.main()
