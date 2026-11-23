# -*- coding: utf-8 -*-
"""CC 桌面版的原生小窗弹出时在最前面（11-23 用户：「点了后那个弹窗在他妈大后面，不主动切到前面都看不到」）。

按类守住：CC 是两个进程（window_link.py），桥在托盘进程，托盘不是前台进程，Windows 不许它抢前台，托盘里直接弹的小窗落在 CC
窗口后面。所以要人操作的原生小窗只许出现在：CC 窗口进程（window_main.py，以 CC 窗口为父）、自己就是前台的进程（main.py 第二次
双击的提示）、小窗本身的定义（login_dialog.py），以及桥里「单进程 / 测试」的兜底（tomo_bridge.py，托盘建桥时注入窗口进程那条）。
以后谁在别处新弹一个原生小窗，这里就红。
"""
import ast
import pathlib

SRC = pathlib.Path(__file__).resolve().parents[1] / "src" / "companion"
# 建原生小窗的东西
DIALOGS = {"QDialog", "QMessageBox", "QInputDialog", "QFileDialog"}
ALLOWED = {
    "express_operator/login_dialog.py": "小窗本身：弹出时置前、取得焦点（下面行为测试守住）",
    "window_main.py": "CC 窗口进程：选文件夹、账号密码小窗都以 CC 窗口为父",
    "main.py": "第二次双击 CC 的提示：用户刚起的进程有前台权，另加置顶与取得焦点",
    "tomo_bridge.py": "桥的单进程 / 测试兜底；托盘建桥时 pick、ask_login 都注入窗口进程那条（下面核 gui_tray）",
}


def _uses(path):
    """这个模块里用到的原生小窗类名与 ``login_dialog.ask(`` 调用。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in DIALOGS:
            found.add(node.id)
        elif isinstance(node, ast.Attribute) and node.attr in DIALOGS:
            found.add(node.attr)
        elif isinstance(node, ast.alias) and node.name in DIALOGS:
            found.add(node.name)
        elif isinstance(node, ast.Call) and ast.unparse(node.func) == "login_dialog.ask":
            found.add("login_dialog.ask")
    return found


def test_native_dialogs_are_only_built_where_they_can_come_to_the_front():
    used = {}
    for path in sorted(SRC.rglob("*.py")):
        names = _uses(path)
        if names:
            used[path.relative_to(SRC).as_posix()] = sorted(names)
    assert set(used) <= set(ALLOWED), {name: used[name] for name in set(used) - set(ALLOWED)}
    assert "window_main.py" in used and "express_operator/login_dialog.py" in used


def test_the_tray_hands_every_dialog_of_the_bridge_to_the_window_process():
    tray = (SRC / "gui_tray.py").read_text(encoding="utf-8")
    build = tray[tray.index("self._bridge = TomoBridge("):]
    build = build[:build.index(")\n")]
    assert "pick=host.pick" in build and "ask_login=host.login" in build
    bridge = (SRC / "tomo_bridge.py").read_text(encoding="utf-8")
    tree = ast.parse(bridge)
    stores = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func).endswith("ask_and_store")]
    assert len(stores) == 2   # Express 授权登录、MR.ERP 代录登录
    for call in stores:
        assert any(k.arg == "asker" and ast.unparse(k.value) == "self._ask_login" for k in call.keywords), ast.unparse(call)
    # 桥里直接弹小窗的只有单进程兜底那一处（_local_login）
    asks = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and ast.unparse(node.func) == "login_dialog.ask"]
    local = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_local_login")
    assert asks and all(any(call is inner for inner in ast.walk(local)) for call in asks)


def test_the_login_window_comes_to_the_front_and_takes_focus(monkeypatch):
    from PySide6.QtWidgets import QApplication, QDialog, QWidget

    from companion.express_operator import login_dialog

    QApplication.instance() or QApplication([])
    seen = []
    monkeypatch.setattr(QDialog, "raise_", lambda self: seen.append("raise"))
    monkeypatch.setattr(QDialog, "activateWindow", lambda self: seen.append("activate"))

    def fake_exec(dialog):
        seen.append(("modal", dialog.windowModality().name, dialog.parent() is parent))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    parent = QWidget()
    assert login_dialog.ask("zh", parent, title_key="mrerp_login_title") is None
    assert seen == ["raise", "activate", ("modal", "WindowModal", True)]


def test_the_already_running_notice_stays_on_top_and_takes_focus():
    text = (SRC / "main.py").read_text(encoding="utf-8")
    notice = text[text.index("box = QMessageBox()"):text.index("box.exec()")]
    assert "WindowStaysOnTopHint" in notice and "box.raise_()" in notice and "box.activateWindow()" in notice

