# -*- coding: utf-8 -*-
"""MR.ERP 代录离线回放的公共夹具：假站点 + 真网页内核（QtWebEngine，offscreen）+ 代录宿主。

- 网页内核在测试主线程（界面线程）；代录批次在另一条线程跑（同 CC 里批次线程与网页内核分开），``spin`` 期间主线程处理界面事件；
- ``Maker``：在界面线程建 ``web_driver.Driver``、交给批次线程用的 ``LocalDesk``（同进程；子进程那条另有测试）；
- ``webengine_works``：先在子进程里试一次网页内核能不能在这台机器上跑（Windows 的 CI 机器是没有桌面的系统服务，可能起不来），
  起不来的回放测试跳过，由纯逻辑测试（``test_mrerp_operator_parts.py``）兜住。
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List

SRC = Path(__file__).resolve().parents[1] / "src"
PASSWORD = "S3cret-pass!"
_PROBE = r"""
import sys
from PySide6.QtCore import QCoreApplication, Qt, QTimer, QUrl
QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWidgets import QApplication
app = QApplication([])
profile = QWebEngineProfile()
page = QWebEnginePage(profile)
out = {}
def loaded(ok):
    page.runJavaScript("JSON.stringify(document.title)", 1, lambda r: (out.update(r=r), app.quit()))
page.loadFinished.connect(loaded)
page.setHtml("<title>ok</title>", QUrl("http://127.0.0.1/"))
QTimer.singleShot(45000, app.quit)
app.exec()
del page
sys.exit(0 if out.get("r") == '"ok"' else 3)
"""
_WORKS: Dict[str, bool] = {}


def webengine_works() -> bool:
    if "v" not in _WORKS:
        env = dict(os.environ, QT_QPA_PLATFORM=os.environ.get("QT_QPA_PLATFORM", "offscreen"))
        try:
            done = subprocess.run([sys.executable, "-c", _PROBE], env=env, timeout=90, capture_output=True)
            _WORKS["v"] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _WORKS["v"] = False
    return _WORKS["v"]


def qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401 — 网页内核要在 QApplication 之前导入

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        app = QApplication([])
    return app


def spin(work: Callable, timeout: float = 120.0, tick: Callable[[], None] = lambda: None):
    """在另一条线程跑 ``work``，主线程处理界面事件直到它结束（``tick`` 每轮在主线程调一次：模拟人在窗口里动）。"""
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    box: Dict = {}

    def run():
        try:
            box["value"] = work()
        except BaseException as error:  # noqa: BLE001
            box["error"] = error

    thread = threading.Thread(target=run, name="mrerp-batch", daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    while thread.is_alive():
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 50)
        tick()
        time.sleep(0.003)
        if time.monotonic() > deadline:
            raise TimeoutError("batch did not finish")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def pump(seconds: float) -> None:
    from PySide6.QtCore import QEventLoop
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 20)
        time.sleep(0.003)


class Maker:
    """界面线程里建 MR.ERP 页面，交出同进程的 desk。记下建过的每一个（看关窗、看人工输入）。"""

    def __init__(self):
        from companion.mrerp_operator.web_driver import Driver, UiCall

        self.drivers: List = []
        self.ui = UiCall(lambda _op, _args, done: done({"ok": True, "driver": Driver()}))

    def __call__(self):
        from companion.mrerp_operator.web_driver import LocalDesk

        driver = self.ui.call("make", {}, 30)["driver"]
        self.drivers.append(driver)
        return LocalDesk(driver)


def factory(maker: Maker, *, secret=("ccbot", PASSWORD), idle_s: float = 0.6, secrets_seen=None):
    """测试用的 MR.ERP 装配：假站点、同进程网页内核、快的等待。"""
    from companion.mrerp_operator import host as mhost

    def read_secret(target):
        if secrets_seen is not None:
            secrets_seen.append(target)
        return secret

    def assemble(state, store, settings, root, notify, member=None):
        return mhost.assemble(state, store, settings, root, notify, member=member, desk_factory=maker, read_secret=read_secret,
                              load_s=20, presence_options={"idle_s": idle_s, "poll_s": 0.1},
                              runner_options={"net_poll_s": 0.3, "net_wait_s": 6})

    assemble.settings_of = mhost.settings_of
    assemble.draft_of = mhost.draft_of
    return assemble


def payload(site_url: str, *, doc_type="purchase", menu="363", batch="m1", index=1, total=1, doc="CWH-1",
            docnum="0001", party="S001", ref="IV-1", date="2026-10-20", vat="7%", lines=None, name="TEST2569",
            company="TEST Trading Co., Ltd.", comidyear="12", credit_days=""):
    return {"payload_version": 18, "entry_mode": "operator",
            "operator": {"target": "mrerp", "batch": {"id": batch, "index": index, "total": total, "doc": doc}},
            "mrerp": {"url": site_url, "connection": "conn1",
                      "account_set": {"comidyear": comidyear, "seldb": "1", "name": name, "company_name": company},
                      "doc_type": doc_type, "menu": menu, "docnum": docnum, "date": date, "party_code": party,
                      "ref_no": ref, "ref_date": date, "vat_rate": vat, "credit_days": credit_days,
                      "lines": lines or [{"code": "P001", "qty": "2", "price": "100"}]}}
