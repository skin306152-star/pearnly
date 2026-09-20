"""Local-only cost-free OCR load fixture. No external network or real documents."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

lock = threading.Lock()
stats = {"calls": 0, "active": 0, "peak": 0, "rejections": 0, "completed": 0}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def send(self, body, status=200):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        if status in (429,503): self.send_header("Retry-After", "5")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        with lock: result = dict(stats)
        self.send(result)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        if self.headers.get("x-goog-api-key") != "cost-free-lab-only" or not 0 < length < 24 * 1024 * 1024:
            self.send({"error":"local fixture only"},403)
            return
        self.rfile.read(length)
        with lock:
            stats["calls"] += 1
            number = stats["calls"]
            if number in (7, 23):
                stats["rejections"] += 1
                rejected = True
            else:
                rejected = False
                stats["active"] += 1
                stats["peak"] = max(stats["peak"], stats["active"])
        if rejected:
            self.send({"error":"synthetic quota rejection"}, 429 if number == 7 else 503)
            return
        time.sleep(3)
        card = {"people_id":"1000009200019", "first_name_th":"ทดสอบ", "last_name_th":"คิวระบบ",
                "date_of_birth_be":"01/01/2540", "house_no":"1", "province":"กรุงเทพมหานคร"}
        with lock:
            stats["active"] -= 1
            stats["completed"] += 1
        self.send({"candidates":[{"content":{"parts":[{"text":json.dumps(card)}]}}],
                   "usageMetadata":{"promptTokenCount":1000,"candidatesTokenCount":100},
                   "modelVersion":"cost-free-lab-fixture"})

ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()
