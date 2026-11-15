"""流程用例的容器内常驻 Python（scripts/regression/lab_worker.py + run.py 的 LabWorker / Lab._python_call）：
用一个假的 frappe 包在本机起真进程，查每段之间状态不串、输出不混进协议、崩了退回每段新起进程并在格里标出来。"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "scripts/regression/lab_worker.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("scripts/flows/run.py", "flows_run_worker")

FAKE_FRAPPE = '''
import threading
class _Local(threading.local):
    pass
local = _Local()
destroyed = []
def init(site, sites_path=None):
    local.site = site
    local.lang = "en"
def connect():
    local.db = _DB()
class _DB:
    rolled = 0
    def rollback(self):
        _DB.rolled += 1
def destroy():
    destroyed.append(getattr(local, "site", None))
    local.__dict__.clear()
def enqueue(path, **kwargs):
    return "queued"
client_cache = None
cache = None
'''
FAKE_CACHING = '''
from collections import defaultdict
_SITE_CACHE = defaultdict(dict)
'''
FAKE_OURS = '''
import functools
WAIT_SECONDS = 5
_memo = {}
_session = None
def binding_of(user):
    return "real"
@functools.lru_cache
def squared(n):
    CALLS.append(n)
    return n * n
CALLS = []
'''
FAKE_REGISTRANT = '''
from pearnly_fake import mod
mod._memo["registered at import"] = True
'''


class Worker(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "frappe/utils").mkdir(parents=True)
        (self.tmp / "frappe/__init__.py").write_text(FAKE_FRAPPE)
        (self.tmp / "frappe/utils/__init__.py").write_text("")
        (self.tmp / "frappe/utils/caching.py").write_text(FAKE_CACHING)
        (self.tmp / "pearnly_fake").mkdir()
        (self.tmp / "pearnly_fake/__init__.py").write_text("")
        (self.tmp / "pearnly_fake/mod.py").write_text(FAKE_OURS)
        (self.tmp / "pearnly_fake/registrant.py").write_text(FAKE_REGISTRANT)
        (self.tmp / "script.py").write_text("import sys, json\nprint(json.dumps(sys.argv[1:]))\nraise SystemExit(sys.argv[1] if sys.argv[1] == 'no' else 0)\n")
        env = dict(os.environ, PYTHONPATH=str(self.tmp), LAB_WORKER_SITES=str(self.tmp))
        self.saved_env = os.environ.copy()
        os.environ.update(env)
        self.worker = run.LabWorker([sys.executable, str(WORKER)])

    def tearDown(self):
        self.worker.close()
        os.environ.clear()
        os.environ.update(self.saved_env)

    def code(self, body):
        return self.worker.call({"code": textwrap.dedent(body)}, 30)

    def last(self, reply):
        return json.loads([line for line in reply["stdout"].splitlines() if line.startswith("{")][-1])

    def test_module_patches_memos_and_lazy_slots_do_not_reach_the_next_call(self):
        first = self.code("""
            import json, frappe
            from pearnly_fake import mod
            frappe.enqueue = lambda path, **kw: "inline"
            mod.WAIT_SECONDS = 0
            mod.binding_of = lambda user: "fake"
            mod._memo["a"] = 1
            mod._session = object()
            mod.squared(3)
            print(json.dumps({"enqueue": frappe.enqueue("x"), "wait": mod.WAIT_SECONDS, "memo": dict(mod._memo)}))
        """)
        self.assertEqual(self.last(first), {"enqueue": "inline", "wait": 0, "memo": {"a": 1}})
        second = self.code("""
            import json, frappe
            from pearnly_fake import mod
            calls = len(mod.CALLS)
            mod.squared(3)
            print(json.dumps({"enqueue": frappe.enqueue("x"), "wait": mod.WAIT_SECONDS, "binding": mod.binding_of("u"),
                              "memo": dict(mod._memo), "session": mod._session, "recomputed": len(mod.CALLS) > calls}))
        """)
        self.assertEqual(self.last(second), {"enqueue": "queued", "wait": 5, "binding": "real", "memo": {},
                                             "session": None, "recomputed": True})

    def test_what_another_module_registers_at_import_stays(self):
        self.code("import pearnly_fake.mod\nimport pearnly_fake.registrant\nprint('{}')")
        reply = self.code("import json\nfrom pearnly_fake import mod\nprint(json.dumps(dict(mod._memo)))")
        self.assertEqual(self.last(reply), {"registered at import": True})

    def test_frappe_local_is_released_uncommitted_work_rolled_back_and_site_caches_cleared(self):
        self.code("""
            import frappe
            from frappe.utils import caching
            frappe.init(site="a.site"); frappe.connect()
            frappe.local.lang = "zh"
            caching._SITE_CACHE["conf"]["a"] = 1
            print("{}")
        """)
        reply = self.code("""
            import json, frappe
            from frappe.utils import caching
            print(json.dumps({"site": getattr(frappe.local, "site", None), "lang": getattr(frappe.local, "lang", None),
                              "rolled": frappe._DB.rolled, "destroyed": frappe.destroyed, "cache": dict(caching._SITE_CACHE["conf"])}))
        """)
        self.assertEqual(self.last(reply), {"site": None, "lang": None, "rolled": 1, "destroyed": ["a.site"], "cache": {}})

    def test_exit_codes_tracebacks_argv_and_environment_match_a_fresh_process(self):
        self.assertEqual(self.worker.call({"script": str(self.tmp / "script.py"), "argv": ["yes", "b"]}, 30)["stdout"], '["yes", "b"]\n')
        refused = self.worker.call({"script": str(self.tmp / "script.py"), "argv": ["no"]}, 30)
        self.assertEqual((refused["returncode"], refused["stderr"].strip()), (1, "no"))
        failed = self.code("raise ValueError('boom')")
        self.assertEqual(failed["returncode"], 1)
        self.assertIn("ValueError: boom", failed["stderr"])
        self.code("import os\nos.environ['LEAK'] = '1'\nos.chdir('/')")
        reply = self.code("import os, json\nprint(json.dumps({'leak': os.environ.get('LEAK'), 'cwd': os.getcwd()}))")
        self.assertIsNone(self.last(reply)["leak"])
        self.assertEqual(Path(self.last(reply)["cwd"]).resolve(), self.tmp.resolve())

    def test_child_process_output_and_stdin_do_not_touch_the_protocol(self):
        reply = self.code("""
            import os, subprocess, sys
            os.system("echo child-out")
            sys.__stdout__.write("raw\\n"); sys.__stdout__.flush()
            print(subprocess.run(["cat"], capture_output=False, stdout=subprocess.PIPE, text=True).stdout == "")
        """)
        self.assertEqual((reply["returncode"], reply["stdout"].strip()), (0, "True"))
        self.assertEqual(self.code("print('{\"next\": 1}')")["stdout"].strip(), '{"next": 1}')

    def test_a_worker_killed_mid_call_is_reported_gone(self):
        with self.assertRaises(run.LabWorkerGone):
            self.code("import os\nos._exit(3)")


class FallBack(unittest.TestCase):
    """常驻进程起不来、或半路没了：这一段照老路报失败（不重跑），这个线程之后每段新起一个进程，格里标 fallback。"""

    def lab(self, worker):
        lab = run.Lab(persistent=True)
        calls = []
        lab.exec = lambda *argv, timeout=900: calls.append(argv) or subprocess.CompletedProcess(argv, 0, '{"n": 0}\n', "")
        run_saved = run.LabWorker
        run.LabWorker = worker
        self.addCleanup(setattr, run, "LabWorker", run_saved)
        return lab, calls

    def test_gone_mid_call_fails_that_call_then_falls_back_and_is_marked(self):
        class Gone:
            def __init__(self, *a):
                pass

            def call(self, request, timeout):
                raise run.LabWorkerGone("worker exited (-9)")

            def close(self):
                pass
        lab, calls = self.lab(Gone)
        lab.cell_started()
        with self.assertRaises(RuntimeError):
            lab.python("a.site", "print(1)")
        self.assertEqual(calls, [])
        self.assertEqual(lab.client_errors("a.site"), 0)  # 之后走老路
        self.assertEqual(len(calls), 1)
        self.assertTrue(lab.cell_mode().startswith("fallback: worker exited"))
        lab.cell_started()
        self.assertEqual(lab.cell_mode(), "per-call")
        self.assertEqual(lab.fallbacks, ["worker exited (-9)"])

    def test_cannot_start_runs_the_old_way_from_the_first_call(self):
        class Broken:
            def __init__(self, *a):
                raise run.LabWorkerGone("worker did not start: {}")
        lab, calls = self.lab(Broken)
        lab.cell_started()
        self.assertEqual(lab.client_errors("a.site"), 0)
        self.assertEqual(len(calls), 1)
        self.assertTrue(lab.cell_mode().startswith("fallback: start:"))

    def test_switched_off_or_other_callers_always_run_per_call(self):
        self.assertFalse(run.Lab().persistent)
        saved = os.environ.get("PEARNLY_FLOWS_LAB_WORKER")
        os.environ["PEARNLY_FLOWS_LAB_WORKER"] = "0"
        try:
            lab = run.Lab(persistent=True)
        finally:
            os.environ.pop("PEARNLY_FLOWS_LAB_WORKER") if saved is None else os.environ.update(PEARNLY_FLOWS_LAB_WORKER=saved)
        lab.cell_started()
        self.assertEqual(lab.cell_mode(), "per-call")

    def test_each_thread_gets_its_own_worker(self):
        made = []

        class Fine:
            def __init__(self, *a):
                made.append(threading.get_ident())

            def call(self, request, timeout):
                return {"returncode": 0, "stdout": '{"n": 2}\n', "stderr": ""}

            def close(self):
                pass
        lab, _ = self.lab(Fine)
        threads = [threading.Thread(target=lambda: [lab.client_errors("s") for _ in range(3)]) for _ in range(2)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(len(made), 2)
        lab.close_workers()


if __name__ == "__main__":
    unittest.main()
