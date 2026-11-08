# -*- coding: utf-8 -*-
"""CC 窗口画不出来时自动改用软件绘制（2026-11-08，办公室一台老 Acer 一体机装好 CC 后窗口整片浅灰、登录页不出来）。

网页内核（QtWebEngine / Chromium）默认用显卡画页面；老显卡、显卡驱动太旧或有毛病的电脑上它可能一帧都画不出来，
窗口只剩页面底色，什么都点不了。这里按类处理所有这样的电脑，不针对某一台：

- 绘制档位（``LEVELS``）：0 = 默认用显卡；1 = 网页内核不用显卡（``--disable-gpu --disable-gpu-compositing``），
  Qt 的画面合成改用系统自带的软件渲染（D3D11 WARP，``QSG_RHI_PREFER_SOFTWARE_RENDERER``）。档位记在本机
  ``%APPDATA%\\Pearnly\\tomo\\render.json``，之后每次打开窗口都按它；不永久锁死：CC 升了 1.x 版或记下满 30 天就回到显卡
  重新试一次（换了驱动能画就一直用显卡，还画不出来再自动换回）。
- 判断「画不出来」（``RenderWatch``）：自己的页面加载成功、窗口看得见，几次截取网页区域都是一整片同一种颜色，
  而页面里其实已经有字（登录页、对话页都有文字和输入框，画出来不会整片一个颜色），就是没画出来。页面还没有字
  （网慢、还在取数据）、窗口看不见（关着、最小化、锁屏、被挡住）时不判断。
- 画不出来且还有下一档：记下一档，窗口进程请托盘重开窗口（``restart``）后退出，新的窗口进程按新档位起网页内核。
  已经是最后一档就只记日志，不来回重开。
- 窗口进程的 Qt / 网页内核警告写进 ``logs/window.log``（``install_qt_log``，另开 ``qt.webenginecontext`` 的启动信息：
  用的是哪块显卡、哪种绘制方式），以前这些都写到没人看的标准错误里，出了空白窗口日志里什么都没有。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable, Optional

from PySide6.QtCore import QObject, Qt, QTimer
from PySide6.QtGui import QImage

from .logger import get_logger

log = get_logger(__name__)

ENV_CHROMIUM_FLAGS = "QTWEBENGINE_CHROMIUM_FLAGS"
SOFTWARE_FLAGS = ("--disable-gpu", "--disable-gpu-compositing")
# 每一档要设的环境变量（网页内核在建 QApplication 之前读它们）；值为元组的是要并进 Chromium 参数的
LEVELS = (
    {},
    {ENV_CHROMIUM_FLAGS: SOFTWARE_FLAGS, "QSG_RHI_PREFER_SOFTWARE_RENDERER": "1"},
)
LEVEL_NAMES = ("显卡", "软件绘制")
RETRY_DAYS = 30                    # 软件绘制记下这么多天后回到显卡重新试一次（CC 升了 1.x 版也重新试）
PROBES_MS = (3000, 6000, 10000)   # 页面加载成功后第几毫秒截一次；每次都是一整片同一种颜色才算画不出来
SAMPLE = 48                        # 截图缩成 SAMPLE×SAMPLE（平滑缩放，文字、边框会让格子颜色不一样）再比
TOLERANCE = 1                      # 各通道最大差不超过这个就算同一种颜色（真画不出来时一点差别都没有）
TEXT_SCRIPT = "((document.body&&document.body.innerText)||'').trim().length"   # 页面里有多少字
QT_LOG_LIMIT = 300                 # 一个窗口进程最多往 window.log 写这么多行 Qt 消息（同一句只写一次）


def state_file() -> Path:
    from .config import APPDATA_DIR

    return APPDATA_DIR / "tomo" / "render.json"


def load_level(path: Optional[Path] = None, *, version: Optional[str] = None,
               clock: Callable[[], float] = time.time) -> int:
    """本机当前的绘制档位；没有记录、读不出、超出范围都按 0（用显卡）。

    软件绘制不永久锁死：记下时的 CC 版本前两段（如 1.12）变了、或记下超过 ``RETRY_DAYS`` 天，就回到显卡重新试一次
    （换了显卡驱动、或新版网页内核能画了就一直用显卡；还画不出来再自动换回软件绘制，代价是那一次多等十来秒）。"""
    path = path or state_file()
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        level = int(record.get("level", 0))
    except Exception:
        return 0
    if not 0 <= level < len(LEVELS):
        return 0
    if level > 0:
        version = _version() if version is None else version
        try:
            age = clock() - float(record.get("at") or 0)
        except (TypeError, ValueError):
            age = RETRY_DAYS * 86400
        if _series(record.get("version")) != _series(version) or age >= RETRY_DAYS * 86400:
            log.info("CC 窗口重新试用显卡绘制（软件绘制记于 %s 版、%d 天前）", record.get("version") or "?", age // 86400)
            return 0
    return level


def _version() -> str:
    from .version import VERSION

    return VERSION


def _series(version) -> str:
    """版本号前两段（1.12.19 → 1.12）。"""
    return ".".join(str(version or "").split(".")[:2])


def escalate(reason: str, path: Optional[Path] = None, *, current: Optional[int] = None,
             clock: Callable[[], float] = time.time, version: Optional[str] = None) -> Optional[int]:
    """换到下一档并记下来（连同当时的 CC 版本与时间，见 ``load_level``）；已经是最后一档（或写不进去）返回 None。"""
    path = path or state_file()
    level = load_level(path, version=version, clock=clock) if current is None else current
    if level + 1 >= len(LEVELS):
        return None
    record = {"level": level + 1, "reason": str(reason or ""), "at": int(clock()),
              "version": _version() if version is None else version}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    except Exception:
        log.exception("记绘制档位失败")
        return None
    return level + 1


def apply(level: int, env=None) -> dict:
    """把这一档的设置写进环境变量（默认 ``os.environ``），返回实际写了什么。Chromium 参数并进已有的，不覆盖别人设的。"""
    env = os.environ if env is None else env
    changed = {}
    for key, value in LEVELS[level if 0 <= level < len(LEVELS) else 0].items():
        if isinstance(value, tuple):
            have = str(env.get(key) or "").split()
            merged = " ".join(have + [flag for flag in value if flag not in have])
            env[key] = changed[key] = merged
        else:
            env[key] = changed[key] = value
    return changed


def is_blank(image) -> bool:
    """截图是不是一整片同一种颜色（空图也算）。``image`` 是 QImage。"""
    if image is None or image.isNull() or image.width() < 2 or image.height() < 2:
        return True
    small = image.convertToFormat(QImage.Format.Format_RGB32).scaled(
        SAMPLE, SAMPLE, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    first = small.pixelColor(0, 0)
    base = (first.red(), first.green(), first.blue())
    for y in range(small.height()):
        for x in range(small.width()):
            color = small.pixelColor(x, y)
            if max(abs(color.red() - base[0]), abs(color.green() - base[1]), abs(color.blue() - base[2])) > TOLERANCE:
                return False
    return True


class RenderWatch(QObject):
    """盯着窗口里的网页：自己的页面加载成功后截几次图，一直是一整片同一种颜色就调 ``on_blank``（每次加载最多一次）。

    ``grab`` 返回网页区域的截图（QImage）；``visible`` 说窗口现在有没有人看得见；``own`` 判断地址是不是 CC 自己的页面
    （别的网站、错误页不判断）。"""

    def __init__(self, page, *, grab: Callable[[], object], visible: Callable[[], bool],
                 own: Callable[[str], bool], on_blank: Callable[[str], None],
                 probes: Iterable[int] = PROBES_MS, level: int = 0) -> None:
        super().__init__()
        self._page = page
        self._grab = grab
        self._visible = visible
        self._own = own
        self._on_blank = on_blank
        self._probes = tuple(probes)
        self._level = level
        self._timers: list = []
        self._painted = False
        page.loadStarted.connect(self._reset)
        page.loadFinished.connect(self._loaded)
        terminated = getattr(page, "renderProcessTerminated", None)
        if terminated is not None:
            terminated.connect(self._terminated)

    def _reset(self) -> None:
        for timer in self._timers:
            timer.stop()
        self._timers = []

    def _loaded(self, ok: bool) -> None:
        url = self._page.url().toString()
        log.info("CC 窗口页面加载%s · %s · 绘制：%s", "完成" if ok else "失败", _short(url),
                 LEVEL_NAMES[self._level] if 0 <= self._level < len(LEVEL_NAMES) else self._level)
        self._reset()
        if not ok or not self._own(url) or self._painted:
            return
        last = self._probes[-1] if self._probes else 0
        for delay in self._probes:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda delay=delay, last=last, url=url: self._probe(delay == last, url))
            timer.start(delay)
            self._timers.append(timer)

    def _probe(self, last: bool, url: str) -> None:
        if not self._timers:
            return
        if not self._visible():
            self._reset()   # 没人看得见时截不到真画面：这次加载不判断
            return
        try:
            blank = is_blank(self._grab())
        except Exception:
            log.exception("截取 CC 窗口画面失败")
            self._reset()
            return
        if not blank:
            self._painted = True   # 画得出来：这个窗口进程之后不再查
            self._reset()
            log.info("CC 窗口页面已画出")
            return
        if last:
            self._reset()
            # 页面里已经有字却一整片空白才是画不出来；还没有字（网慢、页面还在取数据）不算
            self._page.runJavaScript(TEXT_SCRIPT, 0, lambda count, url=url: self._judged(count, url))

    def _judged(self, count, url: str) -> None:
        if self._painted or not isinstance(count, (int, float)) or count <= 0:
            log.info("CC 窗口页面还没有内容，这次不判断 · %s", _short(url))
            return
        log.warning("CC 窗口页面已有内容，加载完成 %s 秒后仍是一整片空白 · %s · 绘制：%s",
                    self._probes[-1] // 1000, _short(url), LEVEL_NAMES[self._level]
                    if 0 <= self._level < len(LEVEL_NAMES) else self._level)
        self._on_blank(url)

    def _terminated(self, status, code) -> None:
        log.warning("CC 窗口网页内核的页面进程退出 · 状态 %s · 代码 %s", getattr(status, "name", status), code)



def _short(url: str) -> str:
    return str(url or "").split("?", 1)[0].split("#", 1)[0][:200]


_qt_logged: set = set()


def qt_message(mode, context, message: str) -> None:
    """Qt 的消息处理函数（``qInstallMessageHandler``）：写进 window.log；页面自己的控制台输出（``js``）不写，同一句只写一次。"""
    category = str(getattr(context, "category", "") or "")
    if category == "js" or len(_qt_logged) >= QT_LOG_LIMIT:
        return
    text = str(message or "")
    key = (category, text)
    if key in _qt_logged:
        return
    _qt_logged.add(key)
    name = getattr(mode, "name", str(mode))
    level = {"QtDebugMsg": 20, "QtInfoMsg": 20, "QtWarningMsg": 30, "QtCriticalMsg": 40, "QtFatalMsg": 50}.get(name, 30)
    log.log(level, "Qt[%s] %s", category or "default", text[:2000])


def install_qt_log() -> None:
    """建 QApplication 之前调：Qt 消息进 window.log，并打开网页内核启动信息（显卡、绘制方式）那一类的输出。"""
    from PySide6.QtCore import QLoggingCategory, qInstallMessageHandler

    QLoggingCategory.setFilterRules("qt.webenginecontext.debug=true")
    qInstallMessageHandler(qt_message)
