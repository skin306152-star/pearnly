"""pearnly.com 静态官网：每页都有独立标题/描述、自指 canonical、三语互指 hreflang，站内链接都能打开，结构化数据可解析，sitemap 列全。"""
import html
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

EXTERNAL_PREFIXES = ("/downloads/",)  # 小助手下载由 nginx 另外提供，不在静态站里


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title, self.meta, self.links, self.hrefs, self.srcs, self.ld, self.ids = "", {}, [], [], [], [], set()
        self._in_title = self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("id"):
            self.ids.add(a["id"])
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
        for key in list(C.PAGES) + list(build.THAI_ONLY):
            for lang in build.languages(key):
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
        thai_only = {build.url("th", key) for key in build.THAI_ONLY}
        for path, page in self.pages.items():
            langs = [C.DEFAULT_LANG] if path in thai_only else list(C.LANGS)
            expected = {C.LANGS[l]["hreflang"] for l in langs} | {"x-default"}
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
                elif href.startswith("#"):
                    self.assertIn(href[1:], page.ids, f"{path} → {href}")
                else:
                    self.assertRegex(href, r"^(https://|mailto:)", f"{path} → {href}")

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

    def test_guides_have_complete_front_matter_and_resolve_their_links(self):
        import guides as G
        self.assertGreaterEqual(len(G.ALL), 1)
        for g in G.ALL:
            self.assertIn(g.product, C.PRODUCTS, g.slug)
            for tool in g.tools:
                self.assertIn(tool, C.TOOLS, g.slug)
            _, toc = g.sections()
            self.assertGreaterEqual(len(toc), 3, g.slug)
            self.assertNotIn("Express", g.body, g.slug)
        slugs = {g.slug for g in G.ALL}
        self.assertGreaterEqual(len(G.LANDING), 1)
        for g in G.LANDING:
            self.assertIn(g.product, C.PRODUCTS, g.slug)
            self.assertTrue(all(tool in C.TOOLS for tool in g.tools), g.slug)
            self.assertTrue(set(g.meta.get("guides", [])) <= slugs, g.slug)
            self.assertGreaterEqual(len(g.faq), 3, g.slug)
            self.assertNotIn("Express", g.body + json.dumps(g.meta, ensure_ascii=False), g.slug)
            self.assertNotIn(g.slug, C.PAGES, "landing slug must not shadow a site page")

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

    def test_home_story_keeps_how_it_works_readable_in_every_language(self):
        """首页滚动故事：动画只给眼睛看，"怎么运作"的三步仍是页面正文；数据可解析，30 张单据。"""
        for lang in C.LANGS:
            html_text = (self.out / build.url(lang, "home").lstrip("/") / "index.html").read_text(encoding="utf-8")
            self.assertEqual(html_text.count('class="ss-stage" aria-hidden="true"'), 1, lang)
            self.assertIn(f'id="story-title">{build.esc(C.HOME[lang]["steps_title"])}</h2>', html_text)
            for title, text in C.HOME[lang]["steps"]:
                self.assertIn(build.esc(text), html_text)
            raw = html_text.split('data-story="', 1)[1].split('"', 1)[0]
            data = json.loads(html.unescape(raw))
            self.assertEqual((len(data["rows"]), len(data["captions"])), (30, 5))
            self.assertIn('href="#products" tabindex="-1"', html_text)

    def test_mrerp_family_is_linked_from_home_product_pages_and_its_own_page(self):
        """MR.ERP 专业版：首页书架、ERP/DMS 页底部和 /mrerp/ 都指过去；/mrerp/ 带两个登录入口和 8 个模块。"""
        for lang in C.LANGS:
            mrerp = build.url(lang, "mrerp")
            for key in ("home", "erp", "dms"):
                self.assertIn(mrerp, self.pages[build.url(lang, key)].hrefs, (lang, key))
            page = self.pages[mrerp]
            self.assertIn(C.MRERP_LOGIN, page.hrefs)
            self.assertIn(C.MRERP_DMS_LOGIN, page.hrefs)
            self.assertEqual(len(C.MRERP[lang]["modules"]), len(C.MRERP_ICONS))

    def test_mascots_are_decorative_and_self_contained(self):
        """品牌形象只给眼睛看（aria-hidden），每只都是内联 SVG，不引用外部图片或页面里的 <defs>。"""
        import mascot as M
        for kind in M.KINDS:
            svg = M.svg(kind, "x")
            self.assertTrue(svg.startswith(f'<svg class="mascot m-{kind} x"'), kind)
            self.assertIn('aria-hidden="true"', svg)
            self.assertNotIn("href", svg)
        home = (self.out / "index.html").read_text(encoding="utf-8")
        for kind in ("base", "done", *C.PRODUCTS, "pro"):
            self.assertIn(f'class="mascot m-{kind}', home)


    def test_topbar_capsule_class_is_not_reused_inside_the_page(self):
        """顶栏胶囊 .pill 只有一个；页面里别的组件重名会把它的样式（比如默认隐藏）套到顶栏上。"""
        for lang in C.LANGS:
            for key in ("home", "mrerp", "erp"):
                html_text = (self.out / build.url(lang, key).lstrip("/") / "index.html").read_text(encoding="utf-8")
                self.assertEqual(len(re.findall(r'class="pill[" ]', html_text)), 1, (lang, key))


    def test_video_upload_dates_carry_a_time_zone(self):
        """Google 视频结构化数据要求 uploadDate 是带时区的完整日期时间，只写日期会被 Search Console 报"缺少时区信息"。"""
        from datetime import datetime
        found = 0
        for page in self.pages.values():
            for block in page.ld:
                for node in json.loads(block).get("@graph", []):
                    if node.get("@type") == "VideoObject":
                        found += 1
                        self.assertIsNotNone(datetime.fromisoformat(node["uploadDate"]).tzinfo, node["uploadDate"])
        self.assertGreater(found, 0)


    def test_demo_buttons_open_line_and_carry_a_prefilled_message_for_phones(self):
        """预约演示按钮：默认链到加好友页（电脑扫码）；手机上 site.js 换成预填好产品名的对话。MR.ERP 页是约顾问。"""
        from urllib.parse import unquote
        for lang in C.LANGS:
            for key, expect in (("cowork", "Pearnly Cowork"), ("dms", "Pearnly DMS"), ("home", "Pearnly")):
                html_text = (self.out / build.url(lang, key).lstrip("/") / "index.html").read_text(encoding="utf-8")
                messages = re.findall(r'href="https://line.me/R/ti/p/@pearnly" data-line-message="([^"]+)"', html_text)
                self.assertTrue(messages, (lang, key))
                for m in messages:
                    m = unquote(html.unescape(m))
                    self.assertTrue(m.startswith("https://line.me/R/oaMessage/@pearnly/?"), m)
                    self.assertIn(expect, m)
            mrerp = (self.out / build.url(lang, "mrerp").lstrip("/") / "index.html").read_text(encoding="utf-8")
            self.assertIn(html.escape(C.COMMON[lang]["cta_consult"]), mrerp)
        for page in ("about", "privacy"):
            for lang in C.LANGS:
                self.assertIn(build.url(lang, page), self.pages[build.url(lang, "home")].hrefs)


if __name__ == "__main__":
    unittest.main()
