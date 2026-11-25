"""Pearnly Companion 主入口:DBF 直写 + 后台索引常驻。

工作流:
  1. 装载 config + 启动 logger
  2. 单实例守卫(同机第二个实例友好退出)
  3. 托盘常驻 + Tomo 窗口(手动打开时直接打开 Tomo;没配对 Express 也能用 Tomo,连接在「连接与设置」里配)
     (或 --headless console 直写循环,须已配对)
  4. poll loop:queue_client.poll() 拉任务 → DBF 直写 → 写盘后即重建索引(后台 CDX)→ ack
  5. Ctrl+C / SIGTERM 优雅停止

CLI:
  python -m src.companion.main              # 托盘常驻
  python -m src.companion.main --headless   # 无托盘 console 直写循环(测试/服务器)
  python -m src.companion.main --background # 开机自启:后台常驻,不弹 Tomo 窗口
  python -m src.companion.main --debug      # 调试日志
  python -m src.companion.main --window     # CC 窗口进程(只由托盘拉起,window_main.py;不占单实例锁)
  python -m src.companion.main --cloud      # 云端那份 CC(runtime_mode):无托盘/窗口/通知/更新/自启/防睡眠,只跑直写 + 按需请求 + 代录
  python -m src.companion.main --cloud --pair <令牌>  # 云端设备配对(只在云端安装命令里带一次;配完退出)
"""

from __future__ import annotations

import argparse
import os
import signal
import sys

from . import runtime_mode
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
    """已有实例在跑 → 让它把 Tomo 窗口拿到前面(同 Claude 桌面端再点一次图标);连不上才提示后退出。headless 只记日志。"""
    from .i18n import default_lang, normalize, t

    lang = normalize(getattr(cfg, "ui_lang", "") or default_lang())
    if headless:
        print(t("already_running_body", lang))
        return
    try:
        from .gui_font import qt_app
        from . import instance_channel

        qt_app()
        if instance_channel.ask_running_to_show():
            return
    except Exception:
        log.exception("通知正在运行的实例失败")
    try:
        from PySide6.QtWidgets import QMessageBox

        from .gui_tray import pearnly_icon

        from .gui_font import qt_app

        app = qt_app()  # noqa: F841
        box = QMessageBox()
        box.setWindowIcon(pearnly_icon())
        box.setIcon(QMessageBox.Information)
        box.setWindowTitle(t("already_running_title", lang))
        box.setText(t("already_running_body", lang))
        # 弹出时置前（原生小窗一律置前，11-23）：这个进程是用户刚双击起来的，有前台权
        from PySide6.QtCore import Qt

        box.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        box.show()
        box.raise_()
        box.activateWindow()
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
    from . import package_identity

    if package_identity.packaged():
        return  # Microsoft Store 版：进程本来就带包里的 AppUserModelID，另设的那个在包里没有快捷方式，通知会被丢掉
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


# ── 步骤 ─────────────────────────────────────────────────────
def step_poll_loop(configs, stop=None) -> None:
    """console 直写循环(headless / 测试):拉任务 → DBF 直写 → ack。``stop``：要停时回真（默认看 SIGTERM / Ctrl+C）。"""
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

    from . import device_link
    from .profile_store import ProfileStore

    def settled(cfg, status):   # 没有界面：密钥不能再用就直接改本机成员（这次运行里这个成员已停用）
        device_link.settle(ProfileStore.load(), str(getattr(cfg, "profile_id", "") or ""), status)

    device_link.ANNOUNCER.on_result = settled
    worker = MultiProfileWorker(
        configs,
        task_handler=handle,
        announce=device_link.ANNOUNCER.announce,
        on_status=lambda profile_id, key, detail: (
            log.error("poll error · profile=%s · %s", profile_id, detail)
            if key == "offline"
            else None
        ),
    )
    log.info("multi-profile poll loop started · profiles=%d", len(configs))
    try:
        worker.run_forever(stop or (lambda: _STOP))
    finally:
        worker.close(signal_offline=True)


def _cloud_configs() -> list:
    """云端本机现在的成员（只 Express 成员，``runtime_mode.serves``）；缺账套的不起。"""
    from .profile_store import ProfileStore

    configs = [cfg for cfg in ProfileStore.load().runtime_configs(include_paused=True) if runtime_mode.serves(cfg)]
    return [cfg for cfg in configs if cfg.paused or (cfg.account_dir and (cfg.account_set or "").strip())]


def step_cloud_loop() -> None:
    """云端那份 CC（``--cloud``，runtime_mode）：取任务直写（代录单照常交代录宿主，在云端会话里开 Express）+ 按需请求。
    托盘、窗口、通知、自动更新、开机自启、防睡眠、文件夹自动录入一样都不起；MR.ERP 成员调用前已去掉（runtime_mode.serves）。

    成员由桥下发（``ctl\\members.json``，D3，``cloud_side.take_members``）：没有成员时等着、不退出（它是登录时从启动文件夹起来的，
    一退出就要等到下次登录）；成员变了（新站点登记、站点被删）停下直写循环、重读成员再起。"""
    from .cloud_link import cloud_side
    from .request_worker import RequestWorker

    stop = lambda: _STOP  # noqa: E731
    # 先等隧道盘与桥交来的会话（没有就退避等，最多 15 分钟看一次，不退出），装上隧道 transport：之后所有联网请求都走隧道
    tunnel = cloud_side.start(stop)
    if tunnel is None:
        return
    link = tunnel.link
    try:
        cloud_side.check_ctl(link)   # 桥在会话之后紧跟着写的成员文件：不等监视线程，先看一眼
    except Exception:
        log.exception("云端 ctl 检查失败")
    current: list = []
    requests = RequestWorker(lambda: current)
    requests.start()
    said = None
    try:
        while not stop():
            generation = link.generation
            current[:] = _cloud_configs()
            if not current:
                if said != 0:
                    log.info("云端没有成员，等桥下发 · 结果=%s", link.members_code or "-")
                    said = 0
                link.wait_change(generation, stop)
                continue
            said = len(current)
            log.info("云端成员就绪 · profiles=%d", len(current))
            step_poll_loop(list(current), stop=lambda: stop() or link.generation != generation)
    finally:
        requests.stop()
        tunnel.stop()


def _run_dbf(store, args) -> int:
    """入口:校验账套白名单 → 托盘常驻 + Tomo(或 --headless console 直写,须已配对;--cloud 云端那份)。"""
    configs = store.runtime_configs(include_paused=True)
    cloud = runtime_mode.cloud()
    console = args.headless or cloud   # 没有界面：缺配对、缺账套就退出（托盘模式照常启动）
    if cloud:
        configs = [cfg for cfg in configs if runtime_mode.serves(cfg)]
        log.info("云端模式 · method=dbf · profiles=%d", len(configs))
    if cloud:
        # 云端那份：成员由桥下发，没有就等（step_cloud_loop），不以 2 退出；缺账套的成员不起
        try:
            step_cloud_loop()
        except KeyboardInterrupt:
            pass
        log.info("companion exited")
        return 0
    if not configs and console:
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
            if console:
                return 2   # 托盘模式照常启动：Tomo 不受影响，连接问题在「连接与设置」里看到并修

    if console:
        try:
            step_poll_loop(configs)
        except KeyboardInterrupt:
            pass
        log.info("companion exited")
        return 0

    from .gui_tray import TrayApp

    log.info("托盘常驻 · method=dbf · profiles=%d", len(configs))
    return TrayApp(configs, open_tomo=not args.background).run()


def _pair(token: str, cloud: bool, tunnel: str = "") -> int:
    """``--cloud --pair <令牌>``:只配对、不跑直写,配完以退出码报结果。不占单实例锁(云端 CC 可能已在跑)。令牌不进日志。"""
    if not cloud:
        return 2
    setup_logging()
    from .cloud_link import cloud_side

    return cloud_side.run_pair(token, root=tunnel or None)


# ── 入口 ─────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Pearnly Companion")
    parser.add_argument("--debug", action="store_true", help="Enable verbose logging")
    parser.add_argument(
        "--background",
        action="store_true",
        help="开机自启用:后台常驻,不弹 Tomo 窗口",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="无托盘 GUI 的 console 直写循环(测试/服务器)",
    )
    parser.add_argument(
        "--cloud",
        action="store_true",
        help="云端那份 CC(桥启动):无托盘/窗口/通知/更新/自启/防睡眠,只跑直写、按需请求与代录",
    )
    # 桥电脑的显示名(心跳 link.via);也可经环境变量 PEARNLY_CLOUD_VIA 交过来
    parser.add_argument("--cloud-via", default=None, help=argparse.SUPPRESS)
    # 云端设备配对的一次性令牌(只在云端安装命令里带一次;配完即退出,退出码见 cloud_link.cloud_side.run_pair)
    parser.add_argument("--pair", default=None, help=argparse.SUPPRESS)
    # 安装脚本交来的隧道根(它自己所在的 \\tsclient\<盘>\cc 的上一级);只配对时用,合规才用
    parser.add_argument("--tunnel", default="", help=argparse.SUPPRESS)
    parser.add_argument("--window", action="store_true", help=argparse.SUPPRESS)  # CC 窗口进程(托盘拉起)
    # MR.ERP 代录的网页窗口进程(ADR-026:每批由代录宿主拉起、录完退出;网页内核只在这里,托盘不加载)
    parser.add_argument("--mrerp-desk", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.cloud:
        runtime_mode.enable(args.cloud_via)   # 进入云端模式：之后各处只问 runtime_mode.cloud()
    cloud = runtime_mode.cloud()
    if args.pair is not None:
        return _pair(args.pair, cloud, args.tunnel)
    if args.mrerp_desk:
        from .mrerp_operator.web_driver import child_main

        return child_main()
    if args.window:
        # CC 窗口进程(2026-10-18 轻量化):网页内核只在这里;不占单实例锁、不跑取任务,关窗口就退出
        from .window_main import run as run_window

        return run_window()

    if not cloud:
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
        _notify_already_running(cfg, headless=args.headless or cloud)
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

    # 托盘常驻;手动打开时直接打开 Tomo。
    return _run_dbf(store, args)


if __name__ == "__main__":
    sys.exit(main())
