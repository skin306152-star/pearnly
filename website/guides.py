"""泰文教程文章：website/guides/<slug>.html。

文件开头一段 HTML 注释写 JSON 元数据，其余是文章正文 HTML（只用 h2/h3/p/ul/ol/table/.note）。
文章只有泰文版（面向泰国搜索），所以不出 hreflang 多语版本。涉及税务规则的文章发布前要经用户或会计确认。
"""
import json
import re
from pathlib import Path

DIR = Path(__file__).resolve().parent / "guides"
REQUIRED = ("order", "title", "description", "h1", "lead", "published", "updated", "minutes", "product", "tools")
FRONT = re.compile(r"\A\s*<!--(.*?)-->\s*", re.S)
H2 = re.compile(r"<h2>(.*?)</h2>", re.S)
THAI_MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
               "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]


class Guide:
    def __init__(self, slug, meta, body):
        self.slug, self.meta, self.body = slug, meta, body

    def __getattr__(self, name):
        try:
            return self.meta[name]
        except KeyError:
            raise AttributeError(name)

    def sections(self):
        """正文里的 h2 依次编号成锚点，返回 (带锚点的正文, [(id, 标题文字)])。"""
        toc = []

        def anchor(match):
            sid = f"s{len(toc) + 1}"
            toc.append((sid, re.sub(r"<[^>]+>", "", match.group(1))))
            return f'<h2 id="{sid}">{match.group(1)}</h2>'

        return H2.sub(anchor, self.body), toc


def thai_date(iso):
    year, month, day = (int(part) for part in iso.split("-"))
    return f"{day} {THAI_MONTHS[month - 1]} {year + 543}"


def load():
    guides = []
    for path in sorted(DIR.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        match = FRONT.match(text)
        if not match:
            raise ValueError(f"{path.name}: missing front matter comment")
        meta = json.loads(match.group(1))
        missing = [key for key in REQUIRED if key not in meta]
        if missing:
            raise ValueError(f"{path.name}: missing {missing}")
        guides.append(Guide(path.stem, meta, text[match.end():].strip()))
    return sorted(guides, key=lambda g: g.order)


ALL = load()
