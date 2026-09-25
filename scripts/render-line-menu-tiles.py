#!/usr/bin/env python3
"""Render the ERP LINE rich-menu tiles (one per function and phone language).

`integrations/line_menu.py` pastes these tiles into a 2500x1686 menu for each
permission set and language when the menus are published, so the product image
never needs fonts. Re-run after changing a tile's wording in `messages.tsv`:

    docker run --rm --network none --entrypoint /home/frappe/frappe-bench/env/bin/python \
      -v "$PWD:/repo" <frappe image> /repo/scripts/render-line-menu-tiles.py

Offline and idempotent; it only rewrites `public/images/line-menu/tiles/`.
Fonts are the open fonts shipped in the Frappe image (Loma, WenQuanYi Zen Hei,
IPAGothic, DejaVu). Purchase and sales icons are the ones of the old
`pearnly-erp-v1` menu (cut from `line-menu-source.png`); the lock is the old
"coming soon" lock without its text (user 2026-09-26).
"""
import csv
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1] / "apps/pearnly_erp_portal/pearnly_erp_portal"
ART = ROOT / "public/images/line-menu"
OUT = ART / "tiles"
TILE = (833, 843)
BACKGROUND = "#FAF9FC"
CARD = (44, 44, 799, 799)
LANGS = ("th", "zh", "en", "ja")
FONTS = {
    "th": ("/usr/share/fonts/opentype/tlwg/Loma-Bold.otf", "/usr/share/fonts/opentype/tlwg/Loma.otf", 0),
    "zh": ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 2),
    "ja": ("/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf", 2),
    "en": ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
}
# function -> (title msgid, subtitle msgid, accent, card border, icon box in the source image)
FUNCTIONS = {
    "purchase": ("Record purchases", "Receive documents and add stock", "#198C6A", "#CCE6DE", (290, 184, 544, 438)),
    "sales": ("Record sales", "Issue documents and deduct stock", "#7656D6", "#E1DAF6", (1124, 184, 1378, 438)),
    "stocktake": ("Stock count", "Count stock on the web", "#D9731F", "#F6DEC8", None),
}
LOCK_BOX = (2003, 240, 2163, 435)
LOCKED_CARD, SUBTITLE = "#ECEAF2", "#6F6A80"


def glossary():
    rows = {}
    with open(ROOT / "i18n/messages.tsv", encoding="utf-8") as handle:
        for row in csv.reader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if len(row) >= 4 and row[0] not in rows:
                rows[row[0]] = dict(zip(("en", "zh", "th", "ja"), row[:4]))
    return rows


def base(fill, border=None):
    image = Image.new("RGB", TILE, BACKGROUND)
    ImageDraw.Draw(image).rounded_rectangle(CARD, radius=48, fill=fill, outline=border,
                                            width=4 if border else 0)
    return image


def stocktake_icon(color, size=254):
    """Clipboard with ticks, drawn at 4x and reduced (the old menu had no stocktake tile)."""
    scale = 4
    big = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    s = lambda value: int(value * scale)
    draw.rounded_rectangle((s(6), s(6), s(248), s(248)), radius=s(58), fill=color)
    white, line = "#FFFFFF", s(11)
    draw.rounded_rectangle((s(70), s(60), s(184), s(210)), radius=s(14), outline=white, width=line)
    draw.rounded_rectangle((s(100), s(44), s(154), s(76)), radius=s(8), fill=white)
    for y in (108, 146, 184):
        draw.line((s(88), s(y), s(100), s(y + 11), s(118), s(y - 8)), fill=white, width=s(9), joint="curve")
        draw.line((s(130), s(y + 2), s(166), s(y + 2)), fill=white, width=s(9))
    return big.resize((size, size), Image.LANCZOS)


def text(draw, y, value, font, fill, stroke):
    draw.text((TILE[0] // 2 - 6, y), value, font=font, fill=fill, anchor="mm",
              stroke_width=stroke, stroke_fill=fill)


def fit(path, size, value, limit=700):
    while True:
        font = ImageFont.truetype(path, size)
        if font.getlength(value) <= limit or size <= 20:
            return font
        size -= 2


def main():
    words, source = glossary(), Image.open(ART / "line-menu-source.png").convert("RGB")
    OUT.mkdir(exist_ok=True)
    for name, (title, subtitle, accent, border, box) in FUNCTIONS.items():
        icon = source.crop(box) if box else stocktake_icon(accent)
        for lang in LANGS:
            bold, regular, stroke = FONTS[lang]
            image = base("#FFFFFF", border)
            if icon.mode == "RGBA":
                image.paste(icon, (TILE[0] // 2 - 6 - icon.width // 2, 190), icon)
            else:
                image.paste(icon, (TILE[0] // 2 - 6 - icon.width // 2, 184))
            draw = ImageDraw.Draw(image)
            head, sub = words[title][lang], words[subtitle][lang]
            text(draw, 548, head, fit(bold, 64, head), accent, stroke)
            text(draw, 644, sub, fit(regular, 36, sub, 660), SUBTITLE, 0)
            image.save(OUT / f"{name}-{lang}.png", optimize=True)
    locked = base(LOCKED_CARD)
    lock = source.crop(LOCK_BOX)
    locked.paste(lock, (TILE[0] // 2 - 6 - lock.width // 2, TILE[1] // 2 - 22 - lock.height // 2))
    locked.save(OUT / "locked.png", optimize=True)


if __name__ == "__main__":
    main()
