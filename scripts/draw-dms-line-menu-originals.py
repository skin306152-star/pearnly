#!/usr/bin/env python3
"""Draw the Thai DMS LINE rich menus and the text-menu icon for item 6.

Ported from the old app's `generate_dms_rich_menu_assets.py` so every card keeps the same look
(Sarabun Bold, card colours, line icons). Both menus are 2500x1686, three cards on the top row:

    query: customer | booking | portal  /  credentials | query | manual
    basic: customer | booking | portal  /  credentials | manual   (two wide cards)

Titles and subtitles are the Thai column of `dms.card.rich.*` in the DMS glossary. After running
this, run `render-dms-line-menus.py` for en/zh/ja and publish the menus again. Offline and
idempotent; it rewrites `public/images/menu/rich-{basic,query}.png` and `menu-6.png` only.

    python3 scripts/draw-dms-line-menu-originals.py
"""
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms"
ART = ROOT / "public/images/menu"
FONT = Path(__file__).resolve().parent / "fonts/Sarabun-Bold.ttf"

PURPLE = (118, 86, 214)
GREEN = (25, 140, 106)
BLUE = (47, 107, 255)
PINK = (242, 92, 110)
ORANGE = (194, 106, 22)
TEAL = (15, 124, 140)  # THEME_TEAL of the text menu card
INK = (55, 48, 75)
BACKGROUND = (250, 249, 252)
WHITE = (255, 255, 255)
WIDTH, HEIGHT, ROW = 2500, 1686, 843


def font(size):
    return ImageFont.truetype(str(FONT), size)


def blend(base, tint, amount):
    return tuple(round(a + (b - a) * amount) for a, b in zip(base, tint))


def centered_text(draw, text, face, fill, center_x, center_y):
    left, top, right, bottom = draw.textbbox((0, 0), text, font=face)
    draw.text((center_x - (left + right) / 2, center_y - (top + bottom) / 2), text, font=face, fill=fill)


def draw_customer(draw, x, y, size, color, width):
    head = size * 0.17
    draw.ellipse((x - head, y - size * 0.45, x + head, y - size * 0.11), outline=color, width=width)
    draw.arc((x - size * 0.34, y, x + size * 0.34, y + size * 0.50), 180, 360, fill=color, width=width)


def draw_car(draw, x, y, size, color, width):
    draw.rounded_rectangle((x - size * 0.44, y - size * 0.06, x + size * 0.44, y + size * 0.22),
                           radius=size * 0.10, outline=color, width=width)
    draw.line((x - size * 0.30, y - size * 0.06, x - size * 0.17, y - size * 0.30,
               x + size * 0.17, y - size * 0.30, x + size * 0.30, y - size * 0.06),
              fill=color, width=width, joint="curve")
    for wheel in (x - size * 0.22, x + size * 0.22):
        draw.ellipse((wheel - size * 0.10, y + size * 0.12, wheel + size * 0.10, y + size * 0.32),
                     outline=color, width=width)


def draw_dashboard(draw, x, y, size, color, width):
    gap, tile = size * 0.08, size * 0.34
    left, top = x - tile - gap / 2, y - tile - gap / 2
    for tx, ty in ((left, top), (left + tile + gap, top), (left, top + tile + gap)):
        draw.rounded_rectangle((tx, ty, tx + tile, ty + tile), radius=tile * 0.28, outline=color, width=width)
    tx, ty = left + tile + gap, top + tile + gap
    middle, tip = ty + tile / 2, tx + tile * 0.50
    draw.line((tx - size * 0.06, middle, tip, middle), fill=color, width=width)
    draw.line((tip - tile * 0.28, middle - tile * 0.28, tip, middle, tip - tile * 0.28, middle + tile * 0.28),
              fill=color, width=width, joint="curve")
    draw.line((tx + tile * 0.15, ty, tx + tile, ty, tx + tile, ty + tile, tx + tile * 0.15, ty + tile),
              fill=color, width=width, joint="curve")


def draw_lock(draw, x, y, size, color, width):
    draw.rounded_rectangle((x - size * 0.30, y - size * 0.05, x + size * 0.30, y + size * 0.42),
                           radius=size * 0.10, outline=color, width=width)
    draw.arc((x - size * 0.18, y - size * 0.40, x + size * 0.18, y - size * 0.04), 180, 360,
             fill=color, width=width)
    draw.line((x - size * 0.18, y - size * 0.22, x - size * 0.18, y), fill=color, width=width)
    draw.line((x + size * 0.18, y - size * 0.22, x + size * 0.18, y), fill=color, width=width)


def draw_search(draw, x, y, size, color, width):
    radius = size * 0.27
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), outline=color, width=width)
    draw.line((x + radius * 0.72, y + radius * 0.72, x + size * 0.43, y + size * 0.43), fill=color, width=width)


def draw_video(draw, x, y, size, color, width):
    """A screen with a play triangle on a stand: watch a tutorial clip."""
    draw.rounded_rectangle((x - size * 0.44, y - size * 0.34, x + size * 0.44, y + size * 0.24),
                           radius=size * 0.08, outline=color, width=width)
    draw.polygon([(x - size * 0.10, y - size * 0.17), (x - size * 0.10, y + size * 0.07),
                  (x + size * 0.15, y - size * 0.05)], fill=color)
    draw.line((x, y + size * 0.24, x, y + size * 0.42), fill=color, width=width)
    draw.line((x - size * 0.22, y + size * 0.42, x + size * 0.22, y + size * 0.42), fill=color, width=width)


CARDS = {  # name -> (colour, icon, title size)
    "customer": (BLUE, draw_customer, 68),
    "booking": (PINK, draw_car, 68),
    "portal": (PURPLE, draw_dashboard, 68),
    "credentials": (GREEN, draw_lock, 56),
    "query": (ORANGE, draw_search, 68),
    "manual": (TEAL, draw_video, 68),
}
MENUS = {
    "query": (("customer", "booking", "portal"), ("credentials", "query", "manual")),
    "basic": (("customer", "booking", "portal"), ("credentials", "manual")),
}


def thai_words():
    words = {}
    with open(ROOT / "i18n/messages.tsv", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 5 and row[4].startswith("dms.card.rich."):
                words[row[4]] = row[2]
    return words


def draw_menu(rows, words):
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    dot = blend(BACKGROUND, PURPLE, 0.05)
    for x in range(60, WIDTH, 124):
        for y in range(60, HEIGHT, 124):
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=dot)
    for row, names in enumerate(rows):
        edges = (0, 833, 1666, 2500) if len(names) == 3 else (0, 1250, 2500)
        top = row * ROW
        for col, name in enumerate(names):
            color, glyph, size = CARDS[name]
            left, right = edges[col], edges[col + 1]
            center = (left + right) / 2
            draw.rounded_rectangle((left + 44, top + 44, right - 44, top + ROW - 44), radius=48,
                                   fill=WHITE, outline=blend(WHITE, color, 0.22), width=4)
            draw.rounded_rectangle((center - 120, top + 190, center + 120, top + 430), radius=64, fill=color)
            glyph(draw, center, top + 310, 150, WHITE, 12)
            centered_text(draw, words[f"dms.card.rich.{name}.title"], font(size), color, center, top + 538)
            centered_text(draw, words[f"dms.card.rich.{name}.sub"], font(40),
                          blend(INK, BACKGROUND, 0.35), center, top + 640)
    return image


def main():
    words = thai_words()
    for menu, rows in MENUS.items():
        draw_menu(rows, words).save(ART / f"rich-{menu}.png", optimize=True)
    icon = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    draw_video(ImageDraw.Draw(icon), 48, 48, 88, TEAL, 6)
    icon.save(ART / "menu-6.png", optimize=True)


if __name__ == "__main__":
    main()
