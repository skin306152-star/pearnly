#!/usr/bin/env python3
"""iPhone 主屏幕 app 的启动图（2026-10-12：iOS 做到网页 app 的极限）。

点开主屏幕上的 CC，页面下载完之前 iOS 默认显示黑屏；给出按屏幕尺寸的启动图，iOS 一点开就显示（同 Claude 原生 app 的启动画面：
浅底中间一个标志）。底色同页面（globals.css 的 --cc-bg-app）：浅色 #fcfcfb，深色 #151515；标志用 frontend/assistant/public/icon-512.png。
苹果在「添加到主屏幕」那一刻读取，已添加的要删掉重加才生效。页面里的引用由 vite.config.ts 的 ccIcons 生成（带内容指纹）。

用法：`python3 scripts/build-ios-splash.py`（需要 Pillow）；`--check` 只核对文件齐不齐。
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "frontend/assistant/public"
OUT = PUBLIC / "splash"

# (逻辑宽, 逻辑高, 倍率)：竖屏，常见 iPhone
DEVICES = (
    (440, 956, 3), (430, 932, 3), (428, 926, 3), (414, 896, 3), (414, 896, 2), (402, 874, 3),
    (393, 852, 3), (390, 844, 3), (375, 812, 3), (375, 667, 2), (360, 780, 3),
)
THEMES = {"light": (252, 252, 251), "dark": (21, 21, 21)}
LOGO_PT = 96  # 标志边长（逻辑点）


def name(width: int, height: int, ratio: int, theme: str) -> str:
    return f"splash-{width}x{height}@{ratio}x-{theme}.png"


def main() -> int:
    files = [name(w, h, r, t) for (w, h, r) in DEVICES for t in THEMES]
    if "--check" in sys.argv:
        missing = [f for f in files if not (OUT / f).is_file()]
        print("missing: " + ", ".join(missing) if missing else "splash images are present")
        return 1 if missing else 0
    from PIL import Image

    logo = Image.open(PUBLIC / "icon-512.png").convert("RGBA")
    OUT.mkdir(parents=True, exist_ok=True)
    for width, height, ratio in DEVICES:
        size = (width * ratio, height * ratio)
        mark = logo.resize((LOGO_PT * ratio, LOGO_PT * ratio), Image.LANCZOS)
        for theme, color in THEMES.items():
            canvas = Image.new("RGBA", size, color + (255,))
            canvas.alpha_composite(mark, ((size[0] - mark.width) // 2, (size[1] - mark.height) // 2))
            canvas.convert("RGB").save(OUT / name(width, height, ratio, theme), optimize=True)
    print(f"wrote {len(files)} splash images")
    return 0


if __name__ == "__main__":
    sys.exit(main())
