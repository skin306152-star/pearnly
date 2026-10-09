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
from urllib.parse import quote
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import content as C  # noqa: E402
import guides as G  # noqa: E402
import mascot as M  # noqa: E402
import poster as P  # noqa: E402
import scenes as S  # noqa: E402
import story as ST  # noqa: E402

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
THAI_ONLY = {"guides": "/guides/", **{"guide:" + g.slug: f"/guides/{g.slug}/" for g in G.ALL},
             **{"landing:" + g.slug: f"/{g.slug}/" for g in G.LANDING}}


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
       "address": {"@type": "PostalAddress", **C.COMPANY["postal"]}, "sameAs": [C.LINE_URL, C.YOUTUBE_URL, C.MRERP_URL]}

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
    "asset": '<path d="M4 21V9l8-5 8 5v12M9 21v-6h6v6"/>',
    "percent": '<path d="M19 5L5 19"/><circle cx="7" cy="7" r="2.5"/><circle cx="17" cy="17" r="2.5"/>',
    "book": '<path d="M6 3h12v18H6z"/><path d="M9 3v18M12 8h4M12 12h4"/>',
    "clock": '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
    "login": '<path d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 16l4-4-4-4M14 12H4"/>',
}


def icon(name, cls="icon"):
    return (f'<svg class="{cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" '
            f'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">{ICONS[name]}</svg>')


def breadcrumb(lang, trail):
    items = [{"@type": "ListItem", "position": i + 1, "name": name, "item": absolute(url(lang, key))} for i, (key, name) in enumerate(trail)]
    return {"@type": "BreadcrumbList", "itemListElement": items}


def head(lang, key, title, description, graph, noindex=False, fonts=()):
    alternates = "".join(f'<link rel="alternate" hreflang="{C.LANGS[l]["hreflang"]}" href="{absolute(url(l, key))}">' for l in languages(key))
    alternates += f'<link rel="alternate" hreflang="x-default" href="{absolute(url(C.DEFAULT_LANG, key))}">'
    canonical = absolute(url(lang, key))
    ld = jsonld({"@context": "https://schema.org", "@graph": graph}) if graph else ""
    robots = '<meta name="robots" content="noindex">\n' if noindex else ""
    extra = "".join(f'<link rel="stylesheet" href="{esc(f)}">\n' for f in fonts)
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
{extra}<link rel="stylesheet" href="/static/style.css?v={VERSION}">
<script>document.documentElement.classList.add('js')</script>
<script src="/static/site.js?v={VERSION}" defer></script>
{ld}
</head>
"""


def header(lang, key):
    t = C.COMMON[lang]
    here, chosen = ' aria-current="page"', ' aria-current="true"'
    nav = "".join(
        f'<a href="{C.TUTORIALS_URL}" target="_blank" rel="noopener">{esc(label)}</a>' if k == "tutorials" else
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
    products += f'<li><a href="{url(lang, "mrerp")}">MR.ERP ERP · DMS</a></li>'
    if lang == "th":
        products += "".join(f'<li><a href="{url(lang, "landing:" + g.slug)}">{esc(g.meta.get("short", g.h1))}</a></li>' for g in G.LANDING)
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
    <p class="legal-links"><a href="{url(lang, "about")}">{esc(t["about"])}</a><a href="{url(lang, "privacy")}">{esc(t["privacy"])}</a><a href="{url(lang, "terms")}">{esc(t["terms"])}</a></p>
    <p>{esc(C.COMPANY["address"][lang])}</p>
  </div>
</footer>
</body>
</html>
"""


def line_message(text):
    """打开 @pearnly 的对话并预填好这句话（LINE 的 oaMessage 链接）。只在手机上有用：电脑上它会跳到 LINE 首页。"""
    return "https://line.me/R/oaMessage/" + quote(C.LINE_ID, safe="") + "/?" + quote(text, safe="")


def demo_button(lang, name="Pearnly", consult=False):
    """默认链到加好友页（电脑上显示二维码）；site.js 在手机上换成预填好消息的对话，客户点一下发送就行。"""
    t = C.COMMON[lang]
    message = line_message(t["consult_msg"] if consult else t["demo_msg"].format(name=name))
    return (f'<a class="btn dark" href="{C.LINE_URL}" data-line-message="{esc(message)}" rel="noopener">'
            f'{icon("chat")}<span>{esc(t["cta_consult" if consult else "cta"])}</span></a>')


def cta_buttons(lang, extra="", name="Pearnly", consult=False):
    return f'<div class="actions">{demo_button(lang, name, consult)}{extra}</div>'


def contact(lang, name="Pearnly", consult=False):
    t = C.COMMON[lang]
    mail = f'<a class="btn light" href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a>'
    return f"""<section class="wrap section">
  <div class="panel cta reveal">
    <h2 class="display">{esc(t["contact_title"])}</h2>
    <p class="lead">{esc(t["contact_text"])}</p>
    {cta_buttons(lang, mail, name, consult)}
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


# ---------------- 首页交互演示：三段"现实 → 系统"的小故事（样例全是虚构） ----------------
# 编排写在标记里，site.js 只是一个通用时间轴：
#   data-at="900,1500:pick,2400:-pick"  到点给元素加/去类（默认类 on，"-类" 表示去掉）
#   class="fly" data-from data-to       到点从一个元素飞到另一个元素（按实时位置算，桌面/手机通用）
#   data-type="文字"                    加上 on 时逐字打出来
#   data-count="on:120:110|refill:6:30" 加上对应类时数字滚动到目标值
# 系统开了"减少动态效果"时直接显示终态。

def flyer(text, src, dst, at, cls=""):
    return f'<span class="fly {cls}" data-from="{src}" data-to="{dst}" data-at="{at}">{esc(text)}</span>'


def demo(lang):
    L = C.DEMO_LABELS[lang]
    tabs = "".join(
        f'<button type="button" role="tab" data-tab="{k}" aria-selected="{"true" if i == 0 else "false"}">{esc(C.PRODUCT[k][lang]["name"])}</button>'
        for i, k in enumerate(C.PRODUCTS))
    body = S.DEFS + S.cowork(L).replace('class="scene ', 'class="scene active ', 1) + S.dms(L) + S.erp(L)
    return window(f'<div class="demo-body" aria-hidden="true">{body}</div>',
                  f'<div class="seg" role="tablist">{tabs}</div>', "demo reveal")


# ---------------- 页面 ----------------

def hand_chars(lang):
    """首页上所有用手写体写出来的字（中文手写字体按字取子集，漏了的字会掉回印刷体）。"""
    t, m = C.COMMON[lang], C.MRERP[lang]
    parts = [*C.DEMO_LABELS[lang].values(), *C.INTEGRATIONS[lang], t["integrations"], t["more"], m["tier_eyebrow"],
             t["products"], t["nav"]["tools"], t["contact"], *(C.PRODUCT[k][lang]["who"] for k in C.PRODUCTS)]
    return "".join(str(x) for x in parts)


def page_home(lang):
    h, t = C.HOME[lang], C.COMMON[lang]
    watch = f'<a class="btn light" href="#products">{icon("play")}<span>{esc(t["watch"])}</span></a>'
    logos = "".join(f"<span>{esc(name)}</span>" for name in C.INTEGRATIONS[lang])
    products = ""
    for i, k in enumerate(C.PRODUCTS):
        p, o = C.PRODUCT[k][lang], C.OVERVIEW[k]
        points = "".join(f"<li>{icon('check', 'icon sm')}<span>{esc(a)}</span></li>" for a, _ in p["features"][:3])
        shot = window(f'<img src="{o["poster"]}" alt="" loading="lazy" width="1280" height="720"><span class="playbtn"><span class="circle">{icon("play")}</span></span>')
        products += f"""<article class="panel product reveal{' flip' if i % 2 else ''}">{S.badge(k, "card-mascot")}
  <div class="product-text">
    <span class="eyebrow">{esc(p["who"])}</span>
    <h3 class="display">Pearnly {esc(p["name"])}</h3>
    <p class="muted">{esc(p["summary"])}</p>
    <ul class="points">{points}</ul>
    <a class="more" href="{url(lang, k)}">{esc(t["more"])} {icon("arrow", "icon sm")}</a>
  </div>
  <a class="poster" href="{url(lang, k)}#overview" aria-label="{esc(t["overview"])} · Pearnly {esc(p["name"])}">{shot}</a>
</article>"""
    samples = {"baht-text": "1,070 → หนึ่งพันเจ็ดสิบบาทถ้วน", "vat": "1,070.00 → VAT 70.00",
               "withholding-tax": "10,000 · 3% → 300.00", "tax-id": "0-1055-99999-99-7 ✓"}
    tools = "".join(
        f'<a class="tile reveal" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p><code>{esc(samples[k])}</code></a>'
        for k in C.TOOLS
    )
    graph = [ORG, {"@type": "WebSite", "@id": C.SITE + "/#site", "name": "Pearnly", "url": C.SITE + "/",
                   "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}]
    steps = "".join(f"<li><b>{esc(a)}</b> {esc(b)}</li>" for a, b in h["steps"])
    tale = {**h["tale"], "cols": h["story"]["cols"], "saved": h["story"]["saved"]}
    body = f"""<section class="hero">
  <div class="wrap center">
    <h1 class="display">{lines(h["h1"]).replace(esc(h["h1_mark"]), f'<span class="hl">{esc(h["h1_mark"])}</span>', 1)}</h1>
    <p class="lead">{esc(h["lead"])}</p>
    {cta_buttons(lang, watch)}
  </div>
  <div class="wrap wide st-wrap">{P.doodle()}{ST.figure(tale, C.STORY_ROWS[:8])}</div>
</section>
<section class="wrap wide section demo-band" aria-labelledby="steps-title"><h2 class="sr-only" id="steps-title">{esc(h["steps_title"])}</h2><ol class="sr-only">{steps}</ol><div class="hero-stage">{demo(lang)}</div></section>
<section class="wrap section logos reveal">
  <p class="serif">{esc(t["integrations"])}</p>
  <div class="logo-row">{logos}</div>
</section>
<section class="wrap section" id="products">
  <h2 class="display center reveal">{esc(h["products_title"])}</h2>
  <div class="stack">{products}</div>
</section>
{pro_tier(lang, sketch=True)}
<section class="wrap section">
  <h2 class="display center reveal">{esc(h["tools_title"])}</h2>
  <p class="lead center reveal">{esc(h["tools_text"])}</p>
  <div class="tiles">{tools}</div>
</section>
{home_guides(lang)}"""
    # 首页整页手绘（试验）：正文仍是印刷体，边框、标题下划线、对勾、标签小字换成铅笔手绘，见 style.css「首页手绘」
    return (head(lang, "home", h["title"], h["description"], graph, fonts=P.fonts(hand_chars(lang) + ST.chars(tale))) + header(lang, "home")
            + '<div class="sketch">' + body + contact(lang) + '</div>' + footer(lang).replace('<footer class="footer', '<footer class="footer sketch', 1))


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
    """泰文页面底部的“延伸阅读”：工具页按 tools、产品页按 product 找关键词专页与文章。"""
    found = [("landing:" + g.slug, g) for g in G.LANDING if matches(g)] + [("guide:" + g.slug, g) for g in G.ALL if matches(g)]
    if lang != "th" or not found:
        return ""
    links = "".join(f'<li><a href="{url(lang, key)}">{esc(g.h1)}</a></li>' for key, g in found)
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
              "thumbnailUrl": absolute(o["poster"]), "contentUrl": video_url(o), "uploadDate": o["uploaded"] + "T00:00:00+07:00",  # Google 要带时区的完整日期时间（泰国时间）
              "duration": f"PT{minutes}M{seconds}S", "inLanguage": "th", "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(p["who"])}</span>
    <h1 class="display">{esc(p["h1"])}</h1>
    <p class="lead">{esc(p["lead"])}</p>
    {cta_buttons(lang, download, "Pearnly " + p["name"])}
  </div>
  <div class="wrap wide" id="overview">{window(f'<div class="player">{video}</div>', bar, "reveal")}</div>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(t["features"])}</h2>
  <ul class="cards">{features}</ul>
</section>
{related_guides(lang, lambda g: g.product == key)}
{pro_band(lang, key)}
<section class="wrap section">
  <h2 class="display center reveal">{esc(t["others"])}</h2>
  <div class="tiles two">{others}</div>
</section>
"""
    return head(lang, key, p["title"], p["description"], graph) + header(lang, key) + body + contact(lang, "Pearnly " + p["name"]) + footer(lang)


# ---------------- MR.ERP 专业版（同一家公司） ----------------

def walkers(sketch=False):
    """三只 Pearnly 小单沿虚线走向托着小单的大账本：从轻量长大到完整版。sketch=True 时三只小单用首页的铅笔画版。"""
    small = "".join(f'<span class="walker w{i}">{S.badge(k, "mascot") if sketch else M.svg(k)}</span>' for i, k in enumerate(C.PRODUCTS))
    return f'<div class="walk" aria-hidden="true"><div class="walk-line"></div>{small}<span class="walker big">{M.svg("pro")}</span></div>'


def pro_tier(lang, sketch=False):
    m = C.MRERP[lang]
    cards = "".join(f'<li><b>{esc(a)}</b><span>{esc(b)}</span></li>' for a, b in m["tier_cards"])
    return f"""<section class="wrap section" id="mrerp">
  <div class="panel pro reveal">
    <div class="pro-text">
      <span class="eyebrow">{esc(m["tier_eyebrow"])}</span>
      <h2 class="display">{esc(m["tier_title"])}</h2>
      <p>{esc(m["tier_text"])}</p>
      <ul class="pro-cards">{cards}</ul>
      <a class="btn light" href="{url(lang, "mrerp")}"><span>{esc(m["tier_more"])}</span>{icon("arrow", "icon sm")}</a>
    </div>
    {walkers(sketch)}
  </div>
</section>
"""


def pro_band(lang, key):
    band = C.MRERP[lang]["band"].get(key)
    if not band:
        return ""
    return f"""<section class="wrap section">
  <a class="pro-band reveal" href="{url(lang, "mrerp")}">{M.svg("pro", "band-mascot")}<span><b>{esc(band[0])}</b><span>{esc(band[1])}</span></span>{icon("arrow")}</a>
</section>
"""


def page_mrerp(lang):
    m, t = C.MRERP[lang], C.COMMON[lang]
    trail = [("home", t["home"]), ("mrerp", "MR.ERP")]
    login = f'<a class="btn light" href="{C.MRERP_LOGIN}" rel="noopener">{icon("login")}<span>{esc(m["login"])}</span></a>'

    def column(name, note, items, cls):
        rows = "".join(f"<li>{esc(x)}</li>" for x in items)
        return f'<div class="flow-col {cls}"><b>{esc(name)}</b><span>{esc(note)}</span><ul>{rows}</ul></div>'

    modules = "".join(f'<li class="card reveal">{icon(ic)}<h3>{esc(x)}</h3></li>' for x, ic in zip(m["modules"], C.MRERP_ICONS))
    industries = "".join(f"<span>{esc(x)}</span>" for x in m["industries"])
    head_row = "".join(f"<th>{esc(c)}</th>" for c in m["compare_cols"])
    rows = "".join(f"<tr><th>{esc(a)}</th><td>{esc(b)}</td><td>{esc(c)}</td></tr>" for a, b, c in m["compare_rows"])
    price, light, full = m["compare_price"]  # 不写价格：两边都接我们的 LINE
    rows += (f'<tr class="price"><th>{esc(price)}</th><td><a href="{C.LINE_URL}" rel="noopener">{esc(light)}</a></td>'
             f'<td><a href="{C.LINE_URL}" rel="noopener">{esc(full)} {icon("arrow", "icon sm")}</a></td></tr>')
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "SoftwareApplication", "name": "MR.ERP", "applicationCategory": "BusinessApplication", "operatingSystem": "Web",
              "description": m["description"], "url": absolute(url(lang, "mrerp")), "sameAs": C.MRERP_URL,
              "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(m["badge"])}</span>
    <h1 class="display">{lines(m["h1"])}</h1>
    <p class="lead">{esc(m["lead"])}</p>
    {cta_buttons(lang, login, consult=True)}
  </div>
</section>
<section class="wrap section">
  <div class="panel flow reveal">
    <h2 class="display center">{esc(m["flow_title"])}</h2>
    <p class="lead center">{esc(m["flow_text"])}</p>
    <div class="flow-grid">{column(*m["light"], "light")}{walkers()}{column(*m["full"], "full")}</div>
  </div>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(m["modules_title"])}</h2>
  <ul class="cards modules">{modules}</ul>
  <div class="chips reveal"><p>{esc(m["industries_title"])}</p><div>{industries}</div></div>
</section>
<section class="wrap section">
  <article class="panel product reveal">
    <div class="product-text">
      <span class="eyebrow">MR.ERP DMS</span>
      <h3 class="display">{esc(m["dms_title"])}</h3>
      <p class="muted">{esc(m["dms_text"])}</p>
      <div class="actions"><a class="btn dark" href="{C.MRERP_DMS_LOGIN}" rel="noopener">{icon("login")}<span>{esc(m["dms_login"])}</span></a>
      <a class="btn light" href="{url(lang, "dms")}">{esc(m["dms_more"])}</a></div>
    </div>
    <div class="duo" aria-hidden="true">{M.svg("dms")}<span class="duo-arrow">{icon("arrow")}</span>{M.svg("prodms")}</div>
  </article>
</section>
<section class="wrap narrow section">
  <h2 class="display center reveal">{esc(m["compare_title"])}</h2>
  <div class="compare reveal"><table><thead><tr>{head_row}</tr></thead><tbody>{rows}</tbody></table></div>
</section>
"""
    return head(lang, "mrerp", m["title"], m["description"], graph) + header(lang, "mrerp") + body + contact(lang, consult=True) + footer(lang)


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
      <div class="actions">{demo_button(lang, "Pearnly " + p["name"])}<a class="btn light" href="{url(lang, g.product)}">{esc(t["more"])}</a></div>
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


def page_landing(g):
    """关键词专页：一个搜索需求一页，带产品总览片、常见问题（FAQPage）与相关文章/工具。"""
    lang, t, T = "th", C.COMMON["th"], C.GUIDE_TEXT
    key = "landing:" + g.slug
    p, o = C.PRODUCT[g.product][lang], C.OVERVIEW[g.product]
    trail = [("home", t["home"]), (g.product, "Pearnly " + p["name"]), (key, g.h1)]
    video = (f'<video preload="none" playsinline controls poster="{o["poster"]}" width="1920" height="1080" aria-label="{esc(t["overview"])}">'
             f'<source src="{video_url(o)}" type="video/mp4"></video>'
             f'<button type="button" class="playbtn" aria-label="{esc(t["play"])}"><span class="circle">{icon("play")}</span></button>')
    bar = f'<span class="wintitle">{esc(t["overview"])} · Pearnly {esc(p["name"])}</span>'
    more = f'<a class="btn light" href="{url(lang, g.product)}">{esc(t["more"])}</a>'
    faq = "".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in g.faq)
    related = "".join(
        f'<a class="tile" href="{url(lang, "guide:" + other.slug)}"><span class="eyebrow">{esc(T["label"])}</span><h3>{esc(other.h1)}</h3><p>{esc(other.lead)}</p></a>'
        for other in G.ALL if other.slug in g.meta.get("guides", [])
    ) + "".join(
        f'<a class="tile" href="{url(lang, k)}"><span class="eyebrow">{esc(t["nav"]["tools"])}</span><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p></a>'
        for k in g.tools
    )
    related_html = f'<section class="wrap section"><h2 class="display center">{esc(T["related"])}</h2><div class="tiles three">{related}</div></section>' if related else ""
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "WebPage", "name": g.h1, "description": g.description, "url": absolute(url(lang, key)), "inLanguage": "th",
              "dateModified": g.updated, "about": {"@type": "SoftwareApplication", "name": "Pearnly " + p["name"], "url": absolute(url(lang, g.product))},
              "publisher": {"@id": ORG["@id"]}},
             {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in g.faq]}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(p["who"])}</span>
    <h1 class="display">{esc(g.h1)}</h1>
    <p class="lead">{esc(g.lead)}</p>
    {cta_buttons(lang, more, "Pearnly " + p["name"])}
  </div>
  <div class="wrap wide">{window(f'<div class="player">{video}</div>', bar, "reveal")}</div>
</section>
<section class="wrap narrow">
  <article class="prose">{g.body}</article>
</section>
<section class="wrap narrow section">
  <h2 class="display">{esc(t["faq"])}</h2>
  <div class="faq">{faq}</div>
</section>
{related_html}
"""
    return head(lang, key, g.title, g.description, graph) + header(lang, key) + body + contact(lang, "Pearnly " + p["name"]) + footer(lang)


# ---------------- 关于我们 ----------------

def page_about(lang):
    a, t, m = C.ABOUT[lang], C.COMMON[lang], C.MRERP[lang]
    trail = [("home", t["home"]), ("about", t["about"])]

    def column(name, note, items, cls, href):
        rows = "".join(f"<li>{esc(x)}</li>" for x in items)
        return f'<a class="flow-col {cls}" href="{href}"><b>{esc(name)}</b><span>{esc(note)}</span><ul>{rows}</ul></a>'

    story = "".join(f"<p>{esc(x)}</p>" for x in a["story"])
    principles = "".join(f'<li class="card reveal"><h3>{esc(h)}</h3><p>{esc(d)}</p></li>' for h, d in a["principles"])
    industries = "".join(f"<span>{esc(x)}</span>" for x in m["industries"])
    values = [C.COMPANY["legal_name"], C.COMPANY["reg_no"], C.COMPANY["address"][lang],
              f'<a href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a>', f'<a href="{C.LINE_URL}" rel="noopener">{esc(C.LINE_ID)}</a>',
              f'<a href="{C.MRERP_URL}" rel="noopener">mrerp4sme.com</a>']
    rows = "".join(f"<tr><th>{esc(k)}</th><td>{v if v.startswith('<a') else esc(v)}</td></tr>" for k, v in zip(a["rows"], values))
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "AboutPage", "name": a["title"], "url": absolute(url(lang, "about")), "inLanguage": C.LANGS[lang]["html"],
              "about": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <span class="badge">{esc(a["badge"])}</span>
    <h1 class="display">{lines(a["h1"])}</h1>
    <p class="lead">{esc(a["lead"])}</p>
  </div>
</section>
<section class="wrap narrow section">
  <h2 class="display">{esc(a["story_title"])}</h2>
  <div class="prose about-story">{story}</div>
</section>
<section class="wrap section">
  <div class="panel flow reveal">
    <h2 class="display center">{esc(a["family_title"])}</h2>
    <div class="flow-grid">{column(*m["light"], "light", url(lang, "home") + "#products")}{walkers()}{column(*m["full"], "full", url(lang, "mrerp"))}</div>
  </div>
  <div class="chips reveal"><p>{esc(a["industries_title"])}</p><div>{industries}</div></div>
</section>
<section class="wrap section">
  <h2 class="display center reveal">{esc(a["principles_title"])}</h2>
  <ul class="cards">{principles}</ul>
</section>
<section class="wrap narrow section">
  <h2 class="display center reveal">{esc(a["company_title"])}</h2>
  <div class="compare reveal"><table><tbody>{rows}</tbody></table></div>
</section>
"""
    return head(lang, "about", a["title"], a["description"], graph) + header(lang, "about") + body + contact(lang) + footer(lang)


# ---------------- 隐私政策与使用条款 ----------------

def policy_blocks(blocks):
    """一节里的内容：字符串是段落，列表是要点。"""
    return "".join(f"<p>{esc(b)}</p>" if isinstance(b, str) else "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in b) + "</ul>"
                   for b in blocks)


def page_policy(lang, key):
    """隐私政策（C.PRIVACY）与使用条款（C.TERMS）共用一个版式。"""
    pv, t = (C.PRIVACY if key == "privacy" else C.TERMS)[lang], C.COMMON[lang]
    effective = C.PRIVACY_EFFECTIVE if key == "privacy" else C.TERMS_EFFECTIVE
    trail = [("home", t["home"]), (key, t[key])]
    toc = "".join(f'<li><a href="#s{i + 1}">{esc(h)}</a></li>' for i, (h, _b) in enumerate(pv["sections"]))
    sections = "".join(f'<h2 id="s{i + 1}">{esc(h)}</h2>{policy_blocks(b)}' for i, (h, b) in enumerate(pv["sections"]))
    contact_rows = (f'<p>{esc(C.COMPANY["legal_name"])} · {esc(C.COMPANY["address"][lang])}</p>'
                    f'<p><a href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a> · <a href="{C.LINE_URL}" rel="noopener">LINE {esc(C.LINE_ID)}</a></p>')
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "WebPage", "name": pv["h1"], "url": absolute(url(lang, key)), "inLanguage": C.LANGS[lang]["html"],
              "dateModified": effective, "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero sub">
  <div class="wrap center">
    {crumbs(lang, trail)}
    <h1 class="display">{esc(pv["h1"])}</h1>
    <p class="lead">{esc(pv["lead"])}</p>
    <p class="meta">{esc(pv["effective"])}</p>
  </div>
</section>
<section class="wrap narrow">
  <nav class="toc panel" aria-label="{esc(pv["h1"])}"><ol>{toc}</ol></nav>
  <article class="prose policy">{sections}<h2>{esc(t["contact"])}</h2>{contact_rows}</article>
</section>
"""
    return head(lang, key, pv["title"], pv["description"], graph) + header(lang, key) + body + footer(lang)


def page_404(lang):
    t = C.COMMON[lang]
    back = f'<div class="actions"><a class="btn dark" href="{url(lang, "home")}">{esc(t["notfound_back"])}</a></div>'
    body = (f'<section class="hero"><div class="wrap center">{M.svg("find", "nf-mascot")}<h1 class="display">{esc(t["notfound_title"])}</h1>'
            f'<p class="lead">{esc(t["notfound_text"])}</p>{back}</div></section>\n')
    return head(lang, "home", t["notfound_title"] + " | Pearnly", t["notfound_text"], None, noindex=True) + header(lang, "home") + body + footer(lang)


def render(lang, key):
    if key == "guides":
        return page_guides()
    if key.startswith("guide:"):
        return page_guide(next(g for g in G.ALL if "guide:" + g.slug == key))
    if key.startswith("landing:"):
        return page_landing(next(g for g in G.LANDING if "landing:" + g.slug == key))
    if key == "home":
        return page_home(lang)
    if key in C.PRODUCTS:
        return page_product(lang, key)
    if key == "tools":
        return page_tools(lang)
    if key == "mrerp":
        return page_mrerp(lang)
    if key == "about":
        return page_about(lang)
    if key in ("privacy", "terms"):
        return page_policy(lang, key)
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
