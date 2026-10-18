# -*- coding: utf-8 -*-
"""托盘：打开 Tomo、连接与设置、通知点击、自动更新时机（2026-10-10 起小助手并入 Tomo）。无 PySide6 则跳过。"""

from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6")

from src.companion.gui_tray import MENU_TEXT_MAX, _menu_text  # noqa: E402


def test_tray_status_line_is_capped():
    long = "บริษัท " * 30 + " · 离线 · 云端推送未开启"
    assert len(_menu_text(long)) == MENU_TEXT_MAX
    assert _menu_text("离线") == "离线"


def test_tray_menu_opens_tomo_first_and_left_click_opens_tomo():
    # 2026-10-10：小助手并入 Tomo 桌面端：托盘是状态、打开 Tomo、连接与设置、退出；左键打开 Tomo。
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.configs = []
    app.tray = QSystemTrayIcon()
    app._quit = lambda: None
    opened, settings = [], []
    app.open_tomo = lambda text=None: opened.append(text)
    app._repair = lambda: settings.append(True)
    app._build_menu()

    texts = [action.text() for action in app.tray.contextMenu().actions()
             if action.isVisible() and not action.isSeparator()]
    assert texts == ["启动中…", "打开 CC", "连接与设置", "退出"]

    app._on_tray_activated(QSystemTrayIcon.Context)
    assert opened == []
    app._on_tray_activated(QSystemTrayIcon.Trigger)
    assert opened == [None]
    assert settings == []


def test_tray_without_express_says_not_connected():
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.configs = []
    app.tray = QSystemTrayIcon()
    app._quit = lambda: None
    app._repair = lambda: None
    app.open_tomo = lambda text=None: None
    app._profile_states = {}
    app._build_menu()
    app._render_status()
    assert app._status_action.text() == "未连接 Express"


def test_tomo_notifications_run_their_click_action_once():
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    app.tray = QSystemTrayIcon()
    clicked = []
    app._notify("Tomo", "done", lambda: clicked.append(1))
    app._on_message_clicked()
    app._on_message_clicked()
    assert clicked == [1]


def test_reminders_pop_up_once_per_notice_and_open_their_conversation():
    # 2026-10：对话提醒（页面实时收到 / 文件夹同步带来）按 notice:<编号> 只弹一次，点了打开那段对话；去重记忆有上限
    from PySide6.QtWidgets import QApplication

    from src.companion.gui_tray import NOTICE_MEMORY, TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)   # 不走 __init__：去重记忆现建
    shown, opened = [], []
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args))
    app.open_conversation = lambda site, session: opened.append((site, session))
    items = [{"id": "pnotice_1", "title": "", "text": "2 files waiting", "created": 1.0, "session": "sess-1"}]
    app._on_folder_notices("https://acme.pearnly.com", items)
    app._on_folder_notices("https://acme.pearnly.com", items)
    app._notify("CC", "2 files waiting", None, tag="notice:pnotice_1")   # 对话页实时收到的同一条
    assert [(title, body) for title, body, *_ in shown] == [("CC", "2 files waiting")]
    app._on_message_clicked()
    assert opened == [("https://acme.pearnly.com", "sess-1")]
    app._notify("CC", "reply", None, tag="tomo-reply")
    app._notify("CC", "reply", None, tag="tomo-reply")
    assert len(shown) == 3   # 回复完成等别的通知不去重
    for n in range(NOTICE_MEMORY + 20):
        app._notify("CC", "x", None, tag=f"notice:{n}")
    assert len(app._seen_notices) == NOTICE_MEMORY


def test_a_batch_of_reminders_pops_up_as_one_notice_that_opens_the_tasks_page():
    # companion-2：托盘点击只记得住最后一条的动作，连弹几条会点错：一批不止一条时合成一条（标题带条数与最新一条的
    # 标题，点开定时任务页）；只有一条时点开那段对话。已经弹过的（页面实时收到）不算进条数
    from PySide6.QtWidgets import QApplication

    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    app = TrayApp.__new__(TrayApp)
    shown, opened = [], []
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args[:2]))
    app.open_conversation = lambda site, session: opened.append((site, session))
    site = "https://acme.pearnly.com"
    items = [{"id": f"p{n}", "title": f"T{n}", "text": f"text {n}", "created": float(n), "session": f"s-{n}"}
             for n in (1, 3, 2)]
    app._notify("T1", "text 1", None, tag="notice:p1")
    shown.clear()
    app._on_folder_notices(site, items)
    assert shown == [("(2) T3", "text 3")]
    app._on_message_clicked()
    assert opened == [(site, "")]
    app._on_folder_notices(site, items + [{"id": "p4", "title": "", "text": "only", "created": 4.0, "session": "s-4"}])
    assert shown[-1] == ("CC", "only")
    app._on_message_clicked()
    assert opened[-1] == (site, "s-4")
    app._on_folder_notices(site, items)
    assert len(shown) == 2


def test_a_new_version_toast_does_not_wipe_a_fresh_reminder(monkeypatch):
    # companion-2：「有新版本」通知会把还没点的提醒的点击动作冲掉：提醒弹出两分钟内、还没点时，新版不另弹通知
    # （托盘菜单里照样有更新项）；点过了或过了两分钟照常弹
    from src.companion import gui_tray
    from src.companion.gui_tray import NOTICE_HOLD_SECONDS, TrayApp

    now = [1000.0]
    monkeypatch.setattr(gui_tray, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(gui_tray, "activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui_tray, "QTimer", SimpleNamespace(singleShot=lambda *args: None))
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    shown, opened = [], []
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args[0]))
    app._update_action = None
    app._auto_timer = SimpleNamespace(isActive=lambda: True)
    app._auto_update = SimpleNamespace(offer=lambda version, url: True)
    app.open_conversation = lambda site, session: opened.append(session)
    site = "https://acme.pearnly.com"

    def remind(n):
        app._on_folder_notices(site, [{"id": f"p{n}", "title": f"T{n}", "text": "x", "created": 1.0,
                                       "session": f"s-{n}"}])

    remind(1)
    app._on_update_available("1.4.5", "/a.exe")
    assert shown == ["T1"] and app._pending_update == ("1.4.5", "/a.exe")
    app._on_message_clicked()
    assert opened == ["s-1"]
    app._on_update_available("1.4.6", "/b.exe")
    assert shown[-1] == "有新版本"
    remind(2)
    now[0] += NOTICE_HOLD_SECONDS + 1
    app._on_update_available("1.4.7", "/c.exe")
    assert shown[-1] == "有新版本" and app._message_action is None


def test_tray_opens_a_reminder_conversation_in_the_cc_window():
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    calls = []
    # 2026-10-18：窗口在窗口进程里，托盘把「打开那段对话」交给 window_link.WindowHost（没在跑就拉起）
    app._window = SimpleNamespace(open=lambda command: calls.append((command["url"], command["session"]))
                                  if command["op"] == "open_conversation" else calls.append(command))
    app.open_tomo = lambda text=None: calls.append("window")
    app.open_conversation("https://acme.pearnly.com", "s-123")
    app.open_conversation("https://acme.pearnly.com", "")
    app.open_conversation("https://evil.example", "s-123")
    assert calls == [("https://acme.pearnly.com/assistant?c=s-123", "s-123"),
                     ("https://acme.pearnly.com/assistant?view=tasks", ""), "window"]


def test_cc_window_hands_the_conversation_to_an_open_chat_page_or_loads_it():
    pytest.importorskip("PySide6.QtWebEngineWidgets")
    from src.companion import gui_tomo

    def window(current, *, ready=True, loading=False):
        calls = []
        page = SimpleNamespace(url=lambda: SimpleNamespace(toString=lambda: current), isLoading=lambda: loading,
                               runJavaScript=lambda script: calls.append(("js", script)),
                               setUrl=lambda url: calls.append(("load", url.toString())))
        return SimpleNamespace(show_front=lambda: calls.append("front"), page=page, _ready=ready,
                               _pending_ask="x", _pending_settings=None), calls

    url = "https://acme.pearnly.com/assistant?c=s-123"
    fake, calls = window("https://acme.pearnly.com/assistant")
    gui_tomo.TomoWindow.open_conversation(fake, url, "s-123")
    assert calls[0] == "front" and calls[1][0] == "js" and 't.open("s-123")' in calls[1][1]
    for current, kwargs in (("https://other.pearnly.com/assistant", {}), ("https://fatcc.com/", {}),
                            ("https://acme.pearnly.com/assistant", {"loading": True}),
                            ("https://acme.pearnly.com/assistant", {"ready": False})):
        fake, calls = window(current, **kwargs)
        gui_tomo.TomoWindow.open_conversation(fake, url, "s-123")
        assert calls == ["front", ("load", url)], current
        assert fake._pending_ask is None   # 别的页面里排着的「新对话」不再交过去

    notes = []
    fake = SimpleNamespace(_notify=lambda *args, **kwargs: notes.append((args, kwargs)), show_front=lambda: None)
    notification = SimpleNamespace(title=lambda: "", message=lambda: "m", tag=lambda: "notice:pnotice_1",
                                   click=lambda: None)
    gui_tomo.TomoWindow._present(fake, notification)
    assert notes[0][0][:2] == ("CC", "m") and notes[0][1] == {"tag": "notice:pnotice_1"}


def test_tray_checks_for_a_new_version_in_the_background_and_hands_it_to_auto_update(monkeypatch):
    # 2026-10-09：托盘不再定时查（新版随心跳回复带来）；只有点「检查更新」时在托盘自己的后台线程查一次。
    import time

    from PySide6.QtWidgets import QApplication

    from src.companion import gui_tray, updater
    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    assert not hasattr(gui_tray, "UPDATE_CHECK_MS")
    app = TrayApp.__new__(TrayApp)
    app._quitting = app._updating = False
    app._checking = None
    app._checked_latest = False
    offered = []
    app._on_update_available = lambda version, url: offered.append((version, url))
    app._auto_update = SimpleNamespace(pending=None, state=lambda: ("none", ""))
    answers = [None, {"version": "1.4.0", "url": "/x.exe"}]
    monkeypatch.setattr(updater, "check_update", lambda _site: answers.pop(0))

    def run(manual):
        app._check_for_update(manual=manual)
        assert app.update_status() == ("checking", "")
        deadline = time.monotonic() + 5
        while app._checking is not None and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)

    run(True)
    assert offered == [] and app.update_status() == ("latest", "")
    run(False)
    assert offered == [("1.4.0", "/x.exe")]


def test_auto_update_waits_while_a_helper_window_is_open_unless_someone_asked(monkeypatch):
    # 2026-10-08：自动更新不打断正在用小助手的人：窗口开着先不装；点了「立即更新」就照装。录入中永远不装。
    from src.companion import gui_tray
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    app._profile_states = {"A": ("idle", "")}
    app._update_requested = False
    opened = {"value": False}
    monkeypatch.setattr(gui_tray, "_window_open", lambda: opened["value"])

    assert app._busy() is False
    opened["value"] = True
    assert app._busy() is True
    app._update_requested = True
    assert app._busy() is False
    app._profile_states = {"A": ("working", "INV-1")}
    assert app._busy() is True


def test_auto_update_waits_for_a_folder_run_unless_someone_asked(monkeypatch):
    # companion-1：文件夹线程正在逐份上传（一次运行或补传）时不自动装；有人点了「立即更新」就照装
    # （叫停只停在两份文件之间，下次启动同一时刻接着跑）
    from src.companion import gui_tray
    from src.companion.gui_tray import TrayApp

    app = TrayApp.__new__(TrayApp)
    app._profile_states = {"A": ("idle", "")}
    app._update_requested = False
    monkeypatch.setattr(gui_tray, "_window_open", lambda: False)
    busy = {"value": True}
    app._folders = SimpleNamespace(busy=lambda: busy["value"])
    assert app._busy() is True
    busy["value"] = False
    assert app._busy() is False
    busy["value"] = True
    app._update_requested = True
    assert app._busy() is False


def test_auto_update_only_waits_while_someone_is_using_the_tomo_window(monkeypatch):
    # 2026-10-10：原小助手窗口并入 Tomo；Tomo 窗口开着且在最前面才算「正在用」，开着放在后面照常更新
    from types import SimpleNamespace

    # 2026-10-18：窗口在窗口进程里，看它经管道报来的状态（window_link.WindowHost：连着、看得见、在最前面）
    from src.companion import gui_tray

    monkeypatch.setattr(gui_tray, "WINDOW_HOST", None)
    assert gui_tray._window_open() is False
    monkeypatch.setattr(gui_tray, "WINDOW_HOST", SimpleNamespace(connected=True, visible=True, active=True))
    assert gui_tray._window_open() is True
    monkeypatch.setattr(gui_tray, "WINDOW_HOST", SimpleNamespace(connected=True, visible=True, active=False))
    assert gui_tray._window_open() is False
    monkeypatch.setattr(gui_tray, "WINDOW_HOST", SimpleNamespace(connected=False, visible=True, active=True))
    assert gui_tray._window_open() is False   # 窗口进程已退出（报来的状态作废）


def test_notifications_fall_back_to_the_tray_balloon_when_windows_notifications_are_unavailable(monkeypatch):
    # 2026-10-13：桌面通知先走 Windows 应用通知（锁屏时也进通知中心）；发不出来（缺库、系统拒绝）照旧弹托盘气泡
    from PySide6.QtWidgets import QApplication

    from src.companion import gui_tray
    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    tried, shown, opened = [], [], []
    monkeypatch.setattr(gui_tray.toast, "show", lambda *args: tried.append(args) or False)
    app = TrayApp.__new__(TrayApp)
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args[:2]))
    app.open_conversation = lambda site, session: opened.append((site, session))
    app._on_folder_notices("https://acme.pearnly.com",
                           [{"id": "p1", "title": "T1", "text": "x", "created": 1.0, "session": "s-1"}])
    assert [args[:3] for args in tried] == [("T1", "x", "notice:p1")] and callable(tried[0][3])
    assert shown == [("T1", "x")]
    app._on_message_clicked()
    assert opened == [("https://acme.pearnly.com", "s-1")]


def test_windows_notifications_replace_the_balloon_and_their_clicks_run_on_the_ui_thread(monkeypatch):
    # 应用通知的点击回调在系统线程上：只发信号，排队回界面线程打开和点气泡一样的地方（那段对话 / 定时任务页）；
    # 按 notice:<编号> 去重、一批合成一条、提醒两分钟内不弹「有新版本」都照旧
    import threading
    import time

    from PySide6.QtCore import QCoreApplication
    from PySide6.QtWidgets import QApplication

    from src.companion import gui_tray
    from src.companion.gui_tray import TrayApp

    QApplication.instance() or QApplication([])
    toasts, shown, opened = [], [], []
    monkeypatch.setattr(gui_tray.toast, "show", lambda *args: toasts.append(args) or True)
    monkeypatch.setattr(gui_tray, "activity", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui_tray, "QTimer", SimpleNamespace(singleShot=lambda *args: None))
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.tray = SimpleNamespace(showMessage=lambda *args: shown.append(args[:2]))
    app.open_conversation = lambda site, session: opened.append((site, session, threading.get_ident()))
    app._update_action = None
    app._auto_timer = SimpleNamespace(isActive=lambda: True)
    app._auto_update = SimpleNamespace(offer=lambda version, url: True)
    site = "https://acme.pearnly.com"

    def click(hook):
        before = len(opened)
        worker = threading.Thread(target=hook)   # 系统线程上的点击回调
        worker.start()
        worker.join()
        assert len(opened) == before   # 没在系统线程上直接执行
        deadline = time.monotonic() + 5
        while len(opened) == before and time.monotonic() < deadline:
            QCoreApplication.sendPostedEvents()
            QApplication.processEvents()

    app._on_folder_notices(site, [{"id": "p1", "title": "T1", "text": "x", "created": 1.0, "session": "s-1"}])
    app._notify("CC", "x", None, tag="notice:p1")   # 对话页实时收到的同一条：不再弹
    app._on_update_available("1.4.5", "/a.exe")     # 提醒刚弹、还没点：不弹「有新版本」
    assert shown == [] and [args[:3] for args in toasts] == [("T1", "x", "notice:p1")]

    click(toasts[0][3])
    assert opened == [(site, "s-1", threading.get_ident())]   # 回到界面线程执行
    assert app._message_action is None                        # 点过了：「有新版本」不再为它压着

    app._on_folder_notices(site, [{"id": f"p{n}", "title": f"T{n}", "text": f"t{n}", "created": float(n),
                                   "session": f"s-{n}"} for n in (2, 3)])
    assert toasts[-1][:3] == ("(2) T3", "t3", "")
    click(toasts[-1][3])
    assert opened[-1][:2] == (site, "")   # 一批合成一条：点开定时任务页

    app._message_action = None
    app._on_update_available("1.4.6", "/b.exe")
    assert toasts[-1][0] == "有新版本" and toasts[-1][2] == "update" and toasts[-1][3] is None
    assert shown == []   # 全程没弹托盘气泡


class _Refused(OSError):
    """Windows 拒绝启动安装包时 Python 抛的样子（winerror 4551「An Application Control policy has blocked this file」）。"""

    @property
    def winerror(self):
        return 4551


def _blocked_tray(monkeypatch, tmp_path):
    """托盘接上真的自动更新流程与本机记录；安装包一启动就被 Windows 拒绝。"""
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from src.companion import gui_tray
    from src.companion.auto_update import AutoUpdate
    from src.companion.gui_tray import TrayApp
    from src.companion.update_block import UpdateMemory

    QApplication.instance() or QApplication([])
    seen = {"toasts": [], "opened": [], "logged": [], "installs": []}
    monkeypatch.setattr(gui_tray.toast, "show", lambda *args: seen["toasts"].append(args) or True)
    monkeypatch.setattr(gui_tray, "activity", lambda key, **kw: seen["logged"].append((key, kw.get("ver"))))
    monkeypatch.setattr(gui_tray.QDesktopServices, "openUrl", lambda url: seen["opened"].append(url.toString()))
    app = TrayApp.__new__(TrayApp)
    app.lang = "zh"
    app.configs = []
    app.tray = QSystemTrayIcon()
    app._quit = app._repair = lambda: None
    app.open_tomo = lambda text=None: None
    app._quitting = False
    app._pending_update = None
    app._checking = None
    app._checked_latest = False
    app._auto_timer = SimpleNamespace(isActive=lambda: True, setInterval=lambda ms: None)
    monkeypatch.setattr(gui_tray, "QTimer", SimpleNamespace(singleShot=lambda *args: None))

    def refuse(path):
        seen["installs"].append(path)
        raise _Refused(13, "blocked")

    def start():
        app._auto_update = AutoUpdate(busy=lambda: False, stop_work=lambda: None, work_stopped=lambda: True,
                                      resume_work=lambda: None, start_download=lambda version, url: None,
                                      install=refuse, memory=UpdateMemory(tmp_path / "update_state.json", "1.5.1"))
        app._build_menu()
        app._render_update_action()

    start()
    return app, seen, start


def _menu(app):
    return [action.text() for action in app.tray.contextMenu().actions() if action.isVisible() and not action.isSeparator()]


def _blocked_install(app):
    """新版提示 → 下载完 → 空闲停手 → 启动安装包（被拒）。"""
    app._on_update_available("1.5.2", "/r/1.5.2/CC-Setup.exe")
    app._auto_update.tick()
    app._auto_update.downloaded("C:/Temp/CC-Setup_x.exe")
    app._auto_update.tick()
    app._auto_update_tick()


def test_a_blocked_update_shows_download_new_version_and_notifies_once(monkeypatch, tmp_path):
    # 教程问题第 1 条：智能应用控制拦下没签名的安装包 → 托盘一行「下载新版」+ 一条通知，点了在浏览器打开官网下载；
    # 不再重试轰炸，也不绕开系统的拦截
    from src.companion.updater import DOWNLOAD_PAGE

    app, seen, start = _blocked_tray(monkeypatch, tmp_path)
    app._on_update_available("1.5.2", "/r/1.5.2/CC-Setup.exe")
    assert _menu(app)[1] == "更新到最新版 v1.5.2"
    _blocked_install(app)
    assert seen["installs"] == ["C:/Temp/CC-Setup_x.exe"]
    assert _menu(app)[1] == "下载新版 v1.5.2"
    assert seen["toasts"][-1][:3] == ("Windows 拦截了 v1.5.2 的自动更新", "下载新版", "update")
    assert ("log_update_blocked", "1.5.2") in seen["logged"]
    assert app.update_status() == ("blocked", "1.5.2")   # 设置页「版本」一行

    app._on_message_clicked()                           # 点通知
    app._update_action.trigger()                        # 点菜单：打开下载，不是再装一次
    assert seen["opened"] == [DOWNLOAD_PAGE, DOWNLOAD_PAGE]
    assert seen["installs"] == ["C:/Temp/CC-Setup_x.exe"]

    count = len(seen["toasts"])
    app._auto_update_tick()                             # 退避期间：不再启动安装包、不再弹
    assert len(seen["toasts"]) == count and len(seen["installs"]) == 1

    start()                                             # 重开 CC：心跳带来新版之前菜单里就有「下载新版」
    assert _menu(app)[1] == "下载新版 v1.5.2"
    assert app.update_status() == ("blocked", "1.5.2")
    assert len(seen["toasts"]) == count


def test_the_blocked_notice_waits_for_an_unclicked_reminder(monkeypatch, tmp_path):
    # 刚弹的对话提醒还没点：先不弹「被拦」（托盘气泡只记得住最后一条的动作），菜单里照样有「下载新版」
    app, seen, _start = _blocked_tray(monkeypatch, tmp_path)
    app._show("T1", "x", lambda: None, tag="notice:p1")
    _blocked_install(app)
    assert [toast[0] for toast in seen["toasts"]] == ["T1"]
    assert _menu(app)[1] == "下载新版 v1.5.2"
    assert app._auto_update.take_blocked_notice() == "1.5.2"   # 没消耗：下次被拦时补弹
