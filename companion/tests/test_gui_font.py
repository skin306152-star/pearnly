"""泰文 Windows 的界面字体（gui_font，2026-10-08）：默认是 Leelawadee UI 时换成 Segoe UI，字号不变；别的字体不动。"""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtGui import QFont  # noqa: E402

from src.companion import gui_font  # noqa: E402


def test_leelawadee_is_replaced_and_the_size_kept():
    app = gui_font.qt_app()
    original = app.font()
    try:
        broken = QFont("Leelawadee UI")
        broken.setPointSizeF(9.0)
        app.setFont(broken)
        assert gui_font.fix_ui_font(app) is True
        assert app.font().family() == "Segoe UI"
        assert app.font().pointSizeF() == 9.0
        assert gui_font.fix_ui_font(app) is False  # 换过一次就不再动
    finally:
        app.setFont(original)


@pytest.mark.parametrize("family", ["Microsoft YaHei UI", "Segoe UI", "Tahoma"])
def test_other_fonts_are_left_alone(family):
    app = gui_font.qt_app()
    original = app.font()
    try:
        app.setFont(QFont(family))
        assert gui_font.fix_ui_font(app) is False
        assert app.font().family() == family
    finally:
        app.setFont(original)
