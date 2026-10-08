# -*- coding: utf-8 -*-
"""本机记下「哪个推送任务已经写进账套」，同一个任务被服务器重发时不再写第二张。

只认推送任务号（服务器的推送记录号），不看发票号、金额或账套里有没有同号的单：会计再推一次是新任务，
照写（用户 2026-09-30、2026-10-08：不查重、不拦）。这里只防一种情况 —— 写完了但回执没送到服务器
（断网、关机），服务器过一会儿把同一个任务再发下来。

文件在 ``%APPDATA%\\Pearnly\\written_tasks.json``，只留最近 ``KEEP`` 个任务，读写失败就当没记过（照常写）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

from .config import APPDATA_DIR
from .logger import get_logger

log = get_logger(__name__)

FILE = APPDATA_DIR / "written_tasks.json"
KEEP = 1000
_LOCK = threading.Lock()


def _load(path: Path) -> dict:
    try:
        with open(path, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def written(task_id: str, path: Optional[Path] = None) -> Optional[dict]:
    """这个任务上次写成功时的回执（docnum / line_modes / meta）；没写过返回 None。"""
    if not task_id:
        return None
    with _LOCK:
        found = _load(path or FILE).get(str(task_id))
    return found if isinstance(found, dict) else None


def remember(task_id: str, receipt: dict, path: Optional[Path] = None) -> None:
    """写成功、回执发出前记下。"""
    if not task_id:
        return
    path = path or FILE
    with _LOCK:
        data = _load(path)
        data[str(task_id)] = {**receipt, "at": time.time()}
        if len(data) > KEEP:
            newest = sorted(data.items(), key=lambda item: float((item[1] or {}).get("at") or 0))[-KEEP:]
            data = dict(newest)
        temporary = path.with_suffix(".part")
        try:
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False)
            os.replace(temporary, path)
        except OSError as exc:
            log.warning("written task journal not saved: %s", exc)
