"""CC 桌面版「自动更新被 Windows 拦下」的上报（教程问题第 1 条，2026-10-15）：纯规则，不连库、不依赖框架。

CC 桌面版在心跳（`reports.plan_report`）与文件夹同步（`folder_intake.machine`）里都带 ``update_blocked``：
``{"version": "1.5.2", "at": "2026-10-13T11:18:00Z"}`` = 被拦下的新版本与发现的时刻（UTC）；``null`` = 没被拦。
旧版 CC 桌面版不带这个键，收的一方原样保留上次的值（不知道，不当成没被拦）。CC 桌面版那一侧见
``companion/src/companion/update_block.py``，两边的约定由 ``tests/test_companion_contract.py`` 守住。

网页「连接」只在被拦的版本比这台电脑现在跑的版本新时显示（`current`）：手动装上新版以后、旧值还没被下一次上报清掉之前不显示。
"""
import re
from datetime import datetime, timezone

KEY = "update_blocked"
_VERSION = re.compile(r"^\d{1,4}(\.\d{1,4}){1,3}$")


def _version(value):
    try:
        return tuple(int(part) for part in str(value).split("."))
    except ValueError:
        return ()


def parse(raw):
    """上报里的 ``update_blocked`` → ``{"version", "at"}``（``at`` 为 UTC ISO 8601）；``None``、不合形状都回 ``None``。"""
    if not isinstance(raw, dict):
        return None
    version = str(raw.get("version") or "").strip()
    at = str(raw.get("at") or "").strip()
    if not _VERSION.match(version) or not at or len(at) > 40:
        return None
    try:
        moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None or moment.utcoffset() is None:
        return None   # 不带时区说不清是哪一刻
    moment = moment.astimezone(timezone.utc).replace(microsecond=0)
    return {"version": version, "at": moment.isoformat()}


def current(value, running):
    """网页显示用：存着的值仍然成立（被拦的版本比这台电脑现在跑的 ``running`` 新）就回规整后的值，否则 ``None``。
    不知道现在跑的版本时照实显示。"""
    blocked = parse(value)
    if blocked is None:
        return None
    now = _version(str(running or "").strip()) if _VERSION.match(str(running or "").strip()) else ()
    return blocked if not now or _version(blocked["version"]) > now else None
