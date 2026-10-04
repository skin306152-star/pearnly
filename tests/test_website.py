"""pearnly.com 静态官网：每页都有独立标题/描述、自指 canonical、三语互指 hreflang，站内链接都能打开，结构化数据可解析，sitemap 列全。"""
import json
import re
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "website"))
import build  # noqa: E402
import content as C  # noqa: E402

EXTERNAL_PREFIXES = ("/downloads/",)  # 小助手下载目录由 nginx 另外提供，不在静态站里


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.meta, self.links, self.hrefs, self.srcs, self.ld = "", {}, [], [], [], []
        self._in_title = self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        elif tag == "meta" and ("name" in a or "property" in a):
            self.meta[a.get("name") or a.get("property")] = a.get("content", "")
        elif tag == "link":
            self.links.append(a)
        elif tag == "a" and "href" in a:
            self.hrefs.append(a["href"])
        elif tag in ("script", "img", "source", "video"):
            for key in ("src", "poster"):
                if a.get(key):
                    self.srcs.append(a[key])
            if tag == "script" and a.get("type") == "application/ld+json":
                self._in_ld = True

    def handle_endtag(self, tag):
        self._in_title = self._in_title and tag != "title"
        self._in_ld = self._in_ld and tag != "script"

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._in_ld:
            self.ld.append(data)


class WebsiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = build.build(Path(cls.tmp.name) / "dist")
        cls.pages = {}
        for key in C.PAGES:
            for lang in C.LANGS:
                path = build.url(lang, key)
                parser = Page()
                parser.feed((cls.out / path.lstrip("/") / "index.html").read_text(encoding="utf-8"))
                cls.pages[path] = parser

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def resolves(self, href):
        path = href.split("?")[0].split("#")[0]
        if path.startswith(EXTERNAL_PREFIXES):
            return True
        target = self.out / path.lstrip("/")
        return (target / "index.html").is_file() if path.endswith("/") else target.is_file()

    def test_every_page_has_a_unique_title_and_description(self):
        titles = [p.title for p in self.pages.values()]
        descriptions = [p.meta.get("description") for p in self.pages.values()]
        self.assertTrue(all(titles) and all(descriptions))
        self.assertEqual(len(set(titles)), len(titles))
        self.assertEqual(len(set(descriptions)), len(descriptions))

    def test_canonical_points_at_itself_and_hreflang_is_complete(self):
        expected = {C.LANGS[l]["hreflang"] for l in C.LANGS} | {"x-default"}
        for path, page in self.pages.items():
            canonical = [l["href"] for l in page.links if l.get("rel") == "canonical"]
            self.assertEqual(canonical, [C.SITE + path], path)
            alternates = {l["hreflang"]: l["href"] for l in page.links if l.get("rel") == "alternate"}
            self.assertEqual(set(alternates), expected, path)
            for href in alternates.values():
                other = self.pages[href[len(C.SITE):]]
                back = {l["href"] for l in other.links if l.get("rel") == "alternate"}
                self.assertIn(C.SITE + path, back, f"{href} does not link back to {path}")

    def test_internal_links_and_assets_resolve(self):
        for path, page in self.pages.items():
            for href in page.hrefs + page.srcs + [l["href"] for l in page.links if l.get("rel") in ("stylesheet", "icon", "apple-touch-icon")]:
                if href.startswith("/"):
                    self.assertTrue(self.resolves(href), f"{path} → {href}")
                else:
                    self.assertRegex(href, r"^(https://|mailto:|#main$)", f"{path} → {href}")

    def test_structured_data_parses(self):
        for path, page in self.pages.items():
            self.assertEqual(len(page.ld), 1, path)
            data = json.loads(page.ld[0])
            types = {node["@type"] for node in data["@graph"]}
            self.assertIn("Organization", types, path)

    def test_sitemap_lists_every_page_and_robots_points_at_it(self):
        sitemap = (self.out / "sitemap.xml").read_text(encoding="utf-8")
        locs = set(re.findall(r"<loc>([^<]+)</loc>", sitemap))
        self.assertEqual(locs, {C.SITE + p for p in self.pages})
        self.assertIn(f"Sitemap: {C.SITE}/sitemap.xml", (self.out / "robots.txt").read_text())

    def test_not_found_page_is_not_indexed(self):
        self.assertIn('<meta name="robots" content="noindex">', (self.out / "404.html").read_text(encoding="utf-8"))

    def test_every_language_has_the_same_copy_keys(self):
        def shape(value):
            if isinstance(value, dict):
                return {k: shape(v) for k, v in value.items()}
            if isinstance(value, (list, tuple)):
                return [shape(v) for v in value]
            return None
        for table in (C.COMMON, C.HOME, C.TOOLS_INDEX):
            self.assertEqual(len({json.dumps(shape(table[l]), sort_keys=True) for l in C.LANGS}), 1)
        for table in (C.PRODUCT, C.TOOL):
            for key, by_lang in table.items():
                self.assertEqual(len({json.dumps(shape(by_lang[l]), sort_keys=True) for l in C.LANGS}), 1, key)


if __name__ == "__main__":
    unittest.main()
