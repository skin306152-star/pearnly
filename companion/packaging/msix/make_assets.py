# -*- coding: utf-8 -*-
"""由 CC 图标（assets/cc.png）生成 MSIX 清单引用的图标（Square44x44Logo / Square150x150Logo / Wide310x150Logo / StoreLogo，
透明底），生成好的 PNG 随仓库提交在 packaging/msix/Assets/。换了 cc.png 时重跑：

    QT_QPA_PLATFORM=offscreen python packaging/msix/make_assets.py
    QT_QPA_PLATFORM=offscreen python packaging/msix/make_assets.py --check   # 只核对已提交的文件与本脚本一致

用 PySide6（已锁在 requirements-build.txt）画图，不另加依赖。Inno 安装包不用这些图（安装窗口的图标直接取 assets/cc.ico）。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPANION = HERE.parents[1]
SOURCE = COMPANION / "assets" / "cc.png"
MSIX_ASSETS = HERE / "Assets"

# (文件, 宽, 高, 图标边长占较短边的比例, 底色；None = 透明)
MSIX = [
    (MSIX_ASSETS / "Square44x44Logo.png", 44, 44, 1.0, None),
    (MSIX_ASSETS / "Square150x150Logo.png", 150, 150, 0.66, None),
    (MSIX_ASSETS / "Wide310x150Logo.png", 310, 150, 0.66, None),
    (MSIX_ASSETS / "StoreLogo.png", 50, 50, 1.0, None),
]
TARGETS = MSIX


def render(width: int, height: int, ratio: float, background):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QImage, QPainter

    icon = QImage(str(SOURCE))
    if icon.isNull():
        raise SystemExit(f"读不了 {SOURCE}")
    side = max(1, round(min(width, height) * ratio))
    scaled = icon.scaled(side, side, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(QColor(background) if background else QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    left, top = (width - scaled.width()) // 2, (height - scaled.height()) // 2
    painter.drawImage(QRect(left, top, scaled.width(), scaled.height()), scaled)
    painter.end()
    return image


def _pixels(image) -> bytes:
    from PySide6.QtGui import QImage

    converted = image.convertToFormat(QImage.Format.Format_ARGB32)
    return bytes(converted.constBits())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    from PySide6.QtGui import QGuiApplication, QImage

    _app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    stale = []
    for path, width, height, ratio, background in TARGETS:
        image = render(width, height, ratio, background)
        if args.check:
            current = QImage(str(path))
            if current.isNull() or current.size() != image.size() or _pixels(current) != _pixels(image):
                stale.append(str(path.relative_to(COMPANION)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(path), "PNG"):
            raise SystemExit(f"写不了 {path}")
        print(path.relative_to(COMPANION), f"{width}x{height}")
    if stale:
        print("与 cc.png 不一致，重新生成：", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
