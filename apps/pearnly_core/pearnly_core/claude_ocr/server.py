"""甲骨文上的 claude-ocr 服务（ADR-022）：POST /v1/read，读一份单据（Haiku→Opus），只调 Anthropic。
只放行生产 IP（安全列表 + iptables）、HTTPS（自签证书，生产钉死指纹）、令牌校验。常驻、与 Anthropic 保持长连接。
环境变量：CLAUDE_OCR_TOKEN、ANTHROPIC_API_KEY、CLAUDE_OCR_CERT / CLAUDE_OCR_KEY、CLAUDE_OCR_PORT（默认 8443）。
"""
import base64
import hmac
import json
import os
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from pearnly_core.claude_ocr import engine

MAX_BODY = 60 * 1024 * 1024
MEDIA = {"image/jpeg", "image/png", "image/webp", "image/gif"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    client = None

    def log_message(self, fmt, *args):  # 不记录请求内容；只记方法、路径、状态
        sys.stderr.write("%s %s\n" % (self.address_string(), fmt % args))

    def _send(self, status, body):
        if status != 200:
            self.close_connection = True  # 请求正文可能没读完：断开，别把残留当成下一个请求
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self._send(200, {"ok": True}) if self.path == "/healthz" else self._send(404, {"error": "not_found"})

    def do_POST(self):
        token = os.environ["CLAUDE_OCR_TOKEN"]
        given = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if self.path != "/v1/read" or not hmac.compare_digest(given.encode(), token.encode()):
            return self._send(403, {"error": "forbidden"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._send(413, {"error": "too_large"})
        try:
            body = json.loads(self.rfile.read(length))
            images = [(i["media_type"], base64.b64decode(i["data"])) for i in body.get("images") or []]
            text = body.get("text") or None
            if not (images or isinstance(text, str)) or any(m not in MEDIA for m, _ in images):
                return self._send(400, {"error": "bad_images"})
            result = engine.run(self.client, body["kind"], images, body.get("own_tax"), text)
        except (KeyError, ValueError) as error:
            return self._send(400, {"error": type(error).__name__})
        except Exception as error:  # noqa: BLE001  Anthropic 出错：交给生产按工作单重试
            return self._send(502, {"error": type(error).__name__, "detail": str(error)[:300]})
        self._send(200, result)


def main():
    import anthropic
    Handler.client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], max_retries=3, timeout=180)
    server = ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("CLAUDE_OCR_PORT", "8443"))), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(os.environ["CLAUDE_OCR_CERT"], os.environ["CLAUDE_OCR_KEY"])
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
