"""Small Qt helpers shared by the helper's windows (background probes)."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class ProbeThread(QThread):
    ok = Signal(object)
    err = Signal(object)

    def __init__(self, fn) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            self.ok.emit(self._fn())
        except Exception as exc:  # noqa: BLE001 - the GUI renders a safe error
            self.err.emit(exc)


class Probes:
    """窗口里的后台任务：同类新任务使旧的作废（代次守卫），关窗后回调直接丢弃，不碰已销毁的控件。"""

    def __init__(self) -> None:
        self.alive = True
        self._running: list = []  # 在跑的后台线程（持引用防 GC）
        self._gen: dict = {}

    def spawn(self, kind: str, fn, on_ok, on_err=None) -> None:
        self._gen[kind] = self._gen.get(kind, 0) + 1
        gen = self._gen[kind]
        worker = ProbeThread(fn)
        self._running.append(worker)

        def guard(callback):
            def inner(result=None):
                if self.alive and gen == self._gen.get(kind) and callback is not None:
                    callback(result)

            return inner

        # 线程真正结束(run 返回)后才松引用——否则丢掉运行中 QThread 的最后引用会崩。
        def cleanup():
            if worker in self._running:
                self._running.remove(worker)

        worker.ok.connect(guard(on_ok))
        worker.err.connect(guard(on_err))
        worker.finished.connect(cleanup)
        worker.start()

    def shutdown(self) -> None:
        self.alive = False
        for worker in list(self._running):
            try:
                worker.wait(3000)
            except RuntimeError:
                pass
        self._running.clear()
