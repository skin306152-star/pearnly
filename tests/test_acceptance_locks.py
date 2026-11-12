"""占用锁（scripts/acceptance/locks.py）：约期占用互斥、续期、超时不自动作废、接手要原因；进程占用与约期占用互相看得见；
与流程运行器 lab_lock 的锁文件互通；资源名只认登记的。"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("acceptance_locks", ROOT / "scripts" / "acceptance" / "locks.py")
locks = importlib.util.module_from_spec(spec)
sys.modules["acceptance_locks"] = locks
spec.loader.exec_module(locks)


class LocksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ["PEARNLY_LOCK_DIR"] = self.tmp.name
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(os.environ.pop, "PEARNLY_LOCK_DIR", None)

    def test_lease_excludes_others_and_renews_for_holder(self):
        first = locks.acquire("ledger:69EXP/TEST2", "win-a", "示范场景", 30, restore="恢复基线")
        with self.assertRaises(locks.Busy) as caught:
            locks.acquire("ledger:69EXP/TEST2", "win-b", "别的实验", 30)
        self.assertIn("win-a", str(caught.exception))
        self.assertIn("恢复基线", str(caught.exception))
        again = locks.acquire("ledger:69EXP/TEST2", "win-a", "示范场景", 60)
        self.assertEqual(again["since"], first["since"])
        self.assertGreater(again["until"], first["until"])
        locks.acquire("ledger:69EXP/TEST", "win-b", "另一本不受影响", 10)

    def test_release_only_by_holder_then_free(self):
        locks.acquire("testpc1", "win-a", "跑场景", 10)
        with self.assertRaises(locks.NotHolder):
            locks.release("testpc1", "win-b")
        locks.release("testpc1", "win-a", note="已还原")
        locks.acquire("testpc1", "win-b", "接着用", 10)
        events = [line["event"] for line in locks.history("testpc1")]
        self.assertEqual(events, ["acquire", "release", "acquire"])

    def test_expired_lease_still_blocks_until_takeover_with_reason(self):
        locks.acquire("samsung", "win-a", "扫码", 1)
        path = locks.lock_path("samsung")
        record = json.loads(path.read_text())
        record["until"] = time.time() - 600
        path.write_text(json.dumps(record))
        with self.assertRaises(locks.Busy) as caught:
            locks.acquire("samsung", "win-b", "要用", 10)
        self.assertIn("已超时", str(caught.exception))
        with self.assertRaises(locks.Busy):
            locks.acquire("samsung", "win-b", "要用", 10, takeover=True)  # 没写原因
        locks.acquire("samsung", "win-b", "要用", 10, takeover=True, reason="win-a 已停，核过还原清单")
        self.assertEqual(locks.history("samsung")[-2]["event"], "takeover")

    def test_takeover_refused_while_lease_is_live(self):
        locks.acquire("testpc2", "win-a", "在用", 30)
        with self.assertRaises(locks.Busy):
            locks.acquire("testpc2", "win-b", "硬闯", 10, takeover=True, reason="等不及")

    def test_process_hold_respects_lease_and_blocks_while_alive(self):
        locks.acquire("ledger:69EXP/TEST2", "win-a", "约了", 30)
        with self.assertRaises(locks.Busy):
            with locks.hold("ledger:69EXP/TEST2", "runner-b", "跑"):
                pass
        with locks.hold("ledger:69EXP/TEST2", "win-a", "自己约的可以进"):
            pass
        self.assertEqual(locks.status()[0][1]["holder"], "win-a")  # 约期占用原样留着

        code = textwrap.dedent(f"""
            import importlib.util, sys, time
            spec = importlib.util.spec_from_file_location("l", {str(ROOT / "scripts" / "acceptance" / "locks.py")!r})
            l = importlib.util.module_from_spec(spec); sys.modules["l"] = l; spec.loader.exec_module(l)
            with l.hold("testpc1", "runner-x", "长跑"):
                print("held", flush=True); time.sleep(30)
        """)
        child = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=os.environ)
        try:
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaises(locks.Busy) as caught:
                locks.acquire("testpc1", "win-b", "要用", 10)
            self.assertIn("runner-x", str(caught.exception))
        finally:
            child.kill()
            child.wait()
        locks.acquire("testpc1", "win-b", "进程死了锁就没了", 10)

    def test_hold_all_releases_everything_when_one_is_busy(self):
        locks.acquire("samsung", "win-a", "在用", 10)
        with self.assertRaises(locks.Busy):
            locks.hold_all(["testpc1", "samsung"], "runner", "场景")
        locks.acquire("testpc1", "win-b", "没被留下", 10)

    def test_lab_lock_file_is_shared_with_flows_runner(self):
        self.assertEqual(locks.lock_path("lab:pearnly-regression-lab").name, "pearnly-regression-lab.lock")
        # run.py 的 lab_lock 只写进程号、靠 flock；进程已退出（拿得到 flock）时不算占用
        locks.lock_path("lab:pearnly-regression-lab").write_text("12345")
        with locks.hold("lab:pearnly-regression-lab", "runner", "跑"):
            pass

    def test_flows_runner_lab_lock_and_this_library_block_each_other(self):
        spec = importlib.util.spec_from_file_location("flows_run_for_locks", ROOT / "scripts" / "flows" / "run.py")
        flows = importlib.util.module_from_spec(spec)
        sys.modules["flows_run_for_locks"] = flows
        spec.loader.exec_module(flows)
        resource = f"lab:{flows.LAB}"
        handle = flows.lab_lock(self.tmp.name)   # run.py 占着实验栈
        try:
            with self.assertRaises(locks.Busy):
                locks.acquire(resource, "win-a", "想起栈跑场景", 10)
        finally:
            handle.close()
        with locks.hold(resource, "runner", "验收运行器占着实验栈"):
            with self.assertRaises(SystemExit) as caught:
                flows.lab_lock(self.tmp.name)
            self.assertIn("正在用实验栈", str(caught.exception))
        flows.lab_lock(self.tmp.name).close()

    def test_unknown_resource_names_are_refused(self):
        for bad in ("testpc3", "ledger:", "ledger:../x", "testenv-domain:billing", "lab:a b"):
            with self.assertRaises(ValueError, msg=bad):
                locks.lock_path(bad)
        self.assertEqual(locks.main(["acquire", "nope", "--holder", "a", "--purpose", "b", "--minutes", "1"]), 2)

    def test_status_lists_leases_with_their_resource_names(self):
        locks.acquire("ledger:69EXP/TEST2", "win-a", "x", 10)
        locks.acquire("testenv-domain:erp", "win-b", "y", 10)
        self.assertEqual(sorted(name for name, _ in locks.status()), ["ledger:69EXP/TEST2", "testenv-domain:erp"])


if __name__ == "__main__":
    unittest.main()
