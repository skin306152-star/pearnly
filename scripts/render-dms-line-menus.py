#!/usr/bin/env python3
"""Render the DMS LINE rich menus in English, Chinese and Japanese.

The Thai menus (`rich-basic.png`, `rich-query.png`) are the designed originals and
stay untouched. Each other language reuses them: every card's text band is covered
with the card's own colour and redrawn with that language's title and subtitle, in
the card's icon colour. Wording comes from `dms.card.rich.*` in the DMS glossary.
Re-run after changing that wording:

    docker run --rm --network none --entrypoint /home/frappe/frappe-bench/env/bin/python \
      -v "$PWD:/repo" <frappe image> /repo/scripts/render-dms-line-menus.py

The query menu's sixth card is the manual (LINE menu item 6, `action=menu_manual`). It used to be a
grey "coming soon" placeholder: replace that card in the Thai original first (icon in the card's accent
colour, title คู่มือ, subtitle คลิปสอนการใช้งาน), then re-run this script, then publish the menus again.

Offline and idempotent; it only rewrites `public/images/menu/rich-{basic,query}-{en,zh,ja}.png`.
Fonts are the open fonts shipped in the Frappe image (WenQuanYi Zen Hei, IPA PGothic, DejaVu).
"""
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_dms/pearnly_dms"
ART = ROOT / "public/images/menu"
LANGS = ("en", "zh", "ja")
FONTS = {  # language -> (title font, subtitle font, fake-bold stroke)
    "zh": ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 2),
    "ja": ("/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf", "/usr/share/fonts/opentype/ipafont-gothic/ipagp.ttf", 2),
    "en": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
}
SUBTITLE = "#6F6A80"
# Text bands of the 2500x1686 originals (title + subtitle area below each icon).
ROWS = ((481, 688), (1325, 1531))
LAYOUTS = {
    # menu -> list of (card x0, card x1, row, function) in reading order
    "basic": [(46, 1204, 0, "customer"), (1296, 2454, 0, "booking"),
              (46, 1204, 1, "portal"), (1296, 2454, 1, "credentials")],
    "query": [(46, 788, 0, "customer"), (879, 1621, 0, "booking"), (1712, 2454, 0, "portal"),
              (46, 788, 1, "credentials"), (879, 1621, 1, "query"), (1712, 2454, 1, "manual")],
}
ICON_Y = (310, 1153)  # icon centre per row: its fill is the card's accent colour


def glossary():
    rows = {}
    with open(ROOT / "i18n/messages.tsv", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 5 and row[4].startswith("dms.card.rich."):
                rows[row[4]] = dict(zip(("en", "zh", "th", "ja"), row[:4]))
    return rows


def fit(path, size, value, limit):
    while True:
        font = ImageFont.truetype(path, size)
        if font.getlength(value) <= limit or size <= 20:
            return font
        size -= 2


def main():
    words = glossary()
    for menu, cards in LAYOUTS.items():
        source = Image.open(ART / f"rich-{menu}.png").convert("RGB")
        for lang in LANGS:
            bold, regular, stroke = FONTS[lang]
            image = source.copy()
            draw = ImageDraw.Draw(image)
            for x0, x1, row, name in cards:
                top, bottom = ROWS[row]
                inner = (x0 + 40, top, x1 - 40, bottom)
                fill = image.getpixel((inner[0] + 6, inner[1] + 6))
                draw.rectangle(inner, fill=fill)
                centre = (x0 + x1) // 2
                limit = inner[2] - inner[0] - 40
                accent = image.getpixel((centre - 80, ICON_Y[row] - 80))
                head = words[f"dms.card.rich.{name}.title"][lang]
                sub = words[f"dms.card.rich.{name}.sub"][lang]
                draw.text((centre, top + 58), head, font=fit(bold, 70, head, limit), fill=accent,
                          anchor="mm", stroke_width=stroke, stroke_fill=accent)
                draw.text((centre, top + 160), sub, font=fit(regular, 42, sub, limit), fill=SUBTITLE,
                          anchor="mm")
            image.save(ART / f"rich-{menu}-{lang}.png", optimize=True)


if __name__ == "__main__":
    main()
