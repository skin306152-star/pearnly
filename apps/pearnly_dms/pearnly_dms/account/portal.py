"""Uncached browser login relay. Only the configured external DMS receives credentials.

页面本身**故意极简**（一次性票据 60 秒有效，凭据直投对方站点），但正文与按钮居中、
按钮做大 —— 2026-09-25 操作员反馈"按钮太小、又靠左"。样式用 nonce 放行，
`default-src 'none'` 的其余部分一律不放宽。
"""
import html
import json
import secrets
from urllib.parse import urlsplit, urlunsplit

from pearnly_dms.i18n import t


def render(system_url, username, password, language):
    parsed = urlsplit(system_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("DMS browser handoff requires a configured HTTPS origin")
    origin = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
    root = parsed.path.rsplit("/", 1)[0] if parsed.path.endswith(".php") else parsed.path.rstrip("/")
    login = origin + root + "/login/checklogin.php"
    home = origin + root + "/home/home.php"
    nonce = secrets.token_urlsafe(24)
    text = lambda key: html.escape(t("dms.account." + key, lang=language))
    page = f'''<!doctype html><html lang="{html.escape(language or '', quote=True)}"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>DMS</title>
<style nonce="{nonce}">
  /* 2026-09-25 操作员反馈：这个中转页原来只有一个贴着左边的小按钮。
     现在正文居中、按钮做大并居中；样式走 nonce，不放松整页 CSP。 */
  html, body {{ margin: 0; height: 100%; }}
  body {{ display: flex; align-items: center; justify-content: center;
          font-family: -apple-system, "Noto Sans Thai", "Helvetica Neue", sans-serif;
          background: #ffffff; color: #111111; }}
  .portal {{ width: min(22rem, 86vw); text-align: center; }}
  .portal p {{ margin: 0 0 1.25rem; font-size: 1.05rem; line-height: 1.6; }}
  .portal button {{ width: 100%; min-height: 3.25rem; padding: 0.9rem 1.25rem;
                    font-size: 1.125rem; font-weight: 600; color: #ffffff;
                    background: #1570ef; border: 0; border-radius: 0.75rem; }}
  .portal button:disabled {{ opacity: 0.6; }}
</style>
<form id="login" class="portal" method="post" action="{html.escape(login, quote=True)}" autocomplete="off">
<input type="hidden" name="txtusers" value="{html.escape(username, quote=True)}">
<input type="hidden" name="txtpasswords" value="{html.escape(password, quote=True)}">
<input type="hidden" name="btnsubmit" value="Submit">
<p>{text('portalHint')}</p><button type="submit">{text('open')}</button></form>
<script nonce="{nonce}">
document.getElementById('login').addEventListener('submit', function(event) {{
  event.preventDefault();
  const popup = window.open('about:blank', 'pearnly-dms-portal');
  if (!popup) return;
  this.target = 'pearnly-dms-portal'; this.submit();
  this.querySelectorAll('input').forEach(input => input.value = '');
  this.querySelector('button').disabled = true;
  let count = 0;
  const timer = setInterval(() => {{
    if (popup.closed || ++count > 120) {{ clearInterval(timer); return; }}
    try {{ popup.location.href; }} catch (_) {{
      clearInterval(timer); popup.location = {json.dumps(home)};
    }}
  }}, 100);
}});
</script></html>'''
    return page, {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache", "Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow",
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; "
                                   f"style-src 'nonce-{nonce}'; form-action {origin}; "
                                   "base-uri 'none'; frame-ancestors 'none'",
        "X-Frame-Options": "DENY",
    }
