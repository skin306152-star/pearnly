"""Pearnly Companion 主入口:DBF 直写 + 后台索引常驻。

工作流:
  1. 装载 config + 启动 logger
  2. 单实例守卫(同机第二个实例友好退出)
  3. 首次未配对 → 配对窗;已配对 → 托盘常驻(或 --headless console 直写循环)
  4. poll loop:queue_client.poll() 拉任务 → DBF 直写 → 写盘后即重建索引(后台 CDX)→ ack
  5. Ctrl+C / SIGTERM 优雅停止

CLI:
  python -m src.companion.main              # 托盘常驻
  python -m src.companion.main --headless   # 无托盘 console 直写循环(测试/服务器)
  python -m src.companion.main --background # 开机自启:跳过配对窗直接后台常驻(已配对)
  python -m src.companion.main --debug      # 调试日志
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from .config import CompanionConfig
from .logger import get_logger, setup_logging
from .worker import (
    _handle_dbf_task,  # noqa: F401  从 worker 再导出(向后兼容)
    route_task,
)

log = get_logger("pearnly_companion.main")


# ── 信号 / 单实例 ────────────────────────────────────────────
_STOP = False
_INSTANCE_GUARD = None  # 单实例锁,进程存活期间持有(模块级防 GC 提前释放)


def _notify_already_running(cfg: CompanionConfig, headless: bool) -> None:
    """已有实例在跑 → 友好告知后退出(标准托盘应用行为)。headless 只记日志。"""
    from .i18n import default_lang, normalize, t

    lang = normalize(getattr(cfg, "ui_lang", "") or default_lang())
    if headless:
        print(t("already_running_body", lang))
        return
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from .gui_tray import pearnly_icon

        app = QApplication.instance() or QApplication([])  # noqa: F841
        box = QMessageBox()
        box.setWindowIcon(pearnly_icon())
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(t("already_running_title", lang))
        box.setText(t("already_running_body", lang))
        box.exec()
    except Exception:
        print(t("already_running_body", lang))


def _stop_handler(signum, _frame):
    global _STOP
    _STOP = True
    log.info(f"received signal {signum}, shutting down…")


# Win10/11 的托盘 Toast 通知按进程 AppUserModelID 归属;不注册显式 AUMID 时
# QSystemTrayIcon.showMessage 会被系统当成无主通知静默丢弃(录入完成气泡看不到)。
# 须在任何 QApplication 创建前调用,且与开始菜单快捷方式的 AUMID 一致。
APP_USER_MODEL_ID = "Pearnly.Companion"


def _register_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


# ── 步骤 ─────────────────────────────────────────────────────
def step_poll_loop(configs) -> None:
    """console 直写循环(headless / 测试):拉任务 → DBF 直写 → ack。"""
    from .multi_profile_worker import MultiProfileWorker

    if isinstance(configs, CompanionConfig):
        configs = [configs]
    configs = list(configs)

    def handle(cfg, qc, task, index_batch):
        log.info(
            "received task %s · profile=%s · invoice_no=%s · attempt=%s",
            task.id,
            cfg.profile_id,
            task.invoice_no,
            task.attempt_count,
        )
        return route_task(cfg, qc, task, index_batch=index_batch)

    worker = MultiProfileWorker(
        configs,
        task_handler=handle,
        on_status=lambda profile_id, key, detail: (
            log.error("poll error · profile=%s · %s", profile_id, detail)
            if key == "offline"
            else None
        ),
    )
    log.info("multi-profile poll loop started · profiles=%d", len(configs))
    try:
        worker.run_forever(lambda: _STOP)
    finally:
        worker.close(signal_offline=True)


def _run_dbf(store, args) -> int:
    """DBF 直写入口:连接管理 → 校验账套白名单 → 托盘常驻(或 --headless console)。"""
    from .profile_store import ProfileStore

    configs = store.runtime_configs(include_paused=True)
    if not configs and not args.background and not args.headless:
        from .gui_profiles import open_profile_manager

        open_profile_manager()
        store = ProfileStore.load()
        configs = store.runtime_configs(include_paused=True)

    if not configs:
        log.error("dbf 需要至少一个已配对 Profile")
        return 2
    for cfg in configs:
        if cfg.paused:
            continue
        if not cfg.account_dir or not (cfg.account_set or "").strip():
            log.error(
                "dbf Profile 缺客户所选账套;profile=%s dir=%r set=%r",
                cfg.profile_id,
                cfg.account_dir,
                cfg.account_set,
            )
            return 2

    if args.headless:
        try:
            step_poll_loop(configs)
        except KeyboardInterrupt:
            pass
        log.info("companion exited")
        return 0

    from .gui_tray import TrayApp

    log.info("托盘常驻 · method=dbf · profiles=%d", len(configs))
    return TrayApp(configs, open_connections=not args.background).run()


# ── 入口 ─────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Pearnly Companion")
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--background",
        action="store_true",
        help="开机自启用:跳过配对窗,直接后台常驻(已配对)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无托盘 GUI 的 console 直写循环(测试/服务器)",
    )
    args = parser.parse_args()

    _register_app_user_model_id()  # 必须早于任何 QApplication,否则托盘通知不弹

    signal.signal(signal.SIGINT, _stop_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _stop_handler)

    from .profile_store import ProfileStore

    store = ProfileStore.load()
    configs = store.runtime_configs(include_paused=True)
    cfg = configs[0] if configs else store.new_runtime_config()
    # 单实例守卫:同机第二个实例会争抢账套 DBF/CDX → 检测到已在运行就友好退出。
    global _INSTANCE_GUARD
    from . import single_instance

    _INSTANCE_GUARD = single_instance.acquire()
    if _INSTANCE_GUARD is None:
        _notify_already_running(cfg, headless=args.headless)
        return 0
    # 若 launcher 选了非默认端口,覆盖 cfg.backend_url(env override)
    _env_port = os.environ.get("PEARNLY_COMPANION_PORT")
    if _env_port and _env_port not in cfg.backend_url:
        cfg.backend_url = f"http://127.0.0.1:{_env_port}"
        cfg.save()
        print(f"[main] backend_url overridden from env: {cfg.backend_url}")
    if args.debug:
        cfg.update(debug_mode=True, log_level="DEBUG").save()

    # backend/debug 属全局配置；保存后重载，让每个 runtime Profile 都拿到同一份新值。
    if _env_port or args.debug:
        store = ProfileStore.load()
        configs = store.runtime_configs(include_paused=True)
        cfg = configs[0] if configs else store.new_runtime_config()

    setup_logging(cfg)

    # DBF 直写法:不碰界面。首次未配对进配对流程;已配对托盘常驻。
    return _run_dbf(store, args)


if __name__ == "__main__":
    sys.exit(main())
