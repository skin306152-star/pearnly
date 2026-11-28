"""scripts/check-external-writes.py：站外写入点必须登记、富菜单这类表面必须随部署同步（11-28）。"""
import importlib.util
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_external_writes", ROOT / "scripts" / "check-external-writes.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

SURFACE = '''
SURFACE = "rich_menu"
CONFIG = "x"
def ready(): return ""
def wanted(): return {}
def drift(): return {}
def restore(before): pass
def release(key): return {}, b""
def remount(uid): pass
def publish():
    _request("POST", "richmenu")
'''


class Repo:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        subprocess.run(["git", "-c", "core.quotepath=off", "init", "-q"], cwd=self.root, check=True)
        self.write("scripts/external-writes.toml", "")

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        subprocess.run(["git", "-c", "core.quotepath=off", "add", rel], cwd=self.root, check=True)

    def register(self, *rows):
        self.write("scripts/external-writes.toml", "\n".join(
            f'[[write]]\ntarget = "{t}"\nkind = "{k}"\nsurface = "s"\nnote = "{n}"\n' + (f'via = "{v[0]}"\n' if v else "")
            for t, k, n, *v in rows))

    def check(self):
        return gate.check(self.root)


class ExternalWrites(unittest.TestCase):
    def repo(self):
        repo = Repo()
        self.addCleanup(repo.dir.cleanup)
        return repo

    def test_repository_passes(self):
        self.assertEqual(gate.check(ROOT), [])

    def test_repository_declares_all_three_menus(self):
        self.assertEqual(sorted(gate.declared_surfaces(ROOT).values()), ["cowork", "dms", "erp"])

    def test_new_unregistered_write_is_blocked(self):
        repo = self.repo()
        repo.write("apps/app_a/app_a/onboard.py", '''
            def set_thing(token):
                requests.post("https://api.line.me/v2/bot/thing", headers={})
            def read_thing():
                return requests.get("https://api.line.me/v2/bot/thing")
        ''')
        problems = repo.check()
        self.assertEqual(len(problems), 1)
        self.assertIn("apps/app_a/app_a/onboard.py::set_thing", problems[0])
        self.assertIn("not in scripts/external-writes.toml", problems[0])

    def test_stale_row_and_bad_kind_and_empty_note_are_blocked(self):
        repo = self.repo()
        repo.write("apps/app_a/app_a/onboard.py", 'def f():\n    api.request("/admin/realms/x", "PUT")\n')
        repo.register(("apps/app_a/app_a/onboard.py::f", "sometimes", ""), ("apps/app_a/app_a/gone.py::g", "runtime", "n"))
        problems = "\n".join(repo.check())
        self.assertIn("kind 'sometimes'", problems)
        self.assertIn("note is empty", problems)
        self.assertIn("gone.py::g: registered", problems)

    def test_deploy_sync_needs_a_declared_complete_surface(self):
        repo = self.repo()
        repo.write("apps/app_a/app_a/menu.py", SURFACE)
        repo.register(("apps/app_a/app_a/menu.py::publish", "deploy-sync", "n"))
        self.assertIn("not declared in any hooks pearnly_line_surfaces", "\n".join(repo.check()))
        repo.write("apps/app_a/app_a/hooks.py", 'pearnly_line_surfaces = [{"product": "a", "module": "app_a.menu"}]\n')
        self.assertEqual(repo.check(), [])
        repo.write("apps/app_a/app_a/menu.py", SURFACE.replace("def drift(): return {}\n", ""))
        self.assertIn("app_a.menu lacks drift", "\n".join(repo.check()))

    def test_deploy_sync_through_a_declared_wrapper(self):
        repo = self.repo()
        repo.write("apps/app_a/app_a/menu.py", 'def publish():\n    _request("POST", "richmenu")\n')
        repo.write("apps/app_a/app_a/menu_surface.py", SURFACE.replace("def publish():\n    _request(\"POST\", \"richmenu\")\n", "def publish(): pass\n"))
        repo.write("apps/app_a/app_a/hooks.py", 'pearnly_line_surfaces = [{"product": "a", "module": "app_a.menu_surface"}]\n')
        repo.register(("apps/app_a/app_a/menu.py::publish", "deploy-sync", "n", "app_a.menu_surface"))
        self.assertIn("via app_a.menu_surface does not import app_a.menu", "\n".join(repo.check()))
        repo.write("apps/app_a/app_a/menu_surface.py", "from app_a import menu\n" + SURFACE.replace("def publish():\n    _request(\"POST\", \"richmenu\")\n", "def publish(): pass\n"))
        self.assertEqual(repo.check(), [])

    def test_tests_are_not_write_points(self):
        repo = self.repo()
        repo.write("apps/app_a/app_a/tests/test_x.py", 'def t():\n    requests.post("https://api.line.me/v2/bot/x")\n')
        repo.write("apps/app_a/app_a/test_y.py", 'def t():\n    requests.post("https://api.line.me/v2/bot/x")\n')
        self.assertEqual(repo.check(), [])


if __name__ == "__main__":
    unittest.main()
