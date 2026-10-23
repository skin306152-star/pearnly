"""我们最新发布的小助手版本，随心跳回复带给小助手（用户 2026-10-09：发布后客户端就能收到，不要定时检查更新）。

发布脚本（`infrastructure/production/receive-companion.py`）把 `latest.json` 写在服务器的下载目录里，
Cowork 容器只读挂在 `RELEASE_FILE`（`infrastructure/production/compose.yml`）。这里只读本机这个小文件，不联网；
按修改时间缓存，文件不在（测试机、本地开发）或内容不对就不带，小助手照常工作。
"""
import json
import os
import re

RELEASE_FILE = "/opt/pearnly/companion-release/latest.json"
_VERSION = re.compile(r"^\d{1,4}(\.\d{1,4}){1,3}$")
_cache = {"mtime": None, "value": None}


def latest(path=RELEASE_FILE):
    """``{"version", "url"}``；读不到返回 None。"""
    try:
        mtime = os.stat(path).st_mtime_ns
    except OSError:
        return None
    if _cache["mtime"] == mtime and _cache.get("path") == path:
        return _cache["value"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    version = str((data or {}).get("version") or "").strip() if isinstance(data, dict) else ""
    url = str(data.get("url") or "").strip() if isinstance(data, dict) else ""
    value = {"version": version, "url": url} if (_VERSION.match(version) and url.startswith("/downloads/companion/")
                                                 and len(url) <= 300) else None
    _cache.update(mtime=mtime, path=path, value=value)
    return value
