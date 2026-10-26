"""冒烟开的本机无头 Chrome 一律收干净（主会话 10-26：本机留下 9 个跑了 1 天半的孤儿 Chrome、102 个临时 profile）。

用一个假 Chrome（记下自己的 pid、一直睡）代替真的跑 ``scripts/smoke/drive.mjs``：
- 驱动超时：run_driver 整组收掉，假 Chrome 不在、临时 profile 删掉；
- 驱动中途被 SIGTERM 打断：它自己关掉 Chrome、删 profile；
- 启动时的清理只认运行标记（profile / 工作目录里记的进程号）：那次运行已经结束的才结掉它的 Chrome、删它的目录，还在跑的、没有标记的
  一律不碰（主会话 10-26：别按进程名或父进程缺失猜）。
没有 node 时跳过。
"""
import importlib.util
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("chrome_reaper", ROOT / "scripts/smoke/chrome_reaper.py")
reaper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reaper)


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # 已经结束、等着被收的僵尸不算活着
        out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
        return bool(out) and not out.startswith("Z")
    except OSError:
        return True


def wait_for(path: Path, seconds=10.0):
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text().strip())
        time.sleep(0.05)
    raise AssertionError(f"{path} never appeared")


@unittest.skipUnless(shutil.which("node"), "needs node")
class SmokeChromeCleanup(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.pidfile = self.tmp / "chrome.pid"
        fake = self.tmp / "fake-chrome"
        fake.write_text(textwrap.dedent(f"""\
            #!{sys.executable}
            import os, time
            open({str(self.pidfile)!r}, "w").write(str(os.getpid()))
            time.sleep(120)
            """))
        fake.chmod(0o755)
        self.env = {**os.environ, "SMOKE_CHROME": str(fake), "TMPDIR": str(self.tmp)}
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def profiles(self):
        return sorted(path.name for path in self.tmp.glob("smoke-*"))

    def test_a_driver_that_times_out_leaves_no_chrome_and_no_profile(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            reaper.run_driver(["node", str(ROOT / "scripts/smoke/drive.mjs"), '{"url": "about:blank"}'], timeout=2, env=self.env)
        pid = wait_for(self.pidfile)
        time.sleep(0.5)
        self.assertFalse(alive(pid), "fake Chrome still running after the driver timed out")
        self.assertEqual(self.profiles(), [])

    def test_a_driver_interrupted_midway_closes_chrome_and_removes_its_profile(self):
        proc = subprocess.Popen(["node", str(ROOT / "scripts/smoke/drive.mjs"), '{"url": "about:blank"}'], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        pid = wait_for(self.pidfile)
        self.assertEqual(len(self.profiles()), 1)
        proc.send_signal(signal.SIGTERM)   # 只打断驱动本身，看它自己收不收
        proc.wait(10)
        time.sleep(0.5)
        self.assertFalse(alive(pid), "fake Chrome left running after the driver was interrupted")
        self.assertEqual(self.profiles(), [])

    def _gone_pid(self):
        """一个已经结束的进程号：当作以前那次冒烟（已经退出）的运行标记。"""
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def _chrome(self, profile, *flags):
        """假 Chrome（命令行带 --headless 与这个 profile），外壳退出后父进程不是驱动。回它的进程号。"""
        pidfile = self.tmp / f"{profile.name}-{len(flags)}.pid"
        subprocess.run(["sh", "-c", f"{sys.executable} -c 'import time; time.sleep(120)' --headless=new {' '.join(flags)} "
                                    f"--user-data-dir={profile} & echo $! > {pidfile}"], check=True)
        pid = wait_for(pidfile)
        self.addCleanup(lambda: alive(pid) and os.kill(pid, signal.SIGKILL))
        return pid

    def test_the_startup_sweep_takes_only_chrome_and_directories_of_runs_that_have_ended(self):
        """主会话 10-26：只认运行标记——profile 里记的那次运行已经结束的，主进程与 GPU 子进程都结掉、目录删掉；那次运行还在跑的
        （别的窗口、跑超过 1 小时的冒烟）、没有标记的，一律不碰；不按进程名或父进程缺失猜。"""
        ended, running, unmarked = self.tmp / "smoke-ended", self.tmp / "smoke-running", self.tmp / "smoke-unmarked"
        for path in (ended, running, unmarked):
            path.mkdir()
        (ended / reaper.OWNER_FILE).write_text(str(self._gone_pid()))
        reaper.claim(running)   # 还在跑：标记是本进程
        dead_root, dead_gpu = self._chrome(ended), self._chrome(ended, "--type=gpu-process")
        live_root, live_gpu = self._chrome(running), self._chrome(running, "--type=gpu-process")
        stranger = self._chrome(unmarked)
        workdir_ended, workdir_running = self.tmp / "scan-selftest-ended", self.tmp / "smoke-work-running"
        workdir_ended.mkdir(), workdir_running.mkdir()
        (workdir_ended / reaper.OWNER_FILE).write_text(str(self._gone_pid()))
        reaper.claim(workdir_running)
        time.sleep(0.3)
        swept = reaper.reap_orphans(self.tmp)
        time.sleep(0.3)
        self.assertEqual(sorted(swept["killed"]), sorted([dead_root, dead_gpu]))
        self.assertFalse(alive(dead_root) or alive(dead_gpu))
        self.assertTrue(alive(live_root) and alive(live_gpu) and alive(stranger))
        self.assertEqual(sorted(swept["removed"]), ["scan-selftest-ended", "smoke-ended"])
        self.assertTrue(running.exists() and unmarked.exists() and workdir_running.exists())

    def test_the_driver_marks_its_profile_with_its_own_pid(self):
        proc = subprocess.Popen(["node", str(ROOT / "scripts/smoke/drive.mjs"), '{"url": "about:blank"}'], env=self.env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            wait_for(self.pidfile)
            (profile,) = self.tmp.glob("smoke-*")
            self.assertEqual(int((profile / reaper.OWNER_FILE).read_text()), proc.pid)
        finally:
            proc.send_signal(signal.SIGTERM)
            proc.wait(10)

if __name__ == "__main__":
    unittest.main()
