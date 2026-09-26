"""Rotating logger 配置

输出:
  控制台(stdout)+ 文件(%APPDATA%/Pearnly/logs/companion.log,日轮转 7 天)

(补丁 3)调试模式启用 DEBUG 级别 + 输出 score / fallback 计数到日志
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .config import LOG_DIR, CompanionConfig

LOG_FILE = LOG_DIR / "companion.log"
# 面向客户的「活动记录」:只记人话事件(随 ui_lang 出泰/中),与技术日志分离。
# 客户点托盘「打开日志文件夹」主要看它;companion.log 留给技术排错。
ACTIVITY_FILE = LOG_DIR / "activity.log"

_FMT = "%(asctime)s [%(levelname)s] %(name)s :: %(message)s"
_ACT_FMT = "%(asctime)s  %(message)s"


def setup_logging(cfg: CompanionConfig | None = None) -> logging.Logger:
    cfg = cfg or CompanionConfig.load()
    _setup_activity()
    root = logging.getLogger("pearnly_companion")
    if root.handlers:
        return root  # 已配置
    level = (
        logging.DEBUG
        if cfg.debug_mode
        else getattr(logging, cfg.log_level.upper(), logging.INFO)
    )
    root.setLevel(level)

    fmt = logging.Formatter(_FMT)
    if sys.stderr is not None:  # frozen windowed(无控制台)时 stderr=None → 只写文件
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root.addHandler(sh)

    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=2 * 1024 * 1024, backupCount=7, encoding="utf-8"
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)
    return root


def _setup_activity() -> logging.Logger:
    act = logging.getLogger("pearnly_companion.activity")
    if act.handlers:
        return act
    afh = RotatingFileHandler(
        ACTIVITY_FILE, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
    )
    afh.setFormatter(logging.Formatter(_ACT_FMT, datefmt="%Y-%m-%d %H:%M"))
    act.addHandler(afh)
    act.setLevel(logging.INFO)
    act.propagate = False  # 独立文件 · 不混进 companion.log 也不重复
    return act


def activity(key: str, lang: str | None = None, **fmt) -> None:
    """记一条人话活动事件到 activity.log(随 ui_lang 出泰/中)。低频事件 · 失败不抛。"""
    from .i18n import default_lang, normalize, t

    if not lang:
        try:
            lang = CompanionConfig.load().ui_lang or default_lang()
        except Exception:
            lang = default_lang()
    try:
        _setup_activity().info(t(key, normalize(lang), **fmt))
    except Exception:
        pass


def get_logger(name: str = "pearnly_companion") -> logging.Logger:
    """方便子模块拿到已配置的 logger"""
    clean = str(name or "").strip()
    if clean.startswith("src.companion."):
        clean = "pearnly_companion." + clean.removeprefix("src.companion.")
    elif clean == "src.companion":
        clean = "pearnly_companion"
    return logging.getLogger(clean or "pearnly_companion")
