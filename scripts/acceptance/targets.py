"""真环境的执行通道（验收运行器用）。只做「把命令送到那台机器、把结果拿回来」，不含任何业务判断。

- `site(product, target)`：在站点网页容器里跑一段脚本。直接用共用件冒烟的 `scripts/smoke/run.py::Remote`（本机实验栈 / 测试机实验栈 /
  生产只读三种目标），不另写一套。
- `windows(host)`：在测试机上跑 Python 脚本（经 ssh；脚本先 scp 过去，避开 ssh → PowerShell 的多层引号）。
- `ledger(host, path)`：在测试机上对验收账套做探测 / 快照 / 核对 / 还原 / 对比（把 `ledger.py` 送过去跑）。

主机、Python 路径、工作目录从环境变量取，默认值是 11-12「机器分工与占用」里登记的；连不上、超时一律抛 `Infra`（运行器只对它重跑一次）。
注意：从 ssh 会话访问账套服务器（\\\\accserver）可能没有凭据——11-12 的做法是以桌面用户的一次性计划任务跑。第一期首次实跑时确认，
不行就把 `Windows.python` 换成计划任务通道（`scripts/express-operator/run-as-session-user.ps1`）。
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

HOSTS = {
    "testpc1": {"ssh": "PEARNLY_ACC_TESTPC1", "default": "lenovo-office-lan", "python": "PEARNLY_ACC_TESTPC1_PYTHON",
                "python_default": "python", "work": "PEARNLY_ACC_TESTPC1_WORK", "work_default": "C:/PearnlyAcceptance"},
    "testpc2": {"ssh": "PEARNLY_ACC_TESTPC2", "default": "testpc2", "python": "PEARNLY_ACC_TESTPC2_PYTHON",
                "python_default": "C:/PearnlyPython311/python.exe", "work": "PEARNLY_ACC_TESTPC2_WORK", "work_default": "C:/RecSetup/acceptance"},
}


if "acc_errors" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("acc_errors", HERE / "acc_errors.py")
    sys.modules["acc_errors"] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules["acc_errors"])
Infra = sys.modules["acc_errors"].Infra


class Windows:
    def __init__(self, name: str, runner=subprocess.run):
        spec = HOSTS[name]
        self.name, self.runner = name, runner
        self.host = os.environ.get(spec["ssh"], spec["default"])
        self.python_exe = os.environ.get(spec["python"], spec["python_default"])
        self.work = os.environ.get(spec["work"], spec["work_default"])

    def _run(self, argv: list[str], timeout: int):
        try:
            done = self.runner(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise Infra(f"{self.name}：{argv[0]} 超时（{timeout} 秒）") from error
        if done.returncode == 255:  # ssh 自己的失败（连不上、被拒）
            raise Infra(f"{self.name}：ssh 连不上（{(done.stderr or '').strip()[-200:]}）")
        return done

    def put(self, local: Path, name: str | None = None) -> str:
        remote = f"{self.work}/{name or local.name}"
        self._run(["ssh", "-o", "BatchMode=yes", self.host, f'cmd /c if not exist "{self.work}" mkdir "{self.work}"'], 60)
        done = self._run(["scp", "-q", str(local), f"{self.host}:{remote}"], 120)
        if done.returncode:
            raise Infra(f"{self.name}：scp {local.name} 失败（{(done.stderr or '').strip()[-200:]}）")
        return remote

    def python(self, script: Path, *args: str, timeout: int = 600) -> tuple[int, str]:
        """把脚本送过去跑。参数经 base64 传（账套路径里的反斜杠、泰文不经 shell 转义），脚本见到 b64: 前缀自己解。"""
        remote = self.put(script)
        quoted = " ".join("b64:" + base64.b64encode(arg.encode("utf-8")).decode() for arg in args)
        done = self._run(["ssh", "-o", "BatchMode=yes", self.host, f'"{self.python_exe}" "{remote}" {quoted}'], timeout)
        return done.returncode, done.stdout


class Ledger:
    """测试机上的一本验收账套。所有子命令的结果是 `ledger.py` 打印的那一行 JSON。"""

    def __init__(self, windows: Windows, path: str):
        self.windows, self.path = windows, path

    def call(self, *args: str, timeout: int = 900) -> dict:
        code, out = self.windows.python(HERE / "ledger.py", *args, timeout=timeout)
        lines = [line for line in out.splitlines() if line.strip().startswith("{")]
        if not lines:
            raise Infra(f"{self.windows.name}：ledger.py {args[0]} 没有回结果（退出码 {code}）：{out[-300:]}")
        return json.loads(lines[-1])

    def probe(self) -> dict:
        return self.call("probe", self.path)

    def snapshot(self, snap: str, note: str = "") -> dict:
        return self.call("snapshot", self.path, snap, "--note", note)

    def verify(self, snap: str) -> dict:
        return self.call("verify", self.path, snap)

    def restore(self, snap: str) -> dict:
        return self.call("restore", snap, self.path)

    def copy(self, to: str) -> dict:
        """跑完后的现场：整本只读拷到证据目录旁（对比用），不动账套。"""
        return self.call("copy", self.path, to)

    def diff(self, before: str, after: str, allow: list[str] | None = None, out: str | None = None) -> dict:
        args = ["diff", before, after]
        if out:
            args += ["--out", out]
        if allow is not None:
            args += ["--allow", ",".join(allow)]
        return self.call(*args)


class Targets:
    def __init__(self):
        self._smoke = None

    def site(self, target: str):
        """`target`：local（本机实验栈）/ lab（测试机实验栈）/ production（生产只读）。返回 smoke 的 Remote。"""
        if self._smoke is None:
            sys.path.insert(0, str(ROOT / "scripts" / "smoke"))
            spec = importlib.util.spec_from_file_location("smoke_run", ROOT / "scripts" / "smoke" / "run.py")
            self._smoke = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self._smoke)
        return self._smoke.Remote(target)

    def windows(self, name: str) -> Windows:
        return Windows(name)

    def ledger(self, host: str, path: str) -> Ledger:
        return Ledger(self.windows(host), path)
