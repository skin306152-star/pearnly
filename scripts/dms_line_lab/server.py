"""Local LINE platform double + chat UI. DMS and OCR traffic are not intercepted.

Docker DNS aliases route ONLY api.line.me/api-data.line.me here. This service accepts
only lab tokens and hosts, and signs events for the unmodified production webhook.
"""
import base64
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import requests
from config import SITES, channel_config

ROOT = Path(__file__).parent
DB = "/lab-data/line.sqlite3"
Path(DB).parent.mkdir(exist_ok=True)


def database():
    db = sqlite3.connect(DB, timeout=20)
    db.row_factory = sqlite3.Row
    return db


with database() as db:
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, site TEXT, user TEXT, kind TEXT, body TEXT, created REAL);
    CREATE TABLE IF NOT EXISTS replies (token TEXT PRIMARY KEY, site TEXT, user TEXT, created REAL, used INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS media (id TEXT PRIMARY KEY, site TEXT, content BLOB, mime TEXT);
    CREATE TABLE IF NOT EXISTS identities (token TEXT PRIMARY KEY, site TEXT, user TEXT, expires REAL);
    CREATE TABLE IF NOT EXISTS rich_menus (id TEXT PRIMARY KEY, site TEXT, body TEXT, image BLOB);
    CREATE TABLE IF NOT EXISTS rich_links (site TEXT, user TEXT, menu_id TEXT, PRIMARY KEY(site,user));
    """)


def capture(site, user, kind, body):
    with database() as db:
        db.execute("INSERT INTO messages(site,user,kind,body,created) VALUES(?,?,?,?,?)",
                   (site, user, kind, json.dumps(body, ensure_ascii=False), time.time()))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 128

    def __init__(self, address, handler):
        # Inbound simulated clients wait for webhook responses. LINE callback and
        # media traffic must have a separate pool or >32 clients deadlock the
        # simulator itself (not the product queue).
        self.slots = threading.BoundedSemaphore(128 if address[1] == 8090 else 32)
        super().__init__(address, handler)

    def process_request(self, request, address):
        self.slots.acquire()
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # Tokens and customer content never enter access logs.

    def respond(self, body, status=200, mime="application/json", headers=()):
        raw = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers:
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def handle_request(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 24 * 1024 * 1024:
                return self.respond({"error": "too large"}, 413)
            self.body = self.rfile.read(length) if length else b""
            self.path_info = urlsplit(self.path)
            host = self.headers.get("Host", "").split(":")[0]
            if host in ("api.line.me", "api-data.line.me"):
                return self.line_api()
            site = host if host in SITES else SITES[0] if host in ("localhost", "127.0.0.1") else None
            if site is None:
                return self.respond({"error": "unknown lab host"}, 400)
            if self.path_info.path in ("/", "/__lab"):
                return self.respond((ROOT / "chat.html").read_bytes(), mime="text/html; charset=utf-8")
            if self.path_info.path == "/__lab/editor":
                return self.respond((ROOT / "editor.html").read_bytes(), mime="text/html; charset=utf-8")
            if self.path_info.path.startswith("/__lab/"):
                return self.lab_api(site)
            return self.proxy(site)
        except (ValueError, KeyError, TypeError) as exc:
            self.respond({"error": type(exc).__name__}, 400)
        except Exception as exc:
            self.respond({"error": type(exc).__name__}, 500)

    def proxy(self, site):
        headers = {key: value for key, value in self.headers.items()
                   if key.lower() not in {"host", "connection", "accept-encoding", "content-length"}}
        headers.update({"Host": site + ":8197", "X-Frappe-Site-Name": site})
        # Same-origin polling may omit Origin; match the native nginx proxy contract.
        # Preserve an explicit Origin so Frappe can still reject cross-origin clients.
        headers.setdefault("Origin", "http://" + site + ":8197")
        upstream = "http://socketio:8196" if self.path.startswith("/socket.io/") else "http://site:8000"
        response = requests.request(self.command, upstream + self.path,
                                    data=self.body, headers=headers, timeout=180, allow_redirects=False)
        outgoing = [("Set-Cookie", item) for item in response.raw.headers.getlist("Set-Cookie")]
        if response.headers.get("Location"):
            outgoing.append(("Location", response.headers["Location"]))
        for header in ("Content-Security-Policy", "X-Frame-Options", "Referrer-Policy"):
            if header in response.headers:
                outgoing.append((header, response.headers[header]))
        return self.respond(response.content, response.status_code,
                            response.headers.get("Content-Type", "application/octet-stream"), outgoing)

    def lab_api(self, site):
        query = parse_qs(self.path_info.query)
        body = json.loads(self.body or b"{}")
        user = str(body.get("user") or query.get("user", ["Ulab-alice"])[0])
        if not user.startswith("Ulab-") or len(user) > 80:
            return self.respond({"error": "lab users only"}, 400)
        route = self.path_info.path
        if route == "/__lab/richmenu":
            with database() as db:
                link = db.execute("SELECT menu_id FROM rich_links WHERE site=? AND user IN (?, 'all') ORDER BY user='all' LIMIT 1", (site, user)).fetchone()
                menu = db.execute("SELECT * FROM rich_menus WHERE site=? AND id=?", (site, link["menu_id"])).fetchone() if link else None
            if not menu:
                return self.respond({})
            result = json.loads(menu["body"])
            result["richMenuId"] = menu["id"]
            result["image"] = "data:image/png;base64," + base64.b64encode(menu["image"] or b"").decode()
            return self.respond(result)
        if route == "/__lab/messages":
            after = int(query.get("after", [0])[0])
            with database() as db:
                rows = db.execute("SELECT * FROM messages WHERE site=? AND user=? AND id>? ORDER BY id LIMIT 200",
                                  (site, user, after)).fetchall()
            return self.respond([dict(row, body=json.loads(row["body"])) for row in rows])
        if route == "/__lab/identity" and self.command == "POST":
            token = secrets.token_urlsafe(40)
            with database() as db:
                db.execute("INSERT INTO identities VALUES(?,?,?,?)", (token, site, user, time.time() + 300))
            return self.respond({"id_token": token, "simulated_identity": True})
        if route == "/__lab/send" and self.command == "POST":
            event_id = body.get("event_id") or secrets.token_hex(16)
            reply = secrets.token_urlsafe(24)
            with database() as db:
                db.execute("INSERT INTO replies(token,site,user,created) VALUES(?,?,?,?)", (reply, site, user, time.time()))
            event = {"webhookEventId": event_id, "timestamp": int(time.time() * 1000),
                     "source": {"type": "user", "userId": user}, "replyToken": reply,
                     "mode": "active", "deliveryContext": {"isRedelivery": False}}
            if "postback" in body:
                event.update(type="postback", postback={"data": body["postback"], "params": body.get("params") or {}})
            elif body.get("event") in ("follow", "unfollow"):
                event["type"] = body["event"]
            else:
                message = {"id": secrets.token_hex(12), "type": "text", "text": str(body.get("text") or "")}
                if body.get("image"):
                    image = base64.b64decode(body["image"], validate=True)
                    with database() as db:
                        db.execute("INSERT INTO media VALUES(?,?,?,?)", (message["id"], site, image, body.get("mime") or "image/jpeg"))
                    message = {"id": message["id"], "type": "image", "contentProvider": {"type": "line"}}
                event.update(type="message", message=message)
            capture(site, user, "incoming", {"event_id": event_id, "event": event})
            raw = json.dumps({"destination": "Ulab-bot", "events": [event]}, ensure_ascii=False).encode()
            secret = channel_config(site)["pearnly_line_channel_secret"]
            signature = base64.b64encode(hmac.new(secret.encode(), raw, hashlib.sha256).digest()).decode()
            response = requests.post("http://site:8000/api/method/pearnly_core.line.webhook", data=raw,
                                     headers={"Host": site, "Content-Type": "application/json", "X-Line-Signature": signature}, timeout=180)
            return self.respond({"event_id": event_id, "http_status": response.status_code,
                                 "backend": response.json()}, response.status_code)
        return self.respond({"error": "unknown lab API"}, 404)

    def line_api(self):
        route = self.path_info.path
        if route == "/oauth2/v2.1/verify" and self.command == "POST":
            values = parse_qs(self.body.decode())
            with database() as db:
                identity = db.execute("SELECT * FROM identities WHERE token=? AND expires>?",
                                      (values.get("id_token", [""])[0], time.time())).fetchone()
            if identity:
                audience = channel_config(identity["site"])["pearnly_line_liff_id"].split("-")[0]
                if values.get("client_id", [""])[0] == audience:
                    return self.respond({"iss": "https://access.line.me", "aud": audience, "sub": identity["user"],
                                         "exp": int(identity["expires"]), "iat": int(time.time())})
            return self.respond({"error": "invalid lab identity"}, 400)
        bearer = self.headers.get("Authorization", "").removeprefix("Bearer ")
        site = bearer.removeprefix("lab:") if bearer.startswith("lab:") else ""
        if site not in SITES:
            return self.respond({"message": "Only laboratory tokens are accepted"}, 401)
        if route == "/v2/bot/info":
            return self.respond({"basicId": "@local-lab", "displayName": "LOCAL LINE simulator", "userId": "Ulab-bot"})
        if route.startswith("/v2/bot/profile/"):
            user = route.rsplit("/", 1)[-1]
            return self.respond({"userId": user, "displayName": user.removeprefix("Ulab-")})
        if route.endswith("/content") and route.startswith("/v2/bot/message/"):
            with database() as db:
                media = db.execute("SELECT * FROM media WHERE id=? AND site=?", (route.split("/")[-2], site)).fetchone()
            return self.respond(media["content"], mime=media["mime"]) if media else self.respond({}, 404)
        if route.startswith(("/v2/bot/richmenu", "/v2/bot/user/")):
            return self.rich_menu_api(site, route.removeprefix("/v2/bot/"))
        body = json.loads(self.body or b"{}")
        if route == "/v2/bot/chat/loading/start":
            capture(site, body["chatId"], "loading", body)
            return self.respond({}, 202)
        if route in ("/v2/bot/message/push", "/v2/bot/message/reply"):
            user = body.get("to")
            if route.endswith("/reply"):
                with database() as db:
                    reply = db.execute("SELECT * FROM replies WHERE token=? AND site=? AND used=0 AND created>?",
                                       (body.get("replyToken"), site, time.time() - 60)).fetchone()
                    if not reply:
                        return self.respond({"message": "expired/used reply token"}, 400)
                    db.execute("UPDATE replies SET used=1 WHERE token=?", (body["replyToken"],))
                    user = reply["user"]
            capture(site, user, "outgoing", body.get("messages") or [])
            return self.respond({"sentMessages": [{"id": secrets.token_hex(8)} for _ in body.get("messages", [])]})
        return self.respond({"message": "Unimplemented LINE contract: " + route}, 404)

    def rich_menu_api(self, site, route):
        parts = route.split("/")
        with database() as db:
            if route == "richmenu/list":
                rows = db.execute("SELECT id,body FROM rich_menus WHERE site=?", (site,)).fetchall()
                return self.respond({"richmenus": [dict(json.loads(row["body"]), richMenuId=row["id"]) for row in rows]})
            if route == "richmenu" and self.command == "POST":
                body = json.loads(self.body)
                if not body.get("areas") or body.get("size") != {"width": 2500, "height": 1686}:
                    return self.respond({"message": "invalid menu"}, 400)
                menu_id = "richmenu-" + secrets.token_hex(16)
                db.execute("INSERT INTO rich_menus(id,site,body) VALUES(?,?,?)", (menu_id, site, json.dumps(body)))
                return self.respond({"richMenuId": menu_id})
            if len(parts) == 3 and parts[0] == "richmenu" and parts[2] == "content":
                menu = db.execute("SELECT * FROM rich_menus WHERE site=? AND id=?", (site, parts[1])).fetchone()
                if not menu:
                    return self.respond({}, 404)
                if self.command == "POST":
                    if not self.body.startswith(b"\x89PNG"):
                        return self.respond({"message": "invalid image"}, 400)
                    db.execute("UPDATE rich_menus SET image=? WHERE site=? AND id=?", (self.body, site, parts[1]))
                    return self.respond({})
                return self.respond(menu["image"], mime="image/png")
            if len(parts) >= 3 and parts[0] == "user" and parts[2] == "richmenu":
                user = parts[1]
                if self.command == "DELETE":
                    db.execute("DELETE FROM rich_links WHERE site=? AND user=?", (site, user))
                    return self.respond({})
                if self.command == "POST" and len(parts) == 4:
                    menu = db.execute("SELECT id FROM rich_menus WHERE site=? AND id=? AND image IS NOT NULL", (site, parts[3])).fetchone()
                    if not menu:
                        return self.respond({}, 404)
                    db.execute("INSERT OR REPLACE INTO rich_links VALUES(?,?,?)", (site, user, parts[3]))
                    return self.respond({})
                link = db.execute("SELECT menu_id FROM rich_links WHERE site=? AND user=?", (site, user)).fetchone()
                return self.respond({"richMenuId": link["menu_id"]}) if link else self.respond({}, 404)
        return self.respond({"message": "Unimplemented rich-menu contract"}, 404)


secure = Server(("0.0.0.0", 443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/lab-certs/line.pem", "/lab-certs/line.key")
secure.socket = context.wrap_socket(secure.socket, server_side=True)
threading.Thread(target=secure.serve_forever, daemon=True).start()
print("Local LINE double and chat on 8090; external DMS/OCR remain real", flush=True)
Server(("0.0.0.0", 8090), Handler).serve_forever()
