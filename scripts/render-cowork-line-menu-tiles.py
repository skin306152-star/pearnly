#!/usr/bin/env python3
"""Render the Cowork LINE rich-menu tiles (one per function and phone language).

`line/menu.py` pastes these tiles into a 2500x1686 menu for each permission set
and language when the menus are published, so the product image never needs
fonts. Re-run after changing a tile's wording in `messages.tsv`:

    docker run --rm --network none --entrypoint /home/frappe/frappe-bench/env/bin/python \
      -v "$PWD:/repo" <frappe image> /repo/scripts/render-cowork-line-menu-tiles.py

Offline and idempotent; it only rewrites `public/images/line-menu/tiles/`.
The cards and icons are the old `pearnly-cowork-work-v3` menu (legacy
`ec2e5c95:static/brand/line-richmenu-cowork-v3-2500x1686.png`, kept as
`line-menu-source.png`); only the wording is redrawn per language. The lock is
the old "coming soon" card without its text (ERP 2026-09-26 rule).
"""
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_cowork/pearnly_cowork"
ART = ROOT / "public/images/line-menu"
OUT = ART / "tiles"
TILE = (833, 843)
LANGS = ("th", "zh", "en", "ja")
FONTS = {
    "th": ("/usr/share/fonts/opentype/tlwg/Loma-Bold.otf", "/usr/share/fonts/opentype/tlwg/Loma.otf", 0),
    "zh": ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 2),
    "ja": ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 2),
    "en": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
}
# function -> (title msgid, subtitle msgid, cell (column, row) in the source image)
FUNCTIONS = {
    "intake": ("Send documents to ERP", "Upload · Review · Choose destination", (0, 0)),
    "stocktake": ("Stock count", "Scan barcodes · Record quantities", (1, 0)),
}
LOCKED = (0, 1)
# Text band of a source card (tile coordinates) that is repainted with the card colour.
BAND = (70, 450, 763, 740)
TITLE_Y, SUBTITLE_Y = 525, 625
INK, MUTED = "#202033", "#847E8F"


def glossary():
    rows = {}
    with open(ROOT / "i18n/messages.tsv", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 4 and row[0] not in rows:
                rows[row[0]] = dict(zip(("en", "zh", "th", "ja"), row[:4]))
    return rows


def cell(source, column, row):
    left = (0, 833, 1666)[column]
    return source.crop((left, row * TILE[1], left + TILE[0], (row + 1) * TILE[1]))


def blank(tile):
    """Paint the card's own colour over its text band (the icon above stays)."""
    fill = tile.getpixel((BAND[0] + 10, BAND[3] - 10))
    ImageDraw.Draw(tile).rectangle(BAND, fill=fill)
    return tile


def fit(path, size, value, width):
    while size > 20:
        font = ImageFont.truetype(path, size)
        if font.getlength(value) <= width:
            return font
        size -= 2
    return font


def main():
    words = glossary()
    source = Image.open(ART / "line-menu-source.png").convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    for name, (title, subtitle, (column, row)) in FUNCTIONS.items():
        for lang in LANGS:
            tile = blank(cell(source, column, row))
            bold, regular, stroke = FONTS[lang]  # CJK fonts have no bold face: a thin stroke instead
            draw = ImageDraw.Draw(tile)
            head, sub = words[title][lang], words[subtitle][lang]
            draw.text((TILE[0] // 2, TITLE_Y), head, font=fit(bold, 60, head, 640), fill=INK,
                      anchor="mm", stroke_width=stroke, stroke_fill=INK)
            draw.text((TILE[0] // 2, SUBTITLE_Y), sub, font=fit(regular, 34, sub, 640), fill=MUTED,
                      anchor="mm")
            tile.save(OUT / f"{name}-{lang}.png", optimize=True)
    blank(cell(source, *LOCKED)).save(OUT / "locked.png", optimize=True)


if __name__ == "__main__":
    main()
