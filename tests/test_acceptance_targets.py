"""真环境执行通道（scripts/acceptance/targets.py）离线单测：主机登记了桌面用户时，脚本改由那个用户的一次性计划任务跑——
参数一行一个写进文件送过去、输出文件（UTF-16）取回本机读、退出码取任务回的状态行；任务没删掉、超时、没回状态都算环境问题。
不连任何机器：ssh / scp 换成替身。真机上这条通道 11-13 在测试机2 实跑过（探测、快照、核对、按单号读回）。
"""
import importlib.util
import pathlib
import sys
import types
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts" / "acceptance"))
spec = importlib.util.spec_from_file_location("acc_targets", ROOT / "scripts" / "acceptance" / "targets.py")
targets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(targets)


class Fake:
    """ssh / scp 的替身：记下每次调用；取回输出文件时写一份 UTF-16 的结果。"""

    def __init__(self, status="ACCJOB exit 0 waited=4 task_left=False", output='{"ok": true, "busy": []}\n'):
        self.calls, self.status, self.output, self.sent = [], status, output, {}

    def __call__(self, argv, **_):
        self.calls.append(argv)
        if argv[0] == "scp" and ":" in argv[-2] and argv[-2].endswith(".out"):
            pathlib.Path(argv[-1]).write_bytes(self.output.encode("utf-16"))
        elif argv[0] == "scp" and argv[-1].endswith(".args"):
            self.sent["args"] = pathlib.Path(argv[-2]).read_text(encoding="utf-8")
        out = self.status + "\n" if argv[0] == "ssh" and "acc-run.ps1" in argv[-1] else ""
        return types.SimpleNamespace(returncode=0, stdout=out, stderr="")


class DesktopUserChannelTest(unittest.TestCase):
    def test_script_runs_as_the_desktop_user_and_output_comes_back(self):
        fake = Fake()
        box = targets.Windows("testpc2", runner=fake)
        self.assertEqual(box.user, "aksar")
        code, text = box.python(ROOT / "scripts" / "acceptance" / "ledger.py", "probe", r"\\accserver\d$\ACCOUNT\69EXP\TEST2")
        self.assertEqual((code, text.strip()), (0, '{"ok": true, "busy": []}'))
        self.assertEqual(fake.sent["args"], "probe\n\\\\accserver\\d$\\ACCOUNT\\69EXP\\TEST2\n")  # 原样，一行一个，不经 shell
        run = next(argv[-1] for argv in fake.calls if argv[0] == "ssh" and "acc-run.ps1" in argv[-1])
        self.assertIn("-User aksar", run)
        self.assertIn(r'-Script "C:\PearnlyAcceptance\ledger.py"', run)
        sent = [argv[-2].rsplit("/", 1)[-1] for argv in fake.calls if argv[0] == "scp" and ":" in argv[-1]]
        self.assertEqual(sent[:3], ["ledger.py", "acc-job.ps1", "acc-run.ps1"])

    def test_exit_code_and_environment_failures(self):
        code, _ = targets.Windows("testpc2", runner=Fake("ACCJOB exit 1 waited=6 task_left=False")).python(ROOT / "scripts/acceptance/ledger.py", "verify")
        self.assertEqual(code, 1)  # 脚本自己的不一致 / 被占用：照退出码交回，不当环境问题
        for status, words in (("ACCJOB timeout waited=600 task_left=False", "没跑完"), ("ACCJOB exit 0 waited=3 task_left=True", "没删掉"), ("", "没回状态")):
            with self.assertRaises(targets.Infra) as caught:
                targets.Windows("testpc2", runner=Fake(status)).python(ROOT / "scripts/acceptance/ledger.py", "probe")
            self.assertIn(words, str(caught.exception))
        with self.assertRaises(ValueError):
            targets.Windows("testpc2", runner=Fake()).python(ROOT / "scripts/acceptance/ledger.py", "snapshot", "--note", "两\n行")

    def test_host_without_a_desktop_user_keeps_the_direct_channel(self):
        fake = Fake()
        box = targets.Windows("testpc1", runner=fake)
        self.assertEqual(box.user, "")
        box.python(ROOT / "scripts" / "acceptance" / "ledger.py", "probe", "C:/x")
        self.assertFalse(any("acc-run.ps1" in argv[-1] for argv in fake.calls))


class CcPowerTest(unittest.TestCase):
    """测试机上停 / 起 CC 桌面版的小脚本（scripts/acceptance/win/cc_power.py）：只认起来之后的心跳 INFO 行。"""

    def test_only_heartbeats_after_the_start_count(self):
        from datetime import datetime

        spec = importlib.util.spec_from_file_location("acc_cc_power", ROOT / "scripts" / "acceptance" / "win" / "cc_power.py")
        power = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(power)
        lines = ["2026-11-16 10:00:00,001 [INFO] companion.heartbeat :: [heartbeat] ok (old)",
                 "2026-11-16 10:05:00,001 [WARNING] companion.heartbeat :: [heartbeat] failed",
                 "2026-11-16 10:05:03,001 [INFO] companion.worker :: polling",
                 "2026-11-16 10:05:09,001 [INFO] companion.heartbeat :: [heartbeat] ok"]
        beats = power.beats_since(lines, datetime(2026, 11, 16, 10, 5, 0))
        self.assertEqual(len(beats), 1)
        self.assertIn("10:05:09", beats[0])


if __name__ == "__main__":
    unittest.main()
