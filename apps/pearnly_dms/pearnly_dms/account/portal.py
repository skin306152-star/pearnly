"""Uncached browser login relay. Only the configured external DMS receives credentials."""
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
    page = f'''<!doctype html><html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>DMS</title>
<form id="login" method="post" action="{html.escape(login, quote=True)}" autocomplete="off">
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
        "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; form-action {origin}; base-uri 'none'; frame-ancestors 'none'",
        "X-Frame-Options": "DENY",
    }
