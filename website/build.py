#!/usr/bin/env python3
"""生成 pearnly.com 静态官网：纯 HTML + 一个样式表 + 一个工具脚本，只用标准库。

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
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import content as C  # noqa: E402

STATIC = HERE / "static"


def esc(text):
    return html.escape(str(text), quote=True)


def url(lang, key):
    return C.LANGS[lang]["prefix"] + C.PAGES[key]


def absolute(path):
    return C.SITE + path


def media(path):
    return C.MEDIA + quote(path)


def asset_version():
    digest = hashlib.sha256()
    for name in ("style.css", "tools.js"):
        digest.update((STATIC / name).read_bytes())
    return digest.hexdigest()[:10]


VERSION = None


def jsonld(data):
    return '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/") + "</script>"


ORG = {"@type": "Organization", "@id": C.SITE + "/#org", "name": "Pearnly", "url": C.SITE + "/", "logo": C.SITE + "/static/logo.png",
       "email": C.EMAIL, "sameAs": [C.LINE_URL]}


def breadcrumb(lang, trail):
    items = [{"@type": "ListItem", "position": i + 1, "name": name, "item": absolute(url(lang, key))} for i, (key, name) in enumerate(trail)]
    return {"@type": "BreadcrumbList", "itemListElement": items}


def head(lang, key, title, description, graph):
    alternates = "".join(f'<link rel="alternate" hreflang="{C.LANGS[l]["hreflang"]}" href="{absolute(url(l, key))}">' for l in C.LANGS)
    alternates += f'<link rel="alternate" hreflang="x-default" href="{absolute(url(C.DEFAULT_LANG, key))}">'
    canonical = absolute(url(lang, key))
    ld = jsonld({"@context": "https://schema.org", "@graph": graph}) if graph else ""
    return f"""<!doctype html>
<html lang="{C.LANGS[lang]["html"]}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
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
<meta name="theme-color" content="#ffffff">
<link rel="icon" type="image/png" sizes="32x32" href="/static/icon-32.png">
<link rel="apple-touch-icon" href="/static/icon-180.png">
<link rel="stylesheet" href="/static/style.css?v={VERSION}">
{ld}
</head>
"""


def header(lang, key):
    t = C.COMMON[lang]
    here, chosen = ' aria-current="page"', ' aria-current="true"'
    nav = "".join(
        f'<a href="{url(lang, k)}"{here if key == k or (k == "tools" and key in C.TOOLS) else ""}>{esc(label)}</a>'
        for k, label in t["nav"].items()
    )
    langs = "".join(
        f'<a href="{url(l, key)}" hreflang="{C.LANGS[l]["hreflang"]}" lang="{C.LANGS[l]["html"]}"{chosen if l == lang else ""}>{esc(C.LANGS[l]["label"])}</a>'
        for l in C.LANGS
    )
    return f"""<body>
<a class="skip" href="#main">{esc(t["skip"])}</a>
<header class="site-header">
  <div class="wrap bar">
    <a class="brand" href="{url(lang, "home")}"><img src="/static/logo.png" alt="Pearnly" width="140" height="40"></a>
    <nav class="nav" aria-label="{esc(t["products"])}">{nav}</nav>
    <nav class="langs" aria-label="{esc(t["language"])}">{langs}</nav>
  </div>
</header>
<main id="main">
"""


def footer(lang):
    t = C.COMMON[lang]
    products = "".join(f'<li><a href="{url(lang, k)}">{esc("Pearnly " + C.PRODUCT[k][lang]["name"])}</a></li>' for k in C.PRODUCTS)
    tools = "".join(f'<li><a href="{url(lang, k)}">{esc(C.TOOL[k][lang]["name"])}</a></li>' for k in C.TOOLS)
    return f"""</main>
<footer class="site-footer">
  <div class="wrap cols">
    <div><h2>{esc(t["products"])}</h2><ul>{products}</ul></div>
    <div><h2>{esc(t["nav"]["tools"])}</h2><ul>{tools}</ul></div>
    <div><h2>{esc(t["contact"])}</h2><ul>
      <li><a href="{C.LINE_URL}" rel="noopener">LINE {esc(C.LINE_ID)}</a></li>
      <li><a href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a></li>
    </ul></div>
  </div>
  <div class="wrap legal">© 2026 Pearnly</div>
</footer>
</body>
</html>
"""


def contact(lang):
    t = C.COMMON[lang]
    return f"""<section class="contact">
  <div class="wrap">
    <h2>{esc(t["contact_title"])}</h2>
    <p>{esc(t["contact_text"])}</p>
    <p class="actions"><a class="button" href="{C.LINE_URL}" rel="noopener">{esc(t["cta"])}</a> <a class="button ghost" href="mailto:{C.EMAIL}">{esc(C.EMAIL)}</a></p>
  </div>
</section>
"""


def crumbs(lang, trail):
    t = C.COMMON[lang]
    parts = [f'<a href="{url(lang, "home")}">{esc(t["home"])}</a>']
    for key, name in trail[1:-1]:
        parts.append(f'<a href="{url(lang, key)}">{esc(name)}</a>')
    parts.append(f'<span aria-current="page">{esc(trail[-1][1])}</span>')
    return f'<nav class="crumbs wrap" aria-label="breadcrumb">{" / ".join(parts)}</nav>'


# ---------------- 页面 ----------------

def page_home(lang):
    h, t = C.HOME[lang], C.COMMON[lang]
    cards = "".join(
        f"""<a class="card" href="{url(lang, k)}">
  <span class="eyebrow">{esc(C.PRODUCT[k][lang]["who"])}</span>
  <h3>Pearnly {esc(C.PRODUCT[k][lang]["name"])}</h3>
  <p>{esc(C.PRODUCT[k][lang]["summary"])}</p>
  <span class="more">{esc(t["more"])} →</span>
</a>"""
        for k in C.PRODUCTS
    )
    steps = "".join(f'<li><h3>{esc(a)}</h3><p>{esc(b)}</p></li>' for a, b in h["steps"])
    tools = "".join(f'<li><a href="{url(lang, k)}">{esc(C.TOOL[k][lang]["name"])}</a><span>{esc(C.TOOL[k][lang]["short"])}</span></li>' for k in C.TOOLS)
    graph = [ORG, {"@type": "WebSite", "@id": C.SITE + "/#site", "name": "Pearnly", "url": C.SITE + "/", "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}]
    body = f"""<section class="hero">
  <div class="wrap">
    <h1>{esc(h["h1"])}</h1>
    <p class="lead">{esc(h["lead"])}</p>
    <p class="actions"><a class="button" href="{C.LINE_URL}" rel="noopener">{esc(t["cta"])}</a></p>
  </div>
</section>
<section class="wrap block">
  <h2>{esc(h["products_title"])}</h2>
  <div class="cards">{cards}</div>
</section>
<section class="wrap block">
  <h2>{esc(h["steps_title"])}</h2>
  <ol class="steps">{steps}</ol>
</section>
<section class="wrap block">
  <h2>{esc(h["tools_title"])}</h2>
  <p class="muted">{esc(h["tools_text"])}</p>
  <ul class="tool-list">{tools}</ul>
</section>
"""
    return head(lang, "home", h["title"], h["description"], graph) + header(lang, "home") + body + contact(lang) + footer(lang)


def video_block(lang, key):
    videos = C.VIDEOS.get(key) or []
    if not videos:
        return "", []
    t = C.COMMON[lang]
    items, graph = [], []
    for v in videos:
        name = v["title"][lang]
        items.append(f"""<figure class="video">
  <video controls preload="none" playsinline poster="{media(v["poster"])}" width="1280" height="720"><source src="{media(v["file"])}" type="video/mp4"></video>
  <figcaption>{esc(name)}</figcaption>
</figure>""")
        minutes, seconds = divmod(v["seconds"], 60)
        graph.append({"@type": "VideoObject", "name": f"Pearnly {C.PRODUCT[key][lang]['name']} · {name}", "description": name,
                      "thumbnailUrl": media(v["poster"]), "contentUrl": media(v["file"]), "uploadDate": v["uploaded"],
                      "duration": f"PT{minutes}M{seconds}S", "inLanguage": "th", "publisher": {"@id": ORG["@id"]}})
    return f'<section class="wrap block"><h2>{esc(t["videos"])}</h2><div class="videos">{"".join(items)}</div></section>', graph


def page_product(lang, key):
    p, t = C.PRODUCT[key][lang], C.COMMON[lang]
    features = "".join(f'<li><h3>{esc(a)}</h3><p>{esc(b)}</p></li>' for a, b in p["features"])
    download = f' <a class="button ghost" href="{C.COMPANION_URL}">{esc(p["download"])}</a>' if p.get("download") else ""
    videos, video_graph = video_block(lang, key)
    trail = [("home", t["home"]), (key, "Pearnly " + p["name"])]
    graph = [ORG, breadcrumb(lang, trail),
             {"@type": "SoftwareApplication", "name": "Pearnly " + p["name"], "applicationCategory": "BusinessApplication",
              "operatingSystem": "Web, LINE", "description": p["description"], "url": absolute(url(lang, key)),
              "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}] + video_graph
    body = f"""{crumbs(lang, trail)}
<section class="hero small">
  <div class="wrap">
    <span class="eyebrow">{esc(p["who"])}</span>
    <h1>{esc(p["h1"])}</h1>
    <p class="lead">{esc(p["lead"])}</p>
    <p class="actions"><a class="button" href="{C.LINE_URL}" rel="noopener">{esc(t["cta"])}</a>{download}</p>
  </div>
</section>
<section class="wrap block">
  <h2>{esc(t["features"])}</h2>
  <ul class="features">{features}</ul>
</section>
{videos}"""
    return head(lang, key, p["title"], p["description"], graph) + header(lang, key) + body + contact(lang) + footer(lang)


def page_tools(lang):
    p, t = C.TOOLS_INDEX[lang], C.COMMON[lang]
    tools = "".join(
        f'<a class="card" href="{url(lang, k)}"><h3>{esc(C.TOOL[k][lang]["name"])}</h3><p>{esc(C.TOOL[k][lang]["short"])}</p></a>' for k in C.TOOLS
    )
    trail = [("home", t["home"]), ("tools", t["nav"]["tools"])]
    body = f"""{crumbs(lang, trail)}
<section class="hero small"><div class="wrap"><h1>{esc(p["h1"])}</h1><p class="lead">{esc(p["lead"])}</p></div></section>
<section class="wrap block"><div class="cards">{tools}</div></section>
"""
    return head(lang, "tools", p["title"], p["description"], [ORG, breadcrumb(lang, trail)]) + header(lang, "tools") + body + contact(lang) + footer(lang)


def field(label, control):
    return f'<label class="field"><span>{esc(label)}</span>{control}</label>'


def radios(name, options, checked):
    return "".join(
        f'<label class="choice"><input type="radio" name="{name}" value="{value}"{" checked" if value == checked else ""}> {esc(text)}</label>' for value, text in options
    )


def result_rows(rows):
    return "<dl class=\"result\">" + "".join(f'<div><dt>{esc(label)}</dt><dd data-out="{key}"></dd></div>' for key, label in rows) + "</dl>"


def tool_form(lang, key):
    L = C.TOOL[key][lang]["labels"]
    amount = '<input name="amount" inputmode="decimal" autocomplete="off" placeholder="1,234.50">'
    if key == "baht-text":
        return f"""<form class="tool" data-tool="baht-text">
  {field(L["amount"], amount)}
  <div class="output" data-state="ok" hidden><span class="label">{esc(L["result"])}</span><p class="big" data-out="text" lang="th"></p><button type="button" class="button ghost" data-copy="text">{esc(L["copy"])}</button></div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "vat":
        mode = radios("mode", [("exclusive", L["exclusive"]), ("inclusive", L["inclusive"])], "exclusive")
        return f"""<form class="tool" data-tool="vat">
  {field(L["amount"], amount)}
  <fieldset class="field"><legend>{esc(L["mode"])}</legend>{mode}</fieldset>
  {field(L["rate"], '<input name="rate" inputmode="decimal" value="7" class="short">')}
  <div class="output" data-state="ok" hidden>{result_rows([("base", L["base"]), ("vat", L["vat"]), ("total", L["total"])])}</div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "withholding-tax":
        mode = radios("mode", [("exclusive", L["exclusive"]), ("inclusive", L["inclusive"])], "exclusive")
        options = "".join(f'<option value="{rate}">{esc(names[lang])}</option>' for rate, names in C.WHT_RATES)
        options += f'<option value="custom">{esc(L["custom"])}</option>'
        return f"""<form class="tool" data-tool="withholding-tax">
  {field(L["preset"], f'<select name="preset">{options}</select>')}
  <div data-custom-rate hidden>{field(L["rate"], '<input name="rate" inputmode="decimal" value="3" class="short">')}</div>
  {field(L["amount"], amount)}
  <fieldset class="field"><legend>{esc(L["mode"])}</legend>{mode}</fieldset>
  <label class="choice"><input type="checkbox" name="vat" checked> {esc(L["vat"])}</label>
  <div class="output" data-state="ok" hidden>{result_rows([("base", L["base"]), ("vat", L["vat_out"]), ("wht", L["wht"]), ("pay", L["pay"])])}</div>
  <p class="warn" data-state="invalid" hidden>{esc(L["invalid"])}</p>
</form>"""
    if key == "tax-id":
        return f"""<form class="tool" data-tool="tax-id">
  {field(L["id"], '<input name="id" inputmode="numeric" autocomplete="off" placeholder="0-1055-36112-01-4" maxlength="20">')}
  <p class="ok" data-state="valid" hidden>✓ {esc(L["valid"])} · <span data-out="formatted"></span></p>
  <p class="warn" data-state="invalid" hidden>✗ {esc(L["invalid"])}</p>
  <p class="muted" data-state="incomplete" hidden>{esc(L["incomplete"])}</p>
</form>"""
    raise KeyError(key)


def page_tool(lang, key):
    p, t = C.TOOL[key][lang], C.COMMON[lang]
    note = f'<p class="note">{esc(p["note"])}</p>' if p.get("note") else ""
    faq = "".join(f"<details><summary>{esc(q)}</summary><p>{esc(a)}</p></details>" for q, a in p["faq"])
    others = "".join(f'<li><a href="{url(lang, k)}">{esc(C.TOOL[k][lang]["name"])}</a></li>' for k in C.TOOLS if k != key)
    trail = [("home", t["home"]), ("tools", t["nav"]["tools"]), (key, p["name"])]
    app = {"@type": "WebApplication", "name": p["name"], "description": p["description"], "url": absolute(url(lang, key)),
           "applicationCategory": "FinanceApplication", "operatingSystem": "Any", "isAccessibleForFree": True,
           "offers": {"@type": "Offer", "price": "0", "priceCurrency": "THB"}, "inLanguage": C.LANGS[lang]["html"], "publisher": {"@id": ORG["@id"]}}
    body = f"""{crumbs(lang, trail)}
<section class="hero small"><div class="wrap"><h1>{esc(p["h1"])}</h1><p class="lead">{esc(p["lead"])}</p></div></section>
<section class="wrap block narrow">
  {tool_form(lang, key)}
  {note}
  <p class="muted small">{esc(t["tool_local"])}</p>
</section>
<section class="wrap block narrow">
  <h2>{esc(t["faq"])}</h2>
  <div class="faq">{faq}</div>
</section>
<section class="wrap block narrow">
  <p class="pitch">{esc(t["tool_cta"])} <a href="{url(lang, "home")}">Pearnly →</a></p>
  <h2>{esc(t["tools_more"])}</h2>
  <ul class="plain">{others}</ul>
</section>
<script src="/static/tools.js?v={VERSION}" defer></script>
"""
    return head(lang, key, p["title"], p["description"], [ORG, breadcrumb(lang, trail), app]) + header(lang, key) + body + footer(lang)


def page_404(lang):
    t = C.COMMON[lang]
    body = f"""<section class="hero"><div class="wrap"><h1>{esc(t["notfound_title"])}</h1><p class="lead">{esc(t["notfound_text"])}</p>
<p class="actions"><a class="button" href="{url(lang, "home")}">{esc(t["notfound_back"])}</a></p></div></section>
"""
    html_head = head(lang, "home", t["notfound_title"] + " | Pearnly", t["notfound_text"], None)
    html_head = html_head.replace("<head>", '<head>\n<meta name="robots" content="noindex">', 1)
    return html_head + header(lang, "home") + body + footer(lang)


def render(lang, key):
    if key == "home":
        return page_home(lang)
    if key in C.PRODUCTS:
        return page_product(lang, key)
    if key == "tools":
        return page_tools(lang)
    return page_tool(lang, key)


def sitemap():
    rows = []
    for key in C.PAGES:
        for lang in C.LANGS:
            alts = "".join(f'<xhtml:link rel="alternate" hreflang="{C.LANGS[l]["hreflang"]}" href="{absolute(url(l, key))}"/>' for l in C.LANGS)
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
    for key, path in C.PAGES.items():
        for lang in C.LANGS:
            target = out / (url(lang, key).lstrip("/")) / "index.html"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(render(lang, key), encoding="utf-8")
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
