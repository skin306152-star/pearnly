"""首页手绘画面共用的笔：颜色、手写字体、小工具，以及标题到主视觉卡片之间那一笔黄色记号笔。

主视觉本身在 story.py（“过去 / 现在”的办公室动画），三段产品演示在 scenes.py，都从这里取同一套颜色和手写字。
"""
import html
from urllib.parse import quote

INK, LINE, PAPER = "#2d2b27", "#3d362c", "#fffdf6"
HAND = "'Sriracha', 'Long Cang', cursive"
OUT = f'stroke="{LINE}" stroke-width="2.4" stroke-linejoin="round" stroke-linecap="round"'


def esc(text):
    return html.escape(str(text), quote=True)


def n(v):
    return f"{v:.1f}".rstrip("0").rstrip(".")


def fonts(text):
    """手写字体：Sriracha（泰文 + 拉丁，Google 按字符集分片，只下用到的那片）；中文手写字很大，只按页面上用到的字取子集。"""
    urls = ["https://fonts.googleapis.com/css2?family=Sriracha&display=swap"]
    cjk = "".join(sorted({c for c in text if "\u4e00" <= c <= "\u9fff" or c in "，。？·："}))
    if cjk:
        urls.append(f"https://fonts.googleapis.com/css2?family=Long+Cang&text={quote(cjk)}&display=swap")
    return urls


def doodle():
    """标题区到卡片之间：一笔黄色记号笔，绕一个小圈落进卡片（和标题下的荧光笔同色）。"""
    return ('<svg class="hero-doodle" viewBox="370 100 300 310" fill="none" stroke="#ffd84d" stroke-width="9" stroke-linecap="round" '
            'stroke-linejoin="round" aria-hidden="true" focusable="false">'
            '<path d="M385 143 C430 115 520 112 568 140 C600 158 625 190 615 225 C605 252 560 258 535 240 C510 222 512 180 545 160 '
            'C575 142 625 170 640 200 C652 228 650 280 638 310 C628 335 608 362 582 390"/>'
            '<path d="M585 356 L582 390 L614 382"/></svg>')
