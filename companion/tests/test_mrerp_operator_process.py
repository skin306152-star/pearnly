# -*- coding: utf-8 -*-
"""MR.ERP 代录的轻量口径（ADR-026 第 9 条）：不录时不导入执行层、不建网页内核；录时网页内核在每批一个的子进程里，
录完关窗、子进程与它的渲染进程都退出，托盘这边不留网页内核。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

from _mrerp_harness import SRC, factory, payload, webengine_works

from companion.express_operator.host import Host


def _python(code: str, **env) -> str:
    done = subprocess.run([sys.executable, "-c", code], cwd=str(SRC), capture_output=True, text=True, timeout=120,
                          env={**os.environ, **env})
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.strip()


IDLE = r"""
import sys
sys.path.insert(0, ".")
from companion import worker, payload_contract
from companion.express_operator import host
h = host.Host("{root}")
print(sorted(h.assemblers), payload_contract.operator_targets())
print("mrerp_operator" in " ".join(m for m in sys.modules if m.startswith("companion.")),
      any(m.startswith("PySide6.QtWebEngine") for m in sys.modules))
"""


def test_without_the_switch_nothing_mrerp_is_loaded_and_only_express_is_reported(tmp_path):
    out = _python(IDLE.replace("{root}", str(tmp_path).replace("\\", "/")), PEARNLY_CC_MRERP_OPERATOR="")
    assert out.splitlines() == ["['express'] ('express',)", "False False"]


def test_with_the_switch_mrerp_is_reported_but_still_not_loaded_until_a_job_comes(tmp_path):
    out = _python(IDLE.replace("{root}", str(tmp_path).replace("\\", "/")), PEARNLY_CC_MRERP_OPERATOR="1")
    assert out.splitlines() == ["['express', 'mrerp'] ('express', 'mrerp')", "False False"]


CHILD = ("import sys; sys.path.insert(0, {src!r}); from companion.mrerp_operator.web_driver import child_main; "
         "sys.exit(child_main())")


def _children(pid: int) -> list:
    """子进程的直接子进程（只在有 pgrep 的系统上看；Windows 上只核子进程本身退出——那里 os.kill(pid, 0) 是发 Ctrl+C，不能拿来探活）。"""
    if sys.platform == "win32" or shutil.which("pgrep") is None:
        return []
    done = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True, text=True)
    return [int(x) for x in done.stdout.split()]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@pytest.mark.skipif(not webengine_works(), reason="QtWebEngine cannot start on this machine")
def test_one_batch_runs_in_its_own_process_and_nothing_is_left_after_the_window_closes(tmp_path, monkeypatch):
    from _fake_mrerp import FakeMrerp

    from companion.mrerp_operator.web_desk import ProcessDesk

    monkeypatch.setenv("PEARNLY_CC_MRERP_UNVERIFIED", "1")
    site = FakeMrerp().start()
    desks, seen = [], {}

    class Watched(ProcessDesk):
        def close(self):
            if self.process is not None:
                seen["children"] = _children(self.process.pid)  # 网页内核的渲染等子进程
            super().close()

    def desk_factory():
        desk = Watched(command=[sys.executable, "-c", CHILD.format(src=str(SRC))], cwd=str(SRC),
                       env={**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")})
        desks.append(desk)
        return desk

    try:
        host = Host(tmp_path, assemblers={"mrerp": factory(desk_factory)})
        out = host.handle("t1", payload(site.url, docnum="Q-0001"), None)  # 批次线程不碰 Qt：直接在这里跑
        assert out["status"] == "saved" and len(site.saved()) == 1
        assert len(desks) == 1 and desks[0].process.poll() is not None  # 录完：子进程已退出
        time.sleep(1.0)
        assert not [pid for pid in seen.get("children", []) if _alive(pid)]  # 渲染进程也跟着退出
    finally:
        site.stop()
