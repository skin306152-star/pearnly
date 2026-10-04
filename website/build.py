#!/usr/bin/env python3
"""生成 pearnly.com 静态官网：纯 HTML + 一个样式表 + 两个脚本（站点交互 site.js、免费工具 tools.js），只用标准库。

用法：python3 website/build.py [--out DIR]（默认 website/dist，不提交）。
文案在 content.py；发布见 scripts/publish-website.sh。
"""
import argparse
import hashlib
import html
import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import content as C  # noqa: E402
import guides as G  # noqa: E402

STATIC = HERE / "static"
VERSION = ""
FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@400;500;600"
         "&family=Source+Serif+4:opsz,wght@8..60,500;8..60,600&display=swap")


def esc(text):
    return html.escape(str(text), quote=True)


def lines(text):
    return "<br>".join(esc(part) for part in str(text).split("\n"))


def plain(text):
    return str(text).replace("\n", " ")


# 只有泰文版的页面（教程文章）：不加语言前缀、不出多语 hreflang。
THAI_ONLY = {"guides": "/guides/", **{"guide:" + g.slug: f"/guides/{g.slug}/" for g in G.ALL}}


def url(lang, key):
    if key in THAI_ONLY:
        return THAI_ONLY[key]
    return C.LANGS[lang]["prefix"] + C.PAGES[key]


def languages(key):
    return [C.DEFAULT_LANG] if key in THAI_ONLY else list(C.LANGS)


def absolute(path):
    return C.SITE + path


def video_url(overview):
    return f"{C.MEDIA}{overview['file']}?v={overview['v']}"


def asset_version():
    digest = hashlib.sha256()
    for name in ("style.css", "site.js", "tools.js"):
        digest.update((STATIC / name).read_bytes())
    return digest.hexdigest()[:10]


def jsonld(data):
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + "</script>"


ORG = {"@type": "Organization", "@id": C.SITE + "/#org", "name": "Pearnly", "legalName": C.COMPANY["legal_name"],
       "taxID": C.COMPANY["reg_no"], "url": C.SITE + "/", "logo": C.SITE + "/static/logo-mark-512.png", "email": C.EMAIL,
       "address": {"@type": "PostalAddress", **C.COMPANY["postal"]}, "sameAs": [C.LINE_URL, C.YOUTUBE_URL]}

# 线条图标（24 网格，描边用 currentColor）。
ICONS = {
    "doc": '<path d="M7 3h7l4 4v14H7z"/><path d="M14 3v4h4M9.5 12h6M9.5 16h6"/>',
    "plug": '<path d="M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1"/><path d="M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1"/>',
    "bank": '<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
    "chart": '<path d="M5 20V11M10 20V5M15 20v-6M20 20V8"/>',
    "check": '<path d="M9 11l3 3 8-8"/><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9"/>',
    "board": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
    "idcard": '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="8.5" cy="11" r="2"/><path d="M5.5 16c.6-1.5 1.8-2.2 3-2.2s2.4.7 3 2.2M14 10h4M14 13.5h4"/>',
    "shield": '<path d="M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6z"/><path d="M9 12l2 2 4-4"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 9h18M8 3v4M16 3v4M8 13h3"/>',
    "users": '<circle cx="9" cy="8" r="3"/><path d="M3 20c.8-3.4 3.2-5 6-5s5.2 1.6 6 5M16 4.5a3 3 0 0 1 0 6M18 15c1.6.6 2.6 2.2 3 5"/>',
    "list": '<path d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "box": '<path d="M3 7.5L12 3l9 4.5v9L12 21l-9-4.5z"/><path d="M3 7.5l9 4.5 9-4.5M12 12v9"/>',
    "receipt": '<path d="M6 3h12v18l-3-2-3 2-3-2-3 2z"/><path d="M9 8h6M9 12h6"/>',
    "arrows": '<path d="M5 8h13l-3-3M19 16H6l3 3"/>',
    "register": '<rect x="5" y="3" width="14" height="7" rx="1"/><rect x="3" y="12" width="18" height="9" rx="2"/><path d="M7 16h2M11 16h2M15 16h2"/>',
    "phone": '<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M11 18h2"/>',
    "chat": '<path d="M12 4c4.97 0 9 3.13 9 7s-4.03 7-9 7c-.8 0-1.6-.08-2.35-.24L5 20l.9-3.6C4.1 15.1 3 13.15 3 11c0-3.87 4.03-7 9-7z"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "play": '<path d="M8 5.5v13l11-6.5z" fill="currentColor" stroke="none"/>',
    "download": '<path d="M12 4v11M7 10l5 5 5-5M5 20h14"/>',
}


def icon(name, cls="icon"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def breadcrumb(lang, trail):
    items = [{"@type": "ListItem", "position": i + 1, "name": name, "item": absolute(url(lang, key))} for i, (key, name) in enumerate(trail)]
    return {"@type": "BreadcrumbList", "itemListElement": items}


def head(lang, key, title, description, graph, noindex=False):
    alternates = "".join(f'<link rel="alternate" hreflang="{C.LANGS[l]["hreflang"]}" href="{absolute(url(l, key))}">' for l in languages(key))
    alternates += f'<link rel="alternate" hreflang="x-default" href="{absolute(url(C.DEFAULT_LANG, key))}">'
    canonical = absolute(url(lang, key))
    ld = jsonld({"@context": "https://schema.org", "@graph": graph}) if graph else ""
    robots = '<meta name="robots" content="noindex">\n' if noindex else ""
    return f"""<!doctype html>
<html lang="{C.LANGS[lang]["html"]}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
{robots}<title>{esc(title)}</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{canonical}">
{alternates}
<meta property="og:type" content="website">
<meta property="og:site_name" content="Pearnly">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{canonical}">
<meta property="og:image" content="{C.SITE}/static/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:locale" content="{C.LANGS[lang]["og"]}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#f8f8f6">
<link rel="icon" href="/favicon.ico" sizes="16x16 32x32 48x48">
<link rel="icon" type="image/svg+xml" href="/static/icon.svg">
<link rel="icon" type="image/png" sizes="192x192" href="/static/icon-192.png">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<link rel="stylesheet" href="/static/style.css?v={VERSION}">
<script>document.documentElement.classList.add('js')</script>
<script src="/static/site.js?v={VERSION}" defer></script>
{ld}
</head>
"""


def header(lang, key):
    t = C.COMMON[lang]
    here, chosen = ' aria-current="page"', ' aria-current="true"'
    nav = "".join(
        f'<a href="{url(lang, k)}"{here if key == k or (k == "tools" and key in C.TOOLS) or (k == "guides" and key.startswith("guide:")) else ""}>{esc(label)}</a>'
        for k, label in t["nav"].items()
    )
    short = {"th": "TH", "en": "EN", "zh": "中文"}
    langs = "".join(
        f'<a href="{url(l, key if l in languages(key) else "home")}" hreflang="{C.LANGS[l]["hreflang"]}" lang="{C.LANGS[l]["html"]}" title="{esc(C.LANGS[l]["label"])}"'
        f'{chosen if l == lang else ""}>{short[l]}</a>'
        for l in C.LANGS
    )
    return f"""<body data-lang="{lang}">
<canvas class="dots" aria-hidden="true"></canvas>
<a class="skip" href="#main">{esc(t["skip"])}</a>
<header class="topbar">
  <div class="pill">
    <a class="wordmark" href="{url(lang, "home")}">Pearnly</a>
    <nav class="nav" aria-label="{esc(t["products"])}">{nav}</nav>
    <nav class="langs" aria-label="{esc(t["language"])}">{langs}</nav>
    <a class="btn dark sm" href="{C.LINE_URL}" rel="noopener">{icon("chat")}<span>LINE</span></a>
  </div>
</header>
<main id="main">
"""


def footer(lang):
    t = C.COMMON[lang]
    products = "".join(f'<li><a href="{url(lang, k)}">{esc("Pearnly " + C.PRODUCT[k][lang]["name"])}</a></li>' for k in C.PRODUCTS)
    tools = "".join(f'<li><a href="{url(lang, k)}">{esc(C.TOOL[k][lang]["name"])}</a></li>' for k in C.TOOLS)
    return f"""</main>
<footer class="footer">
  <div class="wrap cols">
    <div class="brandcol"><a class="wordmark" href="{url(lang, "home")}">Pearnly</a><p>{esc(plain(C.HOME[lang]["h1"]))}</p></div>
    <div><h2>{esc(t["products"])}</h2><ul>{products}</ul></div>
    <div><h2>{esc(t["nav"]["tools"])}</h2><ul>{tools}</ul></div>
    <div><h2>{esc(t["contact"])}</h2><ul>
      <li><a href="{C.LINE_URL}" rel="noopener">LINE {esc(C.LINE_ID)}</a></li>
      <li><a href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a></li>
      <li><a href="{C.YOUTUBE_URL}" rel="noopener">YouTube</a></li>
    </ul></div>
  </div>
  <div class="wrap legal">
    <p>© 2026 Pearnly · {esc(t["operator"])} {esc(C.COMPANY["legal_name"])} · {esc(t["reg_no"])} {esc(C.COMPANY["reg_no"])}</p>
    <p>{esc(C.COMPANY["address"][lang])}</p>
  </div>
</footer>
</body>
</html>
"""


def cta_buttons(lang, extra=""):
    t = C.COMMON[lang]
    return f'<div class="actions"><a class="btn dark" href="{C.LINE_URL}" rel="noopener">{icon("chat")}<span>{esc(t["cta"])}</span></a>{extra}</div>'


def contact(lang):
    t = C.COMMON[lang]
    mail = f'<a class="btn light" href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a>'
    return f"""<section class="wrap section">
  <div class="panel cta reveal">
    <h2 class="display">{esc(t["contact_title"])}</h2>
    <p class="lead">{esc(t["contact_text"])}</p>
    {cta_buttons(lang, mail)}
  </div>
</section>
"""


def crumbs(lang, trail):
    t = C.COMMON[lang]
    parts = [f'<a href="{url(lang, "home")}">{esc(t["home"])}</a>']
    for key, name in trail[1:-1]:
        parts.append(f'<a href="{url(lang, key)}">{esc(name)}</a>')
    parts.append(f'<span aria-current="page">{esc(trail[-1][1])}</span>')
    return f'<nav class="crumbs" aria-label="breadcrumb">{" / ".join(parts)}</nav>'


def window(body, bar="", cls=""):
    return f'<div class="window {cls}"><div class="winbar"><span class="lights"><i></i><i></i><i></i></span>{bar}</div>{body}</div>'


# ---------------- 首页交互演示（单据是虚构样例） ----------------

def field(k, label, value, checked=None):
    tick = f'<em class="tick">✓ {esc(checked)}</em>' if checked else ""
    return f'<div class="f" data-k="{k}"><span>{esc(label)}</span><b>{esc(value)}{tick}</b></div>'


COWORK_DOC = """<div class="doc invoice" aria-hidden="true">
  <div class="doc-top"><div class="doc-logo">ต</div><div class="doc-title"><b>ใบกำกับภาษี / ใบเสร็จรับเงิน</b><span data-k="doc_no">เลขที่ IV2569-0142</span><span data-k="date">วันที่ 27/09/2569</span></div></div>
  <div class="doc-seller"><b data-k="seller">บริษัท ตัวอย่างการค้า จำกัด</b><span>88 ถนนตัวอย่าง แขวงสีลม กรุงเทพฯ 10500</span><span data-k="tax_id">เลขประจำตัวผู้เสียภาษี 0105599999997</span></div>
  <table><thead><tr><th>รายการ</th><th>จำนวน</th><th>ราคา</th><th>จำนวนเงิน</th></tr></thead>
  <tbody><tr><td>กาแฟคั่วเข้ม 250 กรัม</td><td>20</td><td>110.00</td><td>2,200.00</td></tr><tr><td>ชาเขียวมัทฉะ 100 กรัม</td><td>10</td><td>150.00</td><td>1,500.00</td></tr></tbody></table>
  <div class="doc-sum"><span data-k="base">รวมเงิน <b>3,700.00</b></span><span data-k="vat">ภาษีมูลค่าเพิ่ม 7% <b>259.00</b></span><span data-k="total" class="grand">รวมทั้งสิ้น <b>3,959.00</b></span></div>
  <i class="scan"></i>
</div>"""

ERP_DOC = """<div class="doc invoice" aria-hidden="true">
  <div class="doc-top"><div class="doc-logo">ว</div><div class="doc-title"><b>ใบกำกับภาษี</b><span data-k="doc_no">เลขที่ INV-0927</span><span>วันที่ 28/09/2569</span></div></div>
  <div class="doc-seller"><b data-k="supplier">หจก. ตัวอย่างวัสดุสำนักงาน</b><span>12 ถนนตัวอย่าง อ.เมือง จ.ขอนแก่น 40000</span></div>
  <table><thead><tr><th>รายการ</th><th>จำนวน</th><th>ราคา</th><th>จำนวนเงิน</th></tr></thead>
  <tbody><tr data-k="item1"><td>กระดาษ A4 80 แกรม</td><td>10 รีม</td><td>125.00</td><td>1,250.00</td></tr><tr data-k="item2"><td>แฟ้มสันกว้าง 3 นิ้ว</td><td>24 เล่ม</td><td>45.00</td><td>1,080.00</td></tr></tbody></table>
  <div class="doc-sum"><span>รวมเงิน <b>2,330.00</b></span><span data-k="vat">ภาษีมูลค่าเพิ่ม 7% <b>163.10</b></span><span data-k="total" class="grand">รวมทั้งสิ้น <b>2,493.10</b></span></div>
  <i class="scan"></i>
</div>"""


def demo(lang):
    L = C.DEMO_LABELS[lang]
    dms_doc = f"""<div class="doc chat" aria-hidden="true">
  <div class="msg me"><div class="idcard"><span class="idc-h">บัตรประจำตัวประชาชน · ตัวอย่าง</span><span class="idc-photo"></span><b data-k="name">นาย สมชาย ใจดี</b><span data-k="id_no">1 1234 xxxxx xx 1</span><span data-k="birth">เกิดวันที่ 12 มี.ค. 2528</span><span data-k="address">99 ถ.ตัวอย่าง อ.เมือง จ.เชียงใหม่</span></div></div>
  <div class="msg me"><span class="bubble" data-k="phone">089-999-1234</span></div>
  <div class="msg bot"><span class="bubble">{esc(L["found"])}<em class="chip">{esc(L["confirm"])}</em></span></div>
</div>"""
    scenes = [
        ("cowork", COWORK_DOC, L["read"], [
            field("seller", L["seller"], "บริษัท ตัวอย่างการค้า จำกัด"),
            field("tax_id", L["tax_id"], "0-1055-99999-99-7", L["checked"]),
            field("doc_no", L["doc_no"], "IV2569-0142"),
            field("date", L["date"], "27/09/2569"),
            field("base", L["base"], "3,700.00"),
            field("vat", L["vat"], "259.00", L["checked"]),
            field("total", L["total"], "3,959.00"),
        ], L["cowork_done"]),
        ("dms", dms_doc, L["dms_read"], [
            field("name", L["name"], "นาย สมชาย ใจดี"),
            field("id_no", L["id_no"], "1-1234-xxxxx-xx-1", L["checked"]),
            field("birth", L["birth"], "12/03/2528"),
            field("address", L["address"], "99 ถ.ตัวอย่าง อ.เมือง จ.เชียงใหม่"),
            field("phone", L["phone"], "089-999-1234"),
        ], L["dms_done"]),
        ("erp", ERP_DOC, L["erp_read"], [
            field("supplier", L["supplier"], "หจก. ตัวอย่างวัสดุสำนักงาน"),
            field("doc_no", L["doc_no"], "INV-0927"),
            field("item1", L["items"], "กระดาษ A4 80 แกรม × 10"),
            field("item2", L["items"], "แฟ้มสันกว้าง 3 นิ้ว × 24"),
            field("vat", L["vat"], "163.10", L["checked"]),
            field("total", L["total"], "2,493.10"),
        ], L["erp_done"]),
    ]
    tabs = "".join(
        f'<button type="button" role="tab" data-tab="{k}" aria-selected="{"true" if i == 0 else "false"}">{esc(C.PRODUCT[k][lang]["name"])}</button>'
        for i, (k, _doc, _title, _fields, _done) in enumerate(scenes)
    )
    body = "".join(
        f"""<div class="scene{' active' if i == 0 else ''}" data-scene="{k}">
  <div class="scene-doc">{doc}</div>
  <div class="scene-out"><div class="out-head"><span class="spark"></span>{esc(title)}</div>{"".join(fields)}<div class="done">{icon("check")}<span>{esc(done)}</span></div></div>
</div>"""
        for i, (k, doc, title, fields, done) in enumerate(scenes)
    )
    return window(f'<div class="demo-body">{body}<div class="progress"><i></i></div></div>', f'<div class="seg" role="tablist">{tabs}</div>', "demo reveal")


# ---------------- 页面 ----------------

def page_home(lang):
    h, t = C.HOME[lang], C.COMMON[lang]
    watch = f'<a class="btn light" href="#products">{icon("play")}<span>{esc(t["watch"])}</span></a>'
    logos = "".join(f"<span>{esc(name)}</span>" for name in C.INTEGRATIONS[lang])
    products = ""
    for i, k in enumerate(C.PRODUCTS):
        p, o = C.PRODUCT[k][lang], C.OVERVIEW[k]
        points = "".join(f"<li>{icon('check', 'icon sm')}<span>{esc(a)}</span></li>" for a, _ in p["features"][:3])
        shot = window(f'<img src="{o["poster"]}" alt="" loading="lazy" width="1280" height="720"><span class="playbtn"><span class="circle">{icon("play")}</span></span>')
        products += f"""<article class="panel product reveal{' flip' if i % 2 else ''}">
  <div class="product-text">
    <span class="eyebrow">{esc(p["who"])}</span>
    <h3 class="display">Pearnly {esc(p["name"])}</h3>
    <p class="muted">{esc(p["summary"])}</p>
    <ul class="points">{points}</ul>
    <a class="more" href="{url(lang, k)}">{esc(t["more"])} {icon("arrow", "icon sm")}</a>
  </div>
  <a class="poster" href="{url(lang, k)}#overview" aria-label="{esc(t["overview"])} · Pearnly {esc(p["name"])}">{shot}</a>
</article>"""
    steps = "".join(f'<li class="reveal"><span class="num">0{i + 1}</span><h3>{esc(a)}</h3><p>{esc(b)}</p></li>' for i, (a, b) in enumerate(h["steps"]))
    samples = {"baht-text": "1,070 → หนึ่งพันเจ็ดสิบบาทถ้วน", "vat": "1,070.00 → VAT 70.00",
               "withholding-tax": "10,000 · 3% → 300.00", "tax-id": "0-1055-99999-99-7 ✓"}
    tools = "".join(
        f'<a class="tile reveal" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p><code>{esc(samples[k])}</code></a>'
        for k in C.TOOLS
    )
    graph = [ORG, {"@type": "WebSite", "@id": C.SITE + "/#site", "name": "Pearnly", "url": C.SITE + "/",
                   "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero">
  <div class="wrap center">
    <span class="badge"><i class="live"></i>{esc(h["badge"])}</span>
    <h1 class="display">{lines(h["h1"])}</h1>
    <p class="lead">{esc(h["lead"])}</p>
    {cta_buttons(lang, watch)}
  </div>
  <div class="wrap wide">{demo(lang)}</div>
</section>
<section class="wrap section logos reveal">
  <p class="serif">{esc(t["integrations"])}</p>
  <div class="logo-row">{logos}</div>
</section>
<section class="wrap section" id="products">
  <h2 class="display center reveal">{esc(h["products_title"])}</h2>
  <div class="stack">{products}</div>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(h["steps_title"])}</h2>
  <ol class="steps">{steps}</ol>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(h["tools_title"])}</h2>
  <p class="lead center reveal">{esc(h["tools_text"])}</p>
  <div class="tiles">{tools}</div>
</section>
{home_guides(lang)}"""
    return head(lang, "home", h["title"], h["description"], graph) + header(lang, "home") + body + contact(lang) + footer(lang)


def home_guides(lang):
    if lang != "th" or not G.ALL:
        return ""
    T = C.GUIDE_TEXT
    tiles = "".join(guide_tile(g) for g in G.ALL[:4])
    return f"""<section class="wrap section">
  <h2 class="display center reveal">{esc(T["home_title"])}</h2>
  <p class="lead center reveal">{esc(T["home_text"])}</p>
  <div class="tiles pair">{tiles}</div>
  <p class="center more-link"><a class="more" href="{url(lang, "guides")}">{esc(T["all"])} {icon("arrow", "icon sm")}</a></p>
</section>
"""


def related_guides(lang, matches):
    """泰文页面底部的“延伸阅读”：工具页按 tools、产品页按 product 找文章。"""
    found = [g for g in G.ALL if matches(g)]
    if lang != "th" or not found:
        return ""
    links = "".join(f'<li><a href="{url(lang, "guide:" + g.slug)}">{esc(g.h1)}</a></li>' for g in found)
    return f'<section class="wrap narrow section"><h2 class="display">{esc(C.GUIDE_TEXT["related"])}</h2><ul class="plain-links">{links}</ul></section>'


def page_product(lang, key):
    p, t, o = C.PRODUCT[key][lang], C.COMMON[lang], C.OVERVIEW[key]
    features = "".join(
        f'<li class="card reveal">{icon(ic)}<h3>{esc(a)}</h3><p>{esc(b)}</p></li>' for (a, b), ic in zip(p["features"], C.FEATURE_ICONS[key])
    )
    download = f'<a class="btn light" href="{C.COMPANION_URL}">{icon("download")}<span>{esc(p["download"])}</span></a>' if p.get("download") else ""
    others = "".join(
        f'<a class="tile reveal" href="{url(lang, k)}"><span class="eyebrow">{esc(C.PRODUCT[k][lang]["who"])}</span>'
        f'<h3>Pearnly {esc(C.PRODUCT[k][lang]["name"])}</h3><p>{esc(C.PRODUCT[k][lang]["summary"])}</p></a>'
        for k in C.PRODUCTS if k != key
    )
    video = (f'<video preload="none" playsinline controls poster="{o["poster"]}" width="1920" height="1080" aria-label="{esc(t["overview"])}">'
             f'<source src="{video_url(o)}" type="video/mp4"></video>'
             f'<button type="button" class="playbtn" aria-label="{esc(t["play"])}"><span class="circle">{icon("play")}</span></button>')
    bar = f'<span class="wintitle">{esc(t["overview"])} · Pearnly {esc(p["name"])}</span>'
    trail = [("home", t["home"]), (key, "Pearnly " + p["name"])]
    minutes, seconds = divmod(o["seconds"], 60)
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "SoftwareApplication", "name": "Pearnly " + p["name"], "applicationCategory": "BusinessApplication",
              "operatingSystem": "Web, LINE", "description": p["description"], "url": absolute(url(lang, key)),
              "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}},
             {"@type": "VideoObject", "name": f"Pearnly {p['name']} · {t['overview']}", "description": p["description"],
              "thumbnailUrl": absolute(o["poster"]), "contentUrl": video_url(o), "uploadDate": o["uploaded"],
              "duration": f"PT{minutes}M{seconds}S", "inLanguage": "th", "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(p["who"])}</span>
    <h1 class="display">{esc(p["h1"])}</h1>
    <p class="lead">{esc(p["lead"])}</p>
    {cta_buttons(lang, download)}
  </div>
  <div class="wrap wide" id="overview">{window(f'<div class="player">{video}</div>', bar, "reveal")}</div>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(t["features"])}</h2>
  <ul class="cards">{features}</ul>
</section>
{related_guides(lang, lambda g: g.product == key)}
<section class="wrap section">
  <h2 class="display center reveal">{esc(t["others"])}</h2>
  <div class="tiles two">{others}</div>
</section>
"""
    return head(lang, key, p["title"], p["description"], graph) + header(lang, key) + body + contact(lang) + footer(lang)


def page_tools(lang):
    p, t = C.TOOLS_INDEX[lang], C.COMMON[lang]
    tools = "".join(
        f'<a class="tile reveal" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p>'
        f'<span class="more">{icon("arrow", "icon sm")}</span></a>'
        for k in C.TOOLS
    )
    trail = [("home", t["home"]), ("tools", t["nav"]["tools"])]
    body = f"""<section class="hero sub"><div class="wrap center">{crumbs(lang, trail)}<h1 class="display">{esc(p["h1"])}</h1><p class="lead">{esc(p["lead"])}</p></div></section>
<section class="wrap section"><div class="tiles two">{tools}</div></section>
"""
    return head(lang, "tools", p["title"], p["description"], [ORG, breadcrumb(lang, trail)]) + header(lang, "tools") + body + contact(lang) + footer(lang)


def control(label, inner):
    return f'<label class="field"><span>{esc(label)}</span>{inner}</label>'


def radios(name, options, checked):
    items = "".join(
        f'<label><input type="radio" name="{name}" value="{value}"{" checked" if value == checked else ""}><span>{esc(text)}</span></label>'
        for value, text in options
    )
    return f'<div class="segmented">{items}</div>'


def result_rows(rows):
    return '<dl class="result">' + "".join(f'<div><dt>{esc(label)}</dt><dd data-out="{key}"></dd></div>' for key, label in rows) + "</dl>"


def tool_form(lang, key):
    L = C.TOOL[key][lang]["labels"]
    amount = '<input name="amount" inputmode="decimal" autocomplete="off" placeholder="1,234.50">'
    if key == "baht-text":
        return f"""<form class="tool" data-tool="baht-text">
  {control(L["amount"], amount)}
  <div class="output" data-state="ok" hidden><span class="label">{esc(L["result"])}</span><p class="big" data-out="text" lang="th"></p><button type="button" class="btn light sm" data-copy="text">{esc(L["copy"])}</button></div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "vat":
        mode = radios("mode", [("exclusive", L["exclusive"]), ("inclusive", L["inclusive"])], "exclusive")
        return f"""<form class="tool" data-tool="vat">
  {control(L["amount"], amount)}
  <fieldset class="field"><legend>{esc(L["mode"])}</legend>{mode}</fieldset>
  {control(L["rate"], '<input name="rate" inputmode="decimal" value="7" class="short">')}
  <div class="output" data-state="ok" hidden>{result_rows([("base", L["base"]), ("vat", L["vat"]), ("total", L["total"])])}</div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "withholding-tax":
        mode = radios("mode", [("exclusive", L["exclusive"]), ("inclusive", L["inclusive"])], "exclusive")
        options = "".join(f'<option value="{rate}">{esc(names[lang])}</option>' for rate, names in C.WHT_RATES)
        options += f'<option value="custom">{esc(L["custom"])}</option>'
        return f"""<form class="tool" data-tool="withholding-tax">
  {control(L["preset"], f'<select name="preset">{options}</select>')}
  <div data-custom-rate hidden>{control(L["rate"], '<input name="rate" inputmode="decimal" value="3" class="short">')}</div>
  {control(L["amount"], amount)}
  <fieldset class="field"><legend>{esc(L["mode"])}</legend>{mode}</fieldset>
  <label class="check"><input type="checkbox" name="vat" checked><span>{esc(L["vat"])}</span></label>
  <div class="output" data-state="ok" hidden>{result_rows([("base", L["base"]), ("vat", L["vat_out"]), ("wht", L["wht"]), ("pay", L["pay"])])}</div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "tax-id":
        return f"""<form class="tool" data-tool="tax-id">
  {control(L["id"], '<input name="id" inputmode="numeric" autocomplete="off" placeholder="0-1055-99999-99-7" maxlength="20">')}
  <p class="ok" data-state="valid" hidden>✓ {esc(L["valid"])} · <span data-out="formatted"></span></p>
  <p class="warn" data-state="invalid" hidden>✗ {esc(L["invalid"])}</p>
  <p class="muted" data-state="incomplete" hidden>{esc(L["incomplete"])}</p>
</form>"""
    raise KeyError(key)


def page_tool(lang, key):
    p, t = C.TOOL[key][lang], C.COMMON[lang]
    note = f'<p class="note">{esc(p["note"])}</p>' if p.get("note") else ""
    faq = "".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in p["faq"])
    others = "".join(
        f'<a class="tile" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p></a>'
        for k in C.TOOLS if k != key
    )
    trail = [("home", t["home"]), ("tools", t["nav"]["tools"]), (key, p["name"])]
    app = {"@type": "WebApplication", "name": p["name"], "description": p["description"], "url": absolute(url(lang, key)),
           "applicationCategory": "FinanceApplication", "operatingSystem": "Any", "isAccessibleForFree": True,
           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "THB"}, "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}
    body = f"""<section class="hero sub"><div class="wrap center">{crumbs(lang, trail)}<h1 class="display">{esc(p["h1"])}</h1><p class="lead">{esc(p["lead"])}</p></div></section>
<section class="wrap narrow">
  <div class="panel toolpanel">{tool_form(lang, key)}{note}<p class="muted small">{esc(t["tool_local"])}</p></div>
</section>
<section class="wrap narrow section">
  <h2 class="display">{esc(t["faq"])}</h2>
  <div class="faq">{faq}</div>
</section>
{related_guides(lang, lambda g: key in g.tools)}
<section class="wrap section">
  <a class="pitch reveal" href="{url(lang, "home")}"><span>{esc(t["tool_cta"])}</span>{icon("arrow")}</a>
  <h2 class="display center">{esc(t["tools_more"])}</h2>
  <div class="tiles three">{others}</div>
</section>
<script src="/static/tools.js?v={VERSION}" defer></script>
"""
    return head(lang, key, p["title"], p["description"], [ORG, breadcrumb(lang, trail), app]) + header(lang, key) + body + footer(lang)


# ---------------- 泰文教程文章 ----------------

def guide_tile(g):
    T = C.GUIDE_TEXT
    return (f'<a class="tile reveal" href="{url("th", "guide:" + g.slug)}"><span class="eyebrow">{esc(T["read"].format(minutes=g.minutes))}</span>'
            f'<h3>{esc(g.h1)}</h3><p>{esc(g.lead)}</p><span class="more">{icon("arrow", "icon sm")}</span></a>')


def page_guides():
    lang, t, P = "th", C.COMMON["th"], C.GUIDES_INDEX
    trail = [("home", t["home"]), ("guides", C.GUIDE_TEXT["label"])]
    items = "".join(guide_tile(g) for g in G.ALL)
    graph = [ORG, breadcrumb(lang, trail), {"@type": "CollectionPage", "name": P["h1"], "url": absolute(url(lang, "guides")), "inLanguage": "th",
             "hasPart": [{"@type": "Article", "headline": g.h1, "url": absolute(url(lang, "guide:" + g.slug))} for g in G.ALL]}]
    body = f"""<section class="hero sub"><div class="wrap center">{crumbs(lang, trail)}<h1 class="display">{esc(P["h1"])}</h1><p class="lead">{esc(P["lead"])}</p></div></section>
<section class="wrap section"><div class="tiles pair">{items}</div></section>
"""
    return head(lang, "guides", P["title"], P["description"], graph) + header(lang, "guides") + body + contact(lang) + footer(lang)


def page_guide(g):
    lang, t, T = "th", C.COMMON["th"], C.GUIDE_TEXT
    key = "guide:" + g.slug
    trail = [("home", t["home"]), ("guides", T["label"]), (key, g.h1)]
    body_html, toc = g.sections()
    toc_html = "".join(f'<li><a href="#{sid}">{esc(title)}</a></li>' for sid, title in toc)
    p, o = C.PRODUCT[g.product][lang], C.OVERVIEW[g.product]
    tools = "".join(
        f'<a class="tile" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p></a>' for k in g.tools
    )
    tools_html = f'<section class="wrap narrow section"><h2 class="display">{esc(T["tools"])}</h2><div class="tiles three">{tools}</div></section>' if tools else ""
    others = "".join(guide_tile(other) for other in G.ALL if other.slug != g.slug)
    shot = window(f'<img src="{o["poster"]}" alt="" loading="lazy" width="1280" height="720"><span class="playbtn"><span class="circle">{icon("play")}</span></span>')
    cta = f"""<section class="wrap section">
  <article class="panel product reveal">
    <div class="product-text">
      <span class="eyebrow">{esc(p["who"])}</span>
      <h3 class="display">{esc(T["cta_title"].format(name=p["name"]))}</h3>
      <p class="muted">{esc(p["summary"])}</p>
      <div class="actions"><a class="btn dark" href="{C.LINE_URL}" rel="noopener">{icon("chat")}<span>{esc(t["cta"])}</span></a><a class="btn light" href="{url(lang, g.product)}">{esc(t["more"])}</a></div>
    </div>
    <a class="poster" href="{url(lang, g.product)}#overview" aria-label="{esc(t["overview"])} · Pearnly {esc(p["name"])}">{shot}</a>
  </article>
</section>"""
    article = {"@type": "Article", "headline": g.h1, "description": g.description, "datePublished": g.published, "dateModified": g.updated,
               "inLanguage": "th", "mainEntityOfPage": absolute(url(lang, key)), "image": C.SITE + "/static/og.png",
               "author": {"@id": ORG["@id"]}, "publisher": {"@id": ORG["@id"]}}
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(T["label"])} · {esc(T["read"].format(minutes=g.minutes))}</span>
    <h1 class="display">{esc(g.h1)}</h1>
    <p class="lead">{esc(g.lead)}</p>
    <p class="meta">{esc(T["updated"].format(date=G.thai_date(g.updated)))}</p>
  </div>
</section>
<section class="wrap narrow">
  <nav class="toc panel" aria-label="{esc(T["toc"])}"><span>{esc(T["toc"])}</span><ol>{toc_html}</ol></nav>
  <article class="prose">{body_html}<p class="disclaimer">{esc(T["disclaimer"])}</p></article>
</section>
{tools_html}
{cta}
<section class="wrap section"><h2 class="display center">{esc(T["more"])}</h2><div class="tiles three">{others}</div></section>
"""
    return head(lang, key, g.title, g.description, [ORG, breadcrumb(lang, trail), article]) + header(lang, key) + body + footer(lang)


def page_404(lang):
    t = C.COMMON[lang]
    back = f'<div class="actions"><a class="btn dark" href="{url(lang, "home")}">{esc(t["notfound_back"])}</a></div>'
    body = (f'<section class="hero"><div class="wrap center"><h1 class="display">{esc(t["notfound_title"])}</h1>'
            f'<p class="lead">{esc(t["notfound_text"])}</p>{back}</div></section>\n')
    return head(lang, "home", t["notfound_title"] + " | Pearnly", t["notfound_text"], None, noindex=True) + header(lang, "home") + body + footer(lang)


def render(lang, key):
    if key == "guides":
        return page_guides()
    if key.startswith("guide:"):
        return page_guide(next(g for g in G.ALL if "guide:" + g.slug == key))
    if key == "home":
        return page_home(lang)
    if key in C.PRODUCTS:
        return page_product(lang, key)
    if key == "tools":
        return page_tools(lang)
    return page_tool(lang, key)


def sitemap():
    rows = []
    for key in list(C.PAGES) + list(THAI_ONLY):
        for lang in languages(key):
            alts = "".join(f'<xhtml:link rel="alternate" hreflang="{C.LANGS[l]["hreflang"]}" href="{absolute(url(l, key))}"/>' for l in languages(key))
            alts += f'<xhtml:link rel="alternate" hreflang="x-default" href="{absolute(url(C.DEFAULT_LANG, key))}"/>'
            rows.append(f"<url><loc>{absolute(url(lang, key))}</loc>{alts}</url>")
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n' + "\n".join(rows) + "\n</urlset>\n")


def build(out):
    global VERSION
    VERSION = asset_version()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(STATIC, out / "static")
    for key in list(C.PAGES) + list(THAI_ONLY):
        for lang in languages(key):
            target = out / (url(lang, key).lstrip("/")) / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render(lang, key), encoding="utf-8")
    shutil.copy(STATIC / "favicon.ico", out / "favicon.ico")  # 谷歌搜索结果的站点图标：至少 48×48，根目录也放一份
    (out / "404.html").write_text(page_404(C.DEFAULT_LANG), encoding="utf-8")
    (out / "sitemap.xml").write_text(sitemap(), encoding="utf-8")
    (out / "robots.txt").write_text(f"User-agent: *\nAllow: /\n\nSitemap: {C.SITE}/sitemap.xml\n", encoding="utf-8")
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE / "dist")
    args = parser.parse_args()
    out = build(args.out.resolve())
    pages = sum(1 for _ in out.rglob("index.html"))
    print(f"Built {pages} pages into {out}")


if __name__ == "__main__":
    main()
