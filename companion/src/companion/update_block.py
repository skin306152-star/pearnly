# -*- coding: utf-8 -*-
"""自动更新被 Windows 拦下：看得见、有出路（教程问题第 1 条，2026-10-13 录制笔记本：智能应用控制拦了没签名的安装包）。

只把「被拦」如实告诉人，**不绕开、不规避系统的拦截**（不签名、不改系统设置、不换启动方式）：

- 判据只用可靠的一种：启动安装包时系统直接拒绝创建进程，错误码属于应用控制 / 代码完整性 / 软件限制策略 / 杀毒拦截
  （``BLOCKED_ERRORS``）。安装包自己退出、5 分钟没关掉本进程、重开后版本没变，原因说不准，只算「没装上」（failed）。
- 结果记在本机（``update_state.json``），重开 CC 也记得：同一版本按 ``auto_update.retry_delay`` 退避再试，不每次启动都试一遍；
  装上新版（本机版本不低于记下的版本）就清掉。
- 心跳与文件夹同步带 ``update_blocked``（被拦的版本 + 发现时刻，UTC），网页「连接」如实显示一句；没被拦时带 null。
  服务端的收法见 ``apps/pearnly_cowork/pearnly_cowork/connections/desktop_update.py``，约定由 ``tests/test_companion_contract.py`` 守住。

本模块不碰 Qt：流程在 ``auto_update.AutoUpdate``，托盘只显示与打开下载页。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .version import VERSION, version_tuple

# 启动安装包时这些错误码 = 系统按策略拒绝运行这个文件（CreateProcess 的 GetLastError，Python 放在 OSError.winerror）
BLOCKED_ERRORS = frozenset({
    225,    # ERROR_VIRUS_INFECTED：杀毒（Microsoft Defender 等）认为文件有毒或是不受欢迎的软件
    226,    # ERROR_VIRUS_DELETED：同上，文件已被移走
    577,    # ERROR_INVALID_IMAGE_HASH：代码完整性校验不过（应用控制、智能应用控制）
    786,    # ERROR_ACCESS_DISABLED_NO_SAFER_UI_BY_POLICY：软件限制策略
    1260,   # ERROR_ACCESS_DISABLED_BY_POLICY：组策略 / AppLocker 拦了这个程序
    # ERROR_SYSTEM_INTEGRITY_*：应用控制策略（4551「An Application Control policy has blocked this file」）
    # 与智能应用控制的信誉判定（4556–4559、4580–4582）
    *range(4550, 4560), 4580, 4581, 4582,
})

log = logging.getLogger("pearnly_companion.update_block")

STATES = ("launched", "blocked", "failed")
STATE_FILE = "update_state.json"
_LOCK = threading.RLock()
_REPORT: dict = {"value": None}   # 心跳线程读的那一份（本进程内，不为心跳每拍读盘）


def blocked_code(error: BaseException) -> Optional[int]:
    """启动安装包抛的异常是「被系统拦下」就回错误码，否则 None（找不到文件、坏包等不算被拦）。"""
    code = getattr(error, "winerror", None)
    return code if isinstance(code, int) and code in BLOCKED_ERRORS else None


def state_file() -> Path:
    """本机记录放在 CC 的数据目录（与 companion.json 同一处）；测试用环境变量换到临时目录。"""
    override = os.environ.get("PEARNLY_UPDATE_STATE_FILE")
    if override:
        return Path(override)
    from .config import APPDATA_DIR   # 延迟导入：config 一导入就建目录，服务端的约定测试只读本模块的纯函数

    return APPDATA_DIR / STATE_FILE


@dataclass
class Record:
    """对某个新版本最近一次安装尝试的结果。``at`` 是 UTC 时间戳（秒）。"""

    version: str
    state: str          # launched（安装包已启动，还不知道结果）| blocked | failed
    at: float
    attempts: int = 1
    code: Optional[int] = None
    notified: bool = False   # 「被拦」的通知弹过了（同一版本只弹一次）


def _record(raw) -> Optional[Record]:
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    state = raw.get("state")
    try:
        at = float(raw.get("at"))
        attempts = max(1, int(raw.get("attempts") or 1))
    except (TypeError, ValueError):
        return None
    code = raw.get("code")
    if version_tuple(version) == (0,) or state not in STATES or at <= 0:
        return None
    return Record(version, state, at, attempts, code if isinstance(code, int) else None, bool(raw.get("notified")))


def report_of(record: Optional[Record]) -> Optional[dict]:
    """心跳 / 同步里的 ``update_blocked``：被拦了才有 ``{"version", "at"}``（UTC ISO 8601，带 Z），否则 None。"""
    if record is None or record.state != "blocked":
        return None
    at = datetime.fromtimestamp(record.at, tz=timezone.utc).replace(microsecond=0)
    return {"version": record.version, "at": at.isoformat().replace("+00:00", "Z")}


def reported() -> Optional[dict]:
    """心跳线程、文件夹线程取当前要报的 ``update_blocked``（由 ``UpdateMemory`` 在读写记录时更新）。"""
    with _LOCK:
        value = _REPORT["value"]
        return dict(value) if value else None


class UpdateMemory:
    """本机记下的安装结果。``path`` 为 None 只记在内存（测试、没有托盘时）。

    读的时候顺便结账：本机已经不低于记下的版本 = 装上了，删掉记录；上次启动了安装包（launched）而这次还是旧版本 =
    没装上（failed，尝试次数不变）。"""

    def __init__(self, path: Optional[Path] = None, current: str = VERSION, *, publish: bool = True) -> None:
        self.path = Path(path) if path else None
        self.publish = publish   # 托盘那一份交给心跳；流程测试里的临时记忆不碰本进程要报的值
        self.record: Optional[Record] = None
        loaded = self._read()
        if loaded is not None and version_tuple(loaded.version) <= version_tuple(current):
            self._write(None)   # 装上了（或手动装了更新的版本）
        elif loaded is not None and loaded.state == "launched":
            log.warning("上次启动了 v%s 的安装包，重开后仍是 v%s：算这次没装上", loaded.version, current)
            loaded.state = "failed"   # 安装包启动过，重开后还是旧版本
            self._write(loaded)
        else:
            self.record = loaded
            self._publish()

    def attempt(self, version: str, now: float) -> Record:
        """要启动安装包了：先记下（安装器会关掉本进程，记在启动之后可能来不及写）。同一版本累计次数。"""
        before = self.record
        same = before is not None and before.version == version
        record = Record(version, "launched", now, before.attempts + 1 if same else 1,
                        notified=bool(same and before.notified))
        self._write(record)
        return record

    def finish(self, version: str, state: str, now: float, code: Optional[int] = None) -> Record:
        """这次没装上：blocked（系统拦下）或 failed（别的原因）。"""
        before = self.record
        same = before is not None and before.version == version
        record = Record(version, state, now, before.attempts if same else 1, code,
                        notified=bool(same and before.notified))
        self._write(record)
        return record

    def blocked(self, version: str = "") -> Optional[Record]:
        record = self.record
        if record is None or record.state != "blocked" or (version and record.version != version):
            return None
        return record

    def take_notice(self) -> Optional[str]:
        """被拦了、还没弹过通知：记成已弹，回版本号（同一版本只弹一次，重开 CC 也不再弹）。"""
        record = self.blocked()
        if record is None or record.notified:
            return None
        record.notified = True
        self._write(record)
        return record.version

    # ── 读写 ──
    def _read(self) -> Optional[Record]:
        if self.path is None:
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                return _record(json.load(handle))
        except (OSError, ValueError):
            return None

    def _write(self, record: Optional[Record]) -> None:
        self.record = record
        self._publish()
        if self.path is None:
            return
        try:
            with _LOCK:
                if record is None:
                    self.path.unlink(missing_ok=True)
                    return
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_name(self.path.name + ".tmp")
                with open(temporary, "w", encoding="utf-8") as handle:
                    json.dump(asdict(record), handle, ensure_ascii=False, sort_keys=True)
                os.replace(temporary, self.path)
        except OSError:
            # 记不下来只是重开后少了退避：本次运行内存里照样有，不影响取任务与推送
            log.warning("记录自动更新结果失败", exc_info=True)

    def _publish(self) -> None:
        if not self.publish:
            return
        with _LOCK:
            _REPORT["value"] = report_of(self.record)
