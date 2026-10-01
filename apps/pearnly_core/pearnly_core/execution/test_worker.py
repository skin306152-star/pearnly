"""后台 worker 入口：父进程预导入后交给原生 worker。离线：不连站点、不连 Redis。"""
import sys
import types
import unittest
from unittest.mock import patch

from pearnly_core.execution import worker


class Worker(unittest.TestCase):
    def test_product_lists_come_from_their_own_hooks_after_the_frappe_list(self):
        hooks = types.SimpleNamespace(pearnly_worker_preload=["x.jobs", "frappe.recorder"])
        with patch.object(worker.importlib, "import_module", side_effect=lambda name: {
                "app_a.hooks": hooks}.get(name) or (_ for _ in ()).throw(ImportError(name))):
            names = worker.modules(["app_a", "app_missing"])
        self.assertEqual(names[:len(worker.FRAPPE_PRELOAD)], list(worker.FRAPPE_PRELOAD))
        self.assertEqual(names[len(worker.FRAPPE_PRELOAD):], ["x.jobs"])  # 去重，缺 hooks 的 App 跳过

    def test_a_module_that_fails_to_import_is_skipped_not_fatal(self):
        sys.modules["pearnly_preload_ok"] = types.ModuleType("pearnly_preload_ok")
        try:
            with patch.object(worker.gc, "freeze") as freeze:
                loaded = worker.preload(["pearnly_preload_ok", "pearnly_preload_missing_module"])
        finally:
            del sys.modules["pearnly_preload_ok"]
        self.assertEqual(loaded, 1)
        freeze.assert_called_once()

    def start(self, argv):
        with patch.object(worker, "preload", return_value=0) as preload, \
                patch.object(worker, "modules", return_value=[]) as modules, \
                patch.object(worker.os, "chdir"), \
                patch("frappe.utils.background_jobs.start_worker") as single, \
                patch("frappe.utils.background_jobs.start_worker_pool") as pool:
            worker.main(argv)
        preload.assert_called_once()
        return types.SimpleNamespace(start_worker=single, start_worker_pool=pool), modules

    def test_without_num_workers_it_is_the_native_single_worker(self):
        jobs, modules = self.start(["--queue", "short,default", "--preload", "pearnly_cowork"])
        jobs.start_worker.assert_called_once_with(queue="short,default", quiet=False, burst=False)
        jobs.start_worker_pool.assert_not_called()
        modules.assert_called_once_with(["pearnly_cowork"])

    def test_with_num_workers_it_is_the_native_pool(self):
        jobs, modules = self.start(["--queue", "long", "--num-workers", "2", "--burst"])
        jobs.start_worker_pool.assert_called_once_with(queue="long", num_workers=2, quiet=False, burst=True)
        jobs.start_worker.assert_not_called()
        modules.assert_called_once_with([])


if __name__ == "__main__":
    unittest.main()
