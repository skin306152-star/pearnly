# -*- coding: utf-8 -*-
"""小助手自动更新:拉云端 latest.json 比对版本,有新版下载安装包静默安装。

发布版本 = `https://pearnly.com/downloads/companion/latest.json`({"version","url"}),全平台一份,
与小助手连哪家租户无关(2026-09-27 起安装包放在我们自己的服务器,见本仓 `companion/README.md`)。
companion 周期拉它(带时间戳参数防缓存),版本更高则提示 + 一键装。安装包静默运行
(Inno /VERYSILENT)会关旧进程→装新→自启,无需用户手动重装。发布流水线写 latest.json。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
from typing import Optional

import httpx

from .logger import get_logger
from .version import VERSION, version_tuple

log = get_logger(__name__)

DOWNLOAD_SITE = "https://pearnly.com"
_LATEST_PATH = "/downloads/companion/latest.json"


def check_update(backend_url: str, current_version: str = VERSION, timeout: float = 8.0) -> Optional[dict]:
    """有新版返 {version,url},否则 None。失败返 None(静默·不打扰用户)。"""
    if not backend_url:
        return None
    url = backend_url.rstrip("/") + _LATEST_PATH
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            r = c.get(url, params={"t": str(int(time.time()))})  # 时间戳破 CDN 缓存,拿最新发布信息
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.debug("check_update failed: %s", e)
        return None
    latest = str(data.get("version") or "").strip()
    rel_url = str(data.get("url") or "").strip()
    if latest and rel_url and version_tuple(latest) > version_tuple(current_version):
        return {"version": latest, "url": rel_url}
    return None


def download_installer(backend_url: str, rel_url: str, timeout: float = 180.0) -> str:
    """下载新安装包到临时文件,返回路径。"""
    full = rel_url if rel_url.startswith("http") else backend_url.rstrip("/") + rel_url
    fd, path = tempfile.mkstemp(suffix=".exe", prefix="CC-Setup_")
    os.close(fd)
    with httpx.Client(timeout=timeout, follow_redirects=True) as c:
        with c.stream("GET", full) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_bytes(chunk_size=262144):
                    f.write(chunk)
    return path


def run_installer_silent(installer_path: str) -> "subprocess.Popen":
    """静默运行安装包:Inno CloseApplications=force 会关掉当前 companion → 装新 → 自启。

    本进程随之被安装器关闭,故 detached 起,不等待;返回进程,调用方据此看安装包是否已自己退出(没装上)。
    """
    return subprocess.Popen(
        [installer_path, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"],
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        close_fds=True,
    )
