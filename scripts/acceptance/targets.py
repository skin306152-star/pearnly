"""真环境的执行通道（验收运行器用）。只做「把命令送到那台机器、把结果拿回来」，不含任何业务判断。

- `site(product, target)`：在站点网页容器里跑一段脚本。直接用共用件冒烟的 `scripts/smoke/run.py::Remote`（本机实验栈 / 测试机实验栈 /
  生产只读三种目标），不另写一套。
- `windows(host)`：在测试机上跑 Python 脚本（经 ssh；脚本先 scp 过去，避开 ssh → PowerShell 的多层引号）。
- `ledger(host, path)`：在测试机上对验收账套做探测 / 快照 / 核对 / 还原 / 对比（把 `ledger.py` 送过去跑）。

主机、Python 路径、工作目录从环境变量取，默认值是 11-12「机器分工与占用」里登记的；连不上、超时一律抛 `Infra`（运行器只对它重跑一次）。
桌面用户通道：ssh 进测试机的账号读不到账套服务器（\\\\accserver），只有已登录桌面的那个用户有凭据。主机登记了 `user` 时，脚本改由
那个用户的一次性计划任务跑（`win/acc-run.ps1` + `win/acc-job.ps1`：参数放文件、输出写文件、跑完删任务并回读），结果文件取回本机读。
测试机2（aksar）11-13 实测：占用探测、整本快照、核对都经这条通道跑通。测试机1 还没登记桌面用户（skin3），首次用时实测。
"""
from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]

HOSTS = {
    "testpc1": {"ssh": "PEARNLY_ACC_TESTPC1", "default": "lenovo-office-lan", "python": "PEARNLY_ACC_TESTPC1_PYTHON",
                "python_default": "python", "work": "PEARNLY_ACC_TESTPC1_WORK", "work_default": "C:/PearnlyAcceptance"},
    "testpc2": {"ssh": "PEARNLY_ACC_TESTPC2", "default": "testpc2", "python": "PEARNLY_ACC_TESTPC2_PYTHON",
                "python_default": "C:/PearnlyPython311/python.exe", "work": "PEARNLY_ACC_TESTPC2_WORK", "work_default": "C:/PearnlyAcceptance",
                "user": "PEARNLY_ACC_TESTPC2_USER", "user_default": "aksar"},
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
        self.user = os.environ.get(spec["user"], spec["user_default"]) if "user" in spec else ""  # 桌面用户通道，见文件头

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
        """把脚本送过去跑。参数经 base64 传（账套路径里的反斜杠、泰文不经 shell 转义），脚本见到 b64: 前缀自己解。
        主机登记了桌面用户时走一次性计划任务（`_as_user`）。"""
        if self.user:
            return self._as_user(script, args, timeout)
        remote = self.put(script)
        quoted = " ".join("b64:" + base64.b64encode(arg.encode("utf-8")).decode() for arg in args)
        done = self._run(["ssh", "-o", "BatchMode=yes", self.host, f'"{self.python_exe}" "{remote}" {quoted}'], timeout)
        return done.returncode, done.stdout


    def _as_user(self, script: Path, args: tuple, timeout: int) -> tuple[int, str]:
        """以桌面用户的一次性计划任务跑脚本：参数一行一个写进文件送过去，输出文件（UTF-16）取回本机读。"""
        if any("\n" in arg or "\r" in arg for arg in args):
            raise ValueError("桌面用户通道的参数不能带换行（参数文件一行一个）")
        name = f"{script.stem}-{uuid.uuid4().hex[:8]}"
        remote = self.put(script)
        for helper in ("acc-job.ps1", "acc-run.ps1"):
            self.put(HERE / "win" / helper)
        jobs = self.work.replace("/", "\\") + "\\jobs"
        made = self._run(["ssh", "-o", "BatchMode=yes", self.host, f'cmd /c if not exist "{jobs}" mkdir "{jobs}"'], 60)
        if made.returncode:
            raise Infra(f"{self.name}：建不了 {self.work}/jobs（{(made.stderr or '').strip()[-200:]}）")
        with tempfile.TemporaryDirectory() as folder:
            local = Path(folder) / f"{name}.args"
            local.write_text("".join(arg + "\n" for arg in args), encoding="utf-8")
            self.put(local, f"jobs/{name}.args")
            win = lambda path: path.replace("/", "\\")  # noqa: E731
            done = self._run(["ssh", "-o", "BatchMode=yes", self.host,
                              f'powershell -NoProfile -ExecutionPolicy Bypass -File "{win(self.work)}\\acc-run.ps1" -Work "{win(self.work)}" '
                              f'-Name {name} -User {self.user} -Python "{win(self.python_exe)}" -Script "{win(remote)}" -Wait {timeout}'],
                             timeout + 120)
            status = next((line for line in reversed((done.stdout or "").splitlines()) if line.startswith("ACCJOB ")), "")
            if not status:
                raise Infra(f"{self.name}：一次性任务没回状态（{((done.stderr or '') + (done.stdout or '')).strip()[-300:]}）")
            if "task_left=True" in status:
                raise Infra(f"{self.name}：一次性任务 PearnlyAcc-{name} 没删掉，先手工删再继续")
            if status.split()[1] == "timeout":
                raise Infra(f"{self.name}：脚本 {script.name} 在桌面用户会话里 {timeout} 秒没跑完")
            back = Path(folder) / f"{name}.out"
            got = self._run(["scp", "-q", f"{self.host}:{self.work}/jobs/{name}.out", str(back)], 120)
            if got.returncode or not back.is_file():
                raise Infra(f"{self.name}：取不回 {name}.out（{(got.stderr or '').strip()[-200:]}）")
            raw = back.read_bytes()
            text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8", "replace")
        return int(status.split()[2]), text


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
